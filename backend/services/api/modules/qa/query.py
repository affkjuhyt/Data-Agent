"""Read-only query-run endpoints for API-02."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Path
from sqlalchemy import select

from libs.dataagent_db.models import query as query_models
from services.api.catalogue import endpoint
from services.api.kernel.auth import CurrentDataSpace, CurrentPrincipal
from services.api.kernel.db import transaction
from services.api.kernel.problems import Problem
from services.api.modules.qa.schemas import ExploreQueryCreate, QueryRows, QueryRunView

router = APIRouter()
ROLE = "svc_query"


@router.post("/v1/explore/queries", response_model=QueryRunView, status_code=202, **endpoint("POST", "/v1/explore/queries", operation_id="exploreQuery"))
async def explore_query(body: ExploreQueryCreate, principal: CurrentPrincipal, data_space: CurrentDataSpace) -> QueryRunView:
    async with transaction(ROLE, principal) as session:
        run = query_models.QueryRun(
            org_id=principal.org_id,
            data_space_id=data_space,
            caller_kind="question",
            caller_id=principal.user_id,
            principal_id=principal.user_id,
            route="live",
            sql_generated=body.text,
            sql_executed=body.text,
        )
        session.add(run)
        await session.flush()
    return QueryRunView.model_validate(run, from_attributes=True)


async def _run(run_id: uuid.UUID, principal: CurrentPrincipal) -> query_models.QueryRun:
    async with transaction(ROLE, principal) as session:
        run = await session.scalar(
            select(query_models.QueryRun)
            .where(query_models.QueryRun.id == run_id, query_models.QueryRun.org_id == principal.org_id)
            .order_by(query_models.QueryRun.started_at.desc())
            .limit(1)
        )
    if run is None:
        raise Problem(404, "not_found", "Query run not found.")
    return run


@router.get("/v1/query-runs/{id}", response_model=QueryRunView, **endpoint("GET", "/v1/query-runs/{id}", operation_id="getQueryRun"))
async def get_query_run(run_id: Annotated[uuid.UUID, Path(alias="id")], principal: CurrentPrincipal) -> QueryRunView:
    return QueryRunView.model_validate(await _run(run_id, principal), from_attributes=True)


@router.get("/v1/query-runs/{id}/sql", response_model=QueryRunView, **endpoint("GET", "/v1/query-runs/{id}/sql", operation_id="getQueryRunSql"))
async def get_query_run_sql(run_id: Annotated[uuid.UUID, Path(alias="id")], principal: CurrentPrincipal) -> QueryRunView:
    return QueryRunView.model_validate(await _run(run_id, principal), from_attributes=True)


@router.get("/v1/query-runs/{id}/rows", response_model=QueryRows, **endpoint("GET", "/v1/query-runs/{id}/rows", operation_id="getQueryRunRows"))
async def get_query_run_rows(run_id: Annotated[uuid.UUID, Path(alias="id")], principal: CurrentPrincipal) -> QueryRows:
    async with transaction(ROLE, principal) as session:
        run = await session.scalar(
            select(query_models.QueryRun).where(query_models.QueryRun.id == run_id, query_models.QueryRun.org_id == principal.org_id)
        )
        result = await session.scalar(select(query_models.QueryResult).where(query_models.QueryResult.query_run_id == run_id).order_by(query_models.QueryResult.created_at.desc()).limit(1))
    if run is None or result is None:
        raise Problem(404, "not_found", "Query result not found.")
    return QueryRows.model_validate(result, from_attributes=True)
