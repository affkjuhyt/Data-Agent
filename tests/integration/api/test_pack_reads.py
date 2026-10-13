"""The pack endpoints added 12/10/2026 against PostgreSQL: POST /v1/packs, GET /v1/pack-installs/{id}
and its /mappings, GET /v1/calibration-runs/{id}, GET /v1/metric-overrides/{id} and
GET /v1/pack-upgrades/{id} (UC-09-02 … UC-09-05).

Organisation, users, signed pack files and installs come from the pack suite. A pack is the
organisation's; an install and everything under it belongs to one Data Space, so reading it with
another X-Data-Space is a 404.
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from . import test_pack
from .test_pack import (
    Org,
    _active_install,
    _attribute,
    _dataset,
    _imported,
    _override,
    _upgradable,
    client,
)

pytestmark = [pytest.mark.integration, pytest.mark.contract]
# The pack suite's fixtures, reused: the same organisation, users and trusted signing key.
org = test_pack.org
_trusted_key = test_pack._trusted_key


def _other_space(org: Org) -> dict[str, str]:
    """The owner's headers in a second Data Space they are a member of."""
    other = org.seed.row(
        "org.data_space",
        org_id=org.org,
        code="DS-OTHER",
        name="Khác",
        steward_id=org.users["owner"],
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
    return {**org.headers("owner"), "X-Data-Space": str(other)}


def _no_ask(org: Org) -> dict[str, str]:
    """A member of the Data Space whose role does not grant ask, the capability of the reads."""
    tag = org.org.hex[:6]
    role = org.seed.row(
        "org.role", org_id=org.org, code="R-noask", name="noask", capabilities=["upload"]
    )
    user = org.seed.row(
        "org.app_user",
        org_id=org.org,
        external_id=f"kc-noask-{tag}",
        username="noask",
        display_name="noask",
        status="active",
    )
    org.seed.row("org.user_role", org_id=org.org, user_id=user, role_id=role, source="manual")
    org.seed.row(
        "org.data_space_member",
        org_id=org.org,
        data_space_id=org.space,
        principal_type="user",
        principal_id=user,
        role_id=role,
    )
    return org.headers("noask")


async def _install(api: httpx.AsyncClient, org: Org) -> tuple[str, dict]:
    version = await _imported(api, org)
    response = await api.post(
        "/v1/pack-installs", headers=org.headers("owner"), json={"pack_version_id": version["id"]}
    )
    assert response.status_code == 201, response.text
    return response.json()["id"], version


# --- POST /v1/packs (UC-09-05) -------------------------------------------------------------------


async def test_an_author_creates_the_organisations_own_pack(org: Org) -> None:
    author = org.headers("author", space=False)
    async with client(org) as api:
        created = await api.post(
            "/v1/packs",
            headers=author,
            json={"code": "retail-own", "name": "Bán lẻ riêng", "description": "Gói của tổ chức"},
        )
        listed = (await api.get("/v1/packs?code=retail-own", headers=author)).json()
    assert created.status_code == 201, created.text
    pack = created.json()
    assert pack["code"] == "retail-own"
    assert pack["name"] == "Bán lẻ riêng"
    assert pack["description"] == "Gói của tổ chức"
    # The organisation publishes it; no industry recorded for the test organisation.
    assert pack["publisher"] == "Tổ chức thử"
    assert pack["industry_code"] == "retail-own"
    # Unsigned until its first version is published, unlike a vendor's pack.
    assert (pack["origin"], pack["publisher_key_id"]) == ("organisation", None)
    assert "org_id" not in pack and "created_by" not in pack
    assert [p["id"] for p in listed["items"]] == [pack["id"]]
    row = org.one(
        "SELECT org_id::text, created_by::text FROM pack.industry_pack WHERE id = %s", pack["id"]
    )
    assert row == (str(org.org), str(org.users["author"]))
    assert org.one("SELECT count(*) FROM pack.pack_version WHERE pack_id = %s", pack["id"])[0] == 0
    assert [e for e, _ in org.events(pack["id"])] == ["industry_pack.created"]


async def test_a_pack_code_is_taken_once_per_organisation(org: Org) -> None:
    author = org.headers("author", space=False)
    async with client(org) as api:
        await _imported(api, org)
        vendor_code = await api.post(
            "/v1/packs", headers=author, json={"code": "banking", "name": "Trùng"}
        )
        first = await api.post("/v1/packs", headers=author, json={"code": "own", "name": "A"})
        again = await api.post("/v1/packs", headers=author, json={"code": "own", "name": "B"})
        invalid = await api.post("/v1/packs", headers=author, json={"code": "own2"})
    assert vendor_code.status_code == 409
    # An imported pack is the vendor's and carries the key that signed it.
    assert org.one(
        "SELECT origin, publisher_key_id IS NOT NULL FROM pack.industry_pack"
        " WHERE org_id = %s AND code = 'banking'",
        org.org,
    ) == ("vendor", True)
    assert vendor_code.json()["code"] == "conflict"
    assert first.status_code == 201
    assert again.status_code == 409
    assert again.json()["code"] == "conflict"
    assert invalid.status_code == 422
    assert org.one("SELECT count(*) FROM pack.industry_pack WHERE org_id = %s", org.org)[0] == 2


async def test_creating_a_pack_needs_pack_author(org: Org) -> None:
    async with client(org) as api:
        response = await api.post(
            "/v1/packs",
            headers=org.headers("owner", space=False),
            json={"code": "own", "name": "Riêng"},
        )
    assert response.status_code == 403
    assert org.one("SELECT count(*) FROM pack.industry_pack WHERE org_id = %s", org.org)[0] == 0


# --- GET /v1/pack-installs/{id} and /mappings (UC-09-02) -------------------------------------------


async def _put_mappings(api: httpx.AsyncClient, org: Org, install: str, version: dict) -> str:
    """loan_id and debt_group mapped, with a code table for debt_group."""
    dataset = _dataset(org)
    group = str(_attribute(org, version["id"], "debt_group"))
    response = await api.put(
        f"/v1/pack-installs/{install}/mappings",
        headers=org.headers("owner"),
        json={
            "entity_mappings": [
                {
                    "attribute_id": str(_attribute(org, version["id"], "loan_id")),
                    "dataset_id": str(dataset),
                    "column_expr": "id",
                },
                {"attribute_id": group, "dataset_id": str(dataset), "column_expr": "grp"},
            ],
            "code_mappings": [
                {"attribute_id": group, "source_code": "A", "canonical_code": "1"},
                {"attribute_id": group, "source_code": "B", "canonical_code": "2"},
            ],
        },
    )
    assert response.status_code == 200, response.text
    return group


async def test_an_install_shows_its_mapping_progress_and_activation(org: Org) -> None:
    async with client(org) as api:
        install, version = await _install(api, org)
        empty = (await api.get(f"/v1/pack-installs/{install}", headers=org.headers("owner"))).json()
        await _put_mappings(api, org, install, version)
        org.execute(
            "UPDATE pack.canonical_attribute SET required = true WHERE id = %s",
            _attribute(org, version["id"], "loan_id"),
        )
        calibrate = await api.post(
            f"/v1/pack-installs/{install}:calibrate", headers=org.headers("owner"), json={}
        )
        response = await api.get(f"/v1/pack-installs/{install}", headers=org.headers("owner"))
    assert empty["mapped"] == 0 and empty["total"] == 3
    assert empty["latest_calibration_run"] is None
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == install
    assert body["data_space_id"] == str(org.space)
    assert (body["pack_code"], body["version"]) == ("banking", version["version"])
    assert body["status"] == "calibrating"
    assert body["entities"] == [
        {
            "code": "loan",
            "name": "Khoản vay",
            "mapped": 2,
            "total": 3,
            "required_mapped": 1,
            "required_total": 1,
        }
    ]
    assert (body["mapped"], body["total"]) == (2, 3)
    assert body["latest_calibration_run"]["id"] == calibrate.json()["id"]
    assert body["latest_calibration_run"]["status"] == "running"


async def test_mappings_list_the_current_ones_by_entity_with_their_code_table(org: Org) -> None:
    h = org.headers("owner")
    async with client(org) as api:
        install, version = await _install(api, org)
        group = await _put_mappings(api, org, install, version)
        dataset = org.one(
            "SELECT dataset_id FROM pack.entity_mapping WHERE pack_install_id = %s LIMIT 1", install
        )[0]
        # Remapping loan_id closes the first mapping (BR-37): only the new one is listed.
        await api.put(
            f"/v1/pack-installs/{install}/mappings",
            headers=h,
            json={
                "entity_mappings": [
                    {
                        "attribute_id": str(_attribute(org, version["id"], "loan_id")),
                        "dataset_id": str(dataset),
                        "column_expr": "loan_no",
                    },
                    {"attribute_id": group, "dataset_id": str(dataset), "column_expr": "grp"},
                ]
            },
        )
        listed = await api.get(f"/v1/pack-installs/{install}/mappings?entity=loan", headers=h)
        other_entity = await api.get(
            f"/v1/pack-installs/{install}/mappings?entity=deposit", headers=h
        )
        first = await api.get(f"/v1/pack-installs/{install}/mappings?page_size=1", headers=h)
        second = await api.get(
            f"/v1/pack-installs/{install}/mappings?page_size=1&cursor="
            + first.json()["next_page_token"],
            headers=h,
        )
    assert listed.status_code == 200, listed.text
    items = listed.json()["items"]
    assert {i["attribute_code"]: i["column_expr"] for i in items} == {
        "loan_id": "loan_no",
        "debt_group": "grp",
    }
    assert {i["entity_code"] for i in items} == {"loan"}
    by_attr = {i["attribute_code"]: i for i in items}
    assert {
        (c["source_code"], c["canonical_code"]) for c in by_attr["debt_group"]["code_mappings"]
    } == {
        ("A", "1"),
        ("B", "2"),
    }
    assert by_attr["loan_id"]["code_mappings"] == []
    assert all(i["valid_to"] is None for i in items)
    assert other_entity.json()["items"] == []
    assert len(first.json()["items"]) == 1 and first.json()["next_page_token"]
    assert len(second.json()["items"]) == 1
    assert {first.json()["items"][0]["id"], second.json()["items"][0]["id"]} == {
        i["id"] for i in items
    }


async def test_an_install_is_read_in_its_own_data_space_only(org: Org) -> None:
    async with client(org) as api:
        install, _ = await _install(api, org)
        other = _other_space(org)
        responses = [
            await api.get(f"/v1/pack-installs/{install}", headers=other),
            await api.get(f"/v1/pack-installs/{install}/mappings", headers=other),
            await api.get(f"/v1/pack-installs/{uuid.uuid4()}", headers=org.headers("owner")),
            await api.get(
                f"/v1/pack-installs/{uuid.uuid4()}/mappings", headers=org.headers("owner")
            ),
        ]
        no_space = await api.get(
            f"/v1/pack-installs/{install}", headers=org.headers("owner", space=False)
        )
    assert [r.status_code for r in responses] == [404, 404, 404, 404]
    assert all(r.json()["code"] == "not_found" for r in responses)
    assert no_space.status_code == 400


# --- GET /v1/calibration-runs/{id} (UC-09-02) -------------------------------------------------------


async def test_calibrate_points_to_the_run_that_reports_each_difference(org: Org) -> None:
    h = org.headers("owner")
    async with client(org) as api:
        install, version = await _install(api, org)
        await _put_mappings(api, org, install, version)
        accepted = await api.post(
            f"/v1/pack-installs/{install}:calibrate", headers=h, json={"periods": ["2026-09"]}
        )
        run = accepted.json()["id"]
        assert accepted.headers["Location"] == f"/v1/calibration-runs/{run}"
        running = await api.get(accepted.headers["Location"], headers=h)
        # The worker of workflow pack_calibrate records each item and the outcome.
        for code, filed, passed, cause in (
            ("NPL_RATIO", "0.0310", False, "Khoản vay nhân viên bị tính vào nợ xấu"),
            ("LOAN_GROWTH", "0.1200", True, None),
        ):
            org.seed.row(
                "pack.calibration_item",
                org_id=org.org,
                calibration_run_id=uuid.UUID(run),
                item_kind="metric",
                item_code=code,
                period="2026-09",
                computed_value=0.0325 if not passed else 0.12,
                filed_value=filed,
                delta=0.0015 if not passed else 0,
                passed=passed,
                suggested_cause=cause,
            )
        org.execute(
            "UPDATE pack.calibration_run SET status = 'failed', passed = 1, failed = 1, "
            "finished_at = now() WHERE id = %s",
            run,
        )
        finished = await api.get(f"/v1/calibration-runs/{run}", headers=h)
    assert running.status_code == 200, running.text
    assert running.json()["status"] == "running"
    assert running.json()["periods"] == ["2026-09"]
    assert running.json()["items"] == []
    body = finished.json()
    assert (body["status"], body["passed"], body["failed"]) == ("failed", 1, 1)
    assert body["pack_install_id"] == install
    assert body["finished_at"]
    assert [(i["item_code"], i["passed"]) for i in body["items"]] == [
        ("LOAN_GROWTH", True),
        ("NPL_RATIO", False),
    ]
    assert body["items"][1]["suggested_cause"] == "Khoản vay nhân viên bị tính vào nợ xấu"
    assert body["items"][1]["filed_value"] == "0.0310"


async def test_a_calibration_run_is_read_in_its_data_space_by_a_caller_with_ask(org: Org) -> None:
    async with client(org) as api:
        install, _ = await _install(api, org)
        run = org.seed.row(
            "pack.calibration_run",
            org_id=org.org,
            pack_install_id=uuid.UUID(install),
            trigger="onboarding",
            periods=["2026-09"],
        )
        noask = await api.get(f"/v1/calibration-runs/{run}", headers=_no_ask(org))
        other = await api.get(f"/v1/calibration-runs/{run}", headers=_other_space(org))
        unknown = await api.get(
            f"/v1/calibration-runs/{uuid.uuid4()}", headers=org.headers("owner")
        )
        own = await api.get(f"/v1/calibration-runs/{run}", headers=org.headers("owner"))
    assert noask.status_code == 403
    assert other.status_code == 404
    assert unknown.status_code == 404
    assert own.status_code == 200


# --- GET /v1/metric-overrides/{id} (UC-09-03) -------------------------------------------------------


async def test_an_override_shows_both_formulas_and_the_approval_it_waits_for(org: Org) -> None:
    async with client(org) as api:
        version = await _imported(api, org)
        install = await _active_install(api, org, version)
        override = await _override(api, org, install, "NPL_RATIO")
        url = f"/v1/metric-overrides/{override['id']}"
        submitted = await api.get(url, headers=org.headers("owner"))
        await api.post(f"{url}:approve", headers=org.headers("approver"))
        halfway = await api.get(url, headers=org.headers("owner"))
        await api.post(f"{url}:approve", headers=org.headers("controller"))
        active = await api.get(url, headers=org.headers("owner"))
        # The trial of the formula is a calibration run the override points to (step 3).
        trial = org.seed.row(
            "pack.calibration_run",
            org_id=org.org,
            pack_install_id=uuid.UUID(install),
            trigger="override",
            periods=["2026-09"],
            status="failed",
            passed=1,
            failed=1,
        )
        org.execute(
            "UPDATE pack.metric_override SET calibration_run_id = %s WHERE id = %s",
            trial,
            override["id"],
        )
        tried = await api.get(url, headers=org.headers("owner"))
        other = await api.get(url, headers=_other_space(org))
        noask = await api.get(url, headers=_no_ask(org))
        unknown = await api.get(
            f"/v1/metric-overrides/{uuid.uuid4()}", headers=org.headers("owner")
        )
    assert submitted.status_code == 200, submitted.text
    body = submitted.json()
    assert body["id"] == override["id"]
    assert body["pack_metric_code"] == "NPL_RATIO"
    assert body["base_expression"] == "sum(bad)/sum(all)"
    assert body["expression"] == "sum(bad - staff)/sum(all)"
    assert body["metric_name"] == "Tỷ lệ nợ xấu"
    assert body["reason"] == "Loại trừ khoản vay nhân viên"
    assert body["in_regulatory_report"] is True
    assert (body["status"], body["awaiting"]) == ("pending_review", "approve")
    assert halfway.json()["awaiting"] == "compliance"
    assert halfway.json()["approved_by"] == str(org.users["approver"])
    assert (active.json()["status"], active.json()["awaiting"]) == ("active", None)
    assert (body["calibration_run_id"], body["calibration_run"]) == (None, None)
    assert tried.json()["calibration_run_id"] == str(trial)
    assert tried.json()["calibration_run"]["id"] == str(trial)
    assert (
        tried.json()["calibration_run"]["status"],
        tried.json()["calibration_run"]["failed"],
    ) == (
        "failed",
        1,
    )
    assert other.status_code == 404
    assert noask.status_code == 403
    assert unknown.status_code == 404


# --- GET /v1/pack-upgrades/{id} (UC-09-04) ----------------------------------------------------------


async def test_an_upgrade_shows_its_conflicts_decisions_and_test_run(org: Org) -> None:
    h = org.headers("owner")
    async with client(org) as api:
        install, _, new = await _upgradable(api, org)
        created = await api.post(
            f"/v1/pack-installs/{install}:upgrade", headers=h, json={"to_version_id": new["id"]}
        )
        assert created.status_code == 201, created.text
        url = f"/v1/pack-upgrades/{created.json()['id']}"
        open_ = await api.get(url, headers=h)
        await api.put(
            f"{url}/decisions",
            headers=h,
            json={"decisions": [{"code": "NPL_RATIO", "decision": "keep_override"}]},
        )
        # The test run of workflow pack_upgrade is a calibration run the upgrade points to.
        run = org.seed.row(
            "pack.calibration_run",
            org_id=org.org,
            pack_install_id=uuid.UUID(install),
            trigger="upgrade",
            periods=["2026-09"],
            status="passed",
            passed=2,
        )
        org.execute(
            "UPDATE pack.pack_upgrade SET calibration_run_id = %s, status = 'pending_approval' "
            "WHERE id = %s",
            run,
            created.json()["id"],
        )
        decided = await api.get(url, headers=h)
        other = await api.get(url, headers=_other_space(org))
        noask = await api.get(url, headers=_no_ask(org))
        unknown = await api.get(f"/v1/pack-upgrades/{uuid.uuid4()}", headers=h)
    assert open_.status_code == 200, open_.text
    body = open_.json()
    assert (body["status"], body["conflicts_open"]) == ("needs_decision", 1)
    assert [(c["code"], c["decision"]) for c in body["conflicts"]] == [("NPL_RATIO", None)]
    assert body["decisions"] == []
    assert body["calibration_run"] is None
    assert body["dry_run"] is False
    after = decided.json()
    assert (after["status"], after["conflicts_open"]) == ("pending_approval", 0)
    assert [(d["code"], d["decision"]) for d in after["decisions"]] == [
        ("NPL_RATIO", "keep_override")
    ]
    assert after["decisions"][0]["decided_by"] == str(org.users["owner"])
    assert after["calibration_run"]["id"] == str(run)
    assert (after["calibration_run"]["status"], after["calibration_run"]["passed"]) == ("passed", 2)
    assert other.status_code == 404
    assert noask.status_code == 403
    assert unknown.status_code == 404
