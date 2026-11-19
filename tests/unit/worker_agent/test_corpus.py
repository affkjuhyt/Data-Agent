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
