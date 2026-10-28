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

from sqlalchemy import func

from libs.dataagent_db import scoped_session, uuid7
from libs.dataagent_db.models import catalog, qa
from libs.sql_guard import dialect_of
from services.worker_agent.answer.narrate import Figure, shown_value
from services.worker_agent.answer.verification import verify
from services.worker_agent.events import Event
from services.worker_agent.llm import Gateway
from services.worker_agent.progress import publish
from services.worker_agent.query.executor import QueryOutcome, run_query
from services.worker_agent.query.secrets import SecretReader
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
) -> bool:
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
        return await run_query(
            org_id=event.org_id,
            data_space_id=space_id,
            principal_id=user_id,
            caller_kind="question",
            caller_id=question_id,
            source=source,
            sql=sql,
            secrets=secrets,
        )

    e = await explore(
        gateway, text, linked, dialect_of(source.kind), space.schema.examples, run_sql
    )
    for name in e.models:
        models.append({"task": "text_to_sql", "model_name": name, "tokens": e.tokens})
    if e.decision == "failed":
        return False
    figures = figures_of(e.columns, e.rows) if e.decision != "ask" else []
    if e.decision == "ask":
        kind, status = "clarification", "clarifying"
        body = (
            f"Chưa đủ chắc chắn để đưa số: các cách viết truy vấn cho kết quả khác nhau (chỉ "
            f"{e.agreeing}/6 trùng nhau). Hãy hỏi cụ thể hơn: nêu bảng, cột hoặc điều kiện cần "
            "dùng, hoặc nhờ người phụ trách định nghĩa chỉ số này."
        )
    else:
        kind, status, body = "data", "answered", text_of(e, figures)
    chart = None
    dims = [c for c in e.columns if figures and c in figures[0].dimensions]
    if len(figures) >= 2 and len(dims) == 1:
        chart = {
            "type": "bar",
            "x": dims[0],
            "x_label": dims[0],
            "y": "value",
            "y_label": figures[0].column,
        }
    answer_id = uuid7()
    async with scoped_session(ROLE, event.org_id, user_id) as session:
        question = await session.get(qa.Question, question_id, with_for_update=True)
        if question is None or question.status != "received":
            return True
        stored = await session.get(qa.Plan, plan_id)
        if stored is not None:
            stored.steps = _plan(e)
        shown = [f for f in figures if f.value is not None]
        stored_figures = [f.as_json(str(e.query_run_id), "", TRUST) for f in figures]
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
    return True
