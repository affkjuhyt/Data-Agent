"""Bulk-import's URL selection per connector type.

Found via a real ANKT registry entry (vn-mof-debt-bulletin): an http_file
row's `landing_url` was an `.htm` browse page whose DNS doesn't even resolve
from this server, with the real fetchable PDF one level down at
`sample_urls[0]` — the code picked landing_url unconditionally regardless of
connector type, so a direct-fetch source would always take the wrong URL
when both fields are present.
"""
from __future__ import annotations

import pytest

from services.kb_backend.router.knowledge.sources import _entry_to_create_request

pytestmark = pytest.mark.unit

_LANDING = "https://qln.mof.gov.vn/ban-tin-no-cong.htm"
_MIRROR = "https://static1.vietstock.vn/edocs/14237/document_2_.pdf"


def _entry(connector: str) -> dict:
    return {
        "id": "vn-mof-debt-bulletin", "title": "Bản tin nợ công", "connector": connector,
        "format": "pdf", "landing_url": _LANDING, "sample_urls": [_MIRROR], "schedule": "yearly",
    }


@pytest.mark.parametrize("connector", ["http-file", "rss", "rest-api", "sdmx"])
def test_direct_fetch_connectors_prefer_sample_urls_over_landing_url(connector: str) -> None:
    req, reason = _entry_to_create_request(_entry(connector))
    assert reason is None
    assert req is not None
    assert req.url == _MIRROR


@pytest.mark.parametrize("connector", ["web-crawl", "web-crawl-headless"])
def test_crawl_connectors_still_prefer_landing_url(connector: str) -> None:
    req, reason = _entry_to_create_request(_entry(connector))
    assert reason is None
    assert req is not None
    assert req.url == _LANDING


def test_falls_back_to_landing_url_when_no_sample_urls() -> None:
    entry = _entry("http-file")
    del entry["sample_urls"]
    req, reason = _entry_to_create_request(entry)
    assert reason is None
    assert req is not None
    assert req.url == _LANDING


def test_yearly_schedule_maps_to_a_once_a_year_cron() -> None:
    req, _ = _entry_to_create_request(_entry("http-file"))
    assert req is not None
    assert req.cron == "0 3 1 1 *"
