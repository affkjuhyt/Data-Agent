"""Vietnamese text as the Understander matches it (tab 5 G5.1): normalised form and periods.

`norm` is the matching form of a phrase: NFC, lower case, accents removed (đ → d), punctuation
to spaces, whitespace collapsed. context.synonym.phrase_norm, glossary_term.term_norm and
value_index.label_norm hold the same form, so a question matches whichever way it was typed.

`find_period` reads the period a question names, relative to `today` (the question's as_of or the
day it was asked): a month, a quarter, a year, a day, or "this/last" month, quarter and year. It
returns the span it read and the half-open date range; None when the question names no period.
"""

from __future__ import annotations

import calendar
import datetime
import re
import unicodedata
from dataclasses import dataclass

_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4}


def norm(text: str) -> str:
    text = unicodedata.normalize("NFC", text).lower().replace("đ", "d")
    stripped = "".join(
        c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn"
    )
    return " ".join(re.sub(r"[^\w%/]+", " ", stripped).split())


@dataclass(frozen=True)
class FoundPeriod:
    start: datetime.date  # inclusive
    end: datetime.date  # exclusive
    label: str  # as written in the chip, e.g. "tháng 9/2026"
    span: str  # the normalised words it was read from


def _month(year: int, month: int) -> tuple[datetime.date, datetime.date]:
    start = datetime.date(year, month, 1)
    return start, start + datetime.timedelta(days=calendar.monthrange(year, month)[1])


def _quarter(year: int, q: int) -> tuple[datetime.date, datetime.date]:
    start = datetime.date(year, 3 * (q - 1) + 1, 1)
    end = datetime.date(year + 1, 1, 1) if q == 4 else datetime.date(year, 3 * q + 1, 1)
    return start, end


def _shift_month(day: datetime.date, by: int) -> tuple[int, int]:
    index = day.year * 12 + day.month - 1 + by
    return index // 12, index % 12 + 1


def find_period(question: str, today: datetime.date) -> FoundPeriod | None:
    t = norm(question)
    year_of = lambda y: int(y) if y else today.year  # noqa: E731

    if m := re.search(r"\bngay (\d{1,2})/(\d{1,2})/(\d{4})\b", t) or re.search(
        r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", t
    ):
        d = datetime.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        return FoundPeriod(d, d + datetime.timedelta(days=1), d.strftime("%d/%m/%Y"), m.group(0))
    if m := re.search(r"\bthang (\d{1,2})(?:\s*/\s*|\s+nam\s+)(\d{4})\b", t) or re.search(
        r"\bthang (\d{1,2})\b(?!\s*/)", t
    ):
        month, year = int(m.group(1)), year_of(m.group(2) if m.lastindex == 2 else None)
        if 1 <= month <= 12:
            start, end = _month(year, month)
            return FoundPeriod(start, end, f"tháng {month}/{year}", m.group(0))
    if m := re.search(r"\bquy (\d|i{1,3}|iv)(?:\s*/\s*|\s+nam\s+)?(\d{4})?\b", t):
        q = int(m.group(1)) if m.group(1).isdigit() else _ROMAN[m.group(1)]
        if 1 <= q <= 4:
            year = year_of(m.group(2))
            start, end = _quarter(year, q)
            return FoundPeriod(start, end, f"quý {q}/{year}", m.group(0))
    if m := re.search(r"\bnam (\d{4})\b", t):
        y = int(m.group(1))
        return FoundPeriod(
            datetime.date(y, 1, 1), datetime.date(y + 1, 1, 1), f"năm {y}", m.group(0)
        )

    relative = (
        ("thang nay", 0, "month"),
        ("thang truoc", -1, "month"),
        ("quy nay", 0, "quarter"),
        ("quy truoc", -1, "quarter"),
        ("nam nay", 0, "year"),
        ("nam ngoai", -1, "year"),
        ("nam truoc", -1, "year"),
    )
    for words, by, unit in relative:
        if re.search(rf"\b{words}\b", t):
            if unit == "month":
                y, mo = _shift_month(today, by)
                start, end = _month(y, mo)
                return FoundPeriod(start, end, f"tháng {mo}/{y}", words)
            if unit == "quarter":
                q, y = (today.month - 1) // 3 + 1, today.year
                q, y = (q - 1 + by) % 4 + 1, y + (q - 1 + by) // 4
                start, end = _quarter(y, q)
                return FoundPeriod(start, end, f"quý {q}/{y}", words)
            y = today.year + by
            return FoundPeriod(
                datetime.date(y, 1, 1), datetime.date(y + 1, 1, 1), f"năm {y}", words
            )
    if re.search(r"\bhom nay\b", t):
        return FoundPeriod(today, today + datetime.timedelta(days=1), "hôm nay", "hom nay")
    return None


def words_of(text: str, phrase: str) -> str:
    """The words of `text` whose normalised form is `phrase`, as the person wrote them (accents,
    case); the phrase itself when no run of words matches."""
    words = text.split()
    size = len(phrase.split())
    return next(
        (
            " ".join(words[i : i + size])
            for i in range(len(words) - size + 1)
            if norm(" ".join(words[i : i + size])) == phrase
        ),
        phrase,
    )
