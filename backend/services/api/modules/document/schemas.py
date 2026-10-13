"""Request and response shapes of the document module (tab 4 A3, group "Tài liệu").
Field types follow tab 3b D4. Responses are the table's columns minus org_id, created_by,
updated_by, deleted_at and storage or secret references (object keys, secret_ref): files are
reached through signed links only (A1.1).
"""

from __future__ import annotations

import datetime
import decimal
import re
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

Media = Literal["pdf", "image", "docx", "email", "audio", "html"]
SourceKind = Literal[
    "smb_folder", "sharepoint", "imap_mailbox", "call_recording_api", "object_store"
]
Resolution = Literal["corrected", "confirmed_mask", "page_dropped", "value_fixed"]

# Five cron fields; the scheduler checks the values themselves.
_CRON = re.compile(r"^\S+( \S+){4}$")


class Accepted(BaseModel):
    """A1.6: the body of a 202; Location names where the result appears."""

    id: uuid.UUID
    status: str
    poll_after_ms: int = 2_000


def _text(value: str) -> str:
    if not value.strip():
        raise ValueError("must not be blank")
    return value.strip()


# --- collections and sources ------------------------------------------------------------------


class CollectionView(BaseModel):
    id: uuid.UUID
    data_space_id: uuid.UUID
    code: str
    name: str
    media_kinds: list[str]
    purpose: str
    pii_policy: str
    retention_days: int | None
    extraction_schema_id: uuid.UUID | None
    qdrant_collection: str
    embed_model: str
    status: str
    created_at: datetime.datetime
    updated_at: datetime.datetime


class CollectionCreate(BaseModel):
    code: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    name: str = Field(min_length=1, max_length=200)
    media_kinds: list[Media] | None = Field(default=None, max_length=6)
    purpose: str = Field(min_length=1, max_length=500)
    pii_policy: Literal["mask", "keep_for_authorized", "reject"] | None = None
    retention_days: int | None = Field(default=None, ge=1, le=36_500)

    _texts = field_validator("name", "purpose")(_text)


class DocumentSourceView(BaseModel):
    id: uuid.UUID
    collection_id: uuid.UUID
    kind: str
    location: str
    sync_cron: str
    last_synced_at: datetime.datetime | None
    status: str
    # Whether credentials are stored; the secret itself is never returned.
    has_credentials: bool
    created_at: datetime.datetime
    updated_at: datetime.datetime


class DocumentSourceCreate(BaseModel):
    collection_id: uuid.UUID
    kind: SourceKind
    location: str = Field(min_length=1, max_length=2_000)
    sync_cron: str = Field(min_length=9, max_length=100)
    credentials: dict[str, str] | None = Field(default=None, max_length=20)

    _location = field_validator("location")(_text)

    @field_validator("sync_cron")
    @classmethod
    def _cron(cls, value: str) -> str:
        if not _CRON.match(value.strip()):
            raise ValueError("sync_cron is a five-field cron expression")
        return value.strip()


# --- documents ----------------------------------------------------------------------------------


class DocumentView(BaseModel):
    id: uuid.UUID
    collection_id: uuid.UUID
    document_source_id: uuid.UUID | None
    sha256: str
    media: str
    file_name: str
    size_bytes: int
    title: str | None
    doc_no: str | None
    issued_on: datetime.date | None
    effective_from: datetime.date | None
    effective_to: datetime.date | None
    supersedes_id: uuid.UUID | None
    parent_document_id: uuid.UUID | None
    page_count: int | None
    status: str
    error: str | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class Progress(BaseModel):
    """How far preprocessing (UC-05-02) has got, from the rows it has written so far."""

    pages_total: int | None
    pages_read: int
    pages_usable: int
    chunks: int
    chunks_indexed: int


class DocumentDetail(DocumentView):
    progress: Progress
    open_quality_issues: int
    blocking_quality_issues: int


# The preprocessing step a document is at, read from its status and the rows the worker has
# written so far (no column records the step): queued until doc_ingest picks it up, then pages,
# passages, vectors; quality_review while a blocking issue waits for a person.
PreprocessingStep = Literal[
    "queued", "reading_pages", "chunking", "indexing", "quality_review", "done", "failed", "removed"
]


class DocumentListItem(DocumentView):
    """A row of GET /v1/documents: the document with its current preprocessing step."""

    step: PreprocessingStep
    progress: Progress


class ValidityChange(BaseModel):
    effective_from: datetime.date | None = None
    effective_to: datetime.date | None = None
    supersedes_id: uuid.UUID | None = None


class DocumentUpdated(DocumentView):
    # UC-05-05: validity changes are proposed for review, not applied; this echoes the proposal.
    proposed_validity: ValidityChange | None = None


class DocumentPatch(BaseModel):
    title: str | None = Field(default=None, max_length=500)
    doc_no: str | None = Field(default=None, max_length=200)
    issued_on: datetime.date | None = None
    effective_from: datetime.date | None = None
    effective_to: datetime.date | None = None
    supersedes_id: uuid.UUID | None = None


class ChunkView(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    collection_id: uuid.UUID
    page_from: int
    page_to: int
    section_path: str | None
    context_header: str | None
    text: str
    tokens: int
    effective_from: datetime.date | None
    effective_to: datetime.date | None
    human_corrected: bool
    injection_suspected: bool
    pii_masked: bool
    created_at: datetime.datetime


class DocumentPageView(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    page_no: int
    page_hash: str
    text_layer: bool
    ocr_conf: decimal.Decimal | None
    text: str | None
    human_corrected: bool
    usable: bool
    created_at: datetime.datetime
    updated_at: datetime.datetime
    # A short-lived signed link to the page image (A1.1); null when the page has none.
    image_url: str | None = None
    chunks: list[ChunkView] = Field(default_factory=list)


class SearchFilters(BaseModel):
    model_config = {"extra": "forbid"}

    document_ids: list[uuid.UUID] | None = Field(default=None, max_length=200)
    media: list[Media] | None = None
    doc_no: str | None = Field(default=None, max_length=200)


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2_000)
    collection_ids: list[uuid.UUID] | None = Field(default=None, max_length=50)
    as_of: datetime.date | None = None
    # UC-05-04 step 2: at most 12 passages reach the model; callers may ask for fewer or a few more.
    top_k: int = Field(default=12, ge=1, le=50)
    filters: SearchFilters | None = None

    _query = field_validator("query")(_text)


class ChunkHit(ChunkView):
    score: float
    document_title: str | None
    doc_no: str | None


# --- quality issues and extraction --------------------------------------------------------------


class QualityIssueView(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    page_no: int | None
    chunk_id: uuid.UUID | None
    extracted_record_id: uuid.UUID | None
    kind: str
    severity: str
    detail: Any
    requires_role: str | None
    status: str
    resolution: str | None
    old_value: str | None
    resolved_by: uuid.UUID | None
    resolved_at: datetime.datetime | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class ResolveIssue(BaseModel):
    resolution: Resolution
    corrected_text: str | None = Field(default=None, max_length=200_000)

    @model_validator(mode="after")
    def _text_for_corrections(self) -> ResolveIssue:
        needs = self.resolution in ("corrected", "value_fixed")
        if needs and not (self.corrected_text or "").strip():
            raise ValueError(f"resolution {self.resolution} needs corrected_text")
        if not needs and self.corrected_text is not None:
            raise ValueError("corrected_text goes with corrected or value_fixed only")
        return self


class ExtractionField(BaseModel):
    name: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    type: str = Field(min_length=1, max_length=50)
    required: bool = False
    pii: bool = False
    target_column: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=1_000)


class ExtractionSchemaView(BaseModel):
    id: uuid.UUID
    collection_id: uuid.UUID
    version: int
    fields: Any
    target_dataset_id: uuid.UUID | None
    join_key: Any
    sample_accuracy: Any
    status: str
    created_at: datetime.datetime
    updated_at: datetime.datetime


class ExtractionSchemaCreate(BaseModel):
    collection_id: uuid.UUID
    fields: list[ExtractionField] = Field(min_length=1, max_length=200)
    target_dataset_id: uuid.UUID | None = None
    join_key: dict[str, Any] | None = None

    @field_validator("fields")
    @classmethod
    def _unique(cls, value: list[ExtractionField]) -> list[ExtractionField]:
        names = [f.name for f in value]
        if len(set(names)) != len(names):
            raise ValueError("field names must be unique")
        return value


class ExtractionTest(BaseModel):
    # UC-05-06 step 2: 10 to 30 sample files; none means the worker picks from the collection.
    sample_document_ids: list[uuid.UUID] | None = Field(default=None, max_length=30)
