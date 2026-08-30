"""compress_tool_results (sandbox_schema_docs.docx §1.8). Constructs synthetic
role="tool" messages directly — this codebase's actual cross-turn `history`
never contains them today (see transcript_compression.py module docstring
for why), so these tests exercise the algorithm's contract on its own,
independent of whether anything in this repo currently produces that shape.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SVC_DIR = str(REPO_ROOT / "backend" / "services" / "workflow-runtime")


@pytest.fixture(autouse=True)
def _sys_path():
    added = [p for p in (_SVC_DIR,) if p not in sys.path]
    sys.path[:0] = added
    yield
    for p in added:
        if p in sys.path:
            sys.path.remove(p)


@pytest.mark.unit
def test_consumed_long_tool_result_is_compressed():
    from transcript_compression import compress_tool_results, COMPRESS_THRESHOLD, COMPRESS_MARKER

    long_content = "x" * (COMPRESS_THRESHOLD + 1)
    messages = [
        {"role": "user", "content": "query"},
        {"role": "tool", "content": long_content},
        {"role": "assistant", "content": "here's your answer"},
    ]

    out = compress_tool_results(messages)

    assert out[0] == messages[0]
    assert out[1]["content"] == COMPRESS_MARKER.format(n=len(long_content))
    assert out[2] == messages[2]


@pytest.mark.unit
def test_short_tool_result_is_not_compressed():
    from transcript_compression import compress_tool_results, COMPRESS_THRESHOLD

    short_content = "x" * (COMPRESS_THRESHOLD - 1)
    messages = [
        {"role": "tool", "content": short_content},
        {"role": "assistant", "content": "answer"},
    ]

    out = compress_tool_results(messages)

    assert out[0]["content"] == short_content


@pytest.mark.unit
def test_not_yet_consumed_tool_result_is_left_alone():
    """No assistant message follows — the result hasn't been used to
    produce a response yet, so it must stay intact."""
    from transcript_compression import compress_tool_results, COMPRESS_THRESHOLD

    long_content = "x" * (COMPRESS_THRESHOLD + 1)
    messages = [{"role": "tool", "content": long_content}]

    out = compress_tool_results(messages)

    assert out[0]["content"] == long_content


@pytest.mark.unit
def test_error_result_is_never_compressed():
    from transcript_compression import compress_tool_results, COMPRESS_THRESHOLD

    long_content = "x" * (COMPRESS_THRESHOLD + 1)
    messages = [
        {"role": "tool", "content": long_content, "is_error": True},
        {"role": "assistant", "content": "I hit an error"},
    ]

    out = compress_tool_results(messages)

    assert out[0]["content"] == long_content


@pytest.mark.unit
def test_user_and_assistant_messages_pass_through_unchanged():
    from transcript_compression import compress_tool_results

    messages = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]

    assert compress_tool_results(messages) == messages
