"""Personal data by country pack, checked by value (GEN-14, libs/pii): an invoice number is not
masked, a national id still is."""

from __future__ import annotations

import pytest

from libs import pii

pytestmark = [pytest.mark.unit]

ALL = ("GLOBAL", "VN", "US")


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("CCCD 079203001234", "CCCD"),
        ("Số CMND: 123456789", "CMND"),
        ("chứng minh nhân dân số 123456789", "CMND"),
        ("gọi 0912 345 678", "PHONE"),
        ("ĐT +84 28 3822 1234", "PHONE"),
        ("thẻ 4111 1111 1111 1111", "CREDIT_CARD"),
        ("IBAN DE89 3704 0044 0532 0130 00", "IBAN"),
        ("SSN 123-45-6789", "SSN"),
        ("a.b@example.vn", "EMAIL"),
    ],
)
def test_personal_data_is_masked(text: str, kind: str) -> None:
    masked, counts = pii.mask(text, packs=ALL)
    assert counts == {kind: 1}, masked


@pytest.mark.parametrize(
    "text",
    [
        # 12 digits whose first three are no province: an invoice, not a CCCD.
        "Hoá đơn số 123456789012",
        "Mã đơn 999203001234",
        # 9 digits with no identity word in front.
        "Đơn hàng 123456789",
        # 10 digits after a 0 with no Vietnamese prefix: an order code, not a phone.
        "mã 0123456789",
        "Lô 0600123456",
        # Fails Luhn; an SSN range never issued.
        "số 4111 1111 1111 1112",
        "SSN 666-45-6789",
    ],
)
def test_numbers_that_only_look_personal_are_kept(text: str) -> None:
    assert pii.mask(text, packs=ALL) == (text, {})


def test_a_pack_applies_only_where_it_is_chosen() -> None:
    assert pii.packs_for("vi-VN") == ("GLOBAL", "VN")
    assert pii.packs_for("en-US") == ("GLOBAL", "US")
    assert pii.packs_for("vi") == ("GLOBAL", "VN")
    # A locale nothing maps keeps the default set: never fewer packs than before.
    assert pii.packs_for("fr-FR") == pii.DEFAULT_PACKS
    assert pii.packs_for(None) == pii.DEFAULT_PACKS
    assert pii.packs_for("vi-VN", ["us"]) == ("GLOBAL", "US")
    # A US team's text keeps a 12-digit number that a Vietnamese one would mask.
    assert pii.mask("id 079203001234", packs=pii.packs_for("en-US"))[1] == {}
    assert pii.mask("id 079203001234", packs=pii.packs_for("vi-VN"))[1] == {"CCCD": 1}


def test_a_column_is_labelled_by_what_its_values_hold() -> None:
    ids = ["079203001234", "001185000123", None, ""]
    assert pii.personal_share(ids) == 1.0
    assert pii.column_label(ids) == "national_id"
    assert pii.column_label(["0912345678", "a@b.vn", "0987654321"]) == "phone"
    invoices = ["123456789012", "123456789013"]
    assert pii.personal_share(invoices) == 0.0 and pii.column_label(invoices) is None


def test_a_column_needs_a_share_and_distinct_values_by_how_sure_its_type_is() -> None:
    """RES-24: one Luhn-valid number among invoice numbers is chance, not a card column."""
    invoices = [f"INV-{i:05d}" for i in range(40)] + ["4111111111111111"]
    assert pii.column_decision(invoices).label is None
    cards = ["4111111111111111", "5500000000000004", "340000000000009", "note", "x", "y"]
    decided = pii.column_decision(cards)
    assert decided.label == "account_no" and decided.distinct_hits == 3
    emails = ["a@x.vn", "b@y.vn", "c", "d", "e", "f", "g", "h"]
    assert pii.column_decision(emails).label == "email"
    phones = ["0912345678", "0987654321", "abc", "def"]
    assert pii.column_decision(phones).label is None  # weak: half the values, and 3 distinct
    two_ids = ["079203001234", "001185000123"]
    assert pii.column_decision(two_ids).label == "national_id"
    assert pii.column_decision(["x@y.vn", "x@y.vn"]).label is None  # one distinct value
