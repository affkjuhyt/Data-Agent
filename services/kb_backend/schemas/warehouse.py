"""Warehouse connection + config schemas — domain, request, response."""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


# ── Domain ────────────────────────────────────────────────────────────────────

class KnowledgeConfigTable(BaseModel):
    id: str
    schema: str
    table_name: str
    row_count: int
    columns: int


# ── Request ───────────────────────────────────────────────────────────────────

class CreateWarehouseConfigRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    name: str
    version: str
    status: Optional[str] = "Draft"
    tables: list
    sync_schedule: Optional[str] = Field(default="Manual", alias="syncSchedule")
    connection: Optional[dict] = None


class ConnectSnowflakeRequest(BaseModel):
    account_identifier: str
    user: str
    password: str
    warehouse: str
    database: str
    schema: str
    role: str | None = None


class SelectTablesRequest(BaseModel):
    connection_name: str
    warehouse_type: str  # "Snowflake" | "Databricks"
    account_identifier: str
    user: str
    password: str
    warehouse: str
    database: str
    schema: str
    selected_table_ids: list[str]
    role: str | None = None
    # secrets_vault id (key_type=WAREHOUSE_PASSWORD) that schema-crawler-worker
    # resolves the real password from at crawl time via OpenBao — `password`
    # above is only ever used for the one-off live connection test in this
    # request, never persisted (see warehouse_connect.py). Optional: a
    # warehouse without a secret_id simply isn't auto-crawled (see
    # docs/Schema_Crawler_Risk_Remediation_Plan.md #1).
    secret_id: str | None = None


# ── Response ──────────────────────────────────────────────────────────────────

class WarehouseConfigResponse(BaseModel):
    id: str
    version: object | None
    active: bool
    host: str | None
    database: str | None
    selected_tables: list[str]
    sync_schedule: str | None
    schema_filter: list[str]
    created_at: str
