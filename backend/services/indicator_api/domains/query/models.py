"""QuerySpec: the deterministic analysis request (03 section 9)."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import AwareDatetime, Field

from services.indicator_api.domains.contract import ContractModel


class AsOfMode(StrEnum):
    """`world` filters on available_at, `system` on system_time (UC13)."""

    WORLD = "world"
    SYSTEM = "system"


class QuerySpec(ContractModel):
    operation: str
    series: list[str] = Field(min_length=1)
    aggregation: str | None = None
    period: str | None = None
    compare_to: str | None = None
    as_of: AwareDatetime | None = None
    as_of_mode: AsOfMode = AsOfMode.WORLD
    filters: dict[str, Any] = Field(default_factory=dict)
