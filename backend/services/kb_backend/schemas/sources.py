"""Scheduled-source registry schemas — request/response for
`/api/knowledge/sources` (V1 connectors: http_file / web_crawl /
web_crawl_headless)."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel


class CreateSourceRequest(BaseModel):
    title: str
    publisher: Optional[str] = None
    connector_type: str  # SourceConnectorType value
    url: str
    format: Optional[str] = None
    cron: str
    render: bool = False
    variables: Optional[list[str]] = None
    notes: Optional[str] = None


class UpdateSourceConfigRequest(BaseModel):
    url: Optional[str] = None
    format: Optional[str] = None
    cron: Optional[str] = None
    render: Optional[bool] = None
    variables: Optional[list[str]] = None
    notes: Optional[str] = None


class SourceResponse(BaseModel):
    id: str
    title: str
    publisher: Optional[str]
    connector_type: str
    url: Optional[str]
    cron: Optional[str]
    render: bool
    created_by: Optional[str]
    created_at: str
    last_run_status: Optional[str] = None
    last_run_at: Optional[str] = None


class SourceRunResponse(BaseModel):
    run_id: str
    status: str
    trigger: str
    started_at: str
    finished_at: Optional[str]
    data_ids: list[str]
    error: Optional[str]
    quality_metrics: Optional[dict[str, Any]]


class BulkImportRequest(BaseModel):
    yaml_text: str


class BulkImportSkipped(BaseModel):
    title: str
    reason: str


class BulkImportResponse(BaseModel):
    created: list[SourceResponse]
    skipped: list[BulkImportSkipped]
