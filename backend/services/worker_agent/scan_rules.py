"""Pure rules of the schema and profile scans (UC-04-02), kept apart from I/O so each is tested
on its own: the structure hash, personal-data labels, the value patterns, history tables, join
candidates, how much of a table a profile reads, and whether the source's window allows it now.
"""

from __future__ import annotations

import datetime
import hashlib
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

# --- B2: the structure hash ------------------------------------------------------------------


def schema_hash(columns: Sequence[Mapping[str, object]]) -> str:
    """The hash of a table's columns as the scan reads them. Its input is the one the first
    scans stored, so a table whose structure did not change keeps the same hash."""
    return hashlib.sha256(repr(list(columns)).encode()).hexdigest()


# --- B4: personal data -------------------------------------------------------------------------

# Whole-word phrases of a column name, per label of catalog.column_meta.pii_label. A phrase
# matches a run of the name's words ("so_cccd" has "cccd"); a single word that is only part of
# a phrase does not ("product_name" has no "full_name"). "name" alone is not a phrase: a
# product's or a branch's name is not personal.
_PII_PHRASES: dict[str, tuple[str, ...]] = {
    "national_id": (
        "cccd",
        "cmnd",
        "cmt",
        "can_cuoc",
        "national_id",
        "citizen_id",
        "id_card",
        "passport",
        "ho_chieu",
    ),
    "phone": (
        "phone",
        "mobile",
        "telephone",
        "sdt",
        "so_dt",
        "dien_thoai",
        "di_dong",
    ),
    "email": ("email", "e_mail"),
    "account_no": (
        "account_no",
        "account_number",
        "so_tai_khoan",
        "stk",
        "card_no",
        "card_number",
        "so_the",
        "iban",
    ),
    "name": (
        "full_name",
        "first_name",
        "last_name",
        "customer_name",
        "ho_ten",
        "ho_va_ten",
        "ten_khach_hang",
        "ten_kh",
    ),
    "address": ("address", "dia_chi"),
    "other": ("tax_code", "ma_so_thue", "date_of_birth", "birth_date", "ngay_sinh", "dob"),
}


def _words(name: str) -> list[str]:
    return [w for w in re.split(r"[^a-z0-9]+", name.lower()) if w]


def pii_label_for_name(column_name: str) -> str | None:
    """The personal-data label a column's name implies, or None."""
    words = _words(column_name)
    for label, phrases in _PII_PHRASES.items():
        for phrase in phrases:
            parts = phrase.split("_")
            if any(words[i : i + len(parts)] == parts for i in range(len(words) - len(parts) + 1)):
                return label
    return None


@dataclass(frozen=True)
class ValuePattern:
    key: str
    label: str
    # What the profile screen says it found (catalog.column_profile.pattern_hint).
    hint: str
    # A PostgreSQL regular expression, matched on the value as text inside the source: the
    # values themselves never leave it.
    regex: str


# Matched on text columns only: an identity number or a phone is stored as text (a leading zero
# would be lost otherwise), and numbers of the same length in a numeric column are amounts.
VALUE_PATTERNS: tuple[ValuePattern, ...] = (
    ValuePattern("cccd", "national_id", "số CCCD (12 chữ số)", r"^[0-9]{12}$"),
    ValuePattern("phone", "phone", "số điện thoại di động", r"^(\+?84|0)(3|5|7|8|9)[0-9]{8}$"),
    ValuePattern(
        "email", "email", "địa chỉ email", r"^[^@[:space:]]+@[^@[:space:]]+\.[a-zA-Z]{2,}$"
    ),
)
# Share of a column's non-empty sampled values that must match for the column to be labelled.
PATTERN_SHARE = 0.8


def matched_pattern(non_null: int, matches: Mapping[str, int]) -> ValuePattern | None:
    """The pattern most of a column's values follow, or None."""
    if non_null <= 0:
        return None
    best = max(VALUE_PATTERNS, key=lambda p: matches.get(p.key, 0))
    return best if matches.get(best.key, 0) / non_null >= PATTERN_SHARE else None


# --- 3a: history tables ----------------------------------------------------------------------

_SCD2_FROM = (
    "valid_from",
    "effective_from",
    "start_date",
    "tu_ngay",
    "hieu_luc_tu",
    "ngay_hieu_luc",
)
_SCD2_TO = ("valid_to", "effective_to", "end_date", "den_ngay", "hieu_luc_den", "ngay_het_hieu_luc")
_SNAPSHOT = (
    "snapshot_date",
    "as_of_date",
    "report_date",
    "data_date",
    "ngay_chot",
    "ky_chot",
    "ngay_bao_cao",
    "ngay_du_lieu",
)
_DATED = ("date", "timestamp")


def history_of(columns: Iterable[Mapping[str, object]]) -> tuple[str, str] | None:
    """(history_kind, business_date_col) for a table that keeps history, else None: SCD2 when it
    has a validity start and end, snapshot when it has a snapshot date (UC-04-02 3a)."""
    dated = {
        str(c["name"]).lower(): str(c["name"])
        for c in columns
        if str(c.get("normalized_type")) in _DATED
    }
    start = next((dated[n] for n in _SCD2_FROM if n in dated), None)
    end = next((dated[n] for n in _SCD2_TO if n in dated), None)
    if start and end:
        return "scd2", start
    snapshot = next((dated[n] for n in _SNAPSHOT if n in dated), None)
    if snapshot:
        return "snapshot", snapshot
    return None


# --- B5: undeclared joins ----------------------------------------------------------------------


@dataclass(frozen=True)
class JoinCandidate:
    child: tuple[str, str]
    child_column: str
    parent: tuple[str, str]
    parent_column: str


def _singular(name: str) -> str:
    return name[:-1] if name.endswith("s") and not name.endswith("ss") else name


def join_candidates(
    tables: Sequence[Mapping[str, Any]], declared: set[tuple[str, str, str]], limit: int
) -> list[JoinCandidate]:
    """Columns whose name says they point at another table's single-column key: `<table>_id`
    (or its singular) for a key named id, or the key's own name when it is not `id` (as banks
    name codes: ma_khach_hang in both tables). Declared foreign keys and keys are left out; the
    types must agree. At most `limit`, so the overlap queries stay bounded."""
    parents: list[tuple[tuple[str, str], str, str]] = []
    for table in tables:
        keys = [c for c in table["columns"] if c.get("is_key")]
        if len(keys) == 1:
            key = keys[0]
            parents.append(
                (
                    (str(table["schema_name"]), str(table["table_name"])),
                    str(key["name"]),
                    str(key["normalized_type"]),
                )
            )
    found: list[JoinCandidate] = []
    for table in tables:
        child = (str(table["schema_name"]), str(table["table_name"]))
        for column in table["columns"]:
            name = str(column["name"])
            if column.get("is_key") or (child[0], child[1], name) in declared:
                continue
            for parent, key, key_type in parents:
                if parent == child or key_type != str(column["normalized_type"]):
                    continue
                table_name = parent[1].lower()
                names = (
                    {f"{table_name}_id", f"{_singular(table_name)}_id"} if key == "id" else {key}
                )
                if name.lower() in {n.lower() for n in names}:
                    found.append(JoinCandidate(child, name, parent, key))
                    if len(found) >= limit:
                        return found
    return found


# Share of a candidate's sampled values found in the parent's key to propose the join.
OVERLAP_SHARE = 0.9


# --- BR-15: how much a profile reads, and when ---------------------------------------------------

# BR-15: a table above this many rows is never read whole; a sample of about SAMPLE_ROWS is.
FULL_SCAN_LIMIT = 10_000_000
SAMPLE_ROWS = 1_000_000


def sample_percent(row_estimate: int) -> float | None:
    """TABLESAMPLE's percentage for a table, or None to read it whole."""
    if row_estimate <= FULL_SCAN_LIMIT:
        return None
    return max(round(100 * SAMPLE_ROWS / row_estimate, 4), 0.0001)


_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_CLOCK = re.compile(r"^([01][0-9]|2[0-3]):([0-5][0-9])$")


def _clock(value: object) -> datetime.time | None:
    match = _CLOCK.match(str(value or ""))
    return datetime.time(int(match[1]), int(match[2])) if match else None


def window_allows(window: object, now: datetime.datetime) -> bool:
    """Whether source.scan_window lets a profile read the source at `now` (local time).

    The window is when the bank allows scanning, outside its peak hours (BR-15, BR-103):
    {"start": "22:00", "end": "05:00", "days": ["mon", …]}. A window past midnight belongs to
    the day it opens; without "days" it opens every day. No window, or one that cannot be read,
    sets no limit: the bank has not said when its peak hours are."""
    if not isinstance(window, Mapping):
        return True
    start, end = _clock(window.get("start")), _clock(window.get("end"))
    if start is None or end is None or start == end:
        return True
    days = window.get("days")
    opened = {d for d in days if d in _DAYS} if isinstance(days, list) else set(_DAYS)
    today = _DAYS[now.weekday()]
    yesterday = _DAYS[(now.weekday() - 1) % 7]
    clock = now.time()
    if start < end:
        return today in opened and start <= clock < end
    # Past midnight: the evening part is today's window, the morning part yesterday's.
    return (today in opened and clock >= start) or (yesterday in opened and clock < end)
