"""Data structures and resolution constants for conflict processing."""
from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Any

from libs.basemodel.services_databaseconnector.postgres_model import (
    ConflictResolution,
    ConflictStatus,
)
from libs.database_connector.model_connector import ModelServiceClient
from libs.database_connector.postgres_connector import PostgresClient
from libs.ingestion.graph_store import GraphStore


SUMMARY_FALLBACK_CHARS = 200

KEEP_EXISTING = ConflictResolution.KEEP_EXISTING.value
KEEP_INCOMING = ConflictResolution.KEEP_INCOMING.value
MERGE = ConflictResolution.MERGE.value
RESOLVED = ConflictStatus.RESOLVED.value

MODE_ALL_KEEP_EXISTING = "all_keep_existing"
MODE_SINGLE_KEEP_INCOMING = "single_keep_incoming"
MODE_MERGE = "merge"
MODE_INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class ResolverStores:
    postgres: PostgresClient
    qdrant: Any
    graph: GraphStore
    model: ModelServiceClient
    mongo: Any


@dataclass(frozen=True, slots=True)
class Conflict:
    conflict_id: str
    status: str
    resolution_method: str | None
    resolution_instruction: str | None
    from_recheck: bool
    detected_at: datetime.datetime | None


@dataclass(frozen=True, slots=True)
class BatchContext:
    batch_id: str
    tenant_id: str
    knowledge_base: str
    existing_block_id: str
    resolved_by: str
    conflicts: list[Conflict]


@dataclass(frozen=True, slots=True)
class MergedBlock:
    content: str
    summary: str
    embedding: list[float]
    payload: dict
    graph_entities: list[dict]
    relationships: list[dict]
    keep_table: bool
    table: dict | None
