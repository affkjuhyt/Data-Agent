"""Bulk-import's URL selection per connector type, and its fan-out when one
registry row bundles several files.

Found via two real ANKT registry entries:
- vn-mof-debt-bulletin (http_file): `landing_url` is an `.htm` browse page
  whose DNS doesn't even resolve from this server, with the real fetchable
  PDF one level down at `sample_urls[0]` — the code picked landing_url
  unconditionally regardless of connector type.
- vn-nso-monthly-report (http_file): `format: [docx, xlsx]` with TWO
  `sample_urls` — a narrative report and a data-table workbook are two
  distinct files, but KBSourceConfig has exactly one `url`, so taking only
  sample_urls[0] silently dropped the second file every time.
"""
from __future__ import annotations

import pytest

from services.kb_backend.router.knowledge.sources import _entry_to_create_requests

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
    reqs, reason = _entry_to_create_requests(_entry(connector))
    assert reason is None
    assert [r.url for r in reqs] == [_MIRROR]


@pytest.mark.parametrize("connector", ["web-crawl", "web-crawl-headless"])
def test_crawl_connectors_still_prefer_landing_url(connector: str) -> None:
    reqs, reason = _entry_to_create_requests(_entry(connector))
    assert reason is None
    assert [r.url for r in reqs] == [_LANDING]


def test_falls_back_to_landing_url_when_no_sample_urls() -> None:
    entry = _entry("http-file")
    del entry["sample_urls"]
    reqs, reason = _entry_to_create_requests(entry)
    assert reason is None
    assert [r.url for r in reqs] == [_LANDING]


def test_yearly_schedule_maps_to_a_once_a_year_cron() -> None:
    reqs, _ = _entry_to_create_requests(_entry("http-file"))
    assert reqs[0].cron == "0 3 1 1 *"


def test_multi_format_row_creates_one_source_per_file() -> None:
    docx_url = "https://www.nso.gov.vn/wp-content/uploads/2026/09/01-Loi-van-T8.2026-final.docx"
    xlsx_url = "https://www.nso.gov.vn/wp-content/uploads/2026/09/02-Bieu-T8.2026.xlsx"
    entry = {
        "id": "vn-nso-monthly-report",
        "title": "Báo cáo tình hình kinh tế - xã hội tháng",
        "connector": "http-file",
        "format": ["docx", "xlsx"],
        "landing_url": "https://www.nso.gov.vn/bai-top/2026/09/bao-cao-.../",
        "sample_urls": [docx_url, xlsx_url],
        "variables": ["gdp_quarterly", "budget_revenue"],
        "schedule": "monthly (day 3-6)",
    }
    reqs, reason = _entry_to_create_requests(entry)
    assert reason is None
    assert [r.url for r in reqs] == [docx_url, xlsx_url]
    # Distinguishable titles, base title unchanged for a single-file row.
    assert reqs[0].title == "Báo cáo tình hình kinh tế - xã hội tháng — DOCX"
    assert reqs[1].title == "Báo cáo tình hình kinh tế - xã hội tháng — XLSX"
    # Both files share the row's schedule and variables.
    assert reqs[0].cron == reqs[1].cron == "0 3 3 * *"  # "monthly (day 3-6)" -> the monthly preset
    assert reqs[0].variables == reqs[1].variables == ["gdp_quarterly", "budget_revenue"]


def test_single_sample_url_keeps_the_plain_title() -> None:
    reqs, _ = _entry_to_create_requests(_entry("http-file"))
    assert reqs[0].title == "Bản tin nợ công"
