"""Vietnamese (GEN-31): the words the rule pass was first written for, moved here unchanged."""

from __future__ import annotations

import datetime
import re

from libs import clock
from services.worker_agent.semantic.analysis import RUNNING_TOTAL, SHARE_OF_TOTAL
from services.worker_agent.semantic.compare import PREVIOUS_PERIOD, SAME_PERIOD_LAST_YEAR
from services.worker_agent.understand.packs import base
from services.worker_agent.understand.packs.base import TO_DATE, FoundPeriod, Pack

_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4}
_FISCAL = r"(?:nam tai chinh|fy)\s?(\d{4})"


def _quarter_number(word: str) -> int:
    return int(word) if word.isdigit() else _ROMAN[word]


def periods(t: str, folded: str, today: datetime.date, cal: clock.Calendar) -> FoundPeriod | None:
    year_of = lambda y: int(y) if y else today.year  # noqa: E731

    # A fiscal year or quarter first: "quý 1 năm tài chính 2026" is not the calendar's quarter 1.
    if m := re.search(rf"\b(?:quy (\d|i{{1,3}}|iv)|q([1-4])) {_FISCAL}\b", t):
        q = int(m.group(2)) if m.group(2) else _quarter_number(m.group(1))
        if found := base.fiscal_quarter(int(m.group(3)), q, cal, m.group(0)):
            return found
    if m := re.search(rf"\b{_FISCAL}\b", t):
        return base.fiscal_year(int(m.group(1)), cal, m.group(0))
    for words, by in (("nam tai chinh nay", 0), ("nam tai chinh truoc", -1)):
        if re.search(rf"\b{words}\b", t):
            return base.relative("fiscal_year", by, today, cal, words)

    if m := re.search(r"\bngay (\d{1,2})/(\d{1,2})/(\d{4})\b", t) or re.search(
        r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", t
    ):
        try:
            d = datetime.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
        return base.day(d, m.group(0))
    if m := re.search(r"\bthang (\d{1,2})(?:\s*/\s*|\s+nam\s+)(\d{4})\b", t) or re.search(
        r"\bthang (\d{1,2})\b(?!\s*/)", t
    ):
        found = base.month(
            year_of(m.group(2) if m.lastindex == 2 else None), int(m.group(1)), m.group(0)
        )
        if found:
            return found
    m = re.search(r"\bquy (\d|i{1,3}|iv)(?:\s*/\s*|\s+nam\s+)?(\d{4})?\b", t)
    if m and (found := base.quarter(year_of(m.group(2)), _quarter_number(m.group(1)), m.group(0))):
        return found
    if m := re.search(r"\bnam (\d{4})\b", t):
        return base.year(int(m.group(1)), m.group(0))

    for words, by, unit in (
        ("thang nay", 0, "month"),
        ("thang truoc", -1, "month"),
        ("quy nay", 0, "quarter"),
        ("quy truoc", -1, "quarter"),
        ("nam nay", 0, "year"),
        ("nam ngoai", -1, "year"),
        ("nam truoc", -1, "year"),
        ("tuan nay", 0, "week"),
        ("tuan truoc", -1, "week"),
        ("hom nay", 0, "day"),
    ):
        if re.search(rf"\b{words}\b", t):
            return base.relative(unit, by, today, cal, words)
    return None


PACK = Pack(
    code="vi",
    periods=periods,
    # "theo tháng 9" names a period (find_period), not a grain: a number after the word is left.
    grains=(
        re.compile(
            r"\b(?:theo|tung|hang|moi)\s+(?P<unit>ngay|tuan|thang|quy|nam)(?P<fiscal>\s+tai chinh)?"
            r"\b(?!\s*\d)"
        ),
    ),
    grain_units={"ngay": "day", "tuan": "week", "thang": "month", "quy": "quarter", "nam": "year"},
    comparisons=(
        (
            re.compile(r"\b(?:so voi |so )?cung (?:ky|thoi diem)(?: (?:nam truoc|nam ngoai))?\b"),
            SAME_PERIOD_LAST_YEAR,
        ),
        (re.compile(r"\b(?:yoy|y o y)\b"), SAME_PERIOD_LAST_YEAR),
        (
            re.compile(
                r"\bso voi (?:(?:thang|quy|nam|ky|tuan|ngay) (?:truoc|lien truoc)|nam ngoai|hom qua)\b"
            ),
            PREVIOUS_PERIOD,
        ),
        (re.compile(r"\b(?:mom|qoq|m o m|q o q)\b"), PREVIOUS_PERIOD),
    ),
    analyses=(
        (
            re.compile(r"\b(?:chiem (?:bao nhieu )?(?:phan tram|%)|chiem bao nhieu)(?!\w)"),
            SHARE_OF_TOTAL,
            None,
            True,
        ),
        (
            # (?!\w), not \b: "%" is no word character, so \b after it would never match.
            re.compile(
                r"\b(?:ty trong|co cau|phan tram (?:tren|so voi) tong|% (?:tren|so voi) tong)(?!\w)"
            ),
            SHARE_OF_TOTAL,
            None,
            False,
        ),
        (
            re.compile(
                r"\b(?:(?:luy ke|cong don) )?tu dau (nam|quy|thang|tuan)(?: tai chinh)?"
                r"(?: (?:den|toi) (?:nay|hien tai))?\b"
            ),
            TO_DATE,
            "",
            False,
        ),
        (re.compile(r"\b(ytd|qtd|mtd|wtd)\b"), TO_DATE, "", False),
        (re.compile(r"\b(?:luy ke|cong don)\b"), RUNNING_TOTAL, None, False),
    ),
    analysis_units={
        "nam": "year",
        "quy": "quarter",
        "thang": "month",
        "tuan": "week",
        "ytd": "year",
        "qtd": "quarter",
        "mtd": "month",
        "wtd": "week",
    },
    rank=re.compile(r"\b(?:top\s*)?(\d{1,3})\b.*?\b(?:nhieu nhat|cao nhat|it nhat|thap nhat)\b"),
    superlative=re.compile(r"\b(?:nhieu nhat|cao nhat|it nhat|thap nhat)\b"),
    ascending=re.compile(r"\b(?:it nhat|thap nhat)\b"),
    by=re.compile(r"\b(?:theo|tung|moi|chia theo)\s+"),
    # Only "theo": without accents "moi" is both "mỗi" (each) and "mới" (new, "mới nhất") and
    # "tung" is not only "từng", so they ask for a cut only when a dimension's name follows.
    # "theo kỳ …" ("theo kỳ được hỏi") speaks of the period, which find_period reads.
    unplaced_cut=re.compile(r"\b(?:chia )?theo\s+(?!ky\b)([^#\s]+(?:\s+[^#\s]+){0,2})"),
    filler=frozenset(
        "la cua cho toi ban biet xem hay va voi o tai trong nay do kia the nao bao nhieu hien "
        "hom qua tuan thang nam quy ky ngay gan nhat moi tong cong toan bo cac nhung mot hoac "
        "duoc da dang se theo tung chia so sanh truoc sau con thi sao nhu vay roi a nhe nhi".split()
    ),
    # "so với" and "so sánh" compare, they ask for no quantity of their own.
    quantity=re.compile(r"\b(?:so luong|bao nhieu|so(?! voi\b| sanh\b))\s+\w"),
    why=re.compile(r"\b(?:vi sao|tai sao|nguyen nhan|ly do|do dau)\b"),
    # Narrow on purpose: an ambiguous word such as "cũ" must not change the point-in-time reading.
    knowledge=(
        ("nhu da nop", "period_close"),
        ("so da nop", "period_close"),
        ("so da cong bo", "period_close"),
        ("ky da chot", "period_close"),
        ("nhu da chot", "period_close"),
        ("theo du lieu hien tai", "latest"),
        ("tinh tren du lieu hien tai", "latest"),
    ),
    comparators=(
        (r">=|tu|it nhat|toi thieu|khong duoi", ">="),
        (r"<=|toi da|khong qua|khong vuot qua", "<="),
        (r">|tren|hon|lon hon|cao hon|nhieu hon|vuot", ">"),
        (r"<|duoi|nho hon|thap hon|it hon", "<"),
        (r"=|bang", "="),
    ),
    between=(
        r"(?:tu|trong khoang|khoang) {low} (?:den|toi) {high}",
        r"{low} (?:-|den) {high}",
    ),
    units={"ty": 1e9, "trieu": 1e6, "nghin": 1e3, "ngan": 1e3, "k": 1e3},
)
