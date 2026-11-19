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


class _Rows:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def all(self) -> list[object]:
        return self._rows


class _Session:
    """Records statements. No database: the knowledge-page policy is not what this proves."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict[str, str] | None]] = []

    async def execute(self, statement: object, params: dict[str, str] | None = None) -> _Rows:
        self.events.append(("execute", str(statement), params))
        return _Rows([])

    async def scalars(self, statement: object) -> _Rows:
        self.events.append(("scalars", str(statement), None))
        return _Rows([])


def _layer(*metrics: object) -> object:
    import types

    from services.worker_agent.understand.resolver import MetricEntry

    entries = [m for m in metrics if isinstance(m, MetricEntry)]
    return types.SimpleNamespace(
        vocabulary=types.SimpleNamespace(metrics=entries, dimensions=[]),
        versions={m.metric_id: object() for m in entries},
        blocked_metric_ids=frozenset(),
    )


async def _load(
    monkeypatch: pytest.MonkeyPatch, session: _Session, layer: object, space_id: object
) -> object:
    import uuid

    from services.worker_agent.agentic.corpus import load_corpus
    from services.worker_agent.sqlgen.schema import Schema
    from services.worker_agent.sqlgen.space import Space

    async def load_space(*_a: object, **_k: object) -> Space:
        return Space(Schema([]))

    monkeypatch.setattr("services.worker_agent.agentic.corpus.load_space", load_space)
    return await load_corpus(session, space_id, uuid.uuid4(), layer)  # type: ignore[arg-type]


async def test_uncertified_in_force_metric_is_not_the_certified_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import uuid

    from services.worker_agent.agentic import prompts
    from services.worker_agent.agentic.corpus import render_corpus
    from services.worker_agent.understand.resolver import MetricEntry

    space_id = uuid.uuid4()
    plain = MetricEntry(uuid.uuid4(), "DOANH_THU_VANG", "Doanh thu taxi vàng", False)
    standard = MetricEntry(uuid.uuid4(), "DOANH_THU", "Doanh thu", True)
    brief = await _load(monkeypatch, _Session(), _layer(plain, standard), space_id)

    by_code = {metric.code: metric for metric in brief.metrics}
    assert by_code["DOANH_THU_VANG"].certified is False
    assert by_code["DOANH_THU"].certified is True
    rendered = render_corpus(brief)
    certified_shelf = rendered.split("Chỉ số đã chứng nhận:\n", 1)[1].split("\nChỉ số nháp:", 1)[0]
    draft_shelf = rendered.split("Chỉ số nháp:\n", 1)[1].split("\nChiều:", 1)[0]
    assert "DOANH_THU_VANG" not in certified_shelf
    assert "DOANH_THU_VANG" in draft_shelf and "chưa chứng nhận" in draft_shelf
    assert "DOANH_THU:" in certified_shelf

    rule = prompts.PLANNER.split("\n3. ", 1)[0].split("\n2. ", 1)[1]
    assert 'mục "Chỉ số đã chứng nhận"' in rule
    assert "thì query_metric" in rule
    assert "Khớp tên một chỉ số chưa chứng nhận không phải đường chỉ số chuẩn" in rule
    assert "không cấm explore_sql" in rule
    assert "Tên gần với một mã trong danh mục" not in rule
    assert "không tự tính" not in prompts.PLANNER


async def test_load_corpus_sets_the_reader_settings_for_this_space(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import uuid

    from libs.dataagent_db.session import reader_settings_for

    space_id = uuid.uuid4()
    session = _Session()
    await _load(monkeypatch, session, _layer(), space_id)

    # The keys and values reader_settings returns for an asker of this space
    # (reader_settings_for is what that function returns). Not a skipped setting.
    expected = {
        "knowledge_reader": "on",
        "data_space_ids": "{" + str(space_id) + "}",
        "clearance": "1",
    }
    assert reader_settings_for([space_id]) == expected
    applied = [
        (params["name"].removeprefix("app."), params["value"])
        for _kind, sql, params in session.events
        if params is not None and "set_config" in sql
    ]
    assert applied == list(expected.items())
    setting_at = next(i for i, (_k, sql, _p) in enumerate(session.events) if "set_config" in sql)
    page_at = next(i for i, (_k, sql, _p) in enumerate(session.events) if "knowledge_page" in sql)
    assert setting_at < page_at
    assert "set role" not in session.events[page_at][1].lower()


def test_planner_answers_a_non_figure_question_from_the_corpus() -> None:
    from services.worker_agent.agentic import prompts
    from services.worker_agent.agentic.tools.query import ExploreSql

    assert "DOANH_THU_INTERNET" not in prompts.PLANNER
    assert (
        "Câu không hỏi một con số (dữ liệu có gì, bảng nào, tài liệu nào) "
        'là mode="answer" từ <corpus>'
    ) in prompts.PLANNER
    assert "Cấm tự tính" not in ExploreSql.description
