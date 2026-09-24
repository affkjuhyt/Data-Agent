from __future__ import annotations

import datetime
from typing import Annotated, Any, Literal, Optional, Union

from pydantic import BaseModel, Field, computed_field, model_validator

from services.kb_backend.schemas.common import coerce_jsonb_dict
from services.kb_backend.schemas.enums import SourceType


class Document(BaseModel):
    source_type: Literal[SourceType.DOC]
    doc_type: str
    author: str | None
    published_date: datetime.datetime | None


class Image(BaseModel):
    source_type: Literal[SourceType.IMAGE]
    video_type: str
    height: int
    width: int
    color_space: str | None


class Video(BaseModel):
    source_type: Literal[SourceType.VIDEO]
    video_type: str
    height: int
    width: int
    codec: str | None
    total_frame: int


class Web(BaseModel):
    source_type: Literal[SourceType.WEB]
    url: str
    web_name: str


MetadataType = Annotated[
    Union[Document, Image, Video, Web],
    Field(discriminator="source_type"),
]


class DocumentMetadata(BaseModel):
    doc_type: str | None = None
    language: str | None = None
    access_role: str | None = None
    url: str | None = None
    author: str | None = None
    published_date: str | None = None
    warehouse_type: str | None = None
    width: int | None = None
    height: int | None = None
    color_space: str | None = None
    file_size: int | None = None
    total_frame: int | None = None
    intents: list[str] = []
    # Set only for documents the web-crawler worker created (register_http_file_bronze
    # / register_web_bronze stamp it into doc_metadata) — absent for a manual upload,
    # which is always manual. Lets Data Layers hide "Process to Silver/Gold" for a
    # document an auto-promote source is already driving, for ANY viewer, not just
    # the browser tab that happened to trigger the crawl.
    promotion_mode: str | None = None
    # Stamped by `libs.ingestion.pipeline_status.stamp_pipeline_status` at the
    # start/end of each Silver or Gold attempt. Without this, "Auto-processing
    # to Silver/Gold..." (AssetInventory.tsx) only checks promotion_mode +
    # current_tier, so a document whose attempt genuinely failed (unsupported
    # extension, corrupt file) looked exactly like one still in flight forever.
    last_pipeline_stage: str | None = None
    last_pipeline_status: str | None = None
    last_pipeline_error: str | None = None


class UpdateDocumentRequest(BaseModel):
    current_tier: Optional[str] = None
    status: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None


class DocResponse(BaseModel):
    data_id: str
    name: str
    source_type: str
    extension: str | None = None
    current_tier: str
    added_by: str | None = None
    added_on: str | None = None
    abstract: str | None = None
    metadata: DocumentMetadata

    @computed_field  # type: ignore[prop-decorator]
    @property
    def status(self) -> str:
        return {"gold": "PUBLISHED", "silver": "EMBEDDING"}.get(self.current_tier, "RAW")

    @model_validator(mode="before")
    @classmethod
    def _from_row(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        row = dict(value)
        meta = coerce_jsonb_dict(row.get("metadata"))
        st = row.get("source_type", "doc")
        ext = (row.get("extension") or "").upper()
        if not meta.get("doc_type"):
            meta["doc_type"] = (
                f"Doc/{ext}" if st == "doc" and ext else
                "Web" if st == "web" else
                f"Image/{ext}" if st == "image" and ext else
                "Image" if st == "image" else
                f"Video/{ext}" if st == "video" and ext else
                "Video" if st == "video" else
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
