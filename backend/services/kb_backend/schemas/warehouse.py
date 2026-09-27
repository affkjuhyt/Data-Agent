"""Warehouse connection + config schemas — domain, request, response."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class CreateWarehouseConfigRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    name: str
    version: str
    status: Optional[str] = "Draft"
    tables: list[Any]
    sync_schedule: Optional[str] = Field(default="Manual", alias="syncSchedule")
    connection: Optional[dict[str, Any]] = None


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
