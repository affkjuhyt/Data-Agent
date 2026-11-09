"""Ask streaming contract (OP-4): commentary vs interrupt vs final.

Events on the Redis SSE bridge (`progress.publish`) fall into three channels the UI may
treat differently:

- **commentary** — short progress the person can skim (`planning`, `plan`, `progress`,
  `activity`, `block`); never the sole source of truth for the turn.
- **interrupt** — the turn stopped for the person (`clarification`, and later approve/budget).
- **final** — self-contained close of the turn (`answer`, plus `done` / `cancelled` / `error`).

Human-facing progress text uses the plan goal or step instruction, never a raw tool API name
(Cursor/Perplexity: say the work, not the call).
"""

from __future__ import annotations

from typing import Any, Literal

Channel = Literal["commentary", "interrupt", "final"]

COMMENTARY = frozenset({"planning", "plan", "progress", "activity", "block"})
INTERRUPT = frozenset({"clarification"})
FINAL = frozenset({"answer", "done", "cancelled", "error"})


def channel(event: str) -> Channel | None:
    if event in COMMENTARY:
        return "commentary"
    if event in INTERRUPT:
        return "interrupt"
    if event in FINAL:
        return "final"
    return None


def progress_message(*, phase: str, goal: str = "", step_title: str = "") -> str:
    """One short Vietnamese line for the progress strip — no tool identifiers."""
    goal = " ".join(goal.split())[:120]
    step_title = " ".join(step_title.split())[:120]
    if phase == "planning":
        return "Đang lập kế hoạch phân tích…"
    if phase == "plan" and goal:
        return f"Kế hoạch: {goal}"
    if phase == "act" and step_title:
        return f"Đang làm: {step_title}"
    if phase == "compose":
        return "Đang soạn câu trả lời…"
    return "Đang xử lý…"


def final_payload(
    *,
    question_id: str,
    answer_id: str,
    kind: str,
    status: str,
    headline: str = "",
    text: str = "",
    blocks: list[dict[str, Any]] | None = None,
    trust_summary: str | None = None,
) -> dict[str, Any]:
    """Self-contained final event: the UI can draw without waiting on GET /answers/{id}.

    `answer_id` remains so the client can still load the durable row (figures, verification).
    """
    body: dict[str, Any] = {
        "question_id": question_id,
        "answer_id": answer_id,
        "kind": kind,
        "status": status,
        "headline": headline or None,
        "text": text or None,
        "blocks": blocks or [],
    }
    if trust_summary:
        body["trust_summary"] = trust_summary
    return body
