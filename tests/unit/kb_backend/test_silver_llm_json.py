"""_llm_generate_json reads a JSON object a model wrapped in a ``` fence or in prose. Its
patterns were double-escaped inside raw strings (r"\\s"), so they needed a literal backslash,
never matched, and every such reply became {} without an error."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from libs.ingestion.silver import shared

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "raw",
    [
        '{"summary": "ok"}',
        '```json\n{"summary": "ok"}\n```',
        '```\n{"summary": "ok"}\n```',
        'Here is the result:\n{"summary": "ok"}\nDone.',
    ],
)
async def test_reply_shapes_parse(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setattr(shared.llm_client, "generate_json", AsyncMock(return_value=raw))
    system_type = next(iter(shared.SYSTEM_PROMPTS))
    assert await shared._llm_generate_json(system_type, "text") == {"summary": "ok"}
