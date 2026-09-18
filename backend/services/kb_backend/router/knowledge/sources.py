"""/api/knowledge/sources — the scheduled source registry (http_file /
web_crawl / web_crawl_headless / rss / rest_api / sdmx). The `web-crawler`
platform worker is a second reader of "KBSource"/"KBSourceConfig" (it decides
what's due off "KBSourceRun"); this router owns writes and the manual
"Run Now" trigger.
"""
from __future__ import annotations

from typing import Any

import yaml
from fastapi import APIRouter, Depends, HTTPException, Request

from libs.basemodel.services_databaseconnector.shared_model import ResponseModel
from libs.basemodel.services_databaseconnector.postgres_model import SourceConnectorType
from services.kb_backend.dependencies.services import svc
from services.kb_backend.dependencies.auth import require_perm
from services.kb_backend.schemas import CreateSourceRequest, UpdateSourceConfigRequest, BulkImportRequest
from services.kb_backend.operations.source_ops import (
    list_sources, create_source, update_source_config, delete_source, list_source_runs,
)

_SUPPORTED_CONNECTORS = {c.value for c in SourceConnectorType}
_DEFAULT_CRON = "0 3 * * *"  # daily at 03:00 — a bulk-imported source without its own `schedule` still gets one

router = APIRouter(prefix="/api/knowledge/sources", tags=["Knowledge-Sources"])

# Consumed by platform_workers/workers/web_crawler/worker.py's best-effort
# manual-trigger consumer — same decoupled shape as schema_crawler's
# ddl.changes topic, so "Run Now" works even without a live consumer (it
# just waits for the next scheduled cron tick instead, per the message
# staying on the topic).
MANUAL_TRIGGER_TOPIC = "web-crawl.manual-trigger"


@router.get("", response_model=ResponseModel)
async def list_sources_route(
        request: Request,
        ctx: dict[str, Any] = Depends(require_perm("viewSources")),
        cursor: str | None = None,
) -> ResponseModel[Any]:
    result = await list_sources(svc(request, "postgres"), tenant_id=ctx["tenant_id"], cursor=cursor)
    return ResponseModel(code=200, data=result)


@router.post("", response_model=ResponseModel)
async def create_source_route(
        body: CreateSourceRequest,
        request: Request,
        ctx: dict[str, Any] = Depends(require_perm("addSource")),
) -> ResponseModel[Any]:
    result = await create_source(
        svc(request, "postgres"), body.model_dump(), created_by=ctx["user_name"],
        tenant_id=ctx["tenant_id"], role_id=ctx["role_id"],
    )
    return ResponseModel(code=200, data=result)


@router.patch("/{source_id}", response_model=ResponseModel)
async def update_source_route(
        source_id: str,
        body: UpdateSourceConfigRequest,
        request: Request,
        ctx: dict[str, Any] = Depends(require_perm("editSource")),
) -> ResponseModel[Any]:
    result = await update_source_config(
        svc(request, "postgres"), source_id,
        {k: v for k, v in body.model_dump().items() if v is not None},
        updated_by=ctx["user_name"], tenant_id=ctx["tenant_id"],
    )
    return ResponseModel(code=200, data=result)


@router.delete("/{source_id}", response_model=ResponseModel)
async def delete_source_route(
        source_id: str,
        request: Request,
        ctx: dict[str, Any] = Depends(require_perm("deleteSource")),
) -> ResponseModel[Any]:
    await delete_source(svc(request, "postgres"), source_id, tenant_id=ctx["tenant_id"])
    return ResponseModel(code=200, data=None)


@router.get("/{source_id}/runs", response_model=ResponseModel)
async def list_source_runs_route(
        source_id: str,
        request: Request,
        ctx: dict[str, Any] = Depends(require_perm("viewSources")),
) -> ResponseModel[Any]:
    result = await list_source_runs(svc(request, "postgres"), source_id, tenant_id=ctx["tenant_id"])
    return ResponseModel(code=200, data=result)


_SCHEDULE_WORD_TO_CRON = {
    # Best-effort mapping from the free-text `schedule` phrases the ANKT
    # registry uses to a 5-field cron. Anything not recognised (e.g. "monthly
    # (day 3-6)", "every 15 min") falls back to _DEFAULT_CRON — a steward can
    # tighten it afterward via PATCH; bulk-import's job is to get all ~30
    # sources registered in one action, not to perfectly parse every phrasing.
    "hourly": "0 * * * *",
    "daily": "0 3 * * *",
    "semi-monthly": "0 3 1,15 * *",
    "weekly": "0 3 * * 1",
    "monthly": "0 3 3 * *",
    "quarterly": "0 3 1 1,4,7,10 *",
    "semi-annual": "0 3 1 1,7 *",
    "yearly": "0 3 1 1 *",
}


def _entry_to_create_requests(entry: dict) -> tuple[list[CreateSourceRequest], str | None]:
    """One ANKT registry row -> one or more CreateSourceRequests, or ([], reason)
    if this connector type still isn't ingestible (manual-upload/bulk-parquet —
    the latter is a one-off dataset import, never a scheduled KBSource, see
    the crawl-feature plan's "Explicitly deferred").

    More than one request comes out when a single registry row actually
    bundles several distinct files — real example: an NSO monthly report row
    with `format: [docx, xlsx]` and two `sample_urls`, one narrative report
    and one data-table workbook. KBSourceConfig has exactly one `url`, so
    each file becomes its own source (matching how Document Hub already
    treats each upload as its own document) rather than silently keeping
    only sample_urls[0] and dropping the rest."""
    raw_connector = str(entry.get("connector", "")).strip().replace("-", "_")
    if raw_connector not in _SUPPORTED_CONNECTORS:
        return [], f"connector={entry.get('connector')!r} is not ingestible by the V1 worker yet"

    # http_file/rss/rest_api/sdmx need a literal fetchable resource URL — a
    # registry's `landing_url` is usually the browse/listing HTML page a
    # human would open (real example: an ANKT entry whose http_file
    # `landing_url` is an `.htm` page with the actual PDF one level down at
    # `sample_urls[0]`, plus a note that the landing page's DNS doesn't even
    # resolve from this server). web_crawl/*_headless genuinely want the
    # landing page itself, since crawling IS the point, so those keep
    # preferring it and never fan out to sample_urls at all.
    sample_urls = [u for u in (entry.get("sample_urls") or []) if u]
    direct_fetch = raw_connector in ("http_file", "rss", "rest_api", "sdmx")
    if direct_fetch and sample_urls:
        urls = sample_urls
    else:
        landing = entry.get("landing_url")
        urls = [landing] if landing else (sample_urls[:1] if not direct_fetch else [])
    if not urls:
        return [], "no landing_url or sample_urls to crawl"

    schedule_raw = str(entry.get("schedule", "")).strip().lower()
    cron = _SCHEDULE_WORD_TO_CRON.get(schedule_raw.split(" ")[0], _DEFAULT_CRON)

    base_title = entry.get("title") or entry.get("id") or urls[0]
    fmt = entry.get("format")
    requests = []
    for url in urls:
        # Multiple files under one row: suffix the title with the actual file
        # extension (not the `format` list's own order, which isn't
        # guaranteed to line up positionally with sample_urls) so the two
        # resulting sources are distinguishable in the registry list.
        title = base_title if len(urls) == 1 else f"{base_title} — {url.rsplit('.', 1)[-1].upper()}"
        requests.append(CreateSourceRequest(
            title=title,
            publisher=entry.get("publisher"),
            connector_type=raw_connector,
            url=url,
            format=fmt if isinstance(fmt, str) else (fmt[0] if fmt else None),
            cron=cron,
            render=raw_connector == "web_crawl_headless",
            variables=entry.get("variables"),
            notes=entry.get("notes"),
        ))
    return requests, None


@router.post("/bulk-import", response_model=ResponseModel)
async def bulk_import_sources_route(
        body: BulkImportRequest,
        request: Request,
        ctx: dict[str, Any] = Depends(require_perm("addSource")),
) -> ResponseModel[Any]:
    """Accepts the ANKT registry's own YAML shape (a top-level `sources:`
    list, or a bare list) as `yaml_text` — paste the file's contents. Rows
    whose connector isn't in SourceConnectorType (manual-upload/bulk-parquet)
    are reported back under `skipped` with a reason; a row bundling several
    files (see _entry_to_create_requests) creates one source per file."""
    try:
        parsed = yaml.safe_load(body.yaml_text)
    except yaml.YAMLError as e:
        raise HTTPException(status_code=422, detail=f"Invalid YAML: {e}")

    entries = parsed.get("sources", parsed) if isinstance(parsed, dict) else parsed
    if not isinstance(entries, list):
        raise HTTPException(status_code=422, detail="Expected a list of sources (optionally under a `sources:` key)")

    created: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    postgres = svc(request, "postgres")
    for entry in entries:
        if not isinstance(entry, dict):
            skipped.append({"title": str(entry), "reason": "not a mapping"})
            continue
        requests, reason = _entry_to_create_requests(entry)
        if not requests:
            skipped.append({"title": entry.get("title") or entry.get("id") or "?", "reason": reason or "unknown"})
            continue
        for req in requests:
            result = await create_source(
                postgres, req.model_dump(), created_by=ctx["user_name"],
                tenant_id=ctx["tenant_id"], role_id=ctx["role_id"],
            )
            created.append(result)

    return ResponseModel(code=200, data={"created": created, "skipped": skipped})


@router.post("/{source_id}/run-now", response_model=ResponseModel)
async def run_source_now_route(
        source_id: str,
        request: Request,
        ctx: dict[str, Any] = Depends(require_perm("runSource")),
) -> ResponseModel[Any]:
    # Validate the source exists (and is this tenant's) before publishing —
    # a 404 here is much easier to act on than a silently-ignored Kafka event.
    await list_source_runs(svc(request, "postgres"), source_id, tenant_id=ctx["tenant_id"])

    kafka_producer = svc(request, "kafka_producer")
    try:
        await kafka_producer.produce(
            topic=MANUAL_TRIGGER_TOPIC,
            value={"event": "run_now", "source_id": source_id, "tenant_id": ctx["tenant_id"]},
        )
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Could not queue run-now: {e}")
    return ResponseModel(code=200, data={"status": "queued", "source_id": source_id})
