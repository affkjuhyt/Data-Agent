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
from services.kb_backend.schemas import BulkImportRequest

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
