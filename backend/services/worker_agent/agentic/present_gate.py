"""Deterministic VERIFY before PRESENT (OP-5 / AGC-11).

Compose must not invent figures when nothing was fetched, named members were dropped, every
measure is empty, tables are out of time, or the metrics read do not measure what was asked.
Column mismatches on kpi/chart/table drafts are also a gate (output contract: right calc, wrong
column → replan or hand over, not a silent full-table fallback).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from services.worker_agent.agentic import blocks as b
from services.worker_agent.agentic.workspace import Workspace, number

FIGURE_BLOCKS = frozenset({"kpi", "chart", "table"})


@dataclass(frozen=True)
class GateHit:
    target: str
    detail: dict[str, Any]
    feedback: str


def no_figure(ws: Workspace) -> bool:
    """True when datasets came back but none holds a measure other than zero or empty."""
    measures: list[float | None] = []
    for d in ws.datasets.values():
        numeric = [
            i
            for i in range(len(d.columns))
            if d.rows and all(number(r[i]) is not None for r in d.rows if r[i] is not None)
        ]
        if numeric:
            measures += [number(r[numeric[-1]]) for r in d.rows]
    return bool(ws.datasets) and all(m in (None, 0) for m in measures)


def draft_column_errors(drafts: list[b.BlockDraft], ws: Workspace) -> list[str]:
    """Reasons figure drafts name columns (or datasets) the workspace does not hold."""
    errors: list[str] = []
    for d in drafts:
        if d.type not in FIGURE_BLOCKS:
            continue
        try:
            b.materialize(d, ws, "contract")
        except b.BlockError as exc:
            errors.append(f"{d.type}: {exc}")
    return errors


def before_write(
    *,
    mode: str,
    ws: Workspace,
    dropped_members: list[str],
    unnamed: list[str],
    out_of_time: list[str],
    plan_steps: list[dict[str, Any]] | None = None,
) -> GateHit | None:
    """First failing check before the reporter writes. None means PRESENT may proceed."""
    if out_of_time:
        return GateHit(
            target=f"Dữ liệu {', '.join(out_of_time)} quá hạn cập nhật; chuyển sang cách trả lời chuẩn.",
            detail={"out_of_time": out_of_time},
            feedback="",  # BR-178: hand over immediately, no replan
        )
    if mode == "analyze" and not ws.citations and not any(d.rows for d in ws.datasets.values()):
        return GateHit(
            target="Chưa lấy được số liệu",
            detail={"steps": plan_steps or []},
            feedback=(
                "chưa bước nào lấy được số liệu; xem lại mã chỉ số, chiều, kỳ (data_coverage), hoặc"
                " dùng explore_sql"
            ),
        )
    if dropped_members:
        joined = ", ".join(dropped_members)
        return GateHit(
            target=f"Chưa lọc theo {joined}",
            detail={"dropped": dropped_members},
            feedback=(
                f"câu hỏi nêu {joined} mà chưa dataset nào lọc hay chia theo đó; tìm giá"
                " trị bằng search_values rồi truy vấn lại có lọc"
            ),
        )
    if mode == "analyze" and no_figure(ws):
        return GateHit(
            target="Không có số liệu cho kỳ hoặc bộ lọc được hỏi",
            detail={"datasets": list(ws.datasets)},
            feedback=(
                "mọi số liệu lấy được đều rỗng hoặc bằng 0 cho kỳ hoặc bộ lọc được hỏi; gọi"
                " data_coverage để biết kỳ có dữ liệu, rồi truy vấn kỳ đầy đủ gần nhất (cùng độ dài)"
                " và kỳ để so sánh"
            ),
        )
    if unnamed:
        joined = " ".join(unnamed)
        return GateHit(
            target=f"Chỉ số đã dùng không đo '{joined}'",
            detail={"unnamed": unnamed},
            feedback=(
                f"không chỉ số nào đã đọc đo '{joined}' mà câu hỏi hỏi; tìm chỉ số khác"
                " (search_catalog) hoặc dùng explore_sql cho phần đó"
            ),
        )
    return None


def after_drafts(
    *,
    mode: str,
    drafts: list[b.BlockDraft],
    made: int,
    ws: Workspace,
) -> GateHit | None:
    """When every figure draft fails the column contract, stop instead of inventing a layout."""
    if mode != "analyze" or made > 0 or not drafts:
        return None
    errors = draft_column_errors(drafts, ws)
    if not errors:
        return None
    return GateHit(
        target="Khối số liệu không khớp cột của dataset",
        detail={"errors": errors[:8]},
        feedback=(
            "các khối kpi/chart/table trỏ dataset hoặc cột không có trong kết quả đã lấy;"
            " chọn đúng id dataset và tên cột từ <datasets>, hoặc lấy lại đúng cột"
        ),
    )
