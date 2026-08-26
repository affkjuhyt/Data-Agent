"""Table schemas — domain, request, response."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class TableColumn(BaseModel):
    name: str
    type: str
    nullable: bool


class UpdateTableRowRequest(BaseModel):
    column: str
    value: Any = None


class TableResponse(BaseModel):
    id: str
    name: str
    description: str
    columns: list[TableColumn]
    rows: list[dict[str, Any]]
