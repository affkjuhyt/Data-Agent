"""Typed correction memory → steward gate (OP-7 / bất biến 12).

Feedback memories stay personal recall only. The same correction said CORRECTION_THRESHOLD times
opens a `feedback_fix` inbox proposal (needs-you) with why + how_to_apply — never auto-applies
a metric, synonym, or verified query.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from services.worker_agent.events import Event
from services.worker_agent.proposals import _open

logger = logging.getLogger(__name__)

CORRECTION_THRESHOLD = 3
# Stable proposal ids for the same (space, user, content_hash) so redelivery opens once.
_NAMESPACE = uuid.UUID("a7e2c4b1-9f3d-4e8a-b2c6-1d5f0e9a8b7c")


def should_escalate(*, kind: str, times: int) -> bool:
    return kind == "feedback" and times >= CORRECTION_THRESHOLD


async def open_feedback_fix(
    *,
    org_id: uuid.UUID,
    space_id: uuid.UUID,
    user_id: uuid.UUID,
    memory_fact_id: uuid.UUID,
    content: str,
    times: int,
    content_hash: str,
    question_id: uuid.UUID | None = None,
) -> uuid.UUID | None:
    """Open (or no-op on conflict) a feedback_fix proposal for steward review."""
    if not should_escalate(kind="feedback", times=times):
        return None
    event_id = uuid.uuid5(_NAMESPACE, f"{space_id}:{user_id}:{content_hash}")
    event = Event(
        event_id,
        org_id,
        "memory.feedback_repeated",
        "memory_fact",
        memory_fact_id,
        {
            "proposal_id": str(event_id),
            "memory_fact_id": str(memory_fact_id),
            "times": times,
            "question_id": str(question_id) if question_id else None,
        },
    )
    why = f"Người dùng nhắc lại cùng một sửa lỗi {times} lần trong Data Space."
    how = (
        "Xem nội dung feedback; nếu đúng nghiệp vụ thì mở đề xuất synonym / metric_change / "
        "example_query riêng — không áp trực tiếp từ trí nhớ hội thoại."
    )
    try:
        return await _open(
            event,
            data_space_id=space_id,
            kind="feedback_fix",
            target_service="qa",
            target_ref=memory_fact_id,
            title=f"Sửa lỗi lặp lại ({times}x): {content[:80]}",
            diff={
                "why": why,
                "how_to_apply": how,
                "feedback": content,
                "times": times,
                "content_hash": content_hash,
                "memory_fact_id": str(memory_fact_id),
            },
            proposed_by=user_id,
            eval_run_id=None,
            impact={"memory_fact_id": str(memory_fact_id)},
            impact_level="medium",
        )
    except Exception:
        # Memory store must not fail the Ask turn if inbox is unavailable.
        logger.exception("feedback_fix proposal not opened for memory_fact %s", memory_fact_id)
        return None


def proposal_diff_shape() -> dict[str, Any]:
    """Documented shape for unit tests / operators."""
    return {"why": str, "how_to_apply": str, "feedback": str, "times": int}
