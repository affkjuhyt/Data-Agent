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
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db import scoped_session, uuid7
from libs.dataagent_db.models import catalog
from services.worker_agent.events import Event

logger = logging.getLogger(__name__)

ROLE = "svc_catalog"
KIND = "schema_change"
NAMESPACE = uuid.UUID("6f0c6a59-3b0f-4f55-9a52-1f2f6e1f0c3a")

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


@dataclass
class Diff:
    added: list[dict[str, Any]] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    retyped: list[dict[str, str]] = field(default_factory=list)  # name, before, after
    nullable_now: list[str] = field(default_factory=list)
    not_null_now: list[str] = field(default_factory=list)

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
    for column in (await session.scalars(live_columns(dataset.id, old_version))).all():
        if column.name in closing:
            column.removed_in_version = new_version
        elif column.name in by_name:
            found = by_name[column.name]
            column.is_nullable = bool(found["is_nullable"])
            column.ordinal = int(found["ordinal"])
    for c in [*d.added, *(by_name[r["name"]] for r in d.retyped)]:
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


async def propose(event: Event, pending: Sequence[Pending], proposed_by: uuid.UUID) -> None:
    """One schema_change proposal per table and new structure: its id comes from both, so a scan
    that runs again finds the one it opened."""
    from services.worker_agent import proposals

    for p in pending:
        pid = uuid.uuid5(NAMESPACE, f"{p.dataset_id}:{schema_hash(p.columns)}")
        keyed = dataclasses.replace(event, payload={**event.payload, "proposal_id": str(pid)})
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
    return True
