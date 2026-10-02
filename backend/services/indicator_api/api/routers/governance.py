"""Review cases and the decisions on them (the paths the NOP-151 mock published)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends

from libs.contracts.v1 import ReviewCaseRef, VersionedWrite
from services.indicator_api.api import NOT_IMPLEMENTED_RESPONSE, not_implemented
from services.indicator_api.auth import current_tenant

router = APIRouter(
    prefix="/review/cases",
    tags=["review"],
    dependencies=[Depends(current_tenant)],
    responses=NOT_IMPLEMENTED_RESPONSE,
)


@router.get("", response_model=list[ReviewCaseRef])
def list_review_cases() -> list[ReviewCaseRef]:
    not_implemented()


@router.get("/{case_id}", response_model=ReviewCaseRef)
def get_review_case(case_id: UUID) -> ReviewCaseRef:
    not_implemented()


@router.post("/{case_id}/approve", response_model=ReviewCaseRef)
def approve_review_case(case_id: UUID, body: VersionedWrite) -> ReviewCaseRef:
    not_implemented()


@router.post("/{case_id}/reject", response_model=ReviewCaseRef)
def reject_review_case(case_id: UUID, body: VersionedWrite) -> ReviewCaseRef:
    not_implemented()
