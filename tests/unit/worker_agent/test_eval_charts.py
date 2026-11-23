"""AL-09: the chart traps run through the real validator, and real answers' words through the
causal scorer the runtime shares."""

from __future__ import annotations

import types

import pytest

from model.eval import corpus
from model.eval.scorers import causal, chart

pytestmark = [pytest.mark.unit, pytest.mark.req("AL-09")]


def test_every_trap_is_caught_and_no_right_chart_is_blocked() -> None:
    cases = corpus.load_cases("charts")
    traps = [c for c in cases if c["expect"] != "pass"]
    assert len(traps) >= 40
    # Every code of the validator is set as a trap at least once.
    from services.worker_agent.answer import chart_check

    assert {c["code"] for c in traps} == set(chart_check.CODES)
    report = chart.score(cases)
    assert report["metrics"] == {"chart_trap_catch_rate": 1.0, "chart_false_block_rate": 0.0}, [
        r for r in report["results"] if not r["passed"]
    ]


def test_a_trap_the_validator_lets_through_is_a_miss(monkeypatch: pytest.MonkeyPatch) -> None:
    from services.worker_agent.answer import chart_check

    monkeypatch.setattr(chart_check, "check", lambda *_a, **_k: [])
    report = chart.score(corpus.load_cases("charts"))
    assert report["metrics"]["chart_trap_catch_rate"] == 0.0
    assert report["metrics"]["chart_false_block_rate"] == 0.0


def test_real_answers_are_read_outside_their_hypotheses() -> None:
    from services.worker_agent.agentic.evaluate import said_of

    answer = types.SimpleNamespace(
        text="Doanh thu tăng 5%.",
        blocks=[
            {"type": "markdown", "content": "Miền Bắc dẫn đầu."},
            {
                "type": "hypothesis",
                "items": [{"text": "Giá giảm dẫn đến nhiều đơn hơn", "check": "q"}],
            },
            {"type": "recommendation", "items": [{"action": "Theo dõi giá", "condition": None}]},
        ],
    )
    said = said_of(answer)  # type: ignore[arg-type]
    assert "Miền Bắc dẫn đầu" in said and "Theo dõi giá" in said
    assert "dẫn đến" not in said  # a hypothesis may name a cause
    summaries = [
        {
            "engine": "router",
            "outcomes": [
                {"item_id": "1", "said": said},
                {"item_id": "2", "said": "Doanh thu giảm, vì vậy lợi nhuận giảm."},
                {"item_id": "3", "said": ""},
            ],
        }
    ]
    report = causal.score_answers(summaries)
    assert report["answers"] == 2
    assert report["metrics"]["answer_causal_claim_rate"] == 0.5
    assert [r["id"] for r in report["results"] if not r["passed"]] == ["router:2"]
