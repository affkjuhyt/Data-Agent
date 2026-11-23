"""Pure rules of the monitor module: no database, so they are unit-tested on their own."""

from __future__ import annotations

import datetime
import decimal
import hashlib
import json
import uuid
import zoneinfo
from collections.abc import Iterable
from typing import Any

BACKTEST_PERIODS = 12  # UC-08-01 step 3: the last 12 periods.
MAX_BACKTEST_PERIODS = 36
NOISE_SHARE = decimal.Decimal("0.3")  # BR-32


def valid_timezone(name: str) -> bool:
    try:
        zoneinfo.ZoneInfo(name)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        return False
    return True


def noise_warning(backtest: Any) -> bool | None:
    """BR-32: True when the backtest fired on more than 30% of its periods, None before it ran.

    The worker writes {"periods": n, "fired": k, ...} into monitor.backtest when it has run.
    """
    if not isinstance(backtest, dict):
        return None
    periods, fired = backtest.get("periods"), backtest.get("fired")
    if not isinstance(periods, int) or not isinstance(fired, int) or periods <= 0:
        return None
    return decimal.Decimal(fired) / decimal.Decimal(periods) > NOISE_SHARE


def backtest_request(periods: int, now: datetime.datetime) -> dict[str, Any]:
    return {"status": "pending", "periods": periods, "requested_at": now.isoformat()}


def close_needs_note(
    value: decimal.Decimal | None, threshold: decimal.Decimal | None, figures: Any = ()
) -> bool:
    """BR-124 as AC-08-02-05 reads it: a value more than twice its threshold needs a closing note
    (4.5% against 2% does, 3.5% does not). The occurrence count side is the table's CHECK.

    A split monitor's alert keeps its members' numbers in `figures` and none in `value` (the
    worker's monitor_runs.py): any member more than twice the threshold needs the note."""
    if threshold is None or threshold == 0:
        return False
    values = [value, *(_figure_value(f) for f in (figures if isinstance(figures, list) else []))]
    return any(v is not None and abs(v) > 2 * abs(threshold) for v in values)


def _figure_value(figure: Any) -> decimal.Decimal | None:
    raw = figure.get("value") if isinstance(figure, dict) else None
    if isinstance(raw, bool) or raw is None:
        return None
    try:
        value = decimal.Decimal(str(raw))
    except decimal.InvalidOperation:
        return None
    return value if value.is_finite() else None


def permission_group(grants: Iterable[tuple[uuid.UUID, Any]]) -> str:
    """BR-34: a hash of a recipient's permission set (role, row scope) in the Data Space;
    recipients with the same set receive the same rendering."""
    canonical = sorted(
        json.dumps([str(role), scope], sort_keys=True, default=str) for role, scope in grants
    )
    return hashlib.sha256("\n".join(canonical).encode()).hexdigest()[:32]


def visible_outputs(outputs: Any, *, owner: bool, groups: set[str]) -> list[dict[str, Any]]:
    """BR-34: the owner sees every rendering, a recipient only their permission group's; the
    object key stays inside (downloads go through a signed link, A1.1)."""
    if not isinstance(outputs, list):
        return []
    return [
        {k: v for k, v in item.items() if k != "object_key"}
        for item in outputs
        if isinstance(item, dict) and (owner or item.get("permission_group") in groups)
    ]
