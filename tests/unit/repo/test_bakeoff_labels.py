"""model/eval/docs/labelstudio/convert.py: a Label Studio export and the table sheets become the
bake-off gold (DOCUMENT_INTELLIGENCE_SPEC §11), refused whole when anything is off, and two
annotators' gold is compared region by region and cell by cell."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import pathlib
import sys
import xml.etree.ElementTree as ET
from typing import Any

import pytest

pytestmark = [pytest.mark.unit]

HERE = pathlib.Path(__file__).parents[3] / "model" / "eval" / "docs" / "labelstudio"
_spec = importlib.util.spec_from_file_location("bakeoff_convert", HERE / "convert.py")
assert _spec is not None and _spec.loader is not None
lc = importlib.util.module_from_spec(_spec)
# Registered first: the dataclasses of convert.py look their module up while they are made.
sys.modules[_spec.name] = lc
_spec.loader.exec_module(lc)

README_SHEET = [["Chỉ tiêu", "Năm 2024", "<"], ["^", "Q1", "Q2"], ["Doanh thu", "1.234,5", "987"]]


def test_the_labels_of_the_config_are_the_ones_converted() -> None:
    root = ET.parse(HERE / "page.xml").getroot()
    labels = [e.get("value") for e in root.iter("Label")]
    assert labels == list(lc.LABELS)
    names = {e.get("name") for e in root if e.get("name")}
    assert {"image", "label", "text", "order"} <= names


def test_a_sheet_with_merged_cells() -> None:
    cells = lc.table_cells(README_SHEET, 2, "t1")
    got = {(c.row, c.col): (c.rowspan, c.colspan, c.text, c.header) for c in cells}
    assert got == {
        (0, 0): (2, 1, "Chỉ tiêu", True),
        (0, 1): (1, 2, "Năm 2024", True),
        (1, 1): (1, 1, "Q1", True),
        (1, 2): (1, 1, "Q2", True),
        (2, 0): (1, 1, "Doanh thu", False),
        (2, 1): (1, 1, "1.234,5", False),
        (2, 2): (1, 1, "987", False),
    }
    html = lc.table_html(cells)
    assert html == (
        "<html><body><table><thead>"
        '<tr><td rowspan="2">Chỉ tiêu</td><td colspan="2">Năm 2024</td></tr>'
        "<tr><td>Q1</td><td>Q2</td></tr></thead>"
        "<tbody><tr><td>Doanh thu</td><td>1.234,5</td><td>987</td></tr></tbody>"
        "</table></body></html>"
    )


def test_a_block_spanning_rows_and_columns() -> None:
    cells = lc.table_cells([["A", "<", "B"], ["^", "+", "C"]], 0, "t")
    assert [(c.text, c.rowspan, c.colspan) for c in cells] == [
        ("A", 2, 2),
        ("B", 1, 1),
        ("C", 1, 1),
    ]


@pytest.mark.parametrize(
    ("rows", "problem"),
    [
        ([["<", "A"]], "'<' at A1 continues no cell"),
        ([["A", "<"], ["^", "<"]], "B2 should be '+'"),
        ([["A", "B"], ["C"]], "row 2 has 1 columns"),
        ([["A"]], "header=3 but the table has 1 rows"),
    ],
)
def test_a_wrong_sheet_names_the_cell(rows: list[list[str]], problem: str) -> None:
    with pytest.raises(lc.Invalid) as e:
        lc.table_cells(rows, 3 if problem.startswith("header") else 0, "t9")
    assert any(problem in p for p in e.value.problems), e.value.problems


def test_an_escaped_marker_is_text() -> None:
    (cell,) = lc.table_cells([["\\<"]], 0, "t")
    assert cell.text == "<"


def _region(
    rid: str, label: str, box: tuple[float, float, float, float], text: str, order: int
) -> list[dict[str, Any]]:
    common = {"id": rid, "to_name": "image", "original_width": 1000, "original_height": 2000}
    x, y, w, h = box
    out = [
        {
            **common,
            "type": "rectanglelabels",
            "from_name": "label",
            "value": {
                "x": x,
                "y": y,
                "width": w,
                "height": h,
                "rotation": 0,
                "rectanglelabels": [label],
            },
        },
        {**common, "type": "number", "from_name": "order", "value": {"number": order}},
    ]
    if text:
        out.append({**common, "type": "textarea", "from_name": "text", "value": {"text": [text]}})
    return out


def _export(result: list[dict[str, Any]], who: Any = 7) -> list[dict[str, Any]]:
    return [
        {
            "id": 1,
            "data": {
                "image": "/data/local-files/?d=nd52/pages/page-001.png",
                "doc_id": "nd52",
                "page": 1,
            },
            "annotations": [{"completed_by": who, "was_cancelled": False, "result": result}],
        }
    ]


def _docs(tmp_path: pathlib.Path) -> pathlib.Path:
    sheets = tmp_path / "docs" / "nd52" / "tables" / "7"
    sheets.mkdir(parents=True)
    (sheets / "t1.csv").write_text(
        "\n".join(",".join(f'"{v}"' for v in row) for row in README_SHEET) + "\n", encoding="utf-8"
    )
    return tmp_path / "docs"


PAGE = (
    _region("a", "Điều", (10, 10, 80, 5), "Điều 1. Phạm vi điều chỉnh", 2)
    + _region("b", "Bảng", (10, 20, 80, 20), "t1; header=2", 3)
    + _region("c", "Đầu trang", (10, 1, 80, 3), "CHÍNH PHỦ", 1)
    + _region("d", "Hình", (10, 50, 30, 20), "", 4)
)


def test_gold_is_written_in_reading_order(tmp_path: pathlib.Path) -> None:
    docs, out = _docs(tmp_path), tmp_path / "gold"
    assert lc.gold(_export(PAGE), docs, "7", out) == ["nd52"]
    layout = json.loads((out / "nd52" / "layout.json").read_text("utf-8"))
    (page,) = layout["pages"]
    assert [e["label"] for e in page["elements"]] == ["Đầu trang", "Điều", "Bảng", "Hình"]
    assert page["elements"][1]["bbox"] == [100.0, 200.0, 900.0, 300.0]
    assert page["elements"][2]["table"] == "t1"
    (table,) = json.loads((out / "nd52" / "tables.json").read_text("utf-8"))
    assert (table["rows"], table["cols"], table["header_rows"], table["page"]) == (3, 3, 2, 1)
    assert json.loads((out / "nd52" / "headings.json").read_text("utf-8")) == [
        {"level": 3, "label": "Điều", "text": "Điều 1. Phạm vi điều chỉnh", "page": 1}
    ]
    assert (out / "nd52" / "ocr" / "page-001.txt").read_text("utf-8") == (
        "CHÍNH PHỦ\nĐiều 1. Phạm vi điều chỉnh\nChỉ tiêu Năm 2024\nQ1 Q2\nDoanh thu 1.234,5 987\n"
    )


def test_text_is_nfc(tmp_path: pathlib.Path) -> None:
    decomposed = "Điều 1"  # "Điều" typed with combining circumflex and grave
    page = _region("a", "Điều", (10, 10, 80, 5), decomposed, 1)
    lc.gold(_export(page), tmp_path, "7", tmp_path / "gold")
    (heading,) = json.loads((tmp_path / "gold" / "nd52" / "headings.json").read_text("utf-8"))
    assert heading["text"] == "Điều 1"


@pytest.mark.parametrize(
    ("page", "problem"),
    [
        (
            _region("a", "Đoạn văn", (1, 1, 5, 5), "x", 1)
            + _region("b", "Đoạn văn", (1, 9, 5, 5), "y", 1),
            "reading order is [1, 1]",
        ),
        (_region("a", "Đoạn văn", (1, 1, 5, 5), "", 1), "Đoạn văn without its text"),
        (_region("a", "Paragraph", (1, 1, 5, 5), "x", 1), "unknown label 'Paragraph'"),
        (_region("a", "Bảng", (1, 1, 5, 5), "bảng 1", 1), "should name its sheet"),
        (_region("a", "Bảng", (1, 1, 5, 5), "t2; header=1", 1), "no sheet"),
        (_region("a", "Hình", (1, 1, 5, 5), "", 1), "sheet t1.csv is named by no table region"),
    ],
)
def test_anything_off_writes_nothing(
    tmp_path: pathlib.Path, page: list[dict[str, Any]], problem: str
) -> None:
    docs, out = _docs(tmp_path), tmp_path / "gold"
    with pytest.raises(lc.Invalid) as e:
        lc.gold(_export(page), docs, "7", out)
    assert any(problem in p for p in e.value.problems), e.value.problems
    assert not out.exists()


def test_the_annotator_is_found_by_id_or_email() -> None:
    by_email = _export(
        _region("a", "Đoạn văn", (1, 1, 5, 5), "x", 1), who={"id": 3, "email": "a@x.vn"}
    )
    assert len(lc.pages_of(by_email, "a@x.vn")[0]) == 1
    assert len(lc.pages_of(by_email, "3")[0]) == 1
    pages, problems = lc.pages_of(by_email, "9")
    assert pages == [] and "0 annotations by 9" in problems[0]


def test_two_annotators_agree_or_say_where_not(tmp_path: pathlib.Path) -> None:
    docs = _docs(tmp_path)
    sheets = docs / "nd52" / "tables"
    (sheets / "8").mkdir()
    (sheets / "8" / "t1.csv").write_text(
        (sheets / "7" / "t1.csv").read_text("utf-8").replace("987", "988"), encoding="utf-8"
    )
    lc.gold(_export(PAGE, 7), docs, "7", tmp_path / "a")
    lc.gold(_export(PAGE, 7), docs, "7", tmp_path / "same")
    assert lc.agree(tmp_path / "a", tmp_path / "same")["differences"] == 0

    other = [dict(r) for r in PAGE]
    for r in other:
        if r["id"] == "a" and r["type"] == "textarea":
            r["value"] = {"text": ["Điều 1. Phạm vi điều chinh"]}
    lc.gold(_export(other, 8), docs, "8", tmp_path / "b")
    report = lc.agree(tmp_path / "a", tmp_path / "b")
    found = report["docs"]["nd52"]
    assert any("text differs (1 edits" in f for f in found), found
    assert any("C3: '987' vs '988'" in f for f in found), found
    assert report["differences"] == 2


def test_the_manifest_keeps_what_a_person_wrote(tmp_path: pathlib.Path) -> None:
    (tmp_path / "nd52").mkdir()
    (tmp_path / "nd52" / "original.pdf").write_bytes(b"%PDF-1.4 x")
    out = lc.manifest(
        tmp_path, {"nd52": {"source": "vbpl.vn", "licence": "NĐ 52/2015", "kind": "pháp quy"}}
    )
    assert out["nd52"]["sha256"] == hashlib.sha256(b"%PDF-1.4 x").hexdigest()
    assert (out["nd52"]["source"], out["nd52"]["licence"], out["nd52"]["bytes"]) == (
        "vbpl.vn",
        "NĐ 52/2015",
        10,
    )
