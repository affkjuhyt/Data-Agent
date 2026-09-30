"""Orchestrator clarification questions carry options.

`input_refine` used to return bare question strings, so its card ("Waiting for
your answer") offered nothing to click — every answer had to be typed. The
model now suggests options per question; the card adds its own "Other".
Model output is still model output: a plain-string reply keeps working, and
the options are cleaned before they become buttons.
"""
from __future__ import annotations

import json
import pathlib
import sys
import warnings
from unittest.mock import AsyncMock, MagicMock

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
_BACKEND = str(REPO_ROOT / "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

pytest.importorskip("langgraph", reason="workflow_runtime deps not installed")
pytest.importorskip("litellm", reason="workflow_runtime deps not installed")

pytestmark = pytest.mark.unit


@pytest.fixture
def refine():
    # litellm emits a pydantic "Valid config keys have changed in V2"
    # UserWarning at import (see test_graph_cache.py).
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="Valid config keys have changed in V2", category=UserWarning)
        from services.workflow_runtime.application.orchestrator.nodes import input_refine
    return input_refine


class TestNormalizeQuestions:
    def test_structured_questions_keep_their_options(self, refine):
        out = refine.normalize_questions([{"text": "Chi nhánh nào?", "options": ["Hà Nội", "TP.HCM"]}])

        assert out == [{"text": "Chi nhánh nào?", "options": ["Hà Nội", "TP.HCM"]}]

    def test_plain_strings_still_work_without_options(self, refine):
        assert refine.normalize_questions(["Bạn hỏi về cái gì?"]) == [{"text": "Bạn hỏi về cái gì?", "options": []}]

    def test_the_model_does_not_add_a_second_other(self, refine):
        out = refine.normalize_questions([{"text": "Kỳ nào?", "options": ["Tháng này", "Khác", "Other", "Tháng này"]}])

        assert out[0]["options"] == ["Tháng này"]

    def test_options_and_questions_are_capped(self, refine):
        many = [{"text": f"Q{i}", "options": [f"o{j}" for j in range(9)] + ["x" * 200]} for i in range(6)]

        out = refine.normalize_questions(many)

        assert len(out) == 3
        assert all(len(q["options"]) == 4 for q in out)

    @pytest.mark.parametrize("raw", [None, "Kỳ nào?", [None, 3, {"options": ["a"]}, {"text": "  "}]])
    def test_malformed_output_yields_no_questions(self, refine, raw):
        assert refine.normalize_questions(raw) == []

    def test_payload_keeps_the_texts_every_client_reads(self, refine):
        payload = refine.clarification_payload([{"text": "Kỳ nào?", "options": ["Quý này"]}, "Chi nhánh nào?"])

        assert payload["clarification_questions"] == ["Kỳ nào?", "Chi nhánh nào?"]
        assert payload["clarification_details"][0]["options"] == ["Quý này"]


class TestRunInputRefine:
    async def test_a_clarify_reply_returns_questions_with_options(self, refine, monkeypatch):
        reply = {"action": "clarify", "questions": [{"text": "Bạn hỏi về cái nào?", "options": ["Doanh thu", "Đơn hàng"]}]}
        response = MagicMock()
        response.choices = [MagicMock(message=MagicMock(content=json.dumps(reply)))]
        monkeypatch.setattr(refine.llm_call, "acompletion", AsyncMock(return_value=response))

        query, questions = await refine.run_input_refine("cái đó thì sao?", [], {})

        assert query == "cái đó thì sao?"
        assert questions == [{"text": "Bạn hỏi về cái nào?", "options": ["Doanh thu", "Đơn hàng"]}]

    async def test_a_clarify_reply_with_no_usable_question_keeps_the_query(self, refine, monkeypatch):
        response = MagicMock()
        response.choices = [MagicMock(message=MagicMock(content='{"action": "clarify", "questions": []}'))]
        monkeypatch.setattr(refine.llm_call, "acompletion", AsyncMock(return_value=response))

        assert await refine.run_input_refine("doanh thu tháng 6", [], {}) == ("doanh thu tháng 6", [])

    def test_the_prompt_asks_for_options_and_no_other(self, refine):
        assert '"options"' in refine._SYSTEM
        assert "Khác" in refine._SYSTEM
