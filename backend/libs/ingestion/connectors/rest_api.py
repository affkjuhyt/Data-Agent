"""`rest_api` / `sdmx` connector — GET a URL with optional query params and
optional bearer/query-param auth, store the raw response bytes in Bronze
as-is (JSON/XML, whatever the API returns). Both connector_type values share
this module: neither parses or transforms the response (that's the
not-yet-built Indicator Service warehouse's job), so there is nothing SDMX-
specific to do here beyond what a plain REST GET already covers.

This module never touches OpenBao itself — `libs/ingestion` is shared code
with no business importing a `services.platform_workers` credential
resolver, so the worker resolves `config["openbao_path"]` (if set) BEFORE
calling `fetch()` and passes the plaintext through as `config["_resolved_secret"]`
(see `web_crawler/worker.py`'s `run_source`). A missing/unresolved secret
just means the request goes out unauthenticated, not a crash.
"""
from __future__ import annotations

import httpx

from libs.ingestion import config as cfg
from libs.ingestion.connectors import tls
from libs.ingestion.connectors.base import FetchedArtifact
from libs.ingestion.connectors.extension import infer_extension

_USER_AGENT = "DataAgent-SourceWorker/1.0"


def fetch(config: dict) -> FetchedArtifact:
    url = config["url"]
    params = config.get("query_params") or {}
    headers = {"User-Agent": _USER_AGENT}

    secret = config.get("_resolved_secret")
    if secret:
        auth_style = config.get("auth_style") or "bearer"
        if auth_style == "query_param":
            param_name = config.get("auth_param_name") or "api_key"
            params = {**params, param_name: secret}
        else:
            headers["Authorization"] = f"Bearer {secret}"

    response = tls.get(
        url, follow_redirects=True, timeout=httpx.Timeout(cfg.WEB_FETCH_TIMEOUT), headers=headers, params=params,
    )
    content = response.content

    if len(content) > cfg.MAX_FILE_SIZE_BYTES:
        raise ValueError(f"rest_api fetch of {url} is {len(content)} bytes, over the {cfg.MAX_FILE_SIZE_BYTES}-byte cap")

    content_type = response.headers.get("content-type", "application/octet-stream").split(";")[0]
    extension = infer_extension(content_type, url, prefer_url=False, content=content)
    return FetchedArtifact(
        content_bytes=content,
        content_type=content_type,
        source_url=url,
        extension=extension,
        extra={"status_code": response.status_code},
    )
