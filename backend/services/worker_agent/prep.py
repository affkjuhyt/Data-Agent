"""The data-prep agent (UC-06-01 step 2): steps proposed from the user's words.

pipeline.steps_requested (the instruction a pipeline was created with) and
pipeline.message_requested (a message on its screen) both reach this handler. The model is given
the input tables' columns (never their rows) and the steps so far, and answers a reply and the
whole list of steps, or a question back when the words are ambiguous (UC-06-01 A1: it asks
instead of guessing). What it proposes is held to what any author's steps are held to before
the pipeline changes: a known kind, read-only SQL, a drop condition on a step that drops rows
(BR-23), the first step reading only the pipeline's inputs and the later ones `t`, and every
step compiling on the source (a LIMIT 0 read). A proposal that fails is not applied; the reply
says why.

The conversation is the pipeline's events: the user's words are in pipeline.steps_requested and
pipeline.message_requested, the agent's answer in pipeline.message_answered (pipeline.outbox,
never purged), which GET /v1/pipelines/{id} reads back as `messages`.
"""

from __future__ import annotations

import datetime
import json
import logging
import uuid
from typing import Any

from sqlalchemy import select

from libs.dataagent_db import scoped_session, uuid7
from libs.dataagent_db.models import catalog
from libs.dataagent_db.models import pipeline as pl
from services.worker_agent.events import Event, Handler
from services.worker_agent.llm import ModelUnavailable
from services.worker_agent.pipelines import _PREVIOUS, chain
from services.worker_agent.query.executor import run_query
from services.worker_agent.query.guard import SqlRejected, check_read_only, dialect_of, tables_of

logger = logging.getLogger(__name__)

ROLE = "svc_pipeline"
STEPS = "pipeline.steps_requested"
MESSAGE = "pipeline.message_requested"
ANSWERED = "pipeline.message_answered"
KINDS = ("filter", "dedupe", "join", "compute", "aggregate", "cast", "quality_rule")
MAX_STEPS = 20

SYSTEM = """Bạn là Agent Chuẩn bị dữ liệu. Bạn đề xuất các bước biến đổi dữ liệu bằng SQL chỉ đọc.
Trả lời một đối tượng JSON:
{"reply": "<một hai câu tiếng Việt nói bạn đã làm gì>",
 "question": "<câu hỏi lại nếu yêu cầu mơ hồ, nếu không thì null>",
 "steps": [{"kind": "filter|dedupe|join|compute|aggregate|cast|quality_rule",
            "description": "<mô tả bằng lời>",
            "code": "<một câu SELECT>",
            "drop_condition": "<điều kiện bỏ dòng, bắt buộc với filter và dedupe>"}]}
Quy tắc:
- "steps" là toàn bộ danh sách bước sau khi sửa, theo thứ tự, giữ nguyên các bước không đổi.
- Bước 1 chỉ đọc các bảng đầu vào, gọi đúng tên bảng đã cho. Mỗi bước sau đọc kết quả bước
  trước bằng tên t (FROM t).
- Chỉ SELECT; không INSERT, UPDATE, DELETE, DDL; không đọc bảng nào khác.
- Không làm thay đổi tổng cột tiền ngoài bước filter hay dedupe.
- Nếu không chắc người dùng muốn gì, đặt "question" và trả "steps" như cũ."""


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def check_steps(
    proposed: Any, inputs: dict[str, str], dialect: str
) -> tuple[list[dict[str, Any]], str | None]:
    """The proposed steps as the pipeline stores them, or why they cannot be applied."""
    if not isinstance(proposed, list) or not proposed:
        return [], "không có bước nào"
    if len(proposed) > MAX_STEPS:
        return [], f"quá {MAX_STEPS} bước"
    allowed_first = {name.lower() for name in inputs}
    steps: list[dict[str, Any]] = []
    for i, raw in enumerate(proposed, start=1):
        if not isinstance(raw, dict):
            return [], f"bước {i} không đúng dạng"
        kind, code = raw.get("kind"), str(raw.get("code") or "").strip().rstrip(";")
        description = str(raw.get("description") or "").strip()
        if kind not in KINDS:
            return [], f"bước {i} có loại {kind!r} không hỗ trợ"
        if not code or not description:
            return [], f"bước {i} thiếu mô tả hoặc SQL"
        drop = str(raw.get("drop_condition") or "").strip() or None
        if kind in ("filter", "dedupe") and drop is None:
            return [], f"bước {i} bỏ dòng mà không nói điều kiện (BR-23)"
        try:
            read = tables_of(
                check_read_only(code if i == 1 else _PREVIOUS.sub(r"\1 x", code), dialect), dialect
            )
        except SqlRejected as exc:
            return [], f"bước {i}: {exc}"
        names = {name.lower() for _, name in read}
        if i == 1 and not names <= allowed_first:
            return [], f"bước 1 đọc bảng ngoài đầu vào: {', '.join(sorted(names - allowed_first))}"
        if i > 1 and not names <= {"x"} | allowed_first:
            return [], f"bước {i} đọc bảng không phải t hay đầu vào"
        step: dict[str, Any] = {"kind": kind, "description": description, "code": code}
        if drop:
            step["drop_condition"] = drop
        steps.append(step)
    return steps, None


def _same(a: dict[str, Any], b: dict[str, Any]) -> bool:
    keys = ("kind", "description", "code", "drop_condition")
    return all(a.get(k) == b.get(k) for k in keys)


async def _answer(
    org_id: uuid.UUID, pipeline_id: uuid.UUID, actor: uuid.UUID, reply: str, **extra: Any
) -> None:
    async with scoped_session(ROLE, org_id, actor) as session:
        session.add(
            pl.Outbox(
                id=uuid7(),
                org_id=org_id,
                aggregate_type="pipeline",
                aggregate_id=pipeline_id,
                event_type=ANSWERED,
                payload={"pipeline_id": str(pipeline_id), "text": reply, **extra},
            )
        )


async def requested(event: Event) -> None:
    from services.worker_agent.pipelines import _context
    from services.worker_agent.questions import gateway, secrets

    p = event.payload
    pipeline_id, actor = uuid.UUID(str(p["pipeline_id"])), uuid.UUID(str(p["requested_by"]))
    words = str(p.get("instruction") or p.get("text") or "").strip()
    async with scoped_session(ROLE, event.org_id, actor) as session:
        row = await session.get(pl.Pipeline, pipeline_id)
        if row is None or row.deleted_at is not None:
            return
        before = [dict(s) for s in row.steps or [] if isinstance(s, dict)]
    # The inputs and their one source, as a run sees them.
    ctx = await _context(
        Event(
            event_id=event.event_id,
            org_id=event.org_id,
            event_type=event.event_type,
            aggregate_type="pipeline",
            aggregate_id=pipeline_id,
            payload={"pipeline_id": str(pipeline_id), "requested_by": str(actor)},
        ),
        need_run=False,
    )
    if ctx is None or ctx["source"] is None:
        await _answer(
            event.org_id,
            pipeline_id,
            actor,
            "Các bảng đầu vào không cùng một nguồn nên chưa đề xuất được bước nào.",
        )
        return
    source, dialect = ctx["source"], dialect_of(ctx["source"].kind)
    inputs: dict[str, str] = {}
    async with scoped_session("svc_catalog", event.org_id, actor) as session:
        for d in ctx["datasets"]:
            cols = (
                await session.scalars(
                    select(catalog.ColumnMeta)
                    .where(catalog.ColumnMeta.dataset_id == d.id)
                    .order_by(catalog.ColumnMeta.ordinal)
                )
            ).all()
            inputs[d.table_name] = ", ".join(
                f"{c.name} {c.normalized_type or c.data_type}" for c in cols
            )
    for d in ctx["datasets"]:
        if not inputs.get(d.table_name):
            out = await run_query(
                org_id=event.org_id,
                data_space_id=ctx["space"],
                principal_id=actor,
                caller_kind="pipeline",
                caller_id=pipeline_id,
                source=source,
                sql=f'SELECT * FROM "{d.schema_name}"."{d.table_name}" LIMIT 0',
                secrets=secrets(),
                limit=1,
            )
            if out.ok:
                inputs[d.table_name] = ", ".join(f"{c['name']} {c['type']}" for c in out.columns)
    model = gateway()
    if model is None:
        await _answer(
            event.org_id,
            pipeline_id,
            actor,
            "Chưa nối được mô hình nên Agent chưa đề xuất được bước; bạn có thể tự thêm bước SQL.",
        )
        return
    prompt = json.dumps(
        {
            "yêu cầu": words,
            "bảng đầu vào": inputs,
            "các bước hiện có": [
                {k: s.get(k) for k in ("kind", "description", "code", "drop_condition")}
                for s in before
            ],
        },
        ensure_ascii=False,
    )
    try:
        reply = await model.json_chat("responder", SYSTEM, prompt, max_tokens=1500)
    except ModelUnavailable as exc:
        logger.info("worker-agent | prep agent for pipeline %s: %s", pipeline_id, exc)
        await _answer(
            event.org_id,
            pipeline_id,
            actor,
            "Mô hình đang không trả lời; chưa có bước nào thay đổi. Hãy thử lại sau.",
        )
        return
    text = str(reply.content.get("reply") or "").strip()
    question = reply.content.get("question")
    if isinstance(question, str) and question.strip():
        await _answer(event.org_id, pipeline_id, actor, question.strip(), asks=True)
        return
    steps, why = check_steps(reply.content.get("steps"), inputs, dialect)
    if why is None:
        for i in range(len(steps)):
            out = await run_query(
                org_id=event.org_id,
                data_space_id=ctx["space"],
                principal_id=actor,
                caller_kind="pipeline",
                caller_id=pipeline_id,
                source=source,
                sql=f"SELECT * FROM ({chain(steps, i)}) q LIMIT 0",
                secrets=secrets(),
                limit=1,
            )
            if not out.ok:
                why = f"bước {i + 1} không chạy được trên nguồn: {out.error or out.status}"
                break
    if why is not None:
        await _answer(
            event.org_id,
            pipeline_id,
            actor,
            f"Agent đề xuất các bước nhưng chưa áp dụng vì {why}.",
            rejected=True,
        )
        return
    for i, step in enumerate(steps):
        # What the screen previews first: the steps this answer added or changed.
        step["is_new"] = i >= len(before) or not _same(step, before[i])
    async with scoped_session(ROLE, event.org_id, actor) as session:
        locked = await session.scalar(
            select(pl.Pipeline).where(pl.Pipeline.id == pipeline_id).with_for_update()
        )
        if locked is None:
            return
        current = [dict(s) for s in locked.steps or [] if isinstance(s, dict)]
        if len(current) != len(before) or not all(
            _same(a, b) for a, b in zip(current, before, strict=True)
        ):
            # Someone edited the steps while the model was thinking: theirs stand (no
            # last-write-wins), the answer says so.
            await _answer(
                event.org_id,
                pipeline_id,
                actor,
                "Các bước vừa được sửa trong lúc Agent đề xuất; hãy gửi lại yêu cầu.",
                rejected=True,
            )
            return
        locked.steps = steps
        locked.updated_by, locked.updated_at = actor, _now()
    await _answer(
        event.org_id,
        pipeline_id,
        actor,
        text or f"Đã cập nhật {len(steps)} bước.",
        changed=[i + 1 for i, s in enumerate(steps) if s["is_new"]],
    )
    logger.info("worker-agent | prep agent set %d steps on pipeline %s", len(steps), pipeline_id)


HANDLERS: dict[str, Handler] = {STEPS: requested, MESSAGE: requested}
