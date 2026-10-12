"""A2 group Truy vấn (schema query) and the metric explorer of Hỏi đáp.

The semantic compiler and the query engine are workers: POST /v1/explore/queries
checks the request against the context layer, records a queued query_run and
the event the compiler consumes, and answers 202 (A1.6). Reading a run, its SQL
and its rows stays here; opening the SQL or the rows is audited (UC-01-02).
"""

from __future__ import annotations

import datetime
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Path, Query, Response
from sqlalchemy import select

from libs.dataagent_db import uuid7
from libs.dataagent_db.models import context
from libs.dataagent_db.models import query as query_models
from services.api.catalogue import endpoint
from services.api.kernel.auth import CurrentDataSpace, CurrentPrincipal, Principal
from services.api.kernel.db import transaction
from services.api.kernel.pagination import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    decode_page_token,
    encode_page_token,
    invalid_page_token,
)
from services.api.kernel.problems import FieldError, Problem
from services.api.modules.qa import scope
from services.api.modules.qa.schemas import (
    ExploreQueryCreate,
    QueryRowsPage,
    QueryRunView,
    QuerySqlView,
)

router = APIRouter()
ROLE = "svc_query"
IdPath = Annotated[uuid.UUID, Path(alias="id")]

# BR-03: a run returns at most 10,000 rows.
MAX_RUN_ROWS = 10_000


@router.post(
    "/v1/explore/queries",
    response_model=QueryRunView,
    status_code=202,
    **endpoint("POST", "/v1/explore/queries", operation_id="exploreQuery"),
)
async def explore_query(
    body: ExploreQueryCreate,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    response: Response,
) -> QueryRunView:
    async with transaction(ROLE, principal) as session:
        metrics = {
            m.id: m
            for m in (
                await session.scalars(
                    select(context.Metric).where(
                        context.Metric.id.in_(body.metric_ids),
                        context.Metric.org_id == principal.org_id,
                        context.Metric.data_space_id == data_space,
                    )
                )
            ).all()
        }
        missing = [str(m) for m in body.metric_ids if m not in metrics]
        if missing:
            raise Problem(
                422,
                "validation_failed",
                "Unknown metric in this Data Space.",
                errors=[
                    FieldError(field="metric_ids", code="not_found", message=", ".join(missing))
                ],
            )
        unusable = [
            m.code for m in metrics.values() if m.status == "retired" or m.current_version is None
        ]
        if unusable:
            raise Problem(
                422,
                "business_rule_violated",
                f"Metric has no usable version: {', '.join(unusable)}.",
            )
        versions = (
            await session.scalars(
                select(context.MetricVersion).where(
                    context.MetricVersion.metric_id.in_(metrics),
                    context.MetricVersion.version.in_(
                        {m.current_version for m in metrics.values()}
                    ),
                )
            )
        ).all()
        current = {
            v.metric_id: v for v in versions if v.version == metrics[v.metric_id].current_version
        }
        # UC-01-04 E1, BR-81: a dimension (to split by or to filter on) must be one every chosen
        # metric allows; the compiler would refuse it later, so refuse it now.
        wanted = set(body.dimensions) | {f.dimension_id for f in body.filters}
        for metric_id, metric in metrics.items():
            version = current.get(metric_id)
            allowed = set(version.allowed_dimensions or []) if version else set()
            bad = sorted(str(d) for d in wanted - allowed)
            if version is None or bad:
                raise Problem(
                    422,
                    "business_rule_violated",
                    f"Metric {metric.code} cannot be split or filtered by dimension {', '.join(bad)}.",
                )
        run = query_models.QueryRun(
            id=uuid7(),
            org_id=principal.org_id,
            data_space_id=data_space,
            # The CHECK has no caller kind for the explorer; it is the user's own ad-hoc question.
            caller_kind="question",
            caller_id=principal.user_id,
            principal_id=principal.user_id,
            route="live",
            # Filled by the semantic compiler (BR-01: only compiled, signed SQL runs).
            sql_generated="",
            sql_executed="",
            metric_version_ids=[v.id for v in current.values()],
            as_of=body.as_of,
            status="queued",
        )
        session.add(run)
        await session.flush()
        await session.refresh(run)
        scope.emit(
            session,
            query_models.Outbox,
            principal,
            aggregate_type="query_run",
            aggregate_id=run.id,
            event_type="query_run.requested",
            payload={
                "query_run_id": run.id,
                "started_at": run.started_at,
                "data_space_id": data_space,
                "principal_id": principal.user_id,
                "metric_ids": body.metric_ids,
                "metric_version_ids": run.metric_version_ids,
                "dimensions": body.dimensions,
                "filters": [f.model_dump() for f in body.filters],
                "as_of": body.as_of,
                "compare": body.compare,
                "status": "queued",
            },
        )
    response.headers["Location"] = f"/v1/query-runs/{run.id}"
    return QueryRunView.model_validate(run)


async def _run(
    session: Any, run_id: uuid.UUID, principal: Principal, space: uuid.UUID
) -> query_models.QueryRun:
    run: query_models.QueryRun | None = await session.scalar(
        select(query_models.QueryRun)
        .where(
            query_models.QueryRun.id == run_id,
            query_models.QueryRun.org_id == principal.org_id,
            query_models.QueryRun.data_space_id == space,
        )
        .order_by(query_models.QueryRun.started_at.desc())
        .limit(1)
    )
    if run is None:
        raise Problem(404, "not_found", "Query run not found.")
    return run


@router.get(
    "/v1/query-runs/{id}",
    response_model=QueryRunView,
    **endpoint("GET", "/v1/query-runs/{id}", operation_id="getQueryRun"),
)
async def get_query_run(
    run_id: IdPath, principal: CurrentPrincipal, data_space: CurrentDataSpace
) -> QueryRunView:
    async with transaction(ROLE, principal) as session:
        run = await _run(session, run_id, principal, data_space)
    return QueryRunView.model_validate(run)


@router.get(
    "/v1/query-runs/{id}/sql",
    response_model=QuerySqlView,
    **endpoint("GET", "/v1/query-runs/{id}/sql", operation_id="getQueryRunSql"),
)
async def get_query_run_sql(
    run_id: IdPath, principal: CurrentPrincipal, data_space: CurrentDataSpace
) -> QuerySqlView:
    # view_detail is checked by A2; sql_generated is therefore always returned here (A3).
    async with transaction(ROLE, principal) as session:
        run = await _run(session, run_id, principal, data_space)
        scope.audit(
            session,
            query_models.Outbox,
            principal,
            action="view_sql",
            object_kind="query_run",
            object_id=run.id,
        )
    return QuerySqlView(
        sql_executed=run.sql_executed,
        policies_applied=list(run.policies_applied or []),
        sql_generated=run.sql_generated,
    )


@router.get(
    "/v1/query-runs/{id}/rows",
    response_model=QueryRowsPage,
    **endpoint("GET", "/v1/query-runs/{id}/rows", operation_id="getQueryRunRows"),
)
async def get_query_run_rows(
    run_id: IdPath,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    cursor: Annotated[str, Query(max_length=2048)] = "",
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
) -> QueryRowsPage:
    position = decode_page_token(cursor)
    try:
        offset = int(position["offset"]) if position else 0
    except (KeyError, TypeError, ValueError) as exc:
        raise invalid_page_token() from exc
    if offset < 0:
        raise invalid_page_token()
    async with transaction(ROLE, principal) as session:
        run = await _run(session, run_id, principal, data_space)
        # The stored rows were masked and row-scoped under the policy of whoever ran the query
        # (principal_id). A3 wants them "masked by the caller's policy": for anyone else that
        # means re-running the masking (a query worker's job), so until then they are refused,
        # as 404 like any row outside the caller's rights (A1.7).
        if run.principal_id != principal.user_id:
            raise Problem(404, "not_found", "Query run not found.")
        result = await session.scalar(
            select(query_models.QueryResult).where(
                query_models.QueryResult.query_run_id == run.id,
                query_models.QueryResult.org_id == principal.org_id,
            )
        )
        if result is None:
            if run.status in ("queued", "running"):
                raise Problem(409, "invalid_state", "The query run has not finished.")
            raise Problem(410, "gone", "The result is no longer kept (30 days).")
        if result.expires_at <= datetime.datetime.now(datetime.UTC):
            raise Problem(410, "gone", "The result is no longer kept (30 days).")
        scope.audit(
            session,
            query_models.Outbox,
            principal,
            action="view_rows",
            object_kind="query_run",
            object_id=run.id,
            after={"offset": offset, "page_size": page_size},
        )
    rows = list(result.rows or [])[:MAX_RUN_ROWS]
    page = rows[offset : offset + page_size]
    end = offset + len(page)
    return QueryRowsPage(
        columns=result.columns,
        items=page,
        next_page_token=encode_page_token({"offset": end} if end < len(rows) else None),
        truncated=result.truncated,
        expires_at=result.expires_at,
    )
