"""Two sources with one title would share (and overwrite) one Bronze document.

Found when a second "TEST ADB Outlook 2026" source, created as a Web Page,
replaced the Remote File source's PDF document: the document name is the title
and is unique per tenant.
"""
from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from services.kb_backend.operations import source_ops
from services.kb_backend.router.knowledge import sources as sources_router
from services.kb_backend.schemas import BulkImportRequest, CreateSourceRequest

pytestmark = pytest.mark.unit


class _Session:
    def __init__(self, existing: Any) -> None:
        self._existing = existing

    async def scalar(self, *_: Any, **__: Any) -> Any:
        return self._existing


class _Postgres:
    def __init__(self, existing: Any) -> None:
        self.session = _Session(existing)
        self.inserted = False

    @asynccontextmanager
    async def get_client(self) -> Any:
        yield self.session

    async def insert(self, *_: Any, **__: Any) -> Any:
        self.inserted = True
        raise AssertionError("must not insert a duplicate")


def test_create_source_refuses_a_title_that_is_already_registered() -> None:
    postgres = _Postgres(existing=uuid.uuid4())
    body = {"title": "TEST ADB Outlook 2026", "connector_type": "web_crawl", "url": "https://x"}
    with pytest.raises(HTTPException) as caught:
        asyncio.run(source_ops.create_source(postgres, body, "u", str(uuid.uuid4()), "r"))
    assert caught.value.status_code == 409 and "already exists" in caught.value.detail
    assert postgres.inserted is False


class _InsertingPostgres:
    """Lets create_source() run past the duplicate-title check to the actual
    inserts, so the `promotion_mode` default can be read back off the config
    payload it built — without a real DB."""

    def __init__(self) -> None:
        self.session = _Session(existing=None)
        self.inserted_configs: list[Any] = []

    @asynccontextmanager
    async def get_client(self) -> Any:
        yield self.session

    async def insert(self, payload: Any) -> Any:
        if isinstance(payload, source_ops.KBSourceInsert):
            return type("R", (), {"code": 200, "data": {"source_id": str(uuid.uuid4())}})()
        self.inserted_configs.append(payload.config)
        return type("R", (), {"code": 200, "data": {}})()


def _create(body: dict[str, Any]) -> Any:
    postgres = _InsertingPostgres()
    fake_row = {
        "source_id": "s", "title": body["title"], "publisher": None,
        "connector_type": body["connector_type"], "created_by": "u", "created_at": None,
    }
    with patch.object(source_ops, "_source_row", return_value=fake_row):
        asyncio.run(source_ops.create_source(postgres, body, "u", str(uuid.uuid4()), "r"))
    return postgres.inserted_configs[0]


def test_a_scheduled_source_defaults_to_auto_promotion() -> None:
    config = _create({"title": "Scheduled", "connector_type": "web_crawl", "url": "https://x", "cron": "0 3 * * *"})
    assert config.promotion_mode == "auto"


def test_a_manual_only_source_defaults_to_manual_promotion() -> None:
    config = _create({"title": "Manual", "connector_type": "web_crawl", "url": "https://x"})
    assert config.promotion_mode == "manual"


def test_an_explicit_promotion_mode_overrides_the_cron_based_default() -> None:
    config = _create({
        "title": "Scheduled but manual", "connector_type": "web_crawl", "url": "https://x",
        "cron": "0 3 * * *", "promotion_mode": "manual",
    })
    assert config.promotion_mode == "manual"


def test_create_source_route_auto_runs_regardless_of_schedule() -> None:
    """Adding a source means "put this in Bronze" — no separate Run Now click
    is required afterward, whether or not the source has a cron."""
    async def _create(_pg: Any, body: dict[str, Any], **_: Any) -> dict[str, Any]:
        return {"id": "new-source", "title": body["title"]}

    queued: list[tuple[str, str]] = []

    async def _queue(_pg: Any, source_id: str, tenant_id: str) -> dict[str, Any]:
        queued.append((source_id, tenant_id))
        return {"run_id": "r1", "status": "queued", "source_id": source_id}

    async def _run(cron: str) -> Any:
        with (
            patch.object(sources_router, "create_source", _create),
            patch.object(sources_router, "queue_run_now", _queue),
            patch.object(sources_router, "svc", lambda *_: object()),
        ):
            body = CreateSourceRequest(title="X", connector_type="http_file", url="https://x/a.pdf", cron=cron)
            return await sources_router.create_source_route(body, object(), {"user_name": "u", "tenant_id": "t", "role_id": "r"})

    asyncio.run(_run(""))
    asyncio.run(_run("0 3 * * *"))
    assert queued == [("new-source", "t"), ("new-source", "t")]


def test_create_source_route_auto_run_failure_does_not_fail_the_create() -> None:
    async def _create(_pg: Any, body: dict[str, Any], **_: Any) -> dict[str, Any]:
        return {"id": "new-source", "title": body["title"]}

    async def _queue(*_: Any, **__: Any) -> dict[str, Any]:
        raise RuntimeError("kafka/db hiccup")

    async def _run() -> Any:
        with (
            patch.object(sources_router, "create_source", _create),
            patch.object(sources_router, "queue_run_now", _queue),
            patch.object(sources_router, "svc", lambda *_: object()),
        ):
            body = CreateSourceRequest(title="X", connector_type="http_file", url="https://x/a.pdf", cron="")
            return await sources_router.create_source_route(body, object(), {"user_name": "u", "tenant_id": "t", "role_id": "r"})

    result = asyncio.run(_run())  # must not raise
    assert result.data["id"] == "new-source"


def test_bulk_import_auto_runs_every_created_row() -> None:
    yaml_text = """
sources:
  - {id: a, title: First, connector: http-file, sample_urls: [https://x/a.pdf]}
"""

    async def _create(_pg: Any, body: dict[str, Any], **_: Any) -> dict[str, Any]:
        return {"id": "new", "title": body["title"]}

    queued: list[str] = []

    async def _queue(_pg: Any, source_id: str, tenant_id: str) -> dict[str, Any]:
        queued.append(source_id)
        return {"run_id": "r1", "status": "queued", "source_id": source_id}

    async def _run() -> Any:
        with (
            patch.object(sources_router, "create_source", _create),
            patch.object(sources_router, "queue_run_now", _queue),
            patch.object(sources_router, "svc", lambda *_: object()),
        ):
            return await sources_router.bulk_import_sources_route(
                BulkImportRequest(yaml_text=yaml_text), object(),
                {"user_name": "u", "tenant_id": "t", "role_id": "r"},
            )

    response = asyncio.run(_run())
    assert [c["title"] for c in response.data["created"]] == ["First"]
    assert queued == ["new"]


def test_bulk_import_skips_a_duplicate_instead_of_aborting_the_whole_import() -> None:
    yaml_text = """
sources:
  - {id: a, title: First,  connector: http-file, sample_urls: [https://x/a.pdf]}
  - {id: b, title: Second, connector: http-file, sample_urls: [https://x/b.pdf]}
"""

    async def _create(_pg: Any, body: dict[str, Any], **_: Any) -> dict[str, Any]:
        if body["title"] == "First":
            raise HTTPException(status_code=409, detail="exists")
        return {"id": "new", "title": body["title"]}

    async def _run() -> Any:
        with patch.object(sources_router, "create_source", _create), patch.object(sources_router, "svc", lambda *_: object()):
            return await sources_router.bulk_import_sources_route(
                BulkImportRequest(yaml_text=yaml_text), object(),
                {"user_name": "u", "tenant_id": "t", "role_id": "r"},
            )

    response = asyncio.run(_run())
    assert [c["title"] for c in response.data["created"]] == ["Second"]
    assert response.data["skipped"] == [{"title": "First", "reason": "already registered"}]
