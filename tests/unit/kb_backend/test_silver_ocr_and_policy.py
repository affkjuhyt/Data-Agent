"""Silver pipeline: real OCR for image/video assets, and the two Policy
Center behaviors the audit found were stored but never consulted
(natural-language filter policies, the extraction addendum).

Before this fix: `_vlm_describe` raised `NotImplementedError` unconditionally
for every image/video, and `KBExtractionPolicy.custom_override` /
natural-language `KBFilterPolicy` rows were readable from the UI but never
reached the real pipeline. These tests exercise the fixed behavior with the
LLM/OCR calls mocked — no real Tesseract binary or model_service required.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from libs.ingestion.silver import filtering, parsing, shared

pytestmark = pytest.mark.unit


# ── OCR (shared._ocr_image_bytes / parsing._parse_image / _parse_video) ────

def test_ocr_image_bytes_calls_tesseract_with_vie_and_eng(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, Any] = {}

    class _FakePytesseract:
        @staticmethod
        def image_to_string(image: Any, lang: str) -> str:
            calls["lang"] = lang
            return "  Nghị định 13/2023/NĐ-CP  \n"

    monkeypatch.setitem(__import__("sys").modules, "pytesseract", _FakePytesseract())

    class _FakeImage:
        @staticmethod
        def open(_buf: Any) -> str:
            return "fake-image"

    monkeypatch.setitem(__import__("sys").modules, "PIL", type("PIL", (), {"Image": _FakeImage}))

    text = shared._ocr_image_bytes(b"fake-jpeg-bytes")

    assert text == "Nghị định 13/2023/NĐ-CP"
    assert calls["lang"] == "vie+eng"


@pytest.mark.asyncio
async def test_parse_image_returns_one_chunk_when_text_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(parsing, "_ocr_image_bytes", lambda _b: "some extracted text")

    chunks = await parsing._parse_image(b"bytes", "jpg")

    assert chunks == [{"block_index": 0, "content": "some extracted text", "table_involved": False, "table": None}]


@pytest.mark.asyncio
async def test_parse_image_returns_no_chunks_when_no_text_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(parsing, "_ocr_image_bytes", lambda _b: "")

    chunks = await parsing._parse_image(b"bytes", "jpg")

    assert chunks == []


# ── Policy Center: natural-language filter policies ─────────────────────────

@pytest.mark.asyncio
async def test_filter_chunks_blocks_on_natural_language_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    postgres = AsyncMock()
    postgres.read.return_value = type("Resp", (), {
        "code": 200,
        "data": [{"configformat": "natural_language", "config": {"rules": ["no medical advice"]}, "is_active": True}],
    })()

    llm_blocked = AsyncMock(return_value=True)
    monkeypatch.setattr(filtering, "_llm_policy_blocked", llm_blocked)

    chunks = [{"block_index": 0, "content": "Take 500mg twice a day.", "table_involved": False}]
    accepted = await filtering._filter_chunks(postgres, "tenant-1", chunks)

    assert accepted == []
    llm_blocked.assert_awaited_once_with("Take 500mg twice a day.", ["no medical advice"])


@pytest.mark.asyncio
async def test_filter_chunks_keeps_content_when_no_nlp_rules_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    postgres = AsyncMock()
    postgres.read.return_value = type("Resp", (), {"code": 200, "data": []})()
    llm_blocked = AsyncMock(return_value=True)  # must never be called with no rules
    monkeypatch.setattr(filtering, "_llm_policy_blocked", llm_blocked)

    chunks = [{"block_index": 0, "content": "anything at all", "table_involved": False}]
    accepted = await filtering._filter_chunks(postgres, "tenant-1", chunks)

    assert accepted == chunks
    llm_blocked.assert_not_awaited()


# ── Policy Center: extraction addendum reaches the LLM prompt ───────────────

@pytest.mark.asyncio
async def test_llm_generate_json_appends_extraction_addendum_to_system_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def _fake_generate_json(user: str, *, system_prompt: str, **kwargs: Any) -> str:
        captured["system_prompt"] = system_prompt
        return "{}"

    monkeypatch.setattr(shared.llm_client, "generate_json", _fake_generate_json)
    monkeypatch.setitem(shared.SYSTEM_PROMPTS, "chunk-summary", "BASE PROMPT")

    await shared._llm_generate_json(
        system_type="chunk-summary", user="hello", extra_instructions="Always tag currency amounts as MONEY.",
    )

    assert "BASE PROMPT" in captured["system_prompt"]
    assert "Always tag currency amounts as MONEY." in captured["system_prompt"]


@pytest.mark.asyncio
async def test_llm_generate_json_omits_addendum_block_when_tenant_has_none(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def _fake_generate_json(user: str, *, system_prompt: str, **kwargs: Any) -> str:
        captured["system_prompt"] = system_prompt
        return "{}"

    monkeypatch.setattr(shared.llm_client, "generate_json", _fake_generate_json)
    monkeypatch.setitem(shared.SYSTEM_PROMPTS, "chunk-summary", "BASE PROMPT")

    await shared._llm_generate_json(system_type="chunk-summary", user="hello")

    assert captured["system_prompt"] == "BASE PROMPT"
