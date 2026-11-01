"""Reading uploaded cells as the values they write (libs/tabular, GEN-02)."""

from __future__ import annotations

import datetime
import decimal

import pytest

from libs import tabular

pytestmark = [pytest.mark.unit]

D = decimal.Decimal


@pytest.mark.parametrize(
    ("values", "mark"),
    [
        (["1.234,5", "7"], ","),  # both marks: the last one is the decimal
        (["1,234.5"], "."),
        (["1.234.567"], ","),  # a repeated mark groups thousands
        (["1,234,567"], "."),
        (["12,5"], ","),  # not three digits after the mark: a decimal
        (["0.123"], "."),  # a group never follows a lone zero
        (["1 234,5"], ","),  # spaces group
        (["12,5%"], ","),
    ],
)
def test_a_column_settles_its_decimal_mark(values: list[str], mark: str) -> None:
    assert tabular.decimal_mark(values) == mark


def test_a_column_of_ambiguous_figures_settles_nothing() -> None:
    # "1.234" is 1234 with "," decimals and 1.234 with ".": the upload's setting decides.
    assert tabular.decimal_mark(["1.234", "5.678", 12, None, ""]) is None


@pytest.mark.parametrize(
    ("text", "mark", "number"),
    [
        ("1.234,5", ",", D("1234.5")),
        ("1,234.5", ".", D("1234.5")),
        ("1,234", ".", D("1234")),
        ("1,234", ",", D("1.234")),
        ("(1.234)", ",", D("-1234")),
        ("-12", ".", D("-12")),
        ("1 234 567", ".", D("1234567")),
    ],
)
def test_a_figure_reads_with_its_columns_mark(
    text: str, mark: str, number: decimal.Decimal
) -> None:
    assert tabular.to_number(text, mark) == number


def test_a_percent_keeps_the_figure_as_written_and_a_word_is_not_a_number() -> None:
    assert tabular.to_percent("12,5%", ",") == D("12.5")
    assert tabular.to_number(0.1, ".") == D("0.1")  # a spreadsheet float, not 0.1000000000000000055
    for bad in ("abc", "1.2.3,4,5", "", True):
        with pytest.raises(ValueError):
            tabular.to_number(bad, ".")


def test_a_date_column_settles_day_or_month_first() -> None:
    assert tabular.date_order(["02/03/2024", "13/02/2024"]) == "dmy"
    assert tabular.date_order(["02/03/2024", "12/25/2024"]) == "mdy"
    assert tabular.date_order(["02/03/2024", "2024-02-03"]) is None


def test_a_date_is_read_in_its_columns_order_never_by_the_server() -> None:
    assert tabular.to_date("02/03/2024", "dmy") == datetime.date(2024, 3, 2)
    assert tabular.to_date("02/03/2024", "mdy") == datetime.date(2024, 2, 3)
    assert tabular.to_date("2024-02-03T10:00", "dmy") == datetime.date(2024, 2, 3)
    assert tabular.to_date(datetime.datetime(2024, 2, 3, 9), "mdy") == datetime.date(2024, 2, 3)
    with pytest.raises(ValueError):
        tabular.to_date("31/02/2024", "dmy")


def test_booleans_in_english_and_vietnamese() -> None:
    assert [tabular.to_boolean(v) for v in ("Yes", "có", "0", "Không")] == [
        True,
        True,
        False,
        False,
    ]
    with pytest.raises(ValueError):
        tabular.to_boolean("maybe")
