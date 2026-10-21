"""The crawler's scope (BR-149) against a local site: robots.txt, redirects out of the declared
domains, a listing followed to its depth, and one request per second per domain."""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from itertools import pairwise
from typing import Any

import httpx
import pytest

from services.crawler import fetch

pytestmark = [pytest.mark.unit]

SITE: dict[str, tuple[int, dict[str, str], bytes]] = {
    "/robots.txt": (200, {"content-type": "text/plain"}, b"User-agent: *\nDisallow: /private\n"),
    "/list": (
        200,
        {"content-type": "text/html"},
        b'<a href="/a">a</a><a href="/b#x">b</a><a href="/private/p">p</a>'
        b'<a href="http://elsewhere.test/x">out</a>',
    ),
    "/a": (200, {"content-type": "text/html"}, b'<a href="/deep">deep</a>'),
    "/b": (200, {"content-type": "text/html"}, b"<p>b</p>"),
    "/deep": (200, {"content-type": "text/html"}, b"<p>deep</p>"),
    "/away": (302, {"location": "http://elsewhere.test/landing"}, b""),
    "/busy": (429, {"content-type": "text/plain"}, b"slow down"),
}


class Site:
    def __init__(self) -> None:
        self.hits: list[tuple[float, str]] = []
        site = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                site.hits.append((time.monotonic(), self.path))
                status, headers, body = SITE.get(self.path, (404, {}, b""))
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: Any) -> None:
                pass

        self.http = HTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://localhost:{self.http.server_port}"


@pytest.fixture
def site(monkeypatch: pytest.MonkeyPatch) -> Iterator[Site]:
    # The test site is on 127.0.0.1, which the crawler otherwise refuses.
    monkeypatch.setattr(fetch, "ALLOW_PRIVATE", True)
    s = Site()
    threading.Thread(target=s.http.serve_forever, daemon=True).start()
    yield s
    s.http.shutdown()


@pytest.fixture
def fast(monkeypatch: pytest.MonkeyPatch) -> None:
    # The pace itself is tested once below; the other tests should not wait a second per page.
    monkeypatch.setattr(fetch, "PACER", fetch.DomainPacer())
    monkeypatch.setattr(fetch.Fetcher, "__init__", _fast_init)


_real_init = fetch.Fetcher.__init__


def _fast_init(self: fetch.Fetcher, client: Any, *, domains: Any, rate_limit_rps: float) -> None:
    _real_init(self, client, domains=domains, rate_limit_rps=rate_limit_rps)
    self._interval = 0.0


async def test_a_listing_is_followed_in_scope_to_its_depth(site: Site, fast: None) -> None:
    async with fetch.client() as http:
        fetcher = fetch.Fetcher(http, domains=["localhost"], rate_limit_rps=1)
        pages, refused = await fetch.crawl(
            fetcher, [f"{site.base}/list"], ["localhost"], follow=True, max_depth=1
        )
    assert [p.url.removeprefix(site.base) for p in pages] == ["/list", "/a", "/b"]
    # robots.txt forbids /private: it is counted, never requested; the other domain is never asked.
    assert refused == 1
    assert all(not path.startswith("/private") for _, path in site.hits)


async def test_a_redirect_out_of_the_domains_is_not_followed(site: Site, fast: None) -> None:
    async with fetch.client() as http:
        page = await fetch.Fetcher(http, domains=["localhost"], rate_limit_rps=1).get(
            f"{site.base}/away"
        )
    assert page is not None and page.content == b""
    assert page.error is not None and "out of scope" in page.error


async def test_a_refusal_is_kept_with_its_status(site: Site, fast: None) -> None:
    async with fetch.client() as http:
        page = await fetch.Fetcher(http, domains=["localhost"], rate_limit_rps=1).get(
            f"{site.base}/busy"
        )
    assert page is not None and page.status == 429 and page.status in fetch.BLOCKED


@pytest.mark.req("AC-14-06-05")
async def test_one_request_per_second_per_domain_whatever_the_source_asks(site: Site) -> None:
    async with fetch.client() as http:
        greedy = fetch.Fetcher(http, domains=["localhost"], rate_limit_rps=10)
        for path in ("/a", "/b"):
            await greedy.get(f"{site.base}{path}")
    times = [t for t, _ in site.hits]  # robots.txt, /a, /b
    assert len(times) == 3
    assert all(later - earlier >= 0.95 for earlier, later in pairwise(times))


def test_domains_cover_their_subdomains_only() -> None:
    assert fetch.in_domains("https://www.bank.example.vn/x", ["bank.example.vn"])
    assert not fetch.in_domains("https://evilbank.example.vn/x", ["bank.example.vn"])


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/x",
        "http://127.0.0.1/x",
        "http://10.43.0.1/x",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]/x",
        "http://minio.dataagent-database.svc.cluster.local:8360/da-raw-docs",
        "http://openbao.internal/v1",
    ],
)
async def test_internal_hosts_are_never_requested(url: str) -> None:
    assert not await fetch.public(url)


async def test_a_public_name_resolving_to_a_private_address_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def resolve(self: Any, host: str, port: int, **_: Any) -> list[Any]:
        address = "10.0.0.7" if host == "rebind.example.vn" else "93.184.215.14"
        return [(2, 1, 6, "", (address, port))]

    monkeypatch.setattr(type(asyncio.get_running_loop()), "getaddrinfo", resolve)
    assert not await fetch.public("https://rebind.example.vn/lai-suat")
    assert await fetch.public("https://bank.example.vn/lai-suat")


async def test_crawl_traffic_takes_the_proxy_whatever_no_proxy_says(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CRAWLER_EGRESS_PROXY", "http://egress-proxy.test:3128")
    monkeypatch.setenv("NO_PROXY", "*")
    async with fetch.client() as http:
        transport = http._transport_for_url(httpx.URL("https://bank.example.vn/"))
    assert transport is not http._transport  # the proxy's transport, not the direct one


@pytest.mark.parametrize(
    "ref",
    [
        "tenants/{org}/sources/dw",
        "tenants/{other}/crawl-sources/k",
        "tenants/{org}/crawl-sources/../sources/dw",
        "",
    ],
)
async def test_the_reader_reads_only_crawl_source_keys_of_the_organisation(ref: str) -> None:
    from services.crawler.secrets import Reader, SecretUnavailable

    org = uuid.uuid4()
    with pytest.raises(SecretUnavailable):
        await Reader().api_key(org, ref.format(org=org, other=uuid.uuid4()))
