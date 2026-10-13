"""Lớp riêng lãnh đạo (UC-16-01 to UC-16-05) against PostgreSQL: the vault and its key wraps, theses,
sealed evidence and scores, the key access log, private memory, private questions and publications.

Each test builds its own organisation: two leaders holding exec_vault (each acts only on their own
vault), an AC05 administrator, an AC06 compliance officer, an analyst without exec_vault, and a
leader of another organisation. OpenBao Transit and the local-model channel are fakes on app.state:
the api never decrypts, so a fake key is all the routes see. Handovers (UC-16-06) are in
test_exec_handover.py.
"""

from __future__ import annotations

import base64
import datetime
import os
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest

from libs.dataagent_db import uuid7
from services.api.app import create_app
from services.api.config import Settings
from services.api.kernel.sse import Event
from services.api.modules.exec.ports import ModelUnavailable, PrivateQuestion, TransitKey

from .conftest import Seed

pytestmark = [pytest.mark.integration, pytest.mark.contract]

PEM = "-----BEGIN PUBLIC KEY-----\nMIIC...\n-----END PUBLIC KEY-----\n"
REFRESH_EVENT = "exec.thesis.evidence_refresh_requested"


def b64(n: int, fill: int = 1) -> str:
    return base64.b64encode(bytes([fill]) * n).decode()


def random_b64(n: int) -> str:
    return base64.b64encode(os.urandom(n)).decode()


def due(days: int = 2) -> datetime.datetime:
    """A whole UTC hour `days` ahead (R-35, and the CHECK's one day after commit)."""
    now = datetime.datetime.now(datetime.UTC).replace(minute=0, second=0, microsecond=0)
    return now + datetime.timedelta(days=days)


# ─── the organisation ───


@dataclass
class World:
    org: uuid.UUID
    space: uuid.UUID
    other_space: uuid.UUID
    users: dict[str, uuid.UUID]
    stranger_org: uuid.UUID
    seed: Seed
    tag: str
    # Token subject → organisation, so the stranger signs in to their own organisation.
    external: dict[str, uuid.UUID] = field(default_factory=dict)

    def headers(self, who: str, space: uuid.UUID | None = None) -> dict[str, str]:
        out = {"Authorization": f"Bearer kc-{who}-{self.tag}"}
        if space is not None:
            out["X-Data-Space"] = str(space)
        return out

    def events(self, aggregate_id: object) -> list[tuple[str, dict[str, Any]]]:
        with self.seed.conn.cursor() as cur:
            cur.execute(
                "SELECT event_type, payload FROM exec.outbox WHERE aggregate_id = %s"
                " ORDER BY created_at, id",
                (str(aggregate_id),),
            )
            return list(cur.fetchall())

    def one(self, sql: str, *params: object) -> Any:
        with self.seed.conn.cursor() as cur:
            cur.execute(sql, [str(p) if isinstance(p, uuid.UUID) else p for p in params])
            row = cur.fetchone()
            return row[0] if row else None

    def execute(self, sql: str, *params: object) -> None:
        with self.seed.conn.cursor() as cur:
            cur.execute(sql, [str(p) if isinstance(p, uuid.UUID) else p for p in params])


def _user(
    seed: Seed,
    world_external: dict[str, uuid.UUID],
    org: uuid.UUID,
    name: str,
    role: uuid.UUID,
    space: uuid.UUID | None,
) -> uuid.UUID:
    user = seed.row(
        "org.app_user",
        org_id=org,
        external_id=f"kc-{name}",
        username=name,
        display_name=name,
        status="active",
    )
    seed.row("org.user_role", org_id=org, user_id=user, role_id=role, source="manual")
    if space is not None:
        seed.row(
            "org.data_space_member",
            org_id=org,
            data_space_id=space,
            principal_type="user",
            principal_id=user,
            role_id=role,
        )
    world_external[f"kc-{name}"] = org
    return user


def build_world(seed: Seed) -> World:
    org = seed.org()
    tag = org.hex[:6]
    external: dict[str, uuid.UUID] = {}

    def role(code: str, caps: list[str], in_org: uuid.UUID = org) -> uuid.UUID:
        return seed.row("org.role", org_id=in_org, code=code, name=code, capabilities=caps)

    leader_role = role("EXEC", ["ask", "exec_vault"])
    admin_role = role("AC05", ["ask", "admin"])
    compliance_role = role("AC06", ["ask", "compliance"])
    # Both AC05 and AC06 in one person: still only one of the two people BR-195 needs.
    boss_role = role("BOSS", ["ask", "admin", "compliance", "exec_vault"])
    ask_role = role("ASK", ["ask"])
    keeper = seed.row(
        "org.app_user",
        org_id=org,
        external_id=f"kc-keeper-{tag}",
        username="keeper",
        display_name="Keeper",
    )
    space = seed.row(
        "org.data_space", org_id=org, code="DS-FIN", name="Tài chính", steward_id=keeper
    )
    other = seed.row(
        "org.data_space", org_id=org, code="DS-NV", name="Nguồn vốn", steward_id=keeper
    )
    users = {
        "leader": _user(seed, external, org, f"leader-{tag}", leader_role, space),
        "leader2": _user(seed, external, org, f"leader2-{tag}", leader_role, space),
        "admin": _user(seed, external, org, f"admin-{tag}", admin_role, None),
        "compliance": _user(seed, external, org, f"compliance-{tag}", compliance_role, None),
        "compliance2": _user(seed, external, org, f"compliance2-{tag}", compliance_role, None),
        "boss": _user(seed, external, org, f"boss-{tag}", boss_role, None),
        "analyst": _user(seed, external, org, f"analyst-{tag}", ask_role, space),
    }
    stranger_org = seed.org()
    stranger_role = role("EXEC", ["ask", "exec_vault", "admin", "compliance"], stranger_org)
    users["stranger"] = _user(seed, external, stranger_org, f"stranger-{tag}", stranger_role, None)
    # The exec triggers that check other rows are SECURITY DEFINER and so under org_isolation even
    # for the admin's inserts: the seed connection names the organisation like the api does.
    with seed.conn.cursor() as cur:
        cur.execute("SELECT set_config('app.org_id', %s, false)", (str(org),))
    return World(org, space, other, users, stranger_org, seed, tag, external)


@pytest.fixture
def w(seed: Seed) -> World:
    return build_world(seed)


class Tokens:
    def __init__(self, w: World) -> None:
        self.w = w

    def claims(self, token: str) -> dict[str, Any]:
        return {"sub": token, "org_id": str(self.w.external.get(token, self.w.org))}


class FakeTransit:
    """OpenBao Transit as the api sees it: a named key with a version and a public half."""

    def __init__(self) -> None:
        self.version = 1
        self.created: list[str] = []

    async def create(self, name: str) -> TransitKey:
        self.created.append(name)
        return TransitKey(public_key_pem=PEM, version=self.version)

    async def public_key(self, name: str, version: int) -> TransitKey:
        return TransitKey(public_key_pem=PEM, version=version)


class FakeQuestions:
    """The ephemeral channel of UC-16-02: keeps questions in memory only, answers for the asker."""

    def __init__(self) -> None:
        self.asked: list[PrivateQuestion] = []
        self.busy = False

    async def submit(self, question: PrivateQuestion) -> None:
        if self.busy:
            raise ModelUnavailable
        self.asked.append(question)

    async def events(
        self, *, org_id: uuid.UUID, owner_id: uuid.UUID, question_id: uuid.UUID
    ) -> list[Event] | None:
        for q in self.asked:
            if (q.id, q.org_id, q.owner_id) == (question_id, org_id, owner_id):
                return [
                    Event("0", "understood", {"question_id": str(q.id)}),
                    Event("1", "answer", {"text": "Cách bình quân thị trường 0,2 điểm"}),
                    Event("2", "done", {}),
                ]
        return None


@dataclass
class Ports:
    transit: FakeTransit | None
    questions: FakeQuestions | None


def client(w: World, *, transit: bool = True, questions: bool = True) -> httpx.AsyncClient:
    app = create_app(Settings(), token_verifier=Tokens(w))
    app.state.exec_transit = FakeTransit() if transit else None
    app.state.exec_private_questions = FakeQuestions() if questions else None
    api = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api")
    api.ports = Ports(app.state.exec_transit, app.state.exec_private_questions)  # type: ignore[attr-defined]
    return api


# ─── bodies ───


def passkey_wrap() -> dict[str, Any]:
    return {
        "kind": "passkey_prf",
        "credential_id": random_b64(16),
        "label": "iPhone",
        "kdf": "hkdf_sha256",
        "kdf_salt": random_b64(32),
        "wrapped_vmk": random_b64(72),
    }


def hardware_wrap() -> dict[str, Any]:
    return {**passkey_wrap(), "kind": "hardware_key", "label": "YubiKey két sắt"}


def recovery_wrap() -> dict[str, Any]:
    return {
        "kind": "recovery_code",
        "label": "Mã giấy 10/2026",
        "kdf": "argon2id",
        "kdf_salt": random_b64(32),
        "wrapped_vmk": random_b64(72),
    }


def vault_body(**extra: Any) -> dict[str, Any]:
    body = {
        "box_public_key": random_b64(32),
        "box_private_key_ct": random_b64(72),
        "key_wraps": [passkey_wrap(), recovery_wrap()],
        "exit_policy": "seal",
    }
    body.update(extra)
    return body


def thesis_body(**extra: Any) -> dict[str, Any]:
    body = {
        # The device numbers what it encrypts (the id is in the AAD).
        "id": str(uuid7()),
        "content_ct": random_b64(120),
        "wrapped_dek": random_b64(72),
        "content_version": 1,
        "cond_ct": random_b64(512),
        "cond_key_version": 1,
        "due_at": due().isoformat(),
    }
    body.update(extra)
    return body


def memory_body(**extra: Any) -> dict[str, Any]:
    body = {
        "id": str(uuid7()),
        "kind": "priority",
        "content_ct": random_b64(80),
        "wrapped_dek": random_b64(72),
        "content_version": 1,
    }
    body.update(extra)
    return body


def publication_body(w: World, **extra: Any) -> dict[str, Any]:
    body = {
        "title": "Giá trần E5RON92 kỳ điều hành",
        # No digit at all, not even in a product code (CHECK statement_public !~ '[0-9]').
        "statement_public": "Giá trần xăng E năm kỳ điều hành sẽ không thấp hơn ngưỡng dưới đây",
        "target_data_space_id": str(w.space),
        "attribution": "role_only",
        "attribution_label": "Thành viên Ban điều hành",
        "ref_kind": "data_product",
        "metric_ref": "gia_xang_dau_trong_nuoc",
        "scope": {"product": "E5RON92"},
        "period_label": "2026-10-22",
        "comparator": "gte",
        "threshold": "20000",
        "unit": "VND/l",
    }
    body.update(extra)
    return body


async def make_vault(api: httpx.AsyncClient, w: World, name: str, **extra: Any) -> dict[str, Any]:
    made = await api.post("/v1/exec/vault", json=vault_body(**extra), headers=w.headers(name))
    assert made.status_code == 201, made.text
    out: dict[str, Any] = made.json()
    return out


async def make_thesis(api: httpx.AsyncClient, w: World, name: str, **extra: Any) -> dict[str, Any]:
    made = await api.post("/v1/exec/theses", json=thesis_body(**extra), headers=w.headers(name))
    assert made.status_code == 201, made.text
    out: dict[str, Any] = made.json()
    return out


def seed_thesis(
    w: World,
    owner: uuid.UUID,
    vault: uuid.UUID,
    *,
    due_at: datetime.datetime,
    committed_at: datetime.datetime | None = None,
) -> uuid.UUID:
    """A thesis written as the admin, for states the api cannot reach (past due, scored)."""
    values: dict[str, Any] = {
        "org_id": w.org,
        "vault_id": vault,
        "owner_id": owner,
        "content_ct": os.urandom(60),
        "wrapped_dek": os.urandom(72),
        "cond_ct": os.urandom(512),
        "cond_key_version": 1,
        "due_at": due_at,
    }
    if committed_at is not None:
        values["committed_at"] = committed_at
    return w.seed.row("exec.thesis", **values)


def seed_batch(w: World, thesis: uuid.UUID, owner: uuid.UUID, batch_no: int) -> None:
    """One evidence batch in one transaction: the deferred trigger wants both sides (BR-190)."""
    with w.seed.conn.cursor() as cur:
        cur.execute("BEGIN")
        for side in ("support", "against"):
            cur.execute(
                "INSERT INTO exec.thesis_evidence (id, org_id, thesis_id, owner_id, batch_no, side,"
                " item_kind, strength, freshness_ok, sealed_ct, agent_run_id)"
                " VALUES (%s, %s, %s, %s, %s, %s, 'evidence', 'moderate', true, %s, %s)",
                (
                    str(uuid.uuid4()),
                    str(w.org),
                    str(thesis),
                    str(owner),
                    batch_no,
                    side,
                    os.urandom(60),
                    str(uuid.uuid4()),
                ),
            )
        cur.execute("COMMIT")


def seed_decrypt(w: World, thesis: uuid.UUID, vault: uuid.UUID, owner: uuid.UUID) -> None:
    w.seed.row(
        "exec.key_access_log",
        org_id=w.org,
        vault_id=vault,
        owner_id=owner,
        thesis_id=thesis,
        key_kind="condition_transit",
        action="decrypt_condition",
        purpose="evidence_refresh",
        actor_kind="workflow",
        actor_ref="exec-thesis/activity-decrypt",
        correlation_id=uuid.uuid4(),
    )


# ─── vault and key wraps ───


async def test_leader_creates_a_vault_with_two_wraps_one_offline(w: World) -> None:
    async with client(w) as api:
        made = await make_vault(api, w, "leader")
        assert made["owner_id"] == str(w.users["leader"]) and made["status"] == "active"
        assert made["transit_key_name"] == f"exec-cond-{w.org}-{w.users['leader']}"
        assert made["transit_public_key"] == PEM and made["transit_key_version"] == 1
        assert {k["kind"] for k in made["key_wraps"]} == {"passkey_prf", "recovery_code"}
        assert api.ports.transit.created == [made["transit_key_name"]]  # type: ignore[attr-defined]
        assert (
            w.one("SELECT count(*) FROM exec.vault_key_wrap WHERE vault_id = %s", made["id"]) == 2
        )
        # The event carries ids and states, never a key (BR-189).
        events = w.events(made["id"])
        assert [e[0] for e in events] == ["exec.vault.created"]
        assert set(events[0][1]) == {"vault_id", "owner_id", "status", "exit_policy"}

        again = await api.post("/v1/exec/vault", json=vault_body(), headers=w.headers("leader"))
        assert again.status_code == 409 and again.json()["code"] == "conflict"

        fetched = await api.get("/v1/exec/vault", headers=w.headers("leader"))
        assert fetched.status_code == 200, fetched.text
        assert fetched.json()["id"] == made["id"] and len(fetched.json()["key_wraps"]) == 2
        assert fetched.json()["transit_public_key"] is None

        # exec_vault opens only the caller's own vault: the other leader has none.
        other = await api.get("/v1/exec/vault", headers=w.headers("leader2"))
        assert other.status_code == 404
        denied = await api.get("/v1/exec/vault", headers=w.headers("analyst"))
        assert denied.status_code == 403
        admin = await api.get("/v1/exec/vault", headers=w.headers("admin"))
        assert admin.status_code == 403


@pytest.mark.req("AC-16-01-06")
async def test_a_vault_needs_two_wraps_one_of_them_offline(w: World) -> None:
    async with client(w) as api:
        h = w.headers("leader")
        one = await api.post(
            "/v1/exec/vault", json=vault_body(key_wraps=[recovery_wrap()]), headers=h
        )
        assert one.status_code == 422 and one.json()["code"] == "business_rule_violated"
        online = await api.post(
            "/v1/exec/vault", json=vault_body(key_wraps=[passkey_wrap(), passkey_wrap()]), headers=h
        )
        assert online.status_code == 422 and online.json()["code"] == "business_rule_violated"
        # A recovery code has no WebAuthn credential: the CHECK refuses one.
        bad = await api.post(
            "/v1/exec/vault",
            json=vault_body(
                key_wraps=[passkey_wrap(), {**recovery_wrap(), "credential_id": b64(8)}]
            ),
            headers=h,
        )
        assert bad.status_code == 422, bad.text
        assert w.one("SELECT count(*) FROM exec.vault WHERE org_id = %s", w.org) == 0
    async with client(w, transit=False) as api:
        down = await api.post("/v1/exec/vault", json=vault_body(), headers=h)
        assert down.status_code == 503 and down.json()["code"] == "source_unavailable"


async def test_key_wraps_are_added_and_revoked_down_to_two_with_one_offline(w: World) -> None:
    async with client(w) as api:
        h = w.headers("leader")
        vault = await make_vault(api, w, "leader")
        await make_vault(api, w, "leader2")
        passkey = next(k for k in vault["key_wraps"] if k["kind"] == "passkey_prf")
        recovery = next(k for k in vault["key_wraps"] if k["kind"] == "recovery_code")

        added = await api.post("/v1/exec/vault/key-wraps", json=hardware_wrap(), headers=h)
        assert added.status_code == 201, added.text
        hardware = added.json()
        assert hardware["vault_id"] == vault["id"] and hardware["revoked_at"] is None
        assert [e[0] for e in w.events(hardware["id"])] == ["exec.vault_key_wrap.added"]

        # Another leader cannot revoke this vault's wrap: it does not exist for them.
        foreign = await api.delete(
            f"/v1/exec/vault/key-wraps/{passkey['id']}", headers=w.headers("leader2")
        )
        assert foreign.status_code == 404
        denied = await api.delete(
            f"/v1/exec/vault/key-wraps/{passkey['id']}", headers=w.headers("analyst")
        )
        assert denied.status_code == 403

        gone = await api.delete(f"/v1/exec/vault/key-wraps/{passkey['id']}", headers=h)
        assert gone.status_code == 204, gone.text
        assert w.one("SELECT revoked_at FROM exec.vault_key_wrap WHERE id = %s", passkey["id"])
        assert [e[0] for e in w.events(passkey["id"])] == ["exec.vault_key_wrap.revoked"]
        twice = await api.delete(f"/v1/exec/vault/key-wraps/{passkey['id']}", headers=h)
        assert twice.status_code == 404

        # recovery + hardware left: revoking either leaves one wrap (BR-189).
        last = await api.delete(f"/v1/exec/vault/key-wraps/{recovery['id']}", headers=h)
        assert last.status_code == 422 and last.json()["code"] == "business_rule_violated"
        assert (
            w.one("SELECT revoked_at FROM exec.vault_key_wrap WHERE id = %s", recovery["id"])
            is None
        )

        live = (await api.get("/v1/exec/vault", headers=h)).json()["key_wraps"]
        assert {k["id"] for k in live} == {recovery["id"], hardware["id"]}

        no_vault = await api.post(
            "/v1/exec/vault/key-wraps", json=hardware_wrap(), headers=w.headers("boss")
        )
        assert no_vault.status_code == 404


async def test_transit_key_is_the_vaults_public_half(w: World) -> None:
    async with client(w) as api:
        none = await api.get("/v1/exec/transit-key", headers=w.headers("leader"))
        assert none.status_code == 404
        await make_vault(api, w, "leader")
        key = await api.get("/v1/exec/transit-key", headers=w.headers("leader"))
        assert key.status_code == 200, key.text
        assert key.json() == {
            "public_key_pem": PEM,
            "key_version": 1,
            "algorithm": "RSA-OAEP-SHA256",
        }
        denied = await api.get("/v1/exec/transit-key", headers=w.headers("analyst"))
        assert denied.status_code == 403
    async with client(w, transit=False) as api:
        down = await api.get("/v1/exec/transit-key", headers=w.headers("leader"))
        assert down.status_code == 503


# ─── theses ───


@pytest.mark.req("AC-16-01-01")
async def test_leader_commits_a_thesis_as_ciphertext_and_the_event_has_no_content(w: World) -> None:
    async with client(w) as api:
        h = w.headers("leader")
        no_vault = await api.post("/v1/exec/theses", json=thesis_body(), headers=h)
        assert no_vault.status_code == 409 and no_vault.json()["code"] == "invalid_state"
        vault = await make_vault(api, w, "leader")
        body = thesis_body()
        made = await api.post("/v1/exec/theses", json=body, headers=h)
        assert made.status_code == 201, made.text
        assert made.json()["id"] == body["id"]
        # The id is the device's: not a UUIDv7, or one already used, is refused.
        v4 = await api.post("/v1/exec/theses", json=thesis_body(id=str(uuid.uuid4())), headers=h)
        assert v4.status_code == 422, v4.text
        twice = await api.post("/v1/exec/theses", json=thesis_body(id=body["id"]), headers=h)
        assert twice.status_code == 409, twice.text
        assert made.status_code == 201, made.text
        thesis = made.json()
        assert thesis["status"] == "open" and thesis["evidence_state"] == "pending"
        assert thesis["vault_id"] == vault["id"] and thesis["owner_id"] == str(w.users["leader"])
        assert thesis["content_ct"] == body["content_ct"]
        assert thesis["workflow_id"] == f"exec-thesis-{thesis['id']}"
        # cond_ct is only the workflow's to decrypt: never returned (A3).
        assert "cond_ct" not in thesis
        stored = w.one("SELECT cond_ct FROM exec.thesis WHERE id = %s", thesis["id"])
        assert bytes(stored) == base64.b64decode(body["cond_ct"])
        events = w.events(thesis["id"])
        assert [e[0] for e in events] == ["exec.thesis.committed"]
        payload = events[0][1]
        assert set(payload) == {
            "thesis_id",
            "vault_id",
            "owner_id",
            "due_at",
            "workflow_id",
            "supersedes_id",
            "status",
        }
        assert body["content_ct"] not in str(payload) and body["cond_ct"] not in str(payload)

        listed = await api.get("/v1/exec/theses", headers=h)
        assert all("cond_ct" not in t for t in listed.json()["items"])
        denied = await api.post("/v1/exec/theses", json=thesis_body(), headers=w.headers("analyst"))
        assert denied.status_code == 403


@pytest.mark.req("AC-16-01-08")
async def test_a_condition_sealed_with_a_rotated_transit_key_is_a_conflict(w: World) -> None:
    async with client(w) as api:
        await make_vault(api, w, "leader")
        w.execute(
            "UPDATE exec.vault SET transit_key_version = 2 WHERE owner_id = %s", w.users["leader"]
        )
        old = await api.post(
            "/v1/exec/theses", json=thesis_body(cond_key_version=1), headers=w.headers("leader")
        )
        assert old.status_code == 409 and old.json()["code"] == "conflict"
        key = await api.get("/v1/exec/transit-key", headers=w.headers("leader"))
        assert key.json()["key_version"] == 2
        await make_thesis(api, w, "leader", cond_key_version=2)


@pytest.mark.req("AC-16-01-09")
async def test_due_at_must_be_a_whole_hour_at_least_a_day_ahead(w: World) -> None:
    async with client(w) as api:
        await make_vault(api, w, "leader")
        h = w.headers("leader")
        off_hour = await api.post(
            "/v1/exec/theses",
            json=thesis_body(due_at=(due() + datetime.timedelta(minutes=30)).isoformat()),
            headers=h,
        )
        assert off_hour.status_code == 422 and off_hour.json()["code"] == "business_rule_violated"
        too_soon = await api.post(
            "/v1/exec/theses",
            json=thesis_body(due_at=(due(0) + datetime.timedelta(hours=2)).isoformat()),
            headers=h,
        )
        assert too_soon.status_code == 422 and too_soon.json()["code"] == "business_rule_violated"
        assert w.one("SELECT count(*) FROM exec.thesis WHERE org_id = %s", w.org) == 0


@pytest.mark.req("AC-16-01-06")
async def test_a_vault_left_with_one_wrap_takes_no_thesis(w: World) -> None:
    # The state the api's own revocation refuses, made by the admin: the trigger still guards it.
    async with client(w) as api:
        vault = await make_vault(api, w, "leader")
        w.execute(
            "DELETE FROM exec.vault_key_wrap WHERE vault_id = %s AND kind = 'recovery_code'",
            vault["id"],
        )
        refused = await api.post("/v1/exec/theses", json=thesis_body(), headers=w.headers("leader"))
        assert refused.status_code == 422 and refused.json()["code"] == "business_rule_violated"
        memory = await api.post(
            "/v1/exec/memory-items", json=memory_body(), headers=w.headers("leader")
        )
        assert memory.status_code == 422 and memory.json()["code"] == "business_rule_violated"


@pytest.mark.req("AC-16-01-04")
async def test_a_changed_mind_is_a_new_thesis_superseding_the_old(w: World) -> None:
    async with client(w) as api:
        await make_vault(api, w, "leader")
        await make_vault(api, w, "leader2")
        old = await make_thesis(api, w, "leader")
        new = await make_thesis(api, w, "leader", supersedes_id=old["id"])
        assert new["supersedes_id"] == old["id"]
        kept = await api.get(f"/v1/exec/theses/{old['id']}", headers=w.headers("leader"))
        assert kept.json()["status"] == "open" and kept.json()["content_ct"] == old["content_ct"]
        # Another leader's thesis cannot be the one replaced (the FK alone does not see RLS).
        foreign = await api.post(
            "/v1/exec/theses",
            json=thesis_body(supersedes_id=old["id"]),
            headers=w.headers("leader2"),
        )
        assert foreign.status_code == 404


async def test_theses_list_and_detail_are_the_owners_only(w: World) -> None:
    async with client(w) as api:
        vault = await make_vault(api, w, "leader")
        await make_vault(api, w, "leader2")
        h = w.headers("leader")
        first = await make_thesis(api, w, "leader", due_at=due(2).isoformat())
        second = await make_thesis(api, w, "leader", due_at=due(5).isoformat())
        mine = await make_thesis(api, w, "leader2")
        await api.post(f"/v1/exec/theses/{second['id']}:withdraw", headers=h)

        listed = await api.get("/v1/exec/theses", headers=h)
        assert listed.status_code == 200, listed.text
        assert {t["id"] for t in listed.json()["items"]} == {first["id"], second["id"]}
        page = await api.get("/v1/exec/theses", params={"page_size": 1}, headers=h)
        assert len(page.json()["items"]) == 1 and page.json()["next_page_token"]
        rest = await api.get(
            "/v1/exec/theses",
            params={"page_size": 1, "page_token": page.json()["next_page_token"]},
            headers=h,
        )
        assert {page.json()["items"][0]["id"], rest.json()["items"][0]["id"]} == {
            first["id"],
            second["id"],
        }
        opened = await api.get("/v1/exec/theses", params={"status": "open"}, headers=h)
        assert [t["id"] for t in opened.json()["items"]] == [first["id"]]
        late = await api.get("/v1/exec/theses", params={"due_from": due(3).isoformat()}, headers=h)
        assert [t["id"] for t in late.json()["items"]] == [second["id"]]
        early = await api.get("/v1/exec/theses", params={"due_to": due(3).isoformat()}, headers=h)
        assert [t["id"] for t in early.json()["items"]] == [first["id"]]

        detail = await api.get(f"/v1/exec/theses/{first['id']}", headers=h)
        assert detail.status_code == 200
        assert detail.json()["evidence_batches"] == 0 and detail.json()["publication_id"] is None
        assert detail.json()["vault_id"] == vault["id"] and "cond_ct" not in detail.json()

        # Not theirs, not in their organisation, not their capability: 404, 404, 403.
        other = await api.get(f"/v1/exec/theses/{first['id']}", headers=w.headers("leader2"))
        assert other.status_code == 404
        stranger = await api.get(f"/v1/exec/theses/{first['id']}", headers=w.headers("stranger"))
        assert stranger.status_code == 404
        boss = await api.get(f"/v1/exec/theses/{first['id']}", headers=w.headers("boss"))
        assert boss.status_code == 404
        admin = await api.get(f"/v1/exec/theses/{first['id']}", headers=w.headers("admin"))
        assert admin.status_code == 403
        theirs = await api.get("/v1/exec/theses", headers=w.headers("leader2"))
        assert [t["id"] for t in theirs.json()["items"]] == [mine["id"]]


@pytest.mark.req("AC-16-01-05")
async def test_withdraw_before_due_only(w: World) -> None:
    async with client(w) as api:
        vault = await make_vault(api, w, "leader")
        await make_vault(api, w, "leader2")
        h = w.headers("leader")
        thesis = await make_thesis(api, w, "leader")
        foreign = await api.post(
            f"/v1/exec/theses/{thesis['id']}:withdraw", headers=w.headers("leader2")
        )
        assert foreign.status_code == 404
        done = await api.post(f"/v1/exec/theses/{thesis['id']}:withdraw", headers=h)
        assert done.status_code == 200, done.text
        assert done.json()["status"] == "withdrawn" and done.json()["withdrawn_at"]
        assert done.json()["content_ct"] == thesis["content_ct"]
        events = w.events(thesis["id"])
        assert [e[0] for e in events] == ["exec.thesis.committed", "exec.thesis.withdrawn"]
        assert events[1][1]["workflow_id"] == thesis["workflow_id"]
        twice = await api.post(f"/v1/exec/theses/{thesis['id']}:withdraw", headers=h)
        assert twice.status_code == 409 and twice.json()["code"] == "invalid_state"

        now = datetime.datetime.now(datetime.UTC).replace(minute=0, second=0, microsecond=0)
        past = seed_thesis(
            w,
            w.users["leader"],
            uuid.UUID(vault["id"]),
            due_at=now - datetime.timedelta(hours=1),
            committed_at=now - datetime.timedelta(days=3),
        )
        late = await api.post(f"/v1/exec/theses/{past}:withdraw", headers=h)
        assert late.status_code == 409 and late.json()["code"] == "invalid_state"


# ─── evidence and scores ───


async def test_evidence_shows_the_latest_batch_and_the_one_before(w: World) -> None:
    async with client(w) as api:
        await make_vault(api, w, "leader")
        await make_vault(api, w, "leader2")
        h = w.headers("leader")
        thesis = await make_thesis(api, w, "leader")
        empty = await api.get(f"/v1/exec/theses/{thesis['id']}/evidence", headers=h)
        assert empty.status_code == 200 and empty.json()["items"] == []
        tid = uuid.UUID(thesis["id"])
        for batch in (1, 2, 3):
            seed_batch(w, tid, w.users["leader"], batch)

        latest = await api.get(f"/v1/exec/theses/{thesis['id']}/evidence", headers=h)
        assert latest.status_code == 200, latest.text
        items = latest.json()["items"]
        assert sorted({i["batch_no"] for i in items}) == [2, 3] and len(items) == 4
        assert {i["side"] for i in items} == {"support", "against"}
        assert all(base64.b64decode(i["sealed_ct"]) for i in items)
        first = await api.get(
            f"/v1/exec/theses/{thesis['id']}/evidence", params={"batch_no": 1}, headers=h
        )
        assert {i["batch_no"] for i in first.json()["items"]} == {1}
        paged = await api.get(
            f"/v1/exec/theses/{thesis['id']}/evidence", params={"page_size": 3}, headers=h
        )
        more = await api.get(
            f"/v1/exec/theses/{thesis['id']}/evidence",
            params={"page_size": 3, "page_token": paged.json()["next_page_token"]},
            headers=h,
        )
        assert len(paged.json()["items"]) == 3 and len(more.json()["items"]) == 1
        assert {i["id"] for i in paged.json()["items"] + more.json()["items"]} == {
            i["id"] for i in items
        }

        detail = await api.get(f"/v1/exec/theses/{thesis['id']}", headers=h)
        assert detail.json()["evidence_batches"] == 3
        assert detail.json()["evidence_state"] == "two_sided"
        other = await api.get(
            f"/v1/exec/theses/{thesis['id']}/evidence", headers=w.headers("leader2")
        )
        assert other.status_code == 404


async def test_evidence_refresh_joins_the_pending_batch_within_the_hour(w: World) -> None:
    async with client(w) as api:
        await make_vault(api, w, "leader")
        await make_vault(api, w, "leader2")
        h = w.headers("leader")
        thesis = await make_thesis(api, w, "leader")
        asked = await api.post(f"/v1/exec/theses/{thesis['id']}/evidence:refresh", headers=h)
        assert asked.status_code == 202, asked.text
        run = asked.json()["id"]
        assert asked.json()["status"] == "queued"
        assert asked.headers["Location"] == f"/v1/runs/{run}"
        again = await api.post(f"/v1/exec/theses/{thesis['id']}/evidence:refresh", headers=h)
        assert again.status_code == 202 and again.json()["id"] == run
        refreshes = [e for e in w.events(thesis["id"]) if e[0] == REFRESH_EVENT]
        assert len(refreshes) == 1
        assert refreshes[0][1]["agent_run_id"] == run
        assert refreshes[0][1]["workflow_id"] == thesis["workflow_id"]

        foreign = await api.post(
            f"/v1/exec/theses/{thesis['id']}/evidence:refresh", headers=w.headers("leader2")
        )
        assert foreign.status_code == 404
        await api.post(f"/v1/exec/theses/{thesis['id']}:withdraw", headers=h)
        closed = await api.post(f"/v1/exec/theses/{thesis['id']}/evidence:refresh", headers=h)
        assert closed.status_code == 409 and closed.json()["code"] == "invalid_state"


@pytest.mark.req("AC-16-03-09")
async def test_three_decrypts_in_a_day_stop_the_refresh_and_the_fourth_is_not_logged(
    w: World,
) -> None:
    async with client(w) as api:
        vault = await make_vault(api, w, "leader")
        h = w.headers("leader")
        thesis = await make_thesis(api, w, "leader")
        tid, vid = uuid.UUID(thesis["id"]), uuid.UUID(vault["id"])
        for _ in range(4):
            seed_decrypt(w, tid, vid, w.users["leader"])
        # The fourth was not written: the trigger drops it and reports it (BR-192).
        assert (
            w.one("SELECT count(*) FROM exec.key_access_log WHERE thesis_id = %s", thesis["id"])
            == 3
        )
        assert "exec.key.decrypt_limit_exceeded" in [e[0] for e in w.events(thesis["id"])]
        limited = await api.post(f"/v1/exec/theses/{thesis['id']}/evidence:refresh", headers=h)
        assert limited.status_code == 429 and limited.json()["code"] == "rate_limited"
        assert REFRESH_EVENT not in [e[0] for e in w.events(thesis["id"])]

        log = await api.get(
            "/v1/exec/key-access-log", params={"thesis_id": thesis["id"]}, headers=h
        )
        assert log.status_code == 200
        assert [r["action"] for r in log.json()["items"]] == ["decrypt_condition"] * 3
        assert all(r["correlation_id"] for r in log.json()["items"])


async def test_scores_are_the_owners_and_carry_the_commit_time(w: World) -> None:
    async with client(w) as api:
        await make_vault(api, w, "leader")
        await make_vault(api, w, "leader2")
        h = w.headers("leader")
        thesis = await make_thesis(api, w, "leader")
        no_scores = await api.get(f"/v1/exec/theses/{thesis['id']}/scores", headers=h)
        assert no_scores.status_code == 200 and no_scores.json()["items"] == []
        # What the scoring workflow does, as the admin: scoring, then the final row.
        w.execute("UPDATE exec.thesis SET status = 'scoring' WHERE id = %s", thesis["id"])
        score = w.seed.row(
            "exec.thesis_score",
            org_id=w.org,
            thesis_id=uuid.UUID(thesis["id"]),
            owner_id=w.users["leader"],
            kind="final",
            outcome="incorrect",
            as_of=datetime.datetime.fromisoformat(thesis["due_at"]),
            sealed_ct=os.urandom(80),
            workflow_run_id="run-1",
        )
        mine = await api.get(f"/v1/exec/theses/{thesis['id']}/scores", headers=h)
        assert [s["id"] for s in mine.json()["items"]] == [str(score)]
        assert mine.json()["items"][0]["outcome"] == "incorrect"
        detail = await api.get(f"/v1/exec/theses/{thesis['id']}", headers=h)
        assert detail.json()["status"] == "scored"

        every = await api.get("/v1/exec/scores", headers=h)
        assert every.status_code == 200, every.text
        row = every.json()["items"][0]
        assert row["thesis_id"] == thesis["id"] and row["committed_at"] == thesis["committed_at"]
        later = await api.get(
            "/v1/exec/scores",
            params={
                "from": (
                    datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=1)
                ).isoformat()
            },
            headers=h,
        )
        assert later.json()["items"] == []
        theirs = await api.get("/v1/exec/scores", headers=w.headers("leader2"))
        assert theirs.json()["items"] == []
        foreign = await api.get(
            f"/v1/exec/theses/{thesis['id']}/scores", headers=w.headers("leader2")
        )
        assert foreign.status_code == 404
        denied = await api.get("/v1/exec/scores", headers=w.headers("analyst"))
        assert denied.status_code == 403


async def test_key_access_log_filters_and_is_the_owners_only(w: World) -> None:
    async with client(w) as api:
        vault = await make_vault(api, w, "leader")
        await make_vault(api, w, "leader2")
        h = w.headers("leader")
        a = await make_thesis(api, w, "leader")
        b = await make_thesis(api, w, "leader")
        seed_decrypt(w, uuid.UUID(a["id"]), uuid.UUID(vault["id"]), w.users["leader"])
        seed_decrypt(w, uuid.UUID(b["id"]), uuid.UUID(vault["id"]), w.users["leader"])
        all_rows = await api.get("/v1/exec/key-access-log", headers=h)
        assert len(all_rows.json()["items"]) == 2
        only_a = await api.get("/v1/exec/key-access-log", params={"thesis_id": a["id"]}, headers=h)
        assert [r["thesis_id"] for r in only_a.json()["items"]] == [a["id"]]
        none = await api.get("/v1/exec/key-access-log", params={"action": "shred"}, headers=h)
        assert none.json()["items"] == []
        future = await api.get(
            "/v1/exec/key-access-log",
            params={
                "from": (
                    datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=1)
                ).isoformat()
            },
            headers=h,
        )
        assert future.json()["items"] == []
        theirs = await api.get("/v1/exec/key-access-log", headers=w.headers("leader2"))
        assert theirs.json()["items"] == []
        bad = await api.get("/v1/exec/key-access-log", params={"action": "read"}, headers=h)
        assert bad.status_code == 422


# ─── private memory ───


@pytest.mark.req("AC-16-02-01")
async def test_memory_item_holds_only_ciphertext(w: World) -> None:
    async with client(w) as api:
        vault = await make_vault(api, w, "leader")
        await make_vault(api, w, "leader2")
        h = w.headers("leader")
        thesis = await make_thesis(api, w, "leader")
        body = memory_body(related_thesis_id=thesis["id"])
        made = await api.post("/v1/exec/memory-items", json=body, headers=h)
        assert made.status_code == 201, made.text
        item = made.json()
        assert item["vault_id"] == vault["id"] and item["kind"] == "priority"
        assert item["content_ct"] == body["content_ct"] and item["shredded_at"] is None
        events = w.events(item["id"])
        assert [e[0] for e in events] == ["exec.memory_item.created"]
        assert set(events[0][1]) == {"memory_item_id", "vault_id", "kind"}

        foreign = await api.post(
            "/v1/exec/memory-items",
            json=memory_body(related_thesis_id=thesis["id"]),
            headers=w.headers("leader2"),
        )
        assert foreign.status_code == 404
        no_vault = await api.post(
            "/v1/exec/memory-items", json=memory_body(), headers=w.headers("boss")
        )
        assert no_vault.status_code == 409 and no_vault.json()["code"] == "invalid_state"

        await api.post("/v1/exec/memory-items", json=memory_body(kind="note"), headers=h)
        listed = await api.get("/v1/exec/memory-items", headers=h)
        assert len(listed.json()["items"]) == 2
        notes = await api.get("/v1/exec/memory-items", params={"kind": "note"}, headers=h)
        assert [i["kind"] for i in notes.json()["items"]] == ["note"]
        theirs = await api.get("/v1/exec/memory-items", headers=w.headers("leader2"))
        assert theirs.json()["items"] == []
        denied = await api.get("/v1/exec/memory-items", headers=w.headers("analyst"))
        assert denied.status_code == 403


@pytest.mark.req("AC-16-02-05")
async def test_shred_drops_the_dek_and_the_ciphertext(w: World) -> None:
    async with client(w) as api:
        await make_vault(api, w, "leader")
        await make_vault(api, w, "leader2")
        h = w.headers("leader")
        item = (await api.post("/v1/exec/memory-items", json=memory_body(), headers=h)).json()
        foreign = await api.post(
            f"/v1/exec/memory-items/{item['id']}:shred", headers=w.headers("leader2")
        )
        assert foreign.status_code == 404
        shred = await api.post(f"/v1/exec/memory-items/{item['id']}:shred", headers=h)
        assert shred.status_code == 200, shred.text
        out = shred.json()
        assert out["content_ct"] is None and out["wrapped_dek"] is None and out["shredded_at"]
        assert w.one(
            "SELECT content_ct IS NULL AND wrapped_dek IS NULL FROM exec.memory_item WHERE id = %s",
            item["id"],
        )
        assert [e[0] for e in w.events(item["id"])][-1] == "exec.memory_item.shredded"
        twice = await api.post(f"/v1/exec/memory-items/{item['id']}:shred", headers=h)
        assert twice.status_code == 409 and twice.json()["code"] == "invalid_state"


# ─── private questions ───


@pytest.mark.req("AC-16-02-02")
async def test_private_question_leaves_no_row_and_streams_to_its_asker(w: World) -> None:
    async with client(w) as api:
        await make_vault(api, w, "leader")
        h = w.headers("leader")
        before = w.one("SELECT count(*) FROM exec.outbox WHERE org_id = %s", w.org)
        asked = await api.post(
            "/v1/exec/private-questions",
            json={
                "text": "Lãi suất 12 tháng của mình đang cách bình quân thị trường bao nhiêu?",
                "context_items": [{"text": "Giữ chi phí vốn dưới 4,8%"}],
            },
            headers=h,
        )
        assert asked.status_code == 202, asked.text
        qid = asked.json()["id"]
        assert asked.headers["Location"] == f"/v1/exec/private-questions/{qid}/stream"
        questions = api.ports.questions  # type: ignore[attr-defined]
        assert questions.asked[0].owner_id == w.users["leader"]
        assert questions.asked[0].context == ["Giữ chi phí vốn dưới 4,8%"]
        # BR-193: nothing in the database, the outbox included, and no qa.question.
        assert w.one("SELECT count(*) FROM exec.outbox WHERE org_id = %s", w.org) == before
        assert w.one("SELECT count(*) FROM qa.question WHERE org_id = %s", w.org) == 0

        stream = await api.get(f"/v1/exec/private-questions/{qid}/stream", headers=h)
        assert stream.status_code == 200, stream.text
        assert stream.headers["content-type"].startswith("text/event-stream")
        assert "event: understood" in stream.text and "event: done" in stream.text
        resumed = await api.get(
            f"/v1/exec/private-questions/{qid}/stream", headers={**h, "Last-Event-ID": "1"}
        )
        assert "event: answer" not in resumed.text and "event: done" in resumed.text
        bad_id = await api.get(
            f"/v1/exec/private-questions/{qid}/stream", headers={**h, "Last-Event-ID": "x"}
        )
        assert bad_id.status_code == 400

        # Only the asker: another leader gets the expired answer, as does an unknown id.
        other = await api.get(
            f"/v1/exec/private-questions/{qid}/stream", headers=w.headers("leader2")
        )
        assert other.status_code == 410 and other.json()["code"] == "gone"
        unknown = await api.get(f"/v1/exec/private-questions/{uuid.uuid4()}/stream", headers=h)
        assert unknown.status_code == 410
        denied = await api.post(
            "/v1/exec/private-questions", json={"text": "?"}, headers=w.headers("analyst")
        )
        assert denied.status_code == 403


@pytest.mark.req("AC-16-02-07")
async def test_private_question_limits_and_a_busy_local_model(w: World) -> None:
    async with client(w) as api:
        h = w.headers("leader")
        long = await api.post(
            "/v1/exec/private-questions",
            json={"text": "Hỏi", "context_items": [{"text": "x" * 9_000}, {"text": "y" * 9_000}]},
            headers=h,
        )
        assert long.status_code == 422
        many = await api.post(
            "/v1/exec/private-questions",
            json={"text": "Hỏi", "context_items": [{"text": "x"}] * 9},
            headers=h,
        )
        assert many.status_code == 422
        api.ports.questions.busy = True  # type: ignore[attr-defined]
        busy = await api.post("/v1/exec/private-questions", json={"text": "Hỏi"}, headers=h)
        assert busy.status_code == 503 and busy.json()["code"] == "model_unavailable"
    async with client(w, questions=False) as api:
        down = await api.post("/v1/exec/private-questions", json={"text": "Hỏi"}, headers=h)
        assert down.status_code == 503 and down.json()["code"] == "model_unavailable"
        gone = await api.get(f"/v1/exec/private-questions/{uuid.uuid4()}/stream", headers=h)
        assert gone.status_code == 410


# ─── publications ───


@pytest.mark.req("AC-16-05-01")
async def test_publication_goes_to_the_callers_data_space_for_review(w: World) -> None:
    async with client(w) as api:
        await make_vault(api, w, "leader")
        thesis = await make_thesis(api, w, "leader")
        h = w.headers("leader", w.space)
        made = await api.post(
            f"/v1/exec/theses/{thesis['id']}/publications", json=publication_body(w), headers=h
        )
        assert made.status_code == 202, made.text
        pid = made.json()["id"]
        assert made.json()["status"] == "pending_review"
        assert made.headers["Location"] == f"/v1/exec/publications/{pid}"
        assert w.one("SELECT status FROM exec.publication WHERE id = %s", pid) == "pending_review"
        assert (
            str(
                w.one(
                    "SELECT due_at = %s::timestamptz FROM exec.publication WHERE id = %s",
                    thesis["due_at"],
                    pid,
                )
            )
            == "True"
        )
        events = w.events(pid)
        assert [e[0] for e in events] == ["exec.publication.requested"]
        assert events[0][1]["target_data_space_id"] == str(w.space)
        assert events[0][1]["thesis_id"] == thesis["id"]

        fetched = await api.get(f"/v1/exec/publications/{pid}", headers=w.headers("leader"))
        assert fetched.status_code == 200
        assert fetched.json()["attribution"] == "role_only"
        assert fetched.json()["confidence_public"] is None
        assert fetched.json()["knowledge_page_id"] is None
        detail = await api.get(f"/v1/exec/theses/{thesis['id']}", headers=w.headers("leader"))
        assert detail.json()["publication_id"] == pid

        # One open publication per thesis.
        again = await api.post(
            f"/v1/exec/theses/{thesis['id']}/publications", json=publication_body(w), headers=h
        )
        assert again.status_code == 409 and again.json()["code"] == "conflict"
        other = await api.get(f"/v1/exec/publications/{pid}", headers=w.headers("leader2"))
        assert other.status_code == 404


async def test_publication_only_into_a_data_space_the_leader_belongs_to(w: World) -> None:
    async with client(w) as api:
        await make_vault(api, w, "leader")
        thesis = await make_thesis(api, w, "leader")
        url = f"/v1/exec/theses/{thesis['id']}/publications"
        # The body names a Data Space the header does not: refused, whatever the leader may see.
        mismatch = await api.post(
            url,
            json=publication_body(w, target_data_space_id=str(w.other_space)),
            headers=w.headers("leader", w.space),
        )
        assert mismatch.status_code == 422
        # Not a member of the other Data Space: it does not exist for them.
        not_member = await api.post(
            url,
            json=publication_body(w, target_data_space_id=str(w.other_space)),
            headers=w.headers("leader", w.other_space),
        )
        assert not_member.status_code == 404
        no_header = await api.post(url, json=publication_body(w), headers=w.headers("leader"))
        assert no_header.status_code == 400 and no_header.json()["code"] == "data_space_required"
        assert w.one("SELECT count(*) FROM exec.publication WHERE org_id = %s", w.org) == 0


@pytest.mark.req("AC-16-05-06", "AC-16-05-07", "AC-16-05-08")
async def test_publication_refusals(w: World) -> None:
    async with client(w) as api:
        await make_vault(api, w, "leader")
        await make_vault(api, w, "leader2")
        thesis = await make_thesis(api, w, "leader")
        h = w.headers("leader", w.space)
        url = f"/v1/exec/theses/{thesis['id']}/publications"
        digits = await api.post(
            url,
            json=publication_body(w, statement_public="Giá trần kỳ 22/10/2026 sẽ từ 20.000 đ/lít"),
            headers=h,
        )
        assert digits.status_code == 422 and digits.json()["code"] == "business_rule_violated"
        # Another leader cannot publish this leader's thesis (BR-194).
        foreign = await api.post(
            url, json=publication_body(w), headers=w.headers("leader2", w.space)
        )
        assert foreign.status_code == 404
        await api.post(f"/v1/exec/theses/{thesis['id']}:withdraw", headers=w.headers("leader"))
        withdrawn = await api.post(url, json=publication_body(w), headers=h)
        assert withdrawn.status_code == 409 and withdrawn.json()["code"] == "invalid_state"
        assert w.one("SELECT count(*) FROM exec.publication WHERE org_id = %s", w.org) == 0
        denied = await api.post(
            url, json=publication_body(w), headers=w.headers("analyst", w.space)
        )
        assert denied.status_code == 403


@pytest.mark.req("AC-16-05-05")
async def test_withdrawn_publication_emits_once(w: World) -> None:
    async with client(w) as api:
        await make_vault(api, w, "leader")
        await make_vault(api, w, "leader2")
        thesis = await make_thesis(api, w, "leader")
        made = await api.post(
            f"/v1/exec/theses/{thesis['id']}/publications",
            json=publication_body(w),
            headers=w.headers("leader", w.space),
        )
        pid = made.json()["id"]
        h = w.headers("leader")
        foreign = await api.post(
            f"/v1/exec/publications/{pid}:withdraw", headers=w.headers("leader2")
        )
        assert foreign.status_code == 404
        done = await api.post(f"/v1/exec/publications/{pid}:withdraw", headers=h)
        assert done.status_code == 200, done.text
        assert done.json()["status"] == "withdrawn" and done.json()["withdrawn_at"]
        events = [e for e in w.events(pid) if e[0] == "exec.publication.withdrawn"]
        assert len(events) == 1 and events[0][1]["publication_id"] == pid
        twice = await api.post(
            f"/v1/exec/publications/{pid}:withdraw", json={"reason": "đổi ý"}, headers=h
        )
        assert twice.status_code == 409 and twice.json()["code"] == "invalid_state"
        # The thesis itself is untouched and can be published again.
        kept = await api.get(f"/v1/exec/theses/{thesis['id']}", headers=h)
        assert kept.json()["status"] == "open" and kept.json()["publication_id"] is None
