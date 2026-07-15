"""/api/knowledge/policies — FilterPolicy and ExtractionPolicy CRUD."""
from fastapi import APIRouter, Depends, Query, Request

from basemodel.services_databaseconnector.shared_model import ResponseModel
from services.kb_backend.schemas import (
    CreateFilterPolicyRequest, UpdateFilterPolicyRequest, UpdateExtractionPolicyRequest,
)
from services.kb_backend.dependencies.auth import authenticate
from services.kb_backend.dependencies.services import svc
from services.kb_backend.dependencies.auth import require_perm
from services.kb_backend.operations.policy_ops import (
    list_filter_policies, create_filter_policy, update_filter_policy,
    delete_filter_policy, get_extraction_policy, update_extraction_policy,
)

router = APIRouter(prefix="/api/knowledge/policies", tags=["Knowledge-Policies"])

from services.kb_backend.config import DEFAULT_TENANT_ID as _DEFAULT_TENANT


@router.get("/filtering", response_model=ResponseModel)
async def list_filter_policies_route(
    request: Request, ctx: dict = Depends(authenticate),
    page: int = Query(1, ge=1),
):
    result = await list_filter_policies(
        postgres=svc(request, "postgres"),
        tenant_id=ctx["tenant_id"],
        user_id=ctx["user_id"],
        page=page,
    )
    return ResponseModel(code=200, data=result)


@router.post("/filtering", response_model=ResponseModel)
async def create_filter_policy_route(
    body: CreateFilterPolicyRequest, request: Request, ctx: dict = Depends(require_perm("add_filtering_policy")),
):
    result = await create_filter_policy(
        postgres=svc(request, "postgres"),
        name=body.name,
        ptype=body.type,
        content=body.content,
        tenant_id=ctx["tenant_id"] or _DEFAULT_TENANT,
        created_by=ctx["user_name"],
    )
    return ResponseModel(code=200, data=result)


@router.put("/filtering/{policy_id}", response_model=ResponseModel)
async def update_filter_policy_route(
    policy_id: str, body: UpdateFilterPolicyRequest,
    request: Request, ctx: dict = Depends(require_perm("edit_filtering_policy")),
):
    result = await update_filter_policy(
        postgres=svc(request, "postgres"),
        policy_id=policy_id,
        body=body.model_dump(exclude_none=True),
    )
    return ResponseModel(code=200, data=result)


@router.delete("/filtering/{policy_id}", response_model=ResponseModel)
async def delete_filter_policy_route(
    policy_id: str, request: Request, ctx: dict = Depends(require_perm("delete_filtering_policy")),
):
    await delete_filter_policy(
        postgres=svc(request, "postgres"),
        policy_id=policy_id,
        tenant_id=ctx["tenant_id"] or _DEFAULT_TENANT,
    )
    return ResponseModel(code=200, data=None)


@router.get("/extraction", response_model=ResponseModel)
async def get_extraction_policy_route(request: Request, ctx: dict = Depends(authenticate)):
    result = await get_extraction_policy(
        postgres=svc(request, "postgres"),
        tenant_id=ctx["tenant_id"],
    )
    return ResponseModel(code=200, data=result)


@router.put("/extraction/custom", response_model=ResponseModel)
async def update_extraction_policy_route(
    body: UpdateExtractionPolicyRequest, request: Request, ctx: dict = Depends(require_perm("edit_extraction_policy")),
):
    result = await update_extraction_policy(
        postgres=svc(request, "postgres"),
        custom=body.custom,
        tenant_id=ctx["tenant_id"] or _DEFAULT_TENANT,
        created_by=ctx["user_name"],
    )
    return ResponseModel(code=200, data=result)
