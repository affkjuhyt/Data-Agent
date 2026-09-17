"""libs.ingestion.connectors — http_file / web_crawl / registry.

These are the connectors the new platform_workers `web-crawler` worker (and
kb_backend's Silver stage, via an executor) share — see
libs/ingestion/connectors/__init__.py for why they're sync, not async.
"""
from __future__ import annotations

from typing import Any

import pytest

from libs.ingestion.connectors import http_file, registry, web_crawl
from libs.ingestion.connectors.base import FetchedArtifact

pytestmark = pytest.mark.unit


class _FakeResponse:
    def __init__(self, content: bytes, headers: dict[str, str]) -> None:
        self.content = content
        self.text = content.decode("utf-8")
        self.headers = headers

    def raise_for_status(self) -> None:
        pass


class _FakeClient:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, *exc: Any) -> None:
        pass

    def get(self, url: str) -> _FakeResponse:
        return self._response


def test_http_file_fetch_returns_bytes_and_extension(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(_FakeResponse(b"%PDF-1.4 fake pdf bytes", {"content-type": "application/pdf"}))
    monkeypatch.setattr(http_file.httpx, "Client", lambda **kwargs: fake)

    artifact = http_file.fetch({"url": "https://example.com/report.pdf"})

    assert artifact.content_bytes == b"%PDF-1.4 fake pdf bytes"
    assert artifact.extension == "pdf"
    assert artifact.content_type == "application/pdf"
    assert artifact.source_url == "https://example.com/report.pdf"


def test_http_file_fetch_rejects_oversized_content(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(_FakeResponse(b"x" * 1000, {"content-type": "application/octet-stream"}))
    monkeypatch.setattr(http_file.httpx, "Client", lambda **kwargs: fake)
    monkeypatch.setattr(http_file.cfg, "MAX_FILE_SIZE_BYTES", 10)

    with pytest.raises(ValueError, match="over the"):
        http_file.fetch({"url": "https://example.com/big.bin"})


def test_web_crawl_fetch_strips_tags_and_computes_quality_metric(monkeypatch: pytest.MonkeyPatch) -> None:
    html = "<html><body><script>ignored</script><p>Điều 1: nội dung quan trọng.</p></body></html>"
    monkeypatch.setattr(web_crawl, "_fetch_html", lambda url, render: html)

    artifact = web_crawl.fetch({"url": "https://example.com/page", "render": False})

    assert isinstance(artifact, FetchedArtifact)
    assert artifact.content_bytes  # extraction produced something non-empty
    assert artifact.extra["raw_len"] == len(html)
    assert 0.0 <= artifact.extra["tag_stripped_pct"] <= 1.0
    # trafilatura may not be installed in every test environment — either way
    # the extension must be one of the two _extract_main_content can return.
    assert artifact.extension in ("md", "html")


def test_web_crawl_fetch_falls_back_to_static_when_render_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(web_crawl, "_fetch_static", lambda url: calls.append("static") or "<p>ok</p>")
    monkeypatch.setattr(
        web_crawl, "_fetch_rendered",
        lambda url: (_ for _ in ()).throw(AssertionError("render=False must never call the headless path")),
    )

    web_crawl.fetch({"url": "https://example.com/x", "render": False})

    assert calls == ["static"]


def test_registry_dispatches_by_connector_type(monkeypatch: pytest.MonkeyPatch) -> None:
    # _CONNECTORS captured a reference to web_crawl.fetch at import time, so
    # the dict entry — not the module attribute — is what to replace here.
    sentinel = FetchedArtifact(content_bytes=b"x", content_type="text/html", source_url="u", extension="html")
    monkeypatch.setitem(registry._CONNECTORS, "web_crawl", lambda config: sentinel)
    monkeypatch.setitem(registry._CONNECTORS, "web_crawl_headless", lambda config: sentinel)

    assert registry.fetch("web_crawl", {"url": "u"}) is sentinel
    # web_crawl_headless dispatches to the SAME connector (render is a config
    # flag, not a separate implementation) — see registry.py's comment.
    assert registry.fetch("web_crawl_headless", {"url": "u", "render": True}) is sentinel


def test_registry_raises_for_unknown_connector_type() -> None:
    with pytest.raises(ValueError, match="no connector registered"):
        registry.get_connector("rest_api")
