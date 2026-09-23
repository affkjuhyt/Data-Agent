"""Scheduled-source registry schemas — request/response for
`/api/knowledge/sources` (http_file / web_crawl / web_crawl_headless / rss /
rest_api / sdmx connectors)."""
from __future__ import annotations

from typing import Any, Literal, Optional

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
    openbao_path: Optional[str] = None
    query_params: Optional[dict[str, str]] = None
    auth_style: Optional[str] = None
    auth_param_name: Optional[str] = None
    recursive: bool = False
    max_depth: int = 1
    same_domain_only: bool = True
    # None (the default) lets create_source pick "auto" for a scheduled source
    # and "manual" for a manual-only one; set explicitly to override that.
    promotion_mode: Optional[Literal["manual", "auto"]] = None
    # Stamped onto every KBData row this source's runs produce. None (the
    # default) lets create_source fall back to "production".
    purpose: Optional[Literal["training", "production", "evaluation"]] = None
    notes: Optional[str] = None


class UpdateSourceConfigRequest(BaseModel):
    url: Optional[str] = None
    format: Optional[str] = None
    cron: Optional[str] = None
    render: Optional[bool] = None
    variables: Optional[list[str]] = None
    openbao_path: Optional[str] = None
    query_params: Optional[dict[str, str]] = None
    auth_style: Optional[str] = None
    auth_param_name: Optional[str] = None
    recursive: Optional[bool] = None
    max_depth: Optional[int] = None
    same_domain_only: Optional[bool] = None
    promotion_mode: Optional[Literal["manual", "auto"]] = None
    purpose: Optional[Literal["training", "production", "evaluation"]] = None
    notes: Optional[str] = None


class SourceResponse(BaseModel):
    id: str
    title: str
    publisher: Optional[str]
    connector_type: str
    url: Optional[str]
    cron: Optional[str]
    render: bool
    openbao_path: Optional[str] = None
    query_params: Optional[dict[str, str]] = None
    auth_style: Optional[str] = None
    auth_param_name: Optional[str] = None
    recursive: bool = False
    max_depth: int = 1
    same_domain_only: bool = True
    promotion_mode: Literal["manual", "auto"] = "manual"
    purpose: Literal["training", "production", "evaluation"] = "production"
    created_by: Optional[str]
    created_at: str
    last_run_status: Optional[str] = None
    last_run_at: Optional[str] = None
    next_run_at: Optional[str] = None


class SourceRunResponse(BaseModel):
    run_id: str
    status: str
    trigger: str
    retry_count: int = 0
    started_at: str
    finished_at: Optional[str]
    data_ids: list[str]
    error: Optional[str]
    quality_metrics: Optional[dict[str, Any]]


class BulkImportRequest(BaseModel):
    # Required for the yaml_text path; ignored (may be omitted) when `rows`
    # carries the already-parsed, steward-edited rows instead.
    yaml_text: str = ""
    # True: parse (or use `rows` as-is) and return a preview — nothing is
    # created. Lets the frontend show an editable table before committing.
    dry_run: bool = False
    # When set, these exact rows are created (or previewed) instead of
    # parsing `yaml_text` — the wizard's Bulk Import review step sends back
    # whatever the steward edited from an earlier dry_run's preview.
    rows: Optional[list[CreateSourceRequest]] = None


class BulkImportSkipped(BaseModel):
    title: str
    reason: str


class BulkImportResponse(BaseModel):
    created: list[SourceResponse]
    skipped: list[BulkImportSkipped]


class BulkImportPreviewResponse(BaseModel):
    preview: list[CreateSourceRequest]
    skipped: list[BulkImportSkipped]
