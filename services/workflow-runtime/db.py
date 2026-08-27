import os
import json
import logging
from datetime import datetime, date
from decimal import Decimal
from enum import Enum
from typing import Any, Optional, cast
from uuid import UUID

try:
    import numpy as np
    _NUMPY = True
except ImportError:
    _NUMPY = False

from sqlalchemy import select, text

from libs.database_connector.postgres_connector import client
from libs.basemodel.services_databaseconnector.postgres_orm.workflow_orm import AgentsORM, MessagesORM, ConversationsORM
from libs.basemodel.services_databaseconnector.postgres_orm.auth_release_orm import MembersORM
from libs.basemodel.services_databaseconnector.postgres_model import (
    ConversationsInsert, MessagesInsert, AgentTracesInsert,
)


def _json_fallback(obj: Any) -> Any:
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, set):
        return list(obj)
    if _NUMPY:
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, (np.integer,)):
            return int(obj)
    return repr(obj)


def _json_dumps(obj: Any) -> str:
    return json.dumps(obj, default=_json_fallback)


_SENSITIVE_KEYS = frozenset({
    "api_key", "apikey", "api_secret", "secret", "secret_key",
    "token", "access_token", "refresh_token", "id_token",
    "password", "passwd", "pwd",
    "authorization", "auth",
    "email", "phone", "ssn", "national_id",
    "credit_card", "card_number",
})

_MAX_STR_LEN  = 2_000
_MAX_LIST_LEN = 20
_MAX_DICT_KEYS = 50


def _sanitize_trace(data: dict[str, Any]) -> dict[str, Any]:
    """Redact sensitive fields and limit size before persisting to AgentTraces."""
    if not isinstance(data, dict):
        return data

    result: dict[str, Any] = {}
    for k, v in list(data.items())[:_MAX_DICT_KEYS]:
        if k.lower() in _SENSITIVE_KEYS:
            result[k] = "[REDACTED]"
        elif isinstance(v, dict):
            result[k] = _sanitize_trace(v)
        elif isinstance(v, list):
            truncated = v[:_MAX_LIST_LEN]
            result[k] = [
                _sanitize_trace(i) if isinstance(i, dict) else i
                for i in truncated
            ]
            if len(v) > _MAX_LIST_LEN:
                result[k].append(f"... (+{len(v) - _MAX_LIST_LEN} items truncated)")
        elif isinstance(v, str) and len(v) > _MAX_STR_LEN:
            result[k] = v[:_MAX_STR_LEN] + f"... ({len(v) - _MAX_STR_LEN} chars truncated)"
        else:
            result[k] = v
    return result


logger = logging.getLogger(__name__)

_DEV_TENANT_ID    = os.getenv("DEV_TENANT_ID",    "00000000-0000-0000-0000-000000000001")
_DEV_AGENT_VER_ID = os.getenv("DEV_AGENT_VER_ID", "00000000-0000-0000-0000-000000000040")

_ROLE_WEIGHTS = {"admin": 3, "editor": 2, "viewer": 1}


async def init_db() -> None:
    url = os.environ.get("RUNTIME_DB_URL") or os.environ.get("DATABASE_URL")
    if not url:
        logger.warning("RUNTIME_DB_URL/DATABASE_URL not set — DB persistence disabled")
        return
    client.set_url(url)
    await client.open()
    logger.info("db engine ready")


async def close_db() -> None:
    await client.close()
    logger.info("db engine closed")


async def validate_agent_tenant(agent_id: str, tenant_id: str) -> bool:
    """Return True if agent_id belongs to tenant_id and is active."""
    if not client.is_connected():
        return True  # dev bypass
    async with client.get_client(tenant_id=tenant_id) as session:
        result = await session.execute(
            select(AgentsORM.id)
            .where(AgentsORM.id == UUID(agent_id))
            .where(AgentsORM.tenant_id == UUID(tenant_id))
            .where(AgentsORM.is_active.is_(True))
            .where(AgentsORM.deleted_at.is_(None))
        )
        return result.first() is not None


async def _has_role(tenant_id: str, user_ref: str, min_role: str) -> bool:
    """Return True if user_ref is an active member of tenant_id with at least min_role."""
    if user_ref == "anonymous":
        return True  # dev bypass
    async with client.get_client(tenant_id=tenant_id) as session:
        result = await session.execute(
            select(MembersORM.tenant_role)
            .where(MembersORM.tenant_id == UUID(tenant_id))
            .where(MembersORM.user_id == user_ref)
            .where(MembersORM.is_active.is_(True))
        )
        row = result.first()
    return bool(row) and _ROLE_WEIGHTS.get(row[0], 0) >= _ROLE_WEIGHTS.get(min_role, 0)


async def create_conversation(
    user_ref: str = "anonymous",
    channel: str = "web",
    tenant_id: str = _DEV_TENANT_ID,
) -> Optional[str]:
    if not client.is_connected():
        return None
    if not await _has_role(tenant_id, user_ref, "viewer"):
        logger.warning("RBAC deny create_conversation: user=%s tenant=%s", user_ref, tenant_id)
        return None
    result = await client.insert(ConversationsInsert(
        tenant_id=tenant_id,
        user_ref=user_ref,
        channel=channel,
        status="active",
    ))
    if result.code != 200:
        logger.error("create_conversation failed: %s", result.error)
        return None
    if result.data is None:  # code==200 always carries data in practice; guard for mypy
        return None
    return cast(Optional[str], result.data.get("id"))


async def get_messages(conversation_id: str, tenant_id: str = _DEV_TENANT_ID, limit: int = 20) -> list[dict[str, Any]]:
    """Load message history từ DB cho conversation đã có."""
    if not client.is_connected():
        return []
    try:
        async with client.get_client(tenant_id=tenant_id) as session:
            result = await session.execute(
                select(MessagesORM.role, MessagesORM.content)
                .where(MessagesORM.conversation_id == UUID(conversation_id))
                .order_by(MessagesORM.created_at.asc())
                .limit(limit)
            )
            rows = result.fetchall()
            return [
                {"role": row.role, "content": (row.content or {}).get("text", "")}
                for row in rows
                if row.role in ("user", "assistant")
            ]
    except Exception as e:
        logger.warning("get_messages failed conv_id=%s: %s", conversation_id, e)
        return []


async def get_last_slots(conversation_id: str, tenant_id: str = _DEV_TENANT_ID) -> Optional[dict[str, Any]]:
    """Analytical slots from the most recent assistant message of this thread.

    Durable fallback behind the Redis cache: a follow-up arriving after a cache
    eviction or a replica restart must still inherit the previous turn's filters,
    or "chỉ lấy miền Bắc" silently becomes a whole-country query — a wrong answer
    that looks exactly like a right one.

    Deliberately NOT folded into get_messages(): that result is fed to the model
    as chat history, and widening its shape to carry machine state would put
    slot JSON in the prompt.
    """
    if not client.is_connected() or not conversation_id:
        return None
    try:
        async with client.get_client(tenant_id=tenant_id) as session:
            result = await session.execute(
                select(MessagesORM.msg_metadata)
                .where(
                    MessagesORM.conversation_id == UUID(conversation_id),
                    MessagesORM.role == "assistant",
                )
                .order_by(MessagesORM.created_at.desc())
                .limit(1)
            )
            row = result.fetchone()
    except Exception as e:
        logger.warning("get_last_slots failed conv_id=%s: %s", conversation_id, e)
        return None

    if not row:
        return None
    slots = (row.msg_metadata or {}).get("slots")
    return slots if isinstance(slots, dict) else None


async def get_messages_ui(conversation_id: str, tenant_id: str = _DEV_TENANT_ID, limit: int = 50) -> list[dict[str, Any]]:
    """Load messages với metadata cho UI restoration."""
    if not client.is_connected():
        return []
    try:
        async with client.get_client(tenant_id=tenant_id) as session:
            result = await session.execute(
                select(MessagesORM.id, MessagesORM.role, MessagesORM.content, MessagesORM.msg_metadata)
                .where(MessagesORM.conversation_id == UUID(conversation_id))
                .order_by(MessagesORM.created_at.asc())
                .limit(limit)
            )
            rows = result.fetchall()
            return [
                {
                    "id":       str(row.id),
                    "role":     row.role,
                    "content":  (row.content or {}).get("text", ""),
                    "metadata": row.msg_metadata or {},
                }
                for row in rows
                if row.role in ("user", "assistant")
            ]
    except Exception as e:
        logger.warning("get_messages_ui failed conv_id=%s: %s", conversation_id, e)
        return []


async def save_message(
    conversation_id: str,
    role: str,
    content: str,
    tokens: int = 0,
    latency_ms: int = 0,
    metadata: Optional[dict[str, Any]] = None,
) -> Optional[str]:
    if not client.is_connected():
        return None
    result = await client.insert(MessagesInsert(
        conversation_id=conversation_id,
        role=role,
        content={"text": content},
        msg_metadata=metadata or {},
        tokens_used=tokens,
        latency_ms=latency_ms,
    ))
    if result.code != 200:
        logger.error("save_message failed: %s", result.error)
        return None
    if result.data is None:  # code==200 always carries data in practice; guard for mypy
        return None
    return cast(Optional[str], result.data.get("id"))


async def save_trace(
    message_id: str,
    trace_index: int,
    tool_name: str,
    input: dict[str, Any],
    output: dict[str, Any],
    status: str = "success",
    latency_ms: int = 0,
) -> None:
    if not client.is_connected():
        return
    result = await client.insert(AgentTracesInsert(
        message_id=message_id,
        trace_index=trace_index,
        tool_name=tool_name,
        input=_sanitize_trace(input),
        output=_sanitize_trace(output),
        status=status,
        latency_ms=latency_ms,
    ))
    if result.code == 409:
        pass  # duplicate (message_id, trace_index) — silently ignore
    elif result.code != 200:
        logger.error("save_trace failed: %s", result.error)
