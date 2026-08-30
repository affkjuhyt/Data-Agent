"""
Conflict working state (Mongo).

Postgres owns the conflict index — which conflicts a batch has, their status and the
user's chosen method. This module owns only what Postgres should not store:

  kb_conflict_resolution — one doc per batch: the block's progressively merged text,
                           mutated as each incoming chunk is folded in.
  kb_conflict_payload    — one doc per conflict: the incoming chunk the resolver
                           replays, keyed by (tenant_id, conflict_id).

The payload's replay fields are dropped once its batch resolves; content and summary
stay for the archive view.
"""
from __future__ import annotations

import datetime

from libs.basemodel.services_databaseconnector.mongo_model import (
    CONFLICT_PAYLOAD_COLLECTION,
    CONFLICT_RESOLUTION_COLLECTION,
)

# ── Constants

# Only the resolver replays these, so they are dead the moment a batch resolves.
REPLAY_FIELDS = (
    "embedding", "entities", "relationships", "table", "table_involved",
    "embed_model_id", "block_index", "approved_by",
)


# ── Shared helpers

def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _batches(database):
    return database[CONFLICT_RESOLUTION_COLLECTION]


def _payloads(database):
    return database[CONFLICT_PAYLOAD_COLLECTION]


async def ensure_indexes(database) -> None:
    """Create the lookup indexes both collections are read by. Idempotent."""
    await _batches(database).create_index("batch_id", unique=True)
    await _payloads(database).create_index([("tenant_id", 1), ("conflict_id", 1)], unique=True)


# ── Batch working text

async def create_resolution_batch(
    database, *, batch_id, tenant_id, knowledge_base, existing_summary, existing_content,
) -> None:
    """Open a batch's working document, seeded with the block's current text.
    The block it is anchored on is KBConflictBatch.existing_block_id — not repeated here.
    """
    now = _now_iso()
    await _batches(database).insert_one({
        "batch_id": batch_id,
        "tenant_id": tenant_id,
        "knowledge_base": knowledge_base,
        "existing_summary": existing_summary,
        "working_content": existing_content,
        "working_summary": existing_summary,
        "created_at": now,
        "updated_at": now,
    })


async def load_resolution_batch(database, batch_id) -> dict | None:
    """Return a batch's working document, or None."""
    return await _batches(database).find_one({"batch_id": batch_id})


async def update_working_block(database, batch_id, *, working_content, working_summary) -> None:
    """Overwrite the batch's progressively-merged working text."""
    await _batches(database).update_one(
        {"batch_id": batch_id},
        {"$set": {
            "working_content": working_content,
            "working_summary": working_summary,
            "updated_at": _now_iso(),
        }},
    )


# ── Conflict payloads

async def save_payload(database, payload: dict) -> None:
    """Store one conflict's incoming chunk."""
    await _payloads(database).insert_one({**payload, "created_at": _now_iso()})


async def load_payload(database, tenant_id, conflict_id) -> dict | None:
    """Return one conflict's incoming chunk, or None."""
    return await _payloads(database).find_one({"tenant_id": tenant_id, "conflict_id": conflict_id})


async def mark_payload_committed(database, tenant_id, conflict_id, block_id: str) -> None:
    """Record the block a conflict's chunk was committed as, so a retry re-uses it."""
    await _payloads(database).update_one(
        {"tenant_id": tenant_id, "conflict_id": conflict_id},
        {"$set": {"committed_block_id": block_id}},
    )


async def load_payloads(database, tenant_id, conflict_ids: list[str]) -> dict[str, dict]:
    """Return conflict_id → incoming chunk for the given conflicts, in one query."""
    if not conflict_ids:
        return {}
    cursor = _payloads(database).find({"tenant_id": tenant_id, "conflict_id": {"$in": conflict_ids}})
    return {payload["conflict_id"]: payload async for payload in cursor}


async def drop_payloads(database, tenant_id, conflict_ids: list[str]) -> None:
    """Delete the given conflicts' payloads outright."""
    if not conflict_ids:
        return
    await _payloads(database).delete_many(
        {"tenant_id": tenant_id, "conflict_id": {"$in": conflict_ids}}
    )


async def strip_replayed_payloads(database, tenant_id, conflict_ids: list[str]) -> None:
    """Drop the replay fields from resolved conflicts, keeping content/summary for the archive."""
    if not conflict_ids:
        return
    await _payloads(database).update_many(
        {"tenant_id": tenant_id, "conflict_id": {"$in": conflict_ids}},
        {"$unset": {field: "" for field in REPLAY_FIELDS}},
    )


# ── Batch teardown

async def drop_batch_working_text(database, batch_id) -> None:
    """Drop a resolved batch's working text — Postgres now holds the final version."""
    await _batches(database).update_one(
        {"batch_id": batch_id},
        {"$unset": {"working_content": "", "working_summary": ""},
         "$set": {"updated_at": _now_iso()}},
    )


async def drop_batches(database, batch_ids: list[str]) -> None:
    """Delete batch working documents outright."""
    if not batch_ids:
        return
    await _batches(database).delete_many({"batch_id": {"$in": batch_ids}})
