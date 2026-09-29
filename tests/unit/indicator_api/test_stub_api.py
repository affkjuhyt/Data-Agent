"""The P0-09 stub routes, one test per acceptance criterion they carry."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from libs.shared.event_envelope import EventType, build_envelope, parse_envelope
from services.indicator_api.main import create_app
from services.indicator_api.stub.store import MAPPING_PROPOSAL_ID, REVIEW_CASE_ID

pytestmark = pytest.mark.unit

API = "/api/indicator"
KEY = "test-gateway-key"
TENANT = "a0000000-0000-0000-0000-000000000001"
AUTH = {"X-Kong-Verified": "true", "X-Gateway-Key": KEY, "X-Tenant-Id": TENANT, "X-User-Id": "u1"}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("GATEWAY_KEY", KEY)
    with TestClient(create_app(), headers=AUTH) as c:
        yield c


def error_code(response: Any) -> str:
    code: str = response.json()["error"]["code"]
    return code


# ── AC-1: the week-2 branches can read the catalog and numbers ────────────────


def test_catalog_and_series_are_served(client: TestClient):
    assert "BRENT_SPOT" in {e["entity_id"] for e in client.get(f"{API}/entities").json()}
    assert client.get(f"{API}/series/EIA:RBRTE:D").json()["status"] == "approved"
    relations = client.get(f"{API}/entities/BRENT_SPOT/relations").json()
    assert relations[0]["predicate"] == "NOT_EQUIVALENT"


def test_observations_carry_every_required_field(client: TestClient):
    rows = client.get(f"{API}/series/EIA:RBRTE:D/observations").json()
    brent = rows[0]
    assert brent["value"] == "130.80"
    for field in ("event_time_start", "event_time_end", "market_timezone", "publication_time",
                  "available_at", "system_time", "release_id", "artifact_sha256", "version_kind",
                  "effective_version", "source_ref"):  # fmt: skip
        assert brent[field] is not None, field


def test_as_of_hides_what_was_not_yet_published(client: TestClient):
    # Brent 22/09 is released 23/09: as of 22/09 only 15/09 is known.
    rows = client.get(
        f"{API}/series/EIA:RBRTE:D/observations", params={"as_of": "2026-09-22T12:00:00+07:00"}
    ).json()
    assert [r["value"] for r in rows] == ["130.80"]


def test_as_of_without_a_timezone_is_refused(client: TestClient):
    r = client.get(f"{API}/series/EIA:RBRTE:D/observations", params={"as_of": "2026-09-22T12:00"})
    assert r.status_code == 422


def test_unknown_entity_is_unresolved(client: TestClient):
    r = client.get(f"{API}/entities/NOPE")
    assert (r.status_code, error_code(r)) == (404, "UNRESOLVED_ENTITY")


# ── AC-3: a number missing available_at is refused, naming the field ─────────


def test_observation_without_available_at_is_refused(client: TestClient):
    obs = client.get(f"{API}/series/EIA:RBRTE:D/observations").json()[0]
    del obs["available_at"]
    r = client.post(f"{API}/observations", json=obs)
    assert r.status_code == 422
    assert ["body", "available_at"] in [err["loc"] for err in r.json()["detail"]]


def test_complete_observation_is_accepted(client: TestClient):
    obs = client.get(f"{API}/series/EIA:RBRTE:D/observations").json()[0]
    obs["effective_version"] = 2
    obs["version_kind"] = "correction"
    assert client.post(f"{API}/observations", json=obs).status_code == 201


# ── AC-4: the second writer on version 3 gets VERSION_CONFLICT ────────────────


def test_second_write_on_the_same_version_conflicts_and_the_first_is_kept(client: TestClient):
    url = f"{API}/governance/mapping-proposals/{MAPPING_PROPOSAL_ID}"
    assert client.get(url).json()["version"] == 3

    first = client.patch(url, json={"expected_version": 3, "status": "approved"})
    assert (first.status_code, first.json()["version"]) == (200, 4)

    second = client.patch(url, json={"expected_version": 3, "status": "rejected"})
    assert second.status_code == 409
    assert second.json() == {
        "error": {
            "code": "VERSION_CONFLICT",
            "message": "expected version 3, current is 4",
            "current_version": 4,
        }
    }
    assert client.get(url).json()["status"] == "approved"


def test_review_decision_is_version_checked(client: TestClient):
    url = f"{API}/review/cases/{REVIEW_CASE_ID}/decision"
    assert client.post(url, json={"expected_version": 1, "decision": "approved"}).status_code == 200
    stale = client.post(url, json={"expected_version": 1, "decision": "rejected"})
    assert error_code(stale) == "VERSION_CONFLICT"


# ── AC-6: a question about a pending series gets SERIES_NOT_APPROVED ──────────


def test_query_on_a_pending_series_is_not_approved(client: TestClient):
    r = client.post(
        f"{API}/query", json={"operation": "fetch_series", "series": ["BCT:E10RON95III:P"]}
    )
    assert (r.status_code, error_code(r)) == (400, "SERIES_NOT_APPROVED")


def test_query_on_an_approved_series_answers_with_provenance(client: TestClient):
    r = client.post(f"{API}/query", json={"operation": "fetch_series", "series": ["EIA:RBRTE:D"]})
    assert r.status_code == 200
    assert {o["release_id"] for o in r.json()["observations"]}


# ── Access ────────────────────────────────────────────────────────────────────


def test_request_not_verified_by_kong_is_refused(client: TestClient):
    r = client.get(f"{API}/entities", headers={"X-Gateway-Key": "wrong"})
    assert r.status_code == 401


def test_caller_without_a_tenant_is_access_denied(client: TestClient):
    r = client.get(f"{API}/entities", headers={"X-Tenant-Id": ""})
    assert (r.status_code, error_code(r)) == (403, "ACCESS_DENIED")


# ── Step 6 of the story: indicator-api reads what a worker wrote ──────────────


def test_indicator_api_reads_an_envelope_built_by_worker_code():
    env = build_envelope(
        EventType.OBSERVATION_PUBLISHED, tenant_id=TENANT, payload={"series_key": "EIA:RBRTE:D"}
    )
    assert parse_envelope(env.model_dump_json().encode()) == env
