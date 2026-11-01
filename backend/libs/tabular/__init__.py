"""Reading the cells of an uploaded table as the values they write (GEN-02).

One reader for the api's first look at an upload and for the worker that writes the table, so a
value the review screen showed as a number is the number the table holds. A column is read as a
whole: whether "," or "." marks its decimals, and whether its dates put the day or the month
first, is settled by the values that cannot be read the other way ("1.234,5", "13/02/2024"), then
by what the upload says, then by a default. Nothing is left for the database to guess: a date
goes in as ISO, so the server's DateStyle never decides which day "02/03/2024" is.
"""

from __future__ import annotations

import csv
import datetime
import decimal
import io
import re
from collections.abc import Iterable
from typing import Any, Literal

DateOrder = Literal["dmy", "mdy"]
# The order an ambiguous column (every value has both fields <= 12) is read in when neither the
# column nor the upload says: day first, the convention of the market the product ships to.
# GEN-11 replaces it with the upload's locale.
DEFAULT_DATE_ORDER: DateOrder = "dmy"
DEFAULT_DECIMAL_MARK = "."

TRUE_WORDS = frozenset({"true", "1", "yes", "y", "có", "co", "đúng"})
FALSE_WORDS = frozenset({"false", "0", "no", "n", "không", "khong", "sai"})

# Grouping a figure may carry besides "." and ",": spaces, no-break spaces, the Swiss apostrophe.
_GROUP_SPACES = re.compile("[\\s\\u00a0\\u202f']")
_NUMBER = re.compile(r"[+-]?(?:\d+(?:[.,]\d+)*(?:[.,]\d*)?|[.,]\d+)")
_ISO_DATE = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T].*)?")
_NUMERIC_DATE = re.compile(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})")


def csv_rows(
    text: str, header_row: int | None = None, *, from_header: bool = True
) -> list[list[str]]:
    """The rows of a CSV text, from its header row on (1-based) unless `from_header` is False.
    The delimiter is sniffed from the header row on: a title line above it ("Báo cáo doanh thu
    quý 1") has none to find, and the sniffer would fall back to commas."""
    lines = text.splitlines(keepends=True)
    body = "".join(lines[max((header_row or 1) - 1, 0) :])
    try:
        dialect: type[csv.Dialect] | csv.Dialect = csv.Sniffer().sniff(
            body[:8192], delimiters=",;\t|"
        )
    except csv.Error:
        dialect = csv.excel
    return [list(row) for row in csv.reader(io.StringIO(body if from_header else text), dialect)]


def xls_row(worksheet: Any, index: int, datemode: int) -> list[Any]:
    """A legacy .xls row with its date cells as dates: xlrd hands them over as serial numbers,
    which would be typed and stored as figures (45123.0)."""
    import xlrd

    return [
        xlrd.xldate_as_datetime(cell.value, datemode)
        if cell.ctype == xlrd.XL_CELL_DATE
        else cell.value
        for cell in worksheet.row(index)
    ]


def _compact(text: str) -> tuple[str, bool]:
    """The figure without grouping spaces, and whether it was written negative in parentheses."""
    s = _GROUP_SPACES.sub("", str(text).strip())
    negative = s.startswith("(") and s.endswith(")")
    return (s[1:-1] if negative else s), negative


def _settles(figure: str) -> str | None:
    """The decimal mark one figure can only have been written with, None when either fits."""
    unsigned = figure.lstrip("+-")
    if "." in unsigned and "," in unsigned:
        return unsigned[max(unsigned.rfind("."), unsigned.rfind(","))]
    for mark in (".", ","):
        if unsigned.count(mark) > 1:
            return "," if mark == "." else "."  # a repeated mark groups thousands
        if unsigned.count(mark) == 1:
            whole, fraction = unsigned.split(mark)
            # A group is exactly three digits after one to three, the first not zero.
            if len(fraction) != 3 or not whole or len(whole) > 3 or whole.startswith("0"):
                return mark
    return None


def decimal_mark(values: Iterable[Any]) -> str | None:
    """The decimal mark a column's text figures settle, None when none of them does."""
    for value in values:
        if value in (None, "") or isinstance(value, int | float | decimal.Decimal):
            continue
        figure, _ = _compact(str(value).rstrip("%").strip())
        if _NUMBER.fullmatch(figure) and (mark := _settles(figure)):
            return mark
    return None


def to_number(value: Any, mark: str) -> decimal.Decimal:
    """The figure `value` writes, with `mark` as its decimal mark. Raises ValueError."""
    if isinstance(value, bool):
        raise ValueError(f"not a number: {value!r}")
    if isinstance(value, int | decimal.Decimal):
        return decimal.Decimal(value)
    if isinstance(value, float):
        return decimal.Decimal(repr(value))
    figure, negative = _compact(str(value))
    if not _NUMBER.fullmatch(figure):
        raise ValueError(f"not a number: {value!r}")
    group = "," if mark == "." else "."
    plain = figure.replace(group, "").replace(mark, ".")
    try:
        number = decimal.Decimal(plain)
    except decimal.InvalidOperation as exc:
        raise ValueError(f"not a number: {value!r}") from exc
    return -number if negative else number


def to_percent(value: Any, mark: str) -> decimal.Decimal:
    """A percent as written ("12,5%" → 12.5): the column keeps the figure its readers saw."""
    text = value if not isinstance(value, str) else value.strip().rstrip("%").strip()
    return to_number(text, mark)


def date_order(values: Iterable[Any]) -> DateOrder | None:
    """Day or month first, from the first numeric date in the column that only fits one."""
    for value in values:
        if not isinstance(value, str):
            continue
        found = _NUMERIC_DATE.fullmatch(value.strip())
        if found is None:
            continue
        first, second = int(found.group(1)), int(found.group(2))
        if first > 12 >= second:
            return "dmy"
        if second > 12 >= first:
            return "mdy"
    return None


def to_date(value: Any, order: DateOrder) -> datetime.date:
    """The date `value` writes: a date or datetime, ISO, or numeric in `order`. Raises ValueError."""
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    text = str(value).strip()
    if found := _ISO_DATE.fullmatch(text):
        year, month, day = (int(found.group(i)) for i in (1, 2, 3))
    elif found := _NUMERIC_DATE.fullmatch(text):
        first, second, year = (int(found.group(i)) for i in (1, 2, 3))
        day, month = (first, second) if order == "dmy" else (second, first)
    else:
        raise ValueError(f"not a date: {value!r}")
    return datetime.date(year, month, day)


def to_boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    word = str(value).strip().lower()
    if word in TRUE_WORDS:
        return True
    if word in FALSE_WORDS:
        return False
    raise ValueError(f"not a boolean: {value!r}")
