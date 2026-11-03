"""The schema scan keeps personal data out of the catalogue by name and by value (GEN-14), and a
column named like a product or an amount is sampled."""

from __future__ import annotations

import pytest

from services.worker_agent.schema_scans import _is_sensitive_column, _redact_personal

pytestmark = [pytest.mark.unit]


@pytest.mark.parametrize(
    "name",
    [
        "email",
        "phone_number",
        "customer_name",
        "FullName",
        "ho_ten",
        "ten_khach",
        "account_no",
        "tax_id",
        "tax_code",
        "ma_so_thue",
        "cccd",
        "home_address",
        "date_of_birth_dob",
        "name",
        "ten",
        "account",
        "so_tai_khoan",
        "ten_kh",
        "ten_nv",
        "customer_type_name",
        # A person's word beside "account" keeps it personal, description or not.
        "customer_account_description",
    ],
)
def test_a_name_that_says_personal_is_never_sampled(name: str) -> None:
    assert _is_sensitive_column(name)


@pytest.mark.parametrize(
    "name",
    [
        "product_name",
        "account_type",
        "tax_amount",
        "invoice_no",
        "region_name",
        "ten_san_pham",
        "loai_tai_khoan",
        "tien_thue",
        # AdventureWorks' general-ledger accounts ("Trade Sales", "Salaries"): a description of
        # what an account is. Its values are still checked for personal data (_redact_personal).
        "account_description",
    ],
)
def test_a_name_that_does_not_is_sampled(name: str) -> None:
    assert not _is_sensitive_column(name)


def test_values_that_hold_personal_data_are_masked_and_labelled() -> None:
    profiles: dict[tuple[str, str, str], dict[str, object]] = {
        ("s", "t", "ma_kh"): {
            "top_values": [
                {"value": "079203001234", "count": 1},
                {"value": "001185000123", "count": 1},
            ],
            "category_values": [{"value": "079203001234", "count": 1}],
            "min_value": "001185000123",
            "max_value": "079203001234",
        },
        ("s", "t", "so_hoa_don"): {
            "top_values": [{"value": "123456789012", "count": 1}],
            "category_values": [{"value": "123456789012", "count": 1}],
            "min_value": "123456789012",
            "max_value": "123456789012",
        },
    }
    _redact_personal(profiles, ("GLOBAL", "VN"))
    ids, invoices = profiles[("s", "t", "ma_kh")], profiles[("s", "t", "so_hoa_don")]
    assert ids["pii_label"] == "national_id"
    assert ids["top_values"] == [{"value": "***", "count": 1}] * 2
    assert ids["category_values"] == [] and ids["min_value"] is None
    assert "pii_label" not in invoices and invoices["max_value"] == "123456789012"
