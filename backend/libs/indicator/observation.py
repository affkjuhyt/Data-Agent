"""Reading the observation store as of a moment in time.

Freshness answers "how old is this data"; vintage answers "which published
version is this", and only the second lets an answer be reproduced. A figure
for 2024 read in March and read again in October can legitimately differ — the
estimate was revised — and a system that cannot tell those apart cannot explain
why its own earlier answer no longer matches.

Every read therefore takes an `as_of` date and returns the newest release
published AT OR BEFORE it. Nothing here mutates: a revision arrives as another
row against a later release, which the migration enforces with a trigger.

`prefetch` and `resolve_from` adapt this store to the callable
`formula.evaluate` expects, which is the seam between the two halves of the
compute path — the formula layer never learns where figures come from, and this
layer never learns what they are for.
"""
from __future__ import annotations

import datetime as _dt
import json
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

from libs.indicator.formula import Resolver
from libs.indicator.period import Period
from libs.indicator.quantity import Quantity, Unit


class ObservationNotFound(LookupError):
    """No approved figure for this series, period and vintage.

    A distinct type because the honest answer to "what was the ratio in 2026"
    is a refusal, not a zero — and a refusal the agent can recognise and
    explain is the difference between "not published yet" and a broken query.
    """


class Connection(Protocol):
    """The slice of an asyncpg connection this module uses."""

    async def fetch(self, query: str, *args: Any) -> Sequence[Any]: ...


@dataclass(frozen=True)
class Observation:
    quantity: Quantity
    period: Period
    release_date: _dt.date
    release_id: str
    vintage_label: str | None
    revision_status: str
    source_ref: dict[str, Any] | None


# Only approved figures are readable. A candidate is a number a machine
# proposed and nobody has checked — using one would put an unreviewed OCR
# reading into an answer that cites a document as its authority
# (README section 3.3 principle 4).
_SELECT = """
SELECT o.value, o.unit, o.unit_multiplier, o.currency, o.period, o.source_ref,
       r.release_id, r.release_date, r.vintage_label, r.revision_status
  FROM ankt.observation o
  JOIN ankt.release r ON r.release_id = o.release_id
 WHERE o.variable_id = (SELECT variable_id FROM ankt.variable
                         WHERE code = $1 AND tenant_id = current_setting('app.tenant_id')::uuid)
   AND o.reference_area = $2
   AND o.period = $3
   AND o.status = 'approved'
"""

# DISTINCT ON is Postgres' argMax: one row per series+period, the newest
# release wins. created_at breaks a tie between two releases published on the
# same date — without it the answer would depend on physical row order, which
# is exactly the kind of irreproducibility vintages exist to remove.
_AS_OF = _SELECT + """
   AND r.release_date <= $4
 ORDER BY r.release_date DESC, r.created_at DESC, r.release_id DESC
 LIMIT 1
"""

_VINTAGES = _SELECT + """
 ORDER BY r.release_date ASC, r.created_at ASC
"""


class ObservationStore:
    def __init__(self, connection: Connection, reference_area: str = "VN") -> None:
        self._connection = connection
        self._reference_area = reference_area

    async def value_as_of(
        self, variable_code: str, period: Period, as_of: _dt.date,
    ) -> Observation:
        """The figure for `period` as it stood on `as_of`."""
        rows = await self._connection.fetch(
            _AS_OF, variable_code, self._reference_area, str(period), as_of,
        )
        if not rows:
            raise ObservationNotFound(
                f"no approved observation of {variable_code} for {period} "
                f"published on or before {as_of.isoformat()}",
            )
        return _to_observation(rows[0])

    async def vintages(self, variable_code: str, period: Period) -> list[Observation]:
        """Every published version of one figure, oldest first.

        The revision triangle: what the number was first reported as, and what
        it became. Reading it is how a changed answer gets explained rather
        than merely noticed.
        """
        rows = await self._connection.fetch(
            _VINTAGES, variable_code, self._reference_area, str(period),
        )
        return [_to_observation(row) for row in rows]

    async def prefetch(
        self, variable_codes: Sequence[str], periods: Sequence[Period], as_of: _dt.date,
    ) -> dict[tuple[str, str], Observation]:
        """Read every figure a formula needs, at ONE vintage, before evaluating.

        Two reasons this is a batch rather than a lazy per-variable lookup.
        `formula.evaluate` is synchronous and deliberately knows nothing about
        IO. And one `as_of` for the whole set is a correctness requirement, not
        a convenience: a ratio that reads a revised numerator against a stale
        denominator is reproducible from no published source at all.
        """
        found: dict[tuple[str, str], Observation] = {}
        for code in variable_codes:
            for period in periods:
                try:
                    found[(code, str(period))] = await self.value_as_of(code, period, as_of)
                except ObservationNotFound:
                    continue
        return found


def resolve_from(observations: dict[tuple[str, str], Observation]) -> Resolver:
    """Turn prefetched observations into the callable `evaluate` takes."""

    def resolve(variable_code: str, period: Period) -> Quantity:
        observation = observations.get((variable_code, str(period)))
        if observation is None:
            raise ObservationNotFound(
                f"no approved observation of {variable_code} for {period} at this vintage",
            )
        return observation.quantity

    return resolve


def _source_ref(raw: Any) -> dict[str, Any] | None:
    """asyncpg hands JSONB back as text unless a codec is registered.

    Decoded here rather than by requiring every caller to set one up: a
    connection that forgot would silently yield a string where the citation
    machinery expects a mapping, and the failure would surface far from here.
    """
    if raw is None or isinstance(raw, dict):
        return raw
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return decoded if isinstance(decoded, dict) else None


def _to_observation(row: Any) -> Observation:
    currency = row["currency"]
    return Observation(
        quantity=Quantity(
            Decimal(str(row["value"])),
            Unit(row["unit"]),
            int(row["unit_multiplier"]),
            currency.strip() if currency else None,
        ),
        period=Period.parse(row["period"]),
        release_date=row["release_date"],
        release_id=str(row["release_id"]),
        vintage_label=row["vintage_label"],
        revision_status=row["revision_status"],
        source_ref=_source_ref(row["source_ref"]),
    )
