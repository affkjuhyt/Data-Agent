"""/api/knowledge/policies — FilterPolicy and ExtractionPolicy CRUD."""
from typing import Any
from fastapi import APIRouter, Depends, Query, Request, Header, Response

from libs.basemodel.services_databaseconnector.shared_model import ResponseModel
from services.kb_backend.schemas import (
    CreateFilterPolicyRequest, UpdateFilterPolicyRequest, UpdateExtractionPolicyRequest,
)
from libs.shared.auth import require_tenant
from services.kb_backend.dependencies.services import svc
from services.kb_backend.dependencies.auth import require_perm
from services.kb_backend.operations.locking import require_if_match, etag
from services.kb_backend.operations.policy_ops import (
    list_filter_policies, create_filter_policy, update_filter_policy,
    delete_filter_policy, get_extraction_policy, update_extraction_policy,
)

router = APIRouter(prefix="/api/knowledge/policies", tags=["Knowledge-Policies"])



@router.get("/filtering", response_model=ResponseModel)
async def list_filter_policies_route(
        request: Request,
        ctx: dict[str, Any] = Depends(require_perm("viewPolicies")),
        page: int = Query(1, ge=1),
) -> ResponseModel[Any]:
    result = await list_filter_policies(
        postgres=svc(request, "postgres"),
        tenant_id=ctx["tenant_id"],
        user_id=ctx["user_id"],
        page=page,
    )
    return ResponseModel(code=200, data=result)


@router.post("/filtering", response_model=ResponseModel)
async def create_filter_policy_route(
        body: CreateFilterPolicyRequest,
        request: Request, ctx: dict[str, Any] = Depends(require_perm("addFilteringPolicy")),
) -> ResponseModel[Any]:
    result = await create_filter_policy(
        postgres=svc(request, "postgres"),
        name=body.name,
        ptype=body.type,
        content=body.content,
        tenant_id=require_tenant(ctx),
        created_by=ctx["user_name"],
    )
    return ResponseModel(code=200, data=result)


@router.put("/filtering/{policy_id}", response_model=ResponseModel)
async def update_filter_policy_route(
        policy_id: str,
        body: UpdateFilterPolicyRequest,
        request: Request,
        response: Response,
        if_match: str = Header(default="", alias="If-Match"),
        ctx: dict[str, Any] = Depends(require_perm("editFilteringPolicy")),
) -> ResponseModel[Any]:
    result = await update_filter_policy(
        postgres=svc(request, "postgres"),
        policy_id=policy_id,
        tenant_id=ctx["tenant_id"],
        body=body.model_dump(exclude_none=True),
        expected_version=require_if_match(if_match),
    )
    if result.get("row_version"):
        response.headers["ETag"] = etag(result["row_version"])
    return ResponseModel(code=200, data=result)


@router.delete("/filtering/{policy_id}", response_model=ResponseModel)
async def delete_filter_policy_route(
        policy_id: str,
        request: Request,
        ctx: dict[str, Any] = Depends(require_perm("deleteFilteringPolicy")),
) -> ResponseModel[Any]:
    await delete_filter_policy(
        postgres=svc(request, "postgres"),
        policy_id=policy_id,
        tenant_id=require_tenant(ctx),
    )
    return ResponseModel(code=200, data=None)


@router.get("/extraction", response_model=ResponseModel)
async def get_extraction_policy_route(
        request: Request,
        ctx: dict[str, Any] = Depends(require_perm("viewPolicies"))
) -> ResponseModel[Any]:
    result = await get_extraction_policy(
        postgres=svc(request, "postgres"),
        tenant_id=ctx["tenant_id"],
    )
    return ResponseModel(code=200, data=result)


@router.put("/extraction/custom", response_model=ResponseModel)
async def update_extraction_policy_route(
        body: UpdateExtractionPolicyRequest,
        request: Request,
        if_match: str = Header(default="", alias="If-Match"),
        ctx: dict[str, Any] = Depends(require_perm("editExtractionPolicy")),
) -> ResponseModel[Any]:
    result = await update_extraction_policy(
        postgres=svc(request, "postgres"),
        custom=body.custom,
        tenant_id=require_tenant(ctx),
        created_by=ctx["user_name"],
        expected_version=require_if_match(if_match),
    )
    return ResponseModel(code=200, data=result)
