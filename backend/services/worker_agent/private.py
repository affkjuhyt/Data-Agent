"""The private-question channel of UC-16-02 (Kho riêng lãnh đạo), worker side.

Contract with the api (services/api/modules/exec/private_channel.py, agreed 13/10/2026):
- the api SETs `exec:pq:{id}` = JSON {id, org_id, owner_id, text, context, as_of, data_space_id}
  with a 900 s TTL and LPUSHes the id to `exec:pq:queue`;
- this worker BLMOVEs ids into `exec:pq:processing` (so a crash does not drop one silently) and
  LREMs them when done;
- it XADDs {event, data} entries to `exec:pq:{id}:events` (MAXLEN ~100, TTL 900): `progress`,
  then a terminal `answer` {text, figures[{key, label, value, unit, query_run_id}]} or `error`
  {code, message}.

BR-193: the text and the answer live at most 15 minutes and never in a table, the outbox or a
log. So the answer path here reads the semantic layer and the catalogue (svc_qa, reads only),
runs its query with private=True (query.query_run keeps who, which source and the result hash, no
SQL, rows or error text), writes no qa.* row, and logs neither the question nor an exception's
message or traceback, which may quote it. A model is used only when the rules place no
metric, and only through the on-prem alias; an unavailable model is an `error`
(model_unavailable), never a fallback elsewhere.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import json
import logging
import os
import uuid
from typing import Any

from libs.dataagent_db import scoped_session
from libs.dataagent_db.models import catalog
from services.worker_agent.answer.narrate import figures_of, text_of
from services.worker_agent.llm import ModelUnavailable
from services.worker_agent.query.executor import CallerKind, run_query
from services.worker_agent.query.guard import SqlRejected, dialect_of
from services.worker_agent.questions import QUERYABLE, gateway, secrets
from services.worker_agent.semantic import compiler as c
from services.worker_agent.semantic.loader import load
from services.worker_agent.understand import llm_reader
from services.worker_agent.understand.resolver import understand

logger = logging.getLogger(__name__)

TTL_SECONDS = 900
QUEUE = "exec:pq:queue"
PROCESSING = "exec:pq:processing"
MAX_EVENTS = 100


def _key(question_id: str) -> str:
    return f"exec:pq:{question_id}"


async def _emit(redis: Any, question_id: str, event: str, data: dict[str, Any]) -> None:
    stream = f"{_key(question_id)}:events"
    await redis.xadd(
        stream,
        {"event": event, "data": json.dumps(data, ensure_ascii=False, default=str)},
        maxlen=MAX_EVENTS,
        approximate=True,
    )
    await redis.expire(stream, TTL_SECONDS)


async def answer(
    question: dict[str, Any],
    *,
    private: bool = True,
    caller_kind: CallerKind = "ad_hoc",
    caller_id: uuid.UUID | None = None,
) -> tuple[str, dict[str, Any]]:
    """The terminal event for one private question: ("answer", …) or ("error", …).

    A workspace's question tile asks the same way, recorded (private=False, caller_kind tile)."""
    if not question.get("data_space_id"):
        return "error", {
            "code": "data_space_required",
            "message": "Câu hỏi riêng cần chọn Data Space để biết chỉ số nào được dùng.",
        }
    org_id = uuid.UUID(str(question["org_id"]))
    owner_id = uuid.UUID(str(question["owner_id"]))
    space_id = uuid.UUID(str(question["data_space_id"]))
    question_id = uuid.UUID(str(question["id"]))
    today = (
        datetime.date.fromisoformat(str(question["as_of"]))
        if question.get("as_of")
        else datetime.date.today()
    )
    text = str(question.get("text") or "")

    async with scoped_session("svc_qa", org_id, owner_id) as session:
        layer = await load(session, space_id)
        u = understand(text, layer.vocabulary, today=today)
        model = gateway()
        if u.decision == "unknown" and model is not None:
            try:
                read = await llm_reader.read(text, layer.vocabulary, model, today=today)
            except ModelUnavailable:
                return "error", {"code": "model_unavailable", "message": "Mô hình nội bộ đang bận."}
            if read is not None:
                u = read[0]
        if u.decision == "clarify":
            names = "; ".join(m.name for m in u.options)
            return "answer", {
                "text": f"Câu hỏi khớp với nhiều chỉ số: {names}. Hãy hỏi lại với tên chỉ số.",
                "figures": [],
            }
        if u.decision != "proceed" or u.metric is None:
            return "error", {
                "code": "UNRESOLVED_ENTITY",
                "message": "Chưa nhận ra chỉ số nào trong câu hỏi.",
            }
        version = layer.versions[u.metric.metric_id]
        try:
            compiled = c.compile_metric(
                c.Request(
                    version,
                    group_by=u.group_by,
                    filters=u.filters,
                    period=c.Period(u.period.start, u.period.end) if u.period else None,
                ),
                layer.layer,
                dialect=dialect_of("postgresql"),
            )
        except (c.CompileError, SqlRejected) as exc:
            return "error", {"code": getattr(exc, "code", "CONTRACT_MISMATCH"), "message": str(exc)}
        dataset = layer.datasets[compiled.dataset_ids[0]]
        source = await session.get(catalog.Source, dataset.source_id)
        dimension_names = {d.code: d.name for d in layer.vocabulary.dimensions}
    if (
        source is None
        or source.mode != "federated"
        or source.status not in QUERYABLE
        or source.kind != "postgresql"
    ):
        return "error", {
            "code": "TOOL_UNAVAILABLE",
            "message": "Bảng của chỉ số này chưa được phép truy vấn tại chỗ.",
        }

    outcome = await run_query(
        org_id=org_id,
        data_space_id=space_id,
        principal_id=owner_id,
        caller_kind=caller_kind,
        caller_id=caller_id or question_id,
        source=source,
        sql=compiled.sql,
        secrets=secrets(),
        metric_version_ids=[compiled.metric_version_id],
        private=private,
    )
    if not outcome.ok:
        return "error", {
            "code": "TOOL_UNAVAILABLE",
            "message": f"Chưa truy vấn được nguồn {source.name}.",
        }
    if not outcome.rows:
        return "error", {"code": "NO_DATA_FOR_PERIOD", "message": "Không có số liệu cho kỳ này."}
    figures = figures_of(
        [col["name"] for col in outcome.columns],
        outcome.rows,
        metric_name=u.metric.name,
        unit=version.unit,
        decimals=version.decimals,
        dimension_names=dimension_names,
        period_label=u.period.label if u.period else None,
    )
    body = text_of(
        figures,
        metric_name=u.metric.name,
        group_by_names=[dimension_names.get(g, g) for g in u.group_by],
        period_label=u.period.label if u.period else None,
        filters_label=", ".join(ch.label for ch in u.chips if ch.kind == "filter") or None,
        latest_note=None
        if u.period
        else "Không nêu kỳ: số liệu là dữ liệu mới nhất có trong nguồn.",
        truncated=outcome.truncated,
    )
    return "answer", {
        "text": body,
        "figures": [
            {
                "key": f.key,
                "label": f.label,
                "value": format(f.value, "f") if f.value is not None else None,
                "unit": f.unit,
                "query_run_id": str(outcome.query_run_id),
            }
            for f in figures
        ],
    }


async def handle(redis: Any, question_id: str) -> None:
    raw = await redis.get(_key(question_id))
    if raw is None:
        return  # expired before its turn: the api already answers 410
    question = json.loads(raw)
    await _emit(redis, question_id, "progress", {"status": "running"})
    try:
        event, data = await answer(question)
    except Exception as exc:  # noqa: BLE001 - every failure ends the stream with an error event
        logger.error(
            "worker-agent | private question %s failed: %s", question_id, type(exc).__name__
        )
        event, data = (
            "error",
            {"code": "internal_error", "message": "Không trả lời được câu hỏi này."},
        )
    await _emit(redis, question_id, event, data)
    await _emit(
        redis, question_id, "done", {"status": "answered" if event == "answer" else "failed"}
    )


async def run(redis: Any, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            question_id = await redis.blmove(QUEUE, PROCESSING, 1, "RIGHT", "LEFT")
        except Exception:  # noqa: BLE001 - Redis down: keep the loop, retry after a pause
            logger.warning("worker-agent | private queue unavailable", exc_info=True)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=2)
            continue
        if question_id is None:
            continue
        try:
            await handle(redis, str(question_id))
        finally:
            await redis.lrem(PROCESSING, 1, question_id)


def redis_from_env() -> Any | None:
    url = os.environ.get("REDIS_URL")
    if not url:
        return None
    import redis.asyncio as redis

    return redis.from_url(url, decode_responses=True)
