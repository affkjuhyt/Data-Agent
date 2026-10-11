"""Response shapes for governance administration endpoints."""

from __future__ import annotations

import datetime
import decimal
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict

from services.api.kernel.pagination import Page


class ModelProviderView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    display_name: str
    kind: str
    api_style: str
    endpoint: str
    data_region: str | None
    zero_retention: bool
    contract_ref: str | None
    max_classification: str
    enabled: bool
    approved_by: uuid.UUID | None
    approved_at: datetime.datetime | None
    price: Any
    monthly_budget_vnd: decimal.Decimal | None
    monthly_cost_vnd: decimal.Decimal = decimal.Decimal("0")
    status: str
    created_at: datetime.datetime
    updated_at: datetime.datetime


ModelProviderPage = Page[ModelProviderView]
