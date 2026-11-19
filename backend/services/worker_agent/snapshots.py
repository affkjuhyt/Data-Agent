"""A dataset version written whole becomes its Parquet snapshot (DF-05, F-01).

Every writer of the workspace announces a version on catalog.outbox (`dataset.version_created`,
libs/dataagent_db/versioning.announce) in the transaction that catalogues it. This handler, the
one with MinIO credentials and a reader of the workspace (svc_query), copies the version's table to
da-snapshots (libs/snapshot.writer), records catalog.dataset_snapshot, `ok` or `skipped` with the
reason in the log, and announces `dataset.snapshot_created` for what profiles and checks a new
version (API_AND_SCHEMA_CONTRACTS §5).

At least once: a redelivered event finds the version's row and does nothing; a file written
before a failure is content-addressed, so writing it again stores nothing new.
"""

from __future__ import annotations

import datetime
import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from libs.dataagent_db import scoped_session, uuid7, versioning
from libs.dataagent_db.models import catalog
from libs.snapshot import writer
from libs.snapshot.store import MinioStore, SnapshotStore, StoreUnavailable
from services.worker_agent.events import Event, Handler
from services.worker_agent.query.executor import ROLE as QUERY_ROLE

logger = logging.getLogger(__name__)

SNAPSHOT_CREATED = "dataset.snapshot_created"
CATALOG_ROLE = "svc_catalog"

_store: SnapshotStore | None = None


def store() -> SnapshotStore:
    global _store
    if _store is None:
        _store = MinioStore.from_env()
    return _store


async def version_created(event: Event) -> None:
    p = event.payload
    dataset_id = uuid.UUID(str(p["dataset_id"]))
    version = int(p["version"])
    physical = str(p["physical_table"])
    available_at = datetime.datetime.fromisoformat(str(p["available_at"]))
    run_id = uuid.UUID(str(p["run_id"])) if p.get("run_id") else None
    async with scoped_session(CATALOG_ROLE, event.org_id) as session:
        done = await session.scalar(
            select(catalog.DatasetSnapshot.id).where(
                catalog.DatasetSnapshot.dataset_id == dataset_id,
                catalog.DatasetSnapshot.version == version,
            )
        )
    if done is not None or not writer.ENABLED:
        return
    try:
        target = store()
    except StoreUnavailable as exc:
        outcome: writer.Snapshot | writer.Skipped = writer.Skipped(str(exc))
    else:
        async with scoped_session(QUERY_ROLE, event.org_id) as session:
            raw = await (await session.connection()).get_raw_connection()
            outcome = await writer.write(
                raw.driver_connection,
                schema="workspace",
                table=physical,
                org_id=event.org_id,
                dataset_id=dataset_id,
                version=version,
                run_id=run_id,
                available_at=available_at,
                store=target,
            )
    await _record(event.org_id, dataset_id, version, available_at, run_id, p, outcome)


async def _record(
    org_id: uuid.UUID,
    dataset_id: uuid.UUID,
    version: int,
    available_at: datetime.datetime,
    run_id: uuid.UUID | None,
    payload: dict[str, Any],
    outcome: writer.Snapshot | writer.Skipped,
) -> None:
    if isinstance(outcome, writer.Snapshot):
        stored: dict[str, Any] = {
            "object_key": outcome.object_key,
            "sha256": outcome.sha256,
            "row_count": outcome.row_count,
            "byte_size": outcome.byte_size,
            "columns": outcome.columns,
        }
    else:
        logger.warning(
            "worker-agent | dataset %s v%s: snapshot skipped: %s",
            dataset_id,
            version,
            outcome.reason,
        )
        stored = {
            "object_key": None,
            "sha256": None,
            "row_count": outcome.row_count or 0,
            "byte_size": None,
            "columns": [],
        }
    ok = outcome.status == "ok"
    snapshot_id = uuid7()
    async with scoped_session(CATALOG_ROLE, org_id) as session:
        inserted = await session.scalar(
            insert(catalog.DatasetSnapshot)
            .values(
                id=snapshot_id,
                org_id=org_id,
                dataset_id=dataset_id,
                version=version,
                status=outcome.status,
                available_at=available_at,
                created_by_run_id=run_id,
                created_by_kind=str(payload.get("created_by_kind") or "job_run"),
                **stored,
            )
            .on_conflict_do_nothing(index_elements=["dataset_id", "version"])
            .returning(catalog.DatasetSnapshot.id)
        )
        if inserted is None:
            return  # another delivery recorded it first
        session.add(
            catalog.Outbox(
                id=uuid7(),
                org_id=org_id,
                aggregate_type="dataset",
                aggregate_id=dataset_id,
                event_type=SNAPSHOT_CREATED,
                payload={
                    "dataset_id": str(dataset_id),
                    "version": version,
                    "snapshot_id": str(snapshot_id) if ok else None,
                    "status": outcome.status,
                    "sha256": stored["sha256"],
                    "row_count": stored["row_count"],
                    "available_at": available_at.isoformat(),
                },
            )
        )
    if ok:
        logger.info(
            "worker-agent | dataset %s v%s snapshot %s (%d rows, %d bytes)",
            dataset_id,
            version,
            stored["object_key"],
            stored["row_count"],
            stored["byte_size"],
        )


HANDLERS: dict[str, Handler] = {versioning.VERSION_CREATED: version_created}
