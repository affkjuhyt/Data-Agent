"""Reading uploaded cells as the values they write (libs/tabular, GEN-02)."""

from __future__ import annotations

import datetime
import decimal
import unicodedata

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


def test_physical_names_fold_accents_prefix_digits_and_stay_unique() -> None:
    # GEN-13: "Số lượng" became s_l_ng, "2023" was refused, a CJK header became column_N only.
    assert tabular.physical_names(
        ["Số lượng", "2023", "Đơn giá (VNĐ)", "CustomerID", "销售额", "so luong", "Order Date"]
    ) == ["so_luong", "c_2023", "don_gia_vnd", "customerid", "column_5", "so_luong_6", "order_date"]
    assert len(tabular.physical_name("x" * 100, "c")) == 63


def test_the_pii_packs_follow_the_locale_unless_a_level_names_them() -> None:
    from libs.tabular import profile

    assert profile.resolve("vi-VN").pii_packs == ("GLOBAL", "VN")
    assert profile.resolve("en-US").pii_packs == ("GLOBAL", "US")
    assert profile.resolve("vi-VN", {"pii_packs": ["us", "VN"]}).pii_packs == ("GLOBAL", "US", "VN")
    with pytest.raises(ValueError, match="pii_packs"):
        profile.validate({"pii_packs": ["XX"]})


def _cp1258(text: str) -> bytes:
    """Text as Windows-1258 writes it: â ă ê ô ơ ư đ precomposed, the tone marks combining."""
    out: list[str] = []
    base = -1
    for ch in unicodedata.normalize("NFD", text):
        if ch in "̛̂̆" and base >= 0:
            out[base] = unicodedata.normalize("NFC", out[base] + ch)
        else:
            if not unicodedata.combining(ch):
                base = len(out)
            out.append(ch)
    return "".join(out).encode("cp1258")


CUSTOMERS = (
    "Mã KH,Họ tên,Địa chỉ\nKH001,Nguyễn Văn An,Hà Nội\nKH002,Trần Thị Bích,Đà Nẵng\n"
    "KH003,Lê Hoàng Phúc,Thành phố Hồ Chí Minh\n"
)


@pytest.mark.parametrize(
    ("raw", "encoding"),
    [
        (CUSTOMERS.encode("utf-8"), "utf-8-sig"),
        (CUSTOMERS.encode("utf-8-sig"), "utf-8-sig"),
        # Excel's "Unicode text" and "CSV UTF-16": a byte order mark, two bytes a character.
        (CUSTOMERS.encode("utf-16"), "utf-16"),
        (CUSTOMERS.encode("utf-16-le"), "utf_16_le"),
        # Excel's plain "CSV" on a Vietnamese Windows.
        (_cp1258(CUSTOMERS), "cp1258"),
        # Short, and so ambiguous: the organisation's language decides between the code pages.
        (_cp1258("Mã,Tên\n1,Đức\n"), "cp1258"),
    ],
)
def test_a_text_file_is_read_in_the_encoding_it_was_written_in(raw: bytes, encoding: str) -> None:
    text, found = tabular.decode_text(raw)
    assert found == encoding
    # Read back composed, as typed: cp1258's combining tone marks are folded in.
    assert text.lstrip("﻿") in (CUSTOMERS, "Mã,Tên\n1,Đức\n")
    assert text == unicodedata.normalize("NFC", text)


def test_a_stated_encoding_is_used_as_is_and_a_wrong_one_is_said() -> None:
    assert tabular.decode_text(_cp1258("Đức"), "cp1258") == ("Đức", "cp1258")
    with pytest.raises(ValueError):
        tabular.decode_text(b"\xff\xfe\x00", "utf-8")
    with pytest.raises(ValueError):
        tabular.decode_text(b"abc", "no-such-codec")


def test_binary_noise_is_not_text() -> None:
    with pytest.raises(ValueError):
        tabular.decode_text(bytes(range(256)) * 4)
