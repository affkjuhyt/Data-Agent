"""The deterministic verifier inside judge_node (T5 wiring).

judge_system.txt asks a model whether an answer looks grounded, which is the
wrong instrument for figures twice over: it compares digit strings, so "1284"
passes against a source saying "128,4"; and it is a model, so the one place the
system must be certain is the one place it guesses (ADR-T5).

These tests pin that the deterministic checks run FIRST and short-circuit — a
failure must not reach the LLM at all, which is both the correctness point and
the reason the failure path costs nothing.
"""
from __future__ import annotations

import pathlib
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SVC_DIR = str(REPO_ROOT / "backend" / "services" / "workflow_runtime")

pytest.importorskip("litellm", reason="litellm not installed in this environment")

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _sys_path():
    added = [p for p in (_SVC_DIR,) if p not in sys.path]
    sys.path[:0] = added
    yield
    for p in added:
        if p in sys.path:
            sys.path.remove(p)


def _state(response: str, chunks: list[dict]) -> dict:
    return {
        "query": "Nợ công/GDP năm 2024 là bao nhiêu?",
        "response": response,
        "reranked_chunks": chunks,
        "config": {},
        "loop_iteration": 0,
    }


def _chunk(content: str, doc_id: str = "doc-17", page: int | None = 12) -> dict:
    return {"content": content, "doc_id": doc_id, "page": page, "source": "Bản tin nợ công"}


BULLETIN = _chunk("Nợ công so với GDP năm 2024 đạt 34,7%; dư nợ 3.109,2 nghìn tỷ đồng.")


async def test_a_grounded_answer_reaches_the_llm_judge(monkeypatch):
    import nodes.model_harness.judge as judge_mod

    called = AsyncMock(return_value=MagicMock(
        choices=[MagicMock(message=MagicMock(content='{"verdict": "PASS", "reason": "ok"}'))]))
    monkeypatch.setattr(judge_mod.llm_call, "acompletion", called)

    result = await judge_mod.judge_node(
        _state("Nợ công/GDP năm 2024 là 34,7% [1].", [BULLETIN]))

    assert result["judge_verdict"] == "PASS"
    assert called.await_count == 1
    assert result["verifier"]["passed"] is True


async def test_a_fabricated_figure_fails_without_asking_the_model(monkeypatch):
    """The short-circuit: a number in no source is not a matter of opinion, so
    there is nothing to ask and no latency to spend asking it."""
    import nodes.model_harness.judge as judge_mod

    called = AsyncMock()
    monkeypatch.setattr(judge_mod.llm_call, "acompletion", called)

    result = await judge_mod.judge_node(
        _state("Nợ công/GDP năm 2024 là 41,2%.", [BULLETIN]))

    assert result["judge_verdict"] == "FAIL"
    assert result["judge_error_type"] == "GROUNDING_ERROR"
    assert "41,2" in result["judge_reason"]
    assert called.await_count == 0


async def test_the_magnitude_error_the_string_judge_let_through(monkeypatch):
    """The source says 3.109,2; the answer says 31092. Same digits, wrong
    figure by a factor of ten — this is E2 on the runtime side."""
    import nodes.model_harness.judge as judge_mod

    monkeypatch.setattr(judge_mod.llm_call, "acompletion", AsyncMock())

    result = await judge_mod.judge_node(
        _state("Dư nợ Chính phủ là 31092 nghìn tỷ đồng.", [BULLETIN]))

    assert result["judge_error_type"] == "GROUNDING_ERROR"


async def test_a_failure_resets_only_the_response(monkeypatch):
    """GROUNDING_ERROR routes back to the responder; the retrieved context was
    never in question, so re-running retrieval would only cost time."""
    import nodes.model_harness.judge as judge_mod

    monkeypatch.setattr(judge_mod.llm_call, "acompletion", AsyncMock())

    result = await judge_mod.judge_node(
        _state("Tỷ lệ là 41,2%.", [BULLETIN]))

    assert result["response"] == ""
    assert "reranked_chunks" not in result
    assert result["loop_iteration"] == 1


async def test_every_check_is_reported_for_the_badge(monkeypatch):
    import nodes.model_harness.judge as judge_mod

    monkeypatch.setattr(judge_mod.llm_call, "acompletion", AsyncMock())

    result = await judge_mod.judge_node(_state("Tỷ lệ là 41,2% [9].", [BULLETIN]))

    names = [c["name"] for c in result["verifier"]["checks"]]
    assert names == ["numbers_grounded", "precision_supported",
                     "vintage_consistent", "citations_resolved"]
    failed = {c["name"] for c in result["verifier"]["checks"] if not c["passed"]}
    assert failed == {"numbers_grounded", "citations_resolved"}


async def test_evidence_is_numbered_the_way_the_responder_numbered_it(monkeypatch):
    """responder.py enumerates `reranked_chunks[:max_chunks]` without skipping
    empties. The judge's own prompt loop DOES skip them, so building evidence
    from that loop would shift every citation by one document."""
    import nodes.model_harness.judge as judge_mod

    monkeypatch.setattr(judge_mod.llm_call, "acompletion", AsyncMock(
        return_value=MagicMock(choices=[MagicMock(
            message=MagicMock(content='{"verdict": "PASS"}'))])))

    empty_first = [_chunk("", doc_id="doc-empty"), BULLETIN]
    result = await judge_mod.judge_node(
        _state("Nợ công/GDP năm 2024 là 34,7% [2].", empty_first))

    assert result["judge_verdict"] == "PASS"


async def test_a_refusal_states_no_figures_and_passes(monkeypatch):
    import nodes.model_harness.judge as judge_mod

    monkeypatch.setattr(judge_mod.llm_call, "acompletion", AsyncMock(
        return_value=MagicMock(choices=[MagicMock(
            message=MagicMock(content='{"verdict": "PASS"}'))])))

    result = await judge_mod.judge_node(
        _state("Chỉ tiêu này chưa được công bố cho kỳ đó.", [BULLETIN]))

    assert result["judge_verdict"] == "PASS"


async def test_a_document_without_a_page_still_lets_the_answer_pass(monkeypatch):
    """Documents ingested before coordinates were recorded are the corpus's
    gap, not the answer's mistake."""
    import nodes.model_harness.judge as judge_mod

    monkeypatch.setattr(judge_mod.llm_call, "acompletion", AsyncMock(
        return_value=MagicMock(choices=[MagicMock(
            message=MagicMock(content='{"verdict": "PASS"}'))])))
    pageless = _chunk("Nợ công so với GDP năm 2024 đạt 34,7%.", page=None)

    result = await judge_mod.judge_node(
        _state("Tỷ lệ năm 2024 là 34,7% [1].", [pageless]))

    assert result["judge_verdict"] == "PASS"
    citations = next(c for c in result["verifier"]["checks"]
                     if c["name"] == "citations_resolved")
    assert citations["passed"] is True
    assert "no page recorded" in citations["detail"]
