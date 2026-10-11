"""POST /mcp (UC-12-02): the MCP server, JSON-RPC 2.0 over Streamable HTTP.

Who calls: an api_client, with a Keycloak client-credentials token (ADR-30). The token's `azp`
names the Keycloak client and its `org_id` (a claim mapper the ClientCredentials adapter sets)
the organisation; the api_client row then decides everything else, read on every call so a
revoked, paused or expired client is refused at once (AC-12-01-03), whatever the token says.

What a call may do: only the tools granted to the client, only in its Data Spaces, and with the
owner's rights when it acts for the owner (BR-48): the owner's capabilities in that space are
resolved like a signed-in request's, so the client never holds more than its owner. Quotas per
minute and per day are counted in integration.usage_counter; a call over quota answers 429 with
Retry-After and is audited as denied (AC-12-02-04). Tool calls run "như một câu hỏi bình thường":
ask_question records a qa question (qa.record_question) that worker-agent answers, and the
answer comes back here, at most 200 table rows (BR-49).

The other five tools need public reads from their modules (context, skill, period, document);
until those exist they answer an MCP tool error saying so, never a guess.
"""

from __future__ import annotations

import asyncio
import datetime
import os
import uuid
from dataclasses import dataclass
from typing import Annotated, Any

import jwt
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import func, select
from starlette.concurrency import run_in_threadpool

from libs.dataagent_db import scoped_session, uuid7
from libs.dataagent_db.models import integration as ig
from services.api.catalogue import endpoint
from services.api.kernel.auth import Principal, TokenVerifier
from services.api.kernel.problems import Problem
from services.api.modules import audit, org, qa
from services.api.modules.integration import rules

router = APIRouter()
ROLE = "svc_integration"
PROTOCOL_VERSION = "2025-03-26"
# BR-49: an MCP answer carries at most 200 rows; the rest is behind the link.
MAX_ROWS = 200
# How long a tools/call waits for worker-agent before answering "still running" with the link.
ANSWER_WAIT_SECONDS = float(os.environ.get("API_MCP_ANSWER_WAIT_SECONDS", "20"))
PUBLIC_URL_ENV = "API_PUBLIC_URL"

_bearer = HTTPBearer(auto_error=False)

# tab 1 "Công cụ MCP": inputs and what comes back, as tools/list publishes them.
TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "ask_question": {
        "description": "Hỏi bằng ngôn ngữ tự nhiên trong một Data Space; trả câu trả lời, bảng rút gọn, cách tính và đường dẫn mở trong Data Agent.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "data_space": {"type": "string", "description": "Mã Data Space (uuid)"},
                "question": {"type": "string", "minLength": 1, "maxLength": 4000},
                "as_of": {"type": "string", "format": "date"},
            },
            "required": ["data_space", "question"],
        },
    },
    "get_metric_definition": {
        "description": "Định nghĩa, phiên bản, người phụ trách và căn cứ của một chỉ số.",
        "inputSchema": {
            "type": "object",
            "properties": {"metric_code": {"type": "string"}},
            "required": ["metric_code"],
        },
    },
    "search_metrics": {
        "description": "Danh sách chỉ số trong quyền theo từ khoá.",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    "run_skill": {
        "description": "Chạy một kỹ năng; trả skill_run_id ngay, kết quả khi xong.",
        "inputSchema": {
            "type": "object",
            "properties": {"skill_code": {"type": "string"}, "params": {"type": "object"}},
            "required": ["skill_code"],
        },
    },
    "get_published_result": {
        "description": "Số đã nộp của một chỉ số trong một kỳ, mã báo cáo và mã băm.",
        "inputSchema": {
            "type": "object",
            "properties": {"metric_code": {"type": "string"}, "period": {"type": "string"}},
            "required": ["metric_code", "period"],
        },
    },
    "search_documents": {
        "description": "Đoạn văn bản kèm trang, mục, hiệu lực.",
        "inputSchema": {
            "type": "object",
            "properties": {"data_space": {"type": "string"}, "query": {"type": "string"}},
            "required": ["data_space", "query"],
        },
    },
}


@dataclass(frozen=True)
class McpClient:
    id: uuid.UUID
    org_id: uuid.UUID
    owner_user_id: uuid.UUID
    identity_mode: str
    tools: frozenset[str]
    data_space_ids: frozenset[uuid.UUID]
    quota_per_minute: int
    quota_per_day: int


def _unauthenticated(detail: str) -> Problem:
    return Problem(401, "unauthenticated", detail, headers={"WWW-Authenticate": "Bearer"})


async def mcp_client(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> McpClient:
    """The `client` check of A1.2: a Keycloak client token, an active api_client in its org."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthenticated("Missing bearer token.")
    verifier: TokenVerifier = request.app.state.token_verifier
    try:
        claims = await run_in_threadpool(verifier.claims, credentials.credentials)
        keycloak_client_id = str(claims["azp"])
        org_id = uuid.UUID(str(claims["org_id"]))
    except (jwt.PyJWTError, KeyError, ValueError) as exc:
        raise _unauthenticated("Not a client token of this platform.") from exc
    async with scoped_session(ROLE, org_id) as session:
        row = await session.scalar(
            select(ig.ApiClient).where(
                ig.ApiClient.org_id == org_id, ig.ApiClient.keycloak_client_id == keycloak_client_id
            )
        )
    now = datetime.datetime.now(datetime.UTC)
    if row is None or row.status != "active" or row.expires_at <= now:
        # Revoked, paused, expired or unknown: same answer, so the state does not leak.
        raise _unauthenticated("The client is not active.")
    tools = frozenset(row.tools)
    if row.identity_mode == "own_identity":
        # BR-48: a client on its own identity never acts for its owner, so only the tools without
        # detail data — enforced here too, not only when the grant is written.
        tools &= rules.OWN_IDENTITY_TOOLS
    return McpClient(
        id=row.id,
        org_id=row.org_id,
        owner_user_id=row.owner_user_id,
        identity_mode=row.identity_mode,
        tools=tools,
        data_space_ids=frozenset(row.data_space_ids),
        quota_per_minute=row.quota_per_minute,
        quota_per_day=row.quota_per_day,
    )


CurrentClient = Annotated[McpClient, Depends(mcp_client)]


def _result(rpc_id: Any, result: dict[str, Any]) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": rpc_id, "result": result})


def _rpc_error(rpc_id: Any, code: int, message: str) -> JSONResponse:
    return JSONResponse(
        {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}}
    )


def _tool_text(
    text: str, *, error: bool = False, structured: dict[str, Any] | None = None
) -> dict[str, Any]:
    out: dict[str, Any] = {"content": [{"type": "text", "text": text}], "isError": error}
    if structured is not None:
        out["structuredContent"] = structured
    return out


async def _count(client: McpClient, tool: str, space: uuid.UUID | None, field: str) -> None:
    """Add one to this minute's counter for (client, tool, space). The row is locked, so
    concurrent calls add up instead of overwriting each other."""
    minute = datetime.datetime.now(datetime.UTC).replace(second=0, microsecond=0)
    uc = ig.UsageCounter
    async with scoped_session(ROLE, client.org_id) as session:
        row = await session.scalar(
            select(uc)
            .where(
                uc.api_client_id == client.id,
                uc.tool == tool,
                uc.data_space_id.is_not_distinct_from(space),
                uc.minute == minute,
            )
            .with_for_update()
        )
        if row is None:
            row = ig.UsageCounter(
                id=uuid7(),
                org_id=client.org_id,
                api_client_id=client.id,
                tool=tool,
                data_space_id=space,
                minute=minute,
            )
            session.add(row)
        setattr(row, field, (getattr(row, field) or 0) + 1)


async def _over_quota(client: McpClient) -> int | None:
    """Seconds until a call is allowed again, or None when under both quotas."""
    now = datetime.datetime.now(datetime.UTC)
    minute = now.replace(second=0, microsecond=0)
    day = minute.replace(hour=0, minute=0)
    uc = ig.UsageCounter
    async with scoped_session(ROLE, client.org_id) as session:
        this_minute, today = (
            await session.execute(
                select(
                    func.coalesce(func.sum(uc.calls).filter(uc.minute == minute), 0),
                    func.coalesce(func.sum(uc.calls), 0),
                ).where(uc.api_client_id == client.id, uc.minute >= day)
            )
        ).one()
    if int(today) >= client.quota_per_day:
        return int((day + datetime.timedelta(days=1) - now).total_seconds()) + 1
    if int(this_minute) >= client.quota_per_minute:
        return 60 - now.second
    return None


def _space_of(client: McpClient, arguments: dict[str, Any]) -> uuid.UUID:
    try:
        space = uuid.UUID(str(arguments["data_space"]))
    except (KeyError, ValueError) as exc:
        raise Problem(422, "validation_failed", "data_space is a Data Space id.") from exc
    if space not in client.data_space_ids:
        raise Problem(403, "policy_denied", "The client is not granted this Data Space.")
    return space


async def _owner(client: McpClient, space: uuid.UUID) -> Principal:
    """BR-48: the owner's rights in that space, checked before anything is generated (inv. 13)."""
    capabilities = await org.capabilities_of(client.org_id, client.owner_user_id, space)
    if not capabilities or "ask" not in capabilities:
        raise Problem(403, "policy_denied", "The client's owner may not ask in this Data Space.")
    return Principal(
        user_id=client.owner_user_id,
        org_id=client.org_id,
        external_id=f"api_client:{client.id}",
        client_id=str(client.id),
        capabilities=capabilities,
        data_space_id=space,
    )


async def _ask(client: McpClient, arguments: dict[str, Any]) -> dict[str, Any]:
    space = _space_of(client, arguments)
    text = str(arguments.get("question") or "").strip()
    if not text or len(text) > 4000:
        raise Problem(422, "validation_failed", "question is 1 to 4000 characters.")
    as_of = None
    if arguments.get("as_of"):
        try:
            as_of = datetime.date.fromisoformat(str(arguments["as_of"]))
        except ValueError as exc:
            raise Problem(422, "validation_failed", "as_of is a date (YYYY-MM-DD).") from exc
    principal = await _owner(client, space)
    recorded = await qa.record_question(
        principal, space, text, channel="mcp", api_client_id=client.id, as_of=as_of
    )
    link = f"{os.environ.get(PUBLIC_URL_ENV, '').rstrip('/')}/analysis/ask?question={recorded.question_id}"
    deadline = asyncio.get_running_loop().time() + ANSWER_WAIT_SECONDS
    while True:
        status, answer = await qa.answer_of(principal, space, recorded.question_id)
        if (
            status in ("answered", "refused", "failed")
            or asyncio.get_running_loop().time() >= deadline
        ):
            break
        await asyncio.sleep(0.5)
    structured: dict[str, Any] = {
        "question_id": str(recorded.question_id),
        "status": status,
        "link": link,
    }
    if answer is None:
        return _tool_text(
            f"Câu hỏi đang được trả lời; mở {link} để xem khi xong.", structured=structured
        )
    figures = answer.figures if isinstance(answer.figures, list) else []
    structured.update(
        answer_id=str(answer.id),
        kind=answer.kind,
        figures=figures[:MAX_ROWS],
        truncated=len(figures) > MAX_ROWS,
    )
    return _tool_text(
        f"{answer.text}\n\nMở trong Data Agent: {link}",
        error=answer.kind == "refusal",
        structured=structured,
    )


@router.post(
    "/mcp",
    **endpoint("POST", "/mcp", operation_id="mcp", authenticate=Depends(mcp_client)),
)
async def mcp(request: Request, client: CurrentClient) -> JSONResponse:
    try:
        message = await request.json()
    except ValueError:
        return _rpc_error(None, -32700, "Parse error")
    if (
        not isinstance(message, dict)
        or message.get("jsonrpc") != "2.0"
        or not isinstance(message.get("method"), str)
    ):
        return _rpc_error(
            message.get("id") if isinstance(message, dict) else None, -32600, "Invalid Request"
        )
    rpc_id, method = message.get("id"), message["method"]
    params = message.get("params") or {}
    if rpc_id is None:
        # A notification (notifications/initialized, …): nothing to answer (Streamable HTTP: 202).
        return JSONResponse(None, status_code=202)
    if method == "initialize":
        return _result(
            rpc_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "serverInfo": {"name": "data-agent", "version": "1"},
                "capabilities": {"tools": {"listChanged": False}},
            },
        )
    if method == "ping":
        return _result(rpc_id, {})
    if method == "tools/list":
        tools = [
            {"name": name, **TOOL_SCHEMAS[name]}
            for name in sorted(client.tools)
            if name in TOOL_SCHEMAS
        ]
        return _result(rpc_id, {"tools": tools})
    if method != "tools/call":
        return _rpc_error(rpc_id, -32601, "Method not found")

    if not isinstance(params, dict):
        return _rpc_error(rpc_id, -32602, "Invalid params")
    name = params.get("name")
    arguments = params.get("arguments") or {}
    if name not in rules.MCP_TOOLS or not isinstance(arguments, dict):
        return _rpc_error(rpc_id, -32602, "Invalid params")
    space = arguments.get("data_space")
    space_id = uuid.UUID(space) if isinstance(space, str) and _is_uuid(space) else None
    if name not in client.tools:
        await _denied(client, name, space_id, "not_granted")
        raise Problem(403, "policy_denied", f"The client is not granted {name}.")
    retry_after = await _over_quota(client)
    if retry_after is not None:
        await _count(client, name, space_id, "blocked_by_quota")
        await audit.append(
            client.org_id,
            actor_kind="client",
            actor_id=client.id,
            on_behalf_of=client.owner_user_id,
            data_space_id=space_id,
            action="mcp.tools_call",
            outcome="denied",
            after={"tool": name, "reason": "quota_exceeded"},
        )
        raise Problem(
            429,
            "quota_exceeded",
            "The client's call quota is used up.",
            headers={"Retry-After": str(retry_after)},
        )
    await _count(client, name, space_id, "calls")
    try:
        if name == "ask_question":
            result = await _ask(client, arguments)
        else:
            result = _tool_text(f"{name} chưa được nối trên máy chủ này.", error=True)
    except Problem as problem:
        if problem.status == 403:
            await _denied(client, name, space_id, problem.code)
        raise
    await audit.append(
        client.org_id,
        actor_kind="client",
        actor_id=client.id,
        on_behalf_of=client.owner_user_id,
        data_space_id=space_id,
        action="mcp.tools_call",
        after={"tool": name, "status": (result.get("structuredContent") or {}).get("status")},
    )
    return _result(rpc_id, result)


async def _denied(client: McpClient, tool: str, space: uuid.UUID | None, reason: str) -> None:
    """A refused call is counted (UC-12-04: "lượt bị chặn do quyền") and audited as denied."""
    await _count(client, tool, space, "blocked_by_permission")
    await audit.append(
        client.org_id,
        actor_kind="client",
        actor_id=client.id,
        on_behalf_of=client.owner_user_id,
        data_space_id=space,
        action="mcp.tools_call",
        outcome="denied",
        after={"tool": tool, "reason": reason},
    )


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True
