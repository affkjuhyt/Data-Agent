"""Unit tests for backend/services/ankt_connectors/plugins.py.

Against recorded responses, not the live APIs: a test that needs the IMF to be
up tells you about the IMF. The payloads below are trimmed copies of real ones
(fetched 16/09/2026), kept in the shapes that actually caused bugs — the World
Bank's two different envelopes, and the WEO table where forecast and outturn sit
in the same column with nothing marking the boundary.

Every case drives the plugin through CONFIG. Nothing in plugins.py names a
publisher, so these configs are exactly what a row in ankt.source holds and
what a steward would fill in — if a source can only be read by editing Python,
that is the bug these tests are shaped to catch.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

pytest.importorskip("httpx", reason="httpx not installed in this environment")

import httpx

from services.ankt_connectors.plugins import ConfigError, FetchError, fetch

pytestmark = pytest.mark.unit


_IMF_CSV = (
    "STRUCTURE,STRUCTURE_ID,COUNTRY,INDICATOR,TIME_PERIOD,OBS_VALUE,SCALE,UNIT,"
    "COUNTRY_UPDATE_DATE\n"
    "dataflow,IMF.RES:WEO(9.0.0),VNM,GGXWDG,2023,3369000000000000,9,XDC,9/23/2025\n"
    "dataflow,IMF.RES:WEO(9.0.0),VNM,GGXWDG,2024,3587002103452000,9,XDC,9/23/2025\n"
    "dataflow,IMF.RES:WEO(9.0.0),VNM,GGXWDG,2025,3897493560238000,9,XDC,9/23/2025\n"
    "dataflow,IMF.RES:WEO(9.0.0),VNM,GGXWDG,2030,5400000000000000,9,XDC,9/23/2025\n"
)

_WDI_JSON = [
    {"page": 1, "pages": 1, "per_page": 200, "total": 2, "lastupdated": "2026-07-13"},
    [
        {"date": "2024", "value": 11510328920773200},
        {"date": "2023", "value": 10319058868956000},
        {"date": "1970", "value": None},
    ],
]

_IDS_JSON = {
    "page": 1, "lastupdated": "2025-12-03",
    "source": {"id": "6", "name": "International Debt Statistics", "data": [
        {"variable": [{"concept": "Country", "id": "VNM"},
                      {"concept": "Time", "id": "YR2024"}],
         "value": 132907340372.3},
        {"variable": [{"concept": "Country", "id": "VNM"},
                      {"concept": "Time", "id": "YR2023"}],
         "value": None},
    ]},
}


SDMX_CONFIG = {
    "base_url": "https://api.imf.org/external/sdmx/3.0/data/dataflow",
    "agency": "IMF.RES",
    "dataflow": "WEO",
    "drop_projections": True,
}

WDI_CONFIG = {
    "url_template": "https://api.worldbank.org/v2/country/VNM/indicator/{key}",
    "params": {"format": "json", "per_page": "200"},
    "rows_path": [1],
    "vintage_path": [0, "lastupdated"],
    "period_field": "date",
    "vintage_prefix": "WDI",
}

# Same publisher, same host, incompatible envelope — one plugin, two configs.
IDS_CONFIG = {
    "url_template": "https://api.worldbank.org/v2/sources/6/country/VNM/series/{key}",
    "params": {"format": "json", "per_page": "200"},
    "rows_path": ["source", "data"],
    "vintage_path": ["lastupdated"],
    "vintage_prefix": "IDS",
    "period_from": {"list_field": "variable", "match_key": "concept",
                    "match_value": "Time", "take": "id", "strip_prefix": "YR"},
}


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _text(body: str):
    return lambda request: httpx.Response(200, text=body)


def _json(body):
    return lambda request: httpx.Response(200, json=body)


# ── IMF ───────────────────────────────────────────────────────────────────────

def test_imf_reads_the_vintage_from_the_response():
    result = fetch("sdmx", SDMX_CONFIG, "VNM.GGXWDG.A", _client(_text(_IMF_CSV)))

    assert result.release_date == dt.date(2025, 9, 23)
    assert result.vintage_label == "IMF.RES:WEO(9.0.0)"


def test_imf_keeps_outturns_and_drops_projections():
    """Half a WEO table is forecast, in the same column as observed years with
    nothing marking the boundary. A year that had not finished when the source
    published cannot be an outturn."""
    result = fetch("sdmx", SDMX_CONFIG, "VNM.GGXWDG.A", _client(_text(_IMF_CSV)))

    assert [p.period for p in result.points] == ["2023", "2024"]
    assert len(result.warnings) == 2
    assert "2030" in result.warnings[1]


def test_imf_keeps_the_value_the_publisher_stated():
    """SCALE=9 is a display hint about how the WEO prints the figure, not a
    factor: applying it would put Vietnam's GDP past the world's."""
    result = fetch("sdmx", SDMX_CONFIG, "VNM.GGXWDG.A", _client(_text(_IMF_CSV)))

    assert result.points[1].value == Decimal("3587002103452000")


def test_imf_records_where_each_figure_came_from():
    [_, point] = fetch("sdmx", SDMX_CONFIG, "VNM.GGXWDG.A", _client(_text(_IMF_CSV))).points

    assert point.source_ref["params"]["key"] == "VNM.GGXWDG.A"
    assert point.source_ref["time_period"] == "2024"
    assert point.source_ref["api"].startswith("https://api.imf.org/")


def test_imf_without_a_usable_update_date_is_an_error():
    """No vintage means no way to tell a rerun from a revision."""
    broken = _IMF_CSV.replace("9/23/2025", "")

    with pytest.raises(FetchError, match="COUNTRY_UPDATE_DATE"):
        fetch("sdmx", SDMX_CONFIG, "VNM.GGXWDG.A", _client(_text(broken)))


def test_imf_empty_response_is_an_error():
    header = _IMF_CSV.splitlines()[0] + "\n"

    with pytest.raises(FetchError, match="no rows"):
        fetch("sdmx", SDMX_CONFIG, "VNM.NOPE.A", _client(_text(header)))


# ── World Bank ────────────────────────────────────────────────────────────────

def test_wdi_reads_the_indicator_envelope():
    result = fetch("rest-json", WDI_CONFIG, "NY.GDP.MKTP.CN", _client(_json(_WDI_JSON)))

    assert result.release_date == dt.date(2026, 7, 13)
    assert [p.period for p in result.points] == ["2024", "2023"]
    assert result.points[0].value == Decimal("11510328920773200")


def test_wdi_skips_periods_with_no_figure():
    result = fetch("rest-json", WDI_CONFIG, "NY.GDP.MKTP.CN", _client(_json(_WDI_JSON)))

    assert "1970" not in [p.period for p in result.points]


def test_ids_reads_the_databank_envelope():
    """Same API, a different shape: the databank endpoints answer with a bare
    object where the indicator endpoint answers with [header, rows]."""
    result = fetch("rest-json", IDS_CONFIG, "DT.DOD.DECT.CD", _client(_json(_IDS_JSON)))

    assert result.release_date == dt.date(2025, 12, 3)
    assert [(p.period, p.value) for p in result.points] == [
        ("2024", Decimal("132907340372.3"))]


def test_an_unknown_series_answers_200_and_must_still_fail():
    """The World Bank reports 'not found' inside a 200 response, so the status
    code alone cannot be trusted."""
    not_found = [{"message": [{"id": "120", "value": "not found"}]}]

    with pytest.raises(FetchError, match="no data block"):
        fetch("rest-json", WDI_CONFIG, "NO.SUCH.SERIES", _client(_json(not_found)))


def test_ids_without_a_source_block_fails():
    with pytest.raises(FetchError, match="no data block"):
        fetch("rest-json", IDS_CONFIG, "NO.SUCH", _client(_json({"page": 1})))


def test_a_source_with_no_vintage_stamp_is_an_error():
    """No vintage means no way to tell a rerun from a revision, which is the
    one thing the whole append-only store rests on."""
    headerless = [{"page": 1, "lastupdated": None}, []]

    with pytest.raises(FetchError, match="vintage_path"):
        fetch("rest-json", WDI_CONFIG, "NY.GDP.MKTP.CN", _client(_json(headerless)))


def test_an_http_error_is_not_swallowed():
    def failing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream down")

    with pytest.raises(httpx.HTTPStatusError):
        fetch("rest-json", WDI_CONFIG, "NY.GDP.MKTP.CN", _client(failing))


# ── the plugins are generic, which is the whole point ─────────────────────────

def test_one_plugin_reads_two_incompatible_envelopes():
    """WDI and IDS come from the same host and share no response shape. If
    telling them apart needed Python, a steward could not add either."""
    wdi = fetch("rest-json", WDI_CONFIG, "NY.GDP.MKTP.CN", _client(_json(_WDI_JSON)))
    ids = fetch("rest-json", IDS_CONFIG, "DT.DOD.DECT.CD", _client(_json(_IDS_JSON)))

    assert wdi.points[0].period == "2024"
    assert ids.points[0].period == "2024"


def test_a_new_sdmx_source_is_config_not_code():
    """A different agency and dataflow on the same protocol — the URL the
    plugin builds follows the config, so adding BIS or OECD is a row."""
    seen = {}

    def capture(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, text=_IMF_CSV)

    fetch("sdmx", {**SDMX_CONFIG, "agency": "BIS", "dataflow": "XRU"},
          "Q.VN.USD.A", _client(capture))

    assert seen["url"].endswith("/BIS/XRU/+/Q.VN.USD.A")


def test_an_unknown_connector_names_what_is_available():
    with pytest.raises(ConfigError, match="rest-json"):
        fetch("carrier-pigeon", {}, "x", _client(_text("")))


def test_a_config_missing_required_keys_is_a_config_error():
    """Distinct from FetchError so the screen can say whose fault it is: the
    steward's settings, or the publisher being down."""
    with pytest.raises(ConfigError, match="dataflow"):
        fetch("sdmx", {"base_url": "https://x", "agency": "IMF.RES"},
              "VNM.NGDP.A", _client(_text(_IMF_CSV)))
