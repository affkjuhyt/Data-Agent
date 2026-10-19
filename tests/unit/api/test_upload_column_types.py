"""The first read of an upload (P04) types each column. csv.reader yields text only, so a check
on Python int/float typed every CSV column "string" and no column was ever a date."""

from __future__ import annotations

import datetime

import pytest

from services.api.modules.ingest.routes import _analyse_upload, _column_type

pytestmark = [pytest.mark.unit]


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        (["1", "25", "-3"], "number"),
        (["1234.5", "0.25"], "number"),
        (["1.234.567", "3.109,2", "12,5"], "number"),
        (["1,234,567.89"], "number"),
        (["12,5%", "3%"], "percent"),
        (["2026-09-30", "2026-10-01"], "date"),
        (["30/09/2026", "1/10/2026"], "date"),
        ([datetime.datetime(2026, 9, 30), datetime.date(2026, 10, 1)], "date"),
        (["true", "False"], "boolean"),
        ([True, False], "boolean"),
        ([1, 2.5], "number"),
        (["001", "002"], "string"),
        (["Hà Nội", "12"], "string"),
        (["", None, "7"], "number"),
        (["", None], "string"),
    ],
)
def test_column_type(values: list[object], expected: str) -> None:
    assert _column_type(values) == expected


def test_csv_upload_types_its_columns() -> None:
    content = "chi_nhanh;so_du;ngay;ty_le\n001;1.234,5;30/09/2026;12,5%\n002;98,0;01/10/2026;3%\n"
    analysis = _analyse_upload(content.encode(), "balances.csv", None)
    types = {column["name"]: column["type"] for column in analysis["column_map"]}
    assert types == {"chi_nhanh": "string", "so_du": "number", "ngay": "date", "ty_le": "percent"}
