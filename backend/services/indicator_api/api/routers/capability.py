"""Capabilities (the path the NOP-151 mock published)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from libs.contracts.v1 import CapabilityRef
from services.indicator_api.api import NOT_IMPLEMENTED_RESPONSE, not_implemented
from services.indicator_api.auth import current_tenant

router = APIRouter(dependencies=[Depends(current_tenant)], responses=NOT_IMPLEMENTED_RESPONSE)


@router.get("/capabilities", response_model=list[CapabilityRef], tags=["capabilities"])
def list_capabilities() -> list[CapabilityRef]:
    not_implemented()
