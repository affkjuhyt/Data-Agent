"""Response shapes for governance administration endpoints."""

from __future__ import annotations

import datetime
import decimal
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

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


class ModelProviderCreate(BaseModel):
    code: str = Field(min_length=1, max_length=100)
    display_name: str = Field(min_length=1, max_length=500)
    kind: Literal["on_prem", "frontier"]
    api_style: Literal["openai_compatible", "anthropic_messages", "google_genai", "other"] = (
        "openai_compatible"
    )
    endpoint: str = Field(min_length=1, max_length=2_000)
    secret_ref: str | None = Field(default=None, max_length=500)
    data_region: str | None = Field(default=None, max_length=200)
    zero_retention: bool = False
    contract_ref: str | None = Field(default=None, max_length=500)
    price: dict[str, Any] = Field(default_factory=dict)
    monthly_budget_vnd: decimal.Decimal | None = Field(default=None, ge=0, max_digits=14)
