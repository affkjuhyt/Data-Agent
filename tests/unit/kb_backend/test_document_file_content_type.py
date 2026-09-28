"""GET /api/knowledge/documents/{id}/file — content type and filename.

Scheduled-source documents are named after the source title (no extension);
the type must come from the KBData `extension` column, or a PDF is served as
application/octet-stream (blank preview, download without ".pdf").
"""
from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import unquote

import pytest

from services.kb_backend.router.knowledge import documents as docs

pytestmark = pytest.mark.unit


class _Minio:
    async def download(self, *_: Any) -> bytes:
        return b"%PDF-1.7 fake"


def _serve(monkeypatch: pytest.MonkeyPatch, record: dict[str, Any]):
    async def _fetch(*_: Any, **__: Any) -> dict[str, Any]:
        return record

    monkeypatch.setattr(docs, "fetch_one_or_404", _fetch)
    monkeypatch.setattr(docs, "svc", lambda _req, name: _Minio() if name == "minio" else object())
    return asyncio.run(docs.get_document_file_route("d1", object(), {"tenant_id": "t"}))


def test_extensionless_name_uses_extension_column(monkeypatch: pytest.MonkeyPatch) -> None:
    resp = _serve(monkeypatch, {"name": "Bản tin nợ công (LIVE TEST)", "extension": "pdf", "path": "p"})
    assert resp.media_type == "application/pdf"
    disposition = resp.headers["content-disposition"]
    assert unquote(disposition.split("filename*=UTF-8''")[1]) == "Bản tin nợ công (LIVE TEST).pdf"


def test_name_already_carrying_extension_is_not_doubled(monkeypatch: pytest.MonkeyPatch) -> None:
    resp = _serve(monkeypatch, {"name": "report.pdf", "extension": "pdf", "path": "p"})
    assert resp.media_type == "application/pdf"
    assert 'filename="report.pdf"' in resp.headers["content-disposition"]


def test_docx_download_gets_its_extension(monkeypatch: pytest.MonkeyPatch) -> None:
    # The MIME type itself depends on the system mime.types (absent in the
    # slim image); the suffix on the saved file is what matters here.
    resp = _serve(monkeypatch, {"name": "Báo cáo", "extension": "docx", "path": "p"})
    assert unquote(resp.headers["content-disposition"].split("filename*=UTF-8''")[1]) == "Báo cáo.docx"


def test_missing_extension_keeps_previous_behaviour(monkeypatch: pytest.MonkeyPatch) -> None:
    resp = _serve(monkeypatch, {"name": "notes", "extension": None, "path": "p"})
    assert resp.media_type == "application/octet-stream"
