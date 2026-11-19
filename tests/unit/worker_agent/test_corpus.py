"""Corpus brief text for the agentic planner (worker_agent/agentic/corpus.py)."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def test_render_names_tables_drafts_and_an_empty_document_shelf() -> None:
    from services.worker_agent.agentic.corpus import (
        CorpusBrief,
        CorpusMetric,
        CorpusTable,
        render_corpus,
    )

    text = render_corpus(
        CorpusBrief(
            tables=(
                CorpusTable(
                    "tlc.yellow_trip",
                    "Chuyến taxi vàng",
                    "chuyến vàng 2024",
                    ("fare_amount (number): tiền cước",),
                ),
            ),
            joins=("tlc.yellow_trip.pu_location_id = tlc.zone.location_id",),
            metrics=(CorpusMetric("DOANH_THU_VANG", "Doanh thu taxi vàng", False),),
            dimensions=("thoi_diem_don: Thời điểm đón",),
            documents=(),
            pages=(),
        )
    )
    assert "tlc.yellow_trip" in text
    assert "fare_amount" in text
    assert "DOANH_THU_VANG" in text and "chưa chứng nhận" in text
    assert "Tài liệu:\nkhông có" in text
    assert "DOANH_THU_INTERNET" not in text


def test_build_corpus_names_the_table_and_both_join_column_lists() -> None:
    import uuid

    from services.worker_agent.agentic.corpus import (
        CorpusDoc,
        CorpusMetric,
        CorpusPage,
        build_corpus,
    )
    from services.worker_agent.sqlgen.schema import Col, Fk, Schema, Tbl
    from services.worker_agent.sqlgen.space import Space

    trip_id = uuid.uuid4()
    trip = Tbl(
        trip_id,
        "tlc",
        "yellow_trip",
        "Chuyến taxi vàng",
        (Col("fare_amount", "tiền cước", "number"),),
        "chuyến vàng 2024",
    )
    space = Space(
        Schema(
            [trip],
            [
                Fk(
                    trip_id,
                    ("pu_location_id", "vendor_id"),
                    trip_id,
                    ("location_id", "vendor_id"),
                )
            ],
        )
    )
    certified = (CorpusMetric("DOANH_THU", "Doanh thu", True),)
    drafts = (CorpusMetric("DOANH_THU_VANG", "Doanh thu taxi vàng", False),)
    documents = (CorpusDoc("Từ điển TLC", ("pdf", "html")),)
    pages = (CorpusPage("Taxi vàng", "topic", "mô tả ngắn"),)

    brief = build_corpus(
        space,
        certified=certified,
        drafts=drafts,
        dimensions=("thoi_diem_don: Thời điểm đón",),
        documents=documents,
        pages=pages,
    )

    assert [table.qualified for table in brief.tables] == ["tlc.yellow_trip"]
    assert brief.tables[0].label == "Chuyến taxi vàng"
    assert brief.tables[0].description == "chuyến vàng 2024"
    assert brief.tables[0].columns == ("fare_amount (number): tiền cước",)
    assert brief.joins == (
        "tlc.yellow_trip.pu_location_id, vendor_id = tlc.yellow_trip.location_id, vendor_id",
    )
    assert brief.metrics[0] == certified[0]
    assert brief.metrics[0].certified is True
    assert brief.metrics[1] == drafts[0]
    assert brief.metrics[1].certified is False
    assert brief.dimensions == ("thoi_diem_don: Thời điểm đón",)
    assert brief.documents == documents
    assert brief.pages == pages


def test_planner_answers_a_non_figure_question_from_the_corpus() -> None:
    from services.worker_agent.agentic import prompts
    from services.worker_agent.agentic.tools.query import ExploreSql

    assert "DOANH_THU_INTERNET" not in prompts.PLANNER
    assert (
        "Câu không hỏi một con số (dữ liệu có gì, bảng nào, tài liệu nào) "
        'là mode="answer" từ <corpus>'
    ) in prompts.PLANNER
    assert "Cấm tự tính" not in ExploreSql.description
