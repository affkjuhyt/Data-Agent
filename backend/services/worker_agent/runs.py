"""Agent runs (UC-13-04, UC-13-06): the executor behind POST /v1/agents/{id}/test-runs, triggers
and onboarding's try-agent.

ADR-37 puts agent runs on Temporal (R3). Until it runs, this outbox executor stands in, under the
three guarantees tab 5 G1.1 asks of a non-Temporal worker: every step is written as it ends, so a
run picks up after the last recorded step; the run's state lives in PostgreSQL and is re-read at
each step boundary, so the api's pause and cancel (routes.py writes the status itself) are obeyed
without a message to this worker; and a redelivered event finds nothing left to do.

First slice: the goal is read like a question (rule pass, then the planner model), planned as the
semantic fast path's one-step Plan IR, and run in place. Deferred: checkpoints and waiting_human,
the +50% budget request and the monthly budget alerts, retries, no_progress, plan edits, the LLM
Planner's multi-step plans, and recovering runs orphaned by a crash. Runs execute inline, so a long
run holds this worker's poll loop for at most its BR-170 duration.
"""

from __future__ import annotations

import datetime
import decimal
import hashlib
import json
import logging
import time
import unicodedata
import uuid
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError

from libs.dataagent_db import scoped_session, uuid7
from libs.dataagent_db.models import agent, catalog, qa
from services.worker_agent.answer.narrate import figures_of, text_of
from services.worker_agent.events import Event, Handler
from services.worker_agent.query.executor import QueryOutcome, run_query
from services.worker_agent.query.guard import SqlRejected
from services.worker_agent.questions import (
    QUERYABLE,
    _internal_workspace_source,
    _plan_ir,
    dialect_for_source,
    gateway,
    secrets,
)
from services.worker_agent.semantic import compiler as c
from services.worker_agent.semantic.loader import load
from services.worker_agent.understand import llm_reader
from services.worker_agent.understand.resolver import Understanding, from_chips, understand
from services.worker_agent.understand.text import words_of

logger = logging.getLogger(__name__)

EVENT_TYPE = "run.status_changed"
ROLE = "svc_agent"
QA_ROLE = "svc_qa"
TERMINAL = frozenset({"succeeded", "failed", "cancelled"})
# Request noise a fingerprint leaves out (tab 5 G4.4): the same call twice is the same call.
_NOISE = frozenset({"request_id", "idempotency_key", "timestamp", "at"})
# The builder's draft grants the read tool as "query" (api agent routes, draft-from-text).
_ALIASES = {"query": "query.semantic"}
LOOP_REPEATS = 3
ROWS_KEPT = 50


@dataclass(frozen=True)
class Limits:
    max_steps: int
    max_tokens: int
    max_duration_s: int


# BR-170 background ceilings (api agent/rules.py LIMIT_CEILINGS); agent_config.limits only lowers.
CEILINGS = Limits(max_steps=60, max_tokens=400_000, max_duration_s=30 * 60)


def wants_execution(payload: Mapping[str, Any]) -> bool:
    """A run is ready to go when the api queued it (create, retry, resume before it started) or
    resumed it mid-way. This executor's own transitions and pause/cancel need nothing."""
    status, previous = payload.get("status"), payload.get("previous_status")
    if status == "queued":
        return previous in (None, "failed", "paused")
    return status == "running" and previous == "paused"


def effective_limits(limits: Mapping[str, Any]) -> Limits:
    def lowered(key: str, ceiling: int, scale: int = 1) -> int:
        value = limits.get(key)
        if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
            return ceiling
        return min(ceiling, int(value * scale))

    return Limits(
        lowered("max_steps", CEILINGS.max_steps),
        lowered("max_tokens", CEILINGS.max_tokens),
        lowered("max_duration_min", CEILINGS.max_duration_s, 60),
    )


def _normal(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _normal(v) for k, v in value.items() if str(k) not in _NOISE}
    if isinstance(value, list | tuple):
        return [_normal(v) for v in value]
    if isinstance(value, datetime.datetime | datetime.date):
        return value.isoformat()
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, uuid.UUID | decimal.Decimal):
        return str(value)
    return value


def fingerprint(tool: str, args: Mapping[str, Any]) -> str:
    """SHA-256 of the tool and its normalised arguments (tab 5 G4.4)."""
    body = json.dumps(_normal(args), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(f"{tool}\n{body}".encode()).hexdigest()


def is_loop(calls: Sequence[tuple[str | None, str | None]]) -> bool:
    """AC-13-02-09: the same call with the same result three times is a hard loop."""
    return any(n >= LOOP_REPEATS for n in Counter(calls).values())


def authority(
    tool: str, granted: Mapping[str, str], *, is_test: bool
) -> Literal["run", "simulate", "denied"]:
    """The agent may call only the tools its config grants; a test run does not write or send
    (UC-13-04 step 1), it records what it would have done."""
    levels = {_ALIASES.get(code, code): level for code, level in granted.items()}
    level = levels.get(tool)
    if level is None:
        return "denied"
    return "simulate" if is_test and level != "read" else "run"


def narrative(text: str, state: str, at: datetime.datetime) -> dict[str, str]:
    """One entry of agent_run.narrative as the run screens draw it ({at, text, state})."""
    return {"at": at.isoformat(), "text": text, "state": state}


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _plain(value: Any) -> Any:
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, datetime.datetime | datetime.date):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


class RunFailed(Exception):
    """The run cannot go on; the message is the sentence the owner reads."""

    def __init__(self, message: str, stop_reason: str = "error") -> None:
        super().__init__(message)
        self.stop_reason = stop_reason


@dataclass
class Planned:
    """The goal read and compiled, ready to run; nothing is written while it is made."""

    understanding: Understanding
    compiled: c.Compiled
    source: catalog.Source
    metric_name: str
    unit: str | None
    decimals: int
    dimension_names: dict[str, str]
    tokens: int = 0
    models: list[dict[str, Any]] = field(default_factory=list)

    @property
    def summary(self) -> str:
        u = self.understanding
        cuts = ", ".join(self.dimension_names.get(g, g) for g in u.group_by)
        return " ".join(
            p
            for p in (
                f"Tính {self.metric_name}",
                f"theo {cuts}" if cuts else "",
                u.period.label if u.period else "",
            )
            if p
        )


async def _plan_step(
    run: agent.AgentRun,
    goal: str,
    chips: list[dict[str, Any]] | None,
    principal: uuid.UUID,
) -> Planned:
    """The goal read against the Data Space's semantic layer, as Hỏi đáp reads a question. A run
    that resumes reads the chips it planned with, so it carries on with the same plan."""
    today = run.as_of or datetime.date.today()
    async with scoped_session(QA_ROLE, run.org_id, principal) as session:
        layer = await load(session, run.data_space_id)
        u = (
            from_chips(chips, layer.vocabulary)
            if chips is not None
            else understand(goal, layer.vocabulary, today=today)
        )
        tokens, models = 0, []
        model = gateway()
        if chips is None and u.decision == "unknown" and model is not None:
            read = await llm_reader.read(goal, layer.vocabulary, model, today=today)
            if read is not None:
                u, reply = read
                tokens += reply.tokens
                models.append(
                    {"task": "understand", "model_name": reply.model, "tokens": reply.tokens}
                )
        if u.decision == "clarify" and u.unplaced_cut is not None:
            raise RunFailed(
                f"Mục tiêu chia theo “{words_of(goal, u.unplaced_cut)}”, nhưng Data Space không có "
                "chiều nào tên như "
                "vậy. Hãy sửa mục tiêu theo tên chiều trong danh mục."
            )
        if u.decision == "clarify":
            names = ", ".join(m.name for m in u.options)
            raise RunFailed(
                f"Mục tiêu khớp với nhiều chỉ số ({names}); hãy sửa mục tiêu cho rõ một chỉ số."
            )
        if u.decision != "proceed" or u.metric is None:
            raise RunFailed(
                "Mục tiêu chưa gọi tên chỉ số nào của Data Space; hãy sửa mục tiêu theo tên chỉ số "
                "trong danh mục, hoặc nhờ người phụ trách thêm từ đồng nghĩa."
            )
        version = layer.versions[u.metric.metric_id]
        request = c.Request(
            version,
            group_by=u.group_by,
            filters=u.filters,
            period=c.Period(u.period.start, u.period.end) if u.period else None,
        )
        try:
            compiled = c.compile_metric(request, layer.layer, dialect="postgres")
            dataset = layer.datasets[compiled.dataset_ids[0]]
            source = (
                _internal_workspace_source(run.data_space_id, run.org_id)
                if dataset.schema_name == "workspace" and dataset.source_id is None
                else await session.get(catalog.Source, dataset.source_id)
            )
            if source is None or source.mode != "federated" or source.status not in QUERYABLE:
                raise RunFailed(
                    "Bảng của chỉ số này nằm trên một nguồn chưa được phép truy vấn tại chỗ."
                )
            compiled = c.compile_metric(
                request, layer.layer, dialect=dialect_for_source(source.kind)
            )
        except (c.CompileError, SqlRejected) as exc:
            raise RunFailed(f"Chưa lập được truy vấn cho mục tiêu: {exc}") from exc
        return Planned(
            understanding=u,
            compiled=compiled,
            source=source,
            metric_name=u.metric.name,
            unit=version.unit,
            decimals=version.decimals,
            dimension_names={d.code: d.name for d in layer.vocabulary.dimensions},
            tokens=tokens,
            models=models,
        )


def _emit(run: agent.AgentRun, previous: str | None) -> agent.Outbox:
    """The same event the api writes on a transition (routes._status_changed)."""
    return agent.Outbox(
        id=uuid7(),
        org_id=run.org_id,
        aggregate_type="agent_run",
        aggregate_id=run.id,
        event_type=EVENT_TYPE,
        payload={
            "run_id": str(run.id),
            "agent_id": str(run.agent_id),
            "config_version": run.config_version,
            "is_test": run.is_test,
            "previous_status": previous,
            "status": run.status,
            "stop_reason": run.stop_reason,
        },
    )


class Run:
    """One execution of a run: each method is its own transaction, so what is done stays done."""

    def __init__(self, run_id: uuid.UUID, org_id: uuid.UUID) -> None:
        self.id, self.org_id = run_id, org_id
        self.started = time.monotonic()
        # Who the run acts as: the person who started it, or the agent's own account for a
        # schedule or event (agent_def.account_user_id).
        self.principal: uuid.UUID = org_id

    def _session(self, principal: uuid.UUID | None = None) -> Any:
        return scoped_session(ROLE, self.org_id, principal)

    async def claim(self, previous: str | None) -> agent.AgentRun | None:
        """queued → planning, or a resumed run carrying on; anything else is not ours to run."""
        async with self._session() as session:
            run: agent.AgentRun | None = await session.get(
                agent.AgentRun, self.id, with_for_update=True
            )
            if run is None:
                return None
            owner = await session.get(agent.AgentDef, run.agent_id)
            if owner is None:
                return None
            self.principal = run.initiated_by or owner.account_user_id
            if run.status == "queued":
                run.status = "planning"
                run.started_at = run.started_at or _now()
                session.add(_emit(run, "queued"))
            elif not (run.status == "running" and previous == "paused"):
                return None
            await session.flush()
            session.expunge(run)
            return run

    async def steps(self) -> list[agent.AgentStep]:
        async with self._session() as session:
            return list(
                (
                    await session.scalars(
                        select(agent.AgentStep)
                        .where(agent.AgentStep.run_id == self.id)
                        .order_by(agent.AgentStep.seq)
                    )
                ).all()
            )

    async def record(
        self,
        *,
        kind: str,
        name: str,
        status: str,
        summary: str,
        told: str | None,
        input: dict[str, Any] | None = None,
        output: dict[str, Any] | None = None,
        fingerprint: str | None = None,
        result_hash: str | None = None,
        tokens: int = 0,
        then: str | None = None,
        started: datetime.datetime,
    ) -> str:
        """Writes one step and what the run shows of it; returns the run's status after it, so a
        pause or cancel the api wrote meanwhile stops the run at this boundary."""
        async with self._session() as session:
            run = await session.get(agent.AgentRun, self.id, with_for_update=True)
            assert run is not None
            seq = (
                int(
                    await session.scalar(
                        select(func.coalesce(func.max(agent.AgentStep.seq), 0)).where(
                            agent.AgentStep.run_id == self.id
                        )
                    )
                )
                + 1
            )
            now = _now()
            session.add(
                agent.AgentStep(
                    id=uuid7(),
                    org_id=self.org_id,
                    run_id=self.id,
                    seq=seq,
                    kind=kind,
                    name=name,
                    input=input,
                    output=output,
                    summary=summary,
                    status=status,
                    started_at=started,
                    duration_ms=int((now - started).total_seconds() * 1000),
                    call_fingerprint=fingerprint,
                    result_hash=result_hash,
                )
            )
            if told:
                run.narrative = [*(run.narrative or []), narrative(told, "done", now)]
            # The step the run has reached (agent_step.seq), what a resume carries on from.
            run.current_step = seq
            run.tokens = (run.tokens or 0) + tokens
            if then is not None and run.status not in ("paused", *TERMINAL):
                previous, run.status = run.status, then
                session.add(_emit(run, previous))
            return str(run.status)

    async def end(
        self,
        status: Literal["succeeded", "failed"],
        stop_reason: str,
        *,
        told: str | None,
        error: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        async with self._session() as session:
            run = await session.get(agent.AgentRun, self.id, with_for_update=True)
            if run is None or run.status in TERMINAL or run.status == "paused":
                return
            previous = run.status
            run.status, run.stop_reason, run.error = status, stop_reason, error
            run.finished_at = _now()
            run.stop_detail = detail or {}
            if told:
                run.narrative = [*(run.narrative or []), narrative(told, "done", run.finished_at)]
            session.add(_emit(run, previous))


def _over_budget(
    limits: Limits, used_steps: int, tokens: int, started: datetime.datetime | None
) -> tuple[str, dict[str, Any]] | None:
    """BR-170: the next step would go past a limit. Time paused is not taken out yet."""
    if used_steps >= limits.max_steps:
        return "budget_steps", {
            "used": used_steps,
            "limit": limits.max_steps,
            "budget": "max_steps",
        }
    if tokens >= limits.max_tokens:
        return "budget_tokens", {"used": tokens, "limit": limits.max_tokens, "budget": "max_tokens"}
    if started is not None:
        elapsed = int((_now() - started).total_seconds())
        if elapsed >= limits.max_duration_s:
            return "budget_duration", {
                "used": elapsed,
                "limit": limits.max_duration_s,
                "budget": "max_duration_s",
            }
    return None


async def execute(event: Event) -> None:
    if not wants_execution(event.payload):
        return
    work = Run(event.aggregate_id, event.org_id)
    run = await work.claim(event.payload.get("previous_status"))
    if run is None:
        return
    try:
        await _execute(work, run)
    except RunFailed as exc:
        await work.end("failed", exc.stop_reason, told=str(exc), error=str(exc))
    except DBAPIError:
        # The database went away mid-run: the event is retried and the run goes on from its
        # last recorded step.
        raise
    except Exception:
        logger.exception("agent run %s failed", work.id)
        message = "Lượt chạy dừng vì một lỗi nội bộ; người vận hành có thể xem nhật ký của worker."
        await work.end("failed", "error", told=message, error=message)


async def _execute(work: Run, run: agent.AgentRun) -> None:
    async with work._session() as session:
        config = await session.scalar(
            select(agent.AgentConfig).where(
                agent.AgentConfig.agent_id == run.agent_id,
                agent.AgentConfig.version == run.config_version,
            )
        )
        if config is None:
            raise RunFailed("Không tìm thấy phiên bản cấu hình của lượt chạy.")
        granted = {
            t.tool: t.permission_level
            for t in (
                await session.scalars(
                    select(agent.AgentTool).where(agent.AgentTool.agent_config_id == config.id)
                )
            ).all()
        }
    limits = effective_limits(config.limits or {})
    done = await work.steps()

    # 1. Plan, once: a resumed run reads back the chips it planned with.
    plan_row = next((s for s in done if s.kind == "plan"), None)
    started = _now()
    planned = await _plan_step(
        run,
        config.goal,
        (plan_row.output or {}).get("chips") if plan_row else None,
        work.principal,
    )
    if plan_row is None:
        plan_id = uuid7()
        ir = _plan_ir(planned.understanding, planned.compiled, run.as_of)
        async with scoped_session(QA_ROLE, run.org_id, work.principal) as session:
            session.add(
                qa.Plan(
                    id=plan_id,
                    org_id=run.org_id,
                    agent_run_id=run.id,
                    version=1,
                    chips=ir["chips"],
                    steps=ir,
                    confidence=decimal.Decimal(str(round(planned.understanding.confidence, 4))),
                )
            )
        status = await work.record(
            kind="plan",
            name="plan",
            status="ok",
            summary=planned.summary,
            told=f"Lập kế hoạch: {planned.summary}.",
            input={"goal": config.goal},
            output={"plan_id": str(plan_id), "chips": ir["chips"], "steps": ir["steps"]},
            tokens=planned.tokens,
            then="running",
            started=started,
        )
        async with work._session() as session:
            row = await session.get(agent.AgentRun, run.id)
            assert row is not None
            row.snapshot_refs = [
                {
                    "config_version": run.config_version,
                    "metric_version_ids": [str(planned.compiled.metric_version_id)],
                    "models": planned.models,
                }
            ]
        if status != "running":
            return
        done = await work.steps()

    # 2. The query step, unless an earlier pass already ran it.
    if any(s.kind == "query" and s.status == "ok" for s in done):
        await work.end("succeeded", "completed", told=None)
        return
    over = _over_budget(limits, len(done), int(run.tokens or 0) + planned.tokens, run.started_at)
    if over is not None:
        reason, detail = over
        raise_message = "Lượt chạy dừng vì đã chạm giới hạn của agent (BR-170)."
        await work.end("failed", reason, told=raise_message, error=raise_message, detail=detail)
        return
    await _query_step(work, run, planned, granted)


async def _query_step(
    work: Run, run: agent.AgentRun, planned: Planned, granted: Mapping[str, str]
) -> None:
    compiled, u = planned.compiled, planned.understanding
    args = {
        "metric_version_id": str(compiled.metric_version_id),
        "group_by": list(u.group_by),
        "filters": u.filters,
        "period": [u.period.start, u.period.end] if u.period else None,
    }
    started = _now()
    allowed = authority("query.semantic", granted, is_test=run.is_test)
    if allowed == "denied":
        message = (
            "Agent chưa được cấp công cụ truy vấn chỉ số (query.semantic), nên không chạy truy vấn."
        )
        await work.record(
            kind="query",
            name="query.semantic",
            status="skipped",
            summary=message,
            told=None,
            input={"sql": compiled.sql, "args": _normal(args)},
            started=started,
        )
        raise RunFailed(message)

    outcome: QueryOutcome = await run_query(
        org_id=run.org_id,
        data_space_id=run.data_space_id,
        principal_id=work.principal,
        caller_kind="agent_run",
        caller_id=run.id,
        source=planned.source,
        sql=compiled.sql,
        secrets=secrets(),
        metric_version_ids=[compiled.metric_version_id],
        as_of=run.as_of,
    )
    call = fingerprint("query.semantic", args)
    if not outcome.ok:
        message = (
            f"Nguồn {planned.source.name} không trả kết quả trong thời gian cho phép."
            if outcome.status == "timeout"
            else f"Chưa truy vấn được nguồn {planned.source.name}; người phụ trách dữ liệu có thể "
            "xem lượt chạy trong lịch sử truy vấn."
        )
        await work.record(
            kind="query",
            name="query.semantic",
            status="error",
            summary=message,
            told=None,
            input={"sql": compiled.sql, "args": _normal(args)},
            output={"query_run_id": str(outcome.query_run_id), "error": outcome.error},
            fingerprint=call,
            started=started,
        )
        raise RunFailed(message)

    figures = figures_of(
        list(compiled.columns),
        outcome.rows,
        metric_name=planned.metric_name,
        unit=planned.unit,
        decimals=planned.decimals,
        dimension_names=planned.dimension_names,
        period_label=u.period.label if u.period else None,
    )
    answer = (
        text_of(
            figures,
            metric_name=planned.metric_name,
            group_by_names=[planned.dimension_names.get(g, g) for g in u.group_by],
            period_label=u.period.label if u.period else None,
            filters_label=None,
            latest_note=None,
            truncated=outcome.truncated,
        )
        if outcome.rows
        else f"Không có dữ liệu {planned.metric_name} cho kỳ được hỏi."
    )
    status = await work.record(
        kind="query",
        name="query.semantic",
        status="ok",
        summary=answer,
        told=f"Đã truy vấn {planned.source.name}: {len(outcome.rows)} dòng.",
        input={
            "sql": compiled.sql,
            "args": _normal(args),
            "metric_version_id": str(compiled.metric_version_id),
        },
        output={
            "query_run_id": str(outcome.query_run_id),
            "row_count": len(outcome.rows),
            "columns": list(compiled.columns),
            "rows": [[_plain(v) for v in row] for row in outcome.rows[:ROWS_KEPT]],
            "truncated": outcome.truncated,
        },
        fingerprint=call,
        result_hash=outcome.result_hash,
        started=started,
    )
    if status != "running":
        return
    calls = [(s.call_fingerprint, s.result_hash) for s in await work.steps() if s.kind == "query"]
    if is_loop(calls):
        message = "Lượt chạy lặp lại cùng một truy vấn với cùng kết quả; đã dừng (AC-13-02-09)."
        await work.end("failed", "loop_detected", told=message, error=message)
        return
    await work.end("succeeded", "completed", told=answer)


HANDLERS: dict[str, Handler] = {EVENT_TYPE: execute}
