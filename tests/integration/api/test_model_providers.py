"""Model providers and the routing log of Quản trị against PostgreSQL (UC-11-05 1b, BR-180,
BR-181, ADR-41): declaring on-site and frontier providers, AC06 approval by someone other than the
enabler, the test call, and the decisions log compliance reads. No network: DNS answers and the
provider probe are stubbed."""

from __future__ import annotations

import datetime
import decimal
import hashlib
import socket
import uuid
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from psycopg2.extras import Json

from services.api.app import create_app
from services.api.config import Settings
from services.api.modules.governance import routes

from .conftest import Seed

pytestmark = [pytest.mark.integration, pytest.mark.contract]

PUBLIC_HOST = "llm.frontier.example"
PRIVATE_HOST = "llm.internal.example"
SHA = hashlib.sha256(b"prompt").hexdigest()


@dataclass
class Org:
    org: uuid.UUID
    other_org: uuid.UUID
    users: dict[str, uuid.UUID]
    seed: Seed

    def headers(self, who: str) -> dict[str, str]:
        return {"Authorization": f"Bearer kc-{who}"}

    def events(self, aggregate_id: object) -> list[tuple[str, dict[str, Any]]]:
        with self.seed.conn.cursor() as cur:
            cur.execute(
                "SELECT event_type, payload FROM governance.outbox WHERE aggregate_id = %s"
                " ORDER BY created_at, event_type",
                (str(aggregate_id),),
            )
            return list(cur.fetchall())

    def one(self, sql: str, *params: object) -> Any:
        with self.seed.conn.cursor() as cur:
            cur.execute(sql, [str(p) if isinstance(p, uuid.UUID) else p for p in params])
            row = cur.fetchone()
            return row[0] if row else None

    def provider(self, org: uuid.UUID | None = None, **values: Any) -> uuid.UUID:
        base: dict[str, Any] = {
            "org_id": org or self.org,
            "code": f"onprem-{uuid.uuid4().hex[:6]}",
            "display_name": "vLLM tại chỗ",
            "kind": "on_prem",
            "endpoint": "http://vllm:8000/v1",
        }
        base.update(values)
        return self.seed.row("governance.model_provider", **base)


@pytest.fixture
def gov(seed: Seed, monkeypatch: pytest.MonkeyPatch) -> Org:
    real = socket.getaddrinfo

    # Only the two test hosts are answered here; everything else (the database) resolves as usual.
    def resolve(host: Any, *args: Any, **kwargs: Any) -> Any:
        if host == PUBLIC_HOST:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
        if host == PRIVATE_HOST:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.20.0.5", 443))]
        return real(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    org = seed.org()
    roles = {
        name: seed.row("org.role", org_id=org, code=name.upper(), name=name, capabilities=caps)
        for name, caps in (
            ("admin", ["admin"]),
            ("compliance", ["compliance"]),
            ("both", ["admin", "compliance"]),
            ("analyst", ["ask"]),
        )
    }
    users: dict[str, uuid.UUID] = {}
    for name, role in (
        ("admin", "admin"),
        ("admin2", "admin"),
        ("officer", "compliance"),
        ("both", "both"),
        ("analyst", "analyst"),
    ):
        users[name] = seed.row(
            "org.app_user",
            org_id=org,
            external_id=f"kc-{name}",
            username=name,
            display_name=name,
            status="active",
        )
        seed.row(
            "org.user_role", org_id=org, user_id=users[name], role_id=roles[role], source="manual"
        )
    other_org = seed.org()
    stranger_role = seed.row(
        "org.role", org_id=other_org, code="ADMIN", name="a", capabilities=["admin", "compliance"]
    )
    users["stranger"] = seed.row(
        "org.app_user",
        org_id=other_org,
        external_id="kc-stranger",
        username="stranger",
        display_name="stranger",
        status="active",
    )
    seed.row(
        "org.user_role",
        org_id=other_org,
        user_id=users["stranger"],
        role_id=stranger_role,
        source="manual",
    )
    return Org(org, other_org, users, seed)


class Tokens:
    def __init__(self, gov: Org) -> None:
        self.gov = gov

    def claims(self, token: str) -> dict[str, Any]:
        org = self.gov.other_org if token == "kc-stranger" else self.gov.org
        return {"sub": token, "org_id": str(org)}


def client(gov: Org) -> httpx.AsyncClient:
    app = create_app(Settings(), token_verifier=Tokens(gov))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api")


def _frontier(gov: Org, **extra: Any) -> dict[str, Any]:
    body = {
        "code": "frontier-a",
        "display_name": "Nhà cung cấp A (API)",
        "kind": "frontier",
        "api_style": "anthropic_messages",
        "endpoint": f"https://{PUBLIC_HOST}/v1",
        "secret_ref": f"secret/tenants/{gov.org}/providers/frontier-a",
        "data_region": "ap-southeast-1",
        "zero_retention": True,
        "contract_ref": "HĐ-2026-17",
        "price": {"model-x": {"input": 30000, "output": 120000}},
        "monthly_budget_vnd": 5000000,
    }
    body.update(extra)
    return body


async def test_an_on_site_provider_is_declared_switched_off(gov: Org) -> None:
    async with client(gov) as api:
        made = await api.post(
            "/v1/admin/model-providers",
            json={
                "code": "onprem-vllm",
                "display_name": "vLLM tại chỗ",
                "kind": "on_prem",
                "endpoint": "http://vllm:8000/v1",
            },
            headers=gov.headers("admin"),
        )
        assert made.status_code == 201, made.text
        body = made.json()
        assert body["enabled"] is False and body["max_classification"] == "restricted"
        assert "secret_ref" not in body
        assert gov.one("SELECT kind FROM governance.model_provider WHERE id = %s", body["id"]) == (
            "on_prem"
        )
        events = dict(gov.events(body["id"]))
        assert events["governance.model_provider.changed"]["code"] == "onprem-vllm"
        assert events["audit.requested"]["action"] == "model_provider.changed"

        again = await api.post(
            "/v1/admin/model-providers",
            json={
                "code": "onprem-vllm",
                "display_name": "x",
                "kind": "on_prem",
                "endpoint": "http://x",
            },
            headers=gov.headers("admin"),
        )
        assert again.status_code == 409
        bad_code = await api.post(
            "/v1/admin/model-providers",
            json={
                "code": "Bad Code!",
                "display_name": "x",
                "kind": "on_prem",
                "endpoint": "http://x",
            },
            headers=gov.headers("admin"),
        )
        assert bad_code.status_code == 422
        denied = await api.post(
            "/v1/admin/model-providers",
            json={
                "code": "onprem-2",
                "display_name": "x",
                "kind": "on_prem",
                "endpoint": "http://x",
            },
            headers=gov.headers("officer"),
        )
        assert denied.status_code == 403


async def test_a_frontier_provider_is_public_https_with_the_orgs_own_secret(gov: Org) -> None:
    async with client(gov) as api:

        async def create(**extra: Any) -> httpx.Response:
            return await api.post(
                "/v1/admin/model-providers",
                json=_frontier(gov, **extra),
                headers=gov.headers("admin"),
            )

        assert (await create(endpoint=f"http://{PUBLIC_HOST}/v1")).status_code == 422
        # The host resolves to a private address: the probe would be a way into the network.
        assert (await create(endpoint=f"https://{PRIVATE_HOST}/v1")).status_code == 422
        assert (await create(endpoint="https://127.0.0.1/v1")).status_code == 422
        other_tenant = await create(secret_ref=f"secret/tenants/{gov.other_org}/providers/a")
        assert other_tenant.status_code == 422
        escape = await create(secret_ref=f"secret/tenants/{gov.org}/../{gov.other_org}/a")
        assert escape.status_code == 422
        # Our own tenant, but a source account: the api's AppRole reads only providers/.
        source_account = await create(secret_ref=f"secret/tenants/{gov.org}/sources/s/account-1")
        assert source_account.status_code == 422
        assert (await create(secret_ref=None)).status_code == 422
        assert (await create(data_region=None)).status_code == 422

        made = await create()
        assert made.status_code == 201, made.text
        body = made.json()
        assert body["enabled"] is False and body["max_classification"] == "public"
        assert body["approved_by"] is None and "secret_ref" not in body


# AC-11-05-03
@pytest.mark.req("AC-11-05-03")
async def test_a_frontier_provider_is_enabled_only_after_another_person_approves(gov: Org) -> None:
    async with client(gov) as api:
        provider = (
            await api.post(
                "/v1/admin/model-providers", json=_frontier(gov), headers=gov.headers("admin")
            )
        ).json()["id"]
        url = f"/v1/admin/model-providers/{provider}"

        early = await api.patch(url, json={"enabled": True}, headers=gov.headers("admin"))
        assert early.status_code == 422 and early.json()["code"] == "business_rule_violated"
        not_compliance = await api.post(
            f"{url}:approve", json={"note": "ok"}, headers=gov.headers("admin")
        )
        assert not_compliance.status_code == 403
        no_note = await api.post(f"{url}:approve", json={}, headers=gov.headers("officer"))
        assert no_note.status_code == 422

        # AC06 who is also an admin approves, then may not be the one who enables.
        approved = await api.post(
            f"{url}:approve", json={"note": "Đã xem hợp đồng"}, headers=gov.headers("both")
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["approved_by"] == str(gov.users["both"])
        assert approved.json()["approved_at"] and approved.json()["enabled"] is False
        self_enabled = await api.patch(url, json={"enabled": True}, headers=gov.headers("both"))
        assert self_enabled.status_code == 403 and self_enabled.json()["code"] == "policy_denied"

        enabled = await api.patch(url, json={"enabled": True}, headers=gov.headers("admin"))
        assert enabled.status_code == 200, enabled.text
        assert enabled.json()["enabled"] is True
        assert str(
            gov.one("SELECT enabled_by FROM governance.model_provider WHERE id = %s", provider)
        ) == str(gov.users["admin"])
        changed = [p for e, p in gov.events(provider) if e == "governance.model_provider.changed"]
        assert changed[-1]["enabled"] is True


async def test_the_enabler_cannot_approve(gov: Org) -> None:
    provider = gov.provider(
        code="frontier-b",
        kind="frontier",
        endpoint=f"https://{PUBLIC_HOST}/v1",
        secret_ref=f"secret/tenants/{gov.org}/providers/b",
        data_region="sg",
        zero_retention=True,
        max_classification="public",
        enabled=True,
        enabled_by=gov.users["both"],
        approved_by=gov.users["officer"],
        approved_at=datetime.datetime.now(datetime.UTC),
    )
    async with client(gov) as api:
        refused = await api.post(
            f"/v1/admin/model-providers/{provider}:approve",
            json={"note": "x"},
            headers=gov.headers("both"),
        )
        assert refused.status_code == 403 and refused.json()["code"] == "policy_denied"
        on_prem = gov.provider()
        not_needed = await api.post(
            f"/v1/admin/model-providers/{on_prem}:approve",
            json={"note": "x"},
            headers=gov.headers("officer"),
        )
        assert not_needed.status_code == 422
        retired = gov.provider(
            code="frontier-c",
            kind="frontier",
            endpoint=f"https://{PUBLIC_HOST}/v1",
            secret_ref=f"secret/tenants/{gov.org}/providers/c",
            data_region="sg",
            zero_retention=True,
            max_classification="public",
            status="retired",
        )
        gone = await api.post(
            f"/v1/admin/model-providers/{retired}:approve",
            json={"note": "x"},
            headers=gov.headers("officer"),
        )
        assert gone.status_code == 409
        foreign = gov.provider(org=gov.other_org)
        hidden = await api.post(
            f"/v1/admin/model-providers/{foreign}:approve",
            json={"note": "x"},
            headers=gov.headers("officer"),
        )
        assert hidden.status_code == 404


# AC-11-05-08
@pytest.mark.req("AC-11-05-08")
async def test_changing_where_data_goes_switches_a_frontier_provider_off(gov: Org) -> None:
    endpoint = f"https://{PUBLIC_HOST}/v1"
    provider = gov.provider(
        code="frontier-d",
        kind="frontier",
        endpoint=endpoint,
        secret_ref=f"secret/tenants/{gov.org}/providers/d",
        data_region="sg",
        zero_retention=True,
        max_classification="public",
        enabled=True,
        enabled_by=gov.users["admin"],
        approved_by=gov.users["officer"],
        approved_at=datetime.datetime.now(datetime.UTC),
    )
    url = f"/v1/admin/model-providers/{provider}"
    async with client(gov) as api:
        # Resending the same endpoint with a new display name is not a contract change.
        renamed = await api.patch(
            url, json={"display_name": "A mới", "endpoint": endpoint}, headers=gov.headers("admin")
        )
        assert renamed.status_code == 200, renamed.text
        assert renamed.json()["enabled"] is True and renamed.json()["display_name"] == "A mới"

        private = await api.patch(
            url, json={"endpoint": f"https://{PRIVATE_HOST}/v1"}, headers=gov.headers("admin")
        )
        assert private.status_code == 422
        foreign_secret = await api.patch(
            url,
            json={"secret_ref": f"secret/tenants/{gov.other_org}/providers/d"},
            headers=gov.headers("admin"),
        )
        assert foreign_secret.status_code == 422

        moved = await api.patch(
            url, json={"data_region": "us-east-1"}, headers=gov.headers("admin")
        )
        assert moved.status_code == 200, moved.text
        assert moved.json()["enabled"] is False and moved.json()["approved_by"] is None
        changed = [p for e, p in gov.events(provider) if e == "governance.model_provider.changed"]
        assert changed[-1]["enabled"] is False

        retired = await api.patch(url, json={"status": "retired"}, headers=gov.headers("admin"))
        assert retired.status_code == 200
        enable_retired = await api.patch(url, json={"enabled": True}, headers=gov.headers("admin"))
        assert enable_retired.status_code == 422

        foreign = gov.provider(org=gov.other_org)
        hidden = await api.patch(
            f"/v1/admin/model-providers/{foreign}",
            json={"display_name": "x"},
            headers=gov.headers("admin"),
        )
        assert hidden.status_code == 404
        denied = await api.patch(url, json={"display_name": "x"}, headers=gov.headers("officer"))
        assert denied.status_code == 403


async def test_providers_list_with_this_months_cost(gov: Org) -> None:
    on_prem = gov.provider(code="onprem-vllm", enabled=True)
    frontier = gov.provider(
        code="frontier-e",
        kind="frontier",
        endpoint=f"https://{PUBLIC_HOST}/v1",
        secret_ref=f"secret/tenants/{gov.org}/providers/e",
        data_region="sg",
        max_classification="public",
        price=Json({"model-x": {"input": 30000, "output": 120000}}),
    )
    gov.provider(org=gov.other_org, code="onprem-other")
    for called_at in (
        datetime.datetime.now(datetime.UTC),
        datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=62),
    ):
        gov.seed.row(
            "governance.model_usage",
            org_id=gov.org,
            task="answer_writing",
            model_name="model-x",
            caller_kind="question",
            input_tokens=1_000_000,
            output_tokens=500_000,
            provider_id=frontier,
            called_at=called_at,
        )
    async with client(gov) as api:
        listed = await api.get("/v1/admin/model-providers", headers=gov.headers("admin"))
        assert listed.status_code == 200, listed.text
        items = {p["code"]: p for p in listed.json()["items"]}
        assert set(items) == {"onprem-vllm", "frontier-e"}
        # Price is VND per million tokens: 1M in x 30 000 + 0.5M out x 120 000, this month only.
        assert decimal.Decimal(str(items["frontier-e"]["monthly_cost_vnd"])) == 90_000
        assert decimal.Decimal(str(items["onprem-vllm"]["monthly_cost_vnd"])) == 0
        assert all("secret_ref" not in p for p in items.values())

        only_frontier = await api.get(
            "/v1/admin/model-providers?kind=frontier", headers=gov.headers("admin")
        )
        assert [p["id"] for p in only_frontier.json()["items"]] == [str(frontier)]
        enabled = await api.get(
            "/v1/admin/model-providers?enabled=true", headers=gov.headers("admin")
        )
        assert [p["id"] for p in enabled.json()["items"]] == [str(on_prem)]
        first = await api.get("/v1/admin/model-providers?page_size=1", headers=gov.headers("admin"))
        token = first.json()["next_page_token"]
        second = await api.get(
            f"/v1/admin/model-providers?page_size=1&page_token={token}",
            headers=gov.headers("admin"),
        )
        assert [p["code"] for p in first.json()["items"] + second.json()["items"]] == [
            "frontier-e",
            "onprem-vllm",
        ]
        denied = await api.get("/v1/admin/model-providers", headers=gov.headers("officer"))
        assert denied.status_code == 403


async def test_a_test_call_is_logged_as_an_eval_decision(
    gov: Org, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = gov.provider(code="onprem-test")
    calls: list[tuple[uuid.UUID, str]] = []

    def probe(row: Any, model_name: str) -> tuple[int, int | None, int | None]:
        calls.append((row.id, model_name))
        return 120, 14, 2

    monkeypatch.setattr(routes, "_probe_provider", probe)
    url = f"/v1/admin/model-providers/{provider}:test"
    async with client(gov) as api:
        ok = await api.post(url, json={"model_name": "Qwen3-32B"}, headers=gov.headers("admin"))
        assert ok.status_code == 200, ok.text
        assert ok.json() == {
            "provider_id": str(provider),
            "model_name": "Qwen3-32B",
            "outcome": "ok",
            "latency_ms": 120,
            "input_tokens": 14,
            "output_tokens": 2,
            "error": None,
        }
        assert calls == [(provider, "Qwen3-32B")]
        row = gov.seed.conn.cursor()
        row.execute(
            "SELECT caller_kind, decision, outcome, input_tokens, max_classification,"
            " context_labels FROM governance.model_route_decision WHERE chosen_provider_id = %s",
            (str(provider),),
        )
        assert row.fetchall() == [("eval", "on_prem_default", "ok", 14, "public", ["public"])]

        def down(row: Any, model_name: str) -> tuple[int, int | None, int | None]:
            raise routes._ProviderUnavailable("The provider did not return a usable response.")

        monkeypatch.setattr(routes, "_probe_provider", down)
        failed = await api.post(url, json={"model_name": "Qwen3-32B"}, headers=gov.headers("admin"))
        assert failed.status_code == 503 and failed.json()["code"] == "model_unavailable"
        assert (
            gov.one(
                "SELECT count(*) FROM governance.model_route_decision"
                " WHERE chosen_provider_id = %s AND outcome = 'error'",
                provider,
            )
            == 1
        )

        foreign = gov.provider(org=gov.other_org)
        hidden = await api.post(
            f"/v1/admin/model-providers/{foreign}:test",
            json={"model_name": "x"},
            headers=gov.headers("admin"),
        )
        assert hidden.status_code == 404
        denied = await api.post(url, json={"model_name": "x"}, headers=gov.headers("officer"))
        assert denied.status_code == 403


async def test_a_frontier_provider_not_switched_on_is_never_called(
    gov: Org, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = gov.provider(
        code="frontier-f",
        kind="frontier",
        endpoint=f"https://{PUBLIC_HOST}/v1",
        secret_ref=f"secret/tenants/{gov.org}/providers/f",
        data_region="sg",
        zero_retention=True,
        max_classification="public",
    )
    calls: list[str] = []
    monkeypatch.setattr(
        routes, "_probe_provider", lambda row, model: calls.append(model) or (1, None, None)
    )
    async with client(gov) as api:
        refused = await api.post(
            f"/v1/admin/model-providers/{provider}:test",
            json={"model_name": "model-x"},
            headers=gov.headers("admin"),
        )
    # BR-180: model_route_decision_guard admits a frontier decision only for an enabled provider
    # with frontier_routing on; nothing is sent to a host nobody approved.
    assert refused.status_code == 422, refused.text
    assert calls == []


def _decision(gov: Org, org: uuid.UUID | None = None, **values: Any) -> uuid.UUID:
    base: dict[str, Any] = {
        "org_id": org or gov.org,
        "task": "answer_writing",
        "caller_kind": "question",
        "product_scope": "in_scope",
        "context_labels": ["internal"],
        "max_classification": "internal",
        "contains_internal_data": True,
        "decision": "on_prem_default",
        "model_name": "Qwen3-32B",
        "prompt_sha256": SHA,
    }
    base.update(values)
    return gov.seed.row("governance.model_route_decision", **base)


async def test_compliance_reads_the_routing_log(gov: Org) -> None:
    on_prem = gov.provider(code="onprem-log")
    frontier = gov.provider(
        code="frontier-g",
        kind="frontier",
        endpoint=f"https://{PUBLIC_HOST}/v1",
        secret_ref=f"secret/tenants/{gov.org}/providers/g",
        data_region="sg",
        max_classification="public",
    )
    now = datetime.datetime.now(datetime.UTC)
    caller = uuid.uuid4()
    default = _decision(
        gov, chosen_provider_id=on_prem, decided_at=now - datetime.timedelta(hours=3)
    )
    blocked = _decision(
        gov,
        chosen_provider_id=on_prem,
        candidate_provider_id=frontier,
        decision="frontier_blocked",
        reasons=["internal_data", "label_not_public"],
        caller_id=caller,
        decided_at=now - datetime.timedelta(hours=1),
    )
    other_reason = _decision(
        gov,
        chosen_provider_id=on_prem,
        candidate_provider_id=frontier,
        decision="frontier_blocked",
        reasons=["org_disabled"],
        caller_kind="agent_run",
        decided_at=now - datetime.timedelta(hours=2),
    )
    foreign_provider = gov.provider(org=gov.other_org, code="onprem-x")
    _decision(gov, org=gov.other_org, chosen_provider_id=foreign_provider)

    async def ids(query: str = "") -> list[str]:
        got = await api.get(
            f"/v1/admin/model-route-decisions{query}", headers=gov.headers("officer")
        )
        assert got.status_code == 200, got.text
        return [d["id"] for d in got.json()["items"]]

    async with client(gov) as api:
        assert await ids() == [str(blocked), str(other_reason), str(default)]
        assert await ids("?reason=internal_data") == [str(blocked)]
        assert await ids("?decision=frontier_blocked") == [str(blocked), str(other_reason)]
        assert await ids("?caller_kind=agent_run") == [str(other_reason)]
        assert await ids(f"?caller_id={caller}") == [str(blocked)]
        assert await ids(f"?provider_id={on_prem}") == [
            str(blocked),
            str(other_reason),
            str(default),
        ]
        since = (now - datetime.timedelta(minutes=150)).isoformat()
        assert await ids(f"?from={since.replace('+', '%2B')}") == [str(blocked), str(other_reason)]

        first = await api.get(
            "/v1/admin/model-route-decisions?page_size=2", headers=gov.headers("officer")
        )
        token = first.json()["next_page_token"]
        assert await ids(f"?page_size=2&cursor={token}") == [str(default)]
        row = first.json()["items"][0]
        assert row["prompt_sha256"] == SHA and "prompt" not in row

        denied = await api.get("/v1/admin/model-route-decisions", headers=gov.headers("admin"))
        assert denied.status_code == 403
