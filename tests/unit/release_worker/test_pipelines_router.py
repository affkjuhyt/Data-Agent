"""release_worker routers/pipelines.py: route table, gateway auth, role guards,
Kafka event shape and SQL-to-response mapping.

The service imports as ``services.release_worker.*`` (its Dockerfile copies it
to /app/services/release_worker), and release_core reads its required config
from env at import time, so the module fixture sets that env before importing
and drops the imported modules again afterwards.

Postgres is a fake connection whose cursor answers by SQL fragment; Kafka's
``produce_event`` and the approval deploy thread's target are recorders. The
real ``libs.shared.auth.get_current_user`` stays in place and is driven with
Kong headers.
"""
from __future__ import annotations

import importlib
import sys
import threading
import types
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

import pytest

pytestmark = pytest.mark.unit

_REQUIRED_ENV = {
    "DATABASE_URL": "postgresql://u:p@localhost:5432/test",
    "KAFKA_BOOTSTRAP": "kafka:9092",
    "KAFKA_USERNAME": "release_worker",
    "KAFKA_PASSWORD": "secret",
    "MINIO_ACCESS_KEY": "minio",
    "MINIO_SECRET_KEY": "minio_secret",
}
GATEWAY_KEY = "gw-test-key"
_PKG = "services.release_worker"


@pytest.fixture(scope="module")
def rw() -> Iterator[types.SimpleNamespace]:
    saved = {n: m for n, m in sys.modules.items() if n == _PKG or n.startswith(_PKG + ".")}
    mp = pytest.MonkeyPatch()
    for key, value in _REQUIRED_ENV.items():
        mp.setenv(key, value)
    mp.setenv("OTEL_SDK_DISABLED", "true")
    try:
        yield types.SimpleNamespace(
            pipelines=importlib.import_module(f"{_PKG}.routers.pipelines"),
            app_module=importlib.import_module(f"{_PKG}.app"),
        )
    finally:
        mp.undo()
        for name in [n for n in sys.modules if n == _PKG or n.startswith(_PKG + ".")]:
            del sys.modules[name]
        sys.modules.update(saved)


class FakeCursor:
    def __init__(self, db: FakeDB) -> None:
        self._db = db
        self._rows: list[Any] = []

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        self._db.executed.append((" ".join(sql.split()), params))
        self._rows = []
        for fragment, rows in self._db.answers.items():
            if fragment in sql:
                self._rows = list(rows)
                break

    def fetchone(self) -> Any:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[Any]:
        return list(self._rows)


class FakeDB:
    def __init__(self) -> None:
        # SQL fragment -> rows the cursor returns for a statement containing it.
        self.answers: dict[str, list[Any]] = {}
        self.executed: list[tuple[str, tuple[Any, ...]]] = []

    @contextmanager
    def connect(self) -> Iterator[Any]:
        db = self

        class _Conn:
            def cursor(self, cursor_factory: Any = None) -> FakeCursor:
                return FakeCursor(db)

        yield _Conn()


class Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.deploys: list[tuple[str, str, str]] = []
        self.status_updates: list[tuple[str, str, str | None]] = []
        self.deploy_started = threading.Event()

    def produce_event(self, topic: str, event: dict[str, Any]) -> None:
        self.events.append((topic, event))

    def run_approval_deploy(self, pipeline_id: str, environment: str, triggered_by: str) -> None:
        self.deploys.append((pipeline_id, environment, triggered_by))
        self.deploy_started.set()

    def update_pipeline_status(self, pipeline_id: str, status: str, error: str | None = None) -> None:
        self.status_updates.append((pipeline_id, status, error))


@pytest.fixture
def db(rw: types.SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> FakeDB:
    fake = FakeDB()
    monkeypatch.setattr(rw.pipelines, "get_pg_conn", fake.connect)
    return fake


@pytest.fixture
def rec(rw: types.SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> Recorder:
    fake = Recorder()
    monkeypatch.setattr(rw.pipelines, "produce_event", fake.produce_event)
    monkeypatch.setattr(rw.pipelines, "_run_approval_deploy", fake.run_approval_deploy)
    monkeypatch.setattr(rw.pipelines, "update_pipeline_status", fake.update_pipeline_status)
    return fake


@pytest.fixture
def client(rw: types.SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> Any:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    monkeypatch.setenv("GATEWAY_KEY", GATEWAY_KEY)
    monkeypatch.delenv("KB_GATEWAY_KEY", raising=False)
    # The real app adds an internal-network origin filter that TestClient's
    # "testclient" peer can never pass (pinned separately below), so the router
    # is mounted on a bare app here.
    app = FastAPI()
    app.include_router(rw.pipelines.router)
    return TestClient(app)


def kong(roles: str = "platform-admin", *, key: str = GATEWAY_KEY) -> dict[str, str]:
    return {
        "X-Kong-Verified": "true",
        "X-Gateway-Key": key,
        "X-User-Id": "alice",
        "X-User-Roles": roles,
        "X-Tenant-Id": "11111111-1111-1111-1111-111111111111",
    }


def _routes(app_or_router: Any) -> set[tuple[str, str]]:
    return {(m, r.path) for r in app_or_router.routes for m in (getattr(r, "methods", None) or ())
            if m != "HEAD"}


_EXPECTED_ROUTES = {
    ("POST", "/api/release/pipeline/trigger"),
    ("POST", "/api/release/pipeline/{pipeline_id}/approve/{environment}"),
    ("POST", "/api/release/rollback"),
    ("GET", "/api/release/pipelines"),
    ("GET", "/api/release/rollback-targets"),
    ("GET", "/api/release/history"),
    ("GET", "/api/release/pipelines/{pipeline_id}"),
}


class TestWiring:
    def test_routes_registered(self, rw: types.SimpleNamespace) -> None:
        assert _routes(rw.pipelines.router) == _EXPECTED_ROUTES
        assert _routes(rw.app_module.create_app()) >= _EXPECTED_ROUTES

    def test_real_app_refuses_non_internal_peer(self, rw: types.SimpleNamespace) -> None:
        from fastapi.testclient import TestClient

        client = TestClient(rw.app_module.create_app())
        # /health stays open for probes; everything else needs a private-range peer.
        assert client.get("/health").status_code == 200
        assert client.get("/api/release/pipelines", headers=kong()).status_code == 403


class TestAuth:
    @pytest.mark.parametrize(("method", "path"), sorted(
        (m, p.replace("{pipeline_id}", "pipe-1").replace("{environment}", "staging"))
        for m, p in _EXPECTED_ROUTES
    ))
    def test_every_route_401_without_gateway(
        self, client: Any, db: FakeDB, rec: Recorder, method: str, path: str
    ) -> None:
        resp = client.request(method, path, json={}, headers={"X-User-Roles": "platform-admin"})
        assert resp.status_code == 401
        assert db.executed == [] and rec.events == []

    def test_wrong_gateway_key_is_401(self, client: Any, db: FakeDB) -> None:
        resp = client.get("/api/release/pipelines", headers=kong(key="nope"))
        assert resp.status_code == 401

    def test_approve_needs_admin_or_engineer(self, client: Any, db: FakeDB, rec: Recorder) -> None:
        resp = client.post("/api/release/pipeline/pipe-1/approve/production",
                           json={"decision": "APPROVED"}, headers=kong("viewer"))
        assert resp.status_code == 403
        assert db.executed == [] and rec.deploys == []

    def test_rollback_needs_platform_admin(self, client: Any, rec: Recorder) -> None:
        resp = client.post("/api/release/rollback",
                           json={"from_version": "2", "to_version": "1", "environment": "prod"},
                           headers=kong("ai-engineer"))
        assert resp.status_code == 403
        assert rec.events == []


class TestHappyPath:
    def test_trigger_produces_event_from_caller(self, client: Any, rec: Recorder) -> None:
        resp = client.post("/api/release/pipeline/trigger",
                           json={"package_version": "1.4.0", "branch": "main"},
                           headers=kong("ai-engineer"))
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "triggered"
        assert body["pipeline_id"].startswith("pipe-")

        [(topic, event)] = rec.events
        assert topic == "release.pipeline.triggered"
        assert event["pipeline_id"] == body["pipeline_id"]
        assert event["triggered_by"] == "alice"
        assert event["package_version"] == "1.4.0"
        assert event["target_environments"] == ["dev"]

    def test_approve_records_and_starts_deploy(self, client: Any, db: FakeDB, rec: Recorder) -> None:
        db.answers['FROM "ReleasePackages"'] = [(42,)]
        resp = client.post("/api/release/pipeline/pipe-1/approve/staging",
                           json={"decision": "APPROVED", "comment": "ok"}, headers=kong("ai-engineer"))
        assert resp.status_code == 200
        assert resp.json() == {"status": "approved", "pipeline_id": "pipe-1", "environment": "staging"}

        insert = [p for sql, p in db.executed if sql.startswith('INSERT INTO "ReleaseApprovals"')]
        assert insert == [(42, "staging", "APPROVED", "alice", "ok", "APPROVED")]
        assert rec.deploy_started.wait(timeout=5)
        assert rec.deploys == [("pipe-1", "staging", "alice")]

    def test_reject_fails_pipeline_without_deploy(self, client: Any, db: FakeDB, rec: Recorder) -> None:
        db.answers['FROM "ReleasePackages"'] = [(42,)]
        resp = client.post("/api/release/pipeline/pipe-1/approve/production",
                           json={"decision": "REJECTED"}, headers=kong())
        assert resp.status_code == 200
        assert resp.json() == {"status": "rejected", "pipeline_id": "pipe-1"}
        assert rec.status_updates == [("pipe-1", "FAILED", "Rejected by alice")]
        assert rec.deploys == []

    def test_rollback_produces_event(self, client: Any, rec: Recorder) -> None:
        resp = client.post("/api/release/rollback",
                           json={"from_version": "2", "to_version": "1", "environment": "prod",
                                 "reason": "bad build"}, headers=kong())
        assert resp.status_code == 200
        [(topic, event)] = rec.events
        assert topic == "release.rollback.initiated"
        assert event["rollback_id"] == resp.json()["rollback_id"]
        assert (event["from_version"], event["to_version"], event["triggered_by"]) == ("2", "1", "alice")

    def test_list_pipelines_filters_and_serializes(self, client: Any, db: FakeDB) -> None:
        created = datetime(2026, 9, 20, 10, 0, tzinfo=UTC)
        db.answers['FROM "Pipelines"'] = [
            {"id": "pipe-1", "status": "FAILED", "created_at": created,
             "updated_at": None, "completed_at": None},
        ]
        resp = client.get("/api/release/pipelines", params={"status": "FAILED", "limit": 5},
                          headers=kong("viewer"))
        assert resp.status_code == 200
        assert resp.json() == {"pipelines": [{"id": "pipe-1", "status": "FAILED",
                                              "created_at": "2026-09-20T10:00:00+00:00",
                                              "updated_at": None, "completed_at": None}],
                               "count": 1}
        [(sql, params)] = db.executed
        assert "WHERE status = %s" in sql
        assert params == ("FAILED", 5)

    def test_get_pipeline_with_steps(self, client: Any, db: FakeDB) -> None:
        db.answers['FROM "PipelineSteps"'] = [{"step_name": "build", "status": "SUCCESS"}]
        db.answers['FROM "Pipelines"'] = [{"id": "pipe-1", "status": "SUCCESS",
                                           "created_at": None, "updated_at": None}]
        resp = client.get("/api/release/pipelines/pipe-1", headers=kong("viewer"))
        assert resp.status_code == 200
        assert resp.json()["steps"] == [{"step_name": "build", "status": "SUCCESS"}]
        assert db.executed[0][1] == ("pipe-1",)


class TestErrors:
    def test_trigger_without_package_version_is_422(self, client: Any, rec: Recorder) -> None:
        resp = client.post("/api/release/pipeline/trigger", json={"branch": "main"}, headers=kong())
        assert resp.status_code == 422
        assert rec.events == []

    def test_approve_unknown_package_is_404(self, client: Any, db: FakeDB, rec: Recorder) -> None:
        resp = client.post("/api/release/pipeline/pipe-x/approve/staging",
                           json={"decision": "APPROVED"}, headers=kong())
        assert resp.status_code == 404
        assert rec.deploys == []
        assert not any(sql.startswith("INSERT") for sql, _ in db.executed)

    def test_unknown_pipeline_is_404(self, client: Any, db: FakeDB) -> None:
        resp = client.get("/api/release/pipelines/pipe-x", headers=kong())
        assert resp.status_code == 404

    def test_non_integer_limit_is_422(self, client: Any, db: FakeDB) -> None:
        resp = client.get("/api/release/history", params={"limit": "all"}, headers=kong())
        assert resp.status_code == 422
        assert db.executed == []
