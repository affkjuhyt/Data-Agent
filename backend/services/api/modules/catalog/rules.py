"""Pure rules of the catalog module: OpenBao references, source status changes, scan schedules.

No database and no FastAPI here, so each rule is tested on its own.
"""

from __future__ import annotations

import dataclasses
import datetime
import re
import uuid
from collections.abc import Callable, Iterator
from typing import Any

# Kinds a federated query can reach in place; the others are ingest only (UC-04-01 step 3, the
# CHECK on catalog.source). Used to pick the default mode, the CHECK still decides.
QUERYABLE_KINDS = frozenset(
    {
        "postgresql",
        "oracle",
        "sqlserver",
        "mysql",
        "snowflake",
        "bigquery",
        "databricks",
        "clickhouse",
    }
)


def default_mode(kind: str) -> str:
    return "federated" if kind in QUERYABLE_KINDS else "ingest"


# --- OpenBao references (BR-14) -----------------------------------------------------------------

# tab 1 §8.5: source accounts live in KV v2 `secret/`, under `tenants/{tenant_id}/` so one
# organisation's paths never overlap another's. svc roles read any path the deploy grants, so
# the org boundary of a reference is enforced here, where the reference enters the system.
_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")


class SecretRefOutsideOrg(ValueError):
    """The reference is not a path under the caller organisation's OpenBao namespace."""


def tenant_prefix(org_id: uuid.UUID) -> str:
    return f"secret/tenants/{org_id}/"


def check_secret_ref(ref: str, org_id: uuid.UUID) -> str:
    """The reference, if it names a secret of `org_id`; raises SecretRefOutsideOrg otherwise.

    `..`, empty segments and anything but [A-Za-z0-9._-] in a segment are refused, so a path
    that starts with the prefix cannot climb out of it."""
    prefix = tenant_prefix(org_id)
    if not ref.startswith(prefix):
        raise SecretRefOutsideOrg(f"secret_ref must start with {prefix}")
    rest = ref[len(prefix) :]
    segments = rest.split("/")
    if not rest or any(s in ("", ".", "..") or not _SEGMENT.match(s) for s in segments):
        raise SecretRefOutsideOrg("secret_ref is not a plain path under the organisation")
    return ref


# --- source status (PATCH /v1/sources/{id}) -----------------------------------------------------


def status_change(current: str, target: str, *, tested: bool) -> str | None:
    """Why a caller may not move a source from `current` to `target`, or None when allowed.

    Only a passed connection test makes a source active (UC-04-01 step 2): a person pauses,
    resumes a paused source that had passed its test, or archives; archived is final."""
    if current == target:
        return None
    if current == "archived":
        return "An archived source cannot change status."
    if target == "archived":
        return None
    if target == "paused":
        if current in ("active", "needs_fix"):
            return None
        return f"A {current} source cannot be paused."
    if target == "active":
        if current == "paused" and tested:
            return None
        return "Only a paused source that passed its connection test can be resumed; test it first."
    return f"Status {target} is set by the system, not by a person."


# --- PII masking (UC-04-02 step 4) --------------------------------------------------------------

MASK = "***"


def mask_profile_values(top_values: Any) -> Any:
    """Top values of a personal-data column with each value masked, counts kept.

    The scan masks before it stores; this holds even for a profile written before the column
    was labelled."""
    if not isinstance(top_values, list):
        return None if top_values is None else MASK
    return [{**item, "value": MASK} if isinstance(item, dict) else MASK for item in top_values]


# --- cron (UC-04-05) ----------------------------------------------------------------------------

_MACROS = {
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
    "@monthly": "0 0 1 * *",
    "@weekly": "0 0 * * 0",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@hourly": "0 * * * *",
}
_FIELDS = (("minute", 0, 59), ("hour", 0, 23), ("day", 1, 31), ("month", 1, 12), ("weekday", 0, 7))
_MONTH_NAMES = {
    m: i + 1
    for i, m in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
    )
}
_DAY_NAMES = {d: i for i, d in enumerate(("sun", "mon", "tue", "wed", "thu", "fri", "sat"))}


class CronError(ValueError):
    pass


@dataclasses.dataclass(frozen=True)
class Cron:
    minutes: tuple[int, ...]
    hours: tuple[int, ...]
    days: frozenset[int]
    months: frozenset[int]
    # 0 = Sunday, as cron counts; 7 is folded into 0.
    weekdays: frozenset[int]
    days_restricted: bool
    weekdays_restricted: bool

    def matches_day(self, day: datetime.date) -> bool:
        if day.month not in self.months:
            return False
        dom = day.day in self.days
        dow = (day.isoweekday() % 7) in self.weekdays
        # Vixie cron: with both day fields restricted, either one matching is enough.
        if self.days_restricted and self.weekdays_restricted:
            return dom or dow
        return dom and dow


def _value(token: str, names: dict[str, int], low: int, high: int, field: str) -> int:
    key = token.lower()
    if key in names:
        return names[key]
    if not token.isdigit():
        raise CronError(f"{field}: {token!r} is not a number")
    value = int(token)
    if not low <= value <= high:
        raise CronError(f"{field}: {value} is outside {low}-{high}")
    return value


def _field(text: str, field: str, low: int, high: int, names: dict[str, int]) -> set[int]:
    out: set[int] = set()
    for part in text.split(","):
        base, _, step_text = part.partition("/")
        step = 1
        if step_text:
            if not step_text.isdigit() or int(step_text) == 0:
                raise CronError(f"{field}: step {step_text!r} is not a positive number")
            step = int(step_text)
        if base == "*":
            start, end = low, high
        elif "-" in base:
            a, _, b = base.partition("-")
            start, end = _value(a, names, low, high, field), _value(b, names, low, high, field)
            if start > end:
                raise CronError(f"{field}: range {base!r} runs backwards")
        else:
            start = _value(base, names, low, high, field)
            end = high if step_text else start
        out.update(range(start, end + 1, step))
    return out


def parse_cron(expression: str) -> Cron:
    """A standard five-field cron expression (minute hour day month weekday) or an @macro."""
    text = _MACROS.get(expression.strip().lower(), expression.strip())
    parts = text.split()
    if len(parts) != 5:
        raise CronError("a cron expression has five fields: minute hour day month weekday")
    values = []
    for part, (field, low, high) in zip(parts, _FIELDS, strict=True):
        names = _MONTH_NAMES if field == "month" else _DAY_NAMES if field == "weekday" else {}
        values.append(_field(part, field, low, high, names))
    minutes, hours, days, months, weekdays = values
    return Cron(
        minutes=tuple(sorted(minutes)),
        hours=tuple(sorted(hours)),
        days=frozenset(days),
        months=frozenset(months),
        weekdays=frozenset(d % 7 for d in weekdays),
        days_restricted=parts[2] != "*",
        weekdays_restricted=parts[4] != "*",
    )


# Two years of days is enough for any expression that fires at all (29 February included).
_HORIZON_DAYS = 366 * 2 + 1


def next_runs(
    cron: Cron,
    after: datetime.datetime,
    count: int,
    *,
    zone: datetime.tzinfo,
    skip_day: Callable[[datetime.date], bool] = lambda _: False,
) -> list[datetime.datetime]:
    """The next `count` fire times strictly after `after`, read in `zone`, on days `skip_day`
    does not drop (non-working days of the work calendar, UC-04-05 step 2)."""
    return list(_runs(cron, after.astimezone(zone), count, zone, skip_day))


def _runs(
    cron: Cron,
    after: datetime.datetime,
    count: int,
    zone: datetime.tzinfo,
    skip_day: Callable[[datetime.date], bool],
) -> Iterator[datetime.datetime]:
    found = 0
    day = after.date()
    for _ in range(_HORIZON_DAYS):
        if cron.matches_day(day) and not skip_day(day):
            for hour in cron.hours:
                for minute in cron.minutes:
                    at = datetime.datetime.combine(day, datetime.time(hour, minute), tzinfo=zone)
                    if at > after:
                        yield at
                        found += 1
                        if found == count:
                            return
        day += datetime.timedelta(days=1)
