"""The Understander's agent pass (the chat upgrade, item 1, stage 1): when neither the rule pass nor
the one-shot model reading places a metric, the planner model works the question out with tools
over the Data Space's own semantic layer, as an analyst would, before anything runs.

The one-shot reading (llm_reader) is handed the whole catalogue once and must pick codes from it.
Here the model looks things up instead, a few turns at most (MAX_TURNS):

  * `search_metrics {text}`: the certified metrics whose name, code or approved synonyms share
    words with the text;
  * `describe_metric {metric}`: its unit, the period it is read over, the cuts its owner allows;
  * `search_values {text, dimension?}`: the members of the value index that look like the text
    ("Mỹ" → United States), so a filter names a real member;
  * `propose {metric, group_by, filters, period, top_n, top_desc}`: the reading. It is compiled
    on the spot; a compile error goes back to the model as the tool's answer, and it may fix the
    reading on its next turn;
  * `ask_user {question, options}`: the question cannot be read without the person.

What the model may see and do is bounded the way the rest of the agent is: only metrics the asker
is not denied (SpaceLayer.blocked_metric_ids) are found, described or proposed (permissions before
generation, invariant 13); the tools read metadata only, never a row of data, so the model never
sees or computes a figure (invariant 6); a proposal goes through resolver.from_chips and the
grounding check of the one-shot reading, so a code it invents is refused and a reading the
question names only in part is asked back (invariant 5). The answer itself is then made by the
same path as any other: compiler, executor, narrator.

The steps (tool, arguments, a summary of what it returned) are kept for the turn's trace.
"""

from __future__ import annotations

import datetime
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel

from services.worker_agent import turn_trace
from services.worker_agent.llm import Gateway, ModelUnavailable
from services.worker_agent.semantic import compiler as c
from services.worker_agent.semantic.loader import SpaceLayer
from services.worker_agent.understand.llm_reader import Turn, history_of
from services.worker_agent.understand.packs.base import iso_answer
from services.worker_agent.understand.resolver import (
    Chip,
    MetricEntry,
    Understanding,
    from_chips,
    grounding,
)
from services.worker_agent.understand.text import find_period, norm
from services.worker_agent.understand.value_retrieval import similarity

MAX_TURNS = 5
MAX_FOUND = 8
MAX_VALUES = 10
VALUE_SIMILARITY = 0.5
MODEL_CONFIDENCE = 0.8

ToolName = Literal["search_metrics", "describe_metric", "search_values", "propose", "ask_user"]

SYSTEM = """Bạn là chuyên viên phân tích, đọc một câu hỏi số liệu (tiếng Việt, tiếng Anh hoặc ngôn ngữ khác)
và tìm đúng chỉ số, chiều, bộ lọc và kỳ trong lớp ngữ nghĩa của Data Space bằng công cụ. Không đoán mã:
chỉ dùng mã mà công cụ trả về. Không bao giờ tự đưa ra con số.
Mỗi lượt trả về đúng một JSON: {"tool": "<tên>", "args": {...}, "reason": "<một câu vì sao>"}.
Công cụ:
- search_metrics {"text": "<cụm từ chỉ đại lượng>"}: tìm chỉ số theo tên, mã, từ đồng nghĩa.
- describe_metric {"metric": "<mã>"}: đơn vị, cách tính theo kỳ, các chiều được phép chia.
- search_values {"text": "<giá trị>", "dimension": "<mã chiều hoặc null>"}: tìm giá trị của một chiều.
- propose {"metric": "<mã>", "group_by": ["<mã chiều>"], "filters": {"<mã chiều>": ["<mã giá trị>"]},
  "period": "<kỳ ISO hoặc null>", "top_n": <số hoặc null>, "top_desc": true}: cách đọc cuối cùng.
  period theo ISO, tính từ ngày hôm nay được cho: "2026", "2026-Q3", "2026-09", "2026-09-30" hoặc
  "2026-07-01/2026-10-01". Chỉ dùng chiều mà describe_metric nói được phép.
- ask_user {"question": "<câu hỏi lại>", "options": ["<mã chỉ số>"]}: khi câu hỏi mơ hồ hoặc danh
  mục không có đại lượng được hỏi; đừng chọn một chỉ số khác đại lượng chỉ vì nó gần nhất.
Thường: search_metrics, rồi describe_metric, rồi search_values nếu có giá trị cần lọc, rồi propose."""


class AgentTurn(BaseModel):
    tool: ToolName
    args: dict[str, Any] = {}
    reason: str = ""


@dataclass
class Step:
    tool: str
    args: dict[str, Any]
    reason: str
    result: Any


@dataclass
class Resolved:
    understanding: Understanding
    steps: list[Step] = field(default_factory=list)
    model: str = ""
    tokens: int = 0


def _visible(layer: SpaceLayer) -> dict[str, MetricEntry]:
    """The metrics this asker may use, by code: certified, in force, not denied (invariant 13)."""
    return {
        m.code: m
        for m in layer.vocabulary.metrics
        if m.metric_id in layer.versions and m.metric_id not in layer.blocked_metric_ids
    }


def search_metrics(layer: SpaceLayer, text: str) -> list[dict[str, Any]]:
    words = set(norm(text).split())
    if not words:
        return []
    synonyms: dict[str, set[str]] = {}
    for phrase, target in layer.vocabulary.synonyms.items():
        if target[0] == "metric":
            synonyms.setdefault(target[1], set()).update(phrase.split())
    scored = []
    for code, m in _visible(layer).items():
        named = set(norm(m.name).split()) | set(norm(code.replace("_", " ")).split())
        named |= synonyms.get(str(m.metric_id), set())
        overlap = len(words & named)
        if overlap:
            scored.append((overlap, m.certified, m.name, code))
    scored.sort(key=lambda s: (-s[0], not s[1], s[2]))
    return [
        {"metric": code, "name": name, "certified": certified}
        for _, certified, name, code in scored[:MAX_FOUND]
    ]


def describe_metric(layer: SpaceLayer, code: str) -> dict[str, Any] | str:
    m = _visible(layer).get(code)
    if m is None:
        return f"không có chỉ số {code} (hoặc bạn không được xem)"
    version = layer.versions[m.metric_id]
    cuts = [
        {"dimension": d.code, "name": d.name}
        for d in layer.vocabulary.dimensions
        if d.dimension_id in version.allowed_dimension_ids and not d.is_time
    ]
    time = c.time_dimension_of(version, layer.layer)
    return {
        "metric": code,
        "name": m.name,
        "unit": version.unit,
        "period": version.period_semantics,
        "time_grain": version.time_grain,
        "time_dimension": time.code if time else None,
        "dimensions": cuts,
    }


def search_values(layer: SpaceLayer, text: str, dimension: str | None) -> list[dict[str, Any]]:
    wanted = norm(text)
    if not wanted:
        return []
    scored = []
    for v in layer.vocabulary.values:
        if dimension and v.dimension_code != dimension:
            continue
        label = norm(v.label)
        score = 1.0 if wanted == label else similarity(wanted, label)
        if score >= VALUE_SIMILARITY:
            scored.append((score, v.dimension_code, v.value_code, v.label))
    # An approved synonym of a value names it as its label does ("Mỹ" for "United States").
    for phrase, target in layer.vocabulary.synonyms.items():
        if target[0] == "value" and phrase == wanted and (not dimension or target[1] == dimension):
            label = next(
                (
                    v.label
                    for v in layer.vocabulary.values
                    if (v.dimension_code, v.value_code) == (target[1], target[2])
                ),
                target[2],
            )
            scored.append((1.0, target[1], target[2], label))
    scored.sort(key=lambda s: -s[0])
    seen: set[tuple[str, str]] = set()
    out = []
    for score, dim, value, label in scored:
        if (dim, value) in seen:
            continue
        seen.add((dim, value))
        out.append({"dimension": dim, "value": value, "label": label, "score": round(score, 2)})
    return out[:MAX_VALUES]


def _proposal(
    layer: SpaceLayer, args: dict[str, Any], today: datetime.date
) -> tuple[Understanding | None, str]:
    """The reading the model proposes, as from_chips reads an edited plan, and compiled; or why
    it cannot be used, said so the model may fix it."""
    visible = _visible(layer)
    code = str(args.get("metric") or "")
    if code not in visible:
        return None, f"không có chỉ số {code}; dùng một mã search_metrics trả về"
    chips: list[dict[str, object]] = [{"kind": "metric", "value": code}]
    group_by = [str(g) for g in args.get("group_by") or []]
    chips += [{"kind": "dimension", "value": g} for g in group_by]
    filters = args.get("filters") or {}
    if isinstance(filters, dict):
        chips += [
            {"kind": "filter", "value": f"{dim}={member}"}
            for dim, members in filters.items()
            for member in (members if isinstance(members, list) else [members])
        ]
    period = None
    if args.get("period"):
        period = iso_answer(str(args["period"]))
        if period is None:
            return None, f"kỳ {args['period']} không đúng dạng ISO"
        chips.append(
            {"kind": "period", "value": f"{period.start}/{period.end}", "label": period.label}
        )
    u = from_chips(chips, layer.vocabulary)
    if u.decision != "proceed" or u.metric is None:
        return None, f"không đọc được: {', '.join(u.unmapped) or 'thiếu chỉ số'}"
    top_n = args.get("top_n")
    if isinstance(top_n, int) and 0 < top_n <= 100:
        u.top_n, u.top_desc = top_n, bool(args.get("top_desc", True))
    version = layer.versions[u.metric.metric_id]
    try:
        c.compile_metric(
            c.Request(
                version,
                group_by=list(u.group_by),
                filters=u.filters,
                period=c.Period(u.period.start, u.period.end) if u.period else None,
                top_n=u.top_n,
                top_desc=u.top_desc,
            ),
            layer.layer,
            dialect="postgres",
        )
    except c.CompileError as exc:
        return None, f"không chạy được: {exc}"
    return u, "ok"


def _summary(result: Any) -> str:
    text = json.dumps(result, ensure_ascii=False, default=str)
    return text if len(text) <= 1500 else text[:1500] + "…"


async def resolve(
    question: str,
    layer: SpaceLayer,
    model: Gateway,
    *,
    today: datetime.date,
    history: Sequence[Turn] = (),
) -> Resolved | None:
    """The reading the planner model works out with the tools, or None when it found none (the
    question goes on to the SQL and document paths, as before)."""
    if not _visible(layer):
        return None
    talk = history_of(history)
    context = f"\n\nHội thoại trước (cũ đến mới):\n{talk}" if talk else ""
    transcript = f"Hôm nay: {today.isoformat()}{context}\n\nCâu hỏi: {question}"
    steps: list[Step] = []
    tokens, model_name = 0, ""
    for _ in range(MAX_TURNS):
        try:
            reply = await model.json_chat("planner", SYSTEM, transcript, AgentTurn, max_tokens=400)
        except ModelUnavailable:
            return None
        tokens += reply.tokens
        model_name = reply.model
        turn = reply.content
        args = turn.args or {}
        result: Any
        if turn.tool == "search_metrics":
            result = search_metrics(layer, str(args.get("text") or question))
        elif turn.tool == "describe_metric":
            result = describe_metric(layer, str(args.get("metric") or ""))
        elif turn.tool == "search_values":
            dim = args.get("dimension")
            result = search_values(layer, str(args.get("text") or ""), str(dim) if dim else None)
        elif turn.tool == "ask_user":
            options = [
                _visible(layer)[str(o)]
                for o in args.get("options") or []
                if str(o) in _visible(layer)
            ]
            await _taken(
                steps, Step(turn.tool, args, turn.reason, "asked"), "done" if options else "failed"
            )
            if not options:
                return None
            return Resolved(
                Understanding(decision="clarify", options=options[:3]), steps, model_name, tokens
            )
        else:  # propose
            u, why = _proposal(layer, args, today)
            if u is not None:
                assert u.metric is not None
                # The question's own words decide (invariant 5), as for the one-shot reading.
                if grounding(question, u, layer.vocabulary, today=today) == "partial":
                    await _taken(steps, Step(turn.tool, args, turn.reason, "asked back: partial"))
                    return Resolved(
                        Understanding(decision="clarify", options=[u.metric], to_confirm=True),
                        steps,
                        model_name,
                        tokens,
                    )
                # The question's period, read by rule, wins over the model's when it names one.
                found = find_period(question, today, layer.vocabulary.calendar)
                if found is not None:
                    u.period = found
                u.chips = [
                    Chip(chip.kind, chip.value, chip.label, "default", MODEL_CONFIDENCE)
                    for chip in u.chips
                ]
                await _taken(steps, Step(turn.tool, args, turn.reason, "ok"))
                return Resolved(u, steps, model_name, tokens)
            result = why
        # A proposal that does not compile goes back to the model; on the timeline it failed.
        await _taken(
            steps,
            Step(turn.tool, args, turn.reason, result),
            "failed" if turn.tool == "propose" else "done",
        )
        transcript += (
            f"\n\nLượt {len(steps)}: {json.dumps({'tool': turn.tool, 'args': args}, ensure_ascii=False)}"
            f"\nKết quả: {_summary(result)}"
        )
    return None


# What the Ask timeline calls each tool turn.
TITLES = {
    "search_metrics": "Tìm chỉ số",
    "describe_metric": "Xem định nghĩa chỉ số",
    "search_values": "Tìm giá trị",
    "propose": "Đề xuất",
    "ask_user": "Hỏi lại bạn",
}


async def _taken(steps: list[Step], step: Step, status: str = "done") -> None:
    """Keep the turn and show it on the timeline: the tool, its arguments and the cut result,
    never the prompt."""
    steps.append(step)
    await turn_trace.record_step(
        "use_tool",
        title=TITLES[step.tool],
        target=step.reason,
        detail={"tool": step.tool, "args": step.args, "result": _summary(step.result)},
        status=status,
    )


def trace_of(resolved: Resolved) -> list[dict[str, Any]]:
    """The steps as the turn's trace keeps them: tool, arguments, why, what came back (cut)."""
    return [
        {"tool": s.tool, "args": s.args, "reason": s.reason, "result": _summary(s.result)}
        for s in resolved.steps
    ]
