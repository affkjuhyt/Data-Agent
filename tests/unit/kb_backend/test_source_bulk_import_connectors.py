"""Bulk import reads each connector's own address field, and only that.

The first import of the ANKT registry skipped 13 rows ("no landing_url or
sample_urls") because rss/rest-api/sdmx rows keep their address in
`api_base` / `example` / `examples`, and it created two rest-api sources from
human landing pages. It also gave every row without a `schedule` a daily cron
and read "every 15 min" as that same default.
"""
from __future__ import annotations

import pytest

from services.kb_backend.router.knowledge.sources import _entry_to_create_requests, _schedule_to_cron

pytestmark = pytest.mark.unit


def _one(entry: dict) -> object:
    requests, reason = _entry_to_create_requests(entry)
    assert reason is None and len(requests) == 1, reason
    return requests[0]


def test_rss_uses_api_base() -> None:
    req = _one({"id": "r", "title": "RSS", "connector": "rss", "api_base": "https://baochinhphu.vn/kinh-te.rss",
                "schedule": "every 15 min"})
    assert req.url == "https://baochinhphu.vn/kinh-te.rss" and req.cron == "*/15 * * * *"


def test_rest_api_prefers_a_complete_example_over_the_bare_endpoint_and_ignores_the_landing_page() -> None:
    req = _one({"id": "w", "title": "WDI", "connector": "rest-api", "landing_url": "https://data.worldbank.org/",
                "example": "https://api.worldbank.org/v2/country/VNM/indicator/X?format=json&mrv=3"})
    assert req.url.endswith("?format=json&mrv=3") and "data.worldbank.org" not in req.url


def test_rest_api_with_only_api_base_uses_it_not_the_landing_page() -> None:
    req = _one({"id": "n", "title": "PX-Web", "connector": "rest-api", "landing_url": "https://pxweb.nso.gov.vn/pxweb/vi/",
                "api_base": "https://pxweb.nso.gov.vn/api/v1/vi/"})
    assert req.url == "https://pxweb.nso.gov.vn/api/v1/vi/"


def test_sdmx_examples_become_one_source_each_with_readable_titles() -> None:
    requests, reason = _entry_to_create_requests({
        "id": "b", "title": "BIS", "connector": "sdmx",
        "examples": ["https://stats.bis.org/api/v1/data/WS_XRU/M.VN.VND.A?format=csv",
                     "https://stats.bis.org/api/v1/data/WS_DEBT_SEC2_PUB/Q.VN.3P.I?format=csv"],
    })
    assert reason is None
    assert [r.title for r in requests] == ["BIS — WS_XRU", "BIS — WS_DEBT_SEC2_PUB"]


def test_api_key_placeholder_is_removed_from_the_url_and_becomes_auth_settings() -> None:
    req = _one({"id": "f", "title": "FRED", "connector": "rest-api", "auth": "api_key",
                "example": "https://api.stlouisfed.org/fred/series/observations?series_id=V&api_key=${FRED_API_KEY}&file_type=json"})
    assert "${" not in req.url and "api_key" not in req.url
    assert "series_id=V" in req.url and "file_type=json" in req.url
    assert req.auth_style == "query_param" and req.auth_param_name == "api_key"
    assert "OpenBao" in (req.notes or "")


@pytest.mark.parametrize("entry, fragment", [
    ({"id": "x", "connector": "http-file", "landing_url": "https://x/page"}, "landing page"),
    ({"id": "x", "connector": "http-file"}, "sample_urls"),
    ({"id": "x", "connector": "rss"}, "api_base"),
    ({"id": "x", "connector": "sdmx"}, "example"),
    ({"id": "x"}, "no connector"),
    ({"id": "x", "connector": "manual-upload"}, "not ingestible"),
    ({"id": "x", "connector": "bulk-parquet"}, "not ingestible"),
])
def test_rows_with_nothing_fetchable_are_skipped_with_a_reason(entry: dict, fragment: str) -> None:
    requests, reason = _entry_to_create_requests(entry)
    assert requests == [] and reason is not None and fragment in reason


@pytest.mark.parametrize("text, cron", [
    (None, ""), ("", ""), ("gibberish", ""),
    ("every 15 min", "*/15 * * * *"), ("every 2 hours", "0 */2 * * *"), ("every 90 min", ""),
    ("hourly", "0 * * * *"), ("daily", "0 3 * * *"), ("monthly (day 3-6)", "0 3 3 * *"),
    ("semi-annual", "0 3 1 1,7 *"), ("yearly", "0 3 1 1 *"),
])
def test_schedule_phrases(text: str | None, cron: str) -> None:
    assert _schedule_to_cron(text) == cron


def test_a_row_without_a_schedule_is_manual_only_not_daily() -> None:
    assert _one({"id": "r", "title": "Report", "connector": "http-file", "sample_urls": ["https://x/a.pdf"]}).cron == ""
