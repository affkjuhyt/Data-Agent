"""Extension inference for downloaded artifacts.

Real cases: BIS SDMX CSV was stored as ".bin"; a URL with a query string
produced an extension like "pdf?token=1"; an API URL ending in a dotted key
(".../M.VN.VND.A") looked like it had extension "A".
"""
from __future__ import annotations

import pytest

from libs.ingestion.connectors.extension import infer_extension

pytestmark = pytest.mark.unit

BIS = "https://stats.bis.org/api/v1/data/WS_XRU/M.VN.VND.A?startPeriod=2024-01&format=csv"


def test_sdmx_csv_is_csv_not_bin() -> None:
    assert infer_extension("text/csv", BIS, prefer_url=False) == "csv"


def test_oecd_sdmx_structured_csv_type() -> None:
    assert infer_extension("application/vnd.sdmx.data+csv", "https://sdmx.oecd.org/x", prefer_url=False) == "csv"


def test_api_dotted_key_is_not_an_extension() -> None:
    assert infer_extension("application/json", "https://api.imf.org/data/VNM.GGXWDG_NGDP.A", prefer_url=False) == "json"


def test_api_with_unknown_type_falls_back_to_bin() -> None:
    assert infer_extension("application/octet-stream", BIS, prefer_url=False) == "bin"


def test_xml_and_rss_types() -> None:
    assert infer_extension("text/xml", "https://x/y", prefer_url=False) == "xml"
    assert infer_extension("application/rss+xml", "https://x/y", prefer_url=False) == "xml"


def test_file_url_query_string_is_ignored() -> None:
    assert infer_extension("application/pdf", "https://x/report.PDF?token=1#p2", prefer_url=True) == "pdf"


def test_file_url_without_extension_uses_content_type() -> None:
    assert infer_extension("application/pdf", "https://x/download/12345", prefer_url=True) == "pdf"
    assert infer_extension(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "https://x/get", prefer_url=True,
    ) == "xlsx"


def test_file_url_extension_wins_over_generic_type() -> None:
    assert infer_extension("application/octet-stream", "https://x/data.xls", prefer_url=True) == "xls"


def test_nothing_known_is_bin() -> None:
    assert infer_extension("", "https://x/download", prefer_url=True) == "bin"
