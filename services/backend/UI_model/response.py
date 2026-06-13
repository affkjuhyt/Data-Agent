from __future__ import annotations

import json
from typing import Any
from pydantic import BaseModel, Field, ConfigDict, computed_field, model_validator


# ── Row-shaping helpers (formerly services/parse_for_ui/mappers.py) ───────────

def to_string(v) -> str:
    return str(v) if v is not None else ""


def parse_jsonb(v) -> dict:
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return {}
    return v or {}


# ── Fleet ─────────────────────────────────────────────────────────────────────

class FleetContent(BaseModel):
    documents: int
    web: int
    media: int
    warehouses: int


class FleetStats(BaseModel):
    content: FleetContent
    qdrant_collections: int
    neo4j_nodes: int
    neo4j_relationships: int
    unresolved_conflict_batches: int


# ── Document ──────────────────────────────────────────────────────────────────
# Field names match Postgres column names exactly. No renames.
# `doc_type` and `status` are computed from other fields, declared explicitly.

class DocumentMetadata(BaseModel):
    """Contents of the KBData.metadata JSONB column, with language pulled in.

    Variant fields per source_type:
      doc:       author, published_date, file_size
      web:       url, web_name, language
      image:     width, height, color_space, file_size
      video:     width, height, total_frame, file_size
      warehouse: warehouse_type
    `author` here is the **document's** author (PDF metadata, web meta tag, etc.),
    not the user who uploaded — that's `DocResponse.added_by`.
    """
    doc_type: str | None = None       # computed from source_type + extension
    language: str | None = None
    access_role: str | None = None
    url: str | None = None
    author: str | None = None         # nullable; only set when known
    published_date: str | None = None
    warehouse_type: str | None = None
    width: int | None = None
    height: int | None = None
    color_space: str | None = None
    file_size: int | None = None
    total_frame: int | None = None


class DocResponse(BaseModel):
    data_id: str
    name: str
    source_type: str
    extension: str | None = None
    current_tier: str                 # bronze | silver | gold
    added_by: str | None = None       # user_id of uploader (from dependency)
    added_on: str | None = None       # timestamp set at insert
    abstract: str | None = None
    metadata: DocumentMetadata

    @computed_field
    @property
    def status(self) -> str:
        """Lifecycle status derived from tier.
        Detailed pipeline status lives in kb_ingestion_logs (Mongo)."""
        return {"gold": "PUBLISHED", "silver": "EMBEDDING"}.get(self.current_tier, "RAW")

    @model_validator(mode="before")
    @classmethod
    def _from_row(cls, value: Any) -> Any:
        """Accept a raw DB row dict: parse JSONB metadata, compute doc_type,
        stringify UUID/datetime fields. Pass through if already shaped."""
        if not isinstance(value, dict):
            return value
        row = dict(value)

        meta = parse_jsonb(row.get("metadata"))
        st  = row.get("source_type", "doc")
        ext = (row.get("extension") or "").upper()
        if not meta.get("doc_type"):
            meta["doc_type"] = (
                f"Doc/{ext}"   if st == "doc"   and ext else
                "Web"          if st == "web"           else
                f"Image/{ext}" if st == "image" and ext else
                "Image"        if st == "image"         else
                f"Video/{ext}" if st == "video" and ext else
                "Video"        if st == "video"         else
                f"Warehouse/{meta.get('warehouse_type', '')}" if st == "warehouse" else
                st
            )
        if row.get("language") and not meta.get("language"):
            meta["language"] = row["language"]
        row["metadata"] = meta

        if row.get("current_tier"):
            row["current_tier"] = str(row["current_tier"]).lower()

        for k in ("data_id", "added_by", "added_on"):
            if row.get(k) is not None and not isinstance(row[k], str):
                row[k] = str(row[k])

        return row


# ── Chunks ────────────────────────────────────────────────────────────────────

class ChunkVersion(BaseModel):
    version_number: str
    create_at: str
    status: str
    embedding_models: str
    entities: list[Any]
    intent: str
    text: str


class ChunkResponse(BaseModel):
    id: str
    title: str
    text: str
    versions: list[ChunkVersion]


# ── Tables ────────────────────────────────────────────────────────────────────

class TableColumn(BaseModel):
    name: str
    type: str
    nullable: bool


class TableResponse(BaseModel):
    id: str
    name: str
    description: str
    columns: list[TableColumn]
    rows: list[dict[str, Any]]


# ── Warehouse configs ─────────────────────────────────────────────────────────

class WarehouseConfigResponse(BaseModel):
    id: str
    version: Any | None
    active: bool
    host: str | None
    database: str | None
    selected_tables: list[str]
    sync_schedule: str | None
    schema_filter: list[str]
    created_at: str


# ── Qdrant ────────────────────────────────────────────────────────────────────

class QdrantCollectionResponse(BaseModel):
    id: str
    name: str | None
    active: bool
    points: int
    dimensions: int
    distance: str
    indexed: int
    embedding_model: str | None


class QdrantPointResponse(BaseModel):
    point_id: str
    score: float
    summary: str
    entities: list[Any]
    intent: list[Any]


# ── Neo4j ─────────────────────────────────────────────────────────────────────

class Neo4jNode(BaseModel):
    id: str
    name: str | None
    description: str | None


class Neo4jEdge(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    source: str = Field(alias="from")
    to: str
    description: str | None
    score: float | None


class Neo4jGraphResponse(BaseModel):
    nodes: list[Neo4jNode]
    edges: list[Neo4jEdge]


class Neo4jSchemaResponse(BaseModel):
    entities: list[str]
    connections: dict[str, list[str]]


# ── Conflicts ─────────────────────────────────────────────────────────────────

class ConflictSummary(BaseModel):
    conflict_id: str
    conflict_type: str
    severity: str
    detected_at: str


class PendingConflictBatch(BaseModel):
    batch_id: str
    batch_name: str | None
    extracted_date: str
    number_pending_conflict: int
    conflicts: list[ConflictSummary]


class ConflictBucketsResponse(BaseModel):
    pending: list[PendingConflictBatch]
    awaiting: list[ConflictSummary]
    resolved: list[ConflictSummary]


class ConflictDetailResponse(BaseModel):
    conflict_id: str
    conflict_type: str
    where_happens: str
    severity: str
    detected_at: str
    status: str | None
    batch_id: str
    detailed_explanation: str
    existing_snapshot: dict[str, Any]
    incoming_snapshot: dict[str, Any]
    affected_location: str
    resolution_instruction: str
    selected_resolution_method: str | None
    resolved_at: str | None
    resolved_by: str | None


# ── Policies ──────────────────────────────────────────────────────────────────

class FilterPolicyResponse(BaseModel):
    id: str
    name: str | None
    type: str
    content: str
    added_by: str
    added_when: str
    active: bool


class ExtractionPolicyResponse(BaseModel):
    base: str
    custom: str


# ── Mutation / action responses ───────────────────────────────────────────────

class StatusResponse(BaseModel):
    status: str

class ConfigActivateResponse(BaseModel):
    status: str
    config_id: str

class VersionCreatedResponse(BaseModel):
    version_id: str | None = None
    version_number: int

class ConfigCreatedResponse(BaseModel):
    config_id: str | None = None
    version_number: int

class PolicyCreatedResponse(BaseModel):
    policy_id: str | None = None

class ConflictResolvedResponse(BaseModel):
    conflict_id: str
    status: str

class ToggleCollectionResponse(BaseModel):
    collection_id: str
    active: bool

class RowUpdateResponse(BaseModel):
    status: str
    row: dict[str, Any]
