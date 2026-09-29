"""Knowledge: claims and events with the evidence behind them (00 section 6, `knowledge.*`).

Claims and events carry the same time axes as observations (03 section 3.1),
plus a validity window.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import AwareDatetime, Field

from services.indicator_api.domains.contract import ContractModel, TimeAxes
from services.indicator_api.domains.observation.models import SHA256_HEX, SourceRef


class Evidence(ContractModel):
    evidence_id: UUID
    release_id: UUID
    artifact_sha256: str = Field(pattern=SHA256_HEX)
    source_ref: SourceRef
    excerpt: str | None = None


class Claim(TimeAxes):
    claim_id: UUID
    subject_entity_id: str
    text: str
    relates_to_event_id: UUID | None = None
    evidence_ids: list[UUID] = Field(
        min_length=1, description="A claim with no evidence is not knowledge"
    )
    valid_from: AwareDatetime | None = None
    valid_to: AwareDatetime | None = None


class Event(TimeAxes):
    event_id: UUID
    title: str
    affects_entity_ids: list[str] = Field(min_length=1)
    evidence_ids: list[UUID] = Field(min_length=1)
    valid_from: AwareDatetime | None = None
    valid_to: AwareDatetime | None = None
