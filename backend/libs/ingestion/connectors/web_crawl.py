"""`web_crawl` / `web_crawl_headless` connector — fetch one URL's HTML and
strip it down to the main content, exactly like the manual Web Portal
ingest path (`register_web_bronze`) already does.

This is the single place Playwright/trafilatura fetch logic lives. It used
to be duplicated inside `libs/ingestion/silver/parsing.py` (async, for the
kb_promote_silver service's own event loop) — that module now delegates
here via `loop.run_in_executor(...)` instead of keeping its own copy, so a
behavior change (timeout, render-wait, extraction library) only has one
place to land, whether the caller is a human's one-shot upload or the
scheduled `web-crawler` worker.

Synchronous by design (`httpx.Client` + `playwright.sync_api`): the primary
caller, the platform_workers `web-crawler` worker, runs a plain sync loop
like every other platform_workers worker — see `connectors/__init__.py`.
"""
from __future__ import annotations

from urllib.parse import urljoin, urlparse

from libs.ingestion import config as cfg
from libs.ingestion.connectors.base import FetchedArtifact

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def _fetch_static(url: str) -> str:
    import httpx

    with httpx.Client(
        follow_redirects=True,
        timeout=httpx.Timeout(cfg.WEB_FETCH_TIMEOUT),
        headers={"User-Agent": _USER_AGENT, "Accept-Language": "vi,en;q=0.8"},
    ) as client:
        response = client.get(url)
        response.raise_for_status()
        return response.text


def _fetch_rendered(url: str) -> str:
    from playwright.sync_api import TimeoutError as PWTimeout
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
        )
        try:
            page = browser.new_page(user_agent=_USER_AGENT, locale="vi-VN")
            page.goto(url, wait_until="domcontentloaded", timeout=int(cfg.WEB_FETCH_TIMEOUT * 1000))
            try:
                page.wait_for_load_state("networkidle", timeout=int(cfg.WEB_RENDER_WAIT * 1000))
            except PWTimeout:
                pass
            return str(page.content())
        finally:
            browser.close()


def _fetch_html(url: str, render: bool) -> str:
    if render:
        try:
            return _fetch_rendered(url)
        except Exception:
            pass
    return _fetch_static(url)


def _extract_main_content(html: str, url: str) -> tuple[str, str]:
    try:
        import trafilatura
    except ImportError:
        return html, "html"

    for output_format in ("markdown", "txt"):
        try:
            extracted = trafilatura.extract(
                html, url=url, output_format=output_format,
                include_tables=True, include_comments=False, include_images=False,
                favor_precision=True,
            )
        except Exception:
            extracted = None
        if extracted and extracted.strip():
            return extracted, "md"
    return html, "html"


def _discover_links(html: str, url: str, same_domain_only: bool) -> list[str]:
    """Link discovery for a `recursive` source (Quân's queue-crawl idea) —
    best-effort: a page that fails to parse just yields no links rather than
    failing the whole fetch, since discovery is additive on top of the page's
    own successful ingest."""
    try:
        import lxml.html  # trafilatura's own dependency, no new pin needed
    except ImportError:
        return []

    try:
        tree = lxml.html.fromstring(html)
    except Exception:
        return []

    origin = urlparse(url).netloc
    links: list[str] = []
    seen: set[str] = set()
    for href in tree.xpath("//a/@href"):
        absolute = urljoin(url, href).split("#", 1)[0]
        if not absolute.startswith(("http://", "https://")) or absolute in seen:
            continue
        if same_domain_only and urlparse(absolute).netloc != origin:
            continue
        seen.add(absolute)
        links.append(absolute)
    return links


def fetch(config: dict) -> FetchedArtifact:
    url = config["url"]
    render = bool(config.get("render", False))
    html = _fetch_html(url, render)
    content, extension = _extract_main_content(html, url)

    # Quân's quality metric: how much of the raw HTML was tag/boilerplate vs
    # kept content, so a run's extraction quality is visible in run history.
    raw_len = len(html)
    content_len = len(content)
    tag_stripped_pct = round(1 - content_len / max(raw_len, 1), 4)

    extra: dict = {"raw_len": raw_len, "content_len": content_len, "tag_stripped_pct": tag_stripped_pct}
    if config.get("recursive"):
        extra["discovered_links"] = _discover_links(html, url, bool(config.get("same_domain_only", True)))

    return FetchedArtifact(
        content_bytes=content.encode("utf-8"),
        content_type="text/html",
        source_url=url,
        extension=extension,
        extra=extra,
    )
