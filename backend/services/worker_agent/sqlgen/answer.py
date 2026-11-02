"""Answering a question the semantic layer cannot by SQL the model writes (01 §3.4, 00 §4.2: a
text-to-SQL answer is EXPLORATORY). Hỏi đáp calls it after the rule pass and the planner model
found no metric, before looking in the documents.

The question is linked to the Data Space's tables; nothing linked, no model, or every candidate
failing hands the question back (False). Otherwise the vote decides: ≥ 4/6 answers with the label
"Khám phá", 3/6 answers asking to check it, ≤ 2/6 gives no number and asks a narrower question.
The answer's numbers are figures of the winning query's rows, traced to its query_run like any
other; the SQL and every candidate are in the plan.
"""

from __future__ import annotations

import datetime
import decimal
import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy import func, select

from libs import clock
from libs.contracts.v1.provider import ProviderRequest
from libs.dataagent_db import scoped_session, uuid7
from libs.dataagent_db.models import catalog, qa
from libs.sql_guard import dialect_of
from services.worker_agent import turn_trace
from services.worker_agent.answer.narrate import Figure, shown_value
from services.worker_agent.answer.verification import verify
from services.worker_agent.events import Event
from services.worker_agent.llm import Gateway
from services.worker_agent.progress import publish
from services.worker_agent.providers.registry import ProviderRegistry
from services.worker_agent.query.executor import QueryOutcome, run_query
from services.worker_agent.query.scope import can_ask_data_space
from services.worker_agent.query.secrets import SecretReader
from services.worker_agent.sqlgen import verified
from services.worker_agent.sqlgen.run import Exploration, explore
from services.worker_agent.sqlgen.schema import Linked, link
from services.worker_agent.sqlgen.space import load_space

ROLE = "svc_qa"
AGENT_NAME = "Agent Hỏi đáp"
TRUST = "Khám phá (truy vấn do mô hình viết)"
QUERYABLE = ("active", "testing")
MAX_FIGURES = 50
SHOWN_ROWS = 10


def _number(v: Any) -> decimal.Decimal | None:
    if isinstance(v, bool) or v is None:
        return None
    try:
        d = decimal.Decimal(str(v))
    except (decimal.InvalidOperation, ValueError):
        return None
    return d if d.is_finite() else None


def figures_of(columns: list[str], rows: list[list[Any]]) -> list[Figure]:
    """One figure per row: its last numeric column is the value, the others its dimensions."""
    numeric = [
        i
        for i, _ in enumerate(columns)
        if rows
        and all(_number(r[i]) is not None for r in rows if r[i] is not None)
        and any(r[i] is not None for r in rows)
    ]
    if not numeric:
        return []
    at = numeric[-1]
    out = []
    for n, row in enumerate(rows[:MAX_FIGURES]):
        value = _number(row[at])
        dims = {columns[i]: str(row[i]) for i in range(len(columns)) if i != at}
        decimals = 0 if value is None or value == value.to_integral_value() else 2
        label = ", ".join(dims.values()) or columns[at]
        out.append(Figure(f"F{n + 1}", label, value, None, decimals, n, columns[at], dims))
    return out


def text_of(e: Exploration, figures: list[Figure]) -> str:
    if len(figures) == 1 and not figures[0].dimensions:
        head = f"Kết quả: {shown_value(figures[0])}."
    elif figures:
        shown = "; ".join(
            f"{f.label}: {shown_value(f)}" for f in figures[:SHOWN_ROWS] if f.value is not None
        )
        more = f" (và {len(e.rows) - SHOWN_ROWS} dòng nữa)" if len(e.rows) > SHOWN_ROWS else ""
        head = f"Kết quả {len(e.rows)} dòng: {shown}{more}."
    else:
        head = f"Kết quả {len(e.rows)} dòng, không có cột số."
    trust = (
        f" Khám phá: truy vấn do mô hình viết, không qua định nghĩa chỉ số; {e.agreeing}/6 cách "
        "viết cho cùng kết quả."
    )
    check = (
        " Nên kiểm lại trước khi dùng: các cách viết chưa đồng thuận rõ."
        if e.decision == "review"
        else ""
    )
    return head + trust + check


def _keep_one_source(
    linked: Linked, source_of: dict[uuid.UUID, uuid.UUID | None]
) -> tuple[Linked, uuid.UUID | None]:
    """One query runs on one source: the tables of the best-linked table's source."""
    src = source_of.get(linked.tables[0].dataset_id)
    tables = [t for t in linked.tables if source_of.get(t.dataset_id) == src]
    ids = {t.dataset_id for t in tables}
    names = {t.name for t in tables}
    return (
        Linked(
            tables,
            [fk for fk in linked.fks if fk.from_table in ids and fk.to_table in ids],
            [v for v in linked.values if v[0] in names],
        ),
        src,
    )


def _plan(e: Exploration) -> dict[str, Any]:
    return {
        "version": 2,
        "goal": "answer",
        "path": "exploratory_sql",
        "steps": [
            {
                "id": "s1",
                "kind": "sql_query",
                "tool": "query.sql",
                "args": {"sql": e.sql},
                "depends_on": [],
                "checkpoint": "none",
                "on_error": "fail",
                "explain": f"Truy vấn khám phá ({e.agreeing}/6 cách viết đồng thuận)",
            }
        ]
        if e.sql
        else [],
        "candidates": e.tried,
        "confidence": e.confidence,
    }


async def answer_by_sql(
    event: Event,
    question_id: uuid.UUID,
    user_id: uuid.UUID | None,
    plan_id: uuid.UUID,
    models: list[dict[str, Any]],
    *,
    gateway: Gateway | None,
    secrets: SecretReader,
    workspace_source: Callable[[uuid.UUID, uuid.UUID], catalog.Source],
    providers: ProviderRegistry,
    ask_back: bool = True,
) -> bool:
    """`ask_back=False`: when the ways of writing disagree, nothing is stored and False comes
    back, so the caller can ask its own question back (a metric it suggests)."""
    if gateway is None or user_id is None:
        return False
    started = datetime.datetime.now(datetime.UTC)
    async with scoped_session(ROLE, event.org_id, user_id) as session:
        question = await session.get(qa.Question, question_id)
        if question is None or question.status != "received":
            return False
        conversation = await session.get(qa.Conversation, question.conversation_id)
        assert conversation is not None
        space_id, text = conversation.data_space_id, question.text
        space = await load_space(session, space_id, user_id)
        linked = link(text, space.schema)
        if not linked.tables:
            return False
        linked, src = _keep_one_source(linked, space.source_of)
        source = (
            workspace_source(space_id, event.org_id)
            if src is None
            else await session.get(catalog.Source, src)
        )
        if source is None or source.mode != "federated" or source.status not in QUERYABLE:
            return False
        if src is not None:
            session.expunge(source)
    await publish(
        str(question_id), "progress", {"question_id": str(question_id), "status": "running"}
    )

    async def run_sql(sql: str) -> QueryOutcome:
        request = ProviderRequest(
            provider="relational",
            org_id=event.org_id,
            data_space_id=space_id,
            principal_id=user_id,
            source_id=source.id,
            sql=sql,
            caller_kind="question",
            caller_id=question_id,
        )
        envelope = await providers.get(request.provider).execute(request)
        return QueryOutcome(
            query_run_id=envelope.provenance.query_run_id
            if envelope.provenance is not None
            else uuid.uuid4(),
            status="succeeded" if envelope.error is None else "failed",
            columns=[column.model_dump() for column in envelope.columns],
            rows=[list(row.values()) for row in envelope.rows],
            truncated=envelope.truncated,
            sql=sql,
            error=envelope.error,
        )

    async with turn_trace.stepping(
        "write_sql",
        target=f"{source.name} cho: {text[:80]}",
        chips=[{"kind": "source", "label": source.name}],
    ) as written:
        e = await explore(
            gateway, text, linked, dialect_of(source.kind), space.schema.examples, run_sql
        )
        written.detail = {
            "candidates": len(e.tried),
            "agreeing": e.agreeing,
            "rejected": sum(1 for t in e.tried if t.get("status") == "rejected"),
        }
        if e.decision == "failed":
            written.status, written.summary = "failed", "Không cách viết SQL nào chạy được."
        elif e.decision == "ask":
            written.summary = f"Chỉ {e.agreeing}/{len(e.tried)} cách viết cho cùng kết quả."
    for name in e.models:
        models.append({"task": "text_to_sql", "model_name": name, "tokens": e.tokens})
    if e.decision == "failed" or (e.decision == "ask" and not ask_back):
        return False
    await turn_trace.record_step(
        "query",
        target=f"{source.name} cho: {text[:80]}",
        chips=[{"kind": "source", "label": source.name}],
        detail={
            "row_count": len(e.rows),
            "truncated": e.truncated,
            "query_run_id": str(e.query_run_id) if e.query_run_id else None,
            "columns": list(e.columns),
        },
    )
    figures = figures_of(e.columns, e.rows) if e.decision != "ask" else []
    if e.decision == "ask":
        kind, status = "clarification", "clarifying"
        body = (
            f"Chưa đủ chắc chắn để đưa số: các cách viết truy vấn cho kết quả khác nhau (chỉ "
            f"{e.agreeing}/6 trùng nhau). Hãy hỏi cụ thể hơn: nêu bảng, cột hoặc điều kiện cần "
            "dùng, hoặc nhờ người phụ trách định nghĩa chỉ số này."
        )
        await turn_trace.record_step(
            "ask_user", target=body, detail={"question": body, "options": []}
        )
    else:
        kind, status, body = "data", "answered", text_of(e, figures)
    chart = _bar(figures, e.columns)
    await _store_answer(
        event,
        question_id,
        plan_id,
        user_id,
        started,
        kind=kind,
        status=status,
        body=body,
        figures=figures,
        chart=chart,
        steps=_plan(e),
        query_run_id=e.query_run_id,
        trust=TRUST,
        consensus=(e.agreeing, len(e.tried)),
    )
    return True


async def answer_verified_first(
    event: Event,
    question_id: uuid.UUID,
    *,
    secrets: SecretReader,
    workspace_source: Callable[[uuid.UUID, uuid.UUID], catalog.Source],
) -> bool:
    """A question that is an approved query's is answered by it before the semantic layer reads
    it (R1-08; invariant 14: Đã kiểm above Có quản trị): a steward approved this SQL for these
    words, so no metric reading nor clarification comes first. Not for an agent version's eval
    run, whose data_scope the semantic path applies (agent_evals.py). The asker's ask grant in the
    Data Space is checked here, as the semantic path checks it; run_query checks the rest."""
    if event.payload.get("data_scope"):
        return False
    async with scoped_session(ROLE, event.org_id, event.payload.get("user_id")) as session:
        question = await session.get(qa.Question, question_id)
        if question is None or question.status != "received":
            return False
        conversation = await session.get(qa.Conversation, question.conversation_id)
        if conversation is None or conversation.user_id is None:
            return False
        user_id, space_id = conversation.user_id, conversation.data_space_id
        today = question.as_of or clock.today(await clock.space_zone(session, space_id))
        found = verified.match(
            question.text,
            await verified.active_examples(session, space_id),
            today,
            await clock.space_calendar(session, space_id),
        )
        if found is None or not await can_ask_data_space(
            session, data_space_id=space_id, principal_id=user_id
        ):
            return False
        plan_id = uuid7()
        version = int(
            await session.scalar(
                select(func.coalesce(func.max(qa.Plan.version), 0) + 1).where(
                    qa.Plan.question_id == question_id
                )
            )
        )
        session.add(
            qa.Plan(
                id=plan_id,
                org_id=event.org_id,
                question_id=question_id,
                version=version,
                chips=[
                    {
                        "kind": "verified_query",
                        "value": str(found.example_id),
                        "label": found.question,
                    }
                ],
                steps={"goal": "answer", "path": "verified_query", "steps": []},
            )
        )
    await publish(str(question_id), "planning", {"question_id": str(question_id)})
    await turn_trace.record_step(
        "use_verified",
        target=found.question,
        detail={"name": found.question, "example_id": str(found.example_id)},
    )
    return await _answer_verified(
        event,
        question_id,
        user_id,
        plan_id,
        datetime.datetime.now(datetime.UTC),
        secrets=secrets,
        workspace_source=workspace_source,
    )


async def _answer_verified(
    event: Event,
    question_id: uuid.UUID,
    user_id: uuid.UUID,
    plan_id: uuid.UUID,
    started: datetime.datetime,
    *,
    secrets: SecretReader,
    workspace_source: Callable[[uuid.UUID, uuid.UUID], catalog.Source],
) -> bool:
    """The question answered by the approved query it matches (sqlgen/verified.py); False when it
    matches none, or that query does not run for this person, and the model writes one instead."""
    async with scoped_session(ROLE, event.org_id, user_id) as session:
        question = await session.get(qa.Question, question_id)
        if question is None or question.status != "received":
            return False
        conversation = await session.get(qa.Conversation, question.conversation_id)
        assert conversation is not None
        space_id = conversation.data_space_id
        today = question.as_of or clock.today(await clock.space_zone(session, space_id))
        found = verified.match(
            question.text,
            await verified.active_examples(session, space_id),
            today,
            await clock.space_calendar(session, space_id),
        )
        if found is None:
            return False
        origin = await verified.origin_of(session, space_id, found.sql, user_id)
        if not origin.ok:
            return False
        source = (
            workspace_source(space_id, event.org_id)
            if origin.source_id is None
            else await session.get(catalog.Source, origin.source_id)
        )
        if source is None or source.mode != "federated" or source.status not in QUERYABLE:
            return False
        if origin.source_id is not None:
            session.expunge(source)
    async with turn_trace.stepping(
        "query",
        target=f"{source.name} cho: {found.question}",
        chips=[{"kind": "source", "label": source.name}],
    ) as step:
        outcome = await run_query(
            org_id=event.org_id,
            data_space_id=space_id,
            principal_id=user_id,
            caller_kind="question",
            caller_id=question_id,
            source=source,
            sql=found.sql,
            secrets=secrets,
        )
        step.detail = {
            "row_count": len(outcome.rows or []),
            "truncated": bool(outcome.truncated),
            "query_run_id": str(outcome.query_run_id) if outcome.query_run_id else None,
            "columns": [str(c.get("name")) for c in outcome.columns],
        }
        if not outcome.ok:
            # The model writes a query instead (the caller's next path); this one is set aside.
            step.status, step.summary = "skipped", "Truy vấn đã duyệt không chạy được cho bạn."
    if not outcome.ok:
        return False
    columns = [str(c.get("name")) for c in outcome.columns]
    figures = figures_of(columns, outcome.rows)
    if len(figures) == 1 and not figures[0].dimensions:
        head = f"Kết quả: {shown_value(figures[0])}."
    elif figures:
        shown = "; ".join(
            f"{f.label}: {shown_value(f)}" for f in figures[:SHOWN_ROWS] if f.value is not None
        )
        more = (
            f" (và {len(outcome.rows) - SHOWN_ROWS} dòng nữa)"
            if len(outcome.rows) > SHOWN_ROWS
            else ""
        )
        head = f"Kết quả {len(outcome.rows)} dòng: {shown}{more}."
    else:
        head = f"Kết quả {len(outcome.rows)} dòng, không có cột số."
    period = f" ({found.period.label})" if found.period else ""
    body = f"{head} Đã kiểm: theo truy vấn đã duyệt cho câu “{found.question}”{period}."
    steps = {
        "goal": "answer",
        "path": "verified_query",
        "steps": [
            {
                "id": "s1",
                "kind": "sql_query",
                "tool": "query.sql",
                "args": {"sql": found.sql, "example_query_id": str(found.example_id)},
                "depends_on": [],
                "checkpoint": "none",
                "on_error": "fail",
                "explain": f"Truy vấn đã duyệt cho “{found.question}”",
            }
        ],
    }
    await publish(
        str(question_id), "progress", {"question_id": str(question_id), "status": "running"}
    )
    await _store_answer(
        event,
        question_id,
        plan_id,
        user_id,
        started,
        kind="data",
        status="answered",
        body=body,
        figures=figures,
        chart=_bar(figures, columns),
        steps=steps,
        query_run_id=outcome.query_run_id,
        trust=verified.TRUST_VERIFIED,
        consensus=None,
    )
    return True


def _bar(figures: list[Figure], columns: list[str]) -> dict[str, Any] | None:
    dims = [c for c in columns if figures and c in figures[0].dimensions]
    if len(figures) >= 2 and len(dims) == 1:
        return {
            "type": "bar",
            "x": dims[0],
            "x_label": dims[0],
            "y": "value",
            "y_label": figures[0].column,
        }
    return None


async def _store_answer(
    event: Event,
    question_id: uuid.UUID,
    plan_id: uuid.UUID,
    user_id: uuid.UUID,
    started: datetime.datetime,
    *,
    kind: str,
    status: str,
    body: str,
    figures: list[Figure],
    chart: dict[str, Any] | None,
    steps: dict[str, Any],
    query_run_id: uuid.UUID | None,
    trust: str,
    consensus: tuple[int, int] | None,
) -> None:
    """The answer, its plan and the question's status, then the events the stream waits for."""
    answer_id = uuid7()
    async with scoped_session(ROLE, event.org_id, user_id) as session:
        question = await session.get(qa.Question, question_id, with_for_update=True)
        if question is None or question.status != "received":
            return
        stored = await session.get(qa.Plan, plan_id)
        if stored is not None:
            stored.steps = steps
        shown = [f for f in figures if f.value is not None]
        stored_figures = [f.as_json(str(query_run_id), "", trust) for f in figures]
        session.add(
            qa.Answer(
                id=answer_id,
                org_id=event.org_id,
                question_id=question_id,
                plan_id=plan_id,
                kind=kind,
                text=body,
                figures=stored_figures,
                chart_spec=chart,
                number_check=verify(
                    kind,
                    {
                        "numbers_total": len(shown),
                        "traced": len(shown),
                        "untraceable_draft": 0,
                        "regenerated": 0,
                        "untraceable_final": 0,
                    },
                    figures=stored_figures,
                    consensus=consensus,
                ),
                agent_name=AGENT_NAME,
                latency_ms=int(
                    (datetime.datetime.now(datetime.UTC) - started).total_seconds() * 1000
                ),
                product_scope="not_applicable",
            )
        )
        question.status = status
        if status == "answered":
            question.answered_at = func.now()
    payload = {"question_id": str(question_id), "answer_id": str(answer_id), "kind": kind}
    await publish(
        str(question_id), "clarification" if kind == "clarification" else "answer", payload
    )
    await publish(str(question_id), "done", {**payload, "status": status})
