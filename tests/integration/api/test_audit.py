"""Kiểm toán (UC-11-03) against PostgreSQL: the hash-chained audit log in dataagent_audit, read as
svc_audit under RLS. Each test builds its own organisation with a controller (AC06, audit_read)
and an analyst who lacks it."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import httpx
import psycopg2
import pytest

from services.api.app import create_app
from services.api.config import Settings

from .conftest import Seed

pytestmark = [pytest.mark.integration, pytest.mark.security]


@dataclass
class Org:
    id: uuid.UUID
    controller: uuid.UUID


class AuditDb:
    def __init__(self, dsn: str) -> None:
        self.conn = psycopg2.connect(dsn)
        self.conn.autocommit = True

    def log(self, org: uuid.UUID, action: str, actor: uuid.UUID | None = None) -> None:
        with self.conn.cursor() as cur:
            # seq, prev_hash and hash are set by the chain trigger.
            cur.execute(
                "INSERT INTO audit.audit_log (id, org_id, seq, actor_kind, actor_id, service, action, "
                "prev_hash, hash) VALUES (%s, %s, 0, 'user', %s, 'api', %s, '', '')",
                (str(uuid.uuid4()), str(org), str(actor) if actor else None, action),
            )

    def one(self, sql: str, *params: object) -> Any:
        with self.conn.cursor() as cur:
            cur.execute(sql, [str(p) if isinstance(p, uuid.UUID) else p for p in params])
            row = cur.fetchone()
        return row[0] if row and len(row) == 1 else row


@pytest.fixture
def audit_db(ddl_server: dict[str, str], api_database: str) -> Iterator[AuditDb]:
    db = AuditDb(ddl_server["dataagent_audit"])
    yield db
    db.conn.close()


def build(seed: Seed) -> Org:
    o = seed.org()
    controller = seed.row(
        "org.app_user", org_id=o, external_id="kc-ac06", username="ac06", display_name="Kiểm soát"
    )
    seed.row(
        "org.app_user", org_id=o, external_id="kc-ac01", username="ac01", display_name="Phân tích"
    )
    role = seed.row(
        "org.role",
        org_id=o,
        code="AC06",
        name="Kiểm soát",
        capabilities=["audit_read", "compliance"],
    )
    seed.row("org.user_role", org_id=o, user_id=controller, role_id=role, source="manual")
    return Org(o, controller)


class _Tokens:
    def __init__(self, org_id: uuid.UUID) -> None:
        self.org_id = org_id

    def claims(self, token: str) -> dict[str, Any]:
        return {"sub": token, "org_id": str(self.org_id)}


def client(org: Org, who: str = "kc-ac06") -> httpx.AsyncClient:
    app = create_app(Settings(), token_verifier=_Tokens(org.id))
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://api",
        headers={"Authorization": f"Bearer {who}"},
    )


async def test_search_filters_pages_and_is_itself_logged(seed: Seed, audit_db: AuditDb) -> None:
    org = build(seed)
    for action in ("source.created", "query.executed", "query.executed", "access.denied"):
        audit_db.log(org.id, action, org.controller)
    async with client(org) as c:
        r = await c.get("/v1/audit", params={"action": "query.executed", "page_size": 1})
        assert r.status_code == 200, r.text
        first = r.json()
        assert [i["action"] for i in first["items"]] == ["query.executed"]
        assert first["next_page_token"]
        r = await c.get(
            "/v1/audit", params={"action": "query.executed", "page_token": first["next_page_token"]}
        )
        second = r.json()
        assert [i["action"] for i in second["items"]] == ["query.executed"]
        assert second["items"][0]["seq"] < first["items"][0]["seq"]
        assert second["next_page_token"] == ""
    searched = audit_db.one(
        "SELECT count(*) FROM audit.audit_log WHERE org_id = %s AND action = 'audit.searched'",
        org.id,
    )
    assert searched == 2


async def test_audit_needs_audit_read(seed: Seed, audit_db: AuditDb) -> None:
    org = build(seed)
    async with client(org, "kc-ac01") as c:
        assert (await c.get("/v1/audit")).status_code == 403
        assert (await c.post("/v1/audit:verify", json={})).status_code == 403
        assert (await c.get("/v1/security-events")).status_code == 403


async def test_another_organisation_sees_none_of_it(seed: Seed, audit_db: AuditDb) -> None:
    mine, theirs = build(seed), build(seed)
    audit_db.log(theirs.id, "secret.read", theirs.controller)
    async with client(mine) as c:
        r = await c.get("/v1/audit", params={"action": "secret.read"})
    assert r.json()["items"] == []


async def test_intact_chain_verifies(seed: Seed, audit_db: AuditDb) -> None:
    org = build(seed)
    for _ in range(3):
        audit_db.log(org.id, "query.executed", org.controller)
    async with client(org) as c:
        r = await c.post("/v1/audit:verify", json={"from_seq": 1})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "broken_at_seq": None, "checked_rows": 3}


async def test_tampered_row_breaks_the_chain_and_raises_a_security_event(
    seed: Seed, audit_db: AuditDb
) -> None:
    org = build(seed)
    for _ in range(3):
        audit_db.log(org.id, "query.executed", org.controller)
    # Someone with database access rewrites history (svc_audit itself cannot UPDATE).
    with audit_db.conn.cursor() as cur:
        cur.execute(
            "UPDATE audit.audit_log SET action = 'nothing.happened' WHERE org_id = %s AND seq = 2",
            (str(org.id),),
        )
    async with client(org) as c:
        r = await c.post("/v1/audit:verify", json={})
        assert r.json()["ok"] is False and r.json()["broken_at_seq"] == 2
        events = (await c.get("/v1/security-events", params={"severity": "high"})).json()["items"]
    assert [(e["kind"], e["detail"]["broken_at_seq"]) for e in events] == [("hash_chain_broken", 2)]


async def test_br_138_export_is_queued_with_its_count_and_is_logged(
    seed: Seed, audit_db: AuditDb
) -> None:
    org = build(seed)
    for action in ("query.executed", "query.executed", "source.created"):
        audit_db.log(org.id, action, org.controller)
    async with client(org) as c:
        r = await c.post(
            "/v1/audit:export",
            json={"filters": {"action": "query.executed"}, "format": "jsonl"},
        )
        assert r.status_code == 202, r.text
        assert r.json()["status"] == "queued" and r.json()["row_count"] == 2
        job = await c.get(r.headers["Location"])
    assert job.status_code == 200, job.text
    assert job.json()["format"] == "jsonl" and "object_key" not in job.json()
    assert job.json()["filters"]["action"] == "query.executed"
    requested = audit_db.one(
        "SELECT count(*) FROM audit.audit_log WHERE org_id = %s AND action = 'audit.export_requested'",
        org.id,
    )
    assert requested == 1


async def test_br_138_a_large_export_waits_for_a_second_person(
    seed: Seed, audit_db: AuditDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    from services.api.modules.audit import routes

    monkeypatch.setattr(routes, "APPROVAL_ROWS", 1)
    org = build(seed)
    for _ in range(2):
        audit_db.log(org.id, "query.executed", org.controller)
    async with client(org) as c:
        r = await c.post("/v1/audit:export", json={"filters": {}})
    assert r.json()["status"] == "waiting_approval"
    # The table itself refuses to run it before someone else approves (BR-138).
    with pytest.raises(psycopg2.errors.CheckViolation), audit_db.conn.cursor() as cur:
        cur.execute(
            "UPDATE audit.export_job SET status = 'running', row_count = 200000 WHERE id = %s",
            (r.json()["id"],),
        )


async def test_export_needs_audit_read_and_stays_in_its_organisation(
    seed: Seed, audit_db: AuditDb
) -> None:
    mine, theirs = build(seed), build(seed)
    async with client(theirs) as c:
        made = await c.post("/v1/audit:export", json={"filters": {}})
    async with client(mine, "kc-ac01") as c:
        assert (await c.post("/v1/audit:export", json={"filters": {}})).status_code == 403
    async with client(mine) as c:
        assert (await c.get(made.headers["Location"])).status_code == 404
