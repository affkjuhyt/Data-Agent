"""Records out of a stored page by an approved schema version (UC-14-02, UC-14-04 step 1-2).

Pure functions over bytes: the run hands each page's original and the schema version, and gets
records back with a confidence per field. A record goes to the review queue with its reasons; the
random sample (2% of the records without a reason, at least 5, AC-14-04-03) goes there too, so the
gate always sees some records it would not otherwise have looked at.

The record selector is CSS or XPath for HTML and XML (XPath when it starts with "/" or "("), and a
small JSONPath subset ($.a.b[*]) for JSON. A field's locator is the same kind of expression,
relative to the record. Checks that need other runs or other sources (outlier, new_category,
semantic_shift, label_changed) are not made here: they belong to drift detection (KB-04).
"""

from __future__ import annotations

import datetime
import json
import random
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from lxml import etree, html
from lxml.cssselect import CSSSelector

LOW_CONFIDENCE = 0.85  # extracted_record.record_confidence: below this the record is reviewed
SAMPLE_SHARE = 0.02
SAMPLE_MIN = 5

_NUMBER = re.compile(r"[-+]?\d[\d.,\s]*")
_THOUSANDS_DOT = re.compile(r"^\d{1,3}(\.\d{3})+$")
_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y")


class ExtractionError(ValueError):
    """The page cannot be read with this schema (wrong content kind, bad selector)."""


@dataclass
class Record:
    entity_key: str
    values: dict[str, Any]
    field_confidence: dict[str, float]
    record_confidence: float
    review_reasons: list[str] = field(default_factory=list)


def number(text: str) -> Decimal | None:
    """A number as Vietnamese and English pages write it: 4,8 / 4.8 / 1.234,5 / 1,234.5."""
    match = _NUMBER.search(text)
    if match is None:
        return None
    raw = re.sub(r"\s", "", match.group(0))
    if "," in raw and "." in raw:
        decimal_mark = "," if raw.rfind(",") > raw.rfind(".") else "."
        raw = raw.replace("." if decimal_mark == "," else ",", "").replace(decimal_mark, ".")
    elif "," in raw:
        raw = raw.replace(",", ".") if raw.count(",") == 1 else raw.replace(",", "")
    elif _THOUSANDS_DOT.match(raw):
        raw = raw.replace(".", "")
    try:
        return Decimal(raw.rstrip("."))
    except InvalidOperation:
        return None


def _date(text: str) -> datetime.date | None:
    value = text.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def typed(kind: str, text: str) -> tuple[Any, float]:
    """(value, confidence): 1 when the text reads as the field's type, 0.5 when it is there but
    does not, so a person sees the raw text instead of a silent null."""
    if kind in ("number", "decimal", "integer", "percent", "money"):
        value = number(text)
        if value is None:
            return text, 0.5
        return (int(value) if kind == "integer" else str(value)), 1.0
    if kind == "date":
        day = _date(text)
        return (day.isoformat(), 1.0) if day else (text, 0.5)
    return text, 1.0


def _xpath(expr: str) -> bool:
    return expr.startswith(("/", "(", "./"))


def _select(node: Any, expr: str) -> list[Any]:
    try:
        if _xpath(expr):
            found = node.xpath(expr)
            return list(found) if isinstance(found, list) else [found]
        return list(CSSSelector(expr)(node))
    except (etree.XPathError, SyntaxError, ValueError) as exc:
        raise ExtractionError(f"bad selector {expr!r}: {exc}") from exc


def _text(found: list[Any]) -> str:
    if not found:
        return ""
    first = found[0]
    value = first if isinstance(first, str) else first.text_content()
    return " ".join(str(value).split())


def _json_path(data: Any, expr: str) -> list[Any]:
    """$.a.b[*].c and a.b — enough for an API's list of rows; anything else is refused."""
    path = expr.strip()
    path = path[1:] if path.startswith("$") else path
    current = [data]
    for part in re.findall(r"[^.\[\]]+|\[\*\]|\[\d+\]", path):
        nxt: list[Any] = []
        for item in current:
            if part == "[*]":
                nxt.extend(item if isinstance(item, list) else [])
            elif part.startswith("["):
                index = int(part[1:-1])
                if isinstance(item, list) and index < len(item):
                    nxt.append(item[index])
            elif isinstance(item, dict) and part in item:
                nxt.append(item[part])
        current = nxt
    return current


def html_text(content: bytes, content_type: str) -> str | bytes:
    """The page as text when its encoding is known. lxml reads bytes without a declared charset as
    Latin-1, which garbles every Vietnamese letter; bytes are handed over only when they are not
    UTF-8, so that lxml can still honour a <meta charset> (windows-1258 pages)."""
    charset = re.search(r"charset=([\w-]+)", content_type, re.IGNORECASE)
    if charset:
        try:
            return content.decode(charset.group(1), errors="replace")
        except LookupError:
            pass
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return content


def _rows(content: bytes, content_type: str, selector: str) -> tuple[str, list[Any]]:
    kind = content_type.split(";")[0].strip().lower()
    if kind.endswith("json") or kind == "application/json":
        try:
            data = json.loads(content)
        except ValueError as exc:
            raise ExtractionError("the page is not valid JSON") from exc
        return "json", _json_path(data, selector)
    if "xml" in kind and "html" not in kind:
        try:
            root = etree.fromstring(content, parser=etree.XMLParser(resolve_entities=False))
        except etree.XMLSyntaxError as exc:
            raise ExtractionError("the page is not valid XML") from exc
        return "xml", _select(root, selector)
    if "html" in kind or kind.startswith("text/"):
        return "html", _select(html.fromstring(html_text(content, content_type)), selector)
    raise ExtractionError(f"no extractor for {kind or 'unknown content'}")


def _locate(kind: str, row: Any, locator: str) -> str:
    if kind == "json":
        found = _json_path(row, locator)
        return "" if not found or found[0] is None else str(found[0]).strip()
    # An absolute XPath locator is read from the record, not from the top of the page.
    return _text(_select(row, "." + locator if locator.startswith("/") else locator))


def _out_of_domain(value: Any, constraints: dict[str, Any]) -> bool:
    try:
        number_value = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return False
    low, high = constraints.get("min"), constraints.get("max")
    return (low is not None and number_value < Decimal(str(low))) or (
        high is not None and number_value > Decimal(str(high))
    )


def extract(
    content: bytes, content_type: str, selector: str, fields: list[dict[str, Any]]
) -> list[Record]:
    kind, rows = _rows(content, content_type, selector)
    keys = [f["name"] for f in fields if f.get("key") or f.get("is_key")] or [fields[0]["name"]]
    records = []
    for row in rows:
        values: dict[str, Any] = {}
        confidence: dict[str, float] = {}
        reasons: set[str] = set()
        for f in fields:
            raw = _locate(kind, row, str(f.get("locator") or ""))
            if not raw:
                values[f["name"]], confidence[f["name"]] = None, 0.0
                if f.get("required"):
                    reasons.add("missing_required")
                continue
            value, sure = typed(str(f.get("type") or "text"), raw)
            values[f["name"]], confidence[f["name"]] = value, sure
            if sure == 1.0 and _out_of_domain(value, f.get("value_constraints") or {}):
                reasons.add("out_of_domain")
        overall = round(sum(confidence.values()) / len(confidence), 4) if confidence else 0.0
        if overall < LOW_CONFIDENCE:
            reasons.add("low_confidence")
        if all(values.get(k) is None for k in keys):
            # A row without its key is not a record of anything (an empty or header row).
            continue
        entity_key = " | ".join(str(values.get(k) or "") for k in keys)
        records.append(Record(entity_key, values, confidence, overall, sorted(reasons)))
    return records


def sample(records: list[Record], seed: str) -> list[Record]:
    """Marks the random sample among the records with no reason; returns the ones marked."""
    clean = [r for r in records if not r.review_reasons]
    size = min(len(clean), max(SAMPLE_MIN, round(len(clean) * SAMPLE_SHARE)))
    picked = random.Random(seed).sample(clean, size)
    for record in picked:
        record.review_reasons = ["random_sample"]
    return picked
