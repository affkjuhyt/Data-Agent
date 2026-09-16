"""/api/knowledge/sources — the ANKT external source registry.

Sits beside /api/knowledge/warehouses deliberately: both are external data
connections a data engineer configures, and putting sources on their own island
would mean a second navigation, a second mental model and a second set of
permissions for the same job.

Reads go through the registry (no network); a run goes through the same runner
the CronJob uses, so the button on the screen and the schedule at 03:00 cannot
drift apart into two behaviours.
"""
import logging
import uuid
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool

from libs.basemodel.services_databaseconnector.shared_model import ResponseModel
from services.ankt_connectors import manage, registry
from services.ankt_connectors.plugins import SCHEMAS
from services.kb_backend.dependencies.auth import require_perm
from services.kb_backend.dependencies.services import svc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/knowledge/sources", tags=["Knowledge-Sources"])


def _session(request: Request, tenant_id: str) -> Any:
    """A tenant-scoped SQLAlchemy session — the client every service here holds.

    `get_client(tenant_id)` sets app.tenant_id transaction-locally, which is
    what makes the FORCE'd row-level policies on the ankt tables do anything:
    a caller that skips it sees zero rows rather than another tenant's, so the
    failure is visible instead of silent.
    """
    return svc(request, "postgres").get_client(tenant_id)


@router.get("", response_model=ResponseModel)
async def list_sources_route(
        request: Request,
        ctx: dict[str, Any] = Depends(require_perm("viewSources")),
) -> ResponseModel[Any]:
    """Every source with its last run folded in — what the catalogue screen shows."""
    async with _session(request, ctx["tenant_id"]) as session:
        return ResponseModel(code=200, data=await registry.list_sources(session))


@router.get("/connectors", response_model=ResponseModel)
async def list_connectors_route(
        ctx: dict[str, Any] = Depends(require_perm("viewSources")),
) -> ResponseModel[Any]:
    """The protocols a source can speak, WITH the fields each one needs.

    The wizard draws its form from this rather than from a hard-coded list, so
    a plugin added to the backend becomes an option without a frontend release
    — the same idea as warehouse.data.ts, moved to the side that knows.
    """
    return ResponseModel(code=200, data=[SCHEMAS[k] for k in sorted(SCHEMAS)])


@router.get("/variables", response_model=ResponseModel)
async def list_variables_route(
        request: Request,
        ctx: dict[str, Any] = Depends(require_perm("viewSources")),
) -> ResponseModel[Any]:
    async with _session(request, ctx["tenant_id"]) as session:
        return ResponseModel(code=200, data=await manage.list_variables(session))


@router.post("/preview", response_model=ResponseModel)
async def preview_route(
        payload: dict[str, Any] = Body(...),
        ctx: dict[str, Any] = Depends(require_perm("viewSources")),
) -> ResponseModel[Any]:
    """Try a connector config against the live source without saving anything.

    The form is unusable without it: nobody types `VNM.GGXWDG.A` correctly from
    memory, and a steward who cannot see what a key returns before saving will
    save something wrong and find out on a schedule at 03:00.
    """
    result = await run_in_threadpool(
        manage.preview, payload["connector"], payload.get("config") or {},
        payload["series_key"])
    return ResponseModel(code=200, data=result)


@router.post("", status_code=201, response_model=ResponseModel)
async def create_source_route(
        request: Request,
        payload: dict[str, Any] = Body(...),
        ctx: dict[str, Any] = Depends(require_perm("editSource")),
) -> ResponseModel[Any]:
    async with _session(request, ctx["tenant_id"]) as session:
        source_id = await manage.create_source(
            session, uuid.UUID(ctx["tenant_id"]), payload)
    return ResponseModel(code=201, data={"source_id": source_id})


@router.get("/{source_id}", response_model=ResponseModel)
async def get_source_route(
        source_id: str,
        request: Request,
        ctx: dict[str, Any] = Depends(require_perm("viewSources")),
) -> ResponseModel[Any]:
    async with _session(request, ctx["tenant_id"]) as session:
        try:
            data = await manage.get_source(
                session, uuid.UUID(ctx["tenant_id"]), uuid.UUID(source_id))
        except manage.SourceNotFound as exc:
            raise HTTPException(status_code=404, detail="Source not found") from exc
    return ResponseModel(code=200, data=data)


@router.patch("/{source_id}", response_model=ResponseModel)
async def update_source_route(
        source_id: str,
        request: Request,
        payload: dict[str, Any] = Body(...),
        ctx: dict[str, Any] = Depends(require_perm("editSource")),
) -> ResponseModel[Any]:
    """PATCH, not PUT: a form that edits one field must not blank the rest by
    omitting them, and a blanked base URL is expensive to notice."""
    async with _session(request, ctx["tenant_id"]) as session:
        try:
            await manage.update_source(
                session, uuid.UUID(ctx["tenant_id"]), uuid.UUID(source_id), payload)
        except manage.SourceNotFound as exc:
            raise HTTPException(status_code=404, detail="Source not found") from exc
    return ResponseModel(code=200, data={"source_id": source_id})


@router.delete("/{source_id}", response_model=ResponseModel)
async def delete_source_route(
        source_id: str,
        request: Request,
        ctx: dict[str, Any] = Depends(require_perm("editSource")),
) -> ResponseModel[Any]:
    """Removes the source and its bindings. Observations survive — they are
    published facts with their own artifacts, not a property of the catalogue
    row that happened to fetch them."""
    async with _session(request, ctx["tenant_id"]) as session:
        try:
            await manage.delete_source(
                session, uuid.UUID(ctx["tenant_id"]), uuid.UUID(source_id))
        except manage.SourceNotFound as exc:
            raise HTTPException(status_code=404, detail="Source not found") from exc
    return ResponseModel(code=200, data={"deleted": source_id})


@router.post("/{source_id}/series", status_code=201, response_model=ResponseModel)
async def add_binding_route(
        source_id: str,
        request: Request,
        payload: dict[str, Any] = Body(...),
        ctx: dict[str, Any] = Depends(require_perm("editSource")),
) -> ResponseModel[Any]:
    async with _session(request, ctx["tenant_id"]) as session:
        binding_id = await manage.add_binding(
            session, uuid.UUID(ctx["tenant_id"]), uuid.UUID(source_id), payload)
    return ResponseModel(code=201, data={"binding_id": binding_id})


@router.patch("/series/{binding_id}", response_model=ResponseModel)
async def toggle_binding_route(
        binding_id: str,
        request: Request,
        payload: dict[str, Any] = Body(...),
        ctx: dict[str, Any] = Depends(require_perm("editSource")),
) -> ResponseModel[Any]:
    async with _session(request, ctx["tenant_id"]) as session:
        await manage.set_binding_enabled(
            session, uuid.UUID(ctx["tenant_id"]), uuid.UUID(binding_id),
            bool(payload.get("enabled", True)))
    return ResponseModel(code=200, data={"binding_id": binding_id})


@router.delete("/series/{binding_id}", response_model=ResponseModel)
async def remove_binding_route(
        binding_id: str,
        request: Request,
        ctx: dict[str, Any] = Depends(require_perm("editSource")),
) -> ResponseModel[Any]:
    async with _session(request, ctx["tenant_id"]) as session:
        await manage.remove_binding(
            session, uuid.UUID(ctx["tenant_id"]), uuid.UUID(binding_id))
    return ResponseModel(code=200, data={"deleted": binding_id})


@router.get("/{source_id}/series", response_model=ResponseModel)
async def list_series_route(
        source_id: str,
        request: Request,
        ctx: dict[str, Any] = Depends(require_perm("viewSources")),
) -> ResponseModel[Any]:
    async with _session(request, ctx["tenant_id"]) as session:
        return ResponseModel(
            code=200, data=await registry.list_bindings(session, uuid.UUID(source_id)))


@router.get("/{source_id}/runs", response_model=ResponseModel)
async def list_runs_route(
        source_id: str,
        request: Request,
        ctx: dict[str, Any] = Depends(require_perm("viewSources")),
        limit: int = Query(20, ge=1, le=100),
) -> ResponseModel[Any]:
    async with _session(request, ctx["tenant_id"]) as session:
        return ResponseModel(
            code=200, data=await registry.list_runs(session, uuid.UUID(source_id), limit))


@router.post("/{source_id}/runs", status_code=202, response_model=ResponseModel)
async def trigger_run_route(
        source_id: str,
        request: Request,
        background: BackgroundTasks,
        ctx: dict[str, Any] = Depends(require_perm("runSource")),
) -> ResponseModel[Any]:
    """Start a run and return immediately. Same code path the schedule uses.

    Asynchronous because it has to be: the browser client gives a mutation
    three seconds (lib/apiClient.ts) and fetching seven series from two
    publishers takes longer than that, so a synchronous version would time out
    in the UI while still succeeding on the server — the worst of both. It also
    does not scale: a catalogue of 38 sources will never fit inside a request.

    `connector_run` already existed as the thing a screen polls, so the run row
    IS the handle; nothing new had to be invented to make this asynchronous.
    """
    postgres = svc(request, "postgres")
    tenant_id = ctx["tenant_id"]
    minio = svc(request, "minio").get_client()
    background.add_task(_run_in_background, postgres, minio, tenant_id, source_id)
    return ResponseModel(code=202, data={"status": "running", "source_id": source_id})


async def _run_in_background(postgres: Any, minio: Any, tenant_id: str,
                             source_id: str) -> None:
    from services.ankt_connectors.runner import run_source

    try:
        async with postgres.get_client(tenant_id) as session:
            await run_source(session, uuid.UUID(tenant_id), uuid.UUID(source_id),
                             triggered_by="manual", minio=minio, postgres=postgres)
    except Exception:
        # run_source records the failure on the run row before re-raising, so
        # the screen shows it. Logging here and swallowing keeps a background
        # task from taking the worker down with it.
        logger.exception("source run failed | source_id=%s", source_id)
