"""The trace of one Ask turn: an agent_run with its steps and model calls (AC-13-02-10).

A question is a run of the Data Space's built-in "Agent Hỏi đáp", so what the agent did on a turn,
how long each part took and what the model calls cost is read where every other agent's runs are
read (agent.agent_run, agent.agent_step). The run names its question in trigger_ref
("question:<id>"): qa.plan belongs to a question or to a run, never both, so the question's plan
does not point back at it.

The turn is collected in memory and written once when it ends: the stream already shows the live
progress, and one write keeps the question path from waiting on the trace. A failure to write the
trace is logged and never fails the answer.

Model calls are recorded without their content (no prompt, no answer, no key): the gateway reports
the role, the model that answered, the tokens in and out, the latency and whether it answered. The
cost is the tokens priced by LLM_PRICES (JSON, per model or role alias: {"responder":
{"input_per_1k": 0.0, "output_per_1k": 0.0}}); a model with no price costs 0, which is what the
on-prem models cost per call.
"""

from __future__ import annotations

import contextvars
import datetime
import decimal
import json
import logging
import os
import time
import uuid
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from functools import cache
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from libs.dataagent_db import scoped_session, uuid7
from libs.dataagent_db.models import agent, org, qa
from services.worker_agent.progress import publish

log = logging.getLogger(__name__)

AGENT_ROLE = "svc_agent"
QA_ROLE = "svc_qa"
AGENT_CODE = "QA"
AGENT_NAME = "Agent Hỏi đáp"
CONFIG_VERSION = 1

# The step contract (docs/superpowers/specs/2026-11-02-ask-chat-redesign-design.md): each action,
# its agent_step kind and the title the timeline shows by default.
ACTIONS: dict[str, tuple[str, str]] = {
    "understand": ("plan", "Hiểu câu hỏi"),
    "choose_metric": ("plan", "Chọn chỉ số"),
    "ask_user": ("checkpoint", "Hỏi lại bạn"),
    "query": ("query", "Truy vấn"),
    "write_sql": ("tool_call", "Viết SQL thăm dò"),
    "search_documents": ("tool_call", "Tìm trong tài liệu"),
    "use_verified": ("query", "Dùng truy vấn đã duyệt"),
    "use_tool": ("tool_call", "Dùng công cụ"),
    "validate": ("plan", "Kiểm tra kết quả"),
    "visualize": ("plan", "Chọn cách hiển thị"),
    "analyze": ("skill", "Phân tích"),
    "write_answer": ("message", "Viết câu trả lời"),
    "model_call": ("llm_call", "Gọi mô hình"),
}
# agent_step.status of each step status the timeline shows.
STORED_STATUS = {"done": "ok", "failed": "error", "skipped": "skipped"}
# A turn starts from these; a plan edit puts an answered question back to planning.
OPEN = frozenset({"received", "planning"})
QUESTION_OUTCOME = {
    "answered": ("succeeded", "completed"),
    "clarifying": ("succeeded", "completed"),
    "refused": ("succeeded", "completed"),
    "failed": ("failed", "error"),
}


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


@dataclass
class Step:
    kind: str
    name: str
    started: datetime.datetime
    status: str = "ok"
    duration_ms: int | None = None
    output: dict[str, Any] | None = None
    summary: str | None = None
    cost: decimal.Decimal = decimal.Decimal(0)
    seq: int = 0
    _clock: float = field(default_factory=time.monotonic)


@dataclass
class StepHandle:
    """What a step reports when it ends: its detail and how it ended."""

    step_id: str
    detail: dict[str, Any] = field(default_factory=dict)
    status: str = "done"
    # Shown under a failed or skipped step: the user-facing reason, never an exception's text.
    summary: str | None = None


@dataclass
class Trace:
    org_id: uuid.UUID
    question_id: uuid.UUID
    user_id: uuid.UUID | None
    steps: list[Step] = field(default_factory=list)
    # The step under way, which a model call made now belongs to.
    open_step: str | None = None
    seq: int = 0
    # Steps begun and not yet ended, by action (begin/end_open).
    opened: dict[str, tuple[Step, StepHandle]] = field(default_factory=dict)
    tokens: int = 0
    cost: decimal.Decimal = decimal.Decimal(0)
    started: datetime.datetime = field(default_factory=_now)
    cancelled: bool = False


_current: contextvars.ContextVar[Trace | None] = contextvars.ContextVar("turn_trace", default=None)


@cache
def _prices() -> dict[str, dict[str, float]]:
    try:
        prices = json.loads(os.environ.get("LLM_PRICES") or "{}")
    except ValueError:
        log.warning("LLM_PRICES is not JSON; model calls are recorded at cost 0")
        return {}
    return prices if isinstance(prices, dict) else {}


def price(model: str, role: str, input_tokens: int, output_tokens: int) -> decimal.Decimal:
    rate = _prices().get(model) or _prices().get(role) or {}
    cost = (
        decimal.Decimal(str(rate.get("input_per_1k", 0))) * input_tokens
        + decimal.Decimal(str(rate.get("output_per_1k", 0))) * output_tokens
    ) / 1000
    return cost.quantize(decimal.Decimal("0.0001"))


def _view(
    step_id: str, title: str, target: str, chips: Sequence[Mapping[str, str]], parent: str | None
) -> dict[str, Any]:
    return {
        "step_id": step_id,
        "title": title,
        "target": target,
        "chips": [dict(chip) for chip in chips],
        "parent": parent,
    }


def _event(trace: Trace, step: Step, action: str, status: str, detail: Any) -> dict[str, Any]:
    view = (step.output or {})["_view"]
    return {
        "kind": "step",
        "step_id": view["step_id"],
        "seq": step.seq,
        "action": action,
        "title": view["title"],
        "target": view["target"],
        "chips": view["chips"],
        "status": status,
        "started_at": step.started.isoformat(),
        "duration_ms": step.duration_ms,
        "detail": detail,
        # Why a failed or skipped step ended so: shown under it while the turn is live.
        "summary": step.summary,
        "parent": view["parent"],
    }


def _open(
    trace: Trace, action: str, title: str | None, target: str, chips: Sequence[Mapping[str, str]]
) -> Step:
    kind, default_title = ACTIONS.get(action, ("plan", action))
    trace.seq += 1
    step_id = f"s{trace.seq}"
    opened = Step(kind=kind, name=action, started=_now())
    opened.seq = trace.seq
    # A step is never another's child; only a model call nests, under the step it served.
    opened.output = {
        "detail": None,
        "_view": _view(step_id, title or default_title, target, chips, None),
    }
    trace.steps.append(opened)
    return opened


def _close(step: Step, status: str, detail: Mapping[str, Any] | None, summary: str | None) -> None:
    step.duration_ms = int((time.monotonic() - step._clock) * 1000)
    step.status = STORED_STATUS.get(status, "ok")
    step.summary = summary
    assert step.output is not None
    step.output["detail"] = dict(detail) if detail else None


async def begin(
    action: str,
    *,
    title: str | None = None,
    target: str = "",
    chips: Sequence[Mapping[str, str]] = (),
) -> StepHandle:
    """Open a step that another place ends (end_open); published as running now."""
    trace = _current.get()
    if trace is None:
        return StepHandle(step_id="")
    opened = _open(trace, action, title, target, chips)
    handle = StepHandle(step_id=opened.output["_view"]["step_id"])  # type: ignore[index]
    trace.opened[action] = (opened, handle)
    trace.open_step = handle.step_id
    await publish(
        str(trace.question_id), "activity", _event(trace, opened, action, "running", None)
    )
    return handle


async def end_open(
    action: str,
    *,
    detail: Mapping[str, Any] | None = None,
    status: str = "done",
    summary: str | None = None,
) -> None:
    """End the open step of `action`, if there is one; a second end changes nothing."""
    trace = _current.get()
    if trace is None or action not in trace.opened:
        return
    step, handle = trace.opened.pop(action)
    if detail is not None:
        handle.detail = dict(detail)
    handle.status, handle.summary = status, summary if summary is not None else handle.summary
    await _end(trace, step, action, handle)


async def _end(trace: Trace, step: Step, action: str, handle: StepHandle) -> None:
    if trace.open_step == handle.step_id:
        trace.open_step = None
    _close(step, handle.status, handle.detail, handle.summary)
    await publish(
        str(trace.question_id),
        "activity",
        _event(trace, step, action, handle.status, step.output["detail"]),  # type: ignore[index]
    )


@asynccontextmanager
async def stepping(
    action: str,
    *,
    title: str | None = None,
    target: str = "",
    chips: Sequence[Mapping[str, str]] = (),
) -> AsyncIterator[StepHandle]:
    """One step of the turn: published as running now, and as it ended when the block exits (an
    exception ends it failed and propagates). Outside a turn it records and publishes nothing."""
    trace = _current.get()
    if trace is None:
        yield StepHandle(step_id="")
        return
    previous = trace.open_step
    handle = await begin(action, title=title, target=target, chips=chips)
    step, _ = trace.opened.pop(action)
    try:
        yield handle
    except BaseException:
        handle.status = "failed"
        raise
    finally:
        await _end(trace, step, action, handle)
        trace.open_step = previous


async def record_step(
    action: str,
    *,
    title: str | None = None,
    target: str = "",
    chips: Sequence[Mapping[str, str]] = (),
    detail: Mapping[str, Any] | None = None,
    status: str = "done",
    summary: str | None = None,
) -> None:
    """A step that happened at once: one finished event."""
    trace = _current.get()
    if trace is None:
        return
    step = _open(trace, action, title, target, chips)
    _close(step, status, detail, summary)
    step.duration_ms = 0
    await publish(
        str(trace.question_id),
        "activity",
        _event(trace, step, action, status, step.output["detail"]),  # type: ignore[index]
    )


def model_call(
    *,
    role: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    latency_ms: int,
    error: str | None = None,
) -> None:
    """One call to the gateway, without its content; outside a turn it is not recorded."""
    trace = _current.get()
    if trace is None:
        return
    cost = price(model, role, input_tokens, output_tokens)
    trace.tokens += input_tokens + output_tokens
    trace.cost += cost
    trace.seq += 1
    trace.steps.append(
        Step(
            kind="llm_call",
            name="model_call",
            started=_now() - datetime.timedelta(milliseconds=latency_ms),
            status="error" if error else "ok",
            duration_ms=latency_ms,
            output={
                "detail": {
                    "model": model,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "latency_ms": latency_ms,
                },
                "_view": _view(f"s{trace.seq}", f"Gọi mô hình {role}", "", (), trace.open_step),
            },
            summary=error,
            cost=cost,
            seq=trace.seq,
        )
    )


def cancelled() -> None:
    trace = _current.get()
    if trace is not None:
        trace.cancelled = True


@asynccontextmanager
async def turn(
    org_id: uuid.UUID, question_id: uuid.UUID, user_id: uuid.UUID | None
) -> AsyncIterator[Trace]:
    """Collect the turn while the block runs, then write it as one run."""
    trace = Trace(org_id=org_id, question_id=question_id, user_id=user_id)
    async with scoped_session(QA_ROLE, org_id, user_id) as session:
        at_start = await session.scalar(
            select(qa.Question.status).where(qa.Question.id == question_id)
        )
    if at_start not in OPEN:
        # A redelivered event: an earlier turn answered the question, this one does nothing
        # (questions.py is idempotent) and leaves no run.
        yield trace
        return
    token = _current.set(trace)
    error: BaseException | None = None
    try:
        yield trace
    except BaseException as exc:
        error = exc
        raise
    finally:
        _current.reset(token)
        try:
            await _write(trace, error)
        except Exception:
            log.exception("worker-agent | the trace of question %s was not written", question_id)


async def _write(trace: Trace, error: BaseException | None) -> None:
    async with scoped_session(QA_ROLE, trace.org_id, trace.user_id) as session:
        row = (
            await session.execute(
                select(qa.Question.status, qa.Conversation.data_space_id, qa.Question.as_of)
                .join(qa.Conversation, qa.Conversation.id == qa.Question.conversation_id)
                .where(qa.Question.id == trace.question_id)
            )
        ).first()
        if row is None:
            return
        question_status, space_id, as_of = row
        steward = await session.scalar(
            select(org.DataSpace.steward_id).where(org.DataSpace.id == space_id)
        )
    if error is not None:
        status, stop_reason = "failed", "error"
    elif trace.cancelled:
        status, stop_reason = "cancelled", "user_cancelled"
    else:
        status, stop_reason = QUESTION_OUTCOME.get(question_status, ("failed", "error"))
    finished = _now()
    run_id = uuid7()
    async with scoped_session(AGENT_ROLE, trace.org_id, trace.user_id) as session:
        agent_id = await _qa_agent(session, trace.org_id, space_id, steward)
        if agent_id is None:
            return
        session.add(
            agent.AgentRun(
                id=run_id,
                org_id=trace.org_id,
                agent_id=agent_id,
                config_version=CONFIG_VERSION,
                data_space_id=space_id,
                trigger_kind="manual",
                trigger_ref=f"question:{trace.question_id}",
                initiated_by=trace.user_id,
                as_of=as_of,
                status=status,
                current_step=len(trace.steps) or None,
                cost=trace.cost.quantize(decimal.Decimal("0.01")),
                tokens=trace.tokens,
                started_at=trace.started,
                finished_at=finished,
                # The exception's type only: its text can carry SQL, values or a provider's body.
                error=type(error).__name__ if error else None,
                stop_reason=stop_reason,
            )
        )
        await session.flush()
        for seq, done in enumerate(trace.steps, start=1):
            if done.duration_ms is None:
                # Still open when the turn ended: the step the turn ended on, its outcome.
                done.duration_ms = int((finished - done.started).total_seconds() * 1000)
                done.status = "ok" if error is None else "error"
            session.add(
                agent.AgentStep(
                    id=uuid7(),
                    org_id=trace.org_id,
                    run_id=run_id,
                    seq=seq,
                    kind=done.kind,
                    name=done.name,
                    output=done.output,
                    summary=done.summary,
                    status=done.status,
                    cost=done.cost,
                    started_at=done.started,
                    duration_ms=done.duration_ms,
                )
            )


async def _qa_agent(
    session: Any, org_id: uuid.UUID, space_id: uuid.UUID, owner: uuid.UUID | None
) -> uuid.UUID | None:
    """The Data Space's built-in Ask agent, created on its first traced turn.

    It only holds the runs of Ask turns, which run as the asker: it is created retired, so it can
    never be configured, deployed or run (api agent routes refuse a retired agent) and never acts
    as the steward it names. It is the one the worker created (created_by NULL): an agent a person
    made with the same code is not taken for it, and the turn is then left untraced."""
    if owner is None:
        return None
    await session.execute(
        insert(agent.AgentDef)
        .values(
            id=uuid7(),
            org_id=org_id,
            data_space_id=space_id,
            code=AGENT_CODE,
            name=AGENT_NAME,
            kind="analysis",
            owner_user_id=owner,
            account_user_id=owner,
            current_config_version=CONFIG_VERSION,
            status="retired",
        )
        .on_conflict_do_nothing(index_elements=["data_space_id", "code"])
    )
    agent_id: uuid.UUID | None = await session.scalar(
        select(agent.AgentDef.id).where(
            agent.AgentDef.data_space_id == space_id,
            agent.AgentDef.code == AGENT_CODE,
            agent.AgentDef.kind == "analysis",
            agent.AgentDef.status == "retired",
            agent.AgentDef.created_by.is_(None),
        )
    )
    if agent_id is None:
        log.warning("worker-agent | Data Space %s has an agent %s of its own", space_id, AGENT_CODE)
        return None
    await session.execute(
        insert(agent.AgentConfig)
        .values(
            id=uuid7(),
            org_id=org_id,
            agent_id=agent_id,
            version=CONFIG_VERSION,
            goal="Trả lời câu hỏi của người dùng trong Data Space bằng chỉ số chuẩn, truy vấn tại chỗ và tài liệu được phép.",
        )
        .on_conflict_do_nothing(index_elements=["agent_id", "version"])
    )
    return agent_id
