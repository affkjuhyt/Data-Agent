"""A source table's structure moved (UC-04-03, backlog RES-22): what changed, and whether it is
written at once or waits for a person.

A schema scan reads each table's columns again. Their canonical hash (name, source type, normalised
type, nullability, key, in name order: the order the source lists them in is no change) is compared
with the current dataset_version's. When it differs:

* only columns added, or a column now allowing NULL: additive. A new dataset_version is written at
  once (AC-04-03-03), the old one closed with valid_to (BR-16), the new columns recorded from it.
* a column removed, a column whose type changed, a column no longer allowing NULL: breaking. Nothing
  is written; a `schema_change` proposal opens with the diff (AC-04-03-07), one per table and new
  structure however often the scan runs. Accepted, it is applied as an additive change is, the
  removed and retyped columns closed with removed_in_version, provided the table is still at the
  version the proposal was made against; otherwise it ends apply_failed and the next scan proposes
  again from where the table is.

ColumnMeta.dataset_version is the first version that has the column; the columns of a version are
those with dataset_version <= it and removed_in_version null or above it (live_columns).
"""

from __future__ import annotations

import dataclasses
import datetime
import hashlib
import json
import logging
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Literal

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db import scoped_session, uuid7
from libs.dataagent_db.models import catalog, context
from services.worker_agent.events import Event

logger = logging.getLogger(__name__)

ROLE = "svc_catalog"
NOTIFY_ROLE = "svc_notify"
KIND = "schema_change"
NAMESPACE = uuid.UUID("6f0c6a59-3b0f-4f55-9a52-1f2f6e1f0c3a")
# AC-04-03-01: a rename guess below this is not offered (too many false pairs on short names).
RENAME_AT = 0.55

Change = Literal["additive", "breaking"]


def _signature(column: dict[str, Any]) -> tuple[str, str, str, bool, bool]:
    return (
        str(column["name"]),
        str(column["data_type"]),
        str(column["normalized_type"]),
        bool(column["is_nullable"]),
        bool(column.get("is_key", False)),
    )


def schema_hash(columns: Sequence[dict[str, Any]]) -> str:
    """The structure's hash: the same columns give the same hash in whatever order the source
    lists them (the order of a dict's repr is no change)."""
    canonical = sorted(_signature(c) for c in columns)
    return hashlib.sha256(json.dumps(canonical, ensure_ascii=False).encode()).hexdigest()


def rename_score(before: str, after: str) -> float:
    """How alike two column names are (0-1), as SequenceMatcher counts it."""
    return SequenceMatcher(None, before.lower(), after.lower()).ratio()


def references_column(expression: str, column: str) -> bool:
    """True when a SQL expression names `column` as an identifier (not a substring of another)."""
    if not column:
        return False
    return (
        re.search(rf"(?i)(^|[^A-Za-z0-9_]){re.escape(column)}([^A-Za-z0-9_]|$)", expression)
        is not None
    )


def guess_renames(
    removed: Sequence[dict[str, Any]],
    added: Sequence[dict[str, Any]],
    *,
    min_score: float = RENAME_AT,
) -> list[dict[str, Any]]:
    """One-to-one rename guesses: same normalised type, best name score first (AC-04-03-01)."""
    pairs: list[tuple[float, str, dict[str, Any]]] = []
    for old in removed:
        for new in added:
            if str(old["normalized_type"]) != str(new["normalized_type"]):
                continue
            score = rename_score(str(old["name"]), str(new["name"]))
            if score >= min_score:
                pairs.append((score, str(old["name"]), new))
    pairs.sort(key=lambda item: (-item[0], item[1], str(item[2]["name"])))
    used_old: set[str] = set()
    used_new: set[str] = set()
    out: list[dict[str, Any]] = []
    for score, old_name, new in pairs:
        new_name = str(new["name"])
        if old_name in used_old or new_name in used_new:
            continue
        used_old.add(old_name)
        used_new.add(new_name)
        out.append(
            {
                "from": old_name,
                "to": new_name,
                "score": round(score, 4),
                "normalized_type": str(new["normalized_type"]),
            }
        )
    return out


def unmapped_removals(d: Diff) -> list[str]:
    """Removed columns that no rename guess covers — metrics using them become unrunnable."""
    mapped = {r["from"] for r in d.renames}
    return [name for name in d.removed if name not in mapped]


@dataclass
class Diff:
    added: list[dict[str, Any]] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    retyped: list[dict[str, str]] = field(default_factory=list)  # name, before, after
    nullable_now: list[str] = field(default_factory=list)
    not_null_now: list[str] = field(default_factory=list)
    renames: list[dict[str, Any]] = field(default_factory=list)

    @property
    def change(self) -> Change | None:
        if self.removed or self.retyped or self.not_null_now:
            return "breaking"
        if self.added or self.nullable_now:
            return "additive"
        return None

    def as_json(self) -> dict[str, Any]:
        return {
            "added": [c["name"] for c in self.added],
            "removed": self.removed,
            "retyped": self.retyped,
            "nullable_now": self.nullable_now,
            "not_null_now": self.not_null_now,
            "renames": self.renames,
        }


def diff(before: Sequence[dict[str, Any]], after: Sequence[dict[str, Any]]) -> Diff:
    old = {str(c["name"]): c for c in before}
    new = {str(c["name"]): c for c in after}
    d = Diff()
    for name, c in new.items():
        if name not in old:
            d.added.append(dict(c))
            continue
        o = old[name]
        if str(o["data_type"]) != str(c["data_type"]):
            d.retyped.append(
                {"name": name, "before": str(o["data_type"]), "after": str(c["data_type"])}
            )
        elif bool(o["is_nullable"]) != bool(c["is_nullable"]):
            (d.nullable_now if c["is_nullable"] else d.not_null_now).append(name)
    d.removed = [name for name in old if name not in new]
    d.renames = guess_renames([old[name] for name in d.removed], d.added)
    return d


def live_columns(dataset_id: uuid.UUID, version: int) -> Any:
    """The columns a version of the dataset has."""
    return select(catalog.ColumnMeta).where(
        catalog.ColumnMeta.dataset_id == dataset_id,
        catalog.ColumnMeta.dataset_version <= version,
        or_(
            catalog.ColumnMeta.removed_in_version.is_(None),
            catalog.ColumnMeta.removed_in_version > version,
        ),
    )


def _as_dict(c: catalog.ColumnMeta) -> dict[str, Any]:
    return {
        "name": c.name,
        "ordinal": c.ordinal,
        "data_type": c.data_type,
        "normalized_type": c.normalized_type,
        "is_nullable": c.is_nullable,
        "is_key": c.is_key,
    }


async def current_columns(session: AsyncSession, dataset: catalog.Dataset) -> list[dict[str, Any]]:
    rows = (await session.scalars(live_columns(dataset.id, dataset.current_version))).all()
    return [_as_dict(c) for c in rows]


async def write_version(
    session: AsyncSession,
    dataset: catalog.Dataset,
    after: Sequence[dict[str, Any]],
    d: Diff,
    *,
    org_id: uuid.UUID,
    by: uuid.UUID | None,
    scan_run_id: uuid.UUID | None,
    proposal_id: uuid.UUID | None,
    now: datetime.datetime,
) -> int:
    """The next dataset_version from the columns found: the old version closed, removed and
    retyped columns closed, added and retyped ones recorded from the new version, nullability and
    position kept current on the columns that stay."""
    old_version = int(dataset.current_version or 1)
    new_version = old_version + 1
    current = await session.scalar(
        select(catalog.DatasetVersion).where(
            catalog.DatasetVersion.dataset_id == dataset.id,
            catalog.DatasetVersion.version == old_version,
        )
    )
    if current is not None and current.valid_to is None:
        current.valid_to = now
    session.add(
        catalog.DatasetVersion(
            id=uuid7(),
            org_id=org_id,
            dataset_id=dataset.id,
            version=new_version,
            schema_hash=schema_hash(after),
            diff=d.as_json(),
            change_kind=d.change or "additive",
            proposal_id=proposal_id,
            valid_from=now,
            scan_run_id=scan_run_id,
            created_by=by,
        )
    )
    by_name = {str(c["name"]): c for c in after}
    closing = set(d.removed) | {r["name"] for r in d.retyped}
    closed: dict[str, catalog.ColumnMeta] = {}
    for column in (await session.scalars(live_columns(dataset.id, old_version))).all():
        if column.name in closing:
            column.removed_in_version = new_version
            closed[column.name] = column
        elif column.name in by_name:
            found = by_name[column.name]
            column.is_nullable = bool(found["is_nullable"])
            column.ordinal = int(found["ordinal"])
    for c in [*d.added, *(by_name[r["name"]] for r in d.retyped)]:
        # A retyped column is the same column read differently: it keeps what people said of it.
        # Its personal-data label above all, or a masked column would come back unmasked; the
        # masking is to be confirmed again, as the type it is read as changed.
        before = closed.get(str(c["name"]))
        session.add(
            catalog.ColumnMeta(
                id=uuid7(),
                org_id=org_id,
                dataset_id=dataset.id,
                dataset_version=new_version,
                name=str(c["name"]),
                ordinal=int(c["ordinal"]),
                data_type=str(c["data_type"]),
                normalized_type=str(c["normalized_type"]),
                is_nullable=bool(c["is_nullable"]),
                is_key=bool(c.get("is_key", False)),
                pii_label=before.pii_label if before else None,
                pii_confirmed=False,
                display_name=before.display_name if before else None,
                description=before.description if before else None,
                description_status=before.description_status if before else "none",
                unit=before.unit if before else None,
                created_by=by,
            )
        )
    dataset.current_version = new_version
    return new_version


@dataclass
class Pending:
    """A breaking change found by a scan, to propose once its transaction has committed."""

    dataset_id: uuid.UUID
    data_space_id: uuid.UUID
    table: str
    from_version: int
    columns: list[dict[str, Any]]
    diff: Diff


async def impact_for_removed(
    session: AsyncSession, dataset_id: uuid.UUID, removed: Sequence[str]
) -> dict[str, Any]:
    """Metrics / dimensions / measures that name a removed column (AC-04-03-01 impact list)."""
    names = [n for n in removed if n]
    empty: dict[str, Any] = {
        "metrics": [],
        "dimensions": [],
        "measures": [],
        "prepared_questions": 0,
    }
    if not names:
        return empty
    entities = (
        await session.scalars(
            select(context.SemanticEntity).where(
                context.SemanticEntity.primary_dataset_id == dataset_id
            )
        )
    ).all()
    entity_ids = [e.id for e in entities]
    space_ids = {e.data_space_id for e in entities}
    dimensions: list[dict[str, str]] = []
    measures: list[dict[str, str]] = []
    if entity_ids:
        for dim in (
            await session.scalars(
                select(context.SemanticDimension).where(
                    context.SemanticDimension.entity_id.in_(entity_ids)
                )
            )
        ).all():
            hit = next((n for n in names if references_column(dim.expression, n)), None)
            if hit is not None:
                dimensions.append({"code": dim.code, "name": dim.name, "column": hit})
        for measure in (
            await session.scalars(
                select(context.SemanticMeasure).where(
                    context.SemanticMeasure.entity_id.in_(entity_ids)
                )
            )
        ).all():
            hit = next((n for n in names if references_column(measure.expression, n)), None)
            if hit is not None:
                measures.append({"code": measure.code, "name": measure.name, "column": hit})
    measure_codes = {m["code"] for m in measures}
    metrics: list[dict[str, str]] = []
    metric_filter = [context.Metric.canonical_dataset_id == dataset_id]
    if space_ids:
        metric_filter.append(context.Metric.data_space_id.in_(space_ids))
    for metric in (
        await session.scalars(
            select(context.Metric).where(
                context.Metric.status == "active",
                or_(*metric_filter),
            )
        )
    ).all():
        if metric.canonical_dataset_id != dataset_id and metric.data_space_id not in space_ids:
            continue
        if metric.current_version is None:
            continue
        version = await session.scalar(
            select(context.MetricVersion).where(
                context.MetricVersion.metric_id == metric.id,
                context.MetricVersion.version == metric.current_version,
            )
        )
        if version is None:
            continue
        hit = next((n for n in names if references_column(version.expression, n)), None)
        if hit is None:
            hit = next(
                (
                    m["column"]
                    for m in measures
                    if m["code"] in measure_codes
                    and references_column(version.expression, m["code"])
                ),
                None,
            )
        if hit is not None:
            metrics.append(
                {
                    "code": metric.code,
                    "name": version.display_name,
                    "column": hit,
                    "metric_version_id": str(version.id),
                }
            )
    examples = 0
    if space_ids:
        rows = (
            await session.scalars(
                select(context.ExampleQuery).where(
                    context.ExampleQuery.data_space_id.in_(space_ids),
                    context.ExampleQuery.status == "active",
                )
            )
        ).all()
        examples = sum(
            1
            for row in rows
            if any(
                references_column(row.sql, n) or references_column(row.question, n) for n in names
            )
        )
    return {
        "metrics": metrics,
        "dimensions": dimensions,
        "measures": measures,
        "prepared_questions": examples,
    }


async def mark_versions_broken(
    session: AsyncSession, dataset_id: uuid.UUID, removed: Sequence[str]
) -> list[str]:
    """Certified versions that still name an unmapped removed column → status broken (AC-04-03-05)."""
    impact = await impact_for_removed(session, dataset_id, removed)
    broken: list[str] = []
    for item in impact["metrics"]:
        version = await session.get(
            context.MetricVersion, uuid.UUID(str(item["metric_version_id"]))
        )
        if version is None or version.status not in ("certified", "testing", "pending_review"):
            continue
        version.status = "broken"
        broken.append(item["code"])
    return broken


async def notify_additive(
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID | None,
    data_space_id: uuid.UUID,
    dataset_id: uuid.UUID,
    table: str,
    added: Sequence[str],
) -> None:
    """AC-04-03-03: an additive column write tells the person who ran the scan (no proposal)."""
    if user_id is None or not added:
        return
    from libs.dataagent_db.models import notify

    async with scoped_session(NOTIFY_ROLE, org_id, user_id) as session:
        session.add(
            notify.Notification(
                id=uuid7(),
                org_id=org_id,
                user_id=user_id,
                data_space_id=data_space_id,
                kind="system",
                title=f"Bảng {table} có cột mới",
                body=f"Đã ghi phiên bản mới với cột: {', '.join(added)}",
                link=f"/data/datasets/{dataset_id}",
                source_ref=dataset_id,
            )
        )


async def propose(event: Event, pending: Sequence[Pending], proposed_by: uuid.UUID) -> None:
    """One schema_change proposal per table and new structure: its id comes from both, so a scan
    that runs again finds the one it opened. Diff carries rename guesses and the impact list."""
    from services.worker_agent import proposals

    for p in pending:
        pid = uuid.uuid5(NAMESPACE, f"{p.dataset_id}:{schema_hash(p.columns)}")
        keyed = dataclasses.replace(event, payload={**event.payload, "proposal_id": str(pid)})
        async with scoped_session(ROLE, event.org_id, proposed_by) as session:
            impact = await impact_for_removed(
                session, p.dataset_id, unmapped_removals(p.diff) or p.diff.removed
            )
        await proposals._open(
            keyed,
            data_space_id=p.data_space_id,
            kind=KIND,
            target_service="catalog",
            target_ref=p.dataset_id,
            title=f"Cấu trúc bảng {p.table} đổi",
            diff={
                **p.diff.as_json(),
                "from_version": p.from_version,
                "schema_hash": schema_hash(p.columns),
                "columns": p.columns,
            },
            proposed_by=proposed_by,
            eval_run_id=None,
            impact=impact,
            impact_level="high",
        )


async def apply(event: Event, proposal: dict[str, Any]) -> bool:
    """An accepted schema_change: the version written as the proposal describes it, if the table
    is still where it was proposed from."""
    if event.payload.get("status") != "accepted":
        return True
    reviewer = uuid.UUID(str(event.payload["reviewer_id"]))
    # What the scan asked for, kept apart from any edit of the diff (proposals._open).
    evidence = proposal.get("evidence") or [{}]
    change = (evidence[0] if isinstance(evidence[0], dict) else {}).get("requested") or {}
    dataset_id = uuid.UUID(str(proposal["target_ref"]))
    async with scoped_session(ROLE, event.org_id, reviewer) as session:
        dataset = await session.get(catalog.Dataset, dataset_id, with_for_update=True)
        if dataset is None or int(dataset.current_version or 1) != int(
            change.get("from_version", 0)
        ):
            logger.info("schema_change %s: the table moved on, not applied", proposal["id"])
            return False
        after = list(change.get("columns") or [])
        d = diff(await current_columns(session, dataset), after)
        if d.change is None:
            return False
        await write_version(
            session,
            dataset,
            after,
            d,
            org_id=event.org_id,
            by=reviewer,
            scan_run_id=None,
            proposal_id=uuid.UUID(str(proposal["id"])),
            now=datetime.datetime.now(datetime.UTC),
        )
        broken = await mark_versions_broken(session, dataset_id, unmapped_removals(d))
        if broken:
            logger.info(
                "schema_change %s: marked metrics broken after unmapped removals: %s",
                proposal["id"],
                ",".join(broken),
            )
    return True
