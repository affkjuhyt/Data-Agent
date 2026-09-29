"""Review cases and the human decisions on them (03 section 8.3, `governance.*`).

Corrections, overrides and approvals are append-only: a human never edits the
raw artifact or the machine result, only adds a decision (invariant 2).
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from services.indicator_api.domains.contract import ContractModel


class ReviewCaseKind(StrEnum):
    SCHEMA_DRIFT = "SCHEMA_DRIFT"
    DQ_BLOCK = "DQ_BLOCK"
    CONFLICT = "CONFLICT"
    EXTRACTION_LOW_CONF = "EXTRACTION_LOW_CONF"


class ReviewCase(ContractModel):
    case_id: UUID
    kind: ReviewCaseKind
    subject_ref: str
    status: str
    authority_role: str
    assignee: str | None = None
    created_at: AwareDatetime
    version: int = Field(ge=1)


class Correction(ContractModel):
    correction_id: UUID
    case_id: UUID
    target_ref: str
    new_value: Decimal | None = None
    new_mapping_version: int | None = None
    reason_code: str
    note: str | None = None
    author: str
    created_at: AwareDatetime

    @model_validator(mode="after")
    def _corrects_exactly_one_thing(self) -> Self:
        # 03 section 8.3: a correction carries a new value OR a new mapping
        # version. Both at once would make "what did this change" ambiguous.
        if (self.new_value is None) == (self.new_mapping_version is None):
            raise ValueError("set exactly one of new_value, new_mapping_version")
        return self


class Override(ContractModel):
    override_id: UUID
    case_id: UUID
    target_ref: str
    rule: str
    reason_code: str
    author: str
    created_at: AwareDatetime


class Approval(ContractModel):
    approval_id: UUID
    case_id: UUID
    checker: str
    decision: str
    expected_version: int = Field(ge=1)
    created_at: AwareDatetime


class ColumnMapping(ContractModel):
    source_column: str
    series_key: str
    unit: str


class MappingProposal(ContractModel):
    """A proposed mapping from a source layout to series (03 section 5).

    Proposed by the LLM, checked deterministically, decided by a human
    (invariant 5); edits carry `expected_version` like every governed write.
    """

    proposal_id: UUID
    source_id: str
    fingerprint: str
    mappings: list[ColumnMapping] = Field(min_length=1)
    status: str
    version: int = Field(ge=1)
