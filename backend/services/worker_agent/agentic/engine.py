"""The dynamic analyst agent's turn: a graph of nodes over one state.

    prepare → plan → act ⟲ → review → compose → persist
                      ↘ replan ↗        (a failed step, at most MAX_REPLANS times)
                      ↘ ask → persist   (the planner or a tool asks the person back)
    any node after prepare ↘ persist    (the asker cancelled: the running node is stopped)

The planner chooses the steps and tools for this question; the executor writes each tool's
arguments from the step and what earlier steps found; a failed step goes back to the planner with
what was observed (RD-Agent's feedback loop, where an error is feedback, not the end); when the plan
is walked, a reviewer may add steps once (ragflow's sufficiency check). The reporter then composes
the answer as blocks of its choosing, whose data the server fills from the datasets and whose
numbers it checks (blocks.py, verify.py). Blocks are published as they are made and stored on the
answer.

`run` returns False, having shown and stored nothing, when the turn should go to the governed path
instead: no model gateway, an edited plan, or a model that fails before the plan is made.
"""

from __future__ import annotations

import asyncio
import dataclasses
import datetime
import json
import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select

from libs import clock
from libs.dataagent_db import scoped_session, uuid7
from libs.dataagent_db.models import qa
from services.worker_agent import turn_trace
from services.worker_agent.agentic import blocks as b
from services.worker_agent.agentic import memory, prompts, signals, verify
from services.worker_agent.agentic import plan as p
from services.worker_agent.agentic.graph import END, Node, Router, StateGraph
from services.worker_agent.agentic.grounded import unnamed
from services.worker_agent.agentic.tools import TOOLS, Clarify, ToolContext, ToolResult
from services.worker_agent.agentic.workspace import TRUST_LABEL, Workspace, number
from services.worker_agent.answer.verification import verify as verify_answer
from services.worker_agent.events import Event
from services.worker_agent.llm import Gateway, ModelUnavailable
from services.worker_agent.progress import is_cancel_requested, publish
from services.worker_agent.query.scope import can_ask_data_space
from services.worker_agent.semantic.loader import load
from services.worker_agent.understand import analyst
from services.worker_agent.understand.resolver import metric_words, quantity_words
from services.worker_agent.understand.text import find_period, norm

logger = logging.getLogger(__name__)

ROLE = "svc_qa"
AGENT_NAME = "Agent Phân tích"
MAX_REPLANS = 2
MAX_REVIEWS = 1
MAX_STEP_RUNS = 14
HISTORY_TURNS = 5
# The timeline's action for a tool whose name the governed path already uses for another step.
ACTION_OF = {"analyze": "run_python"}


def engine_enabled() -> bool:
    return os.environ.get("ASK_ENGINE", "agentic").strip().lower() == "agentic"


class ExecutorReply(BaseModel):
    args: dict[str, Any] = Field(default_factory=dict)


class ReviewReply(BaseModel):
    enough: bool = True
    reason: str = ""
    steps: list[p.PlanStep] = Field(default_factory=list)


@dataclass
class State:
    event: Event
    question_id: uuid.UUID
    model: Gateway
    ctx: ToolContext | None = None
    conversation_id: uuid.UUID | None = None
    history: str = ""
    plan: p.Plan | None = None
    replans: int = 0
    reviews: int = 0
    step_runs: int = 0
    failed: p.PlanStep | None = None
    clarify: Clarify | None = None
    refused: str | None = None
    handed_back: bool = False
    composition: list[dict[str, Any]] = field(default_factory=list)
    headline: str = ""
    dropped_sentences: int = 0
    numbers: int = 0
    started: datetime.datetime = field(default_factory=lambda: datetime.datetime.now(datetime.UTC))
    # What the person said in earlier conversations that bears on this question (memory.py).
    memories: list[memory.Memory] = field(default_factory=list)
    # The catalogue's signals about the tables read (signals.py): caveats, tables out of time.
    signals: signals.Signals = field(default_factory=lambda: signals.Signals())
    answered: bool = False
    cancelled: bool = False


# ---------------------------------------------------------------- prepare


async def _history(session: Any, question: qa.Question) -> str:
    """The turns before this one, oldest first: what was asked, what the agent planned, what it
    answered (DataAgent keeps the plan as the conversation's memory; we keep both)."""
    rows = (
        await session.execute(
            select(qa.Question.id, qa.Question.text)
            .where(
                qa.Question.conversation_id == question.conversation_id,
                qa.Question.seq < question.seq,
            )
            .order_by(qa.Question.seq.desc())
            .limit(HISTORY_TURNS)
        )
    ).all()
    if not rows:
        return ""
    ids = [r.id for r in rows]
    answers = {
        a.question_id: a
        for a in (
            await session.scalars(select(qa.Answer).where(qa.Answer.question_id.in_(ids)))
        ).all()
    }
    plans = {
        pl.question_id: pl
        for pl in (
            await session.scalars(
                select(qa.Plan).where(qa.Plan.question_id.in_(ids)).order_by(qa.Plan.version)
            )
        ).all()
    }
    lines = []
    for r in reversed(rows):
        lines.append(f"Người dùng: {r.text}")
        plan_row = plans.get(r.id)
        steps = plan_row.steps if plan_row is not None else None
        if isinstance(steps, dict) and steps.get("goal"):
            lines.append(f"Kế hoạch: {steps['goal']}")
        answer = answers.get(r.id)
        if answer is not None and answer.text:
            lines.append(f"Trả lời: {answer.text[:400]}")
    return "\n".join(lines)


async def prepare(s: State) -> None:
    event = s.event
    async with scoped_session(ROLE, event.org_id, event.payload.get("user_id")) as session:
        question = await session.get(qa.Question, s.question_id)
        if question is None or question.status != "received":
            s.handed_back = True
            return
        conversation = await session.get(qa.Conversation, question.conversation_id)
        assert conversation is not None
        space_id, user_id = conversation.data_space_id, conversation.user_id
        if not await can_ask_data_space(session, data_space_id=space_id, principal_id=user_id):
            # The governed path refuses with the right code and next action.
            s.handed_back = True
            return
        today = question.as_of or clock.today(await clock.space_zone(session, space_id))
        layer = await load(session, space_id)
        s.history = await _history(session, question)
        s.conversation_id = conversation.id
        question_text = question.text
        s.ctx = ToolContext(
            org_id=event.org_id,
            space_id=space_id,
            user_id=user_id,
            question_id=s.question_id,
            question=question.text,
            today=today,
            as_of=question.as_of,
            purpose=question.purpose,
            layer=layer,
            model=s.model,
            ws=Workspace(),
            period=find_period(question.text, today, layer.vocabulary.calendar),
        )
    try:
        known = await memory.load(event.org_id, space_id, user_id)
        s.memories = memory.rank(question_text, known, datetime.datetime.now(datetime.UTC))
    except Exception:
        # Memory helps; a turn never fails for it (a cluster before v3_0027, a slow read).
        logger.exception("question %s: memory not recalled", s.question_id)


# ---------------------------------------------------------------- plan


def _catalog(s: State) -> str:
    assert s.ctx is not None
    visible = analyst._visible(s.ctx.layer)
    lines = [f"{code}: {m.name}" for code, m in list(visible.items())[:60]]
    dims = [
        f"{d.code}: {d.name}{' (thời gian)' if d.is_time else ''}"
        for d in s.ctx.layer.vocabulary.dimensions[:60]
    ]
    return "Chỉ số:\n" + ("\n".join(lines) or "(không có)") + "\nChiều:\n" + "\n".join(dims)


def _tool_cards() -> str:
    return "\n".join(t.card() for t in TOOLS.values())


def _situation(s: State) -> str:
    assert s.ctx is not None
    parts = [f"Hôm nay: {s.ctx.today.isoformat()}"]
    if s.history:
        parts.append(f"<conversation>\n{s.history}\n</conversation>")
    if s.memories:
        parts.append(memory.render(s.memories))
    parts.append(f"<question>\n{s.ctx.question}\n</question>")
    if s.ctx.period is not None:
        p_ = s.ctx.period
        parts.append(
            f"Kỳ câu hỏi nêu (đọc theo quy tắc, đúng): {p_.label} = period "
            f'"{p_.start.isoformat()}/{p_.end.isoformat()}" của query_metric. '
            "Đây là kỳ, không phải giá trị để search_values."
        )
    return "\n\n".join(parts)


def _progress(s: State) -> str:
    """The plan as walked so far and every dataset, for the replanner, reviewer and executor."""
    assert s.ctx is not None and s.plan is not None
    steps = [
        {
            "id": st.id,
            "tool": st.tool,
            "instruction": st.instruction,
            "status": st.status,
            "datasets": st.datasets,
            "observation": st.observation[:1200],
        }
        for st in s.plan.steps
    ]
    return (
        f"<plan goal={json.dumps(s.plan.goal, ensure_ascii=False)}>\n"
        f"{json.dumps(steps, ensure_ascii=False)}\n</plan>\n\n"
        f"<datasets>\n{s.ctx.ws.summary()}\n</datasets>"
    )


async def _ask_plan(s: State, system: str, user: str) -> p.Plan:
    error = ""
    for _ in range(2):
        reply = await s.model.json_chat(
            "planner",
            system,
            user + (f"\n\nKế hoạch trước bị từ chối: {error}" if error else ""),
            p.Plan,
            max_tokens=1600,
        )
        try:
            return p.validate(reply.content, set(TOOLS))
        except p.PlanInvalid as exc:
            error = str(exc)
    raise p.PlanInvalid(error)


async def plan(s: State) -> None:
    system = prompts.PLANNER.replace("{tools}", _tool_cards()).replace("{catalog}", _catalog(s))
    async with turn_trace.stepping("plan", title="Lập kế hoạch", target="") as step:
        try:
            s.plan = await _ask_plan(s, system, _situation(s))
        except (ModelUnavailable, p.PlanInvalid) as exc:
            step.status, step.summary = "failed", "Không lập được kế hoạch."
            logger.info(
                "question %s: agentic plan failed (%s), governed path",
                s.question_id,
                type(exc).__name__,
            )
            s.handed_back = True
            return
        step.detail = {
            "goal": s.plan.goal,
            "thought": s.plan.thought,
            "mode": s.plan.mode,
            "steps": p.view(s.plan),
        }
    if s.plan.mode == "ask":
        s.clarify = Clarify(
            s.plan.clarification or "Bạn có thể nói rõ hơn câu hỏi?", s.plan.options[:5]
        )
    await publish(
        str(s.question_id),
        "plan",
        {"question_id": str(s.question_id), "goal": s.plan.goal, "steps": p.view(s.plan)},
    )


# ---------------------------------------------------------------- act


async def _arguments(s: State, step: p.PlanStep, error: str) -> Any:
    """The tool's arguments: the planner's own when they validate and the step needs nothing
    computed from earlier results; else written by the executor model."""
    tool = TOOLS[step.tool]
    if not error and step.tool != "analyze":
        try:
            return tool.Args.model_validate(step.args)
        except ValidationError:
            pass
    user = (
        f"{_situation(s)}\n\n{_progress(s)}\n\n"
        f"<step id={step.id} tool={step.tool}>\n{step.instruction}\nTham số kế hoạch: "
        f"{json.dumps(step.args, ensure_ascii=False)}\n</step>\n\n"
        f"Schema tham số: {json.dumps(tool.schema(), ensure_ascii=False)}"
        + (f"\n\nLần trước lỗi: {error}" if error else "")
    )
    for _ in range(2):
        reply = await s.model.json_chat(
            "planner", prompts.EXECUTOR, user, ExecutorReply, max_tokens=1500
        )
        try:
            return tool.Args.model_validate(reply.content.args)
        except ValidationError as exc:
            user += f"\n\nTham số sai schema: {exc.errors()[:3]}"
    raise ValueError("tham số không đúng schema")


# Steps that need nothing of each other run together, this many at most.
MAX_PARALLEL = 3


async def act(s: State) -> None:
    assert s.ctx is not None and s.plan is not None
    # A failure the planner was not asked about (replans spent) is on the step's record already.
    s.failed = None
    steps = p.ready(s.plan, MAX_PARALLEL)
    if not steps:
        return
    s.step_runs += len(steps)
    for step in steps:
        step.status = "running"
    results = await asyncio.gather(*(_run_step(s, step) for step in steps))
    for step, result in zip(steps, results, strict=True):
        step.observation = result.observation
        step.datasets = result.datasets
        step.status = "done" if result.ok else "failed"
        if result.clarify is not None:
            s.clarify = result.clarify
        if not result.ok and s.failed is None:
            s.failed = step


async def _run_step(s: State, step: p.PlanStep) -> ToolResult:
    """One step: its arguments, its tool, one local retry with the error (DataAgent retries SQL
    and Python in place); its own context, so steps run together keep their own step id."""
    assert s.ctx is not None
    tool = TOOLS[step.tool]
    ctx = dataclasses.replace(s.ctx, step_id=step.id)
    result: ToolResult | None = None
    async with turn_trace.stepping(
        ACTION_OF.get(tool.name, tool.name), title=tool.title, target=step.instruction[:160]
    ) as handle:
        error = ""
        for _ in range(2):
            try:
                args = await _arguments(s, step, error)
            except (ModelUnavailable, ValueError) as exc:
                result = ToolResult(False, f"không viết được tham số: {exc}")
                break
            try:
                result = await tool.run(ctx, args)
            except Exception as exc:  # a tool's failure is feedback for the planner
                logger.exception("question %s: tool %s failed", s.question_id, tool.name)
                result = ToolResult(False, f"công cụ lỗi: {type(exc).__name__}")
            step.args = args.model_dump(mode="json") if hasattr(args, "model_dump") else step.args
            if result.ok or tool.name not in ("analyze", "query_metric"):
                break
            error = result.observation
        assert result is not None
        handle.detail = {**result.detail, "args": _shown_args(step)}
        if not result.ok:
            handle.status, handle.summary = "failed", result.observation[:200]
    return result


def _shown_args(step: p.PlanStep) -> dict[str, Any]:
    """Arguments as the timeline shows them: the code of an analysis is shown; nothing holds SQL."""
    return {k: v for k, v in step.args.items() if k != "sql"}


def after_act(s: State) -> str:
    assert s.plan is not None
    if s.cancelled:
        return "persist"
    if s.clarify is not None:
        return "persist"
    if s.failed is not None:
        return "replan" if s.replans < MAX_REPLANS else "act"
    if s.step_runs >= MAX_STEP_RUNS:
        return "compose"
    if any(st.status == "pending" for st in s.plan.steps):
        return "act"
    return "review"


# ---------------------------------------------------------------- replan / review


async def replan(s: State) -> None:
    assert s.plan is not None and s.failed is not None
    failed, s.failed = s.failed, None
    s.replans += 1
    done = [st for st in s.plan.steps if st.status == "done"]
    system = prompts.PLANNER.replace("{tools}", _tool_cards()).replace("{catalog}", _catalog(s))
    user = f"{_situation(s)}\n\n{_progress(s)}\n\n" + prompts.REPLAN.format(
        failed=failed.id, why=failed.observation[:500], next_id=len(s.plan.steps) + 1
    )
    async with turn_trace.stepping(
        "replan", title="Điều chỉnh kế hoạch", target=failed.observation[:160]
    ) as handle:
        try:
            new = await _ask_plan(s, system, user)
        except (ModelUnavailable, p.PlanInvalid):
            handle.status, handle.summary = "failed", "Giữ kế hoạch cũ."
            return
        kept = {st.id: st for st in done}
        steps = [*done, *[st for st in new.steps if st.id not in kept and st.status == "pending"]]
        for st in steps[len(done) :]:
            st.depends_on = [d for d in st.depends_on if d in {x.id for x in steps}]
        failed_view = failed.model_copy()
        s.plan = p.Plan(goal=new.goal or s.plan.goal, thought=new.thought, steps=[*steps])
        # The failed step stays on the record, after the ones that are done.
        s.plan.steps.insert(len(done), failed_view)
        handle.detail = {"steps": p.view(s.plan)}
    if new.mode == "ask":
        s.clarify = Clarify(new.clarification or "Bạn có thể nói rõ hơn?", new.options[:5])


# Tools that only look up or read one governed figure: a plan made of them alone is a simple
# question, which needs neither the sufficiency review nor the reviewer of the draft (RES-05):
# its numbers are checked by rule (verify.py, grounded.py) whatever the model writes.
SIMPLE_TOOLS = frozenset({"search_catalog", "describe_metric", "search_values", "query_metric"})


def simple(s: State) -> bool:
    if s.plan is None or s.plan.mode != "analyze" or s.ctx is None:
        return False
    queried = [st for st in s.plan.steps if st.tool == "query_metric"]
    return (
        all(st.tool in SIMPLE_TOOLS for st in s.plan.steps)
        and len(queried) <= 1
        and len(s.ctx.ws.datasets) <= 1
    )


async def review(s: State) -> None:
    assert s.plan is not None
    if s.reviews >= MAX_REVIEWS or not s.ctx or not s.ctx.ws.datasets or simple(s):
        s.reviews = MAX_REVIEWS
        return
    s.reviews += 1
    system = prompts.REVIEW.replace("{tools}", _tool_cards())
    try:
        reply = await s.model.json_chat(
            "planner", system, f"{_situation(s)}\n\n{_progress(s)}", ReviewReply, max_tokens=1200
        )
    except ModelUnavailable:
        return
    if reply.content.enough or not reply.content.steps:
        return
    known = {st.id for st in s.plan.steps}
    added = []
    for st in reply.content.steps[: p.MAX_STEPS - len(s.plan.steps)]:
        if st.tool not in TOOLS or st.id in known:
            continue
        st.status = "pending"
        st.depends_on = [d for d in st.depends_on if d in known]
        known.add(st.id)
        added.append(st)
    if added:
        s.plan.steps.extend(added)
        await turn_trace.record_step(
            "review",
            title="Rà soát",
            target=reply.content.reason[:160],
            detail={"added": [st.instruction for st in added]},
        )


def after_review(s: State) -> str:
    assert s.plan is not None
    return "act" if any(st.status == "pending" for st in s.plan.steps) else "compose"


# ---------------------------------------------------------------- compose


def _unnamed_quantity(s: State) -> list[str]:
    """The question's quantity words no certified metric the turn read is named by (invariant 3,
    as resolver.grounding decides for one reading); empty when they all are, when the turn wrote
    its own SQL for the question, or when it read no certified metric."""
    assert s.ctx is not None and s.plan is not None
    if s.plan.mode != "analyze":
        return []
    datasets = list(s.ctx.ws.datasets.values())
    if any(d.tool == "explore_sql" for d in datasets):
        return []
    readings = [d.reading for d in datasets if d.reading]
    if not readings:
        return []
    vocab = s.ctx.layer.vocabulary
    by_code = {m.code: m for m in vocab.metrics}
    named: set[str] = set()
    for r in readings:
        if r["metric"] in by_code:
            named |= metric_words(by_code[r["metric"]], vocab)
    words = quantity_words(
        s.ctx.question,
        vocab,
        today=s.ctx.today,
        group_by=[g for r in readings for g in r["group_by"]],
        filters={k: v for r in readings for k, v in r["filters"].items()},
    )
    return unnamed(words, named)


def _named_members(s: State) -> list[tuple[str, str, str]]:
    """The dimension members the question names, by label or approved synonym, as (dimension,
    code, label): whole words, at least three letters, numbers left to the period reader."""
    assert s.ctx is not None
    text = f" {norm(s.ctx.question)} "
    vocab = s.ctx.layer.vocabulary
    found: dict[tuple[str, str], str] = {}
    for v in vocab.values:
        label = norm(v.label)
        if len(label) >= 3 and not label.replace(" ", "").isdigit() and f" {label} " in text:
            found[(v.dimension_code, v.value_code)] = v.label
    for phrase, target in vocab.synonyms.items():
        if target[0] == "value" and len(phrase) >= 3 and f" {phrase} " in text:
            found.setdefault((target[1], target[2]), phrase)
    return [(dim, code, label) for (dim, code), label in found.items()]


def _dropped_members(s: State) -> list[str]:
    """Members the question names that no dataset took into account: not in a certified reading's
    filters, not a row of any dataset, not in the SQL the model wrote."""
    assert s.ctx is not None and s.plan is not None
    if s.plan.mode != "analyze":
        return []
    datasets = list(s.ctx.ws.datasets.values())
    dropped = []
    for dim, code, label in _named_members(s):
        wanted = {norm(code), norm(label)}
        if any(
            code in (d.reading or {}).get("filters", {}).get(dim, [])
            or any(norm(str(v)) in wanted for row in d.rows for v in row if v is not None)
            or (d.sql is not None and any(w in norm(d.sql) for w in wanted))
            for d in datasets
        ):
            continue
        dropped.append(label)
    return dropped


def _no_figure(ws: Workspace) -> bool:
    """True when datasets came back but none holds a measure other than zero or empty. The
    measure is each dataset's last numeric column, as _figures reads it (a year column is a
    number too, and is no figure)."""
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


def _facts_text(known: dict[str, verify.Fact]) -> str:
    return "\n".join(f"{{{{{k}}}}} = {f.shown} ({f.label})" for k, f in known.items())


async def compose(s: State) -> None:
    assert s.ctx is not None and s.plan is not None
    ws = s.ctx.ws
    if (
        s.plan.mode == "analyze"
        and not ws.citations
        and not any(d.rows for d in ws.datasets.values())
    ):
        # Nothing was fetched: rather than write that it cannot answer, hand the question to the
        # governed path (rules, exploratory SQL, documents), which may still find the figure.
        await turn_trace.record_step(
            "handover",
            target="Chưa lấy được số liệu; chuyển sang cách trả lời chuẩn.",
            detail={"steps": p.view(s.plan)},
        )
        s.handed_back = True
        return
    dropped = _dropped_members(s)
    if dropped:
        # The question names a member ("nhóm hàng Bikes") that no dataset was filtered or cut on:
        # an answer from those rows would be for everything, not for what was asked.
        await turn_trace.record_step(
            "handover",
            target=f"Chưa lọc theo {', '.join(dropped)}; chuyển sang cách trả lời chuẩn.",
            detail={"dropped": dropped},
        )
        s.handed_back = True
        return
    if s.plan.mode == "analyze" and _no_figure(ws):
        # Every figure fetched is empty or zero: "Doanh thu 2009 là 0" would state a figure the
        # data does not have. The governed path looks for the periods and members that have data.
        await turn_trace.record_step(
            "handover",
            target="Không có số liệu cho kỳ hoặc bộ lọc được hỏi; chuyển sang cách trả lời chuẩn.",
            detail={"datasets": list(ws.datasets)},
        )
        s.handed_back = True
        return
    try:
        s.signals = await signals.read(s.ctx)
    except Exception:
        logger.exception("question %s: data signals not read", s.question_id)
    if s.signals.out_of_time:
        # BR-178: no figure of a table out of time is stated; the governed path masks them.
        await turn_trace.record_step(
            "handover",
            target=f"Dữ liệu {', '.join(s.signals.out_of_time)} quá hạn cập nhật; chuyển sang cách"
            " trả lời chuẩn.",
            detail={"out_of_time": s.signals.out_of_time},
        )
        s.handed_back = True
        return
    unnamed = _unnamed_quantity(s)
    if unnamed:
        # The metrics read do not measure what the question asks ("khách hàng" read as units
        # sold): the governed path asks back or explores SQL, as it does for a partial reading.
        await turn_trace.record_step(
            "handover",
            target=f"Chỉ số đã dùng không đo '{' '.join(unnamed)}'; chuyển sang cách trả lời chuẩn.",
            detail={"unnamed": unnamed},
        )
        s.handed_back = True
        return
    known = verify.facts(ws)
    citations = "\n".join(
        f"[{c['index']}] {c['source']}: {c['snippet'][:300]}" for c in ws.citations
    )
    user = (
        f"{_situation(s)}\n\n{_progress(s)}\n\n<facts>\n{_facts_text(known) or '(không có)'}\n</facts>"
        + (f"\n\n<citations>\n{citations}\n</citations>" if citations else "")
    )
    allowed = verify.allowed_numbers(s.ctx.question, ws, [c["source"] for c in ws.citations])
    async with turn_trace.stepping("write_answer", title="Soạn câu trả lời", target="") as handle:
        drafts: list[b.BlockDraft] = []
        errors: list[str] = []
        for _ in range(2):
            try:
                reply = await s.model.json_chat(
                    "responder",
                    prompts.REPORTER,
                    user + (f"\n\nLỗi block lần trước: {errors}" if errors else ""),
                    b.Composition,
                    max_tokens=2500,
                )
            except ModelUnavailable:
                break
            s.headline = reply.content.headline
            drafts, errors = reply.content.blocks, []
            for d in drafts:
                try:
                    b.materialize(d, ws, "probe")
                except b.BlockError as exc:
                    errors.append(f"{d.type}: {exc}")
            if not errors:
                break
        if drafts and not errors and not simple(s):
            drafts = await _reviewed(s, user, drafts)
        made = 0
        for d in drafts:
            block = _checked(s, d, known, allowed)
            if block is None:
                continue
            made += 1
            await publish(
                str(s.question_id), "block", {"question_id": str(s.question_id), "block": block}
            )
        if made == 0:
            # No model, or nothing it wrote held: the datasets speak for themselves.
            for ds in list(ws.datasets.values())[-3:]:
                block = b.materialize(
                    b.BlockDraft(type="table", dataset=ds.id), ws, f"b{len(s.composition) + 1}"
                )
                s.composition.append(block)
                await publish(
                    str(s.question_id), "block", {"question_id": str(s.question_id), "block": block}
                )
            if not ws.datasets:
                s.refused = "Chưa tìm được dữ liệu để trả lời câu hỏi này."
        handle.detail = {
            "blocks": [x["type"] for x in s.composition],
            "dropped_sentences": s.dropped_sentences,
        }
    _append_caveats(s)


class AnswerReview(BaseModel):
    ok: bool = True
    problems: list[str] = Field(default_factory=list)


async def _reviewed(s: State, user: str, drafts: list[b.BlockDraft]) -> list[b.BlockDraft]:
    """The draft read once by a reviewer before it is sent, and rewritten once when the reviewer
    names problems (gpt-researcher's reviewer and revisor, with its ceiling: after one revision
    the draft goes out, so a picky reviewer cannot loop)."""
    assert s.ctx is not None
    draft = json.dumps([d.model_dump(exclude_defaults=True) for d in drafts], ensure_ascii=False)
    try:
        review = await s.model.json_chat(
            "planner",
            prompts.REVIEW_ANSWER,
            f"{user}\n\n<draft>\n{draft[:6000]}\n</draft>",
            AnswerReview,
            max_tokens=400,
        )
    except ModelUnavailable:
        return drafts
    problems = [p_ for p_ in review.content.problems if p_.strip()][:5]
    if review.content.ok or not problems:
        return drafts
    await turn_trace.record_step(
        "review_answer", target=problems[0][:160], detail={"problems": problems}
    )
    try:
        reply = await s.model.json_chat(
            "responder",
            prompts.REPORTER,
            f"{user}\n\nBản nháp trước:\n{draft[:6000]}\n\nNgười rà soát chỉ ra, hãy sửa đúng "
            "những điểm này:\n- " + "\n- ".join(problems),
            b.Composition,
            max_tokens=2500,
        )
    except ModelUnavailable:
        return drafts
    revised = reply.content.blocks
    try:
        for d in revised:
            b.materialize(d, s.ctx.ws, "probe")
    except b.BlockError:
        return drafts
    s.headline = reply.content.headline or s.headline
    return revised or drafts


def _checked(
    s: State, d: b.BlockDraft, known: dict[str, verify.Fact], allowed: set[str]
) -> dict[str, Any] | None:
    assert s.ctx is not None
    cited = {int(c["index"]) for c in s.ctx.ws.citations}
    if d.type in ("markdown", "callout") and d.content:
        checked = verify.fill(verify.repair_citations(d.content, cited), known, allowed)
        s.numbers += checked.numbers
        s.dropped_sentences += checked.dropped
        if not checked.text.strip():
            return None
        d = d.model_copy(update={"content": checked.text})
    if d.type == "insights":
        kept = []
        for item in d.insights:
            checked = verify.fill(verify.repair_citations(item.text, cited), known, allowed)
            s.numbers += checked.numbers
            s.dropped_sentences += checked.dropped
            if checked.text.strip():
                kept.append(item.model_copy(update={"text": checked.text}))
        if not kept:
            return None
        d = d.model_copy(update={"insights": kept})
    try:
        block = b.materialize(d, s.ctx.ws, f"b{len(s.composition) + 1}")
    except b.BlockError:
        return None
    s.composition.append(block)
    return block


FETCHING = frozenset({"query_metric", "explore_sql", "search_documents"})


def _append_caveats(s: State) -> None:
    assert s.ctx is not None
    ws = s.ctx.ws
    items = []
    used = {blk.get("dataset") for blk in s.composition} | {
        it.get("dataset")
        for blk in s.composition
        for it in blk.get("items", [])
        if isinstance(it, dict)
    }
    for ds in ws.datasets.values():
        if ds.id in used and ds.consensus is not None:
            # As the governed path's exploratory label says it (sqlgen/answer.label_of).
            agreeing, written = ds.consensus
            items.append(
                f"{ds.title}: {TRUST_LABEL[ds.trust]}; {agreeing}/{written} cách viết cho cùng"
                " kết quả."
                + (
                    ""
                    if ds.agreement_clear
                    else " Nên kiểm lại trước khi dùng: các cách viết" " chưa đồng thuận rõ."
                )
            )
        elif ds.id in used and ds.trust != "certified":
            items.append(f"{ds.title}: {TRUST_LABEL[ds.trust]}.")
        if ds.id in used and ds.truncated:
            items.append(f"{ds.title}: kết quả bị cắt bớt, chỉ hiển thị một phần.")
    items += s.signals.caveats
    if s.dropped_sentences:
        items.append(f"Đã bỏ {s.dropped_sentences} câu có số không truy được nguồn.")
    # A failed step is said only when it was to fetch data and nothing fetched with that tool
    # afterwards: a computation that failed, or a query a later one replaced, is on the timeline
    # and casts no doubt on figures that were all traced.
    steps = s.plan.steps if s.plan else []
    fetched = {st.tool for st in steps if st.status == "done"}
    failed = [
        st
        for st in steps
        if st.status == "failed" and st.tool in FETCHING and st.tool not in fetched
    ]
    if failed:
        items.append(
            f"{len(failed)} bước không thành công: "
            + "; ".join(st.instruction for st in failed)[:300]
        )
    if items:
        s.composition.append(
            {
                "type": "caveats",
                "id": f"b{len(s.composition) + 1}",
                "composed": True,
                "items": list(dict.fromkeys(items)),
            }
        )


# ---------------------------------------------------------------- persist


# Figures kept from a table or chart when the answer has no KPI: the receipt and the eval read
# them; the rest stays in the query run.
MAX_SHOWN_FIGURES = 50


def _figure(
    key: str,
    label: str,
    value: float | None,
    unit: str | None,
    trust_label: str | None,
    runs: list[str],
    dimensions: dict[str, Any],
) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "value": None if value is None else format(value, "f"),
        "unit": unit,
        "decimals": verify._decimals(value) if value is not None else 0,
        "dimensions": dimensions,
        "trust": trust_label,
        "display_state": "shown",
        "query_run_id": runs[0] if runs else None,
        "source": {"query_run_id": runs[0] if runs else None},
    }


def _figures(s: State) -> list[dict[str, Any]]:
    """The answer's figures, each traced to its query runs (the receipt, the trust label and the
    eval read them): its KPIs; without one, the measure of the first table or chart it shows, row
    by row in the order shown, with the row's other cells as the figure's dimensions."""
    out = []
    for blk in s.composition:
        if blk["type"] != "kpi":
            continue
        for i, it in enumerate(blk["items"]):
            out.append(
                _figure(
                    f"{blk['id']}-{i}",
                    it["label"],
                    it.get("value"),
                    it.get("unit"),
                    it.get("trust_label"),
                    it.get("query_run_ids") or [],
                    {},
                )
            )
    if out:
        return out
    for blk in s.composition:
        if blk["type"] == "chart":
            spec = blk["spec"]
            rows, measure = spec["data"], spec["y_keys"][0]
        elif blk["type"] == "table":
            rows = blk["rows"]
            numeric = [
                c
                for c in blk["columns"]
                if rows and all(number(r.get(c)) is not None for r in rows if r.get(c) is not None)
            ]
            if not numeric:
                continue
            # The aggregate is written last (a year or a code may be a number too).
            measure = numeric[-1]
        else:
            continue
        for i, row in enumerate(rows[:MAX_SHOWN_FIGURES]):
            out.append(
                _figure(
                    f"{blk['id']}-{i}",
                    measure,
                    number(row.get(measure)),
                    blk.get("unit"),
                    blk.get("trust_label"),
                    blk.get("query_run_ids") or [],
                    {k: v for k, v in row.items() if k != measure},
                )
            )
        return out
    return out


def _consensus(s: State) -> tuple[int, int] | None:
    """The agreement of the model-written SQL the answer shows, for its confidence meter (R1-14);
    the weakest when it shows several."""
    assert s.ctx is not None
    used = {blk.get("dataset") for blk in s.composition} | {
        it.get("dataset")
        for blk in s.composition
        for it in blk.get("items", [])
        if isinstance(it, dict)
    }
    votes = [
        d.consensus for d in s.ctx.ws.datasets.values() if d.id in used and d.consensus is not None
    ]
    return min(votes, key=lambda v: v[0] / v[1] if v[1] else 0) if votes else None


def _text(s: State) -> str:
    parts = [blk["content"] for blk in s.composition if blk["type"] == "markdown"]
    return "\n\n".join(parts) or s.headline or (s.refused or "")


async def persist(s: State) -> None:
    assert s.ctx is not None
    ctx = s.ctx
    plan_steps = {
        "engine": "agentic",
        "path": "agentic",
        "goal": s.plan.goal if s.plan else "",
        "thought": s.plan.thought if s.plan else "",
        "steps": p.view(s.plan) if s.plan else [],
        "datasets": {
            ds.id: {
                "title": ds.title,
                "tool": ds.tool,
                "trust": ds.trust,
                "query_run_ids": ds.query_run_ids,
                "metric": (ds.reading or {}).get("metric"),
            }
            for ds in ctx.ws.datasets.values()
        },
    }
    refusal: dict[str, str] | None = None
    if s.cancelled:
        turn_trace.cancelled()
        await publish(str(s.question_id), "cancelled", {"question_id": str(s.question_id)})
        kind, status, text = "refusal", "failed", "Đã dừng lượt phân tích theo yêu cầu của bạn."
        refusal = {"code": "CANCELLED", "next_action": "retry"}
        s.composition = []
    elif s.clarify is not None:
        kind, status = "clarification", "clarifying"
        text = s.clarify.question
        s.composition = [
            {"type": "markdown", "id": "b1", "composed": True, "content": s.clarify.question},
            *(
                [{"type": "follow_ups", "id": "b2", "composed": True, "items": s.clarify.options}]
                if s.clarify.options
                else []
            ),
        ]
        await turn_trace.record_step(
            "ask_user", target=text[:160], detail={"question": text, "options": s.clarify.options}
        )
    elif s.refused is not None and not s.composition:
        kind, status, text = "refusal", "refused", s.refused
        refusal = {"code": "NO_DATA_FOR_PERIOD", "next_action": "rephrase"}
    else:
        kind = "data" if ctx.ws.datasets else ("document" if ctx.ws.citations else "data")
        status, text = "answered", _text(s)
    figures = _figures(s)
    follow_ups: list[str] = next(
        (blk["items"] for blk in s.composition if blk["type"] == "follow_ups"), []
    )
    chart = next((blk["spec"] for blk in s.composition if blk["type"] == "chart"), None)
    answer_id = uuid7()
    async with scoped_session(ROLE, ctx.org_id, ctx.user_id) as session:
        question = await session.get(qa.Question, s.question_id, with_for_update=True)
        if question is None or question.status != "received":
            return
        version = (
            await session.scalar(
                select(func.max(qa.Plan.version)).where(qa.Plan.question_id == s.question_id)
            )
            or 0
        ) + 1
        plan_id = uuid7()
        session.add(
            qa.Plan(
                id=plan_id,
                org_id=ctx.org_id,
                question_id=s.question_id,
                version=version,
                chips=[],
                steps=plan_steps,
            )
        )
        await session.flush()
        number_check: dict[str, Any] = {
            "numbers_total": s.numbers + len(figures),
            "traced": s.numbers + len(figures),
            "untraceable_draft": s.dropped_sentences,
            "regenerated": 0,
            "sentences_dropped": s.dropped_sentences,
            "untraceable_final": 0,
        }
        if refusal is not None:
            number_check = {"refusal": refusal}
        session.add(
            qa.Answer(
                id=answer_id,
                org_id=ctx.org_id,
                question_id=s.question_id,
                plan_id=plan_id,
                kind=kind,
                text=text,
                figures=figures,
                chart_spec=chart,
                citations=ctx.ws.citations,
                follow_ups=follow_ups,
                blocks=s.composition,
                number_check=verify_answer(
                    kind,
                    number_check,
                    figures=figures,
                    citations=ctx.ws.citations,
                    consensus=_consensus(s),
                )
                if kind not in ("refusal",)
                else number_check,
                agent_name=AGENT_NAME,
                latency_ms=int(
                    (datetime.datetime.now(datetime.UTC) - s.started).total_seconds() * 1000
                ),
                product_scope="not_applicable",
            )
        )
        question.status = status
        if status == "answered":
            question.answered_at = func.now()
    s.answered = status == "answered"
    payload = {"question_id": str(s.question_id), "answer_id": str(answer_id), "kind": kind}
    event = {"clarification": "clarification", "refusal": "answer"}.get(kind, "answer")
    await publish(str(s.question_id), event, payload)
    await publish(str(s.question_id), "done", {**payload, "status": status})


# ---------------------------------------------------------------- the graph


# How often a running node looks for the asker's stop request (a Redis GET each time).
CANCEL_POLL_SECONDS = 1.0


def _cancellable(node: Node[State]) -> Node[State]:
    """`node`, stopped as soon as the asker cancels: checked before it starts and every
    CANCEL_POLL_SECONDS while it runs, so a long query, analysis or model call is cut off rather
    than finished and thrown away. Steps it left running are recorded as skipped."""

    async def run(s: State) -> None:
        if await is_cancel_requested(str(s.question_id)):
            s.cancelled = True
            return
        task = asyncio.ensure_future(node(s))
        while True:
            done, _ = await asyncio.wait({task}, timeout=CANCEL_POLL_SECONDS)
            if done:
                return task.result()
            if await is_cancel_requested(str(s.question_id)):
                break
        task.cancel()
        await asyncio.wait({task})
        if not task.cancelled() and task.exception() is not None:
            logger.warning("question %s: node failed while cancelled", s.question_id)
        s.cancelled = True
        for step in s.plan.steps if s.plan else []:
            if step.status == "running":
                step.status = "skipped"

    return run


def _or_stop(router: Router[State]) -> Router[State]:
    """A cancelled turn goes straight to persist, whatever the node would route to."""
    return lambda s: "persist" if s.cancelled else router(s)


def _graph() -> StateGraph[State]:
    g: StateGraph[State] = StateGraph()
    g.node("prepare", prepare).route("prepare", lambda s: END if s.handed_back else "plan")
    g.node("plan", _cancellable(plan)).route(
        "plan",
        lambda s: "persist"
        if s.cancelled
        else END
        if s.handed_back
        else "persist"
        if s.clarify is not None
        else "compose"
        if s.plan is not None and s.plan.mode == "answer"
        else "act",
    )
    g.node("act", _cancellable(act)).route("act", after_act)
    g.node("replan", _cancellable(replan)).route(
        "replan", _or_stop(lambda s: "persist" if s.clarify is not None else "act")
    )
    g.node("review", _cancellable(review)).route("review", _or_stop(after_review))
    g.node("compose", _cancellable(compose)).route(
        "compose", _or_stop(lambda s: END if s.handed_back else "persist")
    )
    g.node("persist", persist).edge("persist", END)
    return g


GRAPH = _graph()


async def run(event: Event, question_id: uuid.UUID, model: Gateway | None) -> bool:
    """True when the agent answered (or asked back, or was cancelled); False to hand the question
    to the governed path untouched."""
    if model is None or not engine_enabled():
        return False
    state = State(event=event, question_id=question_id, model=model)
    await publish(str(question_id), "planning", {"question_id": str(question_id)})
    try:
        await GRAPH.run(state, max_steps=64)
    except Exception:
        if state.plan is None:
            # Nothing was shown but a planning step: the governed path still answers.
            logger.exception("question %s: agentic turn failed before planning", question_id)
            return False
        raise
    if state.answered:
        await _remember(state)
    return not state.handed_back


async def _remember(s: State) -> None:
    """After the answer is out: what the person said that should outlive the conversation, kept
    for their next ones (memory.py). Never in the way of the turn."""
    assert s.ctx is not None
    ctx = s.ctx
    if not memory.worth_remembering(ctx.question):
        return
    fetched = {
        n
        for ds in ctx.ws.datasets.values()
        for row in ds.rows
        for v in row
        if number(v) is not None
        for n in re.findall(r"\d+(?:[.,]\d+)*", str(v))
    } | {n for f in verify.facts(ctx.ws).values() for n in re.findall(r"\d+(?:[.,]\d+)*", f.shown)}
    try:
        ops = await memory.extract(
            s.model,
            question=ctx.question,
            answer=_text(s),
            history=s.history,
            known=s.memories,
            fetched_numbers=fetched,
        )
        kept = await memory.store(ctx.org_id, ctx.space_id, ctx.user_id, s.question_id, ops)
        if kept:
            await turn_trace.record_step(
                "remember",
                target=f"Ghi nhớ {kept} điều cho các hội thoại sau",
                detail={"kinds": [op.kind for op in ops]},
            )
    except Exception:
        logger.exception("question %s: memory not stored", s.question_id)
