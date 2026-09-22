"""/api/knowledge/sources — the scheduled source registry (http_file /
web_crawl / web_crawl_headless / rss / rest_api / sdmx). The `web-crawler`
platform worker is a second reader of "KBSource"/"KBSourceConfig" (it decides
what's due off "KBSourceRun"); this router owns writes and the manual
"Run Now" trigger.
"""
from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import yaml
from fastapi import APIRouter, Depends, HTTPException, Request

from libs.basemodel.services_databaseconnector.shared_model import ResponseModel
from libs.basemodel.services_databaseconnector.postgres_model import SourceConnectorType
from services.kb_backend.dependencies.services import svc
from services.kb_backend.dependencies.auth import require_perm
from services.kb_backend.schemas import CreateSourceRequest, UpdateSourceConfigRequest, BulkImportRequest
from services.kb_backend.operations.source_ops import (
    list_sources, create_source, update_source_config, delete_source, list_source_runs,
    queue_run_now, list_recent_activity,
)

_SUPPORTED_CONNECTORS = {c.value for c in SourceConnectorType}
log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/knowledge/sources", tags=["Knowledge-Sources"])

@router.get("", response_model=ResponseModel)
async def list_sources_route(
        request: Request,
        ctx: dict[str, Any] = Depends(require_perm("viewSources")),
        cursor: str | None = None,
) -> ResponseModel[Any]:
    result = await list_sources(svc(request, "postgres"), tenant_id=ctx["tenant_id"], cursor=cursor)
    return ResponseModel(code=200, data=result)


@router.get("/activity", response_model=ResponseModel)
async def list_activity_route(
        request: Request,
        ctx: dict[str, Any] = Depends(require_perm("viewSources")),
) -> ResponseModel[Any]:
    result = await list_recent_activity(svc(request, "postgres"), tenant_id=ctx["tenant_id"])
    return ResponseModel(code=200, data=result)


async def _auto_run(postgres: Any, source_id: str, tenant_id: str) -> None:
    """Adding a source means "I want this in Bronze" — there is no reason to
    make the steward click Run Now right after, whether or not it has a
    schedule (a cron's "next run" is simply computed forward from this first
    run's started_at, so nothing else needs to know a run was "the first
    one"). Best-effort: a queueing hiccup must not fail source creation
    itself, and Run Now stays available on the source afterward regardless."""
    try:
        await queue_run_now(postgres, source_id, tenant_id=tenant_id)
    except HTTPException:
        pass  # already queued/running somehow — nothing to do
    except Exception:  # noqa: BLE001 — creation already succeeded, don't fail the request over this
        log.warning("auto-run: failed to queue the first run for source_id=%s", source_id, exc_info=True)


@router.post("", response_model=ResponseModel)
async def create_source_route(
        body: CreateSourceRequest,
        request: Request,
        ctx: dict[str, Any] = Depends(require_perm("addSource")),
) -> ResponseModel[Any]:
    postgres = svc(request, "postgres")
    result = await create_source(
        postgres, body.model_dump(), created_by=ctx["user_name"],
        tenant_id=ctx["tenant_id"], role_id=ctx["role_id"],
    )
    await _auto_run(postgres, result["id"], ctx["tenant_id"])
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
    # registry uses to a 5-field cron. A phrase that is not recognised gives
    # NO schedule (manual only) rather than a guessed one: a wrong cadence
    # silently re-fetches on a timer nobody asked for.
    "hourly": "0 * * * *",
    "daily": "0 3 * * *",
    "semi-monthly": "0 3 1,15 * *",
    "weekly": "0 3 * * 1",
    "monthly": "0 3 3 * *",
    "quarterly": "0 3 1 1,4,7,10 *",
    "semi-annual": "0 3 1 1,7 *",
    "yearly": "0 3 1 1 *",
}
_EVERY_MINUTES = re.compile(r"^every\s+(\d+)\s*(?:min|mins|minute|minutes)\b")
_EVERY_HOURS = re.compile(r"^every\s+(\d+)\s*(?:h|hr|hrs|hour|hours)\b")
_ENV_PLACEHOLDER = re.compile(r"^\$\{[A-Za-z0-9_]+\}$")


def _schedule_to_cron(text: Any) -> str:
    """"" (manual only) when the row has no schedule or one we cannot read."""
    phrase = str(text or "").strip().lower()
    if not phrase:
        return ""
    minutes = _EVERY_MINUTES.match(phrase)
    if minutes and 1 <= int(minutes.group(1)) <= 59:
        return f"*/{int(minutes.group(1))} * * * *"
    hours = _EVERY_HOURS.match(phrase)
    if hours and 1 <= int(hours.group(1)) <= 23:
        return f"0 */{int(hours.group(1))} * * *"
    return _SCHEDULE_WORD_TO_CRON.get(phrase.split(" ")[0], "")


def _as_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value else []
    return [v for v in (value or []) if isinstance(v, str) and v]


def _pick_urls(connector: str, entry: dict) -> tuple[list[str], str | None]:
    """The URL(s) to fetch for one registry row, or ([], reason).

    Each connector type keeps its fetchable address in a different field, and
    `landing_url` is usually the page a person browses, not the resource:
    - http_file: `sample_urls` (the files themselves; one source per file);
    - web_crawl*: `landing_url` (crawling the page is the point);
    - rss: `api_base` (the feed URL);
    - rest_api / sdmx: `examples` / `example` (a complete request), else
      `api_base` (the endpoint)."""
    landing = entry.get("landing_url")
    if connector == "http_file":
        files = _as_list(entry.get("sample_urls"))
        if files:
            return files, None
        if landing:
            return [], "only a landing page, not the file (add the direct file URL as sample_urls)"
        return [], "no sample_urls (direct file URL) to fetch"
    if connector in ("web_crawl", "web_crawl_headless"):
        urls = [landing] if landing else _as_list(entry.get("sample_urls"))[:1] or _as_list(entry.get("api_base"))
        return urls, None if urls else "no landing_url to crawl"
    if connector == "rss":
        urls = _as_list(entry.get("api_base")) or _as_list(entry.get("example"))
        return urls[:1], None if urls else "no api_base (feed URL) to fetch"
    urls = _as_list(entry.get("examples")) + _as_list(entry.get("example")) or _as_list(entry.get("api_base"))
    return urls, None if urls else "no example or api_base URL to fetch"


def _strip_env_placeholder(url: str) -> tuple[str, str | None]:
    """`...&api_key=${FRED_API_KEY}` -> (url without that parameter, "api_key").
    The placeholder is not a value; the parameter is sent at run time as the
    source's credential, not stored in the URL."""
    parts = urlsplit(url)
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    kept = [(k, v) for k, v in pairs if not _ENV_PLACEHOLDER.match(v)]
    if len(kept) == len(pairs):
        return url, None
    dropped = next(k for k, v in pairs if _ENV_PLACEHOLDER.match(v))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(kept), parts.fragment)), dropped


_FILE_EXTENSIONS = frozenset({"pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "csv", "json", "xml", "txt", "zip"})


def _url_label(url: str, index: int) -> str:
    """Short suffix that tells the sources of one row apart: the file extension
    for a download, else the dataset segment after /data/ (BIS: WS_XRU). An API
    path ends in a dotted key (".../M.VN.VND.A"), so only known file
    extensions count as one."""
    segments = [part for part in urlsplit(url).path.split("/") if part]
    last = segments[-1] if segments else ""
    extension = last.rsplit(".", 1)[-1].lower() if "." in last else ""
    if extension in _FILE_EXTENSIONS:
        return extension.upper()
    if "data" in segments and segments.index("data") + 1 < len(segments):
        return segments[segments.index("data") + 1]
    return str(index + 1)


def _entry_to_create_requests(entry: dict) -> tuple[list[CreateSourceRequest], str | None]:
    """One ANKT registry row -> one or more CreateSourceRequests, or ([], reason)
    if the row cannot become a scheduled source (manual-upload / bulk-parquet
    are one-off imports; a row without a connector has nothing to fetch; the
    address may be missing).

    More than one request comes out when a single row really bundles several
    things to fetch — an NSO report's DOCX plus XLSX, or a dataset with several
    example queries. KBSourceConfig has exactly one `url`, so each becomes its
    own source (as Document Hub treats each upload as its own document)."""
    raw_connector = str(entry.get("connector") or "").strip().replace("-", "_")
    if not raw_connector:
        return [], "no connector declared (nothing to fetch)"
    if raw_connector not in _SUPPORTED_CONNECTORS:
        return [], f"connector={entry.get('connector')!r} is not ingestible by the V1 worker yet"

    urls, reason = _pick_urls(raw_connector, entry)
    if not urls:
        return [], reason or "no URL to fetch"

    cron = _schedule_to_cron(entry.get("schedule"))
    base_title = entry.get("title") or entry.get("id") or urls[0]
    fmt = entry.get("format")
    requests = []
    for index, raw_url in enumerate(urls):
        url, credential_param = _strip_env_placeholder(raw_url)
        notes = entry.get("notes")
        if credential_param:
            hint = f"API key expected as the '{credential_param}' parameter; set its OpenBao path."
            notes = f"{notes} {hint}" if notes else hint
        requests.append(CreateSourceRequest(
            title=base_title if len(urls) == 1 else f"{base_title} — {_url_label(url, index)}",
            publisher=entry.get("publisher"),
            connector_type=raw_connector,
            url=url,
            format=fmt if isinstance(fmt, str) else (fmt[0] if fmt else None),
            cron=cron,
            render=raw_connector == "web_crawl_headless",
            variables=entry.get("variables"),
            notes=notes,
            auth_style="query_param" if credential_param else None,
            auth_param_name=credential_param,
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
    files (see _entry_to_create_requests) creates one source per file.

    Every created row is auto-run immediately, same as a single interactive
    add (see _auto_run) — the frontend's confirm step (reviewing/editing the
    parsed rows before calling this endpoint) is what stands in for the old
    "create now, batch-run later" gate: the storm risk that gate protected
    against is a review the steward has, by this point, already done once
    for the whole batch, not per row afterward."""
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
            try:
                result = await create_source(
                    postgres, req.model_dump(), created_by=ctx["user_name"],
                    tenant_id=ctx["tenant_id"], role_id=ctx["role_id"],
                )
            except HTTPException as exc:
                if exc.status_code != 409:
                    raise
                skipped.append({"title": req.title, "reason": "already registered"})
                continue
            await _auto_run(postgres, result["id"], ctx["tenant_id"])
            created.append(result)

    return ResponseModel(code=200, data={"created": created, "skipped": skipped})


@router.post("/{source_id}/run-now", response_model=ResponseModel)
async def run_source_now_route(
        source_id: str,
        request: Request,
        ctx: dict[str, Any] = Depends(require_perm("runSource")),
) -> ResponseModel[Any]:
    result = await queue_run_now(svc(request, "postgres"), source_id, tenant_id=ctx["tenant_id"])
    return ResponseModel(code=200, data=result)
