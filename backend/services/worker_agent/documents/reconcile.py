"""A figure a document states, checked against the figure the data gives (F-26, AE-13).

A hybrid question ("theo báo cáo, dư nợ Hà Nội năm 2024 là bao nhiêu, có khớp dữ liệu không?")
reads a number twice: from the data (a governed metric or SQL the model wrote) and from a
document's table cell (documents/table_query). Before the answer is written the two are compared
here, deterministically, never by the model (ASK_ENGINE_SPEC §10, ARCHITECTURE §5.4):

* units: a currency against another currency, or a percentage against an amount, is not
  comparable (no exchange table); a cell with no unit is read in the data's unit, said as an
  assumption, and a difference of exactly a power of a thousand is then a scale the document did
  not state, not a disagreement;
* period: both figures must cover the same span (start and end); a cell with no period, or a
  period other than the data's, is not comparable. A fiscal year is read as the calendar year only
  when the Data Space's fiscal year ends in December, and that is said;
* members: every member the data figure is of (a filter, a row's cut) must be named in the cell's
  row, column, caption or title;
* tolerance: |document - data| / |data| <= 0.5 % (a metric's own tolerance when it has one) agrees;
* authority, when they differ: a document an amendment replaced is dropped for the amendment; a
  certified metric outranks a document (verdict `authority`, both numbers said); otherwise the
  two disagree with nothing to settle it: `conflict`, which the answer carries as SOURCE_CONFLICT.

The result is a ReconciliationNote (API_AND_SCHEMA_CONTRACTS §3.10) the server fills; the reporter
only writes sentences around it.
"""

from __future__ import annotations

import datetime
import decimal
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from libs import clock
from libs.contracts.v1.trust import TrustCode
from libs.language import fold

Verdict = Literal["agree", "authority", "conflict", "not_comparable"]
Authority = Literal["certified_metric", "amendment", "original", "document", "none"]

# Two sources agree within this share of the data's figure, in percent (the contract's default).
DEFAULT_TOLERANCE_PCT = 0.5
_PERCENT = {"%", "percent", "pct", "phan tram", "ty le"}
_CURRENCIES = {"vnd": "VND", "dong": "VND", "vnđ": "VND", "usd": "USD", "$": "USD", "eur": "EUR"}
# A difference of exactly one of these is a scale (thousands, millions…) a cell did not state.
_SCALES = tuple(decimal.Decimal(10) ** n for n in (3, 6, 9, 12))
_YEAR = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
_FISCAL = re.compile(r"\bfy\s*\d|\b(?:nam tai chinh|tai khoa|fiscal)\b")


@dataclass(frozen=True)
class Period:
    start: datetime.date  # inclusive
    end: datetime.date  # exclusive
    label: str
    fiscal: bool = False

    def same_span(self, other: Period) -> bool:
        return (self.start, self.end) == (other.start, other.end)


@dataclass(frozen=True)
class SourceFigure:
    """One figure of one source, as the comparison needs it."""

    figure_id: str
    label: str
    value: decimal.Decimal
    unit: str | None
    trust_code: TrustCode
    authority: Authority
    period: Period | None
    # The members the figure is of (a data figure's filters and cuts), as written.
    members: tuple[str, ...] = ()
    # Where a document figure's members and period are named: its row, column, caption, title.
    context: str = ""
    as_of: str | None = None
    cite: int | None = None
    # Said in the note: how the figure was read (a cell's row and column, a metric's version).
    basis: str = ""


@dataclass
class Reconciliation:
    verdict: Verdict
    data: SourceFigure
    document: SourceFigure
    tolerance_pct: float
    residual_abs: decimal.Decimal | None = None
    residual_pct: float | None = None
    winner: SourceFigure | None = None
    rules: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    # Why the two could not be compared (verdict not_comparable).
    open: list[str] = field(default_factory=list)

    @property
    def differs(self) -> bool:
        return self.verdict in ("authority", "conflict")

    def note(self) -> dict[str, Any]:
        """The ReconciliationNote (API_AND_SCHEMA_CONTRACTS §3.10), plus `not_comparable`."""
        identity: dict[str, Any] = {
            "lhs": f"{self.data.label} = {_plain(self.data.value)}",
            "rhs": f"{self.document.label} = {_plain(self.document.value)}",
            "holds": self.verdict == "agree",
        }
        if self.residual_abs is not None:
            identity["diff"] = float(self.residual_abs)
        return {
            "stage": "checked",
            "target": self.data.label,
            "basis": self.data.basis or self.data.label,
            "grain": ", ".join(
                [p.label for p in (self.data.period,) if p is not None] + list(self.data.members)
            ),
            "rules": self.rules,
            "identities": [identity] if self.verdict != "not_comparable" else [],
            "residual": {
                **({"abs": float(self.residual_abs)} if self.residual_abs is not None else {}),
                **({"pct": self.residual_pct} if self.residual_pct is not None else {}),
                "tolerance_pct": self.tolerance_pct,
            },
            "open": self.open,
            "assumptions": self.assumptions,
            "sources": [_source(f, self.winner) for f in (self.data, self.document)],
            "verdict": self.verdict,
            "winner": self.winner.figure_id if self.winner is not None else None,
        }


def _plain(value: decimal.Decimal) -> str:
    return format(value.normalize(), "f") if value == value.to_integral() else format(value, "f")


def _source(f: SourceFigure, winner: SourceFigure | None) -> dict[str, Any]:
    return {
        "figure_id": f.figure_id,
        "label": f.label,
        "value": _plain(f.value),
        "trust_code": f.trust_code.value,
        "as_of": f.as_of or "",
        "unit_normalized": unit_of(f.unit) or "",
        "period_normalized": f.period.label if f.period is not None else "",
        "authority": f.authority,
        "basis": f.basis,
        **({"cite": f.cite} if f.cite is not None else {}),
        **({"wins": True} if winner is not None and winner.figure_id == f.figure_id else {}),
    }


# ---------------------------------------------------------------- normalising


def unit_of(unit: str | None) -> str | None:
    """A unit as it is compared: "percent", a currency code, or the folded word ("chuyen")."""
    if unit is None or not unit.strip():
        return None
    folded = fold(unit).strip()
    if folded in _PERCENT or unit.strip() in _PERCENT:
        return "percent"
    if folded in _CURRENCIES:
        return _CURRENCIES[folded]
    if unit.strip().upper() in {"VND", "USD", "EUR"}:
        return unit.strip().upper()
    return folded


def _currency(unit: str | None) -> bool:
    return unit in {"VND", "USD", "EUR"}


def period_of(text: str, today: datetime.date, fiscal_year_end_month: int = 12) -> Period | None:
    """The period a cell's labels name: what the question reader reads ("Năm 2024", "quý 1/2024",
    "tháng 3/2024", "2024-Q1"), else the one year written alone ("2024", "FY2024")."""
    from services.worker_agent.understand.packs.base import iso_answer
    from services.worker_agent.understand.text import find_period

    if not text.strip():
        return None
    folded = fold(text)
    fiscal = bool(_FISCAL.search(folded))
    years = set(_YEAR.findall(folded))
    if fiscal and fiscal_year_end_month != 12 and len(years) == 1:
        # FY2024 ending in June runs from July 2023 to the end of June 2024: never the calendar
        # year the question reader would make of "2024".
        year, first = int(next(iter(years))), fiscal_year_end_month + 1
        return Period(
            datetime.date(year - 1, first, 1), datetime.date(year, first, 1), f"FY{year}", True
        )
    found = iso_answer(text.strip()) or find_period(text, today, clock.DEFAULT)
    if found is not None:
        return Period(found.start, found.end, found.label, fiscal)
    if len(years) == 1:
        year = int(next(iter(years)))
        return Period(datetime.date(year, 1, 1), datetime.date(year + 1, 1, 1), str(year), fiscal)
    return None


def _named(member: str, context: str) -> bool:
    words = " ".join(re.findall(r"\w+", fold(member)))
    return bool(words) and f" {words} " in f" {' '.join(re.findall(r'\w+', fold(context)))} "


# ---------------------------------------------------------------- comparing


@dataclass
class _Pair:
    data: SourceFigure
    document: SourceFigure
    value: decimal.Decimal
    rules: list[str]
    assumptions: list[str]
    open: list[str]
    unit_assumed: bool = False

    @property
    def comparable(self) -> bool:
        return not self.open


def _pair(data: SourceFigure, doc: SourceFigure, fiscal_year_end_month: int) -> _Pair:
    rules: list[str] = []
    assumptions: list[str] = []
    open_: list[str] = []
    unit_data, unit_doc = unit_of(data.unit), unit_of(doc.unit)
    assumed = False
    if unit_data and unit_doc and unit_data != unit_doc:
        if _currency(unit_data) and _currency(unit_doc):
            open_.append(f"đơn vị khác nhau ({unit_doc} và {unit_data}), không có tỷ giá để đổi")
        elif "percent" in (unit_data, unit_doc) or _currency(unit_data) or _currency(unit_doc):
            open_.append(f"đơn vị khác nhau ({unit_doc} và {unit_data})")
        else:
            # Two count words ("chuyến", "lượt"): the cell's word is the table's, not a unit.
            assumptions.append(f"đơn vị '{doc.unit}' của tài liệu đọc như '{data.unit}'")
    elif unit_data and not unit_doc:
        assumed = _currency(unit_data)
        assumptions.append(
            f"ô tài liệu không ghi đơn vị; đọc theo đơn vị của dữ liệu ({data.unit})"
        )
    else:
        rules.append(f"đơn vị: {unit_data or 'không ghi'}, giá trị ô đã nhân theo thang của bảng")
    asked, stated = data.period, doc.period
    if asked is not None and stated is None:
        open_.append(f"tài liệu không ghi kỳ của số, dữ liệu là {asked.label}")
    elif asked is None and stated is not None:
        open_.append(f"dữ liệu không giới hạn kỳ, tài liệu là {stated.label}")
    elif asked is not None and stated is not None:
        if stated.fiscal and fiscal_year_end_month != 12 and not asked.fiscal:
            open_.append(
                f"tài liệu ghi năm tài chính ({stated.label}), dữ liệu theo năm dương lịch"
            )
        elif not asked.same_span(stated):
            open_.append(f"kỳ khác nhau: tài liệu {stated.label}, dữ liệu {asked.label}")
        else:
            rules.append(f"kỳ: {asked.label} ở cả hai nguồn")
            if stated.fiscal:
                assumptions.append("năm tài chính kết thúc tháng 12, trùng năm dương lịch")
    missing = [m for m in data.members if not _named(m, doc.context)]
    if missing:
        open_.append("tài liệu không nói về " + ", ".join(missing))
    elif data.members:
        rules.append("đối tượng: " + ", ".join(data.members))
    return _Pair(data, doc, doc.value, rules, assumptions, open_, assumed)


def _diff(value: decimal.Decimal, base: decimal.Decimal) -> tuple[decimal.Decimal, float]:
    gap = abs(value - base)
    pct = float(gap / abs(base) * 100) if base != 0 else (0.0 if gap == 0 else float("inf"))
    return gap, pct


def _scale_off(value: decimal.Decimal, base: decimal.Decimal, tolerance_pct: float) -> bool:
    """Whether `value` is `base` times (or over) a power of a thousand, within the tolerance."""
    if value == 0 or base == 0:
        return False
    for s in _SCALES:
        for scaled in (base * s, base / s):
            if _diff(value, scaled)[1] <= tolerance_pct:
                return True
    return False


def _current(documents: Sequence[SourceFigure]) -> tuple[list[SourceFigure], list[str]]:
    """The documents an amendment did not replace: an original is dropped when a figure of a
    document that is not one (its amendment, or a document in force) is beside it."""
    if any(d.authority == "original" for d in documents) and any(
        d.authority != "original" for d in documents
    ):
        return (
            [d for d in documents if d.authority != "original"],
            ["bản sửa đổi thay bản gốc: số của văn bản đã bị thay không được dùng"],
        )
    return list(documents), []


def reconcile(
    data: Sequence[SourceFigure],
    documents: Sequence[SourceFigure],
    *,
    tolerance_pct: float = DEFAULT_TOLERANCE_PCT,
    fiscal_year_end_month: int = 12,
) -> Reconciliation | None:
    """The data figure and the document figure compared, the closest comparable pair; None when
    either side has no figure (nothing to reconcile: not every hybrid answer reads a cell)."""
    if not data or not documents:
        return None
    docs, amended = _current(documents)
    pairs = [_pair(n, d, fiscal_year_end_month) for d in docs for n in data]
    comparable = [p for p in pairs if p.comparable]
    tolerance = f"dung sai {tolerance_pct:g} % của số dữ liệu"
    if not comparable:
        first = pairs[0]
        return Reconciliation(
            "not_comparable",
            first.data,
            first.document,
            tolerance_pct,
            rules=[*amended, *first.rules],
            assumptions=first.assumptions,
            open=list(dict.fromkeys(o for p in pairs[:3] for o in p.open)),
        )
    best = min(comparable, key=lambda p: _diff(p.value, p.data.value)[1])
    gap, pct = _diff(best.value, best.data.value)
    result = Reconciliation(
        "agree",
        best.data,
        best.document,
        tolerance_pct,
        residual_abs=gap,
        residual_pct=round(pct, 4) if pct != float("inf") else None,
        rules=[*amended, *best.rules, tolerance],
        assumptions=best.assumptions,
    )
    if pct <= tolerance_pct:
        return result
    if best.unit_assumed and _scale_off(best.value, best.data.value, tolerance_pct):
        result.verdict = "not_comparable"
        result.open = [
            "hai số lệch đúng một bậc nghìn/triệu/tỷ: tài liệu không ghi đơn vị nên chưa rõ thang"
        ]
        return result
    if best.data.authority == "certified_metric":
        result.verdict, result.winner = "authority", best.data
        result.rules.append("thẩm quyền: chỉ số đã chứng nhận được dùng thay số của tài liệu")
        return result
    result.verdict = "conflict"
    result.rules.append("không có luật thẩm quyền giữa hai nguồn: cần người phụ trách dữ liệu xem")
    return result
