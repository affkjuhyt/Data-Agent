"""DI-12: a number asked of a document is read from the table cell whose row and column the
question names (§7), or asked back when two cells fit as well with other numbers."""

from __future__ import annotations

import decimal
import uuid

import pytest

from services.worker_agent.documents import table_query as tq

pytestmark = [pytest.mark.unit]

D = decimal.Decimal


def _grid(rows: list[list[str | int | None]], *, header_rows: int = 1, spans=None) -> tq.Grid:
    cells = []
    for r, row in enumerate(rows):
        for c, v in enumerate(row):
            if v is None:
                continue
            value = D(v) if isinstance(v, int) else None
            span = (spans or {}).get((r, c), 1)
            cells.append(
                tq.Cell(r, c, 1, span, str(v), value, "VND" if value else None, r < header_rows,
                        1, {"l": c, "t": r, "r": c + 1, "b": r + 1})
            )  # fmt: skip
    return tq.Grid(uuid.uuid4(), uuid.uuid4(), "Dư nợ (triệu đồng)", header_rows, cells)


DEBT = _grid(
    [
        ["Chi nhánh", "Năm 2024", None],
        [None, "Kế hoạch", "Thực hiện"],
        ["Hà Nội", 1200, 1300],
        ["TP. Hồ Chí Minh", 900, 950],
    ],
    header_rows=2,
    spans={(0, 1): 2},
)


def test_the_cell_where_the_named_row_and_column_meet() -> None:
    found = tq.answer_from_grids("Dư nợ thực hiện năm 2024 của chi nhánh Hà Nội", [DEBT])
    assert isinstance(found, tq.Found)
    assert (found.value, found.row_label, found.column_label) == (
        D(1300),
        "Hà Nội",
        "Năm 2024 > Thực hiện",
    )
    assert (found.cell.row, found.cell.col, found.cell.page) == (2, 2, 1)
    assert found.cell.bbox == {"l": 2, "t": 2, "r": 3, "b": 3}


def test_a_question_without_diacritics_finds_the_same_cell() -> None:
    found = tq.answer_from_grids("du no ke hoach nam 2024 tp ho chi minh", [DEBT])
    assert isinstance(found, tq.Found) and found.value == D(900)


def test_two_cells_as_good_with_other_numbers_are_asked_back() -> None:
    # "năm 2024" names both columns under it, "Hà Nội" one row: plan or actual?
    found = tq.answer_from_grids("Dư nợ năm 2024 của Hà Nội", [DEBT])
    assert isinstance(found, tq.Ambiguous)
    assert sorted(o.value for o in found.options) == [D(1200), D(1300)]


def test_one_value_column_needs_no_column_named() -> None:
    plain = _grid([["Chi nhánh", "Dư nợ"], ["Hà Nội", 1200], ["Đà Nẵng", 400]])
    found = tq.answer_from_grids("Đà Nẵng bao nhiêu", [plain])
    assert isinstance(found, tq.Found) and found.value == D(400)


def test_nothing_named_is_not_found() -> None:
    assert tq.answer_from_grids("Lợi nhuận của Cần Thơ", [DEBT]) is None
    assert tq.answer_from_grids("Hà Nội", []) is None


def test_a_figure_is_said_in_vietnamese_marks() -> None:
    assert tq.spoken(D("1234567.5"), "VND") == "1.234.567,5 đồng"
    assert tq.spoken(D("12.5"), "percent") == "12,5%"
    assert tq.spoken(D(1300), None) == "1.300"
