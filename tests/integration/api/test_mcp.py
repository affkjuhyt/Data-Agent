"""POST /mcp (UC-12-02) against PostgreSQL: client tokens, granted tools and spaces, quotas, and
ask_question recorded as an ordinary question of the client's owner (BR-48)."""

from __future__ import annotations

import datetime
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import httpx
import psycopg2
import pytest

from services.api.app import create_app
from services.api.config import Settings
from services.api.modules.integration import mcp

from .conftest import Seed

pytestmark = [pytest.mark.integration, pytest.mark.security]

NOW = datetime.datetime.now(datetime.UTC)


@dataclass
class World:
    org: uuid.UUID
    owner: uuid.UUID
    space: uuid.UUID
    other_space: uuid.UUID
    client: uuid.UUID
    keycloak_client_id: str


def build(
    seed: Seed, *, quota_per_minute: int = 60, owner_caps: tuple[str, ...] = ("ask",)
) -> World:
    o = seed.org()
    owner = seed.row(
        "org.app_user", org_id=o, external_id="kc-ac01", username="ac01", display_name="Phân tích"
    )
    role = seed.row(
        "org.role", org_id=o, code="AC01", name="Phân tích", capabilities=list(owner_caps)
    )
    space = seed.row("org.data_space", org_id=o, code="DS-TD", name="Tín dụng", steward_id=owner)
    other = seed.row("org.data_space", org_id=o, code="DS-TT", name="Thị trường", steward_id=owner)
    seed.row(
        "org.data_space_member",
        org_id=o,
        data_space_id=space,
        principal_type="user",
        principal_id=owner,
        role_id=role,
    )
    kc_id = f"kc-{uuid.uuid4().hex[:12]}"
    client = seed.row(
        "integration.api_client",
        org_id=o,
        name="Trợ lý",
        kind="mcp",
        owner_user_id=owner,
        keycloak_client_id=kc_id,
        secret_ref=f"tenants/{o}/clients/{kc_id}",
        tools=["ask_question", "get_metric_definition"],
        # An array literal: Seed passes lists as text[], which uuid[] does not take.
        data_space_ids=f"{{{space},{other}}}",
        quota_per_minute=quota_per_minute,
        expires_at=NOW + datetime.timedelta(days=30),
        owner_confirmed_at=NOW,
    )
    return World(o, owner, space, other, client, kc_id)


class _ClientTokens:
    def __init__(self, org_id: uuid.UUID) -> None:
        self.org_id = org_id

    def claims(self, token: str) -> dict[str, Any]:
        return {"sub": "service-account", "azp": token, "org_id": str(self.org_id)}


def mcp_client(w: World, keycloak_client_id: str | None = None) -> httpx.AsyncClient:
    app = create_app(Settings(), token_verifier=_ClientTokens(w.org))
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://api",
        headers={"Authorization": f"Bearer {keycloak_client_id or w.keycloak_client_id}"},
    )


def call(tool: str, **arguments: Any) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool, "arguments": arguments},
    }


@pytest.fixture
def audit_rows(ddl_server: dict[str, str], api_database: str) -> Iterator[Any]:
    conn = psycopg2.connect(ddl_server["dataagent_audit"])
    conn.autocommit = True

    def rows(org: uuid.UUID) -> list[tuple[str, str]]:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT action, outcome FROM audit.audit_log WHERE org_id = %s ORDER BY seq",
                (str(org),),
            )
            return cur.fetchall()

    yield rows
    conn.close()


@pytest.fixture(autouse=True)
def _short_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    # No worker-agent runs in the api suite: do not wait 20 s for an answer that will not come.
    monkeypatch.setattr(mcp, "ANSWER_WAIT_SECONDS", 0.2)


async def test_unknown_revoked_or_unconfirmed_client_is_401(seed: Seed) -> None:
    w = build(seed)
    async with mcp_client(w, "kc-nobody") as c:
        assert (
            await c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        ).status_code == 401
    # BR-48: granted in the owner's name but not yet confirmed by them.
    seed.conn.cursor().execute(
        "UPDATE integration.api_client SET status = 'pending_owner', owner_confirmed_at = NULL "
        "WHERE id = %s",
        (str(w.client),),
    )
    async with mcp_client(w) as c:
        r = await c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 401
    seed.conn.cursor().execute(
        "UPDATE integration.api_client SET status = 'revoked' WHERE id = %s", (str(w.client),)
    )
    async with mcp_client(w) as c:
        r = await c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 401  # AC-12-01-03: a token still in its lifetime is refused at once


@pytest.mark.req("AC-12-02-02")
async def test_ac_12_02_02_tools_list_has_only_the_granted_tools(seed: Seed) -> None:
    w = build(seed)
    async with mcp_client(w) as c:
        init = await c.post(
            "/mcp", json={"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {}}
        )
        r = await c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert init.json()["result"]["serverInfo"]["name"] == "data-agent"
    assert [t["name"] for t in r.json()["result"]["tools"]] == [
        "ask_question",
        "get_metric_definition",
    ]
    assert r.json()["result"]["tools"][0]["inputSchema"]["required"] == ["data_space", "question"]


@pytest.mark.req("AC-12-02-05")
async def test_ac_12_02_05_tool_or_space_not_granted_is_403_and_audited(
    seed: Seed, audit_rows: Any
) -> None:
    w = build(seed)
    async with mcp_client(w) as c:
        not_granted = await c.post(
            "/mcp", json=call("search_documents", data_space=str(w.space), query="x")
        )
        foreign = await c.post(
            "/mcp", json=call("ask_question", data_space=str(uuid.uuid4()), question="x")
        )
        # Granted to the client, but the owner is not a member of that space (BR-48).
        no_rights = await c.post(
            "/mcp", json=call("ask_question", data_space=str(w.other_space), question="x")
        )
    for r in (not_granted, foreign, no_rights):
        assert r.status_code == 403 and r.json()["code"] == "policy_denied"
        assert "schema" not in r.text and "qa." not in r.text
    assert audit_rows(w.org).count(("mcp.tools_call", "denied")) == 3


@pytest.mark.req("AC-12-02-04")
async def test_ac_12_02_04_over_quota_is_429_and_audited(seed: Seed, audit_rows: Any) -> None:
    w = build(seed, quota_per_minute=1)
    async with mcp_client(w) as c:
        first = await c.post("/mcp", json=call("get_metric_definition", metric_code="NPL_RATIO"))
        second = await c.post("/mcp", json=call("get_metric_definition", metric_code="NPL_RATIO"))
    assert first.status_code == 200
    assert second.status_code == 429 and second.json()["code"] == "quota_exceeded"
    assert 0 < int(second.headers["Retry-After"]) <= 60
    assert ("mcp.tools_call", "denied") in audit_rows(w.org)


async def test_ac_12_02_01_ask_question_is_an_ordinary_question_of_the_owner(seed: Seed) -> None:
    w = build(seed)
    async with mcp_client(w) as c:
        r = await c.post(
            "/mcp",
            json=call(
                "ask_question", data_space=str(w.space), question="NPL_RATIO theo vùng tháng 9/2026"
            ),
        )
    assert r.status_code == 200, r.text
    result = r.json()["result"]
    question_id = result["structuredContent"]["question_id"]
    assert (
        result["structuredContent"]["status"] == "received"
    )  # worker-agent is not part of this suite
    assert question_id in result["structuredContent"]["link"]
    cur = seed.conn.cursor()
    cur.execute(
        "SELECT c.user_id, c.channel, c.api_client_id, c.data_space_id FROM qa.question q "
        "JOIN qa.conversation c ON c.id = q.conversation_id WHERE q.id = %s",
        (question_id,),
    )
    assert cur.fetchone() == (w.owner, "mcp", w.client, w.space)
    cur.execute(
        "SELECT count(*) FROM qa.outbox WHERE aggregate_id = %s AND event_type = 'question.received'",
        (question_id,),
    )
    assert cur.fetchone()[0] == 1


async def test_protocol_errors_are_json_rpc_errors(seed: Seed) -> None:
    w = build(seed)
    async with mcp_client(w) as c:
        unknown = await c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "resources/list"})
        bad = await c.post("/mcp", json={"jsonrpc": "1.0", "id": 1, "method": "tools/list"})
        note = await c.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert unknown.json()["error"]["code"] == -32601
    assert bad.json()["error"]["code"] == -32600
    assert note.status_code == 202


async def test_own_identity_client_never_asks_for_its_owner(seed: Seed, audit_rows: Any) -> None:
    w = build(seed)
    # A grant written around the API's check: /mcp still drops the detail tools (BR-48).
    seed.conn.cursor().execute(
        "UPDATE integration.api_client SET identity_mode = 'own_identity' WHERE id = %s",
        (str(w.client),),
    )
    async with mcp_client(w) as c:
        listed = await c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        asked = await c.post(
            "/mcp", json=call("ask_question", data_space=str(w.space), question="x")
        )
    assert [t["name"] for t in listed.json()["result"]["tools"]] == ["get_metric_definition"]
    assert asked.status_code == 403 and asked.json()["code"] == "policy_denied"
    assert ("mcp.tools_call", "denied") in audit_rows(w.org)
