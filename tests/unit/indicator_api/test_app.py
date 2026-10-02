"""indicator-api boots on its own: probes, auth on every domain route, the skeleton's 501."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from services.indicator_api.adapters import ADAPTER_NAMES, configured_adapters
from services.indicator_api.app import create_app

pytestmark = [pytest.mark.unit, pytest.mark.req("NOP-162")]

GATEWAY_KEY = "test-gateway-key"
TENANT = "a0000000-0000-0000-0000-000000000001"
CASE_ID = "2e3f4a5b-6c7d-4e8f-9a0b-1c2d3e4f5a06"

# Every domain route the skeleton publishes, with a valid request for each.
DOMAIN_REQUESTS: list[tuple[str, str, dict[str, object] | None]] = [
    ("GET", "/entities", None),
    ("GET", "/entities/BRENT", None),
    ("GET", "/series", None),
    ("GET", "/series/EIA:RBRTE:D", None),
    ("GET", "/series/EIA:RBRTE:D/observations", None),
    ("GET", "/query?series_key=EIA:RBRTE:D", None),
    ("GET", "/review/cases", None),
    ("GET", f"/review/cases/{CASE_ID}", None),
    ("POST", f"/review/cases/{CASE_ID}/approve", {"expected_version": 1}),
    ("POST", f"/review/cases/{CASE_ID}/reject", {"expected_version": 1}),
    ("GET", "/capabilities", None),
]


class _Adapter:
    def __init__(self, name: str, error: Exception | None = None) -> None:
        self.name = name
        self._error = error

    def ping(self) -> None:
        if self._error is not None:
            raise self._error


@pytest.fixture
def gateway_headers(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    monkeypatch.setenv("GATEWAY_KEY", GATEWAY_KEY)
    return {
        "X-Kong-Verified": "true",
        "X-Gateway-Key": GATEWAY_KEY,
        "X-User-Id": "analyst",
        "X-Tenant-Id": TENANT,
    }


def test_liveness_answers_without_any_store() -> None:
    response = TestClient(create_app(adapters=[])).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_with_no_store_configured_reports_each_as_not_configured() -> None:
    response = TestClient(create_app(adapters=[])).get("/ready")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "checks": {name: "not_configured" for name in ADAPTER_NAMES},
    }


def test_ready_is_ok_when_every_configured_store_answers() -> None:
    app = create_app(adapters=[_Adapter("postgres"), _Adapter("kafka")])
    response = TestClient(app).get("/ready")
    assert response.status_code == 200
    assert response.json()["checks"] == {
        "postgres": "ok",
        "clickhouse": "not_configured",
        "kafka": "ok",
        "minio": "not_configured",
    }


@pytest.mark.resilience
def test_ready_is_503_when_a_configured_store_does_not_answer() -> None:
    app = create_app(
        adapters=[_Adapter("postgres"), _Adapter("clickhouse", ConnectionError("refused"))]
    )
    response = TestClient(app).get("/ready")
    assert response.status_code == 503
    assert response.json()["status"] == "unavailable"
    assert response.json()["checks"]["clickhouse"] == "error"
    assert response.json()["checks"]["postgres"] == "ok"


def test_a_store_is_configured_only_by_its_own_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # The platform-wide KAFKA_* / MINIO_* names every pod gets must not switch
    # an adapter on: indicator-api has no principal of its own on those yet.
    for env in (
        "APP_DATABASE_URL",
        "INDICATOR_API_CLICKHOUSE_URL",
        "INDICATOR_API_KAFKA_BOOTSTRAP_SERVERS",
        "INDICATOR_API_MINIO_ENDPOINT",
    ):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:8350")
    monkeypatch.setenv("MINIO_ENDPOINT", "http://minio:8360")
    assert configured_adapters() == []

    monkeypatch.setenv("APP_DATABASE_URL", "postgresql://app_user:pw@postgres:5432/dataagent")
    monkeypatch.setenv("INDICATOR_API_KAFKA_BOOTSTRAP_SERVERS", "kafka:8350")
    assert [adapter.name for adapter in configured_adapters()] == ["postgres", "kafka"]


@pytest.mark.security
@pytest.mark.parametrize(("method", "path", "body"), DOMAIN_REQUESTS)
def test_a_domain_route_refuses_a_caller_kong_did_not_verify(
    monkeypatch: pytest.MonkeyPatch, method: str, path: str, body: dict[str, object] | None
) -> None:
    monkeypatch.setenv("GATEWAY_KEY", GATEWAY_KEY)
    response = TestClient(create_app(adapters=[])).request(method, path, json=body)
    assert response.status_code == 401


@pytest.mark.security
def test_a_verified_caller_without_a_tenant_is_refused(gateway_headers: dict[str, str]) -> None:
    headers = {k: v for k, v in gateway_headers.items() if k != "X-Tenant-Id"}
    response = TestClient(create_app(adapters=[])).get("/entities", headers=headers)
    assert response.status_code == 403


@pytest.mark.parametrize(("method", "path", "body"), DOMAIN_REQUESTS)
def test_a_domain_route_is_published_but_not_implemented(
    gateway_headers: dict[str, str], method: str, path: str, body: dict[str, object] | None
) -> None:
    response = TestClient(create_app(adapters=[])).request(
        method, path, json=body, headers=gateway_headers
    )
    assert response.status_code == 501
