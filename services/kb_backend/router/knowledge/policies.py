"""/api/knowledge/policies — FilterPolicy and ExtractionPolicy CRUD."""
from fastapi import APIRouter, Depends, Query, Request

from basemodel.services_databaseconnector.shared_model import ResponseModel
from services.kb_backend.UI_model.policy import (
    RequestCreateFilterPolicy, RequestUpdateFilterPolicy, RequestExtractionCustom,
)
from services.kb_backend.dependencies.ui_context import get_ui_context, svc
from services.kb_backend.pipeline.kb_ui_operation.policy_ops import (
    list_filter_policies, create_filter_policy, update_filter_policy,
    delete_filter_policy, get_extraction_policy, update_extraction_policy,
)

router = APIRouter(prefix="/api/knowledge/policies", tags=["Knowledge-Policies"])

_DEFAULT_TENANT = "00000000-0000-0000-0000-000000000001"


@router.get("/filtering", response_model=ResponseModel)
async def list_filter_policies_route(
    request: Request, ctx: dict = Depends(get_ui_context),
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
    body: RequestCreateFilterPolicy, request: Request, ctx: dict = Depends(get_ui_context),
):
    result = await create_filter_policy(
        postgres=svc(request, "postgres"),
        name=body.name,
        ptype=body.type,
        content=body.content,
        tenant_id=ctx["tenant_id"] or _DEFAULT_TENANT,
        user_id=ctx["user_id"],
    )
    return ResponseModel(code=200, data=result)


@router.put("/filtering/{policy_id}", response_model=ResponseModel)
async def update_filter_policy_route(
    policy_id: str, body: RequestUpdateFilterPolicy,
    request: Request, ctx: dict = Depends(get_ui_context),
):
    result = await update_filter_policy(
        postgres=svc(request, "postgres"),
        policy_id=policy_id,
        body=body.model_dump(exclude_none=True),
    )
    return ResponseModel(code=200, data=result)


@router.delete("/filtering/{policy_id}", response_model=ResponseModel)
async def delete_filter_policy_route(
    policy_id: str, request: Request, ctx: dict = Depends(get_ui_context),
):
    await delete_filter_policy(
        postgres=svc(request, "postgres"),
        policy_id=policy_id,
        tenant_id=ctx["tenant_id"] or _DEFAULT_TENANT,
    )
    return ResponseModel(code=200, data=None)


@router.get("/extraction", response_model=ResponseModel)
async def get_extraction_policy_route(request: Request, ctx: dict = Depends(get_ui_context)):
    result = await get_extraction_policy(
        postgres=svc(request, "postgres"),
        tenant_id=ctx["tenant_id"],
    )
    return ResponseModel(code=200, data=result)


@router.put("/extraction/custom", response_model=ResponseModel)
async def update_extraction_policy_route(
    body: RequestExtractionCustom, request: Request, ctx: dict = Depends(get_ui_context),
):
    result = await update_extraction_policy(
        postgres=svc(request, "postgres"),
        custom=body.custom,
        tenant_id=ctx["tenant_id"] or _DEFAULT_TENANT,
        user_id=ctx["user_id"],
    )
    return ResponseModel(code=200, data=result)
