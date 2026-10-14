"""The pure rules of UC-04-02's scans: structure hash, personal data, history tables, join
candidates, sampling and the scan window."""

from __future__ import annotations

import datetime
import hashlib
from typing import Any

import pytest

from services.worker_agent.scan_rules import (
    JoinCandidate,
    history_of,
    join_candidates,
    matched_pattern,
    pii_label_for_name,
    sample_percent,
    schema_hash,
    window_allows,
)

pytestmark = [pytest.mark.unit]


def _col(name: str, kind: str = "string", **extra: Any) -> dict[str, Any]:
    return {"name": name, "normalized_type": kind, **extra}


def test_the_hash_is_the_one_the_first_scans_stored() -> None:
    # Unchanged tables of sources scanned before this change must keep their hash (step 2).
    columns = [
        {
            "name": "id",
            "ordinal": 1,
            "data_type": "integer",
            "normalized_type": "integer",
            "is_nullable": False,
            "is_key": True,
        }
    ]
    assert schema_hash(columns) == hashlib.sha256(repr(columns).encode()).hexdigest()
    assert schema_hash(columns) != schema_hash([{**columns[0], "data_type": "bigint"}])


@pytest.mark.req("AC-04-02-02")
@pytest.mark.parametrize(
    ("name", "label"),
    [
        ("so_cccd", "national_id"),
        ("CMND", "national_id"),
        ("so_dien_thoai", "phone"),
        ("mobile_phone", "phone"),
        ("email", "email"),
        ("so_tai_khoan", "account_no"),
        ("customer_name", "name"),
        ("ho_ten", "name"),
        ("dia_chi_thuong_tru", "address"),
        ("ngay_sinh", "other"),
        # Not personal: a product's or a branch's name, codes, amounts.
        ("product_name", None),
        ("name", None),
        ("branch_code", None),
        ("du_no", None),
        ("hotel", None),
    ],
)
def test_personal_columns_by_their_name(name: str, label: str | None) -> None:
    assert pii_label_for_name(name) == label


@pytest.mark.req("AC-04-02-02")
def test_a_pattern_needs_most_of_the_values() -> None:
    assert matched_pattern(100, {"phone": 85}).key == "phone"  # type: ignore[union-attr]
    assert matched_pattern(100, {"phone": 50, "cccd": 30}) is None
    assert matched_pattern(0, {"email": 0}) is None


@pytest.mark.req("AC-04-02-03")
def test_history_tables_by_their_dated_columns() -> None:
    scd2 = [_col("ma_kh"), _col("valid_from", "date"), _col("valid_to", "date")]
    assert history_of(scd2) == ("scd2", "valid_from")
    snapshot = [_col("ma_kh"), _col("ngay_chot", "date"), _col("du_no", "decimal")]
    assert history_of(snapshot) == ("snapshot", "ngay_chot")
    # A text column with the name is not a date: no history.
    assert history_of([_col("valid_from"), _col("valid_to")]) is None
    assert history_of([_col("id", "integer")]) is None


def _table(schema: str, name: str, columns: list[dict[str, Any]]) -> dict[str, Any]:
    return {"schema_name": schema, "table_name": name, "columns": columns}


def test_join_candidates_by_name_type_and_key() -> None:
    customers = _table("core", "customers", [_col("id", "integer", is_key=True), _col("email")])
    branches = _table("core", "chi_nhanh", [_col("ma_chi_nhanh", is_key=True)])
    loans = _table(
        "core",
        "loans",
        [
            _col("id", "integer", is_key=True),
            _col("customer_id", "integer"),
            _col("ma_chi_nhanh"),
            _col("officer_id", "integer"),
        ],
    )
    tables = [customers, branches, loans]
    assert join_candidates(tables, set(), limit=10) == [
        JoinCandidate(("core", "loans"), "customer_id", ("core", "customers"), "id"),
        JoinCandidate(("core", "loans"), "ma_chi_nhanh", ("core", "chi_nhanh"), "ma_chi_nhanh"),
    ]
    # A declared foreign key is the source's own: not proposed again as an inference.
    declared = {("core", "loans", "customer_id")}
    assert [c.child_column for c in join_candidates(tables, declared, limit=10)] == ["ma_chi_nhanh"]
    assert len(join_candidates(tables, set(), limit=1)) == 1
    # Types must agree.
    loans["columns"][1]["normalized_type"] = "string"
    assert [c.child_column for c in join_candidates(tables, set(), limit=10)] == ["ma_chi_nhanh"]


@pytest.mark.req("AC-04-02-06")
def test_a_table_over_ten_million_rows_is_sampled() -> None:
    assert sample_percent(10_000_000) is None
    assert sample_percent(25_000_000) == 4.0
    assert sample_percent(10**12) == 0.0001


def _at(day: int, hour: int, minute: int = 0) -> datetime.datetime:
    # 2026-10-12 is a Monday.
    return datetime.datetime(2026, 10, 12 + day, hour, minute)


@pytest.mark.req("AC-04-02-06")
def test_the_window_keeps_profiles_out_of_peak_hours() -> None:
    night = {"start": "22:00", "end": "05:00"}
    assert window_allows(night, _at(0, 23))
    assert window_allows(night, _at(1, 4, 59))
    assert not window_allows(night, _at(1, 5))
    assert not window_allows(night, _at(0, 10))
    # Weekdays only: Friday's window runs into Saturday morning; Saturday's does not open.
    weekdays = {**night, "days": ["mon", "tue", "wed", "thu", "fri"]}
    assert window_allows(weekdays, _at(5, 3))
    assert not window_allows(weekdays, _at(5, 23))
    assert not window_allows(weekdays, _at(6, 3))
    same_day = {"start": "01:00", "end": "05:00"}
    assert window_allows(same_day, _at(2, 1)) and not window_allows(same_day, _at(2, 5))


def test_no_window_or_one_that_cannot_be_read_sets_no_limit() -> None:
    for window in (None, {}, {"start": "25:00", "end": "05:00"}, {"start": "22:00"}, "22-05"):
        assert window_allows(window, _at(0, 10))
