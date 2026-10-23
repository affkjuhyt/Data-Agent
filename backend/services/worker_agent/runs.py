"""Agent runs (UC-13-04, UC-13-06): the executor behind POST /v1/agents/{id}/test-runs, triggers
and onboarding's try-agent.

ADR-37 puts agent runs on Temporal (R3). Until it runs, this outbox executor stands in, under the
three guarantees tab 5 G1.1 asks of a non-Temporal worker: every step is written as it ends, so a
run picks up after the last recorded step; the run's state lives in PostgreSQL and is re-read at
each step boundary, so the api's pause and cancel (routes.py writes the status itself) are obeyed
without a message to this worker; and a redelivered event finds nothing left to do.

The goal is read like a question (rule pass, then the planner model), planned as the semantic fast
path's Plan IR and run in place. A goal of several lines is a report: one question per line
(questions_of), one query step and chart each, the answer numbered by part. An agent given
notify.internal then tells the result in
the app, after its checkpoint was approved in Cần bạn when it has one. A wait is reminded and
times out, and a passing failure is retried, on durable timers (TIMER). Deferred: the +50%
budget request and the monthly budget alerts, no_progress, plan edits, plans the LLM Planner
writes itself (steps that depend on one another), and recovering runs orphaned by a crash. Runs execute inline, so a long run holds this worker's poll loop for at most its BR-170
duration.
"""

from __future__ import annotations

import datetime
import decimal
import hashlib
import json
import logging
import re
import time
import unicodedata
import uuid
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError

from libs.dataagent_db import scoped_session, uuid7
from libs.dataagent_db.models import agent, catalog, notify, qa
from services.worker_agent import failures
from services.worker_agent.answer.insights import written
from services.worker_agent.answer.narrate import chart_spec_of, figures_of, text_of
from services.worker_agent.answer.thresholds import judged
from services.worker_agent.answer.thresholds import load as load_threshold
from services.worker_agent.audit.findings import audit
from services.worker_agent.audit.run import AuditFailed, Target, gather, load_target
from services.worker_agent.events import Event, Handler
from services.worker_agent.query.executor import QueryOutcome, run_query
from services.worker_agent.query.guard import SqlRejected
from services.worker_agent.query.scope import can_ask_data_space
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
from services.worker_agent.understand.text import norm, words_of

logger = logging.getLogger(__name__)

EVENT_TYPE = "run.status_changed"
# A durable timer in agent.outbox: its payload's `not_before` keeps it unclaimed until then
# (events.OutboxTableSource), so a reminder, a timeout or a retry survives a restart.
TIMER = "run.timer"
# AC-13-06-04: a passing failure is tried again after 5, 15 and 60 minutes, then the run fails.
RETRY_DELAYS = (
    datetime.timedelta(minutes=5),
    datetime.timedelta(minutes=15),
    datetime.timedelta(minutes=60),
)
# AC-13-06-05: a run waiting for a person is reminded on day 1 and 3, and ends on day 7.
REMINDERS = (datetime.timedelta(days=1), datetime.timedelta(days=3))
WAIT_LIMIT = datetime.timedelta(days=7)
# What a source or OpenBao says when it is down or slow, not when the query is wrong.
_PASSING = re.compile(
    r"Connection\w*Error|OperationalError|InterfaceError|TimeoutError|OSError|: connection"
    r"|OpenBao login failed \(5\d\d\)|could not be read \(5\d\d\)"
)
ROLE = "svc_agent"
QA_ROLE = "svc_qa"
NOTIFY_ROLE = "svc_notify"
NOTIFY_TOOL = "notify.internal"
# The read tool a data-quality agent audits with (tool_def: quality.status).
AUDIT_TOOL = "quality.status"
TERMINAL = frozenset({"succeeded", "failed", "cancelled"})
# Request noise a fingerprint leaves out (tab 5 G4.4): the same call twice is the same call.
_NOISE = frozenset({"request_id", "idempotency_key", "timestamp", "at"})
# The builder's draft grants the read tool as "query" (api agent routes, draft-from-text).
_ALIASES = {"query": "query.semantic"}
LOOP_REPEATS = 3
# A report's questions: each may cost a planner call before any budget step is counted.
MAX_QUESTIONS = 10
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
    resumed it mid-way, after a pause or once its checkpoint was decided. This executor's other
    transitions and pause/cancel need nothing."""
    status, previous = payload.get("status"), payload.get("previous_status")
    if status == "queued":
        return previous in (None, "failed", "paused")
    return status == "running" and previous in ("paused", "waiting_human")


def is_transient(status: str, error: str | None) -> bool:
    """A failure that may pass by itself (a timeout, a source or OpenBao down): worth a retry.
    A wrong query, a missing grant or a missing credential fails the same way every time."""
    return status == "timeout" or bool(error and _PASSING.search(error))


def wait_timers(since: datetime.datetime) -> list[tuple[str, datetime.datetime]]:
    return [("reminder", since + d) for d in REMINDERS] + [("timeout", since + WAIT_LIMIT)]


_MARKER = re.compile(r"^\s*(?:[-•*+]|\d+[.)])\s*")


def questions_of(goal: str) -> list[str]:
    """The questions a goal asks, one per line: a report's parts ("- Tỷ lệ nợ xấu theo vùng").
    List markers go; a first line ending in ":" is the report's title, not a question. A goal of
    one line is one question, as it was written."""
    lines = [_MARKER.sub("", line).strip() for line in goal.splitlines()]
    lines = [line for line in lines if line]
    if len(lines) > 1 and lines[0].endswith(":"):
        lines = lines[1:]
    return lines if len(lines) > 1 else [goal.strip()]


def retries_in_a_row(statuses: Sequence[str]) -> int:
    """How often the question being asked was retried: query steps since the last one that ran."""
    n = 0
    for status in reversed(statuses):
        if status != "retried":
            break
        n += 1
    return n


def needs_checkpoint(
    level: str, *, autonomy: str, checkpoints: Sequence[str], requires_checkpoint: bool
) -> bool:
    """Whether a tool call waits for a person (tab 5 G4.1). Sending outside always does
    (AC-13-02-05); writing derived data does when the config names the checkpoint or the tool
    requires one (BR-70 made the api ask for one unless the agent acts then reports)."""
    if level == "send_external":
        return True
    if level != "write_derived":
        return False
    return requires_checkpoint or "before_write_derived" in checkpoints


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


def _told(entries: Sequence[Any] | None, text: str, state: str, at: datetime.datetime) -> list[Any]:
    """The narrative with one more entry; what was going on ("now") is done once the run moves."""
    past = [
        {**e, "state": "done"} if isinstance(e, dict) and e.get("state") == "now" else e
        for e in (entries or [])
    ]
    return [*past, narrative(text, state, at)]


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
    """The run cannot go on; the message is the sentence the owner reads. `tell_owner`: the owner
    is told in the app too (a run that gave up after its retries, AC-13-06-04). `error_class`:
    why, as failures.py names it; the run's stop_detail records it with its route."""

    def __init__(
        self,
        message: str,
        stop_reason: str = "error",
        *,
        tell_owner: bool = False,
        error_class: failures.ErrorClass | None = None,
    ) -> None:
        super().__init__(message)
        self.stop_reason = stop_reason
        self.tell_owner = tell_owner
        self.error_class = error_class

    def detail(self) -> dict[str, Any]:
        if self.error_class is None:
            return {}
        route = failures.ROUTES[self.error_class]
        return {
            "error_class": self.error_class.value,
            "route": route.action,
            "code": route.code.value,
        }


@dataclass
class Planned:
    """The goal read and compiled, ready to run; nothing is written while it is made."""

    understanding: Understanding
    compiled: c.Compiled
    source: catalog.Source
    metric_id: uuid.UUID
    metric_name: str
    unit: str | None
    decimals: int
    period_semantics: str
    dimension_names: dict[str, str]
    text: str = ""
    time_dimensions: frozenset[str] = frozenset()
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
                "vậy. Hãy sửa mục tiêu theo tên chiều trong danh mục.",
                error_class=failures.ErrorClass.INTENT_MISMATCH,
            )
        if u.decision == "clarify":
            names = ", ".join(m.name for m in u.options)
            raise RunFailed(
                f"Mục tiêu khớp với nhiều chỉ số ({names}); hãy sửa mục tiêu cho rõ một chỉ số.",
                error_class=failures.ErrorClass.INTENT_MISMATCH,
            )
        if u.decision != "proceed" or u.metric is None:
            raise RunFailed(
                "Mục tiêu chưa gọi tên chỉ số nào của Data Space; hãy sửa mục tiêu theo tên chỉ số "
                "trong danh mục, hoặc nhờ người phụ trách thêm từ đồng nghĩa.",
                error_class=failures.ErrorClass.INTENT_MISMATCH,
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
        except c.CompileError as exc:
            raise RunFailed(
                f"Chưa lập được truy vấn cho mục tiêu: {exc}",
                error_class=failures.of_compile(exc.code),
            ) from exc
        except SqlRejected as exc:
            raise RunFailed(
                f"Chưa lập được truy vấn cho mục tiêu: {exc}",
                error_class=failures.ErrorClass.TOOL_GRAMMAR,
            ) from exc
        return Planned(
            understanding=u,
            compiled=compiled,
            source=source,
            metric_id=u.metric.metric_id,
            metric_name=u.metric.name,
            unit=version.unit,
            decimals=version.decimals,
            period_semantics=version.period_semantics,
            dimension_names={d.code: d.name for d in layer.vocabulary.dimensions},
            text=goal,
            time_dimensions=frozenset(d.code for d in layer.vocabulary.dimensions if d.is_time),
            tokens=tokens,
            models=models,
        )


async def _plan_all(
    run: agent.AgentRun,
    goal: str,
    saved: list[dict[str, Any]] | None,
    principal: uuid.UUID,
    max_tokens: int,
) -> list[Planned]:
    """Each question of the goal planned on its own; one the Data Space cannot read stops the plan
    and is named. A resumed run plans from the chips it saved, question by question."""
    asked: list[tuple[str, list[dict[str, Any]] | None]] = (
        [(q["text"], q["chips"]) for q in saved]
        if saved
        else [(q, None) for q in questions_of(goal)]
    )
    if len(asked) > MAX_QUESTIONS:
        raise RunFailed(
            f"Mục tiêu có {len(asked)} câu hỏi; một lượt chạy trả lời tối đa {MAX_QUESTIONS} câu. "
            "Hãy rút gọn mục tiêu hoặc chia thành nhiều agent.",
            "budget_steps",
        )
    planned: list[Planned] = []
    spent = int(run.tokens or 0)
    for n, (text, chips) in enumerate(asked, start=1):
        try:
            planned.append(await _plan_step(run, text, chips, principal))
        except RunFailed as exc:
            if len(asked) == 1:
                raise
            raise RunFailed(
                f"Câu “{text}”: {exc}", exc.stop_reason, error_class=exc.error_class
            ) from exc
        spent += planned[-1].tokens
        # BR-170 holds while planning too: no further model call once the tokens are spent.
        if spent >= max_tokens and n < len(asked):
            raise RunFailed(
                f"Lập kế hoạch dừng ở câu {n}/{len(asked)}: đã dùng {spent} token, chạm giới hạn "
                f"{max_tokens} của agent (BR-170).",
                "budget_tokens",
            )
    return planned


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


def _timer(run: agent.AgentRun, timer: str, at: datetime.datetime, **extra: Any) -> agent.Outbox:
    return agent.Outbox(
        id=uuid7(),
        org_id=run.org_id,
        aggregate_type="agent_run",
        aggregate_id=run.id,
        event_type=TIMER,
        payload={"run_id": str(run.id), "timer": timer, "not_before": at.isoformat(), **extra},
    )


async def _tell(
    org_id: uuid.UUID,
    principal: uuid.UUID,
    users: Sequence[uuid.UUID],
    *,
    kind: str,
    title: str,
    body: str,
    link: str,
    data_space_id: uuid.UUID | None,
    source_ref: uuid.UUID,
) -> None:
    """In-app notifications (notify.notification), written as svc_notify."""
    async with scoped_session(NOTIFY_ROLE, org_id, principal) as session:
        for user in sorted(set(users)):
            session.add(
                notify.Notification(
                    id=uuid7(),
                    org_id=org_id,
                    user_id=user,
                    data_space_id=data_space_id,
                    kind=kind,
                    title=title,
                    body=body,
                    link=link,
                    source_ref=source_ref,
                )
            )


class Run:
    """One execution of a run: each method is its own transaction, so what is done stays done."""

    def __init__(self, run_id: uuid.UUID, org_id: uuid.UUID) -> None:
        self.id, self.org_id = run_id, org_id
        self.started = time.monotonic()
        # Who the run acts as: the person who started it, or the agent's own account for a
        # schedule or event (agent_def.account_user_id).
        self.principal: uuid.UUID = org_id
        self.owner: uuid.UUID = org_id
        self.agent_name = ""

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
            self.owner, self.agent_name = owner.owner_user_id, owner.name
            if run.status == "queued":
                run.status = "planning"
                run.started_at = run.started_at or _now()
                session.add(_emit(run, "queued"))
            elif not (run.status == "running" and previous in ("paused", "waiting_human", "retry")):
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
        told_state: str = "done",
        input: dict[str, Any] | None = None,
        output: dict[str, Any] | None = None,
        fingerprint: str | None = None,
        result_hash: str | None = None,
        tokens: int = 0,
        then: str | None = None,
        started: datetime.datetime,
        also: Callable[[Any, agent.AgentRun], None] | None = None,
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
                run.narrative = _told(run.narrative, told, told_state, now)
            # The step the run has reached (agent_step.seq), what a resume carries on from.
            run.current_step = seq
            run.tokens = (run.tokens or 0) + tokens
            if run.status not in ("paused", *TERMINAL):
                if then is not None:
                    previous, run.status = run.status, then
                    session.add(_emit(run, previous))
                if also is not None:
                    # Written with the step, so a run never waits on a request or a timer that
                    # was not made.
                    also(session, run)
            return str(run.status)

    async def checkpoint_outcome(self, checkpoint: str) -> str:
        """The latest hit of this checkpoint: none, open, or its outcome."""
        async with self._session() as session:
            hit = await session.scalar(
                select(agent.AgentCheckpointHit)
                .where(
                    agent.AgentCheckpointHit.run_id == self.id,
                    agent.AgentCheckpointHit.checkpoint == checkpoint,
                )
                .order_by(agent.AgentCheckpointHit.created_at.desc())
                .limit(1)
            )
            if hit is None:
                return "none"
            return hit.outcome or ("open" if hit.resolved_at is None else "expired")

    async def ask(
        self,
        checkpoint: str,
        owner: agent.AgentDef,
        *,
        title: str,
        preview: str,
        args: dict[str, Any],
        started: datetime.datetime,
    ) -> None:
        """The run stops for a person: a checkpoint step, its hit and the HITL request in Cần bạn
        (hitl.requested, opened by hitl.py), all with the move to waiting_human."""
        request_id = uuid7()

        def open_request(session: Any, run: agent.AgentRun) -> None:
            run.waiting_since = _now()
            for timer, at in wait_timers(run.waiting_since):
                session.add(_timer(run, timer, at, hitl_request_id=str(request_id)))
            session.add(
                agent.AgentCheckpointHit(
                    id=uuid7(),
                    org_id=self.org_id,
                    run_id=self.id,
                    checkpoint=checkpoint,
                    hitl_request_id=request_id,
                )
            )
            session.add(
                agent.Outbox(
                    id=uuid7(),
                    org_id=self.org_id,
                    aggregate_type="agent_run",
                    aggregate_id=self.id,
                    event_type="hitl.requested",
                    # The inbox contract (api inbox/public.hitl_requested); the agent's owner decides.
                    payload={
                        "hitl_request_id": str(request_id),
                        "data_space_id": str(run.data_space_id),
                        "kind": "checkpoint",
                        "risk": "medium",
                        "title": title,
                        "context": {
                            "run_id": str(self.id),
                            "agent_id": str(run.agent_id),
                            "checkpoint": checkpoint,
                            "tool": NOTIFY_TOOL,
                            "preview": preview,
                        },
                        "options": [],
                        "source_service": "agent",
                        "source_ref": str(self.id),
                        "created_by": str(self.principal),
                        "assignee_id": str(owner.owner_user_id),
                        "assignee_group": None,
                        "assignee_capability": None,
                        "approval_steps": [],
                        "excluded_approver_ids": [],
                        "due_at": (_now() + WAIT_LIMIT).isoformat(),
                        "parent_run_label": None,
                    },
                )
            )

        await self.record(
            kind="checkpoint",
            name=checkpoint,
            status="ok",
            summary=f"Chờ duyệt trước khi chạy {NOTIFY_TOOL}.",
            told="Chờ chủ sở hữu agent duyệt trong Cần bạn trước khi gửi kết quả.",
            told_state="now",
            input=args,
            then="waiting_human",
            started=started,
            also=open_request,
        )

    async def wait_again(self) -> None:
        async with self._session() as session:
            run = await session.get(agent.AgentRun, self.id, with_for_update=True)
            if run is not None and run.status == "running":
                run.status, run.waiting_since = "waiting_human", _now()
                session.add(_emit(run, "running"))

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
                run.narrative = _told(run.narrative, told, "done", run.finished_at)
            session.add(_emit(run, previous))


def _latest_note(planned: Planned) -> str | None:
    """What a goal without a period was answered with (compiler: a balance's last day)."""
    if planned.understanding.period is not None:
        return None
    if planned.period_semantics == "end_of_period":
        return "Không nêu kỳ: số liệu ngày mới nhất có dữ liệu trong nguồn."
    return "Không nêu kỳ: cộng trên toàn bộ dữ liệu có trong nguồn."


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


def _stop_for_budget(
    limits: Limits, done: Sequence[agent.AgentStep], run: agent.AgentRun, tokens: int
) -> tuple[str, dict[str, Any]] | None:
    return _over_budget(limits, len(done), int(run.tokens or 0) + tokens, run.started_at)


async def _ended_by_budget(work: Run, over: tuple[str, dict[str, Any]] | None) -> bool:
    if over is None:
        return False
    reason, detail = over
    message = "Lượt chạy dừng vì đã chạm giới hạn của agent (BR-170)."
    await work.end("failed", reason, told=message, error=message, detail=detail)
    return True


async def execute(event: Event) -> None:
    if not wants_execution(event.payload):
        return
    work = Run(event.aggregate_id, event.org_id)
    run = await work.claim(event.payload.get("previous_status"))
    if run is not None:
        await _go_on(work, run)


async def _go_on(work: Run, run: agent.AgentRun) -> None:
    try:
        await _execute(work, run)
    except RunFailed as exc:
        await work.end(
            "failed", exc.stop_reason, told=str(exc), error=str(exc), detail=exc.detail()
        )
        if exc.tell_owner and not run.is_test:
            await _tell(
                run.org_id,
                work.principal,
                [work.owner],
                kind="alert",
                title=f"{work.agent_name}: lượt chạy không chạy được",
                body=str(exc),
                link=f"/agents/run/{run.id}",
                data_space_id=run.data_space_id,
                source_ref=run.id,
            )
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
        owner = await session.get(agent.AgentDef, run.agent_id)
        assert owner is not None
        # An agent sees its own Data Space only (R1-11): the api writes a run with the agent's
        # space, so a row naming another one is refused before anything of that space is read,
        # as is a run whose principal holds no ask grant there (the Q&A path's check).
        if run.data_space_id != owner.data_space_id:
            raise RunFailed("Lượt chạy ghi một Data Space khác với Data Space của agent.")
        if not await can_ask_data_space(
            session, data_space_id=run.data_space_id, principal_id=work.principal
        ):
            raise RunFailed(
                "Người chạy agent không thuộc Data Space hoặc chưa được cấp quyền hỏi dữ liệu."
            )
        tools = (
            await session.scalars(
                select(agent.AgentTool).where(agent.AgentTool.agent_config_id == config.id)
            )
        ).all()
        granted = {t.tool: t.permission_level for t in tools}
        requires = {t.tool: t.requires_checkpoint for t in tools}
    limits = effective_limits(config.limits or {})
    done = await work.steps()
    if owner.kind == "data_quality":
        # A data-quality agent audits the table its goal names (audit/), it does not ask questions.
        await _audit_flow(work, run, config, owner, granted, requires, limits, done)
        return

    # 1. Plan, once: a resumed run reads back the chips it planned with.
    plan_row = next((s for s in done if s.kind == "plan"), None)
    started = _now()
    saved = (plan_row.output or {}) if plan_row else {}
    planned = await _plan_all(
        run,
        config.goal,
        saved.get("questions")
        or ([{"text": config.goal, "chips": saved["chips"]}] if "chips" in saved else None),
        work.principal,
        limits.max_tokens,
    )
    tokens = sum(p.tokens for p in planned)
    if plan_row is None:
        plan_id = uuid7()
        irs = [_plan_ir(p.understanding, p.compiled, run.as_of) for p in planned]
        steps = [{**ir["steps"][0], "id": f"s{i}"} for i, ir in enumerate(irs, start=1)]
        chips = [chip for ir in irs for chip in ir["chips"]]
        summary = "; ".join(p.summary for p in planned)
        async with scoped_session(QA_ROLE, run.org_id, work.principal) as session:
            session.add(
                qa.Plan(
                    id=plan_id,
                    org_id=run.org_id,
                    agent_run_id=run.id,
                    version=1,
                    chips=chips,
                    steps={
                        **irs[0],
                        "chips": chips,
                        "steps": steps,
                        "outputs": [{"step": st["id"], "as": "table"} for st in steps],
                    },
                    confidence=decimal.Decimal(
                        str(round(min(p.understanding.confidence for p in planned), 4))
                    ),
                )
            )
        status = await work.record(
            kind="plan",
            name="plan",
            status="ok",
            summary=summary,
            told=(
                f"Lập kế hoạch: {summary}."
                if len(planned) == 1
                else f"Lập kế hoạch {len(planned)} phần: {summary}."
            ),
            input={"goal": config.goal},
            output={
                "plan_id": str(plan_id),
                "chips": irs[0]["chips"],
                "questions": [
                    {"text": p.text, "chips": ir["chips"]}
                    for p, ir in zip(planned, irs, strict=True)
                ],
                "steps": steps,
            },
            tokens=tokens,
            then="running",
            started=started,
        )
        async with work._session() as session:
            row = await session.get(agent.AgentRun, run.id)
            assert row is not None
            row.snapshot_refs = [
                {
                    "config_version": run.config_version,
                    "metric_version_ids": [str(p.compiled.metric_version_id) for p in planned],
                    "models": [m for p in planned for m in p.models],
                }
            ]
        if status != "running":
            return
        done = await work.steps()

    # 2. One query step per question, from the first one not answered yet.
    answers = [s.summary or "" for s in done if s.kind == "query" and s.status == "ok"]
    for i in range(len(answers), len(planned)):
        if await _ended_by_budget(work, _stop_for_budget(limits, done, run, tokens)):
            return
        ran = await _query_step(work, run, planned[i], granted, part=(i + 1, len(planned)))
        if ran is None:
            return
        answers.append(ran)
        done = await work.steps()
    answer = (
        answers[0]
        if len(planned) == 1
        else "\n".join(f"{i}. {a}" for i, a in enumerate(answers, start=1))
    )

    await _tell_and_end(work, run, config, owner, granted, requires, limits, done, tokens, answer)


async def _tell_and_end(
    work: Run,
    run: agent.AgentRun,
    config: agent.AgentConfig,
    owner: agent.AgentDef,
    granted: Mapping[str, str],
    requires: Mapping[str, bool],
    limits: Limits,
    done: Sequence[agent.AgentStep],
    tokens: int,
    answer: str,
) -> None:
    """Tell the result inside the organisation, when the agent was given that tool, then end."""
    if NOTIFY_TOOL in granted and not any(
        s.name == NOTIFY_TOOL and s.kind == "tool_call" for s in done
    ):
        if await _ended_by_budget(work, _stop_for_budget(limits, done, run, tokens)):
            return
        if not await _notify_step(work, run, config, owner, granted, requires, answer):
            return
    await work.end("succeeded", "completed", told=answer)


async def _audit_target(
    run: agent.AgentRun, goal: str, principal: uuid.UUID, dataset_id: str | None
) -> Target:
    """The table the goal names among the Data Space's datasets (its display or table name), with
    its catalogued columns and where to query it in place."""
    async with scoped_session(QA_ROLE, run.org_id, principal) as session:
        datasets = (
            await session.scalars(
                select(catalog.Dataset).where(
                    catalog.Dataset.data_space_id == run.data_space_id,
                    catalog.Dataset.enabled.is_(True),
                    catalog.Dataset.deleted_at.is_(None),
                )
            )
        ).all()
        if dataset_id is not None:
            chosen = next((d for d in datasets if str(d.id) == dataset_id), None)
        else:
            said = f" {norm(goal)} "
            named = [
                (len(name), d)
                for d in datasets
                for name in {norm(d.display_name or ""), norm(d.table_name)}
                if name and f" {name} " in said
            ]
            chosen = max(named, key=lambda x: x[0])[1] if named else None
        if chosen is None:
            listed = ", ".join(sorted(d.display_name or d.table_name for d in datasets)[:8])
            raise RunFailed(
                "Mục tiêu chưa gọi tên bảng nào của Data Space để kiểm toán"
                + (f"; các bảng có: {listed}." if listed else "; Data Space chưa có bảng nào.")
            )
        try:
            return await load_target(session, chosen, run.org_id, run.data_space_id)
        except AuditFailed as exc:
            raise RunFailed(str(exc)) from exc


async def _audit_flow(
    work: Run,
    run: agent.AgentRun,
    config: agent.AgentConfig,
    owner: agent.AgentDef,
    granted: Mapping[str, str],
    requires: Mapping[str, bool],
    limits: Limits,
    done: list[agent.AgentStep],
) -> None:
    plan_row = next((s for s in done if s.kind == "plan"), None)
    started = _now()
    target = await _audit_target(
        run,
        config.goal,
        work.principal,
        (plan_row.output or {}).get("dataset_id") if plan_row else None,
    )
    name = target.dataset.display_name or target.dataset.table_name
    if plan_row is None:
        status = await work.record(
            kind="plan",
            name="plan",
            status="ok",
            summary=f"Kiểm toán {name}: {len(target.columns)} cột",
            told=f"Lập kế hoạch: kiểm toán bảng {name} ({len(target.columns)} cột).",
            input={"goal": config.goal},
            output={
                "dataset_id": str(target.dataset.id),
                "columns": [c.name for c in target.columns],
            },
            then="running",
            started=started,
        )
        if status != "running":
            return
        done = await work.steps()

    audited = next((s for s in done if s.name == AUDIT_TOOL and s.status == "ok"), None)
    if audited is not None:
        answer = audited.summary or ""
    else:
        if await _ended_by_budget(work, _stop_for_budget(limits, done, run, 0)):
            return
        started = _now()
        if authority(AUDIT_TOOL, granted, is_test=run.is_test) == "denied":
            message = "Agent chưa được cấp công cụ kiểm tra chất lượng dữ liệu (quality.status)."
            await work.record(
                kind="tool_call",
                name=AUDIT_TOOL,
                status="skipped",
                summary=message,
                told=None,
                input={"dataset_id": str(target.dataset.id)},
                started=started,
            )
            raise RunFailed(message)
        try:
            got = await gather(
                target,
                dialect=dialect_for_source(target.source.kind),
                org_id=run.org_id,
                space_id=run.data_space_id,
                principal=work.principal,
                run_id=run.id,
                secrets=secrets(),
            )
        except AuditFailed as exc:
            raise RunFailed(str(exc)) from exc
        result = audit(name, target.columns, got.aggregates)
        when = f", ảnh chụp {got.snapshot:%d/%m/%Y}" if got.snapshot else ""
        answer = result.summary.replace(
            f"({vn_rows(got.aggregates.rows)} dòng)",
            f"({vn_rows(got.aggregates.rows)} dòng{when})",
            1,
        )
        status = await work.record(
            kind="tool_call",
            name=AUDIT_TOOL,
            status="ok",
            summary=answer,
            told=f"Đã kiểm toán {name}: {len(got.query_run_ids)} truy vấn chỉ đọc.",
            input={
                "dataset_id": str(target.dataset.id),
                "snapshot": str(got.snapshot) if got.snapshot else None,
            },
            output={
                "dataset_id": str(target.dataset.id),
                "snapshot": got.snapshot.isoformat() if got.snapshot else None,
                "rows": got.aggregates.rows,
                "profiles": result.profiles,
                "findings": [f.__dict__ for f in result.findings],
                "parts": result.parts,
                # Rules a person may take (POST /v1/quality-rules); none is made here.
                "suggestions": result.suggestions,
                "query_run_ids": got.query_run_ids,
            },
            started=started,
        )
        if status != "running":
            return
        done = await work.steps()
    await _tell_and_end(work, run, config, owner, granted, requires, limits, done, 0, answer)


def vn_rows(n: int) -> str:
    return f"{n:,}".replace(",", ".")


async def _query_step(
    work: Run,
    run: agent.AgentRun,
    planned: Planned,
    granted: Mapping[str, str],
    *,
    part: tuple[int, int] = (1, 1),
) -> str | None:
    """Runs the planned query; returns the answer, or None when the run stopped here."""
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
        steps = [s for s in await work.steps() if s.kind == "query"]
        tried = retries_in_a_row([s.status for s in steps])
        error_class = failures.of_query(transient=is_transient(outcome.status, outcome.error))
        # The classes of the retries since the last query that ran, then this one: the route
        # decides, and a class back more often than it allows stops the run (R0-11).
        seen = (
            [(s.output or {}).get("error_class") for s in steps[len(steps) - tried :]]
            if tried
            else []
        )
        # A retry recorded before error_class was (seen holds None) still counts against the waits.
        if not failures.stops([*seen, error_class]) and tried < len(RETRY_DELAYS):
            delay = RETRY_DELAYS[tried]
            minutes = int(delay.total_seconds() // 60)
            await work.record(
                kind="query",
                name="query.semantic",
                status="retried",
                summary=f"{message} Thử lại sau {minutes} phút (lần {tried + 1}/{len(RETRY_DELAYS)}).",
                told=f"Lỗi tạm thời ở nguồn; thử lại sau {minutes} phút.",
                input={"sql": compiled.sql, "args": _normal(args)},
                output={
                    "query_run_id": str(outcome.query_run_id),
                    "error": outcome.error,
                    "error_class": error_class.value,
                },
                fingerprint=call,
                started=started,
                also=lambda session, row: session.add(
                    _timer(row, "retry", _now() + delay, attempt=tried + 1)
                ),
            )
            return None
        await work.record(
            kind="query",
            name="query.semantic",
            status="error",
            summary=message,
            told=None,
            input={"sql": compiled.sql, "args": _normal(args)},
            output={
                "query_run_id": str(outcome.query_run_id),
                "error": outcome.error,
                "error_class": error_class.value,
            },
            fingerprint=call,
            started=started,
        )
        if tried:
            raise RunFailed(
                f"{message} Đã thử lại {tried} lần.", tell_owner=True, error_class=error_class
            )
        raise RunFailed(message, error_class=error_class)

    figures = figures_of(
        list(compiled.columns),
        outcome.rows,
        metric_name=planned.metric_name,
        unit=planned.unit,
        decimals=planned.decimals,
        dimension_names=planned.dimension_names,
        period_label=u.period.label if u.period else None,
    )
    # Where the result stands against the metric's threshold watch (answer/thresholds.py).
    threshold = await load_threshold(
        run.org_id, run.data_space_id, planned.metric_id, work.principal
    )
    against = (
        judged(figures, threshold, metric_name=planned.metric_name, next_key=len(figures) + 1)
        if threshold is not None
        else None
    )
    answer = (
        text_of(
            figures,
            metric_name=planned.metric_name,
            group_by_names=[planned.dimension_names.get(g, g) for g in u.group_by],
            period_label=u.period.label if u.period else None,
            filters_label=None,
            latest_note=_latest_note(planned),
            truncated=outcome.truncated,
            insights=written(against.sentences, figures + against.figures) if against else None,
        )
        if outcome.rows
        else f"Không có dữ liệu {planned.metric_name} cho kỳ được hỏi."
    )
    status = await work.record(
        kind="query",
        name="query.semantic",
        status="ok",
        summary=answer,
        told=(
            f"Đã truy vấn {planned.source.name}: {len(outcome.rows)} dòng."
            if part[1] == 1
            else f"Phần {part[0]}/{part[1]}: đã truy vấn {planned.source.name}, "
            f"{len(outcome.rows)} dòng."
        ),
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
            "question": planned.text,
            # How the result is drawn (AC-01-01-01): a line along time, bars otherwise.
            "chart_spec": chart_spec_of(
                figures,
                group_by=list(u.group_by),
                dimension_names=planned.dimension_names,
                time_dimensions=set(planned.time_dimensions),
                metric_name=planned.metric_name,
                unit=planned.unit,
            ),
        },
        fingerprint=call,
        result_hash=outcome.result_hash,
        started=started,
    )
    if status != "running":
        return None
    calls = [(s.call_fingerprint, s.result_hash) for s in await work.steps() if s.kind == "query"]
    if is_loop(calls):
        message = "Lượt chạy lặp lại cùng một truy vấn với cùng kết quả; đã dừng (AC-13-02-09)."
        await work.end("failed", "loop_detected", told=message, error=message)
        return None
    return answer


async def _notify_step(
    work: Run,
    run: agent.AgentRun,
    config: agent.AgentConfig,
    owner: agent.AgentDef,
    granted: Mapping[str, str],
    requires: Mapping[str, bool],
    answer: str,
) -> bool:
    """notify.internal: the result goes to the agent's owner in the app (kind report). Returns
    False when the run stopped here (waiting for a person, paused, cancelled)."""
    level = granted[NOTIFY_TOOL]
    recipients = sorted({owner.owner_user_id, *([run.initiated_by] if run.initiated_by else [])})
    title = f"{owner.name}: kết quả lượt chạy"
    link = f"/agents/run/{run.id}"
    args = {"recipients": [str(r) for r in recipients], "title": title, "link": link}
    started = _now()
    if authority(NOTIFY_TOOL, granted, is_test=run.is_test) == "simulate":
        await work.record(
            kind="tool_call",
            name=NOTIFY_TOOL,
            status="simulated",
            summary="Lượt chạy thử: sẽ gửi kết quả tới chủ sở hữu agent trong ứng dụng; chưa gửi.",
            told="Lượt chạy thử: bỏ qua việc gửi thông báo (chỉ mô phỏng).",
            input=args,
            fingerprint=fingerprint(NOTIFY_TOOL, args),
            started=started,
        )
        return True
    if needs_checkpoint(
        level,
        autonomy=config.autonomy,
        checkpoints=[str(c) for c in (config.checkpoints or [])],
        requires_checkpoint=bool(requires.get(NOTIFY_TOOL)),
    ):
        name = "send_external" if level == "send_external" else "before_write_derived"
        outcome = await work.checkpoint_outcome(name)
        if outcome == "none":
            await work.ask(
                name,
                owner,
                title=f"{owner.name}: duyệt gửi kết quả",
                preview=answer,
                args=args,
                started=started,
            )
            return False
        if outcome == "open":
            # Resumed from a pause while its request is still open: it goes back to waiting.
            await work.wait_again()
            return False
        if outcome != "approved":
            await work.record(
                kind="tool_call",
                name=NOTIFY_TOOL,
                status="skipped",
                summary="Người duyệt không đồng ý gửi kết quả; bước gửi được bỏ qua.",
                told="Không gửi kết quả: người duyệt không đồng ý.",
                input=args,
                started=started,
            )
            return True
    await _tell(
        run.org_id,
        work.principal,
        recipients,
        kind="report",
        title=title,
        body=answer,
        link=link,
        data_space_id=run.data_space_id,
        source_ref=run.id,
    )
    status = await work.record(
        kind="tool_call",
        name=NOTIFY_TOOL,
        status="ok",
        summary=f"Đã gửi kết quả tới {len(recipients)} người trong ứng dụng.",
        told=f"Đã gửi kết quả tới {len(recipients)} người trong ứng dụng.",
        input=args,
        fingerprint=fingerprint(NOTIFY_TOOL, args),
        started=started,
    )
    return status == "running"


async def resume_after_checkpoint(event: Event) -> None:
    """hitl.resolved for an agent checkpoint (resolutions.ROUTES): records the decision on the
    hit and puts a waiting run back to running, which this executor picks up again."""
    p = event.payload
    request_id = uuid.UUID(str(p["hitl_request_id"]))
    decided = {"approved": "approved", "rejected": "rejected", "expired": "expired"}.get(
        str(p.get("status")), "rejected"
    )
    async with scoped_session(ROLE, event.org_id) as session:
        hit = await session.scalar(
            select(agent.AgentCheckpointHit)
            .where(agent.AgentCheckpointHit.hitl_request_id == request_id)
            .with_for_update()
        )
        if hit is None or hit.resolved_at is not None:
            return
        hit.resolved_at, hit.outcome = _now(), decided
        run = await session.get(agent.AgentRun, hit.run_id, with_for_update=True)
        if run is None or run.status != "waiting_human":
            # Paused or cancelled meanwhile: the decision waits on the hit for the resume.
            return
        run.status, run.waiting_since = "running", None
        session.add(_emit(run, "waiting_human"))


async def on_timer(event: Event) -> None:
    """A run's timer came due: a retry carries on from the failed step; a reminder tells the owner
    again; a timeout ends a run nobody answered (AC-13-06-04/05). A timer whose moment passed
    (the run moved on, the checkpoint was decided) does nothing."""
    p = event.payload
    if p.get("timer") == "retry":
        work = Run(event.aggregate_id, event.org_id)
        last = (await work.steps())[-1:]
        attempt = int(p.get("attempt") or 0)
        tried = retries_in_a_row([s.status for s in await work.steps() if s.kind == "query"])
        if not last or last[0].status != "retried" or tried != attempt:
            return
        run = await work.claim("retry")
        if run is not None:
            await _go_on(work, run)
        return
    if p.get("timer") in ("reminder", "timeout"):
        await _wait_timer(event, str(p["timer"]), uuid.UUID(str(p["hitl_request_id"])))


async def _wait_timer(event: Event, timer: str, request_id: uuid.UUID) -> None:
    now = _now()
    async with scoped_session(ROLE, event.org_id) as session:
        run = await session.get(agent.AgentRun, event.aggregate_id, with_for_update=True)
        hit = await session.scalar(
            select(agent.AgentCheckpointHit)
            .where(agent.AgentCheckpointHit.hitl_request_id == request_id)
            .with_for_update()
        )
        if run is None or hit is None or hit.resolved_at is not None:
            return
        if run.status != "waiting_human":
            return
        owner = await session.get(agent.AgentDef, run.agent_id)
        assert owner is not None
        principal = run.initiated_by or owner.account_user_id
        if timer == "reminder":
            # An event, not a sentence: the screen keeps showing what the run waits for.
            run.narrative = [*(run.narrative or []), {"event": "reminder", "at": now.isoformat()}]
            title = f"Nhắc: {owner.name} đang chờ bạn duyệt"
            body = "Lượt chạy đang chờ quyết định của bạn trong Cần bạn."
            kind, link = "hitl", "/needs-you"
        else:
            message = (
                "Không ai duyệt trong 7 ngày nên lượt chạy tự huỷ; ngữ cảnh của lượt mở lại được "
                "trong 30 ngày."
            )
            hit.resolved_at, hit.outcome = now, "expired"
            run.status, run.stop_reason, run.error = "cancelled", "waiting_timeout", message
            run.finished_at = now
            run.narrative = _told(run.narrative, message, "done", now)
            session.add(_emit(run, "waiting_human"))
            title, body = f"{owner.name}: lượt chạy tự huỷ vì không ai duyệt", message
            kind, link = "alert", f"/agents/run/{run.id}"
        org, space, owner_id = run.org_id, run.data_space_id, owner.owner_user_id
    await _tell(
        org,
        principal,
        [owner_id],
        kind=kind,
        title=title,
        body=body,
        link=link,
        data_space_id=space,
        source_ref=event.aggregate_id,
    )


HANDLERS: dict[str, Handler] = {EVENT_TYPE: execute, TIMER: on_timer}
