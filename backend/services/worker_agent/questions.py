"""Answering a question (UC-01-01): the `question.received` handler, the fast path of tab 5 G1.3.

The worker takes the question the api recorded and moves it through
received → planning → running → answered / clarifying / refused / failed, which is what
GET /v1/questions/{id}/stream reports. Three steps, each in its own transaction so a slow source
never holds a lock on the question:

1. understand and plan, as svc_qa: the Data Space's semantic layer (semantic/loader.py), the rule
   Understander (understand/), the metric version in force, the compiled SQL. A question that names
   no metric is refused with UNRESOLVED_ENTITY; two equally likely metrics ask which, before any
   query (AC-01-01-02); a cut the compiler cannot make is refused with its code.
2. run the SQL in place on the source, as svc_query (query/executor.py).
3. answer, as svc_qa: figures F1…Fn from the rows, each tied to its query_run row, the text written
   from them (answer/narrate.py), the trust label, and, when the metric is in an active data
   product, the freshness gate and a receipt sealed in the same transaction (ADR-43, BR-175..178).

Only single-metric questions take this path; planning several steps with a model and
comparisons are R1 S5. A question that names no metric is looked up in the Data Space's documents
before it is refused (UC-05-04, documents/): an answer whose every claim carries a checked
citation, or "không tìm thấy trong tài liệu được phép". The handler is idempotent: a question past
`received` is left alone.
"""

from __future__ import annotations

import datetime
import decimal
import hashlib
import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.contracts.v1.errors import ErrorCode, NextAction, Refusal
from libs.dataagent_db import scoped_session, uuid7
from libs.dataagent_db.models import catalog, context, document, qa
from services.worker_agent.answer import narrator
from services.worker_agent.answer.insights import insights_of, is_additive, written
from services.worker_agent.answer.narrate import chart_spec_of, figures_of, text_of
from services.worker_agent.answer.thresholds import judged
from services.worker_agent.answer.thresholds import load as load_threshold
from services.worker_agent.documents import answer as document_answer
from services.worker_agent.documents.retrieve import retrieve
from services.worker_agent.documents.search import SearchServices, services_from_env
from services.worker_agent.events import Event, Handler
from services.worker_agent.llm import Gateway, gateway_from_env
from services.worker_agent.progress import publish
from services.worker_agent.query.executor import run_query
from services.worker_agent.query.guard import SqlRejected, dialect_of
from services.worker_agent.query.secrets import SecretReader, StaticReader, reader_from_env
from services.worker_agent.semantic import compiler as c
from services.worker_agent.semantic.loader import SpaceLayer, load
from services.worker_agent.sqlgen.answer import answer_by_sql
from services.worker_agent.understand import llm_reader
from services.worker_agent.understand.resolver import (
    Understanding,
    follow_up,
    from_chips,
    understand,
)
from services.worker_agent.understand.text import norm

logger = logging.getLogger(__name__)

ROLE = "svc_qa"
AGENT_NAME = "Agent Hỏi đáp"
RUNTIME_VERSION = "worker-agent-r1"
QUERYABLE = ("active", "testing")
INTERNAL_WORKSPACE = "internal://workspace"
# "Vì sao NPL tăng?": the question asks what moved, which the decomposition skill answers.
_WHY = re.compile(r"\b(?:vi sao|tai sao|nguyen nhan|ly do|do dau)\b")

# Trust labels of tab 5 G5.3 / G5.16: what the answer says about where its numbers come from.
STANDARD = "Theo chỉ số chuẩn"
NOT_STANDARD = "chưa có định nghĩa chuẩn"
OUT_OF_SCOPE = "Ngoài phạm vi data product, không có biên nhận"

DOCUMENT_AGENT_NAME = "Agent Tài liệu"

_secrets: SecretReader | None = None
_gateway: Gateway | None = None
_gateway_read = False
_search: SearchServices | None = None


def dialect_for_source(source_kind: str) -> str:
    """Return the SQL compiler dialect used by a catalog source kind."""
    return dialect_of(source_kind)


def secrets() -> SecretReader:
    global _secrets
    if _secrets is None:
        # Without OpenBao no source can be opened; the run then fails with that said, not a guess.
        _secrets = reader_from_env() or StaticReader({})
    return _secrets


def gateway() -> Gateway | None:
    global _gateway, _gateway_read
    if not _gateway_read:
        _gateway, _gateway_read = gateway_from_env(), True
    return _gateway


def search_services() -> SearchServices:
    global _search
    if _search is None:
        _search = services_from_env()
    return _search


def _internal_workspace_source(data_space_id: uuid.UUID, org_id: uuid.UUID) -> catalog.Source:
    """Represent materialized workspace tables as the local query source."""
    return catalog.Source(
        id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        org_id=org_id,
        data_space_id=data_space_id,
        name="Data Space workspace",
        kind="postgresql",
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("PGPORT", "8300")),
        database_name=os.environ.get("POSTGRES_DB", "dataagent"),
        conn_options={"schema": "workspace"},
        secret_ref=INTERNAL_WORKSPACE,
        mode="federated",
        status="active",
        max_concurrency=4,
        query_timeout_s=120,
    )


@dataclass
class Prepared:
    """What step 1 decided, carried to steps 2 and 3."""

    org_id: uuid.UUID
    space_id: uuid.UUID
    user_id: uuid.UUID
    plan_id: uuid.UUID
    metric_id: uuid.UUID
    metric_name: str
    version: c.MetricVersion
    version_row: context.MetricVersion
    compiled: c.Compiled
    source: catalog.Source
    understanding: Understanding
    dimension_names: dict[str, str]
    datasets: dict[uuid.UUID, catalog.Dataset]
    as_of: datetime.date | None
    knowledge_mode: str
    knowledge_at: datetime.datetime | None
    started: datetime.datetime
    # Codes of the time dimensions: a cut along one is drawn as a line.
    time_dimensions: set[str] = field(default_factory=set)
    # One summed or counted measure: its members add up to a total (answer/insights.py).
    additive: bool = False
    # The cuts the metric allows besides time, (code, name): the chart's "Đi sâu theo" (UC-01-04).
    cuts: list[tuple[str, str]] = field(default_factory=list)
    # Model calls behind this answer, for the receipt (ADR-43: models[], frontier_used).
    models: list[dict[str, Any]] = field(default_factory=list)


def _plan_ir(
    u: Understanding,
    compiled: c.Compiled | None,
    as_of: datetime.date | None,
    explain_by: str | None = None,
    explain_name: str | None = None,
) -> dict[str, Any]:
    """Plan IR v2 (tab 5 G3.1): the fast path's template plan, one semantic query step.

    A "vì sao" question (UC-03-01) also gets a second step that waits for the person: the
    decomposition skill on the same metric and month against the month before, cut by
    `explain_by`. Nothing runs it until they confirm (checkpoint `confirm`); the screen runs it
    through POST /v1/skills/{id}/runs."""
    steps: list[dict[str, Any]] = []
    if compiled is not None and u.metric is not None:
        steps.append(
            {
                "id": "s1",
                "kind": "semantic_query",
                "tool": "query.semantic",
                "args": {
                    "metric_version_id": str(compiled.metric_version_id),
                    "group_by": list(u.group_by),
                    "filters": u.filters,
                    "period": [u.period.start.isoformat(), u.period.end.isoformat()]
                    if u.period
                    else None,
                },
                "depends_on": [],
                "checkpoint": "none",
                "on_error": "fail",
                "explain": f"Tính {u.metric.name}",
            }
        )
        if explain_by is not None and u.period is not None:
            month = u.period.start
            before = (month - datetime.timedelta(days=1)).replace(day=1)
            steps.append(
                {
                    "id": "s2",
                    "kind": "skill",
                    "tool": "skill.run",
                    "args": {
                        "category": "decomposition",
                        "params": {
                            "metric": u.metric.code,
                            "dimension": explain_by,
                            "period": f"{month:%Y-%m}",
                            "base_period": f"{before:%Y-%m}",
                        },
                    },
                    "depends_on": ["s1"],
                    "checkpoint": "confirm",
                    "on_error": "fail",
                    "explain": f"Phân rã biến động {u.metric.name} theo {explain_name or explain_by}"
                    " so với tháng trước",
                }
            )
    return {
        "version": 2,
        "goal": "answer",
        "as_of": as_of.isoformat() if as_of else None,
        "knowledge_mode": "latest",
        "chips": [chip.__dict__ for chip in u.chips],
        "steps": steps,
        "outputs": [{"step": "s1", "as": "table" if u.group_by else "text"}] if steps else [],
        "budget": {"max_steps": 15, "max_tokens": 0, "max_duration": 120},
        "phase": 1,
        "max_phases": 1,
    }


async def _finish(
    question_id: uuid.UUID,
    org_id: uuid.UUID,
    user_id: uuid.UUID | None,
    plan_id: uuid.UUID,
    *,
    kind: str,
    text: str,
    status: str,
    extra: dict[str, Any] | None = None,
) -> uuid.UUID:
    """A refusal or clarification: the answer row and the question's end state."""
    answer_id = uuid7()
    async with scoped_session(ROLE, org_id, user_id) as session:
        question = await session.get(qa.Question, question_id, with_for_update=True)
        session.add(
            qa.Answer(
                id=answer_id,
                org_id=org_id,
                question_id=question_id,
                plan_id=plan_id,
                kind=kind,
                text=text,
                agent_name=AGENT_NAME,
                product_scope="not_applicable",
                **(extra or {}),
            )
        )
        if question is not None:
            question.status = status
            question.answered_at = func.now()
    return answer_id


async def _refuse(
    event: Event,
    question_id: uuid.UUID,
    user_id: uuid.UUID | None,
    plan_id: uuid.UUID,
    refusal: Refusal,
    *,
    status: str = "refused",
) -> None:
    # qa.answer has no refusal-code column: the code goes to the log and the stream's payload.
    answer_id = await _finish(
        question_id,
        event.org_id,
        user_id,
        plan_id,
        kind="refusal",
        text=refusal.message,
        status=status,
    )
    logger.info("question %s %s with %s", question_id, status, refusal.code)
    payload = {"question_id": str(question_id), "answer_id": str(answer_id), "kind": "refusal"}
    await publish(str(question_id), "answer", {**payload, "code": str(refusal.code)})
    await publish(str(question_id), "done", {**payload, "status": status})


def _cut_clarification(
    question: str, u: Understanding, layer: SpaceLayer
) -> tuple[str, list[dict[str, str]]]:
    """Asks which cut a "theo X" meant when X names no dimension (BR-80).

    The options are the cuts the metric's owner allowed (BR-81), each written as a whole question:
    the screen answers a clarification by asking again, so an option must stand on its own.
    """
    assert u.metric is not None and u.unplaced_cut is not None
    words = question.split()
    size = len(u.unplaced_cut.split())
    # The person's own words, accents and all, for the phrase the rule pass could not place.
    asked = next(
        (
            " ".join(words[i : i + size])
            for i in range(len(words) - size + 1)
            if norm(" ".join(words[i : i + size])) == u.unplaced_cut
        ),
        u.unplaced_cut,
    )
    allowed = layer.versions[u.metric.metric_id].allowed_dimension_ids
    period = f" {u.period.label}" if u.period else ""
    choices = [
        {"value": d.code, "label": f"{u.metric.name} theo {d.name}{period}"}
        for d in layer.vocabulary.dimensions
        if d.dimension_id in allowed and not d.is_time
    ]
    if not choices:
        return (
            f"Chưa nhận ra “{asked}” là chiều nào. {u.metric.name} chưa được phép chia theo chiều "
            "nào; người phụ trách chỉ số có thể mở thêm.",
            [],
        )
    listed = "; ".join(f"{i + 1}. {c['label']}" for i, c in enumerate(choices))
    return (
        f"Chưa nhận ra “{asked}” là chiều nào của {u.metric.name}. Bạn muốn hỏi: {listed}? "
        f"Người phụ trách có thể thêm “{asked}” làm từ đồng nghĩa để lần sau hệ thống hiểu ngay.",
        choices,
    )


def _dimension_name(layer: SpaceLayer, code: str | None) -> str | None:
    dim = layer.layer.dimensions.get(code) if code else None
    return dim.name if dim else None


def _explain_cut(u: Understanding, layer: SpaceLayer) -> str | None:
    """The cut a "vì sao" decomposes by: the one asked for, else the metric's first allowed one.
    None when there is no single month to compare with the month before."""
    if u.decision != "proceed" or u.metric is None or u.period is None:
        return None
    start, end = u.period.start, u.period.end
    if start.day != 1 or end != (start + datetime.timedelta(days=32)).replace(day=1):
        return None
    dims = layer.layer.dimensions
    asked = [g for g in u.group_by if g in dims and not dims[g].is_time]
    if asked:
        return asked[0]
    allowed = layer.versions[u.metric.metric_id].allowed_dimension_ids
    cuts = sorted(
        (d for d in dims.values() if d.id in allowed and not d.is_time), key=lambda d: d.name
    )
    return cuts[0].code if cuts else None


def _cut_not_allowed(u: Understanding, layer: SpaceLayer) -> str | None:
    """A cut or filter on a dimension the metric's owner did not allow (BR-81), in words, with
    the allowed ones, those on the same entity first (the nearest cut that can be made)."""
    assert u.metric is not None
    allowed_ids = layer.versions[u.metric.metric_id].allowed_dimension_ids
    dims = layer.layer.dimensions
    bad = [
        dims[code]
        for code in [*u.group_by, *u.filters]
        if code in dims and not dims[code].is_time and dims[code].id not in allowed_ids
    ]
    if not bad:
        return None
    entity = bad[0].entity_id
    allowed = sorted(
        (d for d in dims.values() if d.id in allowed_ids and not d.is_time),
        key=lambda d: (d.entity_id != entity, d.name),
    )
    head = f"{u.metric.name} không chia được theo {', '.join(d.name for d in bad)}."
    if not allowed:
        return f"{head} Chỉ số này chưa được phép chia theo chiều nào."
    return f"{head} Có thể chia theo: {', '.join(d.name for d in allowed)}."


async def _conversation(session: AsyncSession, question: qa.Question) -> list[llm_reader.Turn]:
    """The conversation before this question, oldest first: each question's text and the chips
    of its latest plan (AC-01-04-05)."""
    rows = (
        await session.execute(
            select(qa.Question.id, qa.Question.text)
            .where(
                qa.Question.conversation_id == question.conversation_id,
                qa.Question.seq < question.seq,
            )
            .order_by(qa.Question.seq)
        )
    ).all()
    if not rows:
        return []
    plans = (
        await session.execute(
            select(qa.Plan.question_id, qa.Plan.chips)
            .where(qa.Plan.question_id.in_([r.id for r in rows]))
            .order_by(qa.Plan.version)
        )
    ).all()
    chips = {p.question_id: [c for c in (p.chips or []) if isinstance(c, dict)] for p in plans}
    return [(r.text, chips.get(r.id, [])) for r in rows]


async def _previous_chips(session: AsyncSession, question: qa.Question) -> list[dict[str, object]]:
    """The reading of the question this one follows: its parent, else the one before it in the
    conversation; the chips of its latest plan."""
    earlier = question.parent_question_id or await session.scalar(
        select(qa.Question.id)
        .where(
            qa.Question.conversation_id == question.conversation_id,
            qa.Question.seq < question.seq,
        )
        .order_by(qa.Question.seq.desc())
        .limit(1)
    )
    if earlier is None:
        return []
    chips = await session.scalar(
        select(qa.Plan.chips)
        .where(qa.Plan.question_id == earlier)
        .order_by(qa.Plan.version.desc())
        .limit(1)
    )
    return [c for c in (chips or []) if isinstance(c, dict)]


def _code(code: str) -> ErrorCode:
    try:
        return ErrorCode(code)
    except ValueError:
        return ErrorCode.TOOL_UNAVAILABLE


async def answer_question(event: Event) -> None:
    await _run(event, uuid.UUID(str(event.payload["question_id"])), edited_plan_id=None)


async def answer_edited_plan(event: Event) -> None:
    """UC-01-03: the person corrected the chips; the api wrote the new plan version without steps
    and set the question back to planning. Re-answer from those chips."""
    await _run(
        event,
        uuid.UUID(str(event.payload["question_id"])),
        edited_plan_id=uuid.UUID(str(event.payload["plan_id"])),
    )


async def _run(event: Event, question_id: uuid.UUID, *, edited_plan_id: uuid.UUID | None) -> None:
    await publish(str(question_id), "planning", {"question_id": str(question_id)})
    prepared = await _understand_and_plan(event, question_id, edited_plan_id)
    if prepared is None:
        return
    await publish(
        str(question_id), "progress", {"question_id": str(question_id), "status": "running"}
    )
    outcome = await run_query(
        org_id=prepared.org_id,
        data_space_id=prepared.space_id,
        principal_id=prepared.user_id,
        caller_kind="question",
        caller_id=question_id,
        source=prepared.source,
        sql=prepared.compiled.sql,
        secrets=secrets(),
        metric_version_ids=[prepared.compiled.metric_version_id],
        as_of=prepared.as_of,
    )
    if not outcome.ok:
        name = prepared.source.name
        message = (
            f"Nguồn {name} không trả kết quả trong thời gian cho phép; câu hỏi vẫn mở, bạn có thể thử lại."
            if outcome.status == "timeout"
            else f"Chưa truy vấn được nguồn {name}. Người phụ trách dữ liệu có thể xem lượt chạy trong lịch sử truy vấn."
        )
        await _refuse(
            event,
            question_id,
            prepared.user_id,
            prepared.plan_id,
            Refusal.of(
                ErrorCode.TOOL_UNAVAILABLE,
                message=message,
                next_action=NextAction.RETRY,
                detail_id=str(outcome.query_run_id),
            ),
            status="failed",
        )
        return
    if not outcome.rows:
        await _refuse(
            event,
            question_id,
            prepared.user_id,
            prepared.plan_id,
            Refusal.of(ErrorCode.NO_DATA_FOR_PERIOD, detail_id=str(outcome.query_run_id)),
        )
        return
    await _answer(event, question_id, prepared, outcome)


async def _understand_and_plan(
    event: Event, question_id: uuid.UUID, edited_plan_id: uuid.UUID | None
) -> Prepared | None:
    async with scoped_session(ROLE, event.org_id, event.payload.get("user_id")) as session:
        question = await session.scalar(
            select(qa.Question).where(qa.Question.id == question_id).with_for_update()
        )
        edited: qa.Plan | None = None
        if edited_plan_id is not None and question is not None and question.status == "planning":
            edited = await session.get(qa.Plan, edited_plan_id)
            latest = await session.scalar(
                select(func.max(qa.Plan.version)).where(qa.Plan.question_id == question_id)
            )
            # Only the newest edit, and only once: a later edit or a redelivery finds steps set.
            if edited is None or edited.version != latest or edited.steps:
                edited = None
        if question is None or (question.status != "received" and edited is None):
            logger.info("question %s: nothing to do (already handled or gone)", question_id)
            return None
        conversation = await session.get(qa.Conversation, question.conversation_id)
        assert conversation is not None
        space_id, user_id = conversation.data_space_id, conversation.user_id
        today = question.as_of or datetime.date.today()
        layer = await load(session, space_id)
        u = (
            from_chips(list(edited.chips or []), layer.vocabulary)
            if edited is not None
            else understand(question.text, layer.vocabulary, today=today)
        )
        if edited is None and u.decision == "unknown":
            # UC-01-04: "còn theo vùng thì sao?" reads against the question before it.
            u = follow_up(u, await _previous_chips(session, question), layer.vocabulary)
        models: list[dict[str, Any]] = []
        model = gateway()
        if edited is None and u.decision == "unknown" and model is not None:
            # The rule pass placed no metric: the fast model reads it against this catalogue.
            read = await llm_reader.read(
                question.text,
                layer.vocabulary,
                model,
                today=today,
                history=await _conversation(session, question),
            )
            if read is not None:
                u, reply = read
                models.append(
                    {"task": "understand", "model_name": reply.model, "tokens": reply.tokens}
                )

        compiled: c.Compiled | None = None
        problem: Refusal | None = None
        compile_request: c.Request | None = None
        not_cut = _cut_not_allowed(u, layer) if u.decision == "proceed" else None
        if not_cut is not None:
            # AC-01-04-04: said in words, with the cuts the metric does allow.
            problem = Refusal.of(
                ErrorCode.CONTRACT_MISMATCH, message=not_cut, detail_id=str(question.id)
            )
        elif u.decision == "proceed" and u.metric is not None:
            version = layer.versions[u.metric.metric_id]
            compile_request = c.Request(
                version,
                group_by=u.group_by,
                filters=u.filters,
                period=c.Period(u.period.start, u.period.end) if u.period else None,
            )
            try:
                # Compile once to discover the dataset/source, then regenerate in that source's
                # dialect after the source policy checks below.
                compiled = c.compile_metric(
                    compile_request,
                    layer.layer,
                    dialect="postgres",
                )
            except (c.CompileError, SqlRejected) as exc:
                problem = Refusal.of(
                    _code(getattr(exc, "code", "CONTRACT_MISMATCH")),
                    message=str(exc),
                    detail_id=str(question.id),
                )

        explain_by = (
            _explain_cut(u, layer) if problem is None and _WHY.search(norm(question.text)) else None
        )
        if edited is not None:
            # The person's chips stay as they wrote them; the plan gains its steps.
            plan_id = edited.id
            edited.steps = _plan_ir(
                u, compiled, question.as_of, explain_by, _dimension_name(layer, explain_by)
            )
            edited.confidence = decimal.Decimal(str(round(u.confidence, 4)))
        else:
            plan_id = uuid7()
            version_no = int(
                await session.scalar(
                    select(func.coalesce(func.max(qa.Plan.version), 0) + 1).where(
                        qa.Plan.question_id == question.id
                    )
                )
            )
            session.add(
                qa.Plan(
                    id=plan_id,
                    org_id=event.org_id,
                    question_id=question.id,
                    version=version_no,
                    chips=[chip.__dict__ for chip in u.chips]
                    + [{"kind": "option", "value": m.code, "label": m.name} for m in u.options],
                    steps=_plan_ir(
                        u, compiled, question.as_of, explain_by, _dimension_name(layer, explain_by)
                    ),
                    confidence=decimal.Decimal(str(round(u.confidence, 4))),
                )
            )
        await session.flush()

        source: catalog.Source | None = None
        if compiled is not None:
            dataset = layer.datasets[compiled.dataset_ids[0]]
            source = (
                _internal_workspace_source(space_id, event.org_id)
                if dataset.schema_name == "workspace" and dataset.source_id is None
                else await session.get(catalog.Source, dataset.source_id)
            )
            if source is None or source.mode != "federated" or source.status not in QUERYABLE:
                problem = Refusal.of(
                    ErrorCode.TOOL_UNAVAILABLE,
                    message="Bảng của chỉ số này nằm trên một nguồn chưa được phép truy vấn tại chỗ.",
                    detail_id=str(question.id),
                )
            elif compile_request is not None:
                try:
                    compiled = c.compile_metric(
                        compile_request,
                        layer.layer,
                        dialect=dialect_for_source(source.kind),
                    )
                except (c.CompileError, SqlRejected) as exc:
                    problem = Refusal.of(
                        _code(getattr(exc, "code", "CONTRACT_MISMATCH")),
                        message=str(exc),
                        detail_id=str(question.id),
                    )
        if (
            u.decision == "proceed"
            and problem is None
            and compiled is not None
            and source is not None
        ):
            question.status = "running"
            assert u.metric is not None
            return Prepared(
                org_id=event.org_id,
                space_id=space_id,
                user_id=user_id,
                plan_id=plan_id,
                metric_id=u.metric.metric_id,
                metric_name=u.metric.name,
                version=layer.versions[u.metric.metric_id],
                version_row=layer.version_rows[u.metric.metric_id],
                compiled=compiled,
                source=source,
                understanding=u,
                dimension_names={d.code: d.name for d in layer.vocabulary.dimensions},
                datasets=layer.datasets,
                as_of=question.as_of,
                knowledge_mode=question.knowledge_mode,
                knowledge_at=question.knowledge_at,
                started=datetime.datetime.now(datetime.UTC),
                time_dimensions={d.code for d in layer.vocabulary.dimensions if d.is_time},
                cuts=[
                    (d.code, d.name)
                    for d in layer.vocabulary.dimensions
                    if d.dimension_id in layer.versions[u.metric.metric_id].allowed_dimension_ids
                    and not d.is_time
                ],
                additive=is_additive(
                    layer.versions[u.metric.metric_id].expression,
                    {code: m.expression for code, m in layer.layer.measures.items()},
                ),
                models=models,
            )

    if u.decision == "clarify":
        if u.unplaced_cut is not None and u.metric is not None:
            text, choices = _cut_clarification(question.text, u, layer)
        else:
            listed = "; ".join(f"{i + 1}. {m.name}" for i, m in enumerate(u.options))
            text = f"Câu hỏi khớp với nhiều chỉ số. Bạn muốn hỏi chỉ số nào: {listed}?"
            choices = [{"value": m.code, "label": m.name} for m in u.options]
        answer_id = await _finish(
            question_id,
            event.org_id,
            user_id,
            plan_id,
            kind="clarification",
            status="clarifying",
            text=text,
        )
        await publish(
            str(question_id),
            "clarification",
            {"question_id": str(question_id), "answer_id": str(answer_id), "options": choices},
        )
        await publish(
            str(question_id), "done", {"question_id": str(question_id), "status": "clarifying"}
        )
        return None
    if problem is None and edited is not None:
        problem = Refusal.of(
            ErrorCode.UNRESOLVED_ENTITY,
            message=(
                "Có chip không khớp với chỉ số, chiều hoặc kỳ nào của Data Space: "
                + ", ".join(u.unmapped or ["chưa chọn chỉ số"])
                + ". Hãy chọn lại từ danh sách."
            ),
            next_action=NextAction.REPHRASE,
            detail_id=str(question_id),
        )
    # No metric names the question: the model writes SQL over the Data Space's tables (01 §3.4,
    # EXPLORATORY), then the documents are looked in.
    if (
        problem is None
        and edited is None
        and await answer_by_sql(
            event,
            question_id,
            user_id,
            plan_id,
            models,
            gateway=gateway(),
            secrets=secrets(),
            workspace_source=_internal_workspace_source,
        )
    ):
        return None
    if (
        problem is None
        and edited is None
        and await _answer_from_documents(event, question_id, user_id, plan_id, models)
    ):
        return None
    if problem is None:
        problem = Refusal.of(
            ErrorCode.UNRESOLVED_ENTITY,
            message=(
                "Chưa nhận ra chỉ số nào trong câu hỏi. Hãy gọi tên chỉ số như trong danh mục chỉ số "
                "của Data Space, hoặc nhờ người phụ trách thêm từ đồng nghĩa."
            ),
            next_action=NextAction.REPHRASE,
            detail_id=str(question_id),
        )
    await _refuse(event, question_id, user_id, plan_id, problem)
    return None


def _document_plan(question: qa.Question, as_of: datetime.date) -> dict[str, Any]:
    """Plan IR v2 of a document answer: one retrieval step, its answer written from the passages."""
    return {
        "version": 2,
        "goal": "answer",
        "as_of": as_of.isoformat(),
        "knowledge_mode": question.knowledge_mode,
        "chips": [],
        "steps": [
            {
                "id": "s1",
                "kind": "document_search",
                "tool": "document.search",
                "args": {
                    "query": question.text,
                    "as_of": as_of.isoformat(),
                    "purpose": question.purpose,
                },
                "depends_on": [],
                "checkpoint": "none",
                "on_error": "fail",
                "explain": "Tìm trong tài liệu của Data Space",
            }
        ],
        "outputs": [{"step": "s1", "as": "text"}],
        "budget": {"max_steps": 15, "max_tokens": 0, "max_duration": 120},
        "phase": 1,
        "max_phases": 1,
    }


async def _answer_from_documents(
    event: Event,
    question_id: uuid.UUID,
    user_id: uuid.UUID | None,
    plan_id: uuid.UUID,
    models: list[dict[str, Any]],
) -> bool:
    """UC-05-04 for a question that names no metric. False when the Data Space has no document
    the question could be answered from, so the caller refuses as before."""
    started = datetime.datetime.now(datetime.UTC)
    async with scoped_session(ROLE, event.org_id, user_id) as session:
        question = await session.get(qa.Question, question_id)
        if question is None or question.status != "received":
            return False
        conversation = await session.get(qa.Conversation, question.conversation_id)
        assert conversation is not None
        as_of = question.as_of or datetime.date.today()
        passages = await retrieve(
            session,
            search_services(),
            data_space_id=conversation.data_space_id,
            question=question.text,
            as_of=as_of,
            purpose=question.purpose,
        )
        # Any passage at all in the space: then "not found in the documents" is the true answer;
        # without one the question was never a document question and is refused as before.
        has_documents = (
            bool(passages)
            or (
                await session.scalar(
                    select(document.Chunk.id)
                    .join(
                        document.Collection, document.Collection.id == document.Chunk.collection_id
                    )
                    .where(
                        document.Collection.data_space_id == conversation.data_space_id,
                        document.Collection.deleted_at.is_(None),
                    )
                    .limit(1)
                )
            )
            is not None
        )
        text, plan = question.text, _document_plan(question, as_of)
    if not has_documents:
        return False
    await publish(
        str(question_id), "progress", {"question_id": str(question_id), "status": "running"}
    )
    told = await document_answer.compose(gateway(), text, passages, as_of)
    if told.model:
        models.append({"task": "document_answer", "model_name": told.model, "tokens": told.tokens})
    if told.dropped:
        logger.info(
            "question %s: %d document claims failed their citation check",
            question_id,
            len(told.dropped),
        )
    if not told.found:
        # E1: "not found in the documents you may read" is an answer, not a failure: it is
        # written as one (no citations, so the trust label says there is no evidence) and drawn
        # as plain text, not as a refusal card. The hint covers a question meant for a metric.
        told.text += (
            " Nếu bạn cần số liệu, hãy gọi tên chỉ số như trong danh mục chỉ số của Data Space."
        )
    answer_id = uuid7()
    async with scoped_session(ROLE, event.org_id, user_id) as session:
        question = await session.get(qa.Question, question_id, with_for_update=True)
        if question is None or question.status != "received":
            return True
        stored_plan = await session.get(qa.Plan, plan_id)
        if stored_plan is not None:
            stored_plan.steps = plan
        session.add(
            qa.Answer(
                id=answer_id,
                org_id=event.org_id,
                question_id=question_id,
                plan_id=plan_id,
                kind="document",
                text=told.text,
                citations=[c.as_json() for c in told.citations],
                number_check=told.number_check,
                agent_name=DOCUMENT_AGENT_NAME,
                latency_ms=int(
                    (datetime.datetime.now(datetime.UTC) - started).total_seconds() * 1000
                ),
                lineage_hash=hashlib.sha256(
                    ",".join(sorted(str(c.passage.chunk_id) for c in told.citations)).encode()
                ).hexdigest()
                if told.citations
                else None,
                product_scope="not_applicable",
            )
        )
        question.status = "answered"
        question.answered_at = func.now()
    payload = {"question_id": str(question_id), "answer_id": str(answer_id), "kind": "document"}
    await publish(str(question_id), "answer", {**payload, "citations": len(told.citations)})
    await publish(str(question_id), "done", {**payload, "status": "answered"})
    return True


async def _answer(event: Event, question_id: uuid.UUID, p: Prepared, outcome: Any) -> None:
    u = p.understanding
    columns = [col["name"] for col in outcome.columns]
    figures = figures_of(
        columns,
        outcome.rows,
        metric_name=p.metric_name,
        unit=p.version.unit,
        decimals=p.version.decimals,
        dimension_names=p.dimension_names,
        period_label=u.period.label if u.period else None,
    )
    # The risk team's threshold on this metric, judged once the figures are masked (BR-178).
    threshold = await load_threshold(p.org_id, p.space_id, p.metric_id, p.user_id)
    answer_id = uuid7()
    async with scoped_session(ROLE, p.org_id, p.user_id) as session:
        question = await session.get(qa.Question, question_id, with_for_update=True)
        if question is None or question.status != "running":
            return
        product = await _product_in_scope(session, p)
        trust = (
            OUT_OF_SCOPE if product is None else STANDARD if p.version.certified else NOT_STANDARD
        )
        freshness: list[dict[str, Any]] = []
        state = "not_applicable"
        if product is not None:
            member = await session.scalar(
                select(context.DataProductMember).where(
                    context.DataProductMember.data_product_id == product.id,
                    context.DataProductMember.member_kind == "dataset",
                    context.DataProductMember.member_ref.in_(list(p.compiled.dataset_ids)),
                )
            )
            if member is not None:
                state = {"fresh": "fresh", "overdue": "overdue", "missing": "missing"}.get(
                    member.freshness_state, "not_applicable"
                )
                freshness.append(
                    {
                        "dataset_id": str(member.member_ref),
                        "last_as_of": member.last_as_of.isoformat() if member.last_as_of else None,
                        "expected_as_of": member.expected_as_of.isoformat()
                        if member.expected_as_of
                        else None,
                        "due_at": member.next_due_at.isoformat() if member.next_due_at else None,
                        "state": member.freshness_state,
                    }
                )
            # BR-178: a figure is shown only when its data is in time; the text never states others.
            for f in figures:
                if state == "overdue":
                    f.display_state, f.value = "masked_stale", None
                    f.stale_note = "Dữ liệu quá hạn cập nhật theo cam kết của data product."
                elif state == "missing":
                    f.display_state, f.value = "not_available", None
        filters_label = ", ".join(chip.label for chip in u.chips if chip.kind == "filter") or None
        latest_note = (
            None if u.period else "Không nêu kỳ: số liệu là dữ liệu mới nhất có trong nguồn."
        )
        # What the table and chart show, by arithmetic: each number a figure with its expression.
        ins = insights_of(
            figures,
            group_by=u.group_by,
            time_dimensions=p.time_dimensions,
            metric_name=p.metric_name,
            unit=p.version.unit,
            decimals=p.version.decimals,
            additive=p.additive,
        )
        if threshold is not None:
            against = judged(
                figures,
                threshold,
                metric_name=p.metric_name,
                next_key=len(figures) + len(ins.figures) + 1,
            )
            ins.figures += against.figures
            ins.sentences += against.sentences
        compute_run_id = uuid7() if ins.figures else None
        every = figures + ins.figures
        text = text_of(
            figures,
            metric_name=p.metric_name,
            group_by_names=[p.dimension_names.get(g, g) for g in u.group_by],
            period_label=u.period.label if u.period else None,
            filters_label=filters_label,
            latest_note=latest_note,
            truncated=outcome.truncated,
            insights=written(ins.sentences, every) or None,
        )
        shown = [f for f in every if f.display_state == "shown"]
        check = {
            "numbers_total": len(shown),
            "traced": len(shown),
            "untraceable_draft": 0,
            "regenerated": 0,
            "sentences_dropped": 0,
            "untraceable_final": 0,
        }
        model = gateway()
        if model is not None:
            # G5.15: the main model writes the sentences, numbers only as figure tags, post-checked.
            told = await narrator.narrate(
                model,
                every,
                question=question.text,
                metric_name=p.metric_name,
                exempt_text=[c.label for c in u.chips]
                + [v for f in figures for v in f.dimensions.values()],
                hints=ins.sentences,
            )
            if told is not None:
                notes = [n for n in (latest_note,) if n]
                if outcome.truncated:
                    notes.append("Kết quả có hơn 10.000 dòng; bảng chỉ có 10.000 dòng đầu.")
                text = " ".join([told.text, *notes])
                check.update(
                    numbers_total=told.numbers_total,
                    traced=told.numbers_total,
                    untraceable_draft=told.untraceable_draft,
                    regenerated=told.regenerated,
                    sentences_dropped=told.sentences_dropped,
                )
                p.models.append(
                    {"task": "narrate", "model_name": told.model, "tokens": told.tokens}
                )
        lineage = hashlib.sha256(
            f"{p.compiled.sql_sha256}:{outcome.result_hash}".encode()
        ).hexdigest()
        session.add(
            qa.Answer(
                id=answer_id,
                org_id=p.org_id,
                question_id=question_id,
                plan_id=p.plan_id,
                kind="data",
                text=text,
                figures=[
                    f.as_json(
                        str(outcome.query_run_id),
                        str(p.compiled.metric_version_id),
                        trust,
                        compute_run_id=str(compute_run_id) if compute_run_id else None,
                    )
                    for f in every
                ],
                number_check=check,
                chart_spec=chart_spec_of(
                    figures,
                    group_by=u.group_by,
                    dimension_names=p.dimension_names,
                    time_dimensions=p.time_dimensions,
                    metric_name=p.metric_name,
                    unit=p.version.unit,
                    drill=[cut for cut in p.cuts if cut[0] not in u.group_by],
                ),
                agent_name=AGENT_NAME,
                latency_ms=int(
                    (datetime.datetime.now(datetime.UTC) - p.started).total_seconds() * 1000
                ),
                lineage_hash=lineage,
                data_product_id=product.id if product else None,
                product_scope="in_scope" if product else "out_of_scope",
            )
        )
        await session.flush()
        if product is not None:
            await _seal_receipt(
                session, p, product, answer_id, every, outcome, freshness, state, compute_run_id
            )
        question.status = "answered"
        question.answered_at = func.now()
    payload = {"question_id": str(question_id), "answer_id": str(answer_id), "kind": "data"}
    await publish(str(question_id), "answer", {**payload, "trust": trust})
    await publish(str(question_id), "done", {**payload, "status": "answered"})


async def _product_in_scope(session: AsyncSession, p: Prepared) -> context.DataProduct | None:
    """G5.16, by rule: an active data product of the space that holds this metric, and whose period
    window, when it has one, contains the asked period."""
    products = (
        await session.scalars(
            select(context.DataProduct)
            .join(
                context.DataProductMember,
                context.DataProductMember.data_product_id == context.DataProduct.id,
            )
            .where(
                context.DataProduct.data_space_id == p.space_id,
                context.DataProduct.status == "active",
                context.DataProductMember.member_kind == "metric",
                context.DataProductMember.member_ref == p.metric_id,
            )
        )
    ).all()
    period = p.understanding.period
    for product in products:
        if period is None or (
            (product.period_from is None or period.start >= product.period_from)
            and (
                product.period_to is None
                or period.end <= product.period_to + datetime.timedelta(days=1)
            )
        ):
            return product
    return None


async def _seal_receipt(
    session: Any,
    p: Prepared,
    product: context.DataProduct,
    answer_id: uuid.UUID,
    figures: list[Any],
    outcome: Any,
    freshness: list[dict[str, Any]],
    state: str,
    compute_run_id: uuid.UUID | None = None,
) -> None:
    """ADR-43: the receipt and its figures are written with the answer and sealed before commit;
    a failure here rolls the whole answer back."""
    receipt_id = uuid7()
    session.add(
        qa.AnswerReceipt(
            id=receipt_id,
            org_id=p.org_id,
            answer_id=answer_id,
            data_product_id=product.id,
            data_product_code=product.code,
            data_product_version=product.version,
            plan_id=p.plan_id,
            answered_at=datetime.datetime.now(datetime.UTC),
            knowledge_mode=p.knowledge_mode,
            knowledge_at=p.knowledge_at,
            compiler_version=p.compiled.compiler_version,
            runtime_version=RUNTIME_VERSION,
            models=p.models,  # empty when the answer took no model call
            frontier_used=False,
            freshness=freshness,
        )
    )
    await session.flush()
    v = p.version_row
    certified = (
        v.status == "certified" and v.certified_by is not None and v.certified_at is not None
    )
    for f in figures:
        session.add(
            qa.AnswerReceiptFigure(
                id=uuid7(),
                org_id=p.org_id,
                receipt_id=receipt_id,
                figure_key=f.key,
                label=f.label,
                value=f.value if f.display_state == "shown" else None,
                unit=f.unit,
                display_state=f.display_state,
                stale_note=f.stale_note,
                requested_period=p.understanding.period.label if p.understanding.period else None,
                metric_id=p.metric_id,
                metric_version_id=v.id if certified else None,
                metric_version_no=v.version,
                metric_certified_by=v.certified_by if certified else None,
                metric_certified_at=v.certified_at if certified else None,
                # A derived figure is the in-process computation over the others (G5.10): its
                # expression's hash, so a replay recomputes it rather than reading a row.
                source_kind="compute_run" if f.expr else "query_run",
                query_run_id=None if f.expr else outcome.query_run_id,
                compute_run_id=compute_run_id if f.expr else None,
                expr_sha256=hashlib.sha256(f.expr.encode()).hexdigest() if f.expr else None,
                compiled_sql_sha256=None if f.expr else p.compiled.sql_sha256,
                result_sha256=outcome.result_hash,
                dataset_versions=[
                    {"dataset_id": str(d), "version": p.datasets[d].current_version}
                    for d in p.compiled.dataset_ids
                    if d in p.datasets
                ],
                as_of=p.as_of,
                # A shown figure is fresh or not tracked; a masked one carries why (CHECKs of D4).
                freshness_state=state,
            )
        )
    await session.flush()
    receipt = await session.get(qa.AnswerReceipt, receipt_id)
    assert receipt is not None
    receipt.sealed_at = datetime.datetime.now(datetime.UTC)


HANDLERS: dict[str, Handler] = {
    "question.received": answer_question,
    "plan.edited": answer_edited_plan,
}
