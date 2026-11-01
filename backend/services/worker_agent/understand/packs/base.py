"""What a language pack gives the Understander (GEN-31), and the period arithmetic they share.

A pack is the words of one language for what the rule pass reads beside the Data Space's own
vocabulary: periods, a cut's grain, a comparison, a ranking, an analysis, the words that ask
rather than name ("bao nhiêu", "how many"), and the phrases that pick a knowledge mode. Every
pattern runs on `language.norm` text (no accents, punctuation as spaces); a period reader also
gets the `fold` form, which keeps an ISO date's dashes.

Labels (a chip's "tháng 9/2026") are not the pack's: they are the organisation's language (GEN-41),
whatever language the question was asked in.
"""

from __future__ import annotations

import calendar
import datetime
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from libs import clock, language

# An analysis from the start of a year, quarter, month or week (understand/analysis.py).
TO_DATE = "to_date"


@dataclass(frozen=True)
class FoundPeriod:
    start: datetime.date  # inclusive
    end: datetime.date  # exclusive
    label: str  # as written in the chip, e.g. "tháng 9/2026"
    span: str  # the normalised words it was read from


# (normalised text, folded text, today, calendar) → the period it names
PeriodReader = Callable[[str, str, datetime.date, clock.Calendar], FoundPeriod | None]


@dataclass(frozen=True)
class Pack:
    code: str
    periods: PeriodReader
    # A cut's grain: group `unit` is a word of `grain_units`; a group `fiscal` that matched makes
    # a quarter or a year the fiscal one.
    grains: tuple[re.Pattern[str], ...]
    grain_units: dict[str, str]
    # (pattern, compare_to)
    comparisons: tuple[tuple[re.Pattern[str], str], ...]
    # (pattern, kind, unit, weak): unit "" reads the unit from group 1 (`analysis_units`)
    analyses: tuple[tuple[re.Pattern[str], str, str | None, bool], ...]
    analysis_units: dict[str, str]
    # A ranking: group 1 the count when there is one; `ascending` names the bottom.
    rank: re.Pattern[str]
    superlative: re.Pattern[str]
    ascending: re.Pattern[str]
    # The words before a dimension that make it a cut ("theo", "by").
    by: re.Pattern[str]
    # A cut named with words no phrase placed: group 1 the words.
    unplaced_cut: re.Pattern[str]
    # Words that ask, point at time or join: they name no quantity.
    filler: frozenset[str]
    # A quantity asked for in words ("số khoản vay", "number of loans").
    quantity: re.Pattern[str]
    # A question that asks why.
    why: re.Pattern[str]
    # (phrase, knowledge mode)
    knowledge: tuple[tuple[str, str], ...]
    # A condition on a number ("trên 1 tỷ", "over 1 billion"): (words, op) before the number,
    # the words around a range's two numbers ({low}, {high}), and number words by multiplier.
    comparators: tuple[tuple[str, str], ...] = ()
    between: tuple[str, ...] = ()
    units: dict[str, float] = field(default_factory=dict)


def month_range(year: int, month: int) -> tuple[datetime.date, datetime.date]:
    start = datetime.date(year, month, 1)
    return start, start + datetime.timedelta(days=calendar.monthrange(year, month)[1])


def quarter_range(year: int, q: int) -> tuple[datetime.date, datetime.date]:
    start = datetime.date(year, 3 * (q - 1) + 1, 1)
    end = datetime.date(year + 1, 1, 1) if q == 4 else datetime.date(year, 3 * q + 1, 1)
    return start, end


def shift_month(day: datetime.date, by: int) -> tuple[int, int]:
    index = day.year * 12 + day.month - 1 + by
    return index // 12, index % 12 + 1


# The chip labels, in the language of the work in hand (libs/language.use_locale, GEN-41): the
# organisation's, whatever language the question was asked in. Vietnamese, else English.
def _english() -> bool:
    return language.current_language() != "vi"


_MONTHS_EN = (
    "January February March April May June July August September October November December"
).split()


def month_label(year: int, month: int) -> str:
    return f"{_MONTHS_EN[month - 1]} {year}" if _english() else f"tháng {month}/{year}"


def quarter_label(year: int, q: int) -> str:
    return f"Q{q} {year}" if _english() else f"quý {q}/{year}"


def year_label(year: int) -> str:
    return f"{year}" if _english() else f"năm {year}"


def day_label(day: datetime.date) -> str:
    return day.isoformat() if _english() else day.strftime("%d/%m/%Y")


def week_label(start: datetime.date, end: datetime.date) -> str:
    last = end - datetime.timedelta(days=1)
    if _english():
        return f"week {start.isoformat()} to {last.isoformat()}"
    return f"tuần {start:%d/%m} đến {last:%d/%m/%Y}"


def fiscal_year_label(number: int) -> str:
    return f"FY{number}"


def fiscal_quarter_label(number: int, q: int) -> str:
    return f"Q{q} FY{number}" if _english() else f"quý {q} FY{number}"


def today_label() -> str:
    return "today" if _english() else "hôm nay"


def month(year: int, m: int, span: str) -> FoundPeriod | None:
    if not 1 <= m <= 12:
        return None
    start, end = month_range(year, m)
    return FoundPeriod(start, end, month_label(year, m), span)


def quarter(year: int, q: int, span: str) -> FoundPeriod | None:
    if not 1 <= q <= 4:
        return None
    start, end = quarter_range(year, q)
    return FoundPeriod(start, end, quarter_label(year, q), span)


def year(y: int, span: str) -> FoundPeriod:
    return FoundPeriod(datetime.date(y, 1, 1), datetime.date(y + 1, 1, 1), year_label(y), span)


def day(d: datetime.date, span: str) -> FoundPeriod:
    return FoundPeriod(d, d + datetime.timedelta(days=1), day_label(d), span)


def relative(
    unit: str, by: int, today: datetime.date, cal: clock.Calendar, span: str
) -> FoundPeriod:
    """This (by 0) or the last (by -1) day, week, month, quarter, year or fiscal year."""
    if unit == "day":
        d = today + datetime.timedelta(days=by)
        found = day(d, span)
        return found if by else FoundPeriod(found.start, found.end, today_label(), span)
    if unit == "week":
        start, end = clock.week_of(today + datetime.timedelta(weeks=by), cal)
        return FoundPeriod(start, end, week_label(start, end), span)
    if unit == "month":
        y, m = shift_month(today, by)
        found_month = month(y, m, span)
        assert found_month is not None
        return found_month
    if unit == "quarter":
        q, y = (today.month - 1) // 3 + 1, today.year
        q, y = (q - 1 + by) % 4 + 1, y + (q - 1 + by) // 4
        found_quarter = quarter(y, q, span)
        assert found_quarter is not None
        return found_quarter
    if unit == "fiscal_year":
        number = clock.fiscal_year_of(today, cal) + by
        start, end = clock.fiscal_year(number, cal)
        return FoundPeriod(start, end, fiscal_year_label(number), span)
    return year(today.year + by, span)


def fiscal_year(number: int, cal: clock.Calendar, span: str) -> FoundPeriod:
    start, end = clock.fiscal_year(number, cal)
    return FoundPeriod(start, end, fiscal_year_label(number), span)


def fiscal_quarter(number: int, q: int, cal: clock.Calendar, span: str) -> FoundPeriod | None:
    if not 1 <= q <= 4:
        return None
    start, end = clock.fiscal_quarter(number, q, cal)
    return FoundPeriod(start, end, fiscal_quarter_label(number, q), span)


_ISO_DAY = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_ISO_MONTH = re.compile(r"\b(\d{4})-(\d{2})\b(?!-)")
_ISO_QUARTER = re.compile(r"\b(\d{4})-q([1-4])\b")


_ISO_RANGE = re.compile(r"^(\d{4}-\d{2}-\d{2})/(\d{4}-\d{2}-\d{2})$")
_ISO_YEAR = re.compile(r"^(\d{4})$")


def iso_answer(text: str) -> FoundPeriod | None:
    """A period a model answered in ISO form (GEN-31): "2026-09-01/2026-10-01" (end exclusive),
    "2026-09-30", "2026-09", "2026-Q3" or "2026"; None for anything else, so a model's words for
    a period never become one."""
    written = text.strip().lower()
    if m := _ISO_RANGE.match(written):
        try:
            start, end = (datetime.date.fromisoformat(g) for g in m.groups())
        except ValueError:
            return None
        if start >= end:
            return None
        if end - start == datetime.timedelta(days=1):
            return day(start, written)
        to = " to " if _english() else " đến "
        label = f"{day_label(start)}{to}{day_label(end - datetime.timedelta(days=1))}"
        return FoundPeriod(start, end, label, written)
    if m := _ISO_YEAR.match(written):
        return year(int(m.group(1)), written)
    # The whole answer, not a date inside other words.
    if re.fullmatch(r"\d{4}-\d{2}(?:-\d{2})?|\d{4}-q[1-4]", written):
        return iso_period(written)
    return None


def iso_period(folded: str) -> FoundPeriod | None:
    """A period written the ISO way in any language: 2026-09-30, 2026-09, 2026-Q3. The span is
    the normalised form, as the other readers' are ("2026 09 30")."""
    if m := _ISO_DAY.search(folded):
        try:
            d = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
        return day(d, " ".join(m.groups()))
    if m := _ISO_QUARTER.search(folded):
        return quarter(int(m.group(1)), int(m.group(2)), f"{m.group(1)} q{m.group(2)}")
    if m := _ISO_MONTH.search(folded):
        return month(int(m.group(1)), int(m.group(2)), " ".join(m.groups()))
    return None
