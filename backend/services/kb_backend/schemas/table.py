"""Table schemas — domain, request, response."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class UpdateTableRowRequest(BaseModel):
    column: str
    value: Any = None
