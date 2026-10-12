"""A2 group "Biến đổi dữ liệu" (P06): pipelines, lineage, data marts, shared data products,
writebacks (UC-06-01 to UC-06-06).

The api never runs a transformation or a write: a preview, a materialize, a schedule, a diff and
an undo are rows plus an outbox event that the pipeline worker (Temporal) acts on, and the run or
request row is where the result appears (A1.6). Every create and status change writes its event
to pipeline.outbox in the same transaction (D1 rule 8). POST /v1/data-products is served by the
context module, which calls public.create_published_product for layer "published".
"""

from __future__ import annotations

import datetime
import uuid
from collections import Counter
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Path, Query, Response
from sqlalchemy import and_, or_, select, tuple_
from sqlalchemy.dialects.postgresql import Range
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db import uuid7
from libs.dataagent_db.models import catalog, org
from libs.dataagent_db.models import pipeline as pl
from services.api.catalogue import endpoint
from services.api.kernel.auth import CurrentDataSpace, CurrentPrincipal, Principal
from services.api.kernel.db import transaction
from services.api.kernel.pagination import (
    Order,
    Page,
    PageRequest,
    keyset,
    next_page,
    order_by,
    page_request,
)
from services.api.kernel.problems import Problem
from services.api.modules.pipeline import rules
from services.api.modules.pipeline.schemas import (
    Accepted,
    DataMartView,
    DataProductView,
    LineageGraph,
    LineageGroup,
    LineageLink,
    LineageNode,
    MartCreate,
    MartPatch,
    MaterializeRequest,
    PipelineCreate,
    PipelineDetail,
    PipelinePatch,
    PipelineRunView,
    PipelineView,
    PreviewRequest,
    ScheduleRequest,
    Step,
    UndoRequest,
    WritebackCreate,
    WritebackView,
    WriteWindow,
)
from services.api.modules.pipeline.store import ROLE, emit, invalid, now, require_datasets

router = APIRouter()

PathId = Annotated[uuid.UUID, Path(alias="id")]
POLL_AFTER_MS = 2_000
# UC-06-04 5a: an executed write can be undone from its snapshot for 30 days.
UNDO_WINDOW = datetime.timedelta(days=30)
# BR-60: a breaking contract change is announced 30 days before it is published.
BREAKING_NOTICE = datetime.timedelta(days=30)
LineageKind = Literal[
    "source", "dataset", "column", "metric", "tile", "report", "skill", "agent", "data_product"
]
# UC-06-05: depth 3 by default; above 500 nodes the graph is grouped by kind (E1).
LINEAGE_DEPTH, LINEAGE_MAX_DEPTH, LINEAGE_MAX_NODES = 3, 10, 500
HIDDEN_NAME = "Đối tượng bạn không có quyền xem"


# --- helpers -------------------------------------------------------------------------------------


def _forbid_delegated(principal: Principal) -> None:
    # BR-25: an agent (a client acting for a user) never creates or sends a writeback.
    if principal.client_id is not None:
        raise Problem(403, "forbidden", "Only a user session can request a writeback (BR-25).")


def _check_steps(steps: list[Step]) -> None:
    for i, step in enumerate(steps):
        if step.language != "sql":
            continue
        writes = rules.writes_in_sql(step.code)
        if writes:
            raise Problem(
                422,
                "business_rule_violated",
                f"Step {i + 1} uses {', '.join(writes)}; a step never writes to a source (BR-22).",
            )


def _visible(principal: Principal) -> Any:
    """A personal pipeline is its owner's; a Data Space one is everyone's in the space (BR-111)."""
    return or_(pl.Pipeline.scope == "data_space", pl.Pipeline.owner_id == principal.user_id)


async def _pipeline(
    session: AsyncSession,
    principal: Principal,
    data_space: uuid.UUID,
    pipeline_id: uuid.UUID,
    *,
    lock: bool = False,
) -> pl.Pipeline:
    query = select(pl.Pipeline).where(
        pl.Pipeline.id == pipeline_id,
        pl.Pipeline.org_id == principal.org_id,
        pl.Pipeline.data_space_id == data_space,
        pl.Pipeline.deleted_at.is_(None),
        _visible(principal),
    )
    if lock:
        query = query.with_for_update()
    row = await session.scalar(query)
    if row is None:
        raise Problem(404, "not_found", "Pipeline not found.")
    return row


async def _name_free(
    session: AsyncSession,
    data_space: uuid.UUID,
    name: str,
    pipeline_id: uuid.UUID | None = None,
) -> None:
    """UNIQUE (data_space_id, name) holds deleted pipelines too; a clash is a 409, not a 500."""
    query = select(pl.Pipeline.id).where(
        pl.Pipeline.data_space_id == data_space, pl.Pipeline.name == name
    )
    if pipeline_id is not None:
        query = query.where(pl.Pipeline.id != pipeline_id)
    if await session.scalar(query.limit(1)) is not None:
        raise Problem(409, "conflict", f"A pipeline named {name!r} already exists here.")


async def _check_dependencies(
    session: AsyncSession,
    principal: Principal,
    data_space: uuid.UUID,
    pipeline_id: uuid.UUID,
    depends_on: list[uuid.UUID],
) -> None:
    """BR-112: the upstream pipelines exist in this Data Space and the graph stays acyclic."""
    if not depends_on:
        return
    if pipeline_id in depends_on:
        raise invalid("depends_on_pipeline_ids", "A pipeline cannot depend on itself.")
    rows = (
        await session.execute(
            select(pl.Pipeline.id, pl.Pipeline.depends_on_pipeline_ids).where(
                pl.Pipeline.org_id == principal.org_id,
                pl.Pipeline.data_space_id == data_space,
                pl.Pipeline.deleted_at.is_(None),
            )
        )
    ).all()
    graph = {row[0]: list(row[1]) for row in rows}
    missing = set(depends_on) - set(graph)
    if missing:
        raise invalid(
            "depends_on_pipeline_ids", f"{len(missing)} pipeline(s) are not in this Data Space."
        )
    if rules.creates_cycle(graph, pipeline_id, depends_on):
        raise Problem(
            422, "business_rule_violated", "The pipeline dependencies would form a cycle."
        )


def _etag(row: pl.Pipeline) -> str:
    return f'"{row.updated_at.isoformat()}"'


def _stale(if_match: str | None, row: pl.Pipeline) -> bool:
    """A1: If-Match is optional on PATCH; when sent it is the updated_at the client saw, compared
    as an instant (the JSON body spells UTC "Z", isoformat() "+00:00")."""
    if if_match is None:
        return False
    try:
        seen = datetime.datetime.fromisoformat(if_match.strip().removeprefix("W/").strip('"'))
    except ValueError:
        return True
    return seen.tzinfo is None or seen != row.updated_at


def _accepted(response: Response, location: str, accepted: Accepted) -> Accepted:
    response.headers["Location"] = location
    return accepted


# --- pipelines -----------------------------------------------------------------------------------


@router.get(
    "/v1/pipelines",
    response_model=Page[PipelineView],
    **endpoint("GET", "/v1/pipelines", operation_id="listPipelines"),
)
async def list_pipelines(
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    page: Annotated[PageRequest, Depends(page_request)],
    order: Annotated[
        Order,
        Depends(order_by("created_at", "updated_at", "name", "status", default="created_at desc")),
    ],
    scope: Literal["personal", "data_space"] | None = None,
    status: Literal["draft", "active", "paused"] | None = None,
) -> Page[PipelineView]:
    p = pl.Pipeline
    query = select(p).where(
        p.org_id == principal.org_id,
        p.data_space_id == data_space,
        p.deleted_at.is_(None),
        _visible(principal),
    )
    if scope:
        query = query.where(p.scope == scope)
    if status:
        query = query.where(p.status == status)
    query = keyset(
        query,
        columns={
            "created_at": p.created_at,
            "updated_at": p.updated_at,
            "name": p.name,
            "status": p.status,
        },
        tiebreak=p.id,
        order=order,
        page=page,
    )
    async with transaction(ROLE, principal) as session:
        rows, token = next_page(list((await session.scalars(query)).all()), order=order, page=page)
    return Page(items=[PipelineView.model_validate(r) for r in rows], next_page_token=token)


@router.post(
    "/v1/pipelines",
    status_code=201,
    response_model=PipelineView,
    **endpoint("POST", "/v1/pipelines", operation_id="createPipeline"),
)
async def create_pipeline(
    body: PipelineCreate, principal: CurrentPrincipal, data_space: CurrentDataSpace
) -> PipelineView:
    _check_steps(body.steps)
    async with transaction(ROLE, principal) as session:
        await require_datasets(
            session, principal, data_space, body.input_dataset_ids, "input_dataset_ids"
        )
        await _name_free(session, data_space, body.name)
        stamp = now()
        row = pl.Pipeline(
            id=uuid7(),
            org_id=principal.org_id,
            data_space_id=data_space,
            name=body.name,
            owner_id=principal.user_id,
            input_dataset_ids=list(dict.fromkeys(body.input_dataset_ids)),
            steps=[s.model_dump(mode="json", exclude_none=True) for s in body.steps],
            scope=body.scope,
            status="draft",
            created_by=principal.user_id,
            updated_by=principal.user_id,
            created_at=stamp,
            updated_at=stamp,
        )
        session.add(row)
        emit(
            session,
            principal,
            "pipeline",
            row.id,
            "pipeline.created",
            pipeline_id=row.id,
            data_space_id=data_space,
            status=row.status,
            scope=row.scope,
            input_dataset_ids=row.input_dataset_ids,
        )
        if body.instruction and body.instruction.strip():
            # UC-06-01 step 2: the data-prep agent proposes steps from the user's words; the
            # words are the request itself, so they travel in the event.
            emit(
                session,
                principal,
                "pipeline",
                row.id,
                "pipeline.steps_requested",
                pipeline_id=row.id,
                data_space_id=data_space,
                requested_by=principal.user_id,
                instruction=body.instruction.strip(),
            )
        await session.flush()
        return PipelineView.model_validate(row)


@router.get(
    "/v1/pipelines/{id}",
    response_model=PipelineDetail,
    **endpoint("GET", "/v1/pipelines/{id}", operation_id="getPipeline"),
)
async def get_pipeline(
    pipeline_id: PathId,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    response: Response,
) -> PipelineDetail:
    async with transaction(ROLE, principal) as session:
        row = await _pipeline(session, principal, data_space, pipeline_id)
        last = await session.scalar(
            select(pl.PipelineRun)
            .where(pl.PipelineRun.pipeline_id == row.id)
            .order_by(pl.PipelineRun.started_at.desc(), pl.PipelineRun.id.desc())
            .limit(1)
        )
    # The same tag PATCH answers with, so If-Match from either read passes the kernel's check
    # (which compares against this GET) and the route's own updated_at comparison alike.
    response.headers["ETag"] = _etag(row)
    return PipelineDetail(
        **PipelineView.model_validate(row).model_dump(),
        last_run=PipelineRunView.model_validate(last) if last is not None else None,
    )


@router.patch(
    "/v1/pipelines/{id}",
    response_model=PipelineView,
    **endpoint("PATCH", "/v1/pipelines/{id}", operation_id="updatePipeline"),
)
async def update_pipeline(
    body: PipelinePatch,
    pipeline_id: PathId,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    response: Response,
    if_match: Annotated[str | None, Header()] = None,
) -> PipelineView:
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise invalid("body", "Nothing to change.")
    for field in ("name", "scope", "status", "on_failure", "steps", "depends_on_pipeline_ids"):
        if field in changes and changes[field] is None:
            raise invalid(field, f"{field} cannot be null.")
    if body.steps is not None:
        _check_steps(body.steps)
    async with transaction(ROLE, principal) as session:
        row = await _pipeline(session, principal, data_space, pipeline_id, lock=True)
        if _stale(if_match, row):
            raise Problem(
                412, "precondition_failed", "The pipeline changed since you read it (If-Match)."
            )
        previous = row.status
        if body.status is not None and body.status != row.status:
            if body.status == "draft":
                raise Problem(409, "invalid_state", "A pipeline does not go back to draft.")
            if body.status == "active" and row.target_dataset_id is None:
                # UC-06-02: a pipeline becomes active when its table is materialized.
                raise Problem(
                    409, "invalid_state", "Materialize the pipeline before activating it."
                )
            row.status = body.status
        if body.depends_on_pipeline_ids is not None:
            deps = list(dict.fromkeys(body.depends_on_pipeline_ids))
            await _check_dependencies(session, principal, data_space, row.id, deps)
            row.depends_on_pipeline_ids = deps
        if body.name is not None and body.name != row.name:
            await _name_free(session, data_space, body.name, row.id)
            row.name = body.name
        if body.scope is not None:
            row.scope = body.scope
        if body.on_failure is not None:
            row.on_failure = body.on_failure
        if body.steps is not None:
            row.steps = [s.model_dump(mode="json", exclude_none=True) for s in body.steps]
        row.updated_by, row.updated_at = principal.user_id, now()
        emit(
            session,
            principal,
            "pipeline",
            row.id,
            "pipeline.status_changed" if row.status != previous else "pipeline.updated",
            pipeline_id=row.id,
            data_space_id=data_space,
            status=row.status,
            previous_status=previous,
            changed=sorted(changes),
        )
        await session.flush()
        response.headers["ETag"] = _etag(row)
        return PipelineView.model_validate(row)


@router.post(
    "/v1/pipelines/{id}:preview",
    status_code=202,
    response_model=PipelineRunView,
    **endpoint("POST", "/v1/pipelines/{id}:preview", operation_id="previewPipeline"),
)
async def preview_pipeline(
    pipeline_id: PathId,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    response: Response,
    body: PreviewRequest = PreviewRequest(),
) -> PipelineRunView:
    async with transaction(ROLE, principal) as session:
        row = await _pipeline(session, principal, data_space, pipeline_id)
        if not row.steps:
            raise Problem(409, "invalid_state", "The pipeline has no step to preview.")
        run = pl.PipelineRun(
            id=uuid7(),
            org_id=principal.org_id,
            pipeline_id=row.id,
            mode="preview",
            sample_rows=body.sample_rows,
            status="running",
            started_at=now(),
            created_at=now(),
        )
        session.add(run)
        emit(
            session,
            principal,
            "pipeline_run",
            run.id,
            "pipeline_run.requested",
            pipeline_run_id=run.id,
            pipeline_id=row.id,
            data_space_id=data_space,
            mode=run.mode,
            sample_rows=run.sample_rows,
            status=run.status,
            requested_by=principal.user_id,
        )
        await session.flush()
        # The worker fills rows_dropped per step and totals_check on this run (UC-06-01 step 3).
        response.headers["Location"] = f"/v1/pipeline-runs/{run.id}"
        return PipelineRunView.model_validate(run)


@router.post(
    "/v1/pipelines/{id}:materialize",
    status_code=202,
    response_model=Accepted,
    **endpoint("POST", "/v1/pipelines/{id}:materialize", operation_id="materializePipeline"),
)
async def materialize_pipeline(
    body: MaterializeRequest,
    pipeline_id: PathId,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    response: Response,
) -> Accepted:
    async with transaction(ROLE, principal) as session:
        row = await _pipeline(session, principal, data_space, pipeline_id, lock=True)
        runs = list(
            (
                await session.scalars(
                    select(pl.PipelineRun)
                    .where(pl.PipelineRun.pipeline_id == row.id)
                    .order_by(pl.PipelineRun.started_at.desc(), pl.PipelineRun.id.desc())
                )
            ).all()
        )
        if any(r.status == "running" and r.mode != "preview" for r in runs):
            raise Problem(409, "invalid_state", "A full run of this pipeline is still running.")
        last = next((r for r in runs if r.status in ("succeeded", "failed")), None)
        if last is None or last.status != "succeeded":
            raise Problem(
                409, "invalid_state", "Run the pipeline successfully before saving it as a table."
            )
        mismatched = rules.totals_mismatch(last.totals_check)
        if mismatched:
            raise Problem(
                422,
                "business_rule_violated",
                "Money column totals differ before and after the steps "
                f"({', '.join(mismatched)}); the result is not saved (UC-06-01 E2).",
            )
        taken = await session.scalar(
            select(catalog.Dataset.id).where(
                catalog.Dataset.org_id == principal.org_id,
                catalog.Dataset.data_space_id == data_space,
                catalog.Dataset.origin != "source",
                catalog.Dataset.table_name == body.table_name,
                catalog.Dataset.deleted_at.is_(None),
            )
        )
        if taken is not None and taken != row.target_dataset_id:
            # UC-06-02 E1: another table of the work schema has that name.
            raise Problem(409, "conflict", f"A table named {body.table_name} already exists.")
        if body.scope is not None:
            row.scope = body.scope
        row.updated_by, row.updated_at = principal.user_id, now()
        run = pl.PipelineRun(
            id=uuid7(),
            org_id=principal.org_id,
            pipeline_id=row.id,
            mode="full",
            status="running",
            started_at=now(),
            created_at=now(),
        )
        session.add(run)
        # The worker writes ws_{data_space}.{table_name}, registers the dataset, writes the
        # lineage edges and sets the pipeline active (UC-06-02 steps 2-3).
        emit(
            session,
            principal,
            "pipeline_run",
            run.id,
            "pipeline.materialize_requested",
            pipeline_run_id=run.id,
            pipeline_id=row.id,
            data_space_id=data_space,
            table_name=body.table_name,
            scope=row.scope,
            overwrite_dataset_id=row.target_dataset_id,
            status=run.status,
            requested_by=principal.user_id,
        )
    return _accepted(
        response,
        f"/v1/pipeline-runs/{run.id}",
        Accepted(id=run.id, status="running", poll_after_ms=POLL_AFTER_MS),
    )


@router.put(
    "/v1/pipelines/{id}/schedule",
    response_model=PipelineView,
    **endpoint("PUT", "/v1/pipelines/{id}/schedule", operation_id="schedulePipeline"),
)
async def schedule_pipeline(
    body: ScheduleRequest,
    pipeline_id: PathId,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
) -> PipelineView:
    if body.cron is not None and not rules.valid_cron(body.cron):
        raise invalid("cron", "cron takes five fields: minute hour day month weekday.")
    async with transaction(ROLE, principal) as session:
        row = await _pipeline(session, principal, data_space, pipeline_id, lock=True)
        if row.status == "draft":
            # UC-06-03 precondition: only an active (materialized) pipeline is scheduled.
            raise Problem(409, "invalid_state", "Only an active pipeline can be scheduled.")
        if body.work_calendar_id is not None:
            found = await session.scalar(
                select(org.WorkCalendar.id).where(
                    org.WorkCalendar.id == body.work_calendar_id,
                    org.WorkCalendar.org_id == principal.org_id,
                )
            )
            if found is None:
                raise invalid("work_calendar_id", "Unknown work calendar.")
        deps = list(dict.fromkeys(body.depends_on_pipeline_ids))
        await _check_dependencies(session, principal, data_space, row.id, deps)
        row.cron = body.cron
        row.work_calendar_id = body.work_calendar_id
        row.trigger_on_job_id = body.trigger_on_job_id
        row.depends_on_pipeline_ids = deps
        row.no_overlap = body.no_overlap
        row.on_failure = body.on_failure
        row.updated_by, row.updated_at = principal.user_id, now()
        # The worker (re)creates the Temporal schedule from this event (UC-06-03 step 3).
        emit(
            session,
            principal,
            "pipeline",
            row.id,
            "pipeline.scheduled",
            pipeline_id=row.id,
            data_space_id=data_space,
            status=row.status,
            cron=row.cron,
            work_calendar_id=row.work_calendar_id,
            trigger_on_job_id=row.trigger_on_job_id,
            depends_on_pipeline_ids=deps,
            no_overlap=row.no_overlap,
            on_failure=row.on_failure,
        )
        await session.flush()
        return PipelineView.model_validate(row)


# --- lineage -------------------------------------------------------------------------------------


@router.get(
    "/v1/lineage",
    response_model=LineageGraph,
    **endpoint("GET", "/v1/lineage", operation_id="getLineage"),
)
async def get_lineage(
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    object_kind: LineageKind,
    object_id: uuid.UUID,
    direction: Literal["upstream", "downstream", "both"] = "upstream",
    depth: Annotated[int, Query(ge=1, le=LINEAGE_MAX_DEPTH)] = LINEAGE_DEPTH,
) -> LineageGraph:
    edge = pl.LineageEdge
    root: tuple[str, uuid.UUID] = (object_kind, object_id)
    async with transaction(ROLE, principal) as session:
        nodes: dict[tuple[str, uuid.UUID], bool] = {root: True}
        links: dict[uuid.UUID, pl.LineageEdge] = {}
        frontier = [root]
        for _ in range(depth):
            if not frontier or len(nodes) > LINEAGE_MAX_NODES:
                break
            conditions = []
            if direction in ("upstream", "both"):
                conditions.append(tuple_(edge.to_kind, edge.to_id).in_(frontier))
            if direction in ("downstream", "both"):
                conditions.append(tuple_(edge.from_kind, edge.from_id).in_(frontier))
            found = (
                await session.scalars(
                    select(edge).where(
                        edge.org_id == principal.org_id, edge.valid_to.is_(None), or_(*conditions)
                    )
                )
            ).all()
            visible_frontier = set(frontier)
            frontier = []
            for e in found:
                ends = [(e.from_kind, e.from_id), (e.to_kind, e.to_id)]
                for end in ends:
                    if end in visible_frontier:
                        continue
                    # An edge recorded by another Data Space leads to a node the caller may not
                    # see: it keeps its place in the graph, not its identity (UC-06-05 step 2).
                    seen_here = e.data_space_id == data_space
                    if end not in nodes:
                        nodes[end] = seen_here
                        if seen_here:
                            frontier.append(end)
                    elif seen_here and not nodes[end]:
                        nodes[end] = True
                        frontier.append(end)
                links[e.id] = e
        touches_space = any(e.data_space_id == data_space for e in links.values())
        if not touches_space:
            in_space = root[0] == "dataset" and await session.scalar(
                select(catalog.Dataset.id).where(
                    catalog.Dataset.id == object_id,
                    catalog.Dataset.org_id == principal.org_id,
                    catalog.Dataset.data_space_id == data_space,
                )
            )
            if not in_space:
                raise Problem(404, "not_found", "Object not found.")
        dataset_ids = [i for (k, i), shown in nodes.items() if k == "dataset" and shown]
        names: dict[uuid.UUID, str] = {}
        if dataset_ids:
            for ds in (
                await session.scalars(
                    select(catalog.Dataset).where(
                        catalog.Dataset.id.in_(dataset_ids),
                        catalog.Dataset.org_id == principal.org_id,
                    )
                )
            ).all():
                names[ds.id] = ds.display_name or f"{ds.schema_name}.{ds.table_name}"
    keys: dict[tuple[str, uuid.UUID], str] = {}
    out_nodes: list[LineageNode] = []
    hidden = 0
    for (kind, node_id), shown in nodes.items():
        if shown:
            key = f"{kind}:{node_id}"
            out_nodes.append(LineageNode(key=key, kind=kind, id=node_id, name=names.get(node_id)))
        else:
            hidden += 1
            key = f"hidden:{hidden}"
            out_nodes.append(
                LineageNode(key=key, kind=kind, id=None, name=HIDDEN_NAME, hidden=True)
            )
        keys[(kind, node_id)] = key
    out_edges = [
        LineageLink(
            source=keys[(e.from_kind, e.from_id)],
            target=keys[(e.to_kind, e.to_id)],
            via=e.via if e.data_space_id == data_space else None,
            via_ref=e.via_ref if e.data_space_id == data_space else None,
        )
        for e in links.values()
        if (e.from_kind, e.from_id) in keys and (e.to_kind, e.to_id) in keys
    ]
    root_node = out_nodes[0]
    if len(out_nodes) > LINEAGE_MAX_NODES:
        counts = Counter(n.kind for n in out_nodes[1:])
        return LineageGraph(
            root=root_node,
            direction=direction,
            depth=depth,
            nodes=[root_node],
            edges=[],
            grouped=True,
            groups=[LineageGroup(kind=k, count=c) for k, c in sorted(counts.items())],
        )
    return LineageGraph(
        root=root_node, direction=direction, depth=depth, nodes=out_nodes, edges=out_edges
    )


# --- data marts and shared data products ---------------------------------------------------------


@router.post(
    "/v1/marts",
    status_code=201,
    response_model=DataMartView,
    **endpoint("POST", "/v1/marts", operation_id="createDataMart"),
)
async def create_mart(
    body: MartCreate, principal: CurrentPrincipal, data_space: CurrentDataSpace
) -> DataMartView:
    async with transaction(ROLE, principal) as session:
        await require_datasets(
            session, principal, data_space, [body.fact_dataset_id], "fact_dataset_id"
        )
        stamp = now()
        mart = pl.DataMart(
            id=uuid7(),
            org_id=principal.org_id,
            data_space_id=data_space,
            name=body.name,
            fact_dataset_id=body.fact_dataset_id,
            grain=body.grain,
            measures=body.measures,
            dimensions=body.dimensions,
            relations=body.relations,
            checks=body.checks,
            # Nothing proposes dimensions from the fact table's relations yet; the column stays
            # null until something does, and the author picks from it into dimensions.
            suggested_dimensions=None,
            # UC-06-06 step 2: the worker checks the relations; until then nothing publishes.
            relation_check_passed=False,
            status="draft",
            created_by=principal.user_id,
            updated_by=principal.user_id,
            created_at=stamp,
            updated_at=stamp,
        )
        session.add(mart)
        emit(
            session,
            principal,
            "data_mart",
            mart.id,
            "data_mart.created",
            data_mart_id=mart.id,
            data_space_id=data_space,
            fact_dataset_id=body.fact_dataset_id,
            status=mart.status,
        )
        await session.flush()
        return DataMartView.model_validate(mart)


@router.get(
    "/v1/marts",
    response_model=Page[DataMartView],
    **endpoint("GET", "/v1/marts", operation_id="listDataMarts"),
)
async def list_marts(
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    page: Annotated[PageRequest, Depends(page_request)],
    order: Annotated[
        Order,
        Depends(order_by("created_at", "updated_at", "name", "status", default="created_at desc")),
    ],
    status: Literal["draft", "active", "retired"] | None = None,
) -> Page[DataMartView]:
    m = pl.DataMart
    query = select(m).where(m.org_id == principal.org_id, m.data_space_id == data_space)
    if status:
        query = query.where(m.status == status)
    query = keyset(
        query,
        columns={
            "created_at": m.created_at,
            "updated_at": m.updated_at,
            "name": m.name,
            "status": m.status,
        },
        tiebreak=m.id,
        order=order,
        page=page,
    )
    async with transaction(ROLE, principal) as session:
        rows, token = next_page(list((await session.scalars(query)).all()), order=order, page=page)
    return Page(items=[DataMartView.model_validate(r) for r in rows], next_page_token=token)


async def _mart(
    session: AsyncSession,
    principal: Principal,
    data_space: uuid.UUID,
    mart_id: uuid.UUID,
    *,
    lock: bool = False,
) -> pl.DataMart:
    query = select(pl.DataMart).where(
        pl.DataMart.id == mart_id,
        pl.DataMart.org_id == principal.org_id,
        pl.DataMart.data_space_id == data_space,
    )
    if lock:
        query = query.with_for_update()
    row = await session.scalar(query)
    if row is None:
        raise Problem(404, "not_found", "Data mart not found.")
    return row


@router.get(
    "/v1/marts/{id}",
    response_model=DataMartView,
    **endpoint("GET", "/v1/marts/{id}", operation_id="getDataMart"),
)
async def get_mart(
    mart_id: PathId, principal: CurrentPrincipal, data_space: CurrentDataSpace
) -> DataMartView:
    async with transaction(ROLE, principal) as session:
        return DataMartView.model_validate(await _mart(session, principal, data_space, mart_id))


@router.patch(
    "/v1/marts/{id}",
    response_model=DataMartView,
    **endpoint("PATCH", "/v1/marts/{id}", operation_id="updateDataMart"),
)
async def update_mart(
    body: MartPatch, mart_id: PathId, principal: CurrentPrincipal, data_space: CurrentDataSpace
) -> DataMartView:
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise invalid("body", "Nothing to change.")
    for field, value in changes.items():
        if value is None:
            raise invalid(field, f"{field} cannot be null.")
    if body.name is not None and not body.name.strip():
        raise invalid("name", "name cannot be blank.")
    async with transaction(ROLE, principal) as session:
        mart = await _mart(session, principal, data_space, mart_id, lock=True)
        if mart.status == "retired":
            raise Problem(409, "invalid_state", "A retired data mart is not edited.")
        if body.name is not None:
            mart.name = body.name.strip()
        for field in ("dimensions", "measures", "relations"):
            if field in changes:
                setattr(mart, field, changes[field])
        previous = mart.status
        recheck = bool({"dimensions", "measures", "relations"} & changes.keys())
        if recheck:
            # UC-06-06 step 2 / E1: the model changed, so the relation check it passed no longer
            # holds; the worker runs it again and nothing publishes from the mart until it passes.
            # An active mart needs a passed check (CHECK on data_mart), so it is a draft again.
            mart.relation_check_passed = False
            if mart.status == "active":
                mart.status = "draft"
        mart.updated_by, mart.updated_at = principal.user_id, now()
        emit(
            session,
            principal,
            "data_mart",
            mart.id,
            "data_mart.updated",
            data_mart_id=mart.id,
            data_space_id=data_space,
            status=mart.status,
            previous_status=previous,
            changed=sorted(changes),
            relation_check_requested=recheck,
        )
        await session.flush()
        return DataMartView.model_validate(mart)


@router.post(
    "/v1/data-products/{id}:publish",
    response_model=DataProductView,
    **endpoint("POST", "/v1/data-products/{id}:publish", operation_id="publishDataProduct"),
)
async def publish_data_product(
    product_id: PathId, principal: CurrentPrincipal, data_space: CurrentDataSpace
) -> DataProductView:
    dp = pl.DataProduct
    async with transaction(ROLE, principal) as session:
        product = await session.scalar(
            select(dp)
            .where(
                dp.id == product_id, dp.org_id == principal.org_id, dp.data_space_id == data_space
            )
            .with_for_update()
        )
        if product is None:
            raise Problem(404, "not_found", "Data product not found.")
        if product.status != "draft":
            raise Problem(409, "invalid_state", f"The data product is {product.status}.")
        if product.created_by == principal.user_id:
            # UC-06-06 step 4: the owner approves what the engineer drafted, not the drafter.
            raise Problem(
                403, "policy_denied", "Whoever drafted the data product cannot publish it."
            )
        if product.owner_id != principal.user_id:
            # The approver of a published version is its owner (CHECK approved_by = owner_id).
            raise Problem(403, "policy_denied", "Only the data product's owner publishes it.")
        if product.data_mart_id is not None:
            mart = await session.scalar(
                select(pl.DataMart).where(pl.DataMart.id == product.data_mart_id)
            )
            if mart is None or not mart.relation_check_passed:
                raise Problem(
                    422,
                    "business_rule_violated",
                    "The data mart's relation check has not passed (UC-06-06 E1).",
                )
        previous = await session.scalar(
            select(dp)
            .where(
                dp.org_id == principal.org_id,
                dp.data_space_id == data_space,
                dp.code == product.code,
                dp.status == "published",
                dp.id != product.id,
            )
            .order_by(dp.version.desc())
            .with_for_update()
        )
        stamp = now()
        breaking = previous is not None and rules.breaking_changes(
            previous.contract, product.contract
        )
        if breaking:
            notice = product.breaking_notice_at
            if notice is None or stamp - notice < BREAKING_NOTICE:
                raise Problem(
                    422,
                    "business_rule_violated",
                    "A breaking contract change is announced to consumers 30 days before it is "
                    f"published (BR-60): {'; '.join(breaking)}.",
                )
        if previous is not None:
            # BR-60: the old version stops being served once the notice has run out (now).
            previous.status = "deprecated"
            previous.retire_after = stamp
            previous.updated_by, previous.updated_at = principal.user_id, stamp
            emit(
                session,
                principal,
                "data_product",
                previous.id,
                "data_product.deprecated",
                data_product_id=previous.id,
                data_space_id=data_space,
                code=previous.code,
                version=previous.version,
                status=previous.status,
                consumers=previous.consumers,
            )
        product.status = "published"
        product.approved_by = principal.user_id
        product.updated_by, product.updated_at = principal.user_id, stamp
        emit(
            session,
            principal,
            "data_product",
            product.id,
            "data_product.published",
            data_product_id=product.id,
            data_space_id=data_space,
            code=product.code,
            version=product.version,
            status=product.status,
            approved_by=principal.user_id,
            consumers=product.consumers,
            replaces=previous.id if previous else None,
        )
        await session.flush()
        return DataProductView.model_validate(product)


# --- writebacks ----------------------------------------------------------------------------------


def _window(value: Any) -> WriteWindow | None:
    if value is None or getattr(value, "isempty", False):
        return None
    lower, upper = getattr(value, "lower", None), getattr(value, "upper", None)
    if lower is None or upper is None:
        return None
    return WriteWindow(start=lower, end=upper)


def _writeback_view(row: pl.WritebackRequest) -> WritebackView:
    return WritebackView(
        id=row.id,
        data_space_id=row.data_space_id,
        source_id=row.source_id,
        target_table=row.target_table,
        from_dataset_id=row.from_dataset_id,
        requested_by=row.requested_by,
        diff_summary=row.diff_summary,
        diff_ref=row.diff_ref,
        deletes=row.deletes,
        write_window=_window(row.write_window),
        snapshot_ref=row.snapshot_ref,
        approved_by=row.approved_by,
        proposal_id=row.proposal_id,
        status=row.status,
        executed_at=row.executed_at,
        totals_check=row.totals_check,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def _writeback(
    session: AsyncSession, principal: Principal, data_space: uuid.UUID, writeback_id: uuid.UUID
) -> pl.WritebackRequest:
    row = await session.scalar(
        select(pl.WritebackRequest)
        .where(
            pl.WritebackRequest.id == writeback_id,
            pl.WritebackRequest.org_id == principal.org_id,
            pl.WritebackRequest.data_space_id == data_space,
        )
        .with_for_update()
    )
    if row is None:
        raise Problem(404, "not_found", "Writeback request not found.")
    return row


async def _sensitive_columns(
    session: AsyncSession, principal: Principal, row: pl.WritebackRequest
) -> frozenset[str]:
    """Columns labelled personal data (catalog column_meta.pii_label) of the table the write
    reads from and of the target table, lower-cased: they never appear in a diff sample."""
    ds = catalog.Dataset
    datasets = [ds.id == row.from_dataset_id]
    parts = row.target_table.split(".")
    if len(parts) >= 2:
        datasets.append(
            and_(
                ds.source_id == row.source_id,
                ds.schema_name == parts[-2],
                ds.table_name == parts[-1],
            )
        )
    else:
        datasets.append(and_(ds.source_id == row.source_id, ds.table_name == parts[-1]))
    col = catalog.ColumnMeta
    names = await session.scalars(
        select(col.name)
        .join(ds, ds.id == col.dataset_id)
        .where(
            ds.org_id == principal.org_id,
            or_(*datasets),
            col.pii_label.is_not(None),
            col.removed_in_version.is_(None),
        )
    )
    return frozenset(n.lower() for n in names)


def _without_sensitive(value: Any, sensitive: frozenset[str], *, top: bool = False) -> Any:
    """The diff summary minus every sample value of a sensitive column (BR-113, UC-06-04 step 2).
    The worker already leaves them out; this keeps a mistake there from reaching a screen. Top
    level keys are the counts, never column names, so they stay."""
    if isinstance(value, list):
        kept = []
        for item in value:
            # A sample may also be a list of {"column": name, "before": …, "after": …}.
            column = item.get("column") if isinstance(item, dict) else None
            if isinstance(column, str) and column.lower() in sensitive:
                continue
            kept.append(_without_sensitive(item, sensitive))
        return kept
    if isinstance(value, dict):
        return {
            k: _without_sensitive(v, sensitive)
            for k, v in value.items()
            if top or not (isinstance(k, str) and k.lower() in sensitive)
        }
    return value


@router.get(
    "/v1/writebacks/{id}",
    response_model=WritebackView,
    **endpoint("GET", "/v1/writebacks/{id}", operation_id="getWriteback"),
)
async def get_writeback(
    writeback_id: PathId, principal: CurrentPrincipal, data_space: CurrentDataSpace
) -> WritebackView:
    """Where :undo's Location points: diff, before and after sample, status, approver."""
    async with transaction(ROLE, principal) as session:
        row = await session.scalar(
            select(pl.WritebackRequest).where(
                pl.WritebackRequest.id == writeback_id,
                pl.WritebackRequest.org_id == principal.org_id,
                pl.WritebackRequest.data_space_id == data_space,
            )
        )
        if row is None:
            raise Problem(404, "not_found", "Writeback request not found.")
        sensitive = await _sensitive_columns(session, principal, row)
    view = _writeback_view(row)
    if sensitive:
        view.diff_summary = _without_sensitive(view.diff_summary, sensitive, top=True)
    return view


@router.post(
    "/v1/writebacks",
    status_code=201,
    response_model=WritebackView,
    **endpoint("POST", "/v1/writebacks", operation_id="createWriteback"),
)
async def create_writeback(
    body: WritebackCreate,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    response: Response,
    dry_run: Annotated[
        bool, Query(description="Check the request without recording it; no diff is computed")
    ] = False,
) -> WritebackView:
    _forbid_delegated(principal)
    async with transaction(ROLE, principal) as session:
        source = await session.scalar(
            select(catalog.Source).where(
                catalog.Source.id == body.source_id,
                catalog.Source.org_id == principal.org_id,
                catalog.Source.data_space_id == data_space,
                catalog.Source.deleted_at.is_(None),
            )
        )
        if source is None:
            raise invalid("source_id", "The source is not in this Data Space.")
        if not source.writeback_secret_ref:
            # UC-06-04 precondition: the target source declares its own writeback account.
            raise Problem(403, "forbidden", "The source has no writeback account.")
        if body.target_table not in (source.writeback_tables or []):
            # UC-06-04 precondition: only a table the source lists as a writeback target.
            raise Problem(
                422,
                "business_rule_violated",
                f"{body.target_table} is not a writeback target of the source (writeback_tables).",
            )
        derived = await session.scalar(
            select(catalog.Dataset).where(
                catalog.Dataset.id == body.from_dataset_id,
                catalog.Dataset.org_id == principal.org_id,
                catalog.Dataset.data_space_id == data_space,
                catalog.Dataset.deleted_at.is_(None),
            )
        )
        if derived is None:
            raise invalid("from_dataset_id", "The dataset is not in this Data Space.")
        if derived.origin == "source":
            raise invalid("from_dataset_id", "A writeback starts from a derived table.")
        stamp = now()
        row = pl.WritebackRequest(
            id=uuid7(),
            org_id=principal.org_id,
            data_space_id=data_space,
            source_id=body.source_id,
            target_table=body.target_table,
            from_dataset_id=body.from_dataset_id,
            requested_by=principal.user_id,
            diff_summary={},
            deletes=0,
            write_window=(
                Range(body.write_window.start, body.write_window.end, bounds="[)")
                if body.write_window
                else None
            ),
            status="draft",
            created_by=principal.user_id,
            updated_by=principal.user_id,
            created_at=stamp,
            updated_at=stamp,
        )
        if dry_run:
            response.status_code = 200
            return _writeback_view(row)
        session.add(row)
        # SD-06 step 1: the worker computes the diff (no sensitive column in the sample, BR-113)
        # and moves the request to previewed.
        emit(
            session,
            principal,
            "writeback_request",
            row.id,
            "writeback.diff_requested",
            writeback_request_id=row.id,
            data_space_id=data_space,
            source_id=row.source_id,
            from_dataset_id=row.from_dataset_id,
            status=row.status,
            requested_by=principal.user_id,
        )
        await session.flush()
        return _writeback_view(row)


@router.post(
    "/v1/writebacks/{id}:submit",
    response_model=WritebackView,
    **endpoint("POST", "/v1/writebacks/{id}:submit", operation_id="submitWriteback"),
)
async def submit_writeback(
    writeback_id: PathId, principal: CurrentPrincipal, data_space: CurrentDataSpace
) -> WritebackView:
    _forbid_delegated(principal)
    async with transaction(ROLE, principal) as session:
        row = await _writeback(session, principal, data_space, writeback_id)
        if row.requested_by != principal.user_id:
            raise Problem(403, "forbidden", "Only the requester sends the writeback for approval.")
        if row.status != "previewed":
            raise Problem(
                409, "invalid_state", f"The writeback is {row.status}; preview the diff first."
            )
        summary = row.diff_summary if isinstance(row.diff_summary, dict) else {}
        if int(summary.get("deletes") or 0) > 0:
            raise Problem(
                422, "business_rule_violated", "A writeback never deletes target rows (BR-113)."
            )
        row.status = "submitted"
        # The proposal (kind writeback) is created by the inbox module from this event under this
        # id; its reviewer is the target table's owner and never the requester (BR-24).
        row.proposal_id = uuid7()
        row.updated_by, row.updated_at = principal.user_id, now()
        emit(
            session,
            principal,
            "writeback_request",
            row.id,
            "writeback.submitted",
            writeback_request_id=row.id,
            proposal_id=row.proposal_id,
            data_space_id=data_space,
            source_id=row.source_id,
            from_dataset_id=row.from_dataset_id,
            status=row.status,
            requested_by=row.requested_by,
            excluded_reviewer_ids=[row.requested_by],
        )
        await session.flush()
        return _writeback_view(row)


@router.post(
    "/v1/writebacks/{id}:undo",
    status_code=202,
    response_model=Accepted,
    **endpoint("POST", "/v1/writebacks/{id}:undo", operation_id="undoWriteback"),
)
async def undo_writeback(
    body: UndoRequest,
    writeback_id: PathId,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    response: Response,
) -> Accepted:
    _forbid_delegated(principal)
    async with transaction(ROLE, principal) as session:
        row = await _writeback(session, principal, data_space, writeback_id)
        if row.status != "executed" or row.executed_at is None:
            raise Problem(409, "invalid_state", f"The writeback is {row.status}, not executed.")
        if now() - row.executed_at > UNDO_WINDOW:
            raise Problem(
                409, "invalid_state", "The 30-day undo window of this writeback has passed."
            )
        # UC-06-04 5a: the undo is approved like the write; the worker restores from the snapshot
        # and publishes writeback.rolled_back. undo_requested keeps a second undo from being asked.
        row.status = "undo_requested"
        row.updated_by, row.updated_at = principal.user_id, now()
        emit(
            session,
            principal,
            "writeback_request",
            row.id,
            "writeback.undo_requested",
            writeback_request_id=row.id,
            data_space_id=data_space,
            previous_status="executed",
            status=row.status,
            requested_by=principal.user_id,
            excluded_reviewer_ids=[principal.user_id],
            reason=body.reason.strip(),
        )
    return _accepted(
        response,
        f"/v1/writebacks/{row.id}",
        Accepted(id=row.id, status="undo_requested", poll_after_ms=POLL_AFTER_MS),
    )
