from typing import Any
"""Transcript compression (sandbox_schema_docs.docx §1.8).

Tier 1 (output truncation) already lives at the point each tool result is
produced — sandbox_executor's truncate_output() for execute_python/
execute_sql (backend/services/sandbox_executor/output.py), and responder_node's
context_max_chunks/context_max_chars cap on the RRF-merged tool_results it
renders (nodes/model_harness/responder.py).

This is Tier 2: once a tool result has already been *consumed* (an
assistant reply was generated from it), replace its bulky content with a
short marker before it can cost tokens again on a later LLM call that
re-reads the same message list — exactly the doc's algorithm, applied to
messages shaped {"role": ..., "content": ..., "is_error": ...}.

Note: this codebase's persisted cross-turn `history` (backend/services/workflow-
runtime/db.py get_messages) only ever stores role in ("user", "assistant")
— the final synthesized answer, never the raw tool payload — so calling
this on `history` is currently a no-op safeguard rather than an active
token-reduction path. It is still wired in at conversation_runner.py
because that is the doc-specified choke point ("trước mỗi LLM call"), and
the day any node starts appending role="tool" entries into `messages`
within a turn (e.g. a future multi-hop tool-calling loop), this starts
doing real work with no further changes needed there.
"""
COMPRESS_THRESHOLD = 2000  # chars — only compress a result longer than this
COMPRESS_MARKER = "[result compressed: {n} chars]"


def compress_tool_results(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compress tool results already consumed in the transcript.

    'Consumed' = immediately followed by an assistant message. Error
    results are never compressed — they need to stay intact for debugging.
    """
    compressed = []
    for i, msg in enumerate(messages):
        if (
            msg.get("role") == "tool"
            and not msg.get("is_error", False)
            and i + 1 < len(messages)
            and messages[i + 1].get("role") == "assistant"
            and len(msg.get("content", "")) > COMPRESS_THRESHOLD
        ):
            n = len(msg["content"])
            compressed.append({**msg, "content": COMPRESS_MARKER.format(n=n)})
        else:
            compressed.append(msg)
    return compressed
