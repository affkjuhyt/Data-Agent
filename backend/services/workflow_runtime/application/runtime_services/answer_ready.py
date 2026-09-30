"""In-process signal that an agent turn's answer is final.

conversation_runner replays an approved answer word by word. Work that only
needs the final text — the orchestrator's follow-up suggestions — can start the
moment that text is known instead of after the replay. A ContextVar keeps the
signal off the SSE wire: only a caller running in the same task context (the
orchestrator consuming the stream in-process) ever sees it.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from contextvars import ContextVar

logger = logging.getLogger(__name__)

answer_ready_hook: ContextVar[Callable[[str], None] | None] = ContextVar("answer_ready_hook", default=None)


def notify_answer_ready(text: str) -> None:
    """Call the hook set by the consumer, if any. Never raises: a failing hook
    must not break the answer stream it is observing."""
    hook = answer_ready_hook.get()
    if hook is None:
        return
    try:
        hook(text)
    except Exception:
        logger.exception("answer_ready | hook failed")
