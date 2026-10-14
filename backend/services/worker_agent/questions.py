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

Only single-metric questions take this path; planning several steps with a model, comparisons
and documents are R1 S5. The handler is idempotent: a question past `received` is left alone.
"""

from __future__ import annotations

import datetime
import decimal
import hashlib
import logging
import os
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.contracts.v1.errors import ErrorCode, NextAction, Refusal
from libs.dataagent_db import scoped_session, uuid7
from libs.dataagent_db.models import catalog, context, qa
from services.worker_agent.answer import narrator
from services.worker_agent.answer.narrate import figures_of, text_of
from services.worker_agent.events import Event, Handler
from services.worker_agent.llm import Gateway, gateway_from_env
from services.worker_agent.progress import publish
from services.worker_agent.query.executor import run_query
from services.worker_agent.query.guard import SqlRejected, dialect_of
from services.worker_agent.query.secrets import SecretReader, StaticReader, reader_from_env
from services.worker_agent.semantic import compiler as c
from services.worker_agent.semantic.loader import load
from services.worker_agent.understand import llm_reader
from services.worker_agent.understand.resolver import Understanding, from_chips, understand

logger = logging.getLogger(__name__)

ROLE = "svc_qa"
AGENT_NAME = "Agent Hỏi đáp"
RUNTIME_VERSION = "worker-agent-r1"
QUERYABLE = ("active", "testing")
INTERNAL_WORKSPACE = "internal://workspace"

# Trust labels of tab 5 G5.3 / G5.16: what the answer says about where its numbers come from.
STANDARD = "Theo chỉ số chuẩn"
NOT_STANDARD = "chưa có định nghĩa chuẩn"
OUT_OF_SCOPE = "Ngoài phạm vi data product, không có biên nhận"

_secrets: SecretReader | None = None
_gateway: Gateway | None = None
_gateway_read = False


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
    # Model calls behind this answer, for the receipt (ADR-43: models[], frontier_used).
    models: list[dict[str, Any]] = field(default_factory=list)


def _plan_ir(
    u: Understanding, compiled: c.Compiled | None, as_of: datetime.date | None
) -> dict[str, Any]:
    """Plan IR v2 (tab 5 G3.1): the fast path's template plan, one semantic query step."""
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
        models: list[dict[str, Any]] = []
        model = gateway()
        if edited is None and u.decision == "unknown" and model is not None:
            # The rule pass placed no metric: the fast model reads it against this catalogue.
            read = await llm_reader.read(question.text, layer.vocabulary, model, today=today)
            if read is not None:
                u, reply = read
                models.append(
                    {"task": "understand", "model_name": reply.model, "tokens": reply.tokens}
                )

        compiled: c.Compiled | None = None
        problem: Refusal | None = None
        compile_request: c.Request | None = None
        if u.decision == "proceed" and u.metric is not None:
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

        if edited is not None:
            # The person's chips stay as they wrote them; the plan gains its steps.
            plan_id = edited.id
            edited.steps = _plan_ir(u, compiled, question.as_of)
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
                    steps=_plan_ir(u, compiled, question.as_of),
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
                models=models,
            )

    if u.decision == "clarify":
        options = "; ".join(f"{i + 1}. {m.name}" for i, m in enumerate(u.options))
        answer_id = await _finish(
            question_id,
            event.org_id,
            user_id,
            plan_id,
            kind="clarification",
            status="clarifying",
            text=f"Câu hỏi khớp với nhiều chỉ số. Bạn muốn hỏi chỉ số nào: {options}?",
        )
        await publish(
            str(question_id),
            "clarification",
            {
                "question_id": str(question_id),
                "answer_id": str(answer_id),
                "options": [{"value": m.code, "label": m.name} for m in u.options],
            },
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
        text = text_of(
            figures,
            metric_name=p.metric_name,
            group_by_names=[p.dimension_names.get(g, g) for g in u.group_by],
            period_label=u.period.label if u.period else None,
            filters_label=filters_label,
            latest_note=latest_note,
            truncated=outcome.truncated,
        )
        shown = [f for f in figures if f.display_state == "shown"]
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
                figures,
                question=question.text,
                metric_name=p.metric_name,
                exempt_text=[c.label for c in u.chips]
                + [v for f in figures for v in f.dimensions.values()],
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
                    f.as_json(str(outcome.query_run_id), str(p.compiled.metric_version_id), trust)
                    for f in figures
                ],
                number_check=check,
                chart_spec={"type": "bar", "x": u.group_by[0], "y": "value"}
                if u.group_by
                else None,
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
            await _seal_receipt(session, p, product, answer_id, figures, outcome, freshness, state)
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
                source_kind="query_run",
                query_run_id=outcome.query_run_id,
                compiled_sql_sha256=p.compiled.sql_sha256,
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
