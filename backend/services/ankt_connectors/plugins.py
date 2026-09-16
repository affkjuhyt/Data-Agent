"""Connector plugins, one per PROTOCOL.

data/sources.yaml holds 38 sources that between them need seven plugins:
http-file, rest-api, sdmx, web-crawl, bulk-parquet, rss and manual-upload
(TECH-DESIGN T10). A plugin per publisher would need thirty-eight, and the
thirty-ninth source would still be a code change — which is the same as saying
a data steward can never add one.

So nothing here names IMF or the World Bank. Each plugin takes a `config` from
the source registry plus a `series_key` from the binding, and everything that
differs between two sources on the same protocol is data:

    sdmx       {base_url, agency, dataflow}            key: VNM.GGXWDG.A
    rest-json  {url_template, rows_path, period, ...}  key: NY.GDP.MKTP.CN

`rest-json`'s extraction spec is a deliberately small path language, not
JSONPath. It walks keys and list indices, and pulls a period either from a
field or from a tagged entry in a nested list — which is exactly the span of
shapes the REST sources in the catalogue actually use, including the two
mutually incompatible envelopes the World Bank serves from one API. Anything
past that is a sign the source needs its own protocol, not that this spec
should grow a query engine.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx


@dataclass(frozen=True)
class Point:
    period: str
    value: Decimal
    source_ref: dict[str, Any]
    """Where this exact figure came from: endpoint plus the parameters that
    select it, so it can be re-fetched and checked (README 3.3 rule 3)."""


@dataclass(frozen=True)
class Fetched:
    release_date: dt.date
    vintage_label: str
    points: list[Point] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    raw: bytes = b""
    """Exactly what the publisher returned, kept so it can be stored as an
    artifact. Parsed figures alone cannot answer "what did the IMF actually
    send that day" once the IMF has revised — which is the question a citation
    has to survive (README section 3.3 rule 3)."""
    content_type: str = "application/octet-stream"


class FetchError(RuntimeError):
    """The source answered, but not with something this plugin can read."""


class ConfigError(ValueError):
    """The registry holds a config this plugin cannot work with.

    Separate from FetchError so the UI can tell "your settings are wrong" from
    "the publisher is down" — one is the steward's to fix, the other is not.
    """


def _decimal(raw: Any) -> Decimal | None:
    if raw in (None, "", "NA"):
        return None
    try:
        return Decimal(str(raw))
    except InvalidOperation:
        return None


def _require(config: dict[str, Any], *keys: str) -> tuple[Any, ...]:
    missing = [k for k in keys if not config.get(k)]
    if missing:
        raise ConfigError(f"config is missing {', '.join(missing)}")
    return tuple(config[k] for k in keys)


def _parse_date(raw: Any, formats: tuple[str, ...], what: str) -> dt.date:
    for fmt in formats:
        try:
            return dt.datetime.strptime(str(raw).strip(), fmt).date()
        except ValueError:
            continue
    raise FetchError(f"no usable {what} in the response (got {raw!r})")


def _walk(payload: Any, path: list[Any]) -> Any:
    """Follow a path of dict keys and list indices. Returns None if it breaks."""
    current = payload
    for step in path:
        try:
            current = current[step]
        except (KeyError, IndexError, TypeError):
            return None
    return current


def is_projection(period: str, release_date: dt.date) -> bool:
    """Whether a figure is the publisher's forecast rather than an outturn.

    Half of a WEO table is projection, in the same column as observed years
    with nothing marking where one ends. A year that had not finished when the
    source published cannot be an outturn, so that is the line drawn — and it
    errs toward dropping a real figure rather than storing a forecast as one.
    An indicator platform that answers "nợ công 2030" with a number nobody ever
    reported is worse than one that says the year is not published yet.
    """
    try:
        return int(period) >= release_date.year
    except ValueError:
        return False


# ── sdmx ──────────────────────────────────────────────────────────────────────

def fetch_sdmx(config: dict[str, Any], series_key: str, client: httpx.Client) -> Fetched:
    """SDMX 3.0 data query, read as SDMX-CSV.

    CSV rather than SDMX-JSON: the JSON flavour encodes observations as
    positional indexes into separate dimension arrays, compact and unreadable
    in a diff when a figure changes.
    """
    base_url, agency, dataflow = _require(config, "base_url", "agency", "dataflow")
    url = f"{base_url.rstrip('/')}/{agency}/{dataflow}/+/{series_key}"
    response = client.get(url, headers={"Accept": "application/vnd.sdmx.data+csv"})
    response.raise_for_status()

    rows = list(csv.DictReader(io.StringIO(response.text)))
    if not rows:
        raise FetchError(f"no rows for {series_key}")

    vintage_field = config.get("vintage_field", "COUNTRY_UPDATE_DATE")
    label_field = config.get("vintage_label_field", "STRUCTURE_ID")
    release_date = _parse_date(rows[0].get(vintage_field),
                               ("%m/%d/%Y", "%Y-%m-%d"), vintage_field)
    label = rows[0].get(label_field) or dataflow

    drop_projections = config.get("drop_projections", True)
    points, warnings = [], []
    for row in rows:
        period, value = row.get("TIME_PERIOD"), _decimal(row.get("OBS_VALUE"))
        if not period or value is None:
            continue
        if drop_projections and is_projection(period, release_date):
            warnings.append(f"{series_key} {period}: skipped, not an outturn at this vintage")
            continue
        points.append(Point(period, value, {
            "api": url, "params": {"key": series_key, "format": "sdmx-csv"},
            "dataset": label, "time_period": period,
        }))
    return Fetched(release_date, str(label), points, warnings,
                   raw=response.content, content_type="text/csv")


# ── rest-json ─────────────────────────────────────────────────────────────────

def fetch_rest_json(config: dict[str, Any], series_key: str, client: httpx.Client) -> Fetched:
    """A JSON REST endpoint described by an extraction spec in the config."""
    (url_template,) = _require(config, "url_template")
    url = url_template.format(key=series_key, **config.get("url_vars", {}))
    params = dict(config.get("params") or {})

    response = client.get(url, params=params)
    response.raise_for_status()
    payload = response.json()

    rows = _walk(payload, list(config.get("rows_path") or []))
    if not isinstance(rows, list):
        # Several REST sources report "not found" inside a 200 response, so the
        # status code alone cannot be trusted; a missing rows block IS the error.
        raise FetchError(f"no data block for {series_key} at "
                         f"rows_path={config.get('rows_path')!r}")

    updated_raw = _walk(payload, list(config.get("vintage_path") or []))
    release_date = _parse_date(updated_raw, ("%Y-%m-%d", "%d/%m/%Y"), "vintage_path")
    label = f"{config.get('vintage_prefix', 'REST')} {release_date.isoformat()}"

    value_field = config.get("value_field", "value")
    drop_projections = config.get("drop_projections", False)
    points, warnings = [], []
    for row in rows:
        value = _decimal(row.get(value_field) if isinstance(row, dict) else None)
        period = _period_of(row, config)
        if value is None or not period:
            continue
        if drop_projections and is_projection(period, release_date):
            warnings.append(f"{series_key} {period}: skipped, not an outturn at this vintage")
            continue
        points.append(Point(period, value, {
            "api": url, "params": {**params, "series": series_key},
            "dataset": label, "time_period": period,
        }))
    return Fetched(release_date, label, points, warnings,
                   raw=response.content, content_type="application/json")


def _period_of(row: Any, config: dict[str, Any]) -> str | None:
    """The reference period of one row, however this source spells it.

    Two shapes cover the catalogue: a plain field, or an entry tagged inside a
    nested list — which is how databank-style APIs carry dimensions.
    """
    if not isinstance(row, dict):
        return None
    spec = config.get("period_from")
    if not spec:
        return str(row.get(config.get("period_field", "date")) or "") or None

    entries = row.get(spec["list_field"]) or []
    match_key, match_value = spec["match_key"], spec["match_value"]
    for entry in entries:
        if isinstance(entry, dict) and entry.get(match_key) == match_value:
            raw = str(entry.get(spec.get("take", "id"), ""))
            return raw.removeprefix(spec.get("strip_prefix", "")) or None
    return None


PLUGINS = {
    "sdmx": fetch_sdmx,
    "rest-json": fetch_rest_json,
}

# What the wizard needs to draw a form for each protocol, served from here
# rather than hard-coded in the frontend. The repo already had this idea in
# warehouse.data.ts — a declarative field list driving a form — and the only
# change is which side owns it: a plugin added to the backend becomes an option
# in the UI without a frontend release, which is the whole point of protocol
# plugins. `json` fields hold a nested spec the steward pastes; every one of
# them is documented by the example rather than by prose nobody reads.
SCHEMAS: dict[str, dict[str, Any]] = {
    "sdmx": {
        "connector": "sdmx",
        "label": "SDMX 3.0",
        "description": "Chuẩn thống kê quốc tế (IMF, BIS, OECD, Eurostat). "
                       "Đọc dạng SDMX-CSV.",
        "series_key_label": "Mã chuỗi (series key)",
        "series_key_placeholder": "VNM.GGXWDG.A",
        "fields": [
            {"key": "base_url", "label": "Base URL", "required": True,
             "placeholder": "https://api.imf.org/external/sdmx/3.0/data/dataflow"},
            {"key": "agency", "label": "Agency", "required": True,
             "placeholder": "IMF.RES"},
            {"key": "dataflow", "label": "Dataflow", "required": True,
             "placeholder": "WEO"},
            {"key": "vintage_field", "label": "Cột chứa ngày công bố",
             "required": False, "placeholder": "COUNTRY_UPDATE_DATE",
             "help": "Cột nào trong CSV cho biết nguồn cập nhật lần cuối khi nào. "
                     "Không có nó thì không phân biệt được chạy lại với bản sửa."},
            {"key": "drop_projections", "label": "Bỏ số dự phóng", "type": "boolean",
             "required": False, "default": True,
             "help": "Bảng WEO để dự báo chung cột với số thực hiện và không "
                     "đánh dấu ranh giới. Bật thì chỉ giữ năm đã kết thúc."},
        ],
    },
    "rest-json": {
        "connector": "rest-json",
        "label": "REST JSON",
        "description": "Một endpoint JSON bất kỳ, mô tả bằng đường dẫn trích xuất.",
        "series_key_label": "Mã chuỗi ở nguồn",
        "series_key_placeholder": "NY.GDP.MKTP.CN",
        "fields": [
            {"key": "url_template", "label": "URL (dùng {key} cho mã chuỗi)",
             "required": True,
             "placeholder": "https://api.worldbank.org/v2/country/VNM/indicator/{key}"},
            {"key": "params", "label": "Tham số query", "type": "json",
             "required": False, "placeholder": '{"format": "json", "per_page": "200"}'},
            {"key": "rows_path", "label": "Đường tới mảng dữ liệu", "type": "json",
             "required": True, "placeholder": "[1]",
             "help": 'Đi theo khoá và chỉ số. World Bank WDI dùng [1]; '
                     'databank dùng ["source","data"].'},
            {"key": "vintage_path", "label": "Đường tới ngày công bố", "type": "json",
             "required": True, "placeholder": '[0, "lastupdated"]'},
            {"key": "period_field", "label": "Trường chứa kỳ", "required": False,
             "placeholder": "date"},
            {"key": "period_from", "label": "Lấy kỳ từ danh sách lồng nhau",
             "type": "json", "required": False,
             "placeholder": '{"list_field": "variable", "match_key": "concept", '
                            '"match_value": "Time", "take": "id", "strip_prefix": "YR"}',
             "help": "Chỉ dùng khi kỳ không nằm ở một trường phẳng."},
            {"key": "value_field", "label": "Trường chứa giá trị", "required": False,
             "placeholder": "value"},
            {"key": "vintage_prefix", "label": "Nhãn vintage", "required": False,
             "placeholder": "WDI"},
        ],
    },
}


def fetch(connector: str, config: dict[str, Any], series_key: str,
          client: httpx.Client) -> Fetched:
    if connector not in PLUGINS:
        raise ConfigError(
            f"no plugin for connector {connector!r}; available: {', '.join(sorted(PLUGINS))}")
    return PLUGINS[connector](config, series_key, client)
