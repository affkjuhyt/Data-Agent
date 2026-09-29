"""Capability: what the planner may call (03 section 10.1, invariant 7)."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from services.indicator_api.domains.contract import ContractModel


class Capability(ContractModel):
    capability_id: str = Field(min_length=1, description="e.g. COMPARE_PERIOD")
    requires: list[str] = Field(default_factory=list)
    returns: list[str] = Field(default_factory=list)
    composition: list[str] = Field(default_factory=list)
    implementation: dict[str, Any]
    allowed_roles: list[str] = Field(default_factory=list)
    slo_ms: int | None = Field(default=None, ge=1)
