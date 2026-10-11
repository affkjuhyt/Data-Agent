"""Gói ngành (P09) against PostgreSQL: import, authoring, install, mapping, overrides, upgrades.
Each test builds its own organisation: an admin (AC05), a pack author (AC07), a data owner
holding admin_data/steward/approve in one Data Space, a second approver and a controller with
compliance (AC06). Pack files are signed in the test with a key the installation trusts
(PACK_TRUSTED_KEYS).
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from services.api.app import create_app
from services.api.config import Settings
from services.api.modules.pack import rules

from .conftest import Seed

pytestmark = [pytest.mark.integration, pytest.mark.contract]

KEY = Ed25519PrivateKey.generate()
KEY_ID = "vendor-2026"


@pytest.fixture(autouse=True)
def _trusted_key(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = KEY.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    monkeypatch.setenv(rules.TRUSTED_KEYS_ENV, json.dumps({KEY_ID: base64.b64encode(raw).decode()}))
    monkeypatch.setenv(rules.CORE_VERSION_ENV, "3.1.0")


def manifest(version: str = "2026.2", **changes: Any) -> dict[str, Any]:
    m: dict[str, Any] = {
        "schema_version": 1,
        "pack": {
            "code": "banking",
            "name": "Ngân hàng",
            "industry_code": "banking",
            "publisher": "Vendor",
            "publisher_key_id": KEY_ID,
        },
        "version": version,
        "min_core_version": "3.0",
        "release_notes": "Bản phát hành",
        "entities": [
            {
                "code": "loan",
                "name": "Khoản vay",
                "key_attributes": ["loan_id"],
                "attributes": [
                    {"code": "loan_id", "name": "Mã khoản vay", "data_type": "string"},
                    {
                        "code": "debt_group",
                        "name": "Nhóm nợ",
                        "data_type": "code",
                        "code_list": ["1", "2", "3", "4", "5"],
                    },
                    {"code": "legacy_flag", "name": "Cờ cũ", "data_type": "boolean"},
                ],
            }
        ],
        "metrics": [
            {
                "code": "NPL_RATIO",
                "name": "Tỷ lệ nợ xấu",
                "expression": "sum(bad)/sum(all)",
                "legal_basis": "TT 11/2021",
                "in_regulatory_report": True,
            },
            {
                "code": "LOAN_GROWTH",
                "name": "Tăng trưởng",
                "expression": "growth(all)",
                "is_internal": True,
            },
        ],
        "rules": [{"code": "R1", "description": "Khớp sổ cái", "expression": "sum = gl"}],
        "skills": [{"kind": "eval_set", "code": "Q1", "name": "Câu hỏi mẫu", "definition": []}],
        "resources": {},
    }
    m.update(changes)
    return m


def pack_file(m: dict[str, Any], *, tamper: bool = False, key: Any = KEY) -> bytes:
    resources = {"README": base64.b64encode(b"readme").decode()}
    m = {**m, "resources": {"README": hashlib.sha256(b"readme").hexdigest()}}
    signature = base64.b64encode(key.sign(rules.canonical(m))).decode()
    if tamper:
        resources["README"] = base64.b64encode(b"changed after signing").decode()
    return json.dumps({"manifest": m, "signature": signature, "resources": resources}).encode()


@dataclass
class Org:
    org: uuid.UUID
    space: uuid.UUID
    users: dict[str, uuid.UUID]
    seed: Seed

    def headers(self, who: str, space: bool = True) -> dict[str, str]:
        h = {"Authorization": f"Bearer kc-{who}-{self.org.hex[:6]}"}
        if space:
            h["X-Data-Space"] = str(self.space)
        return h

    def events(self, aggregate_id: object) -> list[tuple[str, dict[str, Any]]]:
        with self.seed.conn.cursor() as cur:
            cur.execute(
                "SELECT event_type, payload FROM pack.outbox WHERE aggregate_id = %s ORDER BY created_at, id",
                (str(aggregate_id),),
            )
            return list(cur.fetchall())

    def one(self, sql: str, *params: object) -> Any:
        with self.seed.conn.cursor() as cur:
            cur.execute(sql, [str(p) if isinstance(p, uuid.UUID) else p for p in params])
            return cur.fetchone()

    def execute(self, sql: str, *params: object) -> None:
        with self.seed.conn.cursor() as cur:
            cur.execute(sql, [str(p) if isinstance(p, uuid.UUID) else p for p in params])


ROLES = {
    "admin": ["ask", "admin"],
    "author": ["ask", "pack_author"],
    "owner": ["ask", "admin_data", "steward", "approve"],
    "approver": ["ask", "approve", "admin_data"],
    "controller": ["ask", "approve", "compliance"],
}


@pytest.fixture
def org(seed: Seed) -> Org:
    org_id = seed.org()
    tag = org_id.hex[:6]
    boss = seed.row(
        "org.app_user",
        org_id=org_id,
        external_id=f"kc-boss-{tag}",
        username="boss",
        display_name="Boss",
    )
    space = seed.row("org.data_space", org_id=org_id, code="DS-TD", name="Tín dụng", steward_id=boss)
    users: dict[str, uuid.UUID] = {}
    for name, caps in ROLES.items():
        role = seed.row(
            "org.role", org_id=org_id, code=f"R-{name}", name=name, capabilities=caps
        )
        user = seed.row(
            "org.app_user",
            org_id=org_id,
            external_id=f"kc-{name}-{tag}",
            username=name,
            display_name=name,
            status="active",
        )
        seed.row("org.user_role", org_id=org_id, user_id=user, role_id=role, source="manual")
        seed.row(
            "org.data_space_member",
            org_id=org_id,
            data_space_id=space,
            principal_type="user",
            principal_id=user,
            role_id=role,
        )
        users[name] = user
    return Org(org=org_id, space=space, users=users, seed=seed)


class Tokens:
    def __init__(self, org: Org) -> None:
        self.org = org

    def claims(self, token: str) -> dict[str, Any]:
        return {"sub": token, "org_id": str(self.org.org)}


def client(org: Org) -> httpx.AsyncClient:
    app = create_app(Settings(), token_verifier=Tokens(org))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api")


async def _import(api: httpx.AsyncClient, org: Org, data: bytes) -> httpx.Response:
    return await api.post(
        "/v1/packs:import",
        headers=org.headers("admin", space=False),
        files={"file": ("pack.json", data, "application/json")},
    )


async def _imported(api: httpx.AsyncClient, org: Org, version: str = "2026.2", **changes: Any) -> dict:
    response = await _import(api, org, pack_file(manifest(version, **changes)))
    assert response.status_code == 201, response.text
    return response.json()


def _attribute(org: Org, version_id: str, code: str) -> uuid.UUID:
    return org.one(
        "SELECT a.id FROM pack.canonical_attribute a JOIN pack.canonical_entity e ON e.id = a.entity_id "
        "WHERE e.pack_version_id = %s AND a.code = %s",
        version_id,
        code,
    )[0]


def _dataset(org: Org) -> uuid.UUID:
    return org.seed.row(
        "catalog.dataset",
        org_id=org.org,
        data_space_id=org.space,
        origin="derived",
        schema_name="core",
        table_name="loans",
    )


async def _active_install(api: httpx.AsyncClient, org: Org, version: dict) -> str:
    response = await api.post(
        "/v1/pack-installs", headers=org.headers("owner"), json={"pack_version_id": version["id"]}
    )
    assert response.status_code == 201, response.text
    install = response.json()["id"]
    org.execute(
        "UPDATE pack.pack_install SET status = 'active', activated_at = now() WHERE id = %s", install
    )
    return install


# --- import (UC-09-01) --------------------------------------------------------------------------


@pytest.mark.req("AC-09-01-01")
async def test_import_verifies_and_loads_the_pack(org: Org) -> None:
    async with client(org) as api:
        version = await _imported(api, org)
        assert version["status"] == "available"
        assert version["signature_verified_at"]
        detail = (
            await api.get(f"/v1/pack-versions/{version['id']}", headers=org.headers("owner"))
        ).json()
        listed = (await api.get("/v1/packs", headers=org.headers("owner", space=False))).json()
    assert [e["code"] for e in detail["entities"]] == ["loan"]
    assert {a["code"] for a in detail["entities"][0]["attributes"]} == {
        "loan_id",
        "debt_group",
        "legacy_flag",
    }
    assert [m["code"] for m in detail["metrics"]] == ["LOAN_GROWTH", "NPL_RATIO"]
    assert [r["code"] for r in detail["rules"]] == ["R1"]
    assert [s["code"] for s in detail["skills"]] == ["Q1"]
    assert [p["code"] for p in listed["items"]] == ["banking"]
    assert [e for e, _ in org.events(version["id"])] == ["pack.version_published"]


@pytest.mark.req("AC-09-01-02")
async def test_a_newer_version_tells_data_spaces_on_the_older_one(org: Org) -> None:
    async with client(org) as api:
        old = await _imported(api, org)
        install = await _active_install(api, org, old)
        await _imported(api, org, "2026.3")
    events = org.events(install)
    assert [e for e, _ in events][-1] == "pack.upgrade_ready"
    assert events[-1][1]["version"] == "2026.3"


@pytest.mark.req("AC-09-01-03")
async def test_a_resource_changed_after_signing_is_refused_with_a_security_warning(
    org: Org,
) -> None:
    async with client(org) as api:
        response = await _import(api, org, pack_file(manifest(), tamper=True))
    assert response.status_code == 422
    assert response.json()["code"] == "business_rule_violated"
    assert org.one("SELECT count(*) FROM pack.pack_version WHERE org_id = %s", org.org)[0] == 0
    warning = org.one(
        "SELECT payload FROM pack.outbox WHERE org_id = %s AND event_type = 'pack.import_rejected'",
        org.org,
    )
    assert warning[0]["severity"] == "security"


@pytest.mark.req("AC-09-01-04")
async def test_a_pack_for_a_newer_core_is_refused_naming_the_core_it_needs(org: Org) -> None:
    async with client(org) as api:
        response = await _import(api, org, pack_file(manifest(min_core_version="4.2")))
    assert response.status_code == 422
    assert "4.2" in response.json()["detail"]


@pytest.mark.req("AC-09-01-05")
async def test_only_a_trusted_key_signs_a_pack(org: Org) -> None:
    async with client(org) as api:
        stranger = await _import(api, org, pack_file(manifest(), key=Ed25519PrivateKey.generate()))
        assert stranger.status_code == 422
        assert stranger.json()["code"] == "business_rule_violated"
        await _imported(api, org)


async def test_import_needs_admin(org: Org) -> None:
    async with client(org) as api:
        response = await api.post(
            "/v1/packs:import",
            headers=org.headers("owner", space=False),
            files={"file": ("pack.json", pack_file(manifest()), "application/json")},
        )
    assert response.status_code == 403


# --- authoring (UC-09-05) -----------------------------------------------------------------------


async def _draft(api: httpx.AsyncClient, org: Org, pack_id: str, **body: Any) -> dict:
    content = manifest()
    payload = {
        "version": "2026.3",
        "min_core_version": "3.0",
        "manifest": {k: content[k] for k in ("entities", "metrics", "rules", "skills")},
        **body,
    }
    response = await api.post(
        f"/v1/packs/{pack_id}/versions", headers=org.headers("author", space=False), json=payload
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.req("AC-09-05-01")
async def test_author_validates_then_publishes(org: Org) -> None:
    async with client(org) as api:
        released = await _imported(api, org)
        draft = await _draft(api, org, released["pack_id"])
        assert draft["status"] == "draft"
        author = org.headers("author", space=False)
        early = await api.post(f"/v1/pack-versions/{draft['id']}:publish", headers=author)
        assert early.status_code == 409
        accepted = await api.post(f"/v1/pack-versions/{draft['id']}:validate", headers=author)
        assert accepted.status_code == 202
        assert accepted.headers["Location"] == f"/v1/pack-versions/{draft['id']}"
        assert accepted.json()["status"] == "queued"
        # The worker records the sample-data runs.
        org.seed.row(
            "pack.pack_check_run",
            id=uuid.UUID(accepted.json()["id"]),
            org_id=org.org,
            pack_version_id=uuid.UUID(draft["id"]),
            checks=json.dumps({"sample_datasets": "passed"}),
            passed=True,
            ran_by=org.users["author"],
        )
        published = await api.post(f"/v1/pack-versions/{draft['id']}:publish", headers=author)
    assert published.status_code == 200, published.text
    assert published.json()["status"] == "released"
    assert [e for e, _ in org.events(draft["id"])] == [
        "pack_version.created",
        "pack_version.validation_requested",
        "pack_version.released",
    ]


@pytest.mark.req("AC-09-05-02")
async def test_a_failed_check_keeps_publish_closed(org: Org) -> None:
    async with client(org) as api:
        released = await _imported(api, org)
        draft = await _draft(api, org, released["pack_id"])
        org.seed.row(
            "pack.pack_check_run",
            org_id=org.org,
            pack_version_id=uuid.UUID(draft["id"]),
            checks=json.dumps({"sample_datasets": "failed"}),
            passed=False,
            ran_by=org.users["author"],
        )
        response = await api.post(
            f"/v1/pack-versions/{draft['id']}:publish", headers=org.headers("author", space=False)
        )
    assert response.status_code == 409
    assert response.json()["code"] == "invalid_state"


@pytest.mark.req("AC-09-05-06")
async def test_validate_reports_a_released_code_given_another_meaning(org: Org) -> None:
    async with client(org) as api:
        released = await _imported(api, org)
        content = manifest()
        content["metrics"][0]["expression"] = "something else"
        response = await api.post(
            f"/v1/packs/{released['pack_id']}/versions",
            headers=org.headers("author", space=False),
            json={
                "version": "2026.3",
                "min_core_version": "3.0",
                "manifest": {"entities": [], "metrics": content["metrics"]},
            },
        )
        draft = response.json()
        accepted = await api.post(
            f"/v1/pack-versions/{draft['id']}:validate", headers=org.headers("author", space=False)
        )
        detail = (
            await api.get(f"/v1/pack-versions/{draft['id']}", headers=org.headers("author", False))
        ).json()
    assert accepted.status_code == 202
    assert accepted.json()["status"] == "failed"
    assert detail["latest_check_run"]["passed"] is False
    assert detail["latest_check_run"]["checks"]["code_reuse"] == ["NPL_RATIO"]


@pytest.mark.req("AC-09-05-04")
async def test_a_breaking_change_needs_its_migration_guide(org: Org) -> None:
    async with client(org) as api:
        released = await _imported(api, org)
        draft = await _draft(
            api, org, released["pack_id"], breaking_changes=[{"code": "OLD", "kind": "rename"}]
        )
        accepted = await api.post(
            f"/v1/pack-versions/{draft['id']}:validate", headers=org.headers("author", space=False)
        )
    assert accepted.json()["status"] == "failed"


@pytest.mark.req("AC-09-05-05")
async def test_a_metric_without_legal_basis_must_be_internal(org: Org) -> None:
    async with client(org) as api:
        released = await _imported(api, org)
        response = await api.post(
            f"/v1/packs/{released['pack_id']}/versions",
            headers=org.headers("author", space=False),
            json={
                "version": "2026.4",
                "min_core_version": "3.0",
                "manifest": {"metrics": [{"code": "X", "name": "X", "expression": "x"}]},
            },
        )
    assert response.status_code == 422
    assert response.json()["code"] == "business_rule_violated"


# --- onboarding (UC-09-02) ----------------------------------------------------------------------


@pytest.mark.req("AC-09-02-01")
async def test_onboarding_maps_calibrates_and_asks_for_activation(org: Org) -> None:
    async with client(org) as api:
        version = await _imported(api, org)
        h = org.headers("owner")
        created = await api.post(
            "/v1/pack-installs", headers=h, json={"pack_version_id": version["id"]}
        )
        assert created.status_code == 201, created.text
        install = created.json()
        assert install["status"] == "mapping"
        suggest = await api.post(f"/v1/pack-installs/{install['id']}:suggest-mappings", headers=h)
        assert suggest.status_code == 202
        assert suggest.headers["Location"].endswith("/mappings")
        dataset = _dataset(org)
        loan_id = _attribute(org, version["id"], "loan_id")
        group = _attribute(org, version["id"], "debt_group")
        mapped = await api.put(
            f"/v1/pack-installs/{install['id']}/mappings",
            headers=h,
            json={
                "entity_mappings": [
                    {"attribute_id": str(loan_id), "dataset_id": str(dataset), "column_expr": "id"},
                    {"attribute_id": str(group), "dataset_id": str(dataset), "column_expr": "grp"},
                ],
                "code_mappings": [
                    {"attribute_id": str(group), "source_code": "A", "canonical_code": "1"}
                ],
            },
        )
        assert mapped.status_code == 200, mapped.text
        assert {m["column_expr"] for m in mapped.json()} == {"id", "grp"}
        calibrate = await api.post(
            f"/v1/pack-installs/{install['id']}:calibrate",
            headers=h,
            json={"periods": ["2026-08", "2026-09"]},
        )
        assert calibrate.status_code == 202, calibrate.text
        run = calibrate.json()["id"]
        not_yet = await api.post(f"/v1/pack-installs/{install['id']}:activate", headers=h)
        assert not_yet.status_code == 422
        org.execute("UPDATE pack.calibration_run SET status = 'passed' WHERE id = %s", run)
        activated = await api.post(f"/v1/pack-installs/{install['id']}:activate", headers=h)
    assert activated.status_code == 200, activated.text
    assert activated.json()["status"] == "pending_activation"
    assert [e for e, _ in org.events(install["id"])] == [
        "pack_install.created",
        "pack_install.mapping_suggestion_requested",
        "pack_install.mappings_confirmed",
        "pack_install.activation_requested",
    ]
    assert [e for e, _ in org.events(run)] == ["calibration_run.requested"]
    codes = org.one(
        "SELECT count(*) FROM pack.code_mapping WHERE pack_install_id = %s", install["id"]
    )
    assert codes[0] == 1


async def test_a_data_space_has_one_live_install(org: Org) -> None:
    async with client(org) as api:
        version = await _imported(api, org)
        body = {"pack_version_id": version["id"]}
        first = await api.post("/v1/pack-installs", headers=org.headers("owner"), json=body)
        second = await api.post("/v1/pack-installs", headers=org.headers("owner"), json=body)
    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["code"] == "conflict"


@pytest.mark.req("AC-09-02-07")
async def test_remapping_closes_the_old_mapping(org: Org) -> None:
    async with client(org) as api:
        version = await _imported(api, org)
        install = (
            await api.post(
                "/v1/pack-installs",
                headers=org.headers("owner"),
                json={"pack_version_id": version["id"]},
            )
        ).json()["id"]
        dataset = _dataset(org)
        attr = str(_attribute(org, version["id"], "loan_id"))
        for column in ("id", "loan_no"):
            response = await api.put(
                f"/v1/pack-installs/{install}/mappings",
                headers=org.headers("owner"),
                json={
                    "entity_mappings": [
                        {"attribute_id": attr, "dataset_id": str(dataset), "column_expr": column}
                    ]
                },
            )
            assert response.status_code == 200, response.text
    rows = org.one(
        "SELECT count(*), count(valid_to) FROM pack.entity_mapping WHERE pack_install_id = %s",
        install,
    )
    assert rows == (2, 1)


async def test_mapping_rejects_an_attribute_of_another_version_and_a_foreign_dataset(
    org: Org,
) -> None:
    async with client(org) as api:
        version = await _imported(api, org)
        install = (
            await api.post(
                "/v1/pack-installs",
                headers=org.headers("owner"),
                json={"pack_version_id": version["id"]},
            )
        ).json()["id"]
        attr = str(_attribute(org, version["id"], "loan_id"))
        response = await api.put(
            f"/v1/pack-installs/{install}/mappings",
            headers=org.headers("owner"),
            json={
                "entity_mappings": [
                    {"attribute_id": attr, "dataset_id": str(uuid.uuid4()), "column_expr": "id"}
                ]
            },
        )
        bad_code = await api.put(
            f"/v1/pack-installs/{install}/mappings",
            headers=org.headers("owner"),
            json={
                "entity_mappings": [],
                "code_mappings": [
                    {
                        "attribute_id": str(_attribute(org, version["id"], "debt_group")),
                        "source_code": "A",
                        "canonical_code": "9",
                    }
                ],
            },
        )
    assert response.status_code == 422
    assert bad_code.status_code == 422


@pytest.mark.req("AC-09-02-06")
async def test_activation_is_blocked_by_an_unexplained_regulatory_difference(org: Org) -> None:
    async with client(org) as api:
        version = await _imported(api, org)
        install = (
            await api.post(
                "/v1/pack-installs",
                headers=org.headers("owner"),
                json={"pack_version_id": version["id"]},
            )
        ).json()["id"]
        run = org.seed.row(
            "pack.calibration_run",
            org_id=org.org,
            pack_install_id=uuid.UUID(install),
            trigger="onboarding",
            periods=["2026-09"],
            status="failed",
        )
        for code, passed in (("NPL_RATIO", False), ("LOAN_GROWTH", False)):
            org.seed.row(
                "pack.calibration_item",
                org_id=org.org,
                calibration_run_id=run,
                item_kind="metric",
                item_code=code,
                passed=passed,
            )
        refused = await api.post(f"/v1/pack-installs/{install}:activate", headers=org.headers("owner"))
        assert refused.status_code == 422
        assert "NPL_RATIO" in refused.json()["detail"]
        assert "LOAN_GROWTH" not in refused.json()["detail"]
        org.seed.row(
            "pack.metric_override",
            org_id=org.org,
            pack_install_id=uuid.UUID(install),
            pack_metric_code="NPL_RATIO",
            expression="x",
            reason="Quy định nội bộ",
            status="active",
            approved_by=org.users["approver"],
        )
        allowed = await api.post(f"/v1/pack-installs/{install}:activate", headers=org.headers("owner"))
    assert allowed.status_code == 200, allowed.text


# --- overrides (UC-09-03) -----------------------------------------------------------------------


async def _override(api: httpx.AsyncClient, org: Org, install: str, code: str) -> dict:
    response = await api.post(
        "/v1/metric-overrides",
        headers=org.headers("owner"),
        json={
            "pack_install_id": install,
            "pack_metric_code": code,
            "expression": "sum(bad - staff)/sum(all)",
            "scope_condition": "segment = 'retail'",
            "reason": "Loại trừ khoản vay nhân viên",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.req("AC-09-03-01", "AC-09-03-03", "AC-09-03-07")
async def test_a_regulatory_override_needs_approve_then_compliance(org: Org) -> None:
    async with client(org) as api:
        version = await _imported(api, org)
        install = await _active_install(api, org, version)
        override = await _override(api, org, install, "NPL_RATIO")
        assert override["status"] == "pending_review"
        assert override["scope_condition"] == "segment = 'retail'"
        url = f"/v1/metric-overrides/{override['id']}:approve"
        own = await api.post(url, headers=org.headers("owner"))
        assert own.status_code == 403
        first = await api.post(url, headers=org.headers("approver"))
        assert first.status_code == 200
        assert first.json()["status"] == "pending_review"
        second_without_compliance = await api.post(url, headers=org.headers("approver"))
        assert second_without_compliance.status_code == 403
        final = await api.post(url, headers=org.headers("controller"))
    assert final.status_code == 200, final.text
    body = final.json()
    assert body["status"] == "active"
    assert body["approved_by"] == str(org.users["approver"])
    assert body["compliance_approved_by"] == str(org.users["controller"])
    assert [e for e, _ in org.events(override["id"])] == [
        "metric_override.submitted",
        "metric_override.step_approved",
        "metric_override.approved",
    ]


async def test_an_internal_metric_override_is_active_after_one_approval(org: Org) -> None:
    async with client(org) as api:
        version = await _imported(api, org)
        install = await _active_install(api, org, version)
        first = await _override(api, org, install, "LOAN_GROWTH")
        url = f"/v1/metric-overrides/{first['id']}:approve"
        approved = await api.post(url, headers=org.headers("approver"))
        again = await api.post(url, headers=org.headers("controller"))
        unknown = await api.post(
            "/v1/metric-overrides",
            headers=org.headers("owner"),
            json={
                "pack_install_id": install,
                "pack_metric_code": "NOPE",
                "expression": "x",
                "reason": "r",
            },
        )
    assert approved.json()["status"] == "active"
    assert again.status_code == 409
    assert unknown.status_code == 422


# --- upgrades (UC-09-04) ------------------------------------------------------------------------


async def _upgradable(api: httpx.AsyncClient, org: Org) -> tuple[str, dict, dict]:
    old = await _imported(api, org)
    install = await _active_install(api, org, old)
    m = manifest("2026.3")
    m["metrics"][0]["expression"] = "sum(bad)/sum(all) new"
    m["metrics"][1]["expression"] = "growth(all) new"
    new = await _imported(api, org, "2026.3", metrics=m["metrics"])
    override = await _override(api, org, install, "NPL_RATIO")
    await api.post(f"/v1/metric-overrides/{override['id']}:approve", headers=org.headers("approver"))
    await api.post(
        f"/v1/metric-overrides/{override['id']}:approve", headers=org.headers("controller")
    )
    return install, old, new


@pytest.mark.req("AC-09-04-01", "AC-09-04-02")
async def test_upgrade_merges_decides_applies_and_rolls_back(org: Org) -> None:
    async with client(org) as api:
        install, old, new = await _upgradable(api, org)
        h = org.headers("owner")
        dry = await api.post(
            f"/v1/pack-installs/{install}:upgrade?dry_run=true",
            headers=h,
            json={"to_version_id": new["id"]},
        )
        assert dry.status_code == 200, dry.text
        assert dry.json()["dry_run"] is True
        assert org.one("SELECT count(*) FROM pack.pack_upgrade WHERE org_id = %s", org.org)[0] == 0
        created = await api.post(
            f"/v1/pack-installs/{install}:upgrade", headers=h, json={"to_version_id": new["id"]}
        )
        assert created.status_code == 201, created.text
        upgrade = created.json()
        outcomes = {i["code"]: i["outcome"] for i in upgrade["merge_result"]["items"]}
        assert outcomes == {"NPL_RATIO": "conflict", "LOAN_GROWTH": "take_new"}
        assert upgrade["status"] == "needs_decision"
        assert upgrade["conflicts_open"] == 1
        early = await api.post(
            f"/v1/pack-upgrades/{upgrade['id']}:apply", headers=org.headers("approver"), json={}
        )
        assert early.status_code == 409
        decided = await api.put(
            f"/v1/pack-upgrades/{upgrade['id']}/decisions",
            headers=h,
            json={"decisions": [{"code": "NPL_RATIO", "decision": "keep_override"}]},
        )
        assert decided.json()["status"] == "testing"
        assert decided.json()["conflicts_open"] == 0
        # The test run of workflow pack_upgrade passed.
        org.execute(
            "UPDATE pack.pack_upgrade SET status = 'pending_approval' WHERE id = %s", upgrade["id"]
        )
        own = await api.post(
            f"/v1/pack-upgrades/{upgrade['id']}:apply", headers=h, json={}
        )
        assert own.status_code == 403
        applied = await api.post(
            f"/v1/pack-upgrades/{upgrade['id']}:apply",
            headers=org.headers("approver"),
            json={"apply_from_period": "2026-11"},
        )
        assert applied.status_code == 200, applied.text
        body = applied.json()
        assert body["status"] == "applied"
        assert body["apply_from_period"] == "2026-11"
        rollback_until = datetime.datetime.fromisoformat(body["rollback_until"])
        assert rollback_until - datetime.datetime.now(datetime.UTC) > datetime.timedelta(days=29)
        assert org.one("SELECT pack_version_id FROM pack.pack_install WHERE id = %s", install)[
            0
        ] == uuid.UUID(new["id"])
        rolled = await api.post(
            f"/v1/pack-upgrades/{upgrade['id']}:rollback",
            headers=org.headers("admin"),
            json={"reason": "Số lệch"},
        )
    assert rolled.status_code == 200, rolled.text
    assert rolled.json()["status"] == "rolled_back"
    installed = org.one(
        "SELECT pack_version_id, status FROM pack.pack_install WHERE id = %s", install
    )
    assert installed == (uuid.UUID(old["id"]), "active")
    active = org.one(
        "SELECT count(*) FROM pack.metric_override WHERE pack_install_id = %s AND status = 'active'",
        install,
    )
    assert active[0] == 1
    assert [e for e, _ in org.events(upgrade["id"])] == [
        "pack_upgrade.created",
        "pack_upgrade.decided",
        "pack_upgrade.applied",
        "pack_upgrade.rolled_back",
    ]


@pytest.mark.req("AC-09-04-04")
async def test_rollback_is_refused_after_30_days(org: Org) -> None:
    async with client(org) as api:
        install, old, new = await _upgradable(api, org)
        upgrade = org.seed.row(
            "pack.pack_upgrade",
            org_id=org.org,
            pack_install_id=uuid.UUID(install),
            from_version_id=uuid.UUID(old["id"]),
            to_version_id=uuid.UUID(new["id"]),
            status="applied",
            approved_by=org.users["approver"],
            rollback_until=datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=1),
        )
        response = await api.post(
            f"/v1/pack-upgrades/{upgrade}:rollback",
            headers=org.headers("admin"),
            json={"reason": "Muộn"},
        )
    assert response.status_code == 409
    assert response.json()["code"] == "invalid_state"


@pytest.mark.req("AC-09-04-06")
async def test_no_upgrade_while_a_period_is_closing(org: Org) -> None:
    async with client(org) as api:
        install, _, new = await _upgradable(api, org)
        org.seed.row(
            "period.period_close",
            org_id=org.org,
            data_space_id=org.space,
            period_label="2026-10",
            business_date=datetime.date(2026, 10, 31),
            status="closing",
        )
        response = await api.post(
            f"/v1/pack-installs/{install}:upgrade",
            headers=org.headers("owner"),
            json={"to_version_id": new["id"]},
        )
    assert response.status_code == 423
    assert response.json()["code"] == "period_locked"
    assert "2026-10" in response.json()["detail"]


@pytest.mark.req("AC-09-01-07")
async def test_a_breaking_change_cannot_apply_before_30_days(org: Org) -> None:
    async with client(org) as api:
        old = await _imported(api, org)
        install = await _active_install(api, org, old)
        new = await _imported(
            api,
            org,
            "2026.3",
            breaking_changes=[{"code": "LOAN_GROWTH", "migration": "Đổi tên"}],
        )
        created = await api.post(
            f"/v1/pack-installs/{install}:upgrade",
            headers=org.headers("owner"),
            json={"to_version_id": new["id"]},
        )
        assert created.status_code == 201, created.text
        upgrade = created.json()["id"]
        org.execute("UPDATE pack.pack_upgrade SET status = 'pending_approval' WHERE id = %s", upgrade)
        response = await api.post(
            f"/v1/pack-upgrades/{upgrade}:apply", headers=org.headers("approver"), json={}
        )
    assert response.status_code == 422
    assert "BR-131" in response.json()["detail"]


async def test_another_data_space_does_not_see_the_install(org: Org) -> None:
    other = org.seed.row(
        "org.data_space", org_id=org.org, code="DS-OTHER", name="Khác", steward_id=org.users["owner"]
    )
    role = org.one("SELECT role_id FROM org.user_role WHERE user_id = %s", org.users["owner"])[0]
    org.seed.row(
        "org.data_space_member",
        org_id=org.org,
        data_space_id=other,
        principal_type="user",
        principal_id=org.users["owner"],
        role_id=role,
    )
    async with client(org) as api:
        version = await _imported(api, org)
        install = await _active_install(api, org, version)
        h = {**org.headers("owner"), "X-Data-Space": str(other)}
        response = await api.post(f"/v1/pack-installs/{install}:suggest-mappings", headers=h)
    assert response.status_code == 404
