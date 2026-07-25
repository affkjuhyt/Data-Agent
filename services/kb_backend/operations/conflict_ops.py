"""Business logic for conflict listing, detail, and resolution.

Postgres is the conflict index — type, severity, status, the user's chosen method.
The text each conflict is about lives in Mongo: the batch's working document holds the
existing side, the payload document holds the incoming chunk.
"""
from typing import Any
import logging
import os
import uuid

from fastapi import HTTPException
from sqlalchemy import String, column, select, update as sa_update, values
from sqlalchemy.dialects.postgresql import UUID as PGUUID

from libs.basemodel.services_databaseconnector.mongo_model import (
    CONFLICT_PAYLOAD_COLLECTION,
    CONFLICT_RESOLUTION_COLLECTION,
)
from libs.basemodel.services_databaseconnector.postgres_model import (
    ConflictResolution, ReadJoinRequest, WhereFilter, OrderBy, OrderDirection,
)
from libs.basemodel.services_databaseconnector.postgres_orm.knowledge_base_orm import (
    KBConflictBatchORM,
    KBConflictORM,
    KBTextBlockVersionORM,
)
from services.kb_backend.operations.pagination import (
    PAGE_SIZE, get_cursor, advance, build_page,
)
from services.kb_backend.schemas import to_string

log = logging.getLogger(__name__)


# ── Constants ─────────────────────────────────────────────────────────────────

_KAFKA_TOPIC_CONFLICT_RESOLVE = os.getenv("KAFKA_TOPIC_CONFLICT_RESOLVE", "kb.resolve.conflict")

# One batch's conflicts, read in a single page — a batch is one block's collisions.
_BATCH_CONFLICT_SCAN_LIMIT = 500

# conflict_type, severity, status — wire format = DB enum value.
# UI maps to display labels via src/lib/enums.ts.
_STATUS_PENDING = "pending"
_STATUS_AWAITING = "awaiting"
_STATUS_RESOLVED = "resolved"

_KEEP_EXISTING = ConflictResolution.KEEP_EXISTING.value
_KEEP_INCOMING = ConflictResolution.KEEP_INCOMING.value
_MERGE = ConflictResolution.MERGE.value

MODE_ALL_KEEP_EXISTING = "all_keep_existing"
MODE_SINGLE_KEEP_INCOMING = "single_keep_incoming"
MODE_MERGE = "merge"
MODE_INVALID = "invalid"


# ── Shared helpers ────────────────────────────────────────────────────────────

def _summary(row: dict[str, Any]) -> dict[str, Any]:
    """The fields a conflict list row shows."""
    return {
        "conflict_id":   to_string(row.get("conflict_id")),
        "conflict_type": row.get("conflict_type") or "content_conflict",
        "severity":      row.get("severity") or "medium",
        "detected_at":   to_string(row.get("detected_at", "")),
    }


def _detail(row: dict[str, Any]) -> dict[str, Any]:
    """The index row's own fields. Both snapshots come from Mongo — see get_conflict."""
    return {
        **_summary(row),
        "where_happens":           f"batch/{to_string(row.get('batch_id'))}" if row.get("batch_id") else "unknown",
        "status":                  row.get("status", _STATUS_PENDING),
        "detailed_explanation":    row.get("detailed_explanation", ""),
        "affected_location":       to_string(row.get("conflict_id")),
        "batch_id":                to_string(row.get("batch_id", "")),
        "resolution_instruction":      row.get("resolution_instruction"),
        "selected_resolution_method":  row.get("resolution_method"),
        "resolved_at":  to_string(row.get("resolved_at", "")),
        "resolved_by":  row.get("resolved_by"),
    }


def _classify_batch_resolution(methods: list[str]) -> str:
    """The batch's resolution mode, or MODE_INVALID when the selection breaks the rules."""
    if not methods or any(method is None for method in methods):
        return MODE_INVALID
    distinct_methods = set(methods)
    if distinct_methods == {_KEEP_EXISTING}:
        return MODE_ALL_KEEP_EXISTING
    if methods.count(_KEEP_INCOMING) == 1 and distinct_methods <= {_KEEP_INCOMING, _KEEP_EXISTING}:
        return MODE_SINGLE_KEEP_INCOMING
    if _MERGE in distinct_methods and _KEEP_INCOMING not in distinct_methods:
        return MODE_MERGE
    return MODE_INVALID


# ── Readers ───────────────────────────────────────────────────────────────────

async def _fetch_status_bucket(postgres, tenant_id, user_id, status: str, page: int) -> list[dict]:
    """One status bucket's conflicts, on its own cursor so the buckets page independently."""
    cursor_key = f"conflicts:{status}:{tenant_id}"
    response = await postgres.read(ReadJoinRequest(
        tenant_id=tenant_id,
        joins_table=["KBConflict"],
        filters=[WhereFilter(table_name="KBConflict", column_name="status", value=status)],
        cursor=get_cursor(user_id, cursor_key, page),
        limit=PAGE_SIZE,
        order_by=OrderBy(table_name="KBConflict", column="inserted_at", order=OrderDirection.ASC),
    ))
    rows = response.data or []
    advance(user_id, cursor_key, page, rows)
    return rows


async def _fetch_batch_anchor(postgres, batch_id: str) -> str | None:
    """The block a batch is anchored on."""
    try:
        async with postgres.get_client() as session:
            block_id = await session.scalar(
                select(KBConflictBatchORM.existing_block_id)
                .where(KBConflictBatchORM.batch_id == uuid.UUID(str(batch_id)))
            )
        return str(block_id) if block_id else None
    except Exception as exc:
        log.warning("get_conflict: failed to read batch anchor batch_id=%s: %s", batch_id, exc)
        return None


async def _fetch_batch_document(mongo, batch_id: str, tenant_id: str | None) -> dict:
    """The batch's working document (its merged text). Tenant-scoped like every read."""
    try:
        document = await mongo.get_client()[CONFLICT_RESOLUTION_COLLECTION].find_one(
            {"batch_id": batch_id, "tenant_id": tenant_id}
        )
    except Exception as exc:
        log.warning("get_conflict: failed to read working document batch_id=%s: %s", batch_id, exc)
        return {}
    return document or {}


async def _fetch_conflict_payload(mongo, conflict_id: str, tenant_id: str | None) -> dict:
    """The conflict's incoming chunk. Its replay fields are stripped once resolved."""
    try:
        document = await mongo.get_client()[CONFLICT_PAYLOAD_COLLECTION].find_one(
            {"conflict_id": conflict_id, "tenant_id": tenant_id}
        )
    except Exception as exc:
        log.warning("get_conflict: failed to read payload conflict_id=%s: %s", conflict_id, exc)
        return {}
    return document or {}


async def _fetch_final_block_content(postgres, block_id: str) -> str | None:
    """The block's stable active-version text from Postgres (final state)."""
    try:
        async with postgres.get_client() as session:
            result = await session.execute(
                select(KBTextBlockVersionORM.content)
                .where(KBTextBlockVersionORM.block_id == uuid.UUID(str(block_id)))
                .where(KBTextBlockVersionORM.is_active.is_(True))
                .where(KBTextBlockVersionORM.is_deleted.is_(False))
                .limit(1)
            )
            row = result.first()
            return row[0] if row else None
    except Exception as exc:
        log.warning("get_conflict: failed to fetch block content block_id=%s: %s", block_id, exc)
        return None


# ── Resolution writers ────────────────────────────────────────────────────────

def _required_choices(open_conflicts: list[dict], resolutions: list[dict]) -> list[tuple]:
    """Every open conflict as (conflict_id, method, instruction); 400s unless all were assigned."""
    choice_by_conflict_id = {choice["conflict_id"]: choice for choice in resolutions}
    chosen: list[tuple] = []
    for row in open_conflicts:
        conflict_id = to_string(row.get("conflict_id"))
        choice = choice_by_conflict_id.get(conflict_id)
        if not choice or not choice.get("selected_resolution_method"):
            raise HTTPException(status_code=400, detail="Every conflict in the batch must be resolved")
        chosen.append((
            uuid.UUID(conflict_id),
            choice["selected_resolution_method"],
            choice.get("resolution_instruction") or None,
        ))
    return chosen


async def _stamp_batch_awaiting(
    postgres, batch_id: str, tenant_id: str | None, resolved_by: str, choices: list[tuple],
) -> None:
    """Move the batch and its chosen conflicts to AWAITING in one transaction.

    The conflicts go in a single UPDATE ... FROM (VALUES ...): every row carries its own
    method and instruction, but they are independent, so there is nothing to sequence.
    """
    tenant_uuid = uuid.UUID(tenant_id) if tenant_id else None
    chosen = values(
        column("conflict_id", PGUUID(as_uuid=True)),
        column("resolution_method", String),
        column("resolution_instruction", String),
        name="chosen",
    ).data(choices)

    async with postgres.get_client() as session:
        try:
            await session.execute(
                sa_update(KBConflictORM)
                .where(KBConflictORM.conflict_id == chosen.c.conflict_id)
                .where(KBConflictORM.tenant_id == tenant_uuid)
                .values(
                    status=_STATUS_AWAITING,
                    resolution_method=chosen.c.resolution_method,
                    resolution_instruction=chosen.c.resolution_instruction,
                    resolved_by=resolved_by,
                )
            )
            await session.execute(
                sa_update(KBConflictBatchORM)
                .where(KBConflictBatchORM.batch_id == uuid.UUID(batch_id))
                .where(KBConflictBatchORM.tenant_id == tenant_uuid)
                .values(status=_STATUS_AWAITING)
            )
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def _publish_resolve_event(kafka_producer, batch_id: str, tenant_id: str | None) -> None:
    """Hand the batch to the worker. Best-effort — the resolver's poll loop is the fallback."""
    if kafka_producer is None:
        return
    try:
        await kafka_producer.produce(
            _KAFKA_TOPIC_CONFLICT_RESOLVE,
            {"event": "resolve_batch", "batch_id": batch_id, "tenant_id": tenant_id or ""},
            key=batch_id,
        )
        log.info("resolve_batch %s published to %s", batch_id, _KAFKA_TOPIC_CONFLICT_RESOLVE)
    except Exception as exc:
        log.warning("resolve_batch %s Kafka publish failed: %s — polling fallback will handle it",
                    batch_id, exc)


# ── Operations ────────────────────────────────────────────────────────────────

async def list_conflicts(
    postgres: Any,
    tenant_id: str | None,
    user_id: str,
    pending_page: int = 1,
    awaiting_page: int = 1,
    resolved_page: int = 1,
) -> dict[str, Any]:
    """The three status buckets; pending conflicts are grouped under their batch."""
    batch_response = await postgres.read(ReadJoinRequest(
        tenant_id=tenant_id, joins_table=["KBConflictBatch"], limit=PAGE_SIZE,
        order_by=OrderBy(table_name="KBConflictBatch", column="inserted_at", order=OrderDirection.ASC),
    ))
    batch_by_id = {to_string(row["batch_id"]): row for row in (batch_response.data or [])}

    pending_rows  = await _fetch_status_bucket(postgres, tenant_id, user_id, _STATUS_PENDING,  pending_page)
    awaiting_rows = await _fetch_status_bucket(postgres, tenant_id, user_id, _STATUS_AWAITING, awaiting_page)
    resolved_rows = await _fetch_status_bucket(postgres, tenant_id, user_id, _STATUS_RESOLVED, resolved_page)

    pending_batches: dict[str, dict[str, Any]] = {}
    for row in pending_rows:
        batch_id = to_string(row.get("batch_id", ""))
        if batch_id not in pending_batches:
            batch = batch_by_id.get(batch_id, {})
            pending_batches[batch_id] = {
                "batch_id":                batch_id,
                "batch_name":              batch.get("batch_title", f"Batch {batch_id[:8]}"),
                "extracted_date":          to_string(batch.get("created_at", "")),
                "number_pending_conflict": 0,
                "conflicts":               [],
            }
        pending_batches[batch_id]["number_pending_conflict"] += 1
        pending_batches[batch_id]["conflicts"].append(_summary(row))

    return {
        "pending":  build_page(list(pending_batches.values()), pending_page),
        "awaiting": build_page([_summary(row) for row in awaiting_rows], awaiting_page),
        "resolved": build_page([_summary(row) for row in resolved_rows], resolved_page),
    }


async def get_conflict(postgres: Any, mongo: Any, conflict_id: str, tenant_id: str | None) -> dict[str, Any]:
    """One conflict: its Postgres index row, with both snapshots assembled from Mongo."""
    response = await postgres.read(ReadJoinRequest(
        tenant_id=tenant_id,
        joins_table=["KBConflict"],
        filters=[WhereFilter(table_name="KBConflict", column_name="conflict_id", value=conflict_id)],
        limit=1,
    ))
    if response.code != 200 or not response.data:
        raise HTTPException(status_code=404, detail="Conflict not found")

    detail = _detail(response.data[0])
    batch_id = detail["batch_id"]
    batch = await _fetch_batch_document(mongo, batch_id, tenant_id) if batch_id else {}
    payload = await _fetch_conflict_payload(mongo, detail["conflict_id"], tenant_id)
    existing_block_id = await _fetch_batch_anchor(postgres, batch_id) if batch_id else None

    detail["existing_snapshot"] = {
        "block_id": existing_block_id,
        # The existing side is the merged working text while the batch is open.
        "summary": batch.get("working_summary") or batch.get("existing_summary") or "",
    }
    detail["incoming_snapshot"] = {
        "summary": payload.get("summary") or "",
        "content": payload.get("content") or "",
    }
    # Postgres holds the stable version only once the batch resolves; until then the
    # existing side is Mongo's progressively merged text.
    detail["existing_db_content"] = (
        await _fetch_final_block_content(postgres, existing_block_id)
        if detail["status"] == _STATUS_RESOLVED and existing_block_id
        else batch.get("working_content")
    )
    return detail


async def resolve_batch(
    postgres: Any,
    batch_id: str,
    resolutions: list[dict],
    resolved_by: str,
    kafka_producer: Any = None,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """Apply the user's per-conflict choices for a whole batch and hand it to the worker."""
    response = await postgres.read(ReadJoinRequest(
        tenant_id=tenant_id,
        joins_table=["KBConflict"],
        filters=[WhereFilter(table_name="KBConflict", column_name="batch_id", value=batch_id)],
        limit=_BATCH_CONFLICT_SCAN_LIMIT,
    ))
    rows = response.data or []
    open_conflicts = [row for row in rows if row.get("status") != _STATUS_RESOLVED]
    if not open_conflicts:
        raise HTTPException(status_code=404, detail="No open conflicts in this batch")

    choices = _required_choices(open_conflicts, resolutions)

    # Classify over the WHOLE batch, not just the open conflicts: the worker does, so
    # validating a subset would accept a selection it then rejects, bouncing the batch
    # between AWAITING and the poll loop forever.
    settled_methods = [row.get("resolution_method") for row in rows if row.get("status") == _STATUS_RESOLVED]
    selected_methods = [method for _, method, _ in choices]
    if _classify_batch_resolution(selected_methods + settled_methods) == MODE_INVALID:
        raise HTTPException(
            status_code=400,
            detail="Invalid selection: keep_existing needs all conflicts; keep_incoming needs "
                   "exactly one with the rest keep_existing; merge cannot combine with "
                   "keep_incoming, including conflicts already merged earlier in this batch",
        )

    await _stamp_batch_awaiting(postgres, batch_id, tenant_id, resolved_by, choices)
    log.info("resolve_batch %s conflicts=%d status=awaiting", batch_id, len(choices))

    await _publish_resolve_event(kafka_producer, batch_id, tenant_id)
    return {"batch_id": batch_id, "status": _STATUS_AWAITING}
