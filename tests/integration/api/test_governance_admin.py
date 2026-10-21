"""Chính sách and the governance part of Quản trị against PostgreSQL: access and behaviour
policies (UC-11-02, UC-11-08), data subject requests (UC-11-06), models per task, AI settings and
system health (UC-11-05, UC-11-04)."""

from __future__ import annotations

import hashlib
import hmac
import uuid
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from psycopg2.extras import Json

from services.api.app import create_app
from services.api.config import Settings

from .conftest import Seed

pytestmark = [pytest.mark.integration, pytest.mark.contract]


@dataclass
class Org:
    org: uuid.UUID
    space: uuid.UUID
    users: dict[str, uuid.UUID]
    roles: dict[str, uuid.UUID]
    dataset: uuid.UUID
    seed: Seed

    def headers(self, who: str, space: bool = False) -> dict[str, str]:
        out = {"Authorization": f"Bearer kc-{who}"}
        if space:
            out["X-Data-Space"] = str(self.space)
        return out

    def events(self, aggregate_id: object) -> list[tuple[str, dict[str, Any]]]:
        with self.seed.conn.cursor() as cur:
            cur.execute(
                "SELECT event_type, payload FROM governance.outbox WHERE aggregate_id = %s"
                " ORDER BY created_at",
                (str(aggregate_id),),
            )
            return list(cur.fetchall())

    def one(self, sql: str, *params: object) -> Any:
        with self.seed.conn.cursor() as cur:
            cur.execute(sql, [str(p) if isinstance(p, uuid.UUID) else p for p in params])
            row = cur.fetchone()
            return row[0] if row else None


def _user(
    seed: Seed, org: uuid.UUID, name: str, role: uuid.UUID, attributes: dict[str, str]
) -> uuid.UUID:
    user = seed.row(
        "org.app_user",
        org_id=org,
        external_id=f"kc-{name}",
        username=name,
        display_name=name,
        status="active",
        attributes=Json(attributes),
    )
    seed.row("org.user_role", org_id=org, user_id=user, role_id=role, source="manual")
    return user


@pytest.fixture
def gov(seed: Seed) -> Org:
    org = seed.org()
    roles = {
        "admin": seed.row(
            "org.role", org_id=org, code="ADMIN", name="Admin", capabilities=["ask", "admin"]
        ),
        "compliance": seed.row(
            "org.role", org_id=org, code="AC06", name="AC06", capabilities=["ask", "compliance"]
        ),
        "branch": seed.row(
            "org.role", org_id=org, code="BRANCH", name="Branch", capabilities=["ask"]
        ),
        "head": seed.row("org.role", org_id=org, code="HEAD", name="Head", capabilities=["ask"]),
    }
    users = {
        "admin": _user(seed, org, "admin", roles["admin"], {"branch_code": "HN01"}),
        "officer": _user(seed, org, "officer", roles["compliance"], {}),
        "teller": _user(seed, org, "teller", roles["branch"], {"branch_code": "HCM02"}),
    }
    space = seed.row(
        "org.data_space", org_id=org, code="DS-GOV", name="Gov", steward_id=users["admin"]
    )
    seed.row(
        "org.data_space_member",
        org_id=org,
        data_space_id=space,
        principal_type="user",
        principal_id=users["teller"],
        role_id=roles["branch"],
    )
    dataset = seed.row(
        "catalog.dataset",
        org_id=org,
        data_space_id=space,
        origin="derived",
        schema_name="silver",
        table_name="khach_hang",
        enabled=True,
    )
    for i, (name, pii) in enumerate(
        (
            ("ma_kh", None),
            ("so_cccd", "national_id"),
            ("so_tk", "account_no"),
            ("branch_code", None),
        )
    ):
        seed.row(
            "catalog.column_meta",
            org_id=org,
            dataset_id=dataset,
            dataset_version=1,
            name=name,
            ordinal=i + 1,
            data_type="text",
            normalized_type="string",
            pii_label=pii,
        )
    return Org(org, space, users, roles, dataset, seed)


class Tokens:
    def __init__(self, gov: Org) -> None:
        self.gov = gov

    def claims(self, token: str) -> dict[str, Any]:
        return {"sub": token, "org_id": str(self.gov.org)}


def client(gov: Org) -> httpx.AsyncClient:
    app = create_app(Settings(), token_verifier=Tokens(gov))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api")


def _access(gov: Org, **definition: Any) -> dict[str, Any]:
    base = {
        "data_space_id": str(gov.space),
        "dataset_id": str(gov.dataset),
        "name": "Chi nhánh chỉ thấy khách hàng của mình",
        "kind": "row",
        "condition_expr": "branch_code = :user.branch_code",
        "applies_to": {"role_ids": [str(gov.roles["branch"])]},
    }
    base.update(definition)
    return {"type": "access", "definition": base}


async def test_an_access_policy_is_simulated_before_it_is_switched_on(gov: Org) -> None:
    async with client(gov) as api:
        made = await api.post("/v1/policies", json=_access(gov), headers=gov.headers("admin"))
        assert made.status_code == 201, made.text
        policy = made.json()
        assert policy["type"] == "access" and policy["status"] == "draft"
        assert ("policy.created", "draft") in [
            (e, p["status"]) for e, p in gov.events(policy["id"])
        ]

        early = await api.post(
            f"/v1/policies/{policy['id']}:activate", headers=gov.headers("admin")
        )
        assert early.status_code == 422

        simulated = await api.post(
            f"/v1/policies/{policy['id']}:simulate",
            json={"as_user_id": str(gov.users["teller"]), "sample_sql": "SELECT * FROM khach_hang"},
            headers=gov.headers("admin"),
        )
        assert simulated.status_code == 200, simulated.text
        view = simulated.json()
        assert view["applies"] is True
        assert view["row_condition"] == "branch_code = 'HCM02'"
        assert "WHERE branch_code = 'HCM02'" in view["after_sql"]
        other = await api.post(
            f"/v1/policies/{policy['id']}:simulate",
            json={"as_user_id": str(gov.users["admin"])},
            headers=gov.headers("admin"),
        )
        assert other.json()["applies"] is False

        on = await api.post(f"/v1/policies/{policy['id']}:activate", headers=gov.headers("admin"))
        assert on.status_code == 200, on.text
        assert on.json()["status"] == "active" and on.json()["activated_by"] == str(
            gov.users["admin"]
        )
        again = await api.post(
            f"/v1/policies/{policy['id']}:activate", headers=gov.headers("admin")
        )
        assert again.status_code == 409

        listed = await api.get("/v1/policies?type=access", headers=gov.headers("admin"))
        assert [p["id"] for p in listed.json()] == [policy["id"]]
        # Who sees what is not for every asker.
        hidden = await api.get(
            "/v1/policies?type=access", headers=gov.headers("teller", space=True)
        )
        assert hidden.status_code == 403


async def test_a_policy_names_real_columns_and_known_attributes(gov: Org) -> None:
    async with client(gov) as api:
        # UC-11-02 E1: no user carries the attribute.
        unknown = await api.post(
            "/v1/policies",
            json=_access(gov, condition_expr="region = :user.region_code"),
            headers=gov.headers("admin"),
        )
        assert unknown.status_code == 422
        missing = await api.post(
            "/v1/policies",
            json=_access(gov, kind="column", condition_expr=None, columns=["so_ho_chieu"]),
            headers=gov.headers("admin"),
        )
        assert missing.status_code == 422
        stray = await api.post(
            "/v1/policies",
            json=_access(gov, surprise=True),
            headers=gov.headers("admin"),
        )
        assert stray.status_code == 422
        denied = await api.post("/v1/policies", json=_access(gov), headers=gov.headers("teller"))
        assert denied.status_code == 403


async def test_permissions_compare_two_roles_on_one_table(gov: Org) -> None:
    async with client(gov) as api:
        for definition in (
            _access(gov),
            _access(
                gov,
                name="Ẩn CCCD",
                kind="column",
                condition_expr=None,
                columns=["so_cccd"],
                applies_to={},
            ),
            _access(
                gov,
                name="Che số tài khoản",
                kind="mask",
                condition_expr=None,
                columns=["so_tk"],
                mask_method="last4",
                applies_to={"role_ids": [str(gov.roles["branch"])]},
            ),
        ):
            made = (
                await api.post("/v1/policies", json=definition, headers=gov.headers("admin"))
            ).json()
            await api.post(
                f"/v1/policies/{made['id']}:simulate",
                json={"as_user_id": str(gov.users["teller"])},
                headers=gov.headers("admin"),
            )
            await api.post(f"/v1/policies/{made['id']}:activate", headers=gov.headers("admin"))
        compared = await api.post(
            "/v1/permissions:simulate",
            json={
                "dataset_id": str(gov.dataset),
                "role_ids": [str(gov.roles["branch"]), str(gov.roles["head"])],
            },
            headers=gov.headers("admin"),
        )
        assert compared.status_code == 200, compared.text
        branch, head = compared.json()["views"]
        assert branch["hidden_columns"] == head["hidden_columns"] == ["so_cccd"]
        assert branch["masked_columns"] == {"so_tk": "last4"} and head["masked_columns"] == {}
        assert branch["row_conditions"] == ["branch_code = :user.branch_code"]
        assert head["row_conditions"] == []
        assert "so_cccd" not in branch["visible_columns"]

        three = await api.post(
            "/v1/permissions:simulate",
            json={
                "dataset_id": str(gov.dataset),
                "role_ids": [str(gov.roles["branch"]), str(gov.roles["head"])],
                "user_ids": [str(gov.users["teller"])],
            },
            headers=gov.headers("admin"),
        )
        assert three.status_code == 422


async def test_behaviour_policy_and_backtest(gov: Org) -> None:
    async with client(gov) as api:
        made = await api.post(
            "/v1/policies",
            json={
                "type": "behavior",
                "definition": {
                    "scope": "data_space",
                    "data_space_id": str(gov.space),
                    "kind": "auto_handle",
                    "description": "Lệch dưới 0,05% ngày chốt kỳ thì dùng nguồn A",
                    "condition": {"max_diff_pct": 0.05},
                    "action": {"prefer_source": "A"},
                },
            },
            headers=gov.headers("admin"),
        )
        assert made.status_code == 201, made.text
        policy = made.json()
        assert policy["type"] == "behavior"
        run = await api.post(
            f"/v1/policies/{policy['id']}:backtest",
            json={"window_days": 7},
            headers=gov.headers("admin"),
        )
        assert run.status_code == 202, run.text
        backtest = run.json()["id"]
        assert run.headers["Location"] == f"/v1/policy-backtests/{backtest}"
        assert (
            gov.one("SELECT policy_kind FROM governance.policy_backtest WHERE id = %s", backtest)
            == "behavior"
        )
        on = await api.post(f"/v1/policies/{policy['id']}:activate", headers=gov.headers("admin"))
        assert on.json()["status"] == "active"
        listed = await api.get("/v1/policies", headers=gov.headers("teller", space=True))
        assert [p["id"] for p in listed.json()] == [policy["id"]]
        outside = await api.get("/v1/policies", headers=gov.headers("teller"))
        assert outside.json() == []


KEY = "k" * 40


async def test_a_data_subject_request_keeps_only_the_hash(
    gov: Org, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DSR_SUBJECT_KEY", KEY)
    async with client(gov) as api:
        opened = await api.post(
            "/v1/dsr",
            json={
                "request_kind": "delete",
                "received_at": "2026-10-01T09:00:00+07:00",
                "due_at": "2026-10-31T17:00:00+07:00",
                "subject_identifier": " 0010 9200 1234 ",
            },
            headers=gov.headers("officer"),
        )
        assert opened.status_code == 202, opened.text
        dsr = opened.json()["id"]
        assert opened.headers["Location"] == f"/v1/dsr/{dsr}"
        stored = gov.one("SELECT subject_hash FROM governance.dsr_request WHERE id = %s", dsr)
        org_key = hmac.new(KEY.encode(), str(gov.org).encode(), hashlib.sha256).digest()
        assert stored == hmac.new(org_key, b"001092001234", hashlib.sha256).hexdigest()
        # A plain SHA-256 of the id would be reversed by trying every 12-digit number.
        assert stored != hashlib.sha256(b"001092001234").hexdigest()
        searched = dict(gov.events(dsr))["dsr.search_requested"]
        assert searched["subject_hash"] == stored and "0010" not in str(searched)

        early = await api.post(
            f"/v1/dsr/{dsr}:execute",
            json={"items": [{"id": str(uuid.uuid4()), "action": "delete"}]},
            headers=gov.headers("officer"),
        )
        assert early.status_code == 422

        # What the search worker writes.
        def item(kind: str, action: str, basis: str | None = None) -> uuid.UUID:
            return gov.seed.row(
                "governance.dsr_item",
                org_id=gov.org,
                dsr_request_id=dsr,
                location_kind=kind,
                location_ref=f"{kind}:1",
                proposed_action=action,
                legal_basis=basis,
            )

        chunk = item("chunk", "delete")
        source = item("source_system", "retain", "Nghĩa vụ lưu trữ của ngân hàng")
        filed = item("tile_snapshot", "retain", "Kỳ chốt đã nộp")
        gov.seed.conn.cursor().execute(
            "UPDATE governance.dsr_request SET status = 'review' WHERE id = %s", (dsr,)
        )
        got = await api.get(f"/v1/dsr/{dsr}", headers=gov.headers("officer"))
        assert got.status_code == 200
        assert {i["id"] for i in got.json()["items"]} == {str(chunk), str(source), str(filed)}

        partial = await api.post(
            f"/v1/dsr/{dsr}:execute",
            json={"items": [{"id": str(chunk), "action": "delete"}]},
            headers=gov.headers("officer"),
        )
        assert partial.status_code == 422
        at_source = await api.post(
            f"/v1/dsr/{dsr}:execute",
            json={
                "items": [
                    {"id": str(chunk), "action": "delete"},
                    {"id": str(source), "action": "delete"},
                    {"id": str(filed), "action": "retain", "legal_basis": "Kỳ chốt đã nộp"},
                ]
            },
            headers=gov.headers("officer"),
        )
        assert at_source.status_code == 422
        no_basis = await api.post(
            f"/v1/dsr/{dsr}:execute",
            json={
                "items": [
                    {"id": str(chunk), "action": "delete"},
                    {"id": str(source), "action": "retain"},
                    {"id": str(filed), "action": "retain", "legal_basis": "Kỳ chốt đã nộp"},
                ]
            },
            headers=gov.headers("officer"),
        )
        assert no_basis.status_code == 422
        done = await api.post(
            f"/v1/dsr/{dsr}:execute",
            json={
                "items": [
                    {"id": str(chunk), "action": "delete"},
                    {"id": str(source), "action": "retain", "legal_basis": "Ở hệ thống nguồn"},
                    {"id": str(filed), "action": "retain", "legal_basis": "Kỳ chốt đã nộp"},
                ]
            },
            headers=gov.headers("officer"),
        )
        assert done.status_code == 202, done.text
        assert (
            gov.one("SELECT status FROM governance.dsr_request WHERE id = %s", dsr) == "executing"
        )
        assert (
            gov.one("SELECT decision FROM governance.dsr_item WHERE id = %s", filed) == "retained"
        )
        executed = dict(gov.events(dsr))["dsr.execute_requested"]
        assert executed["delete_item_ids"] == [str(chunk)]

        admin_only = await api.get(f"/v1/dsr/{dsr}", headers=gov.headers("admin"))
        assert admin_only.status_code == 403


async def test_a_model_change_is_evaluated_before_it_serves(gov: Org) -> None:
    provider = gov.seed.row(
        "governance.model_provider",
        org_id=gov.org,
        code="onprem-vllm",
        display_name="vLLM tại chỗ",
        kind="on_prem",
        api_style="openai_compatible",
        endpoint="http://vllm:8000/v1",
        status="active",
    )
    gov.seed.row(
        "governance.model_config",
        org_id=gov.org,
        task="sql_generation",
        version=1,
        model_name="Qwen3-Coder-32B",
        endpoint="http://vllm:8000/v1",
        provider_id=provider,
        status="active",
        eval_run_id=uuid.uuid4(),
        limits=Json({"max_tokens": 4096}),
    )
    async with client(gov) as api:
        changed = await api.put(
            "/v1/admin/models/sql_generation",
            json={"model_name": "Qwen3-Coder-72B", "endpoint": "http://vllm:8000/v1"},
            headers=gov.headers("admin"),
        )
        assert changed.status_code == 202, changed.text
        body = changed.json()
        assert body["version"] == 2 and changed.headers["Location"] == f"/v1/eval-runs/{body['id']}"
        requested = dict(gov.events(body["model_config_id"]))["model_config.eval_requested"]
        assert requested["eval_run_id"] == body["id"]
        elsewhere = await api.put(
            "/v1/admin/models/answer_writing",
            json={"model_name": "gpt", "endpoint": "https://api.example.com/v1"},
            headers=gov.headers("admin"),
        )
        assert elsewhere.status_code == 422
        busy = await api.put(
            "/v1/admin/models/sql_generation",
            json={"model_name": "X", "endpoint": "http://vllm:8000/v1"},
            headers=gov.headers("admin"),
        )
        assert busy.status_code == 409
        unknown = await api.put(
            "/v1/admin/models/painting",
            json={"model_name": "X", "endpoint": "http://x"},
            headers=gov.headers("admin"),
        )
        assert unknown.status_code == 422

        listed = await api.get("/v1/admin/models", headers=gov.headers("admin"))
        assert listed.status_code == 200
        versions = {(m["version"], m["status"]) for m in listed.json()}
        assert versions == {(1, "active"), (2, "testing")}
        # The old version keeps its limits for the new one when none are given.
        assert next(m for m in listed.json() if m["version"] == 2)["limits"] == {"max_tokens": 4096}


async def test_ai_settings_are_versioned_and_checked(gov: Org) -> None:
    async with client(gov) as api:
        first = await api.put(
            "/v1/admin/ai-settings/max_cost_per_run",
            json={"value": 20000},
            headers=gov.headers("admin"),
        )
        assert first.status_code == 200, first.text
        second = await api.put(
            "/v1/admin/ai-settings/max_cost_per_run",
            json={"value": 30000},
            headers=gov.headers("admin"),
        )
        assert second.json()["version"] == 2 and second.json()["value"] == 30000
        audits = [p for e, p in gov.events(second.json()["id"]) if e == "audit.requested"]
        assert audits[-1]["before"] == {"value": 20000, "version": 1}

        bad = await api.put(
            "/v1/admin/ai-settings/frontier_routing",
            json={"value": {"enabled": True, "tasks": ["sql_generation"]}},
            headers=gov.headers("admin"),
        )
        assert bad.status_code == 422
        ok = await api.put(
            "/v1/admin/ai-settings/frontier_routing",
            json={"value": {"enabled": True, "tasks": ["answer_writing"], "data_space_ids": []}},
            headers=gov.headers("admin"),
        )
        assert ok.status_code == 200, ok.text
        unknown = await api.put(
            "/v1/admin/ai-settings/temperature", json={"value": 1}, headers=gov.headers("admin")
        )
        assert unknown.status_code == 422


async def test_health_without_prometheus_is_no_data(
    gov: Org, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PROMETHEUS_URL", raising=False)
    async with client(gov) as api:
        health = await api.get("/v1/admin/system/health", headers=gov.headers("admin"))
        assert health.status_code == 200
        assert health.json()["status"] == "no_data"
        assert {s["status"] for s in health.json()["sections"].values()} == {"no_data"}
        monkeypatch.setenv("PROMETHEUS_URL", "file:///etc/passwd")
        local = await api.get("/v1/admin/system/health", headers=gov.headers("admin"))
        assert local.json()["status"] == "no_data"


async def test_no_subject_key_no_request(gov: Org, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DSR_SUBJECT_KEY", raising=False)
    async with client(gov) as api:
        refused = await api.post(
            "/v1/dsr",
            json={
                "request_kind": "access",
                "received_at": "2026-10-01T09:00:00+07:00",
                "due_at": "2026-10-31T17:00:00+07:00",
                "subject_identifier": "001092001234",
            },
            headers=gov.headers("officer"),
        )
    assert refused.status_code == 503


async def test_an_agent_only_policy_is_simulated_as_the_agent_answers(gov: Org) -> None:
    # The Holistics review: what the agent may see can be less than what the person may.
    async with client(gov) as api:
        made = await api.post(
            "/v1/policies",
            json=_access(gov, applies_to={"actors": ["agent"]}),
            headers=gov.headers("admin"),
        )
        assert made.status_code == 201, made.text
        policy = made.json()
        assert policy["applies_to"]["actors"] == ["agent"]

        simulated = await api.post(
            f"/v1/policies/{policy['id']}:simulate",
            json={"as_user_id": str(gov.users["teller"])},
            headers=gov.headers("admin"),
        )
        assert simulated.status_code == 200, simulated.text
        view = simulated.json()
        assert view["applies"] is True
        assert view["row_condition"] == "branch_code = 'HCM02'"
        assert "when the agent answers" in view["reason"]

        wrong = await api.post(
            "/v1/policies",
            json=_access(gov, applies_to={"actors": ["robot"]}),
            headers=gov.headers("admin"),
        )
        assert wrong.status_code == 422
