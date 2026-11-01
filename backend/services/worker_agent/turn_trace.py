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
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from functools import cache
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from libs.dataagent_db import scoped_session, uuid7
from libs.dataagent_db.models import agent, org, qa

log = logging.getLogger(__name__)

AGENT_ROLE = "svc_agent"
QA_ROLE = "svc_qa"
AGENT_CODE = "QA"
AGENT_NAME = "Agent Hỏi đáp"
CONFIG_VERSION = 1

# The analyst steps of questions._analyst_step, as agent_step kinds.
STEP_KINDS = {"query": "query", "narrate": "message"}
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
    _clock: float = field(default_factory=time.monotonic)


@dataclass
class Trace:
    org_id: uuid.UUID
    question_id: uuid.UUID
    user_id: uuid.UUID | None
    steps: list[Step] = field(default_factory=list)
    open: dict[str, Step] = field(default_factory=dict)
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


def step(name: str, status: str, *, detail: str | None = None) -> None:
    """An analyst step of the turn: "running" opens it, any other status closes it."""
    trace = _current.get()
    if trace is None:
        return
    if status == "running":
        opened = Step(kind=STEP_KINDS.get(name, "plan"), name=name, started=_now())
        trace.open[name] = opened
        trace.steps.append(opened)
        return
    done = trace.open.pop(name, None)
    if done is None:
        # Closed without having been opened: a step the turn passed through at once.
        done = Step(kind=STEP_KINDS.get(name, "plan"), name=name, started=_now())
        trace.steps.append(done)
    done.duration_ms = int((time.monotonic() - done._clock) * 1000)
    done.status = "ok" if status == "done" else "error"
    done.summary = detail


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
    trace.steps.append(
        Step(
            kind="llm_call",
            name=role,
            started=_now() - datetime.timedelta(milliseconds=latency_ms),
            status="error" if error else "ok",
            duration_ms=latency_ms,
            output={
                "model": model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "latency_ms": latency_ms,
            },
            summary=error,
            cost=cost,
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
        agent_id = await _qa_agent(session, trace.org_id, space_id, steward or trace.user_id)
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
                error=f"{type(error).__name__}: {str(error)[:200]}" if error else None,
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
    """The Data Space's built-in Ask agent, created on its first traced turn."""
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
            status="running",
        )
        .on_conflict_do_nothing(index_elements=["data_space_id", "code"])
    )
    agent_id: uuid.UUID | None = await session.scalar(
        select(agent.AgentDef.id).where(
            agent.AgentDef.data_space_id == space_id, agent.AgentDef.code == AGENT_CODE
        )
    )
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
