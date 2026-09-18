"""libs.ingestion.connectors — http_file / web_crawl / registry.

These are the connectors the new platform_workers `web-crawler` worker (and
kb_backend's Silver stage, via an executor) share — see
libs/ingestion/connectors/__init__.py for why they're sync, not async.
"""
from __future__ import annotations

from typing import Any

import pytest

from libs.ingestion.connectors import http_file, registry, rest_api, rss, web_crawl
from libs.ingestion.connectors.base import FetchedArtifact

pytestmark = pytest.mark.unit


class _FakeResponse:
    def __init__(self, content: bytes, headers: dict[str, str], status_code: int = 200) -> None:
        self.content = content
        self.text = content.decode("utf-8")
        self.headers = headers
        self.status_code = status_code

    def raise_for_status(self) -> None:
        pass


class _FakeClient:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response
        self.last_call: dict[str, Any] = {}

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, *exc: Any) -> None:
        pass

    def get(self, url: str, params: dict[str, str] | None = None) -> _FakeResponse:
        self.last_call = {"url": url, "params": params}
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
        registry.get_connector("not_a_real_connector")


def test_registry_dispatches_rest_api_and_sdmx_to_the_same_connector() -> None:
    assert registry.get_connector("rest_api") is registry.get_connector("sdmx") is rest_api.fetch


def test_rss_fetch_stores_raw_bytes_and_counts_items(monkeypatch: pytest.MonkeyPatch) -> None:
    feed = (
        b'<?xml version="1.0"?><rss version="2.0"><channel>'
        b"<item><title>One</title></item><item><title>Two</title></item>"
        b"</channel></rss>"
    )
    fake = _FakeClient(_FakeResponse(feed, {"content-type": "application/rss+xml"}))
    monkeypatch.setattr(rss.httpx, "Client", lambda **kwargs: fake)

    artifact = rss.fetch({"url": "https://example.com/feed.xml"})

    assert artifact.content_bytes == feed  # raw feed, not per-item parsed content
    assert artifact.extension == "xml"
    # feedparser may not be installed everywhere — either it counted 2, or the
    # metric is None (best-effort), never a crash.
    assert artifact.extra["item_count"] in (2, None)


def test_rss_fetch_rejects_oversized_content(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(_FakeResponse(b"x" * 1000, {"content-type": "application/rss+xml"}))
    monkeypatch.setattr(rss.httpx, "Client", lambda **kwargs: fake)
    monkeypatch.setattr(rss.cfg, "MAX_FILE_SIZE_BYTES", 10)

    with pytest.raises(ValueError, match="over the"):
        rss.fetch({"url": "https://example.com/feed.xml"})


def test_rest_api_fetch_sends_query_params(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(_FakeResponse(b'{"ok": true}', {"content-type": "application/json"}))
    monkeypatch.setattr(rest_api.httpx, "Client", lambda **kwargs: fake)

    artifact = rest_api.fetch({"url": "https://api.example.com/series", "query_params": {"frequency": "M"}})

    assert fake.last_call["params"] == {"frequency": "M"}
    assert artifact.content_bytes == b'{"ok": true}'
    assert artifact.extension == "json"


def test_rest_api_fetch_injects_resolved_secret_as_bearer_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class _CapturingClient(_FakeClient):
        def __init__(self, headers: dict[str, str], **kwargs: Any) -> None:
            super().__init__(_FakeResponse(b"{}", {"content-type": "application/json"}))
            captured["headers"] = headers

    monkeypatch.setattr(rest_api.httpx, "Client", lambda **kwargs: _CapturingClient(**kwargs))

    rest_api.fetch({"url": "https://api.example.com/x", "_resolved_secret": "s3cr3t"})

    assert captured["headers"]["Authorization"] == "Bearer s3cr3t"


def test_rest_api_fetch_injects_resolved_secret_as_query_param_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(_FakeResponse(b"{}", {"content-type": "application/json"}))
    monkeypatch.setattr(rest_api.httpx, "Client", lambda **kwargs: fake)

    rest_api.fetch({
        "url": "https://api.example.com/x", "_resolved_secret": "s3cr3t",
        "auth_style": "query_param", "auth_param_name": "api_key",
    })

    assert fake.last_call["params"] == {"api_key": "s3cr3t"}


def test_web_crawl_recursive_discovers_same_domain_links_only(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("lxml.html")  # transitive dep of trafilatura — not guaranteed in every runner image
    html = (
        '<html><body><p>content</p>'
        '<a href="/page-2">next</a>'
        '<a href="https://example.com/page-3">also same-site</a>'
        '<a href="https://other.example/page">off-site</a>'
        "</body></html>"
    )
    monkeypatch.setattr(web_crawl, "_fetch_html", lambda url, render: html)

    artifact = web_crawl.fetch({"url": "https://example.com/page-1", "recursive": True, "same_domain_only": True})

    discovered = artifact.extra["discovered_links"]
    assert "https://example.com/page-2" in discovered
    assert "https://example.com/page-3" in discovered
    assert "https://other.example/page" not in discovered


def test_web_crawl_non_recursive_does_not_discover_links(monkeypatch: pytest.MonkeyPatch) -> None:
    html = '<html><body><a href="/x">x</a></body></html>'
    monkeypatch.setattr(web_crawl, "_fetch_html", lambda url, render: html)

    artifact = web_crawl.fetch({"url": "https://example.com/page-1"})

    assert "discovered_links" not in artifact.extra
