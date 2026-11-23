"""An answer's receipt, written and sealed in the answer's transaction (AE-05, BR-176, ADR-43).

Every path that answers with numbers seals one: the governed path (questions.py, with the metric
version, the published results and the freshness it checked), the explore and verified-query
paths (sqlgen/answer.py) and the agentic engine (agentic/engine.py), whose figures are read from
query runs. A receipt names its data product when the answer is in one; outside every product it
names none (v3_0049). The seal trigger counts the figures and hashes the whole receipt; a failure
here rolls the answer back with it.

Sealing also writes `answer.sealed` to qa.outbox in the same transaction, for the eval nightly and
the correction gate to read (API_AND_SCHEMA_CONTRACTS, outbox events).
"""

from __future__ import annotations

import datetime
import decimal
import hashlib
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db import scoped_session, uuid7
from libs.dataagent_db.models import qa
from libs.dataagent_db.models import query as q

# The Agent runtime and Verifier the receipt names (D4 runtime_version).
RUNTIME_VERSION = "worker-agent-r1"
# compiler_version of a receipt whose numbers no semantic compiler made (explore, agentic).
NO_COMPILER = "none"


@dataclass(frozen=True)
class Product:
    id: uuid.UUID
    code: str
    version: int


@dataclass(frozen=True)
class Receipt:
    org_id: uuid.UUID
    answer_id: uuid.UUID
    question_id: uuid.UUID
    plan_id: uuid.UUID
    engine: str
    trust_code: str
    compiler_version: str
    runtime_version: str
    knowledge_mode: str = "latest"
    knowledge_at: datetime.datetime | None = None
    models: list[dict[str, Any]] = field(default_factory=list)
    freshness: list[dict[str, Any]] = field(default_factory=list)
    product: Product | None = None


async def seal(
    session: AsyncSession, receipt: Receipt, figures: Sequence[dict[str, Any]]
) -> uuid.UUID:
    """The receipt, its figures (qa.answer_receipt_figure columns), the seal and answer.sealed.

    The answer must already be flushed: the insert trigger reads its product scope."""
    receipt_id = uuid7()
    product = receipt.product
    session.add(
        qa.AnswerReceipt(
            id=receipt_id,
            org_id=receipt.org_id,
            answer_id=receipt.answer_id,
            data_product_id=product.id if product else None,
            data_product_code=product.code if product else None,
            data_product_version=product.version if product else None,
            plan_id=receipt.plan_id,
            answered_at=datetime.datetime.now(datetime.UTC),
            knowledge_mode=receipt.knowledge_mode,
            knowledge_at=receipt.knowledge_at,
            compiler_version=receipt.compiler_version,
            runtime_version=receipt.runtime_version,
            models=receipt.models,  # empty when the answer took no model call
            frontier_used=False,
            freshness=receipt.freshness,
        )
    )
    await session.flush()
    for figure in figures:
        session.add(
            qa.AnswerReceiptFigure(
                id=uuid7(), org_id=receipt.org_id, receipt_id=receipt_id, **figure
            )
        )
    await session.flush()
    row = await session.get(qa.AnswerReceipt, receipt_id)
    assert row is not None
    row.sealed_at = datetime.datetime.now(datetime.UTC)
    session.add(
        qa.Outbox(
            id=uuid7(),
            org_id=receipt.org_id,
            aggregate_type="answer",
            aggregate_id=receipt.answer_id,
            event_type="answer.sealed",
            payload={
                "answer_id": str(receipt.answer_id),
                "question_id": str(receipt.question_id),
                "trust_code": receipt.trust_code,
                "receipt_id": str(receipt_id),
                "engine": receipt.engine,
            },
        )
    )
    await session.flush()
    return receipt_id


@dataclass(frozen=True)
class Run:
    sql_sha256: str
    result_sha256: str | None
    as_of: datetime.date | None


def _run_id(figure: dict[str, Any]) -> uuid.UUID | None:
    source = figure.get("source") if isinstance(figure.get("source"), dict) else {}
    raw = (source or {}).get("query_run_id") or figure.get("query_run_id")
    try:
        return uuid.UUID(str(raw)) if raw else None
    except ValueError:
        return None


async def runs_of(
    org_id: uuid.UUID, user_id: uuid.UUID, figures: Sequence[dict[str, Any]]
) -> dict[uuid.UUID, Run]:
    """The query runs the figures were read from. svc_qa cannot read query.query_run, so this is
    its own session, before the answer's transaction: the runs were committed when they ran."""
    ids = {run_id for f in figures if (run_id := _run_id(f)) is not None}
    if not ids:
        return {}
    async with scoped_session("svc_query", org_id, user_id) as session:
        rows = (await session.scalars(select(q.QueryRun).where(q.QueryRun.id.in_(ids)))).all()
    # The replay compares sha256(sql_generated) with the receipt (replay.py).
    return {
        r.id: Run(hashlib.sha256(r.sql_generated.encode()).hexdigest(), r.result_hash, r.as_of)
        for r in rows
    }


def query_figures(
    figures: Sequence[dict[str, Any]], runs: dict[uuid.UUID, Run]
) -> list[dict[str, Any]]:
    """The receipt rows of figures read from query runs (explore, verified query, agentic).

    A figure whose run is unknown is left out: the receipt states only what it can replay. A
    figure without a value says the cell was empty: not_available, which D4 ties to `missing`."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for f in figures:
        key = str(f.get("key") or "")
        run_id = _run_id(f)
        run = runs.get(run_id) if run_id is not None else None
        if not key or key in seen or run is None:
            continue
        seen.add(key)
        value = _decimal(f.get("value"))
        shown = value is not None and f.get("display_state", "shown") == "shown"
        out.append(
            {
                "figure_key": key,
                "label": str(f.get("label") or key),
                "value": value if shown else None,
                "unit": f.get("unit"),
                "display_state": "shown" if shown else "not_available",
                "source_kind": "query_run",
                "query_run_id": run_id,
                "compiled_sql_sha256": run.sql_sha256,
                "result_sha256": run.result_sha256,
                "as_of": run.as_of,
                "freshness_state": "not_applicable" if shown else "missing",
            }
        )
    return out


def _decimal(raw: Any) -> decimal.Decimal | None:
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = decimal.Decimal(str(raw))
    except decimal.InvalidOperation:
        return None
    return value if value.is_finite() else None
