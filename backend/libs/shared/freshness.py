"""Data governance facts about a table: lineage, owner, freshness, classification.

Why this exists: an answer built from a warehouse is only true as of the moment
that warehouse was last loaded, and nothing in the platform knew when that was.
`SchemaCatalog` tracked `last_altered` (when the DDL changed) and
`last_crawled_at` (when *metadata* was read) — neither of which says anything
about the age of the *data*. So a question answered off a pipeline that broke
three days ago produced a confident number with no way for the reader to tell.

The four facts kept here answer four different questions, and they come from two
different places:

  * **freshness** (`data_as_of`) is *measured* — MAX(business date) read from the
    warehouse by the crawler;
  * **owner**, **classification** and **lineage** (`upstream`) are *declared* by
    an operator and never inferred. A guessed sensitivity label is worse than an
    absent one: it invites reliance on a control nobody set.

Undeclared and unmeasured are represented distinctly from "fine". `status()`
returns UNKNOWN, never FRESH, when there is nothing to judge on — the whole
point is that the answer must not imply currency it cannot support.

Pure: no database driver, no network, no service imports (.importlinter keeps
`libs` a leaf). The crawler writes these fields, workflow-runtime reads them.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ── classification ─────────────────────────────────────────────────────────
# A closed vocabulary, ordered least to most sensitive. Closed because an open
# string field becomes six spellings of "confidential" and then cannot be
# reported on; ordered because an answer drawn from several tables carries the
# highest label among them, not the first one seen.
PUBLIC = "public"
INTERNAL = "internal"
CONFIDENTIAL = "confidential"
RESTRICTED = "restricted"

CLASSIFICATION_ORDER: tuple[str, ...] = (PUBLIC, INTERNAL, CONFIDENTIAL, RESTRICTED)

_CLASSIFICATION_ALIASES: dict[str, str] = {
    "public": PUBLIC,
    "open": PUBLIC,
    "internal": INTERNAL,
    "internal-only": INTERNAL,
    "confidential": CONFIDENTIAL,
    "sensitive": CONFIDENTIAL,
    "restricted": RESTRICTED,
    "secret": RESTRICTED,
    "pii": RESTRICTED,
}


def normalize_classification(value: Any) -> str:
    """One of CLASSIFICATION_ORDER, or "" when the label is absent or unknown.

    An unrecognised label returns "" and logs rather than being stored verbatim:
    a value that no downstream comparison understands reads as a declared
    control while behaving like an undeclared one.
    """
    text = str(value or "").strip().lower().replace("_", "-")
    if not text:
        return ""
    resolved = _CLASSIFICATION_ALIASES.get(text)
    if resolved is None:
        logger.warning("freshness | unknown classification label %r — treating as undeclared", value)
        return ""
    return resolved


def _max_classification(labels: Iterable[str]) -> str:
    ranked = [c for c in labels if c in CLASSIFICATION_ORDER]
    if not ranked:
        return ""
    return max(ranked, key=CLASSIFICATION_ORDER.index)


# ── freshness status ───────────────────────────────────────────────────────
FRESH = "fresh"
STALE = "stale"
UNKNOWN = "unknown"


def _aware(value: datetime | None) -> datetime | None:
    """Naive timestamps are read as UTC — Postgres hands back naive values for a
    column declared without a timezone, and comparing one to an aware `now`
    raises rather than returning a wrong answer."""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _iso(value: datetime | None) -> str | None:
    aware = _aware(value)
    return aware.isoformat() if aware else None


@dataclass(frozen=True)
class TableGovernance:
    """Governance facts for ONE table, as the catalog holds them."""

    table: str
    schema_name: str = ""
    owner: str = ""
    classification: str = ""
    upstream: tuple[str, ...] = ()
    data_as_of: datetime | None = None
    freshness_column: str = ""
    freshness_checked_at: datetime | None = None
    freshness_sla_seconds: int | None = None
    last_crawled_at: datetime | None = None

    @property
    def qualified_name(self) -> str:
        return f"{self.schema_name}.{self.table}" if self.schema_name else self.table

    def age_seconds(self, now: datetime | None = None) -> float | None:
        """How old the newest row is, or None when never measured.

        Measured at crawl time, compared against now: the watermark can only
        have moved forward since, so this OVERSTATES age. That direction is
        deliberate — it can raise a false staleness warning, never suppress a
        true one.
        """
        as_of = _aware(self.data_as_of)
        if as_of is None:
            return None
        return ((now or datetime.now(timezone.utc)) - as_of).total_seconds()

    def status(self, now: datetime | None = None) -> str:
        """FRESH / STALE / UNKNOWN.

        UNKNOWN whenever either half of the judgement is missing — no measured
        watermark, or no declared SLA to measure it against. A table with a
        watermark but no SLA is not "fresh by default": nobody has said how old
        is too old, so the answer states the age and claims nothing.
        """
        age = self.age_seconds(now)
        if age is None or not self.freshness_sla_seconds:
            return UNKNOWN
        return STALE if age > self.freshness_sla_seconds else FRESH

    def to_dict(self) -> dict[str, Any]:
        return {
            "table": self.qualified_name,
            "owner": self.owner,
            "classification": self.classification,
            "upstream": list(self.upstream),
            "data_as_of": _iso(self.data_as_of),
            "freshness_column": self.freshness_column,
            "freshness_checked_at": _iso(self.freshness_checked_at),
            "freshness_sla_seconds": self.freshness_sla_seconds,
            "last_crawled_at": _iso(self.last_crawled_at),
        }

    @classmethod
    def from_row(cls, row: Any) -> TableGovernance:
        """Build from a SchemaCatalog row — an ORM object or a mapping.

        `Mapping`, not `dict`: SQLAlchemy hands back a `RowMapping`, which is a
        Mapping but not a dict, and testing for dict would silently send it down
        the getattr branch and produce a row with every field empty.
        """
        if isinstance(row, Mapping):
            def get(key: str) -> Any:
                return row.get(key)
        else:
            def get(key: str) -> Any:
                return getattr(row, key, None)

        upstream = get("upstream") or []
        return cls(
            table=str(get("table_name") or ""),
            schema_name=str(get("schema_name") or ""),
            owner=str(get("owner") or ""),
            classification=normalize_classification(get("classification")),
            upstream=tuple(str(u) for u in upstream if u),
            data_as_of=_aware(get("data_as_of")),
            freshness_column=str(get("freshness_column") or ""),
            freshness_checked_at=_aware(get("freshness_checked_at")),
            freshness_sla_seconds=get("freshness_sla_seconds"),
            last_crawled_at=_aware(get("last_crawled_at")),
        )


@dataclass(frozen=True)
class Provenance:
    """The governance summary for ONE answer, across every table it touched."""

    status: str = UNKNOWN
    as_of: datetime | None = None
    age_seconds: float | None = None
    classification: str = ""
    owners: tuple[str, ...] = ()
    stale_tables: tuple[str, ...] = ()
    unknown_tables: tuple[str, ...] = ()
    lineage: dict[str, list[str]] = field(default_factory=dict)
    tables: tuple[TableGovernance, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "as_of": _iso(self.as_of),
            "age_seconds": self.age_seconds,
            "classification": self.classification,
            "owners": list(self.owners),
            "stale_tables": list(self.stale_tables),
            "unknown_tables": list(self.unknown_tables),
            "lineage": self.lineage,
            "tables": [t.to_dict() for t in self.tables],
        }


def summarize(tables: Sequence[TableGovernance], now: datetime | None = None) -> Provenance:
    """Roll per-table facts up into what one answer can honestly say.

    Two rules do the work:

    `as_of` is the OLDEST watermark among the tables, not the newest. An answer
    joining a table loaded an hour ago to one loaded last week is a statement
    about last week; quoting the hour would be the most flattering reading of
    the evidence rather than the true one.

    `status` is STALE if any table is stale, else UNKNOWN if any is unmeasured,
    else FRESH. FRESH therefore requires every input to be individually
    provable — one unmeasured table is enough to withdraw the claim.
    """
    now = now or datetime.now(timezone.utc)
    if not tables:
        return Provenance()

    statuses = {t.qualified_name: t.status(now) for t in tables}
    stale = tuple(sorted(n for n, s in statuses.items() if s == STALE))
    unknown = tuple(sorted(n for n, s in statuses.items() if s == UNKNOWN))

    watermarks = [w for w in (_aware(t.data_as_of) for t in tables) if w is not None]
    as_of = min(watermarks) if watermarks else None

    if stale:
        status = STALE
    elif unknown:
        status = UNKNOWN
    else:
        status = FRESH

    return Provenance(
        status=status,
        as_of=as_of,
        age_seconds=(now - as_of).total_seconds() if as_of else None,
        classification=_max_classification(t.classification for t in tables),
        owners=tuple(sorted({t.owner for t in tables if t.owner})),
        stale_tables=stale,
        unknown_tables=unknown,
        lineage={t.qualified_name: list(t.upstream) for t in tables if t.upstream},
        tables=tuple(tables),
    )


def _humanize(seconds: float) -> str:
    if seconds < 90:
        return f"{int(seconds)}s"
    if seconds < 5400:
        return f"{seconds / 60:.0f}m"
    if seconds < 172800:
        return f"{seconds / 3600:.1f}h"
    return f"{seconds / 86400:.1f}d"


def describe(prov: Provenance | dict[str, Any] | None) -> str:
    """One line of evidence for the answering model, or "" when there is none.

    Takes either form. The dict is what actually travels: provenance is computed
    where the SQL runs and read again in the responder, crossing the agent state
    as plain JSON in between, so rendering from a rehydrated `Provenance` would
    mean a lossy round-trip for no gain.

    English and structural on purpose: the responder renders the answer in the
    user's language, so phrasing this in any one language would either leak that
    language into the answer or get translated twice.

    States the facts and nothing else — it does not tell the model the number is
    wrong, only how old the data behind it is.
    """
    if isinstance(prov, Provenance):
        data: Mapping[str, Any] = prov.to_dict()
    elif isinstance(prov, Mapping):
        data = prov
    else:
        # None, or a shape this version does not recognise. Rendering nothing is
        # the only safe outcome: this runs inside the responder, and raising here
        # would fail a turn whose answer has already been computed.
        return ""
    if not data.get("tables"):
        return ""

    parts: list[str] = []
    as_of, age = data.get("as_of"), data.get("age_seconds")
    if as_of:
        age_text = f", {_humanize(age)} old" if isinstance(age, (int, float)) else ""
        parts.append(f"data as of {as_of}{age_text}")
    else:
        parts.append("data as of UNKNOWN (freshness never measured for these tables)")

    status = data.get("status") or UNKNOWN
    stale_tables = data.get("stale_tables") or []
    unknown_tables = data.get("unknown_tables") or []
    if status == STALE and stale_tables:
        parts.append(f"STALE — past the agreed freshness window: {', '.join(stale_tables)}")
    elif status == UNKNOWN and unknown_tables:
        parts.append(f"freshness UNVERIFIED for: {', '.join(unknown_tables)}")

    if data.get("classification"):
        parts.append(f"classification={data['classification']}")
    if data.get("owners"):
        parts.append(f"owner={', '.join(data['owners'])}")
    lineage = data.get("lineage") or {}
    if lineage:
        parts.append(
            "lineage: " + "; ".join(f"{t} <- {', '.join(src)}" for t, src in sorted(lineage.items()))
        )
    return " | ".join(parts)
