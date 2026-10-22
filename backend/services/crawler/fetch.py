"""Fetching a source's pages inside its declared scope (BR-149, UC-14-01, UC-14-06).

Every request goes out through the egress proxy (CRAWLER_EGRESS_PROXY, given to the client itself
rather than through HTTPS_PROXY/NO_PROXY, so no crawl request can bypass it), with one User-Agent,
never more than one request per second to a domain whatever the number of sources hitting it
(AC-14-06-05), only to `allowed_domains`, and only where robots.txt allows it. A listing is followed
breadth-first to `max_depth`; nothing is posted, so no login form or captcha is ever submitted.

Internal hosts are refused by name and by address, on the first request and every redirect. Without
a proxy the connection is pinned to the address that was checked, so a name that resolves to a
public address for the check and to a private one for the connection (DNS rebinding) gets nowhere;
through the proxy it is the proxy that resolves and must refuse private ranges. A source's API key
is sent only over https and only to the host it was sent to first, never along a redirect elsewhere.

Some Vietnamese public sites send their leaf certificate without the intermediate CA (hnx.vn). The
legacy connector fetched the intermediate from the certificate's AIA URL over a direct connection,
which the egress-only network refuses; ops add those intermediates to CRAWLER_EXTRA_CA_FILE instead.
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
import ssl
import time
from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import urldefrag, urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import certifi
import httpcore
import httpx
from lxml import html

from services.crawler.extract import html_text

USER_AGENT = "DataAgent-Crawler/1.0 (+https://dataagent.local/crawler)"
MAX_BYTES = int(os.environ.get("CRAWLER_MAX_PAGE_BYTES", str(50 * 1024 * 1024)))
MAX_PAGES = int(os.environ.get("CRAWLER_MAX_PAGES_PER_RUN", "200"))
TIMEOUT = float(os.environ.get("CRAWLER_FETCH_TIMEOUT", "30"))
BLOCKED = frozenset({403, 429})
MAX_REDIRECTS = 5
# Names that resolve inside the cluster or the host. A source's domains never include them (the api
# refuses them too); a public name that resolves to a private address is refused at request time.
INTERNAL_SUFFIXES = ("localhost", ".localhost", ".local", ".internal", ".svc", ".cluster.local")
# Only the tests turn this on, to crawl a server on 127.0.0.1.
ALLOW_PRIVATE = False


@dataclass(frozen=True)
class Fetched:
    url: str
    status: int | None
    content_type: str | None
    content: bytes  # empty when the fetch failed
    error: str | None = None


def in_domains(url: str, domains: Iterable[str]) -> bool:
    """The host is a declared domain or one of its subdomains (the api's rules.in_domains)."""
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    return any(host == d or host.endswith("." + d) for d in domains)


def _public_address(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return ip.is_global and not (ip.is_multicast or ip.is_reserved or ip.is_unspecified)


async def checked_address(url: str) -> str | None:
    """The address to connect to, or None: the host is internal by name, or one of the addresses
    it resolves to is not public. IPv4 first, the address the connection is then pinned to."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        return None
    if not ALLOW_PRIVATE and (host == "localhost" or host.endswith(INTERNAL_SUFFIXES)):
        return None
    try:
        ipaddress.ip_address(host)
        return host if ALLOW_PRIVATE or _public_address(host) else None
    except ValueError:
        pass  # a name, not an address literal
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(
            host, parts.port or (443 if parts.scheme == "https" else 80), type=socket.SOCK_STREAM
        )
    except OSError:
        return None
    addresses = [str(info[4][0]) for info in infos]
    if not addresses or not (ALLOW_PRIVATE or all(_public_address(a) for a in addresses)):
        return None
    v4 = [a for a in addresses if ":" not in a]
    return (v4 or addresses)[0]


async def public(url: str) -> bool:
    return await checked_address(url) is not None


class PinnedBackend(httpcore.AsyncNetworkBackend):
    """Connects a host only to the address it was checked at (DNS rebinding); a host never checked
    is not connected to at all, but the proxy, whose name is the cluster's."""

    def __init__(self, passthrough: frozenset[str] = frozenset()) -> None:
        self._inner = httpcore.AnyIOBackend()
        self._passthrough = passthrough
        self.pins: dict[str, str] = {}

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,  # noqa: ASYNC109 — httpcore's AsyncNetworkBackend signature
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        address = self.pins.get(host.lower())
        if address is None:
            if host.lower() not in self._passthrough:
                raise httpcore.ConnectError(f"{host} was not checked before connecting")
            address = host
        # TLS still verifies the certificate against the name (httpcore passes the origin's host
        # as server_hostname), so pinning changes where we connect, not whom we trust.
        return await self._inner.connect_tcp(
            address,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,  # noqa: ASYNC109 — httpcore's AsyncNetworkBackend signature
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise httpcore.ConnectError("the crawler opens no unix socket")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


class CrawlClient(httpx.AsyncClient):
    def __init__(self, backend: PinnedBackend, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.backend = backend


def ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context(cafile=certifi.where())
    extra = os.environ.get("CRAWLER_EXTRA_CA_FILE")
    if extra and os.path.exists(extra):
        ctx.load_verify_locations(cafile=extra)
    return ctx


class DomainPacer:
    """At most one request per `interval` per domain, across every run in this process.

    One crawler replica is deployed (Recreate); a second replica would need the pace in Redis.
    """

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._last: dict[str, float] = {}

    async def wait(self, url: str, interval: float) -> None:
        host = (urlsplit(url).hostname or "").lower()
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:
            delay = self._last.get(host, 0.0) + interval - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            self._last[host] = time.monotonic()


PACER = DomainPacer()


def keeps_credentials(first: str, url: str) -> bool:
    """A source's API key goes to `url` only if it is https and the same origin (scheme, host and
    port) as the URL it was first sent to: a redirect elsewhere, or down to http, goes without it."""
    a, b = urlsplit(first), urlsplit(url)
    return b.scheme == "https" and (a.scheme, a.hostname, a.port) == (b.scheme, b.hostname, b.port)


class OutOfScope(Exception):
    """A redirect pointed outside the source's domains, or a host resolves to a private address;
    the request is not made (BR-149)."""


class Fetcher:
    def __init__(
        self, client: CrawlClient, *, domains: Iterable[str], rate_limit_rps: float
    ) -> None:
        self._client = client
        self._domains = list(domains)
        # BR-149: the source's own limit, and never above one request per second to a domain.
        self._interval = max(1.0, 1.0 / rate_limit_rps if rate_limit_rps > 0 else 1.0)
        self._robots: dict[str, RobotFileParser | None] = {}

    async def _allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            parser: RobotFileParser | None = RobotFileParser()
            await PACER.wait(origin, self._interval)
            try:
                response, _ = await self._send(f"{origin}/robots.txt", None)
                await response.aread()
                if response.status_code in (401, 403):
                    parser.disallow_all = True  # type: ignore[union-attr]
                elif response.status_code >= 400:
                    parser = None  # no robots.txt: everything is allowed
                else:
                    parser.parse(response.text.splitlines())  # type: ignore[union-attr]
            except (httpx.HTTPError, OutOfScope):
                parser = None
            self._robots[origin] = parser
        robots = self._robots[origin]
        return robots is None or robots.can_fetch(USER_AGENT, url)

    async def _send(self, url: str, headers: dict[str, str] | None) -> tuple[httpx.Response, str]:
        """The final response and its URL; redirects are followed by hand so that each hop is
        checked against the domains and paced like any other request. `headers` (a source's API
        key) go only over https and only to the first URL's host."""
        first = url
        if headers and urlsplit(url).scheme != "https":
            raise OutOfScope(f"{url}: a source's credentials are sent over https only")
        for _ in range(MAX_REDIRECTS + 1):
            address = await checked_address(url)
            if address is None:
                raise OutOfScope(f"{url} is not a public address")
            parts = urlsplit(url)
            self._client.backend.pins[(parts.hostname or "").lower()] = address
            keep = headers if headers and keeps_credentials(first, url) else None
            request = self._client.build_request("GET", url, headers=keep)
            response = await self._client.send(request, stream=True)
            location = response.headers.get("location")
            if not response.is_redirect or not location:
                return response, url
            await response.aclose()
            url = urljoin(url, location)
            if not in_domains(url, self._domains):
                raise OutOfScope(url)
            await PACER.wait(url, self._interval)
        raise httpx.TooManyRedirects(f"more than {MAX_REDIRECTS} redirects", request=request)

    async def get(self, url: str, headers: dict[str, str] | None = None) -> Fetched | None:
        """None when robots.txt forbids the page: no request is made at all."""
        if not await self._allowed(url):
            return None
        await PACER.wait(url, self._interval)
        try:
            response, final = await self._send(url, headers)
            try:
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_BYTES:
                        return Fetched(final, response.status_code, None, b"", "over the size cap")
            finally:
                await response.aclose()
            kind = response.headers.get("content-type", "application/octet-stream")
            if response.status_code >= 400:
                return Fetched(
                    final, response.status_code, kind, b"", f"HTTP {response.status_code}"
                )
            return Fetched(final, response.status_code, kind, bytes(body))
        except OutOfScope as exc:
            return Fetched(url, None, None, b"", f"out of scope: {exc}")
        except httpx.HTTPError as exc:
            return Fetched(url, None, None, b"", type(exc).__name__)


def links(page: Fetched, domains: Iterable[str]) -> list[str]:
    """In-scope links of an HTML page, without fragments, in page order."""
    if not page.content or "html" not in (page.content_type or ""):
        return []
    try:
        doc = html.fromstring(html_text(page.content, page.content_type or ""), base_url=page.url)
    except ValueError:
        return []
    seen: dict[str, None] = {}
    for href in doc.xpath("//a/@href"):
        url = urldefrag(urljoin(page.url, str(href)))[0]
        if urlsplit(url).scheme in ("http", "https") and in_domains(url, domains):
            seen.setdefault(url, None)
    return list(seen)


async def crawl(
    fetcher: Fetcher,
    seeds: list[str],
    domains: list[str],
    *,
    follow: bool,
    max_depth: int,
    headers: dict[str, str] | None = None,
) -> tuple[list[Fetched], int]:
    """(pages fetched, requests refused by robots.txt). Breadth-first; one visit per URL."""
    queue = [(url, 0) for url in seeds if in_domains(url, domains)]
    seen = {url for url, _ in queue}
    pages: list[Fetched] = []
    refused = 0
    while queue and len(pages) < MAX_PAGES:
        url, depth = queue.pop(0)
        page = await fetcher.get(url, headers)
        if page is None:
            refused += 1
            continue
        pages.append(page)
        if follow and depth < max_depth:
            for link in links(page, domains):
                if link not in seen:
                    seen.add(link)
                    queue.append((link, depth + 1))
    return pages, refused


def client() -> CrawlClient:
    # The proxy is passed explicitly and the environment ignored: a NO_PROXY entry meant for MinIO
    # or OpenBao must never let a crawl request out without the proxy.
    proxy = os.environ.get("CRAWLER_EGRESS_PROXY") or None
    ctx = ssl_context()
    backend = PinnedBackend(
        frozenset({(urlsplit(proxy).hostname or "").lower()}) if proxy else frozenset()
    )
    pool: httpcore.AsyncConnectionPool = (
        httpcore.AsyncHTTPProxy(proxy_url=proxy, ssl_context=ctx, network_backend=backend)
        if proxy
        else httpcore.AsyncConnectionPool(ssl_context=ctx, network_backend=backend)
    )
    transport = httpx.AsyncHTTPTransport(verify=ctx)
    # httpx 0.27 takes no network backend; its transport is a thin wrapper around this same pool,
    # so the pool is swapped for one that pins (tests/unit/crawler/test_fetch.py checks it).
    transport._pool = pool
    return CrawlClient(
        backend,
        transport=transport,
        headers={"User-Agent": USER_AGENT, "Accept-Language": "vi,en;q=0.8"},
        timeout=httpx.Timeout(TIMEOUT),
        follow_redirects=False,
        trust_env=False,
    )
