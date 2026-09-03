"""Tenant-scoped read queries behind the admin dashboards.

Everything here is a real query against Conversations / Messages / AgentTraces /
Agents (+ PlatformSettings for the settings store). Empty tenants get zeros and
empty lists — never sample data. Each helper takes the caller's verified tenant
and uses `client.get_client(tenant_id=...)` so RLS applies where enforced and the
explicit `tenant_id = :tid` predicate applies where it is not.

agent attribution: Conversations has no agent_id column. The assistant message
written at the end of every turn (runtime_services/conversation_runner.py)
carries `metadata.agent_version_id`, which resolves through AgentVersions to
Agents. A conversation's agent is therefore the agent of its most recent
assistant message, null when no assistant turn has been stored yet.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
from typing import Any
from uuid import UUID

from sqlalchemy import Date, String, and_, cast, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from libs.basemodel.services_databaseconnector.postgres_orm.workflow_orm import (
    AgentsORM,
    AgentTracesORM,
    AgentVersionsORM,
    ConversationsORM,
    MessagesORM,
    PlatformSettingsORM,
)
from libs.database_connector.postgres_connector import client

logger = logging.getLogger(__name__)

_DEV_TENANT_ID = os.getenv("DEV_TENANT_ID", "00000000-0000-0000-0000-000000000001")


def resolve_tenant(caller: dict[str, Any]) -> str:
    """The caller's verified tenant as a UUID string; dev tenant when it is not one.

    Auth may hand back "" or a non-UUID placeholder outside Kong, and every
    query below casts to uuid. This is the single tenant-resolution helper for
    the platform routers (catalog, schema_memory, schema_catalog included).
    """
    raw = caller.get("tenant_id") if caller else None
    if raw:
        try:
            UUID(str(raw))
            return str(raw)
        except ValueError:
            pass
    return _DEV_TENANT_ID


def _iso(value: dt.datetime | None) -> str | None:
    return value.isoformat() if value else None


def _escape_like(q: str) -> str:
    return q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _agent_version_of_conversation() -> Any:
    """Scalar subquery: agent_version_id from the newest assistant message."""
    return (
        select(MessagesORM.msg_metadata["agent_version_id"].astext)
        .where(
            MessagesORM.conversation_id == ConversationsORM.id,
            MessagesORM.role == "assistant",
            MessagesORM.msg_metadata.isnot(None),
        )
        .order_by(MessagesORM.created_at.desc())
        .limit(1)
        .correlate(ConversationsORM)
        .scalar_subquery()
    )


# ─── Conversations ────────────────────────────────────────────────────────────

async def list_conversations(
    tenant_id: str,
    *,
    limit: int = 50,
    offset: int = 0,
    status: str | None = None,
    q: str | None = None,
    agent_id: str | None = None,
) -> dict[str, Any]:
    if not client.is_connected():
        return {"items": [], "total": 0}

    tid = UUID(tenant_id)
    agg = (
        select(
            MessagesORM.conversation_id.label("conversation_id"),
            func.count(MessagesORM.id).label("message_count"),
            func.max(MessagesORM.created_at).label("last_message_at"),
            func.coalesce(func.sum(MessagesORM.tokens_used), 0).label("tokens"),
            func.avg(MessagesORM.latency_ms).filter(MessagesORM.role == "assistant").label("avg_latency_ms"),
        )
        .group_by(MessagesORM.conversation_id)
        .subquery("agg")
    )
    last_ver = _agent_version_of_conversation()

    base = (
        select(
            ConversationsORM.id,
            ConversationsORM.user_ref,
            ConversationsORM.channel,
            ConversationsORM.status,
            ConversationsORM.started_at,
            ConversationsORM.ended_at,
            ConversationsORM.inserted_at,
            agg.c.message_count,
            agg.c.last_message_at,
            agg.c.tokens,
            agg.c.avg_latency_ms,
            AgentsORM.id.label("agent_id"),
            AgentsORM.name.label("agent_name"),
        )
        .outerjoin(agg, agg.c.conversation_id == ConversationsORM.id)
        # Compared as text: the metadata value is caller-written JSON, and a
        # uuid cast on a malformed value would fail the whole listing.
        .outerjoin(AgentVersionsORM, cast(AgentVersionsORM.id, String) == last_ver)
        .outerjoin(AgentsORM, AgentsORM.id == AgentVersionsORM.agent_id)
        .where(ConversationsORM.tenant_id == tid)
    )
    if status:
        base = base.where(ConversationsORM.status == status)
    if agent_id:
        try:
            base = base.where(AgentsORM.id == UUID(agent_id))
        except ValueError:
            return {"items": [], "total": 0}
    if q:
        pattern = _escape_like(q.strip())
        base = base.where(or_(
            cast(ConversationsORM.id, String).ilike(f"{pattern}%", escape="\\"),
            ConversationsORM.user_ref.ilike(f"%{pattern}%", escape="\\"),
        ))

    last_activity = func.coalesce(
        agg.c.last_message_at, ConversationsORM.started_at, ConversationsORM.inserted_at,
    )
    page = base.order_by(last_activity.desc(), ConversationsORM.id.desc()).limit(limit).offset(offset)
    total_stmt = select(func.count()).select_from(base.subquery("filtered"))

    async with client.get_client(tenant_id=tenant_id) as session:
        rows = (await session.execute(page)).fetchall()
        total = int((await session.execute(total_stmt)).scalar_one())

    items = [
        {
            "id": str(r.id),
            "user_ref": r.user_ref,
            "channel": r.channel,
            "status": r.status,
            "started_at": _iso(r.started_at or r.inserted_at),
            "ended_at": _iso(r.ended_at),
            "message_count": int(r.message_count or 0),
            "last_message_at": _iso(r.last_message_at),
            "agent_id": str(r.agent_id) if r.agent_id else None,
            "agent_name": r.agent_name,
            "tokens": int(r.tokens or 0),
            "avg_latency_ms": round(float(r.avg_latency_ms), 1) if r.avg_latency_ms is not None else None,
        }
        for r in rows
    ]
    return {"items": items, "total": total}


async def get_messages_with_traces(
    conversation_id: str, tenant_id: str, limit: int = 50,
) -> list[dict[str, Any]]:
    """Messages of one conversation with their AgentTraces attached.

    Same tenant scoping as db.get_messages_ui, plus an explicit ownership check
    on Conversations.tenant_id so a caller naming another tenant's conversation
    gets [] even where RLS is not enforced.
    """
    if not client.is_connected():
        return []
    try:
        conv_uuid = UUID(conversation_id)
    except ValueError:
        return []
    tid = UUID(tenant_id)
    try:
        async with client.get_client(tenant_id=tenant_id) as session:
            owned = await session.execute(
                select(ConversationsORM.id).where(
                    ConversationsORM.id == conv_uuid, ConversationsORM.tenant_id == tid,
                )
            )
            if owned.first() is None:
                return []
            rows = (await session.execute(
                select(
                    MessagesORM.id, MessagesORM.role, MessagesORM.content, MessagesORM.msg_metadata,
                    MessagesORM.created_at, MessagesORM.tokens_used, MessagesORM.latency_ms,
                )
                .where(MessagesORM.conversation_id == conv_uuid)
                .order_by(MessagesORM.created_at.asc())
                .limit(limit)
            )).fetchall()
            ids = [r.id for r in rows]
            traces_by_msg: dict[Any, list[dict[str, Any]]] = {}
            if ids:
                trace_rows = (await session.execute(
                    select(
                        AgentTracesORM.message_id, AgentTracesORM.trace_index, AgentTracesORM.tool_name,
                        AgentTracesORM.input, AgentTracesORM.output, AgentTracesORM.status,
                        AgentTracesORM.latency_ms,
                    )
                    .where(AgentTracesORM.message_id.in_(ids))
                    .order_by(AgentTracesORM.message_id, AgentTracesORM.trace_index)
                )).fetchall()
                for t in trace_rows:
                    traces_by_msg.setdefault(t.message_id, []).append({
                        "trace_index": t.trace_index,
                        "tool_name": t.tool_name,
                        "input": t.input,
                        "output": t.output,
                        "status": t.status,
                        "latency_ms": t.latency_ms,
                    })
    except Exception as e:  # noqa: BLE001 — same contract as db.get_messages_ui: a read error is an empty thread, not a 500
        logger.warning("get_messages_with_traces failed conv_id=%s: %s", conversation_id, e)
        return []

    return [
        {
            "id": str(r.id),
            "role": r.role,
            "content": (r.content or {}).get("text", ""),
            "metadata": r.msg_metadata or {},
            "created_at": _iso(r.created_at),
            "tokens_used": r.tokens_used,
            "latency_ms": r.latency_ms,
            "traces": traces_by_msg.get(r.id, []),
        }
        for r in rows
        if r.role in ("user", "assistant")
    ]


# ─── Traces ───────────────────────────────────────────────────────────────────

async def list_traces(
    tenant_id: str,
    *,
    limit: int = 50,
    offset: int = 0,
    status: str | None = None,
    q: str | None = None,
) -> dict[str, Any]:
    if not client.is_connected():
        return {"items": [], "total": 0}

    tid = UUID(tenant_id)
    base = (
        select(
            AgentTracesORM.id,
            MessagesORM.conversation_id,
            AgentTracesORM.message_id,
            AgentTracesORM.tool_name,
            AgentTracesORM.status,
            AgentTracesORM.latency_ms,
            AgentTracesORM.created_at,
            AgentTracesORM.input,
            AgentTracesORM.output,
            ConversationsORM.user_ref,
        )
        .join(MessagesORM, MessagesORM.id == AgentTracesORM.message_id)
        .join(ConversationsORM, ConversationsORM.id == MessagesORM.conversation_id)
        .where(ConversationsORM.tenant_id == tid)
    )
    if status:
        base = base.where(AgentTracesORM.status == status)
    if q:
        pattern = _escape_like(q.strip())
        base = base.where(or_(
            AgentTracesORM.tool_name.ilike(f"%{pattern}%", escape="\\"),
            cast(MessagesORM.conversation_id, String).ilike(f"{pattern}%", escape="\\"),
            ConversationsORM.user_ref.ilike(f"%{pattern}%", escape="\\"),
        ))

    page = base.order_by(AgentTracesORM.created_at.desc(), AgentTracesORM.id.desc()).limit(limit).offset(offset)
    total_stmt = select(func.count()).select_from(base.subquery("filtered"))

    async with client.get_client(tenant_id=tenant_id) as session:
        rows = (await session.execute(page)).fetchall()
        total = int((await session.execute(total_stmt)).scalar_one())

    return {
        "items": [
            {
                "id": str(r.id),
                "conversation_id": str(r.conversation_id),
                "message_id": str(r.message_id),
                "tool_name": r.tool_name,
                "status": r.status,
                "latency_ms": r.latency_ms,
                "created_at": _iso(r.created_at),
                "input": r.input,
                "output": r.output,
                "user_ref": r.user_ref,
            }
            for r in rows
        ],
        "total": total,
    }


# ─── Platform metrics ─────────────────────────────────────────────────────────

def empty_series(days: int, today: dt.date | None = None) -> list[dict[str, Any]]:
    """One zeroed entry per day, oldest first, ending today (UTC)."""
    today = today or dt.datetime.now(dt.UTC).date()
    return [
        {
            "date": (today - dt.timedelta(days=offset)).isoformat(),
            "conversations": 0,
            "messages": 0,
            "tokens": 0,
            "avg_latency_ms": None,
            "errors": 0,
        }
        for offset in range(days - 1, -1, -1)
    ]


def merge_series(
    days: int,
    *,
    conversations: dict[str, int],
    messages: dict[str, dict[str, Any]],
    errors: dict[str, int],
    today: dt.date | None = None,
) -> list[dict[str, Any]]:
    series = empty_series(days, today)
    for entry in series:
        d = entry["date"]
        entry["conversations"] = int(conversations.get(d, 0))
        m = messages.get(d) or {}
        entry["messages"] = int(m.get("count", 0))
        entry["tokens"] = int(m.get("tokens", 0))
        lat = m.get("avg_latency_ms")
        entry["avg_latency_ms"] = round(float(lat), 1) if lat is not None else None
        entry["errors"] = int(errors.get(d, 0))
    return series


def empty_metrics(days: int) -> dict[str, Any]:
    return {
        "agents": {"total": 0, "published": 0},
        "conversations": {"total": 0, "last_24h": 0, "active": 0},
        "messages": {"total": 0, "last_24h": 0},
        "tokens": {"total": 0, "last_24h": 0},
        "latency_ms": {"avg": None, "p95": None},
        "error_rate": 0.0,
        "series": empty_series(days),
    }


async def platform_metrics(tenant_id: str, days: int = 7) -> dict[str, Any]:
    if not client.is_connected():
        return empty_metrics(days)

    tid = UUID(tenant_id)
    now = dt.datetime.now(dt.UTC)
    day_ago = now - dt.timedelta(hours=24)
    since = (now - dt.timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)

    conv_started = func.coalesce(ConversationsORM.started_at, ConversationsORM.inserted_at)
    tenant_conv_ids = select(ConversationsORM.id).where(ConversationsORM.tenant_id == tid)
    tenant_msgs = MessagesORM.conversation_id.in_(tenant_conv_ids)

    agents_stmt = select(
        func.count(AgentsORM.id),
        func.count(AgentsORM.id).filter(AgentsORM.published_version_id.isnot(None)),
    ).where(AgentsORM.tenant_id == tid, AgentsORM.deleted_at.is_(None))

    conv_stmt = select(
        func.count(ConversationsORM.id),
        func.count(ConversationsORM.id).filter(conv_started >= day_ago),
        func.count(ConversationsORM.id).filter(ConversationsORM.status == "active"),
    ).where(ConversationsORM.tenant_id == tid)

    msg_stmt = select(
        func.count(MessagesORM.id),
        func.count(MessagesORM.id).filter(MessagesORM.created_at >= day_ago),
        func.coalesce(func.sum(MessagesORM.tokens_used), 0),
        func.coalesce(func.sum(MessagesORM.tokens_used).filter(MessagesORM.created_at >= day_ago), 0),
        func.avg(MessagesORM.latency_ms).filter(MessagesORM.role == "assistant"),
        func.percentile_cont(0.95).within_group(MessagesORM.latency_ms).filter(
            and_(MessagesORM.role == "assistant", MessagesORM.latency_ms.isnot(None)),
        ),
    ).where(tenant_msgs)

    trace_base = (
        select(
            func.count(AgentTracesORM.id),
            func.count(AgentTracesORM.id).filter(AgentTracesORM.status != "success"),
        )
        .join(MessagesORM, MessagesORM.id == AgentTracesORM.message_id)
        .where(tenant_msgs)
    )

    conv_day = cast(conv_started, Date)
    conv_series_stmt = (
        select(conv_day, func.count(ConversationsORM.id))
        .where(ConversationsORM.tenant_id == tid, conv_started >= since)
        .group_by(conv_day)
    )
    msg_day = cast(MessagesORM.created_at, Date)
    msg_series_stmt = (
        select(
            msg_day,
            func.count(MessagesORM.id),
            func.coalesce(func.sum(MessagesORM.tokens_used), 0),
            func.avg(MessagesORM.latency_ms).filter(MessagesORM.role == "assistant"),
        )
        .where(tenant_msgs, MessagesORM.created_at >= since)
        .group_by(msg_day)
    )
    trace_day = cast(AgentTracesORM.created_at, Date)
    err_series_stmt = (
        select(trace_day, func.count(AgentTracesORM.id))
        .join(MessagesORM, MessagesORM.id == AgentTracesORM.message_id)
        .where(tenant_msgs, AgentTracesORM.status != "success", AgentTracesORM.created_at >= since)
        .group_by(trace_day)
    )

    async with client.get_client(tenant_id=tenant_id) as session:
        agents_total, agents_published = (await session.execute(agents_stmt)).one()
        conv_total, conv_24h, conv_active = (await session.execute(conv_stmt)).one()
        msg_total, msg_24h, tok_total, tok_24h, lat_avg, lat_p95 = (await session.execute(msg_stmt)).one()
        trace_total, trace_err = (await session.execute(trace_base)).one()
        conv_series = {str(d): int(n) for d, n in (await session.execute(conv_series_stmt)).fetchall()}
        msg_series = {
            str(d): {"count": int(n), "tokens": int(t or 0), "avg_latency_ms": lat}
            for d, n, t, lat in (await session.execute(msg_series_stmt)).fetchall()
        }
        err_series = {str(d): int(n) for d, n in (await session.execute(err_series_stmt)).fetchall()}

    return {
        "agents": {"total": int(agents_total or 0), "published": int(agents_published or 0)},
        "conversations": {"total": int(conv_total or 0), "last_24h": int(conv_24h or 0), "active": int(conv_active or 0)},
        "messages": {"total": int(msg_total or 0), "last_24h": int(msg_24h or 0)},
        "tokens": {"total": int(tok_total or 0), "last_24h": int(tok_24h or 0)},
        "latency_ms": {
            "avg": round(float(lat_avg), 1) if lat_avg is not None else None,
            "p95": round(float(lat_p95), 1) if lat_p95 is not None else None,
        },
        "error_rate": (float(trace_err) / float(trace_total)) if trace_total else 0.0,
        "series": merge_series(
            days, conversations=conv_series, messages=msg_series, errors=err_series, today=now.date(),
        ),
    }


# ─── Settings store ───────────────────────────────────────────────────────────

async def get_setting(tenant_id: str, key: str) -> dict[str, Any]:
    if not client.is_connected():
        return {"key": key, "value": None, "updated_at": None}
    tid = UUID(tenant_id)
    async with client.get_client(tenant_id=tenant_id) as session:
        row = (await session.execute(
            select(PlatformSettingsORM.value, PlatformSettingsORM.updated_at)
            .where(PlatformSettingsORM.tenant_id == tid, PlatformSettingsORM.key == key)
        )).first()
    if row is None:
        return {"key": key, "value": None, "updated_at": None}
    return {"key": key, "value": row.value, "updated_at": _iso(row.updated_at)}


async def put_setting(tenant_id: str, key: str, value: Any, updated_by: str | None) -> dict[str, Any]:
    if not client.is_connected():
        raise RuntimeError("database not connected")
    tid = UUID(tenant_id)
    stmt = (
        pg_insert(PlatformSettingsORM)
        .values(tenant_id=tid, key=key, value=value, updated_by=updated_by, updated_at=func.now())
        .on_conflict_do_update(
            constraint="uq_platform_settings_tenant_key",
            set_={"value": value, "updated_by": updated_by, "updated_at": func.now()},
        )
        .returning(PlatformSettingsORM.value, PlatformSettingsORM.updated_at)
    )
    async with client.get_client(tenant_id=tenant_id) as session:
        row = (await session.execute(stmt)).one()
        await session.commit()
    return {"key": key, "value": row.value, "updated_at": _iso(row.updated_at)}
