"""Header extraction for tables Docling returns with merged / multi-row headers.

The fixture holds real Docling grids (cell text, spans, header flags) taken
from the Niên giám thống kê 2025 and Bản tin nợ công PDFs; the expectations
were checked by hand against the PDF pages. Before the fix, `grid[0]` was
the header, so a merged label was repeated in every column and the real
column labels (years) ended up in the first data row.
"""
from __future__ import annotations

import json
import pathlib
from types import SimpleNamespace

import pytest

pytest.importorskip("docling")

from libs.ingestion.docling_parser import _build_parsed_tables

_FIXTURE = json.loads(
    (pathlib.Path(__file__).parent.parent / "fixtures" / "docling_table_grids.json").read_text(encoding="utf-8")
)


def _grid(table: dict) -> list[list[SimpleNamespace]]:
    nrows, ncols = table["nrows"], table["ncols"]
    grid = [
        [
            SimpleNamespace(text="", start_row_offset_idx=r, end_row_offset_idx=r + 1,
                            start_col_offset_idx=c, end_col_offset_idx=c + 1, column_header=False)
            for c in range(ncols)
        ]
        for r in range(nrows)
    ]
    for cell in table["cells"]:
        node = SimpleNamespace(
            text=cell["text"], start_row_offset_idx=cell["r0"], end_row_offset_idx=cell["r1"],
            start_col_offset_idx=cell["c0"], end_col_offset_idx=cell["c1"], column_header=cell["col_header"],
        )
        for r in range(cell["r0"], min(cell["r1"], nrows)):
            for c in range(cell["c0"], min(cell["c1"], ncols)):
                grid[r][c] = node
    return grid


def _parse(key: str) -> dict:
    table = SimpleNamespace(
        data=SimpleNamespace(grid=_grid(_FIXTURE[key]["table"])),
        caption_text=lambda doc: "",
    )
    (parsed,) = _build_parsed_tables(SimpleNamespace(tables=[table]))
    assert parsed is not None
    return parsed


@pytest.mark.parametrize("key", sorted(_FIXTURE))
def test_every_column_gets_a_distinct_name_and_no_row_is_lost(key: str) -> None:
    parsed = _parse(key)
    headers = parsed["data"]["headers"]
    assert len(headers) == _FIXTURE[key]["table"]["ncols"]
    named = [h for h in headers if h]
    assert len(named) == len(set(named)), f"duplicate column names: {headers}"
    assert all(len(row) == len(headers) for row in parsed["data"]["rows"])


def test_year_row_becomes_the_header_and_the_unit_moves_to_the_table_name() -> None:
    parsed = _parse("nien-giam-p105")  # population by province, 2020-2025
    assert parsed["data"]["headers"] == ["", "2020", "2022", "2023", "2024", "Sơ bộ Prel. 2025"]
    assert parsed["table_name"].endswith("(Nghìn người - Thous. persons)")
    first = parsed["data"]["rows"][0]
    assert first[0].startswith("CẢ NƯỚC") and first[1] == "97582,7"


def test_two_level_header_keeps_the_group_label_with_each_column() -> None:
    parsed = _parse("nien-giam-p104")  # population by sex / urban-rural
    assert parsed["data"]["headers"][2].endswith("Nam Male")
    assert "giới tính" in parsed["data"]["headers"][2]
    assert parsed["data"]["headers"][3].endswith("Nữ Female")
    assert parsed["data"]["rows"][0][0] == "2015"


def test_fertility_table_labels_urban_and_rural_columns() -> None:
    parsed = _parse("nien-giam-p126")
    headers = parsed["data"]["headers"]
    assert headers[1] == "Chung General"
    assert headers[2].endswith("Thành thị Urban") and headers[3].endswith("Nông thôn Rural")
    assert parsed["data"]["rows"][0][:2] == ["2015", "2,10"]


def test_currency_table_uses_the_dates_and_moves_the_shared_label_to_the_name() -> None:
    parsed = _parse("ban-tin-no-cong-p11")
    assert parsed["data"]["headers"][1:] == ["31/12/2019", "31/12/2020", "31/12/2021", "31/12/2022", "30/6/2023"]
    assert parsed["table_name"].endswith("(Ngày)")
    assert parsed["data"]["rows"][0][0] == "AUD"


def test_simple_header_row_is_unchanged() -> None:
    parsed = _parse("ban-tin-no-cong-p12")
    assert parsed["data"]["headers"] == ["Chỉ tiêu", "2018", "2019", "2020", "2021", "2022 (P)"]
    assert parsed["data"]["rows"][0][0].startswith("1.")


def test_a_numeric_first_row_is_data_not_the_header() -> None:
    # Docling flagged no header here; the old code named the columns after the
    # first data row ("6/2023 (P)", "VND", "3.358.098,36 ...").
    parsed = _parse("ban-tin-no-cong-p13")
    assert parsed["data"]["headers"][0] == "Column 1"
    assert parsed["data"]["rows"][0][0] == "6/2023 (P)"


def test_known_limit_a_misplaced_year_span_does_not_become_a_data_row() -> None:
    # Docling reads the "2025" of "Sơ bộ Prel. 2025" as one cell spanning all
    # columns, so the column names stay imperfect, but the row must not be
    # emitted as data and the real first row must lead.
    parsed = _parse("nien-giam-p600")
    assert parsed["data"]["rows"][0][0].startswith("CẢ NƯỚC")
    assert parsed["data"]["headers"][1:5] == ["2020", "2022", "2023", "2024"]


def test_a_flagged_row_of_figures_is_data_not_part_of_the_header() -> None:
    # AMRO's revenue table: Docling flagged the row of amounts under the years
    # as header too, giving columns like "2017 / 1,231".
    def cell(text: str, r: int, c: int, header: bool) -> SimpleNamespace:
        return SimpleNamespace(text=text, start_row_offset_idx=r, end_row_offset_idx=r + 1,
                               start_col_offset_idx=c, end_col_offset_idx=c + 1, column_header=header)

    grid = [
        [cell("Revenue", 0, 0, True), cell("2017", 0, 1, True), cell("2018", 0, 2, True)],
        [cell("Total", 1, 0, True), cell("1,231", 1, 1, True), cell("1,364", 1, 2, True)],
        [cell("Tax", 2, 0, False), cell("900", 2, 1, False), cell("950", 2, 2, False)],
    ]
    table = SimpleNamespace(data=SimpleNamespace(grid=grid), caption_text=lambda doc: "")
    (parsed,) = _build_parsed_tables(SimpleNamespace(tables=[table]))
    assert parsed["data"]["headers"] == ["Revenue", "2017", "2018"]
    assert parsed["data"]["rows"][0] == ["Total", "1,231", "1,364"]
