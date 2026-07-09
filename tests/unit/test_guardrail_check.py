"""Unit tests for services/shared/guardrail_check.py — hàm dùng chung cho cả
orchestrator (input + multi-agent synthesis output) và workflow-runtime
(single-agent output). Mock litellm.acompletion — không gọi LLM thật.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from services.shared.guardrail_check import GuardrailCheckError, llm_guardrail_check

pytestmark = pytest.mark.unit


def _canned(content: dict):
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content=json.dumps(content)))]
    return resp


async def test_input_check_uses_check_type_input(monkeypatch):
    captured = {}

    async def fake_acompletion(**kwargs):
        captured["messages"] = kwargs["messages"]
        return _canned({"triggered": False})

    import services.shared.guardrail_check as mod
    monkeypatch.setattr(mod.litellm, "acompletion", fake_acompletion)

    triggered, reason = await llm_guardrail_check("câu hỏi test", {}, check_type="input")

    assert triggered is False
    assert reason == ""
    user_msg = captured["messages"][1]["content"]
    assert "CHECK_TYPE: input" in user_msg
    assert "CONTENT:" in user_msg


async def test_output_check_uses_check_type_output_and_returns_reason(monkeypatch):
    captured = {}

    async def fake_acompletion(**kwargs):
        captured["messages"] = kwargs["messages"]
        return _canned({"triggered": True, "reason": "vi phạm ví dụ"})

    import services.shared.guardrail_check as mod
    monkeypatch.setattr(mod.litellm, "acompletion", fake_acompletion)

    triggered, reason = await llm_guardrail_check("câu trả lời test", {}, check_type="output")

    assert triggered is True
    assert reason == "vi phạm ví dụ"
    user_msg = captured["messages"][1]["content"]
    assert "CHECK_TYPE: output" in user_msg
    assert "CÂU TRẢ LỜI CẦN KIỂM TRA:" in user_msg


async def test_allowed_topics_only_applied_to_input_not_output(monkeypatch):
    captured = {}

    async def fake_acompletion(**kwargs):
        captured["messages"] = kwargs["messages"]
        return _canned({"triggered": False})

    import services.shared.guardrail_check as mod
    monkeypatch.setattr(mod.litellm, "acompletion", fake_acompletion)

    # "CHỦ ĐỀ ĐƯỢC PHÉP" (không kèm dấu ":") tự nó đã xuất hiện trong prompt gốc
    # (mô tả rule 4 của input) — phải check đúng TÊN chủ đề cụ thể mới phân
    # biệt được có bị chèn topics_block hay không.
    cfg = {"guardrail_allowed_topics": ["chủ đề đặc biệt XYZ"]}
    await llm_guardrail_check("test", cfg, check_type="output")
    assert "chủ đề đặc biệt XYZ" not in captured["messages"][0]["content"]

    await llm_guardrail_check("test", cfg, check_type="input")
    assert "chủ đề đặc biệt XYZ" in captured["messages"][0]["content"]


async def test_raises_guardrail_check_error_on_llm_failure(monkeypatch):
    """Không tự fail-open/closed — raise ra ngoài để caller (nodes/guardrail.py
    fail-open, conversation_runner.py/summary_node fail-closed) tự quyết."""
    async def fake_acompletion(**kwargs):
        raise RuntimeError("timeout")

    import services.shared.guardrail_check as mod
    monkeypatch.setattr(mod.litellm, "acompletion", fake_acompletion)

    with pytest.raises(GuardrailCheckError):
        await llm_guardrail_check("test", {}, check_type="input")


async def test_raises_guardrail_check_error_on_bad_json(monkeypatch):
    async def fake_acompletion(**kwargs):
        resp = MagicMock()
        resp.choices = [MagicMock(message=MagicMock(content="not json"))]
        return resp

    import services.shared.guardrail_check as mod
    monkeypatch.setattr(mod.litellm, "acompletion", fake_acompletion)

    with pytest.raises(GuardrailCheckError):
        await llm_guardrail_check("test", {}, check_type="output")
