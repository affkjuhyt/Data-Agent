"""DI-17: an extraction schema's records as rows, and the count tool's statement over them.

The rules that decide what a row holds (typed values, checked or person-fixed only, provenance),
when a load rewrites the table or only upserts, and the SQL count_documents builds from the
schema's own columns, never from the model's text."""

from __future__ import annotations

import datetime
import decimal
import types
import uuid
from typing import Any

import pytest

from libs.tabular.profile import resolve
from services.worker_agent.agentic.tools.extracted import (
    Condition,
    CountDocumentsArgs,
    Refused,
    cited_sql,
    count_sql,
    coverage_text,
    pick,
)
from services.worker_agent.documents import extracted_dataset as ed
from services.worker_agent.documents.extraction import pin

pytestmark = [pytest.mark.unit, pytest.mark.req("DI-17")]

VI = resolve("vi-VN")
FIELDS: list[dict[str, Any]] = [
    {"name": "contract_no", "type": "text", "required": True},
    {"name": "penalty_rate", "type": "percent", "description": "Mức phạt vi phạm"},
    {"name": "amount", "type": "number", "required": True, "target_column": "Giá trị"},
    {"name": "signed_on", "type": "date"},
    {"name": "national_id", "type": "number", "pii": True},
    {"name": "doc_no", "type": "text"},
]


@pytest.mark.parametrize(
    ("kind", "raw", "expected"),
    [
        ("number", "500.000.000 đồng", decimal.Decimal("500000000")),
        ("number", "1,234.5 USD", decimal.Decimal("1234.5")),
        ("percent", "12,5%", decimal.Decimal("12.5")),
        ("percent", "8%", decimal.Decimal("8")),
        ("date", "05/03/2026", datetime.date(2026, 3, 5)),
        ("date", "2026-04-10", datetime.date(2026, 4, 10)),
        ("boolean", "có", True),
        ("text", "  HD-01 ", "HD-01"),
        ("number", "", None),
    ],
)
def test_a_value_reads_as_its_fields_type(kind: str, raw: str, expected: Any) -> None:
    assert ed.typed({"name": "x", "type": kind}, raw, VI) == expected


@pytest.mark.parametrize(
    ("kind", "raw"),
    [
        ("number", "khoảng 5 tỷ"),
        ("number", "năm trăm triệu"),
        ("date", "mồng năm tháng sáu"),
        ("boolean", "tuỳ trường hợp"),
    ],
)
def test_a_value_that_does_not_read_as_its_type_is_refused_not_guessed(kind: str, raw: str) -> None:
    with pytest.raises(ValueError):
        ed.typed({"name": "x", "type": kind}, raw, VI)
    assert ed.invalid_fields([{"name": "x", "type": kind}], {"x": raw}, VI) == ["x"]


def test_columns_carry_provenance_then_the_fields() -> None:
    columns = ed.columns_of(FIELDS)
    names = [c.name for c in columns]
    assert names[: len(ed.PROVENANCE)] == [n for n, _, _ in ed.PROVENANCE]
    by_field = {c.field: c for c in columns if c.field}
    assert by_field["amount"].name == "gia_tri" and by_field["amount"].type == "numeric"
    assert by_field["penalty_rate"].type == "numeric"
    assert by_field["signed_on"].type == "date"
    # A personal-data field is stored masked, as text, labelled.
    assert by_field["national_id"].type == "text" and by_field["national_id"].pii
    # A field named like a provenance column does not overwrite it.
    assert by_field["doc_no"].name == "f_doc_no"


def _source(**kw: Any) -> ed.Source:
    base: dict[str, Any] = {
        "record_id": uuid.uuid4(),
        "document_id": uuid.uuid4(),
        "doc_no": "HD-01",
        "title": "Hợp đồng HD-01",
        "values": {
            "contract_no": "HD-01",
            "penalty_rate": "8%",
            "amount": "500.000.000 đồng",
            "signed_on": "mồng năm tháng sáu",
            "national_id": "•••••••••567",
        },
        "confidence": {
            "contract_no": 1.0,
            "penalty_rate": 1.0,
            "amount": 0.5,
            "signed_on": 1.0,
            "national_id": 1.0,
        },
        "spans": [
            {"field": "contract_no", "chunk_id": "c1", "page_from": 1, "quote": "số HD-01"},
            {"field": "penalty_rate", "chunk_id": "c1", "page_from": 2, "quote": "phạt 8%"},
            {"field": "amount", "chunk_id": "c1", "page_from": 2, "quote": None},
        ],
        "effective_from": datetime.date(2026, 1, 1),
        "effective_to": None,
    }
    base.update(kw)
    return ed.Source(**base)


def test_a_row_keeps_checked_typed_values_and_says_where_each_came_from() -> None:
    columns = ed.columns_of(FIELDS)
    source = _source()
    row = dict(
        zip([c.name for c in columns], ed.row_of(source, columns, FIELDS, 3, VI), strict=False)
    )
    assert row["document_id"] == str(source.document_id)
    assert row["record_id"] == str(source.record_id)
    assert row["schema_version"] == 3
    assert row["contract_no"] == "HD-01"
    assert row["penalty_rate"] == "8"
    # Unchecked (its quote was not found): not loaded.
    assert row["gia_tri"] is None
    # Checked but not a date: NULL, never a guess.
    assert row["signed_on"] is None
    assert row["national_id"] == "•••••••••567"
    assert row["confidence"] == 1.0
    assert {s["field"] for s in row["source_spans"]} == {"contract_no", "penalty_rate"}
    assert row["extractor_version"] == ed.EXTRACTION_VERSION
    assert row["effective_from"] == "2026-01-01"


def test_a_value_a_person_fixed_is_loaded_whatever_the_models_confidence() -> None:
    columns = ed.columns_of(FIELDS)
    source = _source(confirmed=frozenset({"amount"}))
    row = dict(
        zip([c.name for c in columns], ed.row_of(source, columns, FIELDS, 1, VI), strict=False)
    )
    assert row["gia_tri"] == "500000000"


def _wanted(sources: list[ed.Source], loaded: dict[uuid.UUID, tuple[uuid.UUID, bool]]) -> Any:
    schema = uuid.uuid4()
    return ed.Wanted(
        org_id=uuid.uuid4(),
        collection_id=uuid.uuid4(),
        data_space_id=uuid.uuid4(),
        collection_code="hop-dong",
        collection_name="Hợp đồng",
        schema_id=schema,
        schema_version=1,
        fields=FIELDS,
        dataset_id=uuid.uuid4(),
        sources=sources,
        loaded={k: (schema if v[0] is None else v[0], v[1]) for k, v in loaded.items()},
    )


def test_a_load_is_idempotent_upserts_new_rows_and_rebuilds_when_a_row_leaves() -> None:
    a, b = _source(), _source()
    first = _wanted([a, b], {})
    assert ed.plan(first, exists_now=False) == ("rebuild", [a, b])
    same = _wanted([a, b], {a.record_id: (None, True), b.record_id: (None, True)})  # type: ignore[dict-item]
    assert ed.plan(same, exists_now=True) == ("unchanged", [])
    grown = _wanted([a, b], {a.record_id: (None, True)})  # type: ignore[dict-item]
    assert ed.plan(grown, exists_now=True) == ("upsert", [b])
    fixed = _wanted([a, b], {a.record_id: (None, True), b.record_id: (None, False)})  # type: ignore[dict-item]
    assert ed.plan(fixed, exists_now=True) == ("upsert", [b])
    # b's document was deleted, replaced or expired: its record is loaded but no longer wanted.
    left = _wanted([a], {a.record_id: (None, True), b.record_id: (None, True)})  # type: ignore[dict-item]
    assert ed.plan(left, exists_now=True) == ("rebuild", [a])
    # Rows of an earlier schema version: the table is rewritten from this one's records.
    other = _wanted([a], {a.record_id: (uuid.uuid4(), True)})
    assert ed.plan(other, exists_now=True)[0] == "rebuild"


def test_a_table_name_is_a_plain_identifier() -> None:
    name = ed.table_name("Hợp đồng tín dụng", uuid.UUID(int=0xABCDEF12))
    assert name == "trich_hop_dong_tin_dung_abcdef12"


def _target() -> ed.Target:
    return ed.Target(
        dataset_id=uuid.uuid4(),
        table="trich_hop_dong_abcdef12",
        title="Trích từ tài liệu: Hợp đồng",
        collection_id=uuid.uuid4(),
        collection_code="hop-dong",
        collection_name="Hợp đồng tín dụng",
        schema_id=uuid.uuid4(),
        schema_version=2,
        data_space_id=uuid.uuid4(),
        fields=tuple(FIELDS),
        columns=tuple(ed.columns_of(FIELDS)),
    )


DAY = datetime.date(2026, 11, 25)
IN_FORCE = (
    "(effective_from IS NULL OR effective_from <= DATE '2026-11-25') AND "
    "(effective_to IS NULL OR effective_to >= DATE '2026-11-25')"
)


def test_a_count_reads_the_value_as_the_field_reads_and_only_rows_in_force() -> None:
    args = CountDocumentsArgs(where=[Condition(field="penalty_rate", op=">", value="5%")])
    sql, labels = count_sql(_target(), args, DAY, VI)
    assert sql == (
        'SELECT COUNT(*) AS so_tai_lieu FROM workspace."trich_hop_dong_abcdef12" '
        f'WHERE {IN_FORCE} AND "penalty_rate" > 5'
    )
    assert labels == ["Số tài liệu"]


def test_a_sum_says_how_many_documents_gave_the_value() -> None:
    args = CountDocumentsArgs(
        measure="sum",
        field="amount",
        where=[
            Condition(field="signed_on", op=">=", value="01/01/2026"),
            Condition(field="contract_no", op="contains", value="O'Brien"),
        ],
        group_by="contract_no",
    )
    sql, labels = count_sql(_target(), args, DAY, VI)
    assert sql == (
        'SELECT "contract_no" AS nhom, COUNT(*) AS so_tai_lieu, SUM("gia_tri") AS gia_tri, '
        'COUNT("gia_tri") AS so_tai_lieu_co_gia_tri FROM workspace."trich_hop_dong_abcdef12" '
        f"WHERE {IN_FORCE} AND \"signed_on\" >= DATE '2026-01-01' AND "
        "strpos(lower(\"contract_no\"), lower('O''Brien')) > 0 "
        'GROUP BY "contract_no" ORDER BY so_tai_lieu DESC LIMIT 50'
    )
    assert labels == ["contract_no", "Số tài liệu", "Tổng amount", "Số tài liệu có amount"]
    assert cited_sql(_target(), args, DAY, VI).endswith(
        "ORDER BY document_title, document_id LIMIT 5"
    )


@pytest.mark.parametrize(
    ("args", "says"),
    [
        (CountDocumentsArgs(where=[Condition(field="lãi suất", op=">", value="5")]), "không có"),
        (CountDocumentsArgs(where=[Condition(field="national_id", op="=", value="1")]), "cá nhân"),
        (CountDocumentsArgs(where=[Condition(field="amount", op=">", value="nhiều")]), "kiểu"),
        (CountDocumentsArgs(where=[Condition(field="contract_no", op=">", value="A")]), "số"),
        (CountDocumentsArgs(where=[Condition(field="amount", op="contains", value="1")]), "chữ"),
        (CountDocumentsArgs(measure="sum", field="contract_no"), "số"),
        (CountDocumentsArgs(where=[Condition(field="amount", op="=")]), "thiếu"),
    ],
)
def test_a_count_refuses_what_the_schema_does_not_allow(
    args: CountDocumentsArgs, says: str
) -> None:
    with pytest.raises(Refused, match=says):
        count_sql(_target(), args, DAY, VI)


def test_the_coverage_says_what_was_not_counted() -> None:
    assert coverage_text(
        {"documents_in_scope": 5, "extracted": 3, "needs_review": 1, "not_extracted": 1}
    ) == (
        "Đếm trên dữ liệu trích từ tài liệu: 3/5 tài liệu đang hiệu lực đã được trích và tính; "
        "không tính 1 chờ người duyệt, 1 chưa trích được."
    )
    assert coverage_text(
        {"documents_in_scope": 2, "extracted": 2, "needs_review": 0, "not_extracted": 0}
    ).endswith("đã được trích và tính.")


def test_a_collection_is_picked_by_name_or_alone() -> None:
    one = _target()
    assert pick([one], None) is one
    assert pick([one, _target()], None) is None
    assert pick([one], "hop dong tin dung") is one
    assert pick([one], "hoá đơn") is None


def test_a_span_is_pinned_to_the_element_that_holds_its_quote() -> None:
    first, second = uuid.uuid4(), uuid.uuid4()
    elements = {
        first: types.SimpleNamespace(
            id=first, text="Điều 1. Các bên", page_no=1, bbox=None, extractor_version="docling-x"
        ),
        second: types.SimpleNamespace(
            id=second,
            text="Mức phạt vi phạm hợp đồng là 8% giá trị.",
            page_no=3,
            bbox={"l": 1, "t": 2, "r": 3, "b": 4},
            extractor_version="docling-x",
        ),
    }
    chunk = types.SimpleNamespace(id="c1", element_ids=[first, second])
    spans = [
        {
            "field": "penalty_rate",
            "chunk_id": "c1",
            "page_from": 1,
            "quote": "phạt vi phạm hợp đồng là 8%",
        },
        {"field": "national_id", "chunk_id": "c1", "page_from": 1, "quote": None},
    ]
    pinned = pin(spans, [chunk], elements)  # type: ignore[arg-type]
    assert pinned[0]["element_id"] == str(second)
    assert pinned[0]["page_from"] == 3 and pinned[0]["bbox"] == {"l": 1, "t": 2, "r": 3, "b": 4}
    assert pinned[0]["extractor_version"] == "docling-x"
    # No quote kept (personal data): it stays on its passage.
    assert "element_id" not in pinned[1] and pinned[1]["extractor_version"] == "docling-x"
