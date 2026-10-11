"""Request and response shapes of the conflict module (tab 4 A3, group "Xung đột").

Responses are the table's columns minus org_id, created_by, updated_by (A3 "Trả về").
"""

from __future__ import annotations

import datetime
import decimal
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# What a person decides on "So sánh hai nguồn" (UC-07-04 step 6, 6a). insufficient_basis is not
# an outcome column value: it sets status insufficient_basis and leaves outcome empty.
ResolveOutcome = Literal["resolved_by_human", "both_presented", "noted", "insufficient_basis"]


class ConflictView(BaseModel):
    id: uuid.UUID
    data_space_id: uuid.UUID
    kind: str
    detected_at_stage: str
    subject_key: str
    attribute: str
    delta: decimal.Decimal | None
    delta_pct: decimal.Decimal | None
    material: bool
    chosen_claim_id: uuid.UUID | None
    rule_id: uuid.UUID | None
    outcome: str | None
    hitl_request_id: uuid.UUID | None
    decision_note: str | None
    affected_answer_ids: list[uuid.UUID]
    status: str
    created_at: datetime.datetime
    updated_at: datetime.datetime


class SourceBrief(BaseModel):
    id: uuid.UUID
    name: str
    kind: str
    authority_level: str


class ClaimView(BaseModel):
    id: uuid.UUID
    role: str
    subject_key: str
    attribute: str
    value_text: str | None
    value_num: decimal.Decimal | None
    unit: str | None
    valid_from: datetime.date | None
    valid_to: datetime.date | None
    source_kind: str
    source_ref: uuid.UUID
    source_id: uuid.UUID | None
    extracted_at: datetime.datetime
    source: SourceBrief | None = None
    # Position of the claim's source in the data kind's ranking, 1 = highest; None if unranked.
    rank: int | None = None


class SourceAuthorityView(BaseModel):
    id: uuid.UUID
    data_space_id: uuid.UUID
    data_kind: str
    ranking: list[uuid.UUID]
    materiality: Any
    backtest: Any
    approved_by: uuid.UUID | None
    approved_at: datetime.datetime | None
    status: str
    created_at: datetime.datetime
    updated_at: datetime.datetime


class SuggestionView(BaseModel):
    """What BR-30 would choose: an approved rule, then the source authority; claim_id null
    means the data gives no basis and a person decides."""

    claim_id: uuid.UUID | None
    outcome: str
    basis: Literal["resolution_rule", "source_authority", "no_basis"]


class ConflictDetail(ConflictView):
    claims: list[ClaimView] = Field(default_factory=list)
    authority: SourceAuthorityView | None = None
    suggestion: SuggestionView


class ConflictResolve(BaseModel):
    chosen_claim_id: uuid.UUID | None = None
    outcome: ResolveOutcome
    note: str | None = Field(default=None, max_length=4_000)
    remember_as_rule: bool = False

    @model_validator(mode="after")
    def _choice_fits_outcome(self) -> ConflictResolve:
        if self.outcome == "resolved_by_human" and self.chosen_claim_id is None:
            raise ValueError("outcome resolved_by_human needs chosen_claim_id")
        if self.outcome in ("both_presented", "insufficient_basis") and self.chosen_claim_id:
            raise ValueError(f"outcome {self.outcome} chooses no claim")
        if self.remember_as_rule and self.outcome != "resolved_by_human":
            # A rule picks a source; only a choice between claims can become one.
            raise ValueError("remember_as_rule needs outcome resolved_by_human")
        return self


class Materiality(BaseModel):
    """BR-120 thresholds: relative for amounts, absolute for counts."""

    model_config = ConfigDict(extra="forbid")

    amount_pct: decimal.Decimal = Field(default=decimal.Decimal("0.0005"), ge=0, le=1)
    count: int = Field(default=0, ge=0)


class SourceAuthorityPut(BaseModel):
    data_kind: str = Field(min_length=1, max_length=200)
    ranking: list[uuid.UUID] = Field(min_length=1, max_length=100)
    materiality: Materiality | None = None


class ResolutionRuleCreate(BaseModel):
    data_kind: str = Field(min_length=1, max_length=200)
    # Equality tests on the conflict (kind, subject_key, attribute, detected_at_stage, material).
    condition: dict[str, Any]
    # {"source_id": …} picks that source's claim.
    action: dict[str, Any]


class ResolutionRuleView(BaseModel):
    id: uuid.UUID
    data_space_id: uuid.UUID
    data_kind: str
    condition: Any
    action: Any
    origin: str
    behavior_policy_id: uuid.UUID | None
    status: str
    created_at: datetime.datetime
    updated_at: datetime.datetime
