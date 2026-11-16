"""The agent's reasoning as the live timeline shows it while a turn runs (E1, approach A): why a
plan was made or changed (the planner's thought, cleaned for a reader) and what each step found
(said here from the tool's result and the datasets, never by a model, so a figure in it is one the
data holds). No SQL, no table or metric codes, no raw errors."""

from __future__ import annotations

import logging
import re
from typing import Any

from services.worker_agent.agentic.tools.base import ToolResult
from services.worker_agent.agentic.verify import show
from services.worker_agent.agentic.workspace import Dataset, Workspace, number, plain
from services.worker_agent.understand.resolver import Vocabulary

logger = logging.getLogger(__name__)

# A plan step's id ("bước s2", "s3"): the reader sees titles, not ids.
_STEP_ID = re.compile(r"\b(?:bước\s+)?s\d+\b", re.IGNORECASE)
_CODE = re.compile(r"\b[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+\b")

_FAILED = {
    "arguments": "Tham số chưa đúng; agent viết lại hoặc đổi cách.",
    "unavailable": "Công cụ này đang không dùng được; agent đi đường khác.",
    "transient": "Nguồn dữ liệu chưa trả kịp.",
    "forbidden": "Bạn không có quyền xem phần dữ liệu này.",
}


def reader(text: str | None, vocab: Vocabulary | None) -> str:
    """Planner text for a reader: metric and dimension codes by their names, step ids as "bước
    trước", nothing longer than a few lines."""
    if not text:
        return ""
    names: dict[str, str] = {}
    if vocab is not None:
        names = {m.code: m.name for m in vocab.metrics} | {d.code: d.name for d in vocab.dimensions}
    out = _CODE.sub(
        lambda m: names.get(m.group(0), names.get(m.group(0).upper(), m.group(0))), text
    )
    out = _STEP_ID.sub("bước trước", out)
    return out.strip()[:400]


def _highlight(ds: Dataset) -> str:
    """The largest value of a dataset of members or periods and one measure: "cao nhất 2013-12
    (1.874.360,29)"."""
    if len(ds.columns) < 2 or not ds.rows:
        return ""
    measure = len(ds.columns) - 1
    best: tuple[Any, float] | None = None
    for row in ds.rows:
        value = number(row[measure])
        if value is not None and (best is None or value > best[1]):
            best = (plain(row[0]), value)
    if best is None or len(ds.rows) < 2:
        return ""
    return f"; cao nhất {best[0]} ({show(best[1])})"


def _dataset_line(ds: Dataset) -> str:
    if len(ds.rows) == 1 and len(ds.columns) >= 1:
        value = number(ds.rows[0][-1])
        if value is not None:
            return f"{ds.title}: {show(value)}{f' {ds.unit}' if ds.unit else ''}"
    return f"{ds.title}: {len(ds.rows)} dòng{_highlight(ds)}"


def said(tool: str, result: ToolResult, ws: Workspace, vocab: Vocabulary | None) -> str:
    """One line on what a step found, or why it did not; "" rather than an error, since it only
    describes a step and must never fail one."""
    try:
        return _said(tool, result, ws, vocab)
    except Exception:  # a line of the timeline is not worth a step
        logger.exception("could not say what step %s found", tool)
        return ""


def _said(tool: str, result: ToolResult, ws: Workspace, vocab: Vocabulary | None) -> str:
    d = result.detail or {}
    if not result.ok:
        if result.failure in _FAILED:
            return _FAILED[result.failure]
        if result.observation.startswith("code lỗi"):
            return "Phép tính bằng Python bị lỗi; agent sẽ sửa hoặc đổi cách."
        first = result.observation.split(";")[0].split("\n")[0]
        text = reader(first, vocab)
        return text[:1].upper() + text[1:] if text else "Bước này không thành công."
    if tool == "search_catalog":
        metrics = [str(m) for m in d.get("metrics", [])][:4]
        dims = [str(x) for x in d.get("dimensions", [])][:4]
        parts = [
            f"chỉ số {', '.join(metrics)}" if metrics else "",
            f"chiều {', '.join(dims)}" if dims else "",
        ]
        found = " và ".join(p for p in parts if p)
        return f"Tìm thấy {found}." if found else "Không thấy chỉ số nào khớp."
    if tool == "data_coverage":
        if not d.get("first"):
            return "Không thấy dữ liệu khớp với cách đọc này."
        line = f"{d.get('metric') or 'Dữ liệu'} có số từ {d['first']} đến {d.get('last')}"
        return line + (", kỳ cuối chưa đủ." if d.get("partial_last") else ".")
    if tool == "search_documents":
        hits = d.get("hits") or []
        return f"Tìm thấy {len(hits)} đoạn tài liệu liên quan." if hits else ""
    if tool == "explain_change":
        tried = [h for h in d.get("hypotheses", []) if "concentration" in h]
        if tried:
            best = tried[0]
            return f"Thay đổi tập trung nhất khi tách theo {best['dimension']}" + (
                f", lớn nhất ở {best['largest']}." if best.get("largest") else "."
            )
    datasets = [ws.datasets[i] for i in result.datasets if i in ws.datasets]
    if datasets:
        return "; ".join(_dataset_line(ds) for ds in datasets[:2]) + "."
    return ""
