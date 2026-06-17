"""Business logic for conflict listing, detail, and resolution."""
import datetime
import logging
import os
import uuid
from fastapi import HTTPException
from sqlalchemy import select, update as sa_update

_KAFKA_TOPIC_CONFLICT_RESOLVE = os.getenv("KAFKA_TOPIC_CONFLICT_RESOLVE", "kb.resolve.conflict")

from basemodel.services_databaseconnector.postgres_model import (
    ReadJoinRequest, WhereFilter, OrderBy, OrderDirection,
)
from services.kb_backend.pipeline.kb_ui_operation.pagination import (
    PAGE_SIZE, get_cursor, advance, build_page,
)
from basemodel.services_databaseconnector.postgres_orm.knowledge_base_orm import (
    KBConflictORM,
    KBTextBlockVersionORM,
)
from services.kb_backend.UI_model.response import to_string

log = logging.getLogger(__name__)

# conflict_type, severity, status — wire format = DB enum value.
# UI maps to display labels via src/lib/enums.ts.


def _summary(row: dict) -> dict:
    return {
        "conflict_id":   to_string(row.get("conflict_id")),
        "conflict_type": row.get("conflict_type") or "content_conflict",
        "severity":      row.get("severity") or "medium",
        "detected_at":   to_string(row.get("detected_at", "")),
    }


def _detail(row: dict) -> dict:
    return {
        **_summary(row),
        "where_happens":           f"batch/{to_string(row.get('batch_id'))}" if row.get("batch_id") else "unknown",
        "status":                  row.get("status", "pending"),
        "detailed_explanation":    row.get("detailed_explanation", ""),
        "existing_snapshot":       row.get("existing_snapshot") or {},
        "incoming_snapshot":       row.get("incoming_snapshot") or {},
        "affected_location":       to_string(row.get("conflict_id")),
        "batch_id":                to_string(row.get("batch_id", "")),
        "resolution_instruction":      row.get("resolution_instruction"),
        "selected_resolution_method":  row.get("resolution_method"),
        "resolved_at":  to_string(row.get("resolved_at", "")),
        "resolved_by":  row.get("resolved_by"),
    }


async def list_conflicts(
    postgres,
    tenant_id: str | None,
    user_id: str = "dev-user",
    pending_page: int = 1,
    awaiting_page: int = 1,
    resolved_page: int = 1,
) -> dict:
    batch_resp = await postgres.read(ReadJoinRequest(
        tenant_id=tenant_id, joins_table=["KBConflictBatch"], limit=PAGE_SIZE,
        order_by=OrderBy(table_name="KBConflictBatch", column="inserted_at", order=OrderDirection.ASC),
    ))
    batches = {to_string(r["batch_id"]): r for r in (batch_resp.data or [])}

    # Fetch each status bucket separately so cursors are independent
    async def _fetch_bucket(status: str, page: int) -> list[dict]:
        ckey = f"conflicts:{status}:{tenant_id}"
        cursor_ts = get_cursor(user_id, ckey, page)
        resp = await postgres.read(ReadJoinRequest(
            tenant_id=tenant_id,
            joins_table=["KBConflict"],
            filters=[WhereFilter(table_name="KBConflict", column_name="status", value=status)],
            cursor=cursor_ts,
            limit=PAGE_SIZE,
            order_by=OrderBy(table_name="KBConflict", column="inserted_at", order=OrderDirection.ASC),
        ))
        raw = resp.data or []
        advance(user_id, ckey, page, raw)
        return raw

    pending_raw  = await _fetch_bucket("pending",  pending_page)
    awaiting_raw = await _fetch_bucket("awaiting", awaiting_page)
    resolved_raw = await _fetch_bucket("resolved", resolved_page)

    pending_batches: dict[str, dict] = {}
    for row in pending_raw:
        bid = to_string(row.get("batch_id", ""))
        if bid not in pending_batches:
            batch = batches.get(bid, {})
            pending_batches[bid] = {
                "batch_id":                bid,
                "batch_name":              batch.get("batch_title", f"Batch {bid[:8]}"),
                "extracted_date":          to_string(batch.get("created_at", "")),
                "number_pending_conflict": 0,
                "conflicts":               [],
            }
        pending_batches[bid]["number_pending_conflict"] += 1
        pending_batches[bid]["conflicts"].append(_summary(row))

    return {
        "pending":       build_page(list(pending_batches.values()), pending_page),
        "awaiting":      build_page([_summary(r) for r in awaiting_raw], awaiting_page),
        "resolved":      build_page([_summary(r) for r in resolved_raw], resolved_page),
    }


async def get_conflict(postgres, conflict_id: str, tenant_id: str | None) -> dict:
    resp = await postgres.read(ReadJoinRequest(
        tenant_id=tenant_id,
        joins_table=["KBConflict"],
        filters=[WhereFilter(table_name="KBConflict", column_name="conflict_id", value=conflict_id)],
        limit=1,
    ))
    if resp.code != 200 or not resp.data:
        raise HTTPException(status_code=404, detail="Conflict not found")

    detail = _detail(resp.data[0])

    # Fetch live active-version content for the existing block from Postgres so the
    # UI can show the real stored text, not the frozen snapshot captured at detection time.
    existing_block_id = (detail.get("existing_snapshot") or {}).get("block_id")
    existing_db_content: str | None = None
    if existing_block_id:
        try:
            async with postgres.get_client() as session:
                result = await session.execute(
                    select(KBTextBlockVersionORM.content)
                    .where(KBTextBlockVersionORM.block_id == uuid.UUID(str(existing_block_id)))
                    .where(KBTextBlockVersionORM.is_active.is_(True))
                    .where(KBTextBlockVersionORM.is_deleted.is_(False))
                    .limit(1)
                )
                row = result.first()
                if row:
                    existing_db_content = row[0]
        except Exception as exc:
            log.warning("get_conflict: failed to fetch existing block content block_id=%s: %s", existing_block_id, exc)

    detail["existing_db_content"] = existing_db_content
    return detail


# Methods that require no pipeline work: the incoming chunk was already skipped at
# ingestion, so keeping the existing one (or taking no action) resolves immediately.
# Every other method (keep_incoming, merge, delete) mutates Postgres/Qdrant/Neo4j and
# is therefore queued as AWAITING for the conflict_resolver loop in the worker to execute.
_IMMEDIATE_METHODS = {"keep_existing", "no_action"}


async def resolve_conflict(
    postgres,
    conflict_id: str,
    method: str,
    instruction: str,
    user_id: str,
    kafka_producer=None,
    tenant_id: str | None = None,
) -> dict:
    conflict_uuid = uuid.UUID(conflict_id) if isinstance(conflict_id, str) else conflict_id
    immediate = method in _IMMEDIATE_METHODS
    new_status = "resolved" if immediate else "awaiting"
    values = {
        "status":                 new_status,
        "resolution_method":      method,
        "resolution_instruction": instruction or None,
        "resolved_by":            user_id,
    }
    # Only stamp resolved_at when the conflict is actually done. For AWAITING methods the
    # worker sets resolved_at once it finishes executing the resolution.
    if immediate:
        values["resolved_at"] = datetime.datetime.now(datetime.timezone.utc)

    async with postgres.get_client() as session:
        await session.execute(
            sa_update(KBConflictORM)
            .where(KBConflictORM.conflict_id == conflict_uuid)
            .values(**values)
        )
        await session.commit()
    log.info("resolve_conflict %s method=%s status=%s", conflict_id, method, new_status)

    # For async methods, publish a Kafka event so the worker executes the resolution
    # immediately instead of waiting for the 30-second polling interval.
    # The polling loop in conflict_resolver.py remains as a safety-net fallback.
    if not immediate and kafka_producer is not None:
        try:
            await kafka_producer.produce(
                _KAFKA_TOPIC_CONFLICT_RESOLVE,
                {
                    "event":       "resolve_conflict",
                    "conflict_id": conflict_id,
                    "tenant_id":   tenant_id or "",
                },
                key=conflict_id,
            )
            log.info("resolve_conflict %s published to %s", conflict_id, _KAFKA_TOPIC_CONFLICT_RESOLVE)
        except Exception as exc:
            # Non-fatal: the polling fallback will pick it up within POLL_INTERVAL seconds.
            log.warning(
                "resolve_conflict %s Kafka publish failed: %s — polling fallback will handle it",
                conflict_id, exc,
            )

    return {"conflict_id": conflict_id, "status": new_status}
