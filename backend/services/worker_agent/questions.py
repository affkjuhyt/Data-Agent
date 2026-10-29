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
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.contracts.v1.errors import ErrorCode, NextAction, Refusal
from libs.dataagent_db import scoped_session, uuid7
from libs.dataagent_db.models import catalog, context, document, period, qa
from libs.sql_guard import SqlRejected, dialect_of
from services.worker_agent.answer import narrator
from services.worker_agent.answer.comparison import (
    PERIOD_CODE,
    PERIOD_NAME,
    comparison_figures,
    comparison_insights,
    period_label,
)
from services.worker_agent.answer.insights import insights_of, is_additive, written
from services.worker_agent.answer.narrate import Figure, chart_spec_of, figures_of, text_of
from services.worker_agent.answer.thresholds import judged
from services.worker_agent.answer.thresholds import load as load_threshold
from services.worker_agent.answer.verification import verify
from services.worker_agent.data_scope import DataScope
from services.worker_agent.documents import answer as document_answer
from services.worker_agent.documents.retrieve import retrieve
from services.worker_agent.documents.search import SearchServices, services_from_env
from services.worker_agent.events import Event, Handler
from services.worker_agent.llm import Gateway, gateway_from_env
from services.worker_agent.progress import publish
from services.worker_agent.query.executor import record_period_snapshot, run_query
from services.worker_agent.query.scope import (
    OutOfScope,
    active_policies,
    active_row_scopes,
    can_ask_data_space,
)
from services.worker_agent.query.secrets import SecretReader, StaticReader, reader_from_env
from services.worker_agent.semantic import analysis, compare
from services.worker_agent.semantic import compiler as c
from services.worker_agent.semantic.loader import SpaceLayer, load
from services.worker_agent.sqlgen.answer import answer_by_sql
from services.worker_agent.understand import llm_reader
from services.worker_agent.understand.analysis import TO_DATE, find_analysis
from services.worker_agent.understand.comparison import find_comparison
from services.worker_agent.understand.grain import GRAIN_LABELS, find_grain
from services.worker_agent.understand.resolver import (
    Understanding,
    follow_up,
    from_chips,
    understand,
)
from services.worker_agent.understand.text import FoundPeriod, find_knowledge_hint, norm

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
    # R1-06: the base period's label when the question compares ("so với tháng trước").
    base_label: str | None = None
    # Model calls behind this answer, for the receipt (ADR-43: models[], frontier_used).
    models: list[dict[str, Any]] = field(default_factory=list)
    period_close_id: uuid.UUID | None = None
    period_rows: list[list[Any]] | None = None
    period_result_ids: list[uuid.UUID] | None = None
    filed_period_label: str | None = None
    filed_rows: list[dict[str, Any]] | None = None
    # The analysis the answer shows (semantic/analysis.py): share_of_total or running_total.
    analysis: str | None = None


def _plan_ir(
    u: Understanding,
    compiled: c.Compiled | None,
    as_of: datetime.date | None,
    explain_by: str | None = None,
    explain_name: str | None = None,
    compare_to: str | None = None,
    grain: str | None = None,
    knowledge_mode: str = "latest",
    analysis_args: dict[str, Any] | None = None,
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
                    "top_n": u.top_n,
                    "top_desc": u.top_desc,
                },
                "depends_on": [],
                "checkpoint": "none",
                "on_error": "fail",
                "explain": f"Tính {u.metric.name}",
            }
        )
        if compare_to is not None:
            steps[0]["args"]["compare_to"] = compare_to
        if grain is not None:
            steps[0]["args"]["grain"] = grain
        if analysis_args is not None:
            steps[0]["args"]["analysis"] = analysis_args
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
        "knowledge_mode": knowledge_mode,
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
    # qa.answer has no refusal-code column, and a refusal states no number, so its number_check
    # (the answer's check state, served as AnswerView.number_check) holds the code and what the
    # person can do about it: the Ask screen offers retry or rephrase from it (R0-7), on reload too.
    answer_id = await _finish(
        question_id,
        event.org_id,
        user_id,
        plan_id,
        kind="refusal",
        text=refusal.message,
        status=status,
        extra={
            "number_check": {
                "refusal": {"code": str(refusal.code), "next_action": str(refusal.next_action)}
            }
        },
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


def _compiled(
    request: c.Request,
    layer: SpaceLayer,
    compare_to: str | None,
    dialect: str,
    shown: tuple[str, str | None] | None = None,
) -> c.Compiled:
    """The question's statement; with a comparison, both periods in one (semantic/compare.py);
    with an analysis (`shown`: its kind and restart), the share or the accumulation beside the
    value (semantic/analysis.py)."""
    if shown is not None:
        if compare_to is not None:
            raise c.CompileError(
                "CONTRACT_MISMATCH",
                "Chưa so sánh hai kỳ cho tỷ trọng hoặc luỹ kế; hãy hỏi riêng từng kỳ.",
            )
        kind, restart = shown
        if kind == analysis.SHARE_OF_TOTAL:
            return analysis.compile_share(request, layer.layer, dialect=dialect)
        return analysis.compile_running(request, layer.layer, dialect=dialect, restart=restart)
    if compare_to is None:
        return c.compile_metric(request, layer.layer, dialect=dialect)
    return compare.compile_comparison(request, layer.layer, compare_to, dialect=dialect)[0]


_ANALYSIS_LABEL = {
    analysis.SHARE_OF_TOTAL: "tỷ trọng trên tổng",
    analysis.RUNNING_TOTAL: "luỹ kế",
    TO_DATE: "luỹ kế từ đầu {unit}",
}
_UNIT_LABEL = {"year": "năm", "quarter": "quý", "month": "tháng"}


def _analysis_args(kind: str | None, unit: str | None) -> dict[str, Any] | None:
    return None if kind is None else {"kind": kind, "unit": unit}


def _analysis_chip(kind: str, unit: str | None) -> dict[str, object]:
    """The analysis as a plan chip; an edited plan keeps it (from_chips ignores the kind)."""
    return {
        "kind": "analysis",
        "value": kind,
        "unit": unit,
        "label": _ANALYSIS_LABEL[kind].format(unit=_UNIT_LABEL.get(unit or "", "năm")),
        "source": "default",
        "confidence": 1.0,
    }


async def _filed_restricted(
    session: AsyncSession,
    layer: SpaceLayer,
    compiled: c.Compiled | None,
    space_id: uuid.UUID,
    user_id: uuid.UUID,
) -> bool:
    """Whether the asker may see less than the whole: a filed figure is the period's total,
    computed when it was filed, so no row scope or access policy of the asker can be applied to
    it (the security review of filed answers). A scoped member, a policy that applies to them, or
    no ask grant at all refuses the filed figure; the live path filters as usual."""
    if compiled is None:
        return True
    try:
        scopes = await active_row_scopes(session, data_space_id=space_id, principal_id=user_id)
        if scopes is not None:
            return True
        datasets = [layer.datasets[d] for d in compiled.dataset_ids if d in layer.datasets]
        policies = await active_policies(
            session, principal_id=user_id, datasets=datasets, caller_kind="question"
        )
    except OutOfScope:
        return True
    return bool(policies)


def _compare_chip(compare_to: str, period: c.Period | None) -> dict[str, object]:
    """The comparison as a plan chip; an edited plan keeps it (from_chips ignores the kind)."""
    compared_to = (
        period_label(compare.base_period(period, compare_to))
        if period is not None
        else {"previous_period": "kỳ trước", "same_period_last_year": "cùng kỳ năm trước"}[
            compare_to
        ]
    )
    return {
        "kind": "compare",
        "value": compare_to,
        "label": f"so với {compared_to}",
        "source": "default",
        "confidence": 1.0,
    }


def _grain_chip(grain: str) -> dict[str, object]:
    """The grain of a time cut as a plan chip; an edited plan keeps it (from_chips ignores it)."""
    return {
        "kind": "grain",
        "value": grain,
        "label": f"theo {GRAIN_LABELS[grain]}",
        "source": "default",
        "confidence": 1.0,
    }


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
    outcome = (
        await run_query(
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
        if prepared.period_close_id is None
        else await record_period_snapshot(
            org_id=prepared.org_id,
            data_space_id=prepared.space_id,
            principal_id=prepared.user_id,
            caller_id=question_id,
            period_close_id=prepared.period_close_id,
            metric_version_ids=[prepared.compiled.metric_version_id],
            columns=[
                {"name": name, "type": "numeric" if name == "value" else "text"}
                for name in prepared.compiled.columns
            ],
            rows=prepared.period_rows or [],
        )
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
    if not outcome.rows or _no_value(prepared.compiled.columns, outcome.rows):
        await _refuse(
            event,
            question_id,
            prepared.user_id,
            prepared.plan_id,
            Refusal.of(ErrorCode.NO_DATA_FOR_PERIOD, detail_id=str(outcome.query_run_id)),
        )
        return
    await _answer(event, question_id, prepared, outcome)


async def _within(
    scope: DataScope, layer: SpaceLayer, dataset_ids: Sequence[uuid.UUID], session: AsyncSession
) -> bool:
    """Every table the metric reads, and its source, is inside an agent version's data_scope."""
    if scope.tables is None and scope.sources is None:
        return True
    for dataset_id in dataset_ids:
        dataset = layer.datasets[dataset_id]
        source = await session.get(catalog.Source, dataset.source_id) if dataset.source_id else None
        if not scope.allows(dataset, source):
            return False
    return True


def _no_value(columns: Sequence[str], rows: Sequence[Sequence[Any]]) -> bool:
    """An aggregate over a period with no data still returns a row, its value NULL (SUM over no
    rows): that is no data for the period, refused as such, not an answer saying "CHƯA CÓ"."""
    if "value" not in columns:
        return False
    at = list(columns).index("value")
    return all(len(row) > at and row[at] is None for row in rows)


async def _record_unmapped(
    event: Event, data_space_id: uuid.UUID, question_text: str, phrases: list[str]
) -> None:
    """Write review evidence through the context owner role, not the QA read role."""
    async with scoped_session("svc_context", event.org_id, event.payload.get("user_id")) as session:
        for phrase in dict.fromkeys(phrases):
            phrase_norm = norm(phrase)
            if not phrase_norm:
                continue
            unmapped = await session.scalar(
                select(context.UnmappedPhrase)
                .where(
                    context.UnmappedPhrase.org_id == event.org_id,
                    context.UnmappedPhrase.data_space_id == data_space_id,
                    context.UnmappedPhrase.phrase_norm == phrase_norm,
                    context.UnmappedPhrase.status == "open",
                )
                .with_for_update()
            )
            if unmapped is None:
                session.add(
                    context.UnmappedPhrase(
                        id=uuid7(),
                        org_id=event.org_id,
                        data_space_id=data_space_id,
                        phrase_norm=phrase_norm,
                        sample_questions=[question_text],
                        ask_count=1,
                    )
                )
            else:
                unmapped.ask_count += 1
                samples = list(unmapped.sample_questions or [])
                if question_text not in samples and len(samples) < 5:
                    samples.append(question_text)
                unmapped.sample_questions = samples
                unmapped.last_seen_at = datetime.datetime.now(datetime.UTC)


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
        knowledge_hint = find_knowledge_hint(question.text)
        if knowledge_hint is not None and question.knowledge_mode == "latest":
            # The explicit API field wins.  Only infer from prose when the caller left the
            # default in place; this prevents a UI chip from being overwritten by text.
            question.knowledge_mode = knowledge_hint.mode
        today = question.as_of or datetime.date.today()
        layer = await load(session, space_id)
        # Answering for an agent version's eval run (agent_evals.py): what its config lets it
        # read, as its runs would (runs.py); a person's question carries no scope.
        scope = DataScope.of(event.payload.get("data_scope"))
        layer.vocabulary = scope.narrow(layer.vocabulary)
        space_access_problem = None
        if not await can_ask_data_space(session, data_space_id=space_id, principal_id=user_id):
            space_access_problem = Refusal.of(
                ErrorCode.ACCESS_DENIED,
                message="Bạn không thuộc Data Space hoặc chưa được cấp quyền hỏi dữ liệu.",
                detail_id=str(question.id),
            )
        # R1-06: "so với tháng trước" is read first and taken out of the text, or "tháng trước"
        # would be read as the period asked.
        found = find_comparison(question.text) if edited is None else None
        asked = found.rest if found is not None else question.text
        if knowledge_hint is not None:
            asked = re.sub(rf"\b{re.escape(knowledge_hint.span)}\b", " ", norm(asked), count=1)
        compare_to = (
            found.compare_to
            if found is not None
            else next(
                (
                    str(chip.get("value"))
                    for chip in (edited.chips or [] if edited is not None else [])
                    if chip.get("kind") == "compare" and chip.get("value") in compare.COMPARE_TO
                ),
                None,
            )
        )
        # R1-06: "theo tháng" names the grain of a cut along time, taken out like a comparison.
        grain_found = find_grain(asked) if edited is None else None
        if grain_found is not None:
            asked = grain_found.rest
        grain = (
            grain_found.grain
            if grain_found is not None
            else next(
                (
                    str(chip.get("value"))
                    for chip in (edited.chips or [] if edited is not None else [])
                    if chip.get("kind") == "grain" and chip.get("value") in c.GRAINS
                ),
                None,
            )
        )
        # The Holistics review (19/10/2026): "tỷ trọng", "luỹ kế", "từ đầu năm" ask for an
        # analysis over the metric, taken out like a grain, or "từ đầu năm" would be a period.
        analysis_found = find_analysis(asked) if edited is None else None
        if analysis_found is not None:
            asked = analysis_found.rest
        analysis_chip = next(
            (
                chip
                for chip in (edited.chips or [] if edited is not None else [])
                if chip.get("kind") == "analysis"
            ),
            None,
        )
        analysis_kind, analysis_unit = (
            (analysis_found.kind, analysis_found.unit)
            if analysis_found is not None
            else (str(analysis_chip.get("value")), analysis_chip.get("unit"))
            if analysis_chip is not None
            else (None, None)
        )
        u = (
            from_chips(list(edited.chips or []), layer.vocabulary)
            if edited is not None
            else understand(asked, layer.vocabulary, today=today)
        )
        # AC-07-09-02 / BR-80: keep a bounded, tenant- and Data-Space-scoped trail of phrases
        # the rule pass could not place. This is context data for steward review, never prompt
        # context; the context owner role writes it because QA is intentionally read-only there.
        if edited is None and u.unmapped:
            await _record_unmapped(event, space_id, question.text, u.unmapped)
        if edited is None and u.decision == "unknown":
            # UC-01-04: "còn theo vùng thì sao?" reads against the question before it.
            u = follow_up(
                u, await _previous_chips(session, question), layer.vocabulary, question=asked
            )
        access_problem: Refusal | None = space_access_problem
        if u.metric is not None and u.metric.metric_id in layer.blocked_metric_ids:
            access_problem = Refusal.of(
                ErrorCode.ACCESS_DENIED,
                message="Bạn không có quyền xem dữ liệu cần cho câu hỏi này.",
                detail_id=str(question.id),
            )
        models: list[dict[str, Any]] = []
        model = gateway()
        if (
            access_problem is None
            and edited is None
            and u.decision == "unknown"
            and model is not None
        ):
            # The rule pass placed no metric: the fast model reads it against this catalogue.
            read = await llm_reader.read(
                asked,
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
        problem: Refusal | None = access_problem
        compile_request: c.Request | None = None
        if grain is not None and u.decision == "proceed" and u.metric is not None:
            # The cut is along the metric's own time; a metric without one is not cut by month
            # silently into a total (BR-80).
            try:
                time_cut = c.time_dimension_of(layer.versions[u.metric.metric_id], layer.layer)
            except c.CompileError:
                time_cut = None
            if time_cut is None:
                problem = Refusal.of(
                    ErrorCode.CONTRACT_MISMATCH,
                    message=f"{u.metric.name} không có chiều thời gian để chia theo "
                    f"{GRAIN_LABELS[grain]}.",
                    detail_id=str(question.id),
                )
            elif time_cut.code not in u.group_by:
                u.group_by.append(time_cut.code)
        # What the analysis asked is, once the cut is known: a share; an accumulation along the
        # time cut (restarted each year for a to-date one); without a time cut, "luỹ kế" or
        # "từ đầu năm" is one period from the start of the year (or quarter, month) to its end.
        shown: tuple[str, str | None] | None = None
        if (
            analysis_kind is not None
            and problem is None
            and u.decision == "proceed"
            and u.metric is not None
        ):
            times = {d.code for d in layer.vocabulary.dimensions if d.is_time}
            if question.knowledge_mode == "period_close":
                problem = Refusal.of(
                    ErrorCode.CONTRACT_MISMATCH,
                    message="Chưa tính tỷ trọng hoặc luỹ kế trên số đã nộp; hãy hỏi theo dữ liệu"
                    " hiện tại.",
                    detail_id=str(question.id),
                )
            elif analysis_kind == analysis.SHARE_OF_TOTAL:
                weak = analysis_found is not None and analysis_found.weak
                if not weak or analysis.is_additive(
                    layer.versions[u.metric.metric_id], layer.layer
                ):
                    shown = (analysis_kind, None)
                else:
                    # "chiếm bao nhiêu %" of a ratio is the ratio itself, answered as asked.
                    analysis_kind = None
            elif any(code in times for code in u.group_by):
                shown = (
                    analysis.RUNNING_TOTAL,
                    analysis_unit if analysis_kind == TO_DATE else None,
                )
            else:
                unit = analysis_unit or "year"
                asked_period = u.period or FoundPeriod(
                    today, today + datetime.timedelta(days=1), "hôm nay", ""
                )
                try:
                    whole = analysis.to_date(
                        c.Period(asked_period.start, asked_period.end),
                        unit,
                        layer.versions[u.metric.metric_id],
                    )
                except c.CompileError as exc:
                    problem = Refusal.of(
                        _code(exc.code), message=str(exc), detail_id=str(question.id)
                    )
                else:
                    since = f"từ đầu {_UNIT_LABEL[unit]}"
                    u.period = replace(
                        asked_period,
                        start=whole.start,
                        label=f"{since} đến hết {asked_period.label}"
                        if u.period
                        else f"{since} đến nay",
                    )
        not_cut = (
            _cut_not_allowed(u, layer) if u.decision == "proceed" and problem is None else None
        )
        if not_cut is not None:
            # AC-01-04-04: said in words, with the cuts the metric does allow.
            problem = Refusal.of(
                ErrorCode.CONTRACT_MISMATCH, message=not_cut, detail_id=str(question.id)
            )
        elif problem is None and u.decision == "proceed" and u.metric is not None:
            version = layer.versions[u.metric.metric_id]
            compile_request = c.Request(
                version,
                group_by=u.group_by,
                filters=u.filters,
                period=c.Period(u.period.start, u.period.end) if u.period else None,
                grain=grain,
                top_n=u.top_n,
                top_desc=u.top_desc,
                knowledge_at=question.knowledge_at if question.knowledge_mode == "at" else None,
            )
            try:
                # Compile once to discover the dataset/source, then regenerate in that source's
                # dialect after the source policy checks below.
                compiled = _compiled(compile_request, layer, compare_to, "postgres", shown)
            except (c.CompileError, SqlRejected) as exc:
                problem = Refusal.of(
                    _code(getattr(exc, "code", "CONTRACT_MISMATCH")),
                    message=str(exc),
                    detail_id=str(question.id),
                )

        explain_by = (
            _explain_cut(u, layer) if problem is None and _WHY.search(norm(question.text)) else None
        )
        period_close_id: uuid.UUID | None = None
        period_rows: list[list[Any]] | None = None
        period_result_ids: list[uuid.UUID] | None = None
        filed_period_label: str | None = None
        filed_rows: list[dict[str, Any]] | None = None
        if problem is None and question.knowledge_mode == "period_close":
            if u.period is None:
                problem = Refusal.of(
                    ErrorCode.NO_DATA_FOR_PERIOD,
                    message="Hỏi theo số đã nộp cần nêu rõ kỳ chốt.",
                    detail_id=str(question.id),
                )
            else:
                label = f"{u.period.start:%Y-%m}"
                closed = await session.scalar(
                    select(period.PeriodClose)
                    .where(
                        period.PeriodClose.org_id == event.org_id,
                        period.PeriodClose.data_space_id == space_id,
                        period.PeriodClose.period_label == label,
                        period.PeriodClose.status.in_(("closed", "filed")),
                    )
                    .order_by(period.PeriodClose.version.desc())
                    .limit(1)
                )
                if closed is None:
                    problem = Refusal.of(
                        ErrorCode.NO_DATA_FOR_PERIOD,
                        message=f"Không có kỳ đã chốt cho {label}; không trả số theo dữ liệu hiện tại.",
                        detail_id=str(question.id),
                    )
                elif await _filed_restricted(session, layer, compiled, space_id, user_id):
                    problem = Refusal.of(
                        ErrorCode.ACCESS_DENIED,
                        message="Số đã nộp là số toàn kỳ, không lọc được theo phạm vi dữ liệu"
                        " của bạn; hãy hỏi theo dữ liệu hiện tại.",
                        detail_id=str(question.id),
                    )
                else:
                    question.as_of = closed.business_date
                    published = (
                        await session.scalars(
                            select(period.PublishedResult)
                            .where(
                                period.PublishedResult.org_id == event.org_id,
                                period.PublishedResult.period_close_id == closed.id,
                                period.PublishedResult.metric_version_id
                                == compiled.metric_version_id,
                            )
                            .order_by(period.PublishedResult.id)
                        )
                    ).all()
                    if not published:
                        problem = Refusal.of(
                            ErrorCode.NO_DATA_FOR_PERIOD,
                            message=f"Kỳ {label} chưa có số đã nộp cho chỉ số này.",
                            detail_id=str(question.id),
                        )
                    else:
                        period_close_id = closed.id
                        period_rows = [
                            [
                                *[
                                    (row.dimension_key or {}).get(column)
                                    for column in compiled.columns
                                    if column != "value"
                                ],
                                str(row.value),
                            ]
                            for row in published
                        ]
                        period_result_ids = [row.id for row in published]
        elif problem is None and question.knowledge_mode == "latest" and u.period is not None:
            # BR-39 / AC-10-01-02: a dated question without an explicit mode compares the
            # live result with the filed result when the period has one. Missing filed history
            # is not an error: the answer remains explicitly current-data only.
            label = f"{u.period.start:%Y-%m}"
            closed = await session.scalar(
                select(period.PeriodClose)
                .where(
                    period.PeriodClose.org_id == event.org_id,
                    period.PeriodClose.data_space_id == space_id,
                    period.PeriodClose.period_label == label,
                    period.PeriodClose.status.in_(("closed", "filed")),
                )
                .order_by(period.PeriodClose.version.desc())
                .limit(1)
            )
            if closed is not None:
                published = (
                    await session.scalars(
                        select(period.PublishedResult)
                        .where(
                            period.PublishedResult.org_id == event.org_id,
                            period.PublishedResult.period_close_id == closed.id,
                            period.PublishedResult.metric_version_id == compiled.metric_version_id,
                        )
                        .order_by(period.PublishedResult.id)
                    )
                ).all()
                if published:
                    filed_period_label = label
                    filed_rows = [
                        {
                            "id": row.id,
                            "value": row.value,
                            "dimensions": row.dimension_key or {},
                        }
                        for row in published
                    ]
        if edited is not None:
            # The person's chips stay as they wrote them; the plan gains its steps.
            plan_id = edited.id
            edited.steps = _plan_ir(
                u,
                compiled,
                question.as_of,
                explain_by,
                _dimension_name(layer, explain_by),
                compare_to,
                grain,
                question.knowledge_mode,
                _analysis_args(analysis_kind, analysis_unit),
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
                    + [{"kind": "option", "value": m.code, "label": m.name} for m in u.options]
                    + (
                        [
                            _compare_chip(
                                compare_to, compile_request.period if compile_request else None
                            )
                        ]
                        if compare_to is not None
                        else []
                    )
                    + ([_grain_chip(grain)] if grain is not None else [])
                    + (
                        [_analysis_chip(analysis_kind, analysis_unit)]
                        if analysis_kind is not None
                        else []
                    ),
                    steps=_plan_ir(
                        u,
                        compiled,
                        question.as_of,
                        explain_by,
                        _dimension_name(layer, explain_by),
                        compare_to,
                        grain,
                        question.knowledge_mode,
                        _analysis_args(analysis_kind, analysis_unit),
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
            elif not await _within(scope, layer, compiled.dataset_ids, session):
                problem = Refusal.of(
                    ErrorCode.ACCESS_DENIED,
                    message="Chỉ số này đọc một bảng nằm ngoài phạm vi dữ liệu của agent.",
                    detail_id=str(question.id),
                )
            elif compile_request is not None:
                try:
                    compiled = _compiled(
                        compile_request, layer, compare_to, dialect_for_source(source.kind), shown
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
                base_label=period_label(compare.base_period(compile_request.period, compare_to))
                if compare_to is not None
                and compile_request is not None
                and compile_request.period is not None
                else None,
                models=models,
                period_close_id=period_close_id,
                period_rows=period_rows,
                period_result_ids=period_result_ids,
                filed_period_label=filed_period_label,
                filed_rows=filed_rows,
                analysis=shown[0] if shown is not None else None,
            )

    if u.decision == "clarify":
        if u.unplaced_cut is not None and u.metric is not None:
            text, choices = _cut_clarification(question.text, u, layer)
        else:
            listed = "; ".join(f"{i + 1}. {m.name}" for i, m in enumerate(u.options))
            text = (
                f"Data Space không có chỉ số nào khớp đúng câu hỏi. Bạn muốn hỏi: {listed}?"
                if len(u.options) == 1
                else f"Câu hỏi khớp với nhiều chỉ số. Bạn muốn hỏi chỉ số nào: {listed}?"
            )
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
                number_check=verify(
                    "document",
                    told.number_check,
                    citations=[c.as_json() for c in told.citations],
                ),
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
    # A compared answer (R1-06): two figures a row, told apart by the period they read.
    compared_to = p.base_label if u.period is not None else None
    shown_by = [*u.group_by, PERIOD_CODE] if compared_to else list(u.group_by)
    names = {**p.dimension_names, PERIOD_CODE: PERIOD_NAME}
    # An analysed answer shows its own column (semantic/analysis.py): the share in percent, or the
    # accumulated value in the metric's unit; the plain value stays in the table beside it.
    metric_name, unit, decimals, value_column = (
        (f"Tỷ trọng {p.metric_name}", "%", 1, "share")
        if p.analysis == analysis.SHARE_OF_TOTAL
        else (f"{p.metric_name} luỹ kế", p.version.unit, p.version.decimals, "cumulative")
        if p.analysis == analysis.RUNNING_TOTAL
        else (p.metric_name, p.version.unit, p.version.decimals, "value")
    )
    if compared_to and u.period is not None:
        figures = comparison_figures(
            columns,
            outcome.rows,
            metric_name=p.metric_name,
            unit=p.version.unit,
            decimals=p.version.decimals,
            dimension_names=p.dimension_names,
            period_label=u.period.label,
            base_label=compared_to,
        )
    else:
        figures = figures_of(
            columns,
            outcome.rows,
            metric_name=metric_name,
            unit=unit,
            decimals=decimals,
            dimension_names=p.dimension_names,
            period_label=u.period.label if u.period else None,
            value_column=value_column,
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
            "Số này được tính trên dữ liệu hiện tại; không có bản chốt đã nộp cho kỳ được hỏi."
            if u.period and not p.filed_rows
            else None
            if u.period
            else "Không nêu kỳ: số liệu là dữ liệu mới nhất có trong nguồn."
        )
        # What the table and chart show, by arithmetic: each number a figure with its expression.
        ins = (
            comparison_insights(
                figures,
                group_by=u.group_by,
                metric_name=p.metric_name,
                unit=p.version.unit,
                decimals=p.version.decimals,
                period_label=u.period.label,
                base_label=compared_to,
            )
            if compared_to and u.period is not None
            else insights_of(
                figures,
                group_by=u.group_by,
                time_dimensions=p.time_dimensions,
                metric_name=metric_name,
                unit=unit,
                decimals=decimals,
                # Shares add up to 100% and accumulations repeat earlier months: no total.
                additive=p.additive and u.top_n is None and p.analysis is None,
            )
        )
        # The threshold is on the metric's own values, not on a share or an accumulation of them.
        if threshold is not None and p.analysis is None:
            against = judged(
                figures,
                threshold,
                metric_name=p.metric_name,
                next_key=len(figures) + len(ins.figures) + 1,
            )
            ins.figures += against.figures
            ins.sentences += against.sentences
        filed_figures: list[Figure] = []
        filed_items: list[dict[str, str]] = []
        if p.filed_rows:
            by_dimensions = {tuple(sorted(f.dimensions.items())): f for f in figures}
            for row in p.filed_rows:
                dimensions = {str(k): str(v) for k, v in row["dimensions"].items()}
                current = by_dimensions.get(tuple(sorted(dimensions.items())))
                if current is None and not dimensions and len(figures) == 1:
                    current = figures[0]
                if current is None or current.value is None:
                    continue
                filed_key = f"F{len(figures) + len(ins.figures) + len(filed_figures) + 1}"
                filed_value = decimal.Decimal(str(row["value"]))
                filed_figures.append(
                    Figure(
                        filed_key,
                        f"{p.metric_name} — đã nộp {p.filed_period_label}",
                        filed_value,
                        p.version.unit,
                        p.version.decimals,
                        len(filed_figures),
                        "value",
                        dimensions=dimensions,
                        published_result_id=str(row["id"]),
                        comparison_only=True,
                    )
                )
                delta_key = f"F{len(figures) + len(ins.figures) + len(filed_figures) + 1}"
                ins.figures.append(
                    Figure(
                        delta_key,
                        f"Chênh lệch hiện tại so với đã nộp {p.filed_period_label}",
                        current.value - filed_value,
                        p.version.unit,
                        p.version.decimals,
                        -1,
                        "value",
                        dimensions=dimensions,
                        expr=f"{current.key} - {filed_key}",
                        signed=True,
                        comparison_only=True,
                    )
                )
                filed_items.append(
                    {
                        "current": str(current.value),
                        "filed": str(filed_value),
                        "delta": str(current.value - filed_value),
                        "published_result_id": str(row["id"]),
                    }
                )
                ins.sentences.append(
                    f"Số hiện tại tại mốc dữ liệu được truy vấn là {{{{{current.key}}}}}, "
                    f"số đã nộp của kỳ {p.filed_period_label} là {{{{{filed_key}}}}}; "
                    f"chênh lệch hiện tại so với đã nộp là {{{{{delta_key}}}}}."
                )
        compute_run_id = uuid7() if ins.figures else None
        every = figures + ins.figures
        every.extend(filed_figures)
        text = text_of(
            figures,
            metric_name=metric_name,
            group_by_names=[names.get(g, g) for g in shown_by],
            period_label=(
                f"{u.period.label} so với {compared_to}"
                if compared_to and u.period
                else u.period.label
                if u.period
                else None
            ),
            filters_label=filters_label,
            latest_note=latest_note,
            truncated=outcome.truncated,
            top_n=u.top_n,
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
                metric_name=metric_name,
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
        governance_tables = [
            {
                "table": f"{dataset.schema_name}.{dataset.table_name}",
                "classification": "internal",
                "last_crawled_at": dataset.updated_at.isoformat() if dataset.updated_at else None,
            }
            for dataset_id, dataset in p.datasets.items()
            if dataset_id in p.compiled.dataset_ids
        ]
        answer_provenance = {
            "status": state if state in {"fresh", "overdue", "missing"} else "unknown",
            "classification": "internal",
            "tables": governance_tables,
        }
        stored_figures = [
            f.as_json(
                str(outcome.query_run_id),
                str(p.compiled.metric_version_id),
                trust,
                compute_run_id=str(compute_run_id) if compute_run_id else None,
                provenance=answer_provenance,
            )
            for f in every
        ]
        badge = (
            "matched_filed"
            if filed_items and all(decimal.Decimal(item["delta"]) == 0 for item in filed_items)
            else "differs_from_filed"
            if filed_items
            else None
        )
        session.add(
            qa.Answer(
                id=answer_id,
                org_id=p.org_id,
                question_id=question_id,
                plan_id=p.plan_id,
                kind="data",
                text=text,
                figures=stored_figures,
                filed_vs_current=(
                    {"period": p.filed_period_label, "items": filed_items} if filed_items else None
                ),
                reconcile_badge=badge,
                number_check=verify("data", check, figures=stored_figures, reconcile_badge=badge),
                chart_spec=chart_spec_of(
                    figures,
                    group_by=shown_by,
                    dimension_names=names,
                    time_dimensions=p.time_dimensions,
                    metric_name=metric_name,
                    unit=unit,
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
    raw_index = 0
    for f in figures:
        if f.display_state == "not_available" and state != "missing":
            # An empty cell of fresh data (a member with no row in one compared period) states
            # no number; D4 keeps not_available for data that is missing, so it is not sealed.
            continue
        published_result_id = None
        if getattr(f, "published_result_id", None) is not None:
            published_result_id = uuid.UUID(f.published_result_id)
        elif p.period_result_ids and not f.expr and raw_index < len(p.period_result_ids):
            published_result_id = p.period_result_ids[raw_index]
            raw_index += 1
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
                source_kind=(
                    "compute_run"
                    if f.expr
                    else "period_result"
                    if published_result_id is not None
                    else "query_run"
                ),
                query_run_id=None
                if f.expr or published_result_id is not None
                else outcome.query_run_id,
                compute_run_id=compute_run_id if f.expr else None,
                published_result_id=published_result_id,
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
