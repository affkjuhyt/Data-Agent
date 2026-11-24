"""A hybrid turn of the agent (AE-13, ASK_ENGINE_SPEC §10): the figure a document's table states,
read from its cell by the documents specialist's search, checked against the figure the numbers
specialist read before the answer is written, and stored with the answer.

The document search runs for real on a seeded table (documents/table_query); the data figure is
put on the workspace as query_metric or explore_sql leaves it. No model answers: the reporter is
down, so what the answer shows is the server's own blocks.
"""

from __future__ import annotations

import datetime
import types
import uuid
from typing import Any

import pytest

from libs import clock
from services.worker_agent.agentic import engine
from services.worker_agent.agentic import plan as p
from services.worker_agent.agentic.tools.documents import SearchDocuments, SearchDocumentsArgs
from services.worker_agent.agentic.workspace import Workspace
from services.worker_agent.llm import ModelUnavailable

from .conftest import Seed
from .test_document_answers import Library, add_table, library

pytestmark = [pytest.mark.integration]

QUESTION = "Dư nợ thực hiện năm 2024 của chi nhánh Hà Nội theo báo cáo có khớp dữ liệu không?"


class NoModel:
    async def json_chat(self, *args: Any, **kwargs: Any) -> Any:
        raise ModelUnavailable("the reporter is down in this test")


async def _turn(seed: Seed, lib: Library, data: dict[str, Any]) -> uuid.UUID:
    conv = seed.row("qa.conversation", org_id=lib.org, data_space_id=lib.space, user_id=lib.user)
    asked = seed.row("qa.question", org_id=lib.org, conversation_id=conv, seq=1, text=QUESTION)
    ws = Workspace()
    vocab = types.SimpleNamespace(
        metrics=[], synonyms={}, dimensions=[], values=[], calendar=clock.DEFAULT
    )
    ctx = types.SimpleNamespace(
        org_id=lib.org,
        user_id=lib.user,
        space_id=lib.space,
        question_id=asked,
        question=QUESTION,
        today=datetime.date(2026, 11, 24),
        as_of=None,
        purpose=None,
        period=types.SimpleNamespace(
            start=datetime.date(2024, 1, 1), end=datetime.date(2025, 1, 1), label="năm 2024"
        ),
        layer=types.SimpleNamespace(vocabulary=vocab),
        ws=ws,
        step_id="s2",
    )
    # The numbers specialist's reading, then the documents specialist's search.
    ws.add(step_id="s1", **data)
    found = await SearchDocuments().run(
        ctx,  # type: ignore[arg-type]
        SearchDocumentsArgs(query="Dư nợ thực hiện năm 2024 của chi nhánh Hà Nội"),
    )
    assert found.ok and found.detail["cells"] == 1
    state = engine.State(
        event=types.SimpleNamespace(org_id=lib.org),  # type: ignore[arg-type]
        question_id=asked,
        model=NoModel(),  # type: ignore[arg-type]
        ctx=ctx,  # type: ignore[arg-type]
        plan=p.Plan(
            goal="đối chiếu",
            steps=[
                p.PlanStep(id="s1", tool="investigate_numbers", instruction="số", status="done"),
                p.PlanStep(
                    id="s2", tool="investigate_documents", instruction="tài liệu", status="done"
                ),
            ],
        ),
    )
    await engine.compose(state)
    assert not state.handed_back and state.feedback is None
    await engine.persist(state)
    return asked


def _answer(seed: Seed, question: uuid.UUID) -> tuple[Any, ...]:
    return seed.one(
        "SELECT q.status, a.kind, a.trust_code, a.envelope, a.blocks, a.figures FROM qa.question q"
        " JOIN qa.answer a ON a.question_id = q.id WHERE q.id = %s",
        question,
    )


@pytest.mark.req("AE-13")
async def test_a_document_figure_that_agrees_with_the_metric_is_noted(seed: Seed) -> None:
    lib = library(seed)
    add_table(seed, lib)
    question = await _turn(
        seed,
        lib,
        {
            "title": "Dư nợ Hà Nội, 2024",
            "columns": ["Dư nợ"],
            "rows": [[1_300_000_000]],
            "trust": "certified",
            "tool": "query_metric",
            "unit": "VND",
            "reading": {
                "metric": "DU_NO",
                "metric_name": "Dư nợ",
                "period": "2024",
                "filters": {"chi_nhanh": ["Hà Nội"]},
            },
        },
    )
    status, kind, code, envelope, blocks, _ = _answer(seed, question)
    assert (status, kind) == ("answered", "data")
    note = envelope["reconciliation"]
    assert note["verdict"] == "agree" and note["residual"]["abs"] == 0
    assert [s["authority"] for s in note["sources"]] == ["certified_metric", "document"]
    assert note["sources"][1]["cite"] == 1 and note["sources"][1]["period_normalized"]
    assert "refusal" not in envelope and code == "DOCUMENT"
    (shown,) = (b for b in blocks if b["type"] == "reconciliation")
    assert "khớp" in shown["content"] and "[1]" in shown["content"]
    assert not any(b["type"] == "callout" for b in blocks)


@pytest.mark.req("AE-13")
async def test_a_real_difference_is_a_source_conflict_with_both_figures_shown(seed: Seed) -> None:
    lib = library(seed)
    add_table(seed, lib)
    question = await _turn(
        seed,
        lib,
        {
            "title": "Dư nợ Hà Nội 2024 (SQL)",
            "columns": ["du_no"],
            "rows": [[1_250_000_000]],
            "trust": "exploratory",
            "tool": "explore_sql",
            "unit": "VND",
            "sql": "SELECT sum(du_no) FROM loan WHERE branch = 'Hà Nội'",
        },
    )
    status, kind, code, envelope, blocks, figures = _answer(seed, question)
    # Answered with both figures, refused as the answer: neither number may be read as it.
    assert (status, kind, code) == ("answered", "data", "INSUFFICIENT")
    assert envelope["refusal"] == {"code": "SOURCE_CONFLICT", "next_action": "contact_steward"}
    note = envelope["reconciliation"]
    assert note["verdict"] == "conflict" and note["winner"] is None
    assert note["residual"]["pct"] == pytest.approx(4.0)
    assert [b["type"] for b in blocks[:2]] == ["callout", "kpi"]
    assert blocks[0]["tone"] == "warning" and "1.300.000.000 đồng" in blocks[0]["content"]
    assert sorted(float(f["value"]) for f in figures) == [1_250_000_000.0, 1_300_000_000.0]
    assert {f["trust"] for f in figures} == {"SQL thăm dò, chưa qua chỉ số chuẩn", "Theo tài liệu"}
    # The note closes the answer, before its caveats.
    assert [b["type"] for b in blocks[-2:]] == ["reconciliation", "caveats"]


@pytest.mark.req("AE-13")
async def test_a_certified_metric_settles_a_difference_by_authority(seed: Seed) -> None:
    lib = library(seed)
    add_table(seed, lib)
    question = await _turn(
        seed,
        lib,
        {
            "title": "Dư nợ Hà Nội, 2024",
            "columns": ["Dư nợ"],
            "rows": [[1_250_000_000]],
            "trust": "certified",
            "tool": "query_metric",
            "unit": "VND",
            "reading": {"metric": "DU_NO", "metric_name": "Dư nợ", "period": "2024"},
        },
    )
    _, _, code, envelope, blocks, _ = _answer(seed, question)
    note = envelope["reconciliation"]
    assert note["verdict"] == "authority" and note["sources"][0].get("wins") is True
    assert "refusal" not in envelope and code == "DOCUMENT"
    assert blocks[0]["type"] == "kpi" and len(blocks[0]["items"]) == 2
