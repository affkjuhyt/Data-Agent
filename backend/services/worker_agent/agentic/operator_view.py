"""Operator observability for one Ask turn (OP-8).

Steward/analyst L3 view: plan DAG, which steps failed, whether we replanned or handed over,
trust labels on datasets used — without raw row PII. Stored on qa.plan.steps["operator"] so
GET /v1/questions/{id} (plan) answers "bước nào fail, vì sao, đã replan chưa" without pod logs.
"""

from __future__ import annotations

from typing import Any

from services.worker_agent.agentic import plan as p


def build(
    *,
    plan: p.Plan | None,
    replans: int = 0,
    gate_retries: int = 0,
    clarify: bool = False,
    handed_back: bool = False,
    cancelled: bool = False,
    dataset_trusts: dict[str, str] | None = None,
    audience: str = "analyst",
) -> dict[str, Any]:
    steps = p.view(plan) if plan else []
    failed = [s for s in steps if s.get("status") == "failed"]
    skipped = [s for s in steps if s.get("status") == "skipped"]
    trusts = sorted({t for t in (dataset_trusts or {}).values() if t})
    why = _why(
        failed=failed,
        replans=replans,
        gate_retries=gate_retries,
        clarify=clarify,
        handed_back=handed_back,
        cancelled=cancelled,
    )
    return {
        "goal": plan.goal if plan else "",
        "mode": plan.mode if plan else None,
        "dag": steps,
        "failed_step_ids": [s["id"] for s in failed],
        "skipped_step_ids": [s["id"] for s in skipped],
        "replans": replans,
        "gate_retries": gate_retries,
        "clarify": clarify,
        "handed_back": handed_back,
        "cancelled": cancelled,
        "trusts": trusts,
        "audience": audience,
        "why": why,
    }


def _why(
    *,
    failed: list[dict[str, Any]],
    replans: int,
    gate_retries: int,
    clarify: bool,
    handed_back: bool,
    cancelled: bool,
) -> str:
    if cancelled:
        return "Người hỏi đã dừng lượt."
    if clarify:
        return "Đang chờ làm rõ từ người hỏi."
    if handed_back:
        reason = "Đã chuyển sang đường trả lời chuẩn"
        if gate_retries:
            reason += f" sau {gate_retries} lần kiểm lại trước PRESENT"
        if failed:
            reason += f"; bước lỗi: {', '.join(s['id'] for s in failed)}"
        return reason + "."
    if failed:
        ids = ", ".join(s["id"] for s in failed)
        extra = f"; đã replan {replans} lần" if replans else ""
        return f"Có bước lỗi ({ids}){extra}."
    if replans:
        return f"Hoàn tất sau {replans} lần điều chỉnh kế hoạch."
    return "Hoàn tất theo kế hoạch."
