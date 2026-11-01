"""English (GEN-31): the same readings as the Vietnamese pack, for a question asked in English."""

from __future__ import annotations

import datetime
import re

from libs import clock
from services.worker_agent.semantic.analysis import RUNNING_TOTAL, SHARE_OF_TOTAL
from services.worker_agent.semantic.compare import PREVIOUS_PERIOD, SAME_PERIOD_LAST_YEAR
from services.worker_agent.understand.packs import base
from services.worker_agent.understand.packs.base import TO_DATE, FoundPeriod, Pack

_MONTHS = {
    name: number
    for number, names in enumerate(
        (
            ("january", "jan"),
            ("february", "feb"),
            ("march", "mar"),
            ("april", "apr"),
            ("may",),
            ("june", "jun"),
            ("july", "jul"),
            ("august", "aug"),
            ("september", "sep", "sept"),
            ("october", "oct"),
            ("november", "nov"),
            ("december", "dec"),
        ),
        start=1,
    )
    for name in names
}
_MONTH = "|".join(sorted(_MONTHS, key=len, reverse=True))
_ORDINAL = {
    "first": 1,
    "1st": 1,
    "second": 2,
    "2nd": 2,
    "third": 3,
    "3rd": 3,
    "fourth": 4,
    "4th": 4,
}
_ORDINALS = "|".join(_ORDINAL)
_FISCAL = r"(?:fiscal year|fiscal|fy)\s?(\d{4})"
_UNITS = {"day": "day", "week": "week", "month": "month", "quarter": "quarter", "year": "year"}


def periods(t: str, folded: str, today: datetime.date, cal: clock.Calendar) -> FoundPeriod | None:
    if m := re.search(rf"\b(?:q([1-4])|({_ORDINALS}) quarter(?: of)?) {_FISCAL}\b", t):
        q = int(m.group(1)) if m.group(1) else _ORDINAL[m.group(2)]
        if found := base.fiscal_quarter(int(m.group(3)), q, cal, m.group(0)):
            return found
    if m := re.search(rf"\b{_FISCAL}\b", t):
        return base.fiscal_year(int(m.group(1)), cal, m.group(0))
    for words, by in (
        ("this fiscal year", 0),
        ("current fiscal year", 0),
        ("last fiscal year", -1),
        ("previous fiscal year", -1),
    ):
        if re.search(rf"\b{words}\b", t):
            return base.relative("fiscal_year", by, today, cal, words)

    # "September 30, 2026", "30 September 2026"
    if m := re.search(rf"\b({_MONTH}) (\d{{1,2}}) (\d{{4}})\b", t):
        try:
            d = datetime.date(int(m.group(3)), _MONTHS[m.group(1)], int(m.group(2)))
        except ValueError:
            return None
        return base.day(d, m.group(0))
    if m := re.search(rf"\b(\d{{1,2}}) ({_MONTH}) (\d{{4}})\b", t):
        try:
            d = datetime.date(int(m.group(3)), _MONTHS[m.group(2)], int(m.group(1)))
        except ValueError:
            return None
        return base.day(d, m.group(0))
    # "September 2026", "in September" (this year); "may" alone is a verb, so it needs its year.
    if m := re.search(rf"\b({_MONTH})(?: of)? (\d{{4}})\b", t):
        return base.month(int(m.group(2)), _MONTHS[m.group(1)], m.group(0))
    m = re.search(rf"\b(?:in|for|during) ({_MONTH})\b(?! \d)", t)
    if m and m.group(1) != "may":
        return base.month(today.year, _MONTHS[m.group(1)], m.group(0))
    if m := re.search(rf"\b(?:q([1-4])|({_ORDINALS}) quarter)(?: of)?(?: (\d{{4}}))?\b", t):
        q = int(m.group(1)) if m.group(1) else _ORDINAL[m.group(2)]
        return base.quarter(int(m.group(3)) if m.group(3) else today.year, q, m.group(0))
    if m := re.search(r"\b(?:in|for|during|year|of) (\d{4})\b", t):
        return base.year(int(m.group(1)), m.group(0))

    for words, by, unit in (
        ("this month", 0, "month"),
        ("current month", 0, "month"),
        ("last month", -1, "month"),
        ("previous month", -1, "month"),
        ("this quarter", 0, "quarter"),
        ("current quarter", 0, "quarter"),
        ("last quarter", -1, "quarter"),
        ("previous quarter", -1, "quarter"),
        ("this year", 0, "year"),
        ("current year", 0, "year"),
        ("last year", -1, "year"),
        ("previous year", -1, "year"),
        ("this week", 0, "week"),
        ("last week", -1, "week"),
        ("previous week", -1, "week"),
        ("today", 0, "day"),
        ("yesterday", -1, "day"),
    ):
        if re.search(rf"\b{words}\b", t):
            return base.relative(unit, by, today, cal, words)
    return None


PACK = Pack(
    code="en",
    periods=periods,
    grains=(
        re.compile(
            r"\b(?:by|per|each)\s+(?P<fiscal>fiscal\s+)?(?P<unit>day|week|month|quarter|year)\b"
            r"(?!\s*\d)"
        ),
        re.compile(r"\b(?P<unit>daily|weekly|monthly|quarterly|yearly|annually|annual)\b"),
    ),
    grain_units={
        **_UNITS,
        "daily": "day",
        "weekly": "week",
        "monthly": "month",
        "quarterly": "quarter",
        "yearly": "year",
        "annually": "year",
        "annual": "year",
    },
    comparisons=(
        (
            re.compile(
                r"\b(?:(?:vs|versus|compared (?:to|with)) )?(?:the )?same (?:period|time|month"
                r"|quarter|week) (?:last|previous|a|the previous) year\b"
            ),
            SAME_PERIOD_LAST_YEAR,
        ),
        (re.compile(r"\b(?:year over year|year on year|yoy)\b"), SAME_PERIOD_LAST_YEAR),
        (
            re.compile(
                r"\b(?:vs|versus|compared (?:to|with)|against) (?:the )?(?:last|previous|prior)"
                r" (?:day|week|month|quarter|year|period)\b"
            ),
            PREVIOUS_PERIOD,
        ),
        (re.compile(r"\b(?:vs|versus|compared (?:to|with)) yesterday\b"), PREVIOUS_PERIOD),
        (
            re.compile(r"\b(?:month over month|quarter over quarter|mom|qoq)\b"),
            PREVIOUS_PERIOD,
        ),
    ),
    analyses=(
        (
            # Only the total named: "share" alone is in metric names ("market share").
            re.compile(
                r"\b(?:share of (?:the )?total|(?:percent|percentage) of (?:the )?total"
                r"|contribution to (?:the )?total)\b|(?<!\w)% of (?:the )?total\b"
            ),
            SHARE_OF_TOTAL,
            None,
            False,
        ),
        (re.compile(r"\b(year|quarter|month|week) to date\b"), TO_DATE, "", False),
        (
            re.compile(
                r"\bsince the (?:start|beginning) of the (?:fiscal )?(year|quarter|month|week)\b"
            ),
            TO_DATE,
            "",
            False,
        ),
        (re.compile(r"\b(ytd|qtd|mtd|wtd)\b"), TO_DATE, "", False),
        (
            re.compile(r"\b(?:running total|cumulative|cumulatively|accumulated)\b"),
            RUNNING_TOTAL,
            None,
            False,
        ),
    ),
    analysis_units={**_UNITS, "ytd": "year", "qtd": "quarter", "mtd": "month", "wtd": "week"},
    rank=re.compile(r"\b(?:top|bottom|best|worst)\s*(\d{1,3})\b"),
    superlative=re.compile(
        r"\b(?:highest|most|largest|biggest|greatest|top|lowest|least|smallest|fewest|bottom)\b"
    ),
    ascending=re.compile(r"\b(?:bottom|worst|lowest|least|smallest|fewest)\b"),
    by=re.compile(r"\b(?:by|per|each|across|split by|broken down by)\s+"),
    unplaced_cut=re.compile(
        r"\b(?:broken down |split )?by\s+(?!period\b|the period\b)([^#\s]+(?:\s+[^#\s]+){0,2})"
    ),
    filler=frozenset(
        "what whats is was were are be of for in on at to from a an the and or me us my our show "
        "tell give list please can could would you how much many total all each every this that "
        "these those period day week month quarter year today yesterday last previous next "
        "current latest recent so far by per compared versus vs with than it its there do does "
        "did as about".split()
    ),
    quantity=re.compile(r"\b(?:number of|how many|count of)\s+\w"),
    why=re.compile(r"\b(?:why|what caused|what drove|reason for|reasons for|explain)\b"),
    knowledge=(
        ("as filed", "period_close"),
        ("as submitted", "period_close"),
        ("as published", "period_close"),
        ("as closed", "period_close"),
        ("on current data", "latest"),
        ("with current data", "latest"),
        ("using current data", "latest"),
    ),
    comparators=(
        (r">=|at least|no less than", ">="),
        (r"<=|at most|up to|no more than", "<="),
        (r">|over|above|more than|greater than|exceeding|higher than", ">"),
        (r"<|under|below|less than|lower than", "<"),
        (r"=|equal to|equals", "="),
    ),
    between=(r"between {low} and {high}", r"from {low} to {high}"),
    units={
        "billion": 1e9,
        "bn": 1e9,
        "million": 1e6,
        "mn": 1e6,
        "m": 1e6,
        "thousand": 1e3,
        "k": 1e3,
    },
)
