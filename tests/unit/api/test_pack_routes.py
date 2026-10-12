"""Gói ngành: the pack file and merge rules, request validation and error mapping, no database.

Behaviour against PostgreSQL is in tests/integration/api/test_pack.py.
"""

from __future__ import annotations

import base64
import hashlib
import json
import uuid
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient

from services.api.app import create_app
from services.api.config import Settings
from services.api.modules.pack import rules

from .conftest import ORG, FakeIdentities, FakeVerifier

pytestmark = [pytest.mark.unit, pytest.mark.contract]

TOKENS = {
    "admin": {"sub": "kc-admin", "org_id": str(ORG)},
    "owner": {"sub": "kc-owner", "org_id": str(ORG)},
    "asker": {"sub": "kc-asker", "org_id": str(ORG)},
}
USERS = {
    "kc-admin": (uuid.UUID(int=1), frozenset({"ask", "admin", "pack_author"}), frozenset()),
    "kc-owner": (
        uuid.UUID(int=2),
        frozenset({"ask"}),
        frozenset({"admin_data", "steward", "approve"}),
    ),
    "kc-asker": (uuid.UUID(int=3), frozenset({"ask"}), frozenset()),
}
SPACE = {"Authorization": "Bearer owner", "X-Data-Space": "DS-OIL"}
ADMIN = {"Authorization": "Bearer admin"}
ID = "01920000-0000-7000-8000-00000000beef"

KEY = Ed25519PrivateKey.generate()
PUBLIC = base64.b64encode(KEY.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
KEYS = {"vendor": PUBLIC}


@pytest.fixture
def api() -> TestClient:
    app = create_app(
        Settings(), token_verifier=FakeVerifier(TOKENS), identity_resolver=FakeIdentities(USERS)
    )
    return TestClient(app, raise_server_exceptions=False)


def _problem(response: Any, status: int, code: str) -> dict[str, Any]:
    assert response.status_code == status, response.text
    assert response.headers["content-type"].startswith("application/problem+json")
    body: dict[str, Any] = response.json()
    assert body["code"] == code
    return body


def _manifest(**changes: Any) -> dict[str, Any]:
    m: dict[str, Any] = {
        "schema_version": 1,
        "pack": {
            "code": "banking",
            "name": "Ngân hàng",
            "industry_code": "banking",
            "publisher": "Vendor",
            "publisher_key_id": "vendor",
        },
        "version": "2026.3",
        "min_core_version": "3.0",
        "metrics": [{"code": "NPL", "name": "NPL", "expression": "a/b", "legal_basis": "TT"}],
        "resources": {"README": hashlib.sha256(b"readme").hexdigest()},
    }
    m.update(changes)
    return m


def _file(m: dict[str, Any], *, content: bytes = b"readme", key: Any = KEY) -> bytes:
    signature = base64.b64encode(key.sign(rules.canonical(m))).decode()
    return json.dumps(
        {
            "manifest": m,
            "signature": signature,
            "resources": {"README": base64.b64encode(content).decode()},
        }
    ).encode()


# --- the pack file (UC-09-01, BR-128) -----------------------------------------------------------


@pytest.mark.req("AC-09-01-01")
def test_a_signed_pack_is_read() -> None:
    parsed = rules.read_pack_file(_file(_manifest()), KEYS, "3.1.0")
    assert parsed.manifest.pack.code == "banking"
    assert [m.code for m in parsed.manifest.metrics] == ["NPL"]


@pytest.mark.req("AC-09-01-03")
def test_a_resource_changed_after_signing_is_refused() -> None:
    with pytest.raises(rules.PackFileError, match="changed after signing") as caught:
        rules.read_pack_file(_file(_manifest(), content=b"tampered"), KEYS, "3.1.0")
    assert caught.value.code == "business_rule_violated"


@pytest.mark.req("AC-09-01-05")
def test_an_untrusted_key_or_a_forged_signature_is_refused() -> None:
    with pytest.raises(rules.PackFileError, match="does not trust"):
        rules.read_pack_file(_file(_manifest()), {}, "3.1.0")
    with pytest.raises(rules.PackFileError, match="not valid"):
        rules.read_pack_file(_file(_manifest(), key=Ed25519PrivateKey.generate()), KEYS, "3.1.0")
    edited = json.loads(_file(_manifest()))
    edited["manifest"]["version"] = "2099.1"
    with pytest.raises(rules.PackFileError, match="not valid"):
        rules.read_pack_file(json.dumps(edited).encode(), KEYS, "3.1.0")


@pytest.mark.req("AC-09-01-04")
def test_a_pack_for_a_newer_core_names_the_core_it_needs() -> None:
    with pytest.raises(rules.PackFileError, match=r"needs core version 3\.2"):
        rules.read_pack_file(_file(_manifest(min_core_version="3.2")), KEYS, "3.1.9")


@pytest.mark.parametrize("data", [b"not json", b"[]", b'{"manifest": {"version": "1"}}'])
def test_an_unreadable_file_is_a_validation_error(data: bytes) -> None:
    with pytest.raises(rules.PackFileError) as caught:
        rules.read_pack_file(data, KEYS, "3.1.0")
    assert caught.value.code == "validation_failed"


def test_versions_compare_by_number() -> None:
    assert rules.version_key("2026.3") < rules.version_key("2026.10") < rules.version_key("2027.1")
    assert rules.version_key("3.0") <= rules.version_key("3.0.0")


# --- authoring checks (UC-09-05) ----------------------------------------------------------------


@pytest.mark.req("AC-09-05-06")
def test_a_released_code_given_another_definition_is_reuse() -> None:
    released = {"NPL": ("a/b", "%"), "GROWTH": ("g", None)}
    mine = {"NPL": ("c/d", "%"), "GROWTH": ("g", None), "NEW": ("n", None)}
    assert rules.reused_codes(mine, released) == ["NPL"]


@pytest.mark.req("AC-09-05-04")
def test_breaking_changes_need_a_migration_guide() -> None:
    assert rules.breaking_without_guide([{"code": "X", "migration": "Đổi mã"}]) == 0
    assert rules.breaking_without_guide([{"code": "X"}, "rename", {"migration": " "}]) == 3


def test_metrics_need_legal_basis_or_the_internal_label() -> None:
    metrics = [("A", "TT 11", False), ("B", None, True), ("C", " ", False)]
    assert rules.missing_legal_basis(metrics) == ["C"]


# --- three-way merge (UC-09-04) -----------------------------------------------------------------


@pytest.mark.req("AC-09-04-02")
def test_three_way_merge_of_metrics() -> None:
    base = {"A": "a1", "B": "b1", "C": "c1", "D": "d1"}
    new = {"A": "a2", "B": "b1", "C": "c2", "E": "e1"}
    overrides = {"B": "b-own", "C": "c-own", "D": "d-own"}
    items = {i["code"]: i for i in rules.merge_metrics(base, new, overrides)}
    assert items["A"]["outcome"] == "take_new"
    assert items["B"]["outcome"] == "keep_override"
    assert items["C"]["outcome"] == "conflict"
    assert items["C"]["options"] == ["keep_override", "take_new", "defer"]
    # The pack drops a metric the organisation overrode: a person decides.
    assert items["D"]["outcome"] == "conflict"
    assert items["E"]["outcome"] == "added"
    assert rules.open_conflicts(list(items.values())) == 2


def test_dropped_mapped_attributes_are_conflicts() -> None:
    items = rules.merge_mappings({"loan.a", "loan.b"}, {"loan.a"}, {"loan.b"})
    assert [(i["code"], i["outcome"]) for i in items] == [("loan.b", "conflict")]
    assert rules.merge_mappings({"loan.a", "loan.b"}, {"loan.a"}, set()) == []


# --- routes: capabilities, Data Space, request shapes -------------------------------------------


def test_import_needs_admin(api: TestClient) -> None:
    response = api.post(
        "/v1/packs:import",
        headers={"Authorization": "Bearer asker"},
        files={"file": ("p.json", b"{}", "application/json")},
    )
    _problem(response, 403, "forbidden")


def test_import_needs_the_file(api: TestClient) -> None:
    _problem(api.post("/v1/packs:import", headers=ADMIN), 422, "validation_failed")


def test_import_refuses_a_file_that_is_not_a_pack(api: TestClient) -> None:
    response = api.post(
        "/v1/packs:import",
        headers=ADMIN,
        files={"file": ("p.json", b"not a pack", "application/json")},
    )
    _problem(response, 422, "validation_failed")


def test_authoring_needs_pack_author(api: TestClient) -> None:
    body = {"version": "1", "manifest": {}, "min_core_version": "3.0"}
    response = api.post(f"/v1/packs/{ID}/versions", headers=SPACE, json=body)
    _problem(response, 403, "forbidden")


@pytest.mark.parametrize(
    "body",
    [
        {"manifest": {}, "min_core_version": "3.0"},  # version is required
        {"version": "1", "min_core_version": "3.0"},  # manifest is required
        {"version": "1", "manifest": {}},  # min_core_version is required
        {"version": "1", "manifest": {"metrics": [{"code": "X"}]}, "min_core_version": "3"},
        {"version": "1", "manifest": {}, "min_core_version": "3", "status": "released"},
    ],
)
def test_create_version_validates_the_a3_body(api: TestClient, body: dict[str, Any]) -> None:
    response = api.post(f"/v1/packs/{ID}/versions", headers=ADMIN, json=body)
    _problem(response, 422, "validation_failed")


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/v1/pack-installs"),
        ("post", f"/v1/pack-installs/{ID}:suggest-mappings"),
        ("put", f"/v1/pack-installs/{ID}/mappings"),
        ("post", f"/v1/pack-installs/{ID}:calibrate"),
        ("post", f"/v1/pack-installs/{ID}:activate"),
        ("post", "/v1/metric-overrides"),
        ("post", f"/v1/metric-overrides/{ID}:approve"),
        ("post", f"/v1/pack-installs/{ID}:upgrade"),
        ("put", f"/v1/pack-upgrades/{ID}/decisions"),
        ("post", f"/v1/pack-upgrades/{ID}:apply"),
    ],
)
def test_install_resources_need_a_data_space(api: TestClient, method: str, path: str) -> None:
    headers = {"Authorization": "Bearer owner"}
    # The owner's capabilities come with the Data Space; the admin has none of them org-wide.
    response = getattr(api, method)(path, headers=headers, json={})
    assert response.status_code in (400, 403), response.text


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("post", "/v1/pack-installs", {}),
        ("post", "/v1/pack-installs", {"pack_version_id": "nope"}),
        ("put", f"/v1/pack-installs/{ID}/mappings", {}),
        (
            "put",
            f"/v1/pack-installs/{ID}/mappings",
            {"entity_mappings": [{"attribute_id": ID, "dataset_id": ID}]},
        ),
        (
            "put",
            f"/v1/pack-installs/{ID}/mappings",
            {
                "entity_mappings": [
                    {"attribute_id": ID, "dataset_id": ID, "column_expr": "c", "confidence": 2}
                ]
            },
        ),
        ("post", f"/v1/pack-installs/{ID}:calibrate", {"periods": "2026-09"}),
        ("post", "/v1/metric-overrides", {"pack_install_id": ID, "pack_metric_code": "NPL"}),
        (
            "post",
            "/v1/metric-overrides",
            {"pack_install_id": ID, "pack_metric_code": "NPL", "expression": "x", "reason": ""},
        ),
        ("post", f"/v1/pack-installs/{ID}:upgrade", {}),
        ("put", f"/v1/pack-upgrades/{ID}/decisions", {"decisions": []}),
        (
            "put",
            f"/v1/pack-upgrades/{ID}/decisions",
            {"decisions": [{"code": "NPL", "decision": "merge"}]},
        ),
    ],
)
def test_install_bodies_follow_a3(
    api: TestClient, method: str, path: str, body: dict[str, Any]
) -> None:
    response = getattr(api, method)(path, headers=SPACE, json=body)
    _problem(response, 422, "validation_failed")


def test_rollback_needs_admin_and_a_reason(api: TestClient) -> None:
    owner = api.post(f"/v1/pack-upgrades/{ID}:rollback", headers=SPACE, json={"reason": "x"})
    _problem(owner, 403, "forbidden")


def test_apply_needs_approve(api: TestClient) -> None:
    response = api.post(
        f"/v1/pack-upgrades/{ID}:apply",
        headers={"Authorization": "Bearer asker", "X-Data-Space": "DS-OIL"},
        json={},
    )
    _problem(response, 403, "forbidden")
