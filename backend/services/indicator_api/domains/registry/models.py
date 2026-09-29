"""Entities, their relations, and series (03 section 3.2, `registry.*`)."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import AwareDatetime, Field

from services.indicator_api.domains.contract import ContractModel


class RelationType(StrEnum):
    SAME_UNDERLYING = "SAME_UNDERLYING"
    SUCCEEDS = "SUCCEEDS"
    NOT_EQUIVALENT = "NOT_EQUIVALENT"
    COMPONENT_OF = "COMPONENT_OF"


class Entity(ContractModel):
    entity_id: str = Field(min_length=1, description="Stable id, e.g. BRENT_SPOT, GOLD_SJC_BAR")
    kind: str
    name_vi: str
    name_en: str
    aliases: list[str] = Field(default_factory=list)
    attributes: dict[str, Any] = Field(default_factory=dict)


class EntityRelation(ContractModel):
    from_id: str
    predicate: RelationType
    to_id: str
    valid_from: AwareDatetime | None = None
    valid_to: AwareDatetime | None = None


class Series(ContractModel):
    series_key: str = Field(min_length=1, description="e.g. EIA:RBRTE:D")
    entity_id: str
    measure: str
    unit: str
    unit_multiplier: int = 0
    currency: str | None = None
    grain: str
    # A plain string until P0-04 adds D/W/I/P to libs.indicator.period.Frequency;
    # P0-04 then switches this field to that enum and bumps SCHEMA_VERSION.
    frequency: str
    price_side: str | None = None
    decimal_precision: int = Field(ge=0)
    locale: str
    source_id: str
    status: str = Field(description="Approval state; only an approved series is answerable")
    version: int = Field(
        ge=1, description="Current version; writes send it back as expected_version"
    )
