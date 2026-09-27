"""Reference periods, in the spellings the observation store uses.

`period` is a string column — '2024', '2024-Q2', '2024-06' (the v1 observation
schema; V2 adds D/W/I/P, P0-04 in docs/v2/05). Parsing it into a type rather
than passing the string around is what lets the formula layer refuse to divide
an end-of-quarter stock by an annual flow: the frequency has to be visible for
that check to exist at all.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class Frequency(Enum):
    ANNUAL = "A"
    QUARTERLY = "Q"
    MONTHLY = "M"


class PeriodError(ValueError):
    """A period that is not one of the store's three spellings."""


_ANNUAL = re.compile(r"^(\d{4})$")
_QUARTERLY = re.compile(r"^(\d{4})-Q([1-4])$")
_MONTHLY = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")


@dataclass(frozen=True, order=True)
class Period:
    year: int
    frequency: Frequency
    index: int = 0
    """1-4 for a quarter, 1-12 for a month, 0 for a year."""

    @classmethod
    def parse(cls, text: str) -> Period:
        text = text.strip()
        if match := _ANNUAL.match(text):
            return cls(int(match.group(1)), Frequency.ANNUAL)
        if match := _QUARTERLY.match(text):
            return cls(int(match.group(1)), Frequency.QUARTERLY, int(match.group(2)))
        if match := _MONTHLY.match(text):
            return cls(int(match.group(1)), Frequency.MONTHLY, int(match.group(2)))
        raise PeriodError(f"not a period: {text!r} (expected '2024', '2024-Q2' or '2024-06')")

    def __str__(self) -> str:
        if self.frequency is Frequency.ANNUAL:
            return str(self.year)
        if self.frequency is Frequency.QUARTERLY:
            return f"{self.year}-Q{self.index}"
        return f"{self.year}-{self.index:02d}"

    def previous(self) -> Period:
        """The period before this one, at the same frequency.

        Year-over-year is the comparison fiscal reporting makes, so a quarter
        steps back a whole year rather than one quarter: 2024-Q2 -> 2023-Q2.
        Comparing consecutive quarters of seasonal series is a different
        question, and one this method deliberately does not answer silently.
        """
        return Period(self.year - 1, self.frequency, self.index)
