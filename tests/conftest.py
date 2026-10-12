"""Root pytest configuration.

Session-scoped fixtures for the test foundation. Heavy fixtures (Postgres,
Kafka, Redis) come up once per session via testcontainers; unit tests should
not depend on them and run without Docker.

Convention:
    pytest -m unit          → no Docker required, fast
    pytest -m integration   → spins up containers
    pytest                  → both
"""
from __future__ import annotations

import json
import os
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

# Several services call `load_dotenv(find_dotenv())` at import time. Under pytest
# that would pull the developer's ./.env (GATEWAY_KEY, secrets, DB URLs) into
# every test and make results depend on the machine. Neutralise it
# before any service module is imported; tests set what they need via
# monkeypatch.setenv.
try:
    import dotenv
    import dotenv.main

    def _no_dotenv(*_args, **_kwargs) -> bool:
        return False

    dotenv.load_dotenv = _no_dotenv  # type: ignore[assignment]
    dotenv.main.load_dotenv = _no_dotenv  # type: ignore[assignment]
except ImportError:  # pragma: no cover - python-dotenv is optional for pure-unit runs
    pass


# ─── Marker enforcement ────────────────────────────────────────────────────
# Every test must declare `unit` or `integration`. Fails the suite otherwise.
# Prevents a slow integration test sneaking past dev-loop `pytest -m unit`.
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for item in items:
        markers = {m.name for m in item.iter_markers()}
        _check_req_ids(item)
        if not markers & {"unit", "integration", "e2e"}:
            item.add_marker(
                pytest.mark.skip(
                    reason=(
                        f"Test {item.nodeid} has no marker. "
                        "Add @pytest.mark.unit, integration, or e2e."
                    )
                )
            )


# ─── Requirement coverage (docs/v2/15-test-strategy.md §7) ─────────────────
# `@pytest.mark.req("UC07", "NOP-234")` names what a test proves. A typo'd ID
# would silently drop the test out of the coverage report, so a bad ID fails
# collection instead. `--req-report-dir DIR` writes one JSON per run (the
# suites run in separate images); tests/req_coverage.py merges them.
_LEVELS = ("unit", "integration", "e2e")
_KINDS = ("contract", "security", "resilience", "performance", "ai_eval")
# AC-01-01-04: an acceptance criterion of the design doc (tab 8), see docs/v3/checklist/README.md.
_REQ_ID = re.compile(
    r"UC\d{2}|NOP-\d+|SC27-\d{2}|SCVN-\d|AC-\d{2}-\d{2}-\d{2}|[A-Z][A-Z0-9]*-\d{2}[a-z]?"
)
_REQ_ROWS = pytest.StashKey[dict[str, dict]]()


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--req-report-dir",
        metavar="DIR",
        default=None,
        help="write requirement coverage JSON for this run into DIR",
    )


def _check_req_ids(item: pytest.Item) -> list[str]:
    ids = [i for m in item.iter_markers("req") for i in m.args]
    bad = [i for i in ids if not (isinstance(i, str) and _REQ_ID.fullmatch(i))]
    if bad:
        raise pytest.UsageError(f"{item.nodeid}: invalid req id {bad!r}")
    return ids


def pytest_collection_finish(session: pytest.Session) -> None:
    if not session.config.getoption("--req-report-dir"):
        return
    rows = {}
    for item in session.items:
        names = {m.name for m in item.iter_markers()}
        rows[item.nodeid] = {
            "nodeid": item.nodeid,
            "level": next((n for n in _LEVELS if n in names), None),
            "kinds": [k for k in _KINDS if k in names],
            "reqs": sorted(set(_check_req_ids(item))),
            "outcome": "not_run",
        }
    session.config.stash[_REQ_ROWS] = rows


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo) -> Iterator[pytest.TestReport]:
    report = yield
    rows = item.config.stash.get(_REQ_ROWS, None)
    # Setup/teardown only overwrite the outcome when they fail or skip; the
    # call phase decides otherwise.
    if rows is not None and item.nodeid in rows and (report.when == "call" or report.outcome != "passed"):
        rows[item.nodeid]["outcome"] = report.outcome
    return report


def pytest_sessionfinish(session: pytest.Session) -> None:
    out_dir = session.config.getoption("--req-report-dir")
    rows = session.config.stash.get(_REQ_ROWS, None)
    if not out_dir or rows is None:
        return
    # One file per suite: name it after the paths pytest was given.
    label = re.sub(r"[^A-Za-z0-9]+", "_", "-".join(session.config.args)).strip("_") or "all"
    path = Path(out_dir) / f"{label}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"source": "pytest", "tests": list(rows.values())}, indent=1))


# ─── Container fixtures (lazy — only imported when integration tests run) ──
def _testcontainers_available() -> bool:
    try:
        import testcontainers  # noqa: F401
        return True
    except ImportError:
        return False


@pytest.fixture(scope="session")
def postgres_container() -> Iterator[dict[str, str]]:
    """Yield connection info for an ephemeral Postgres 16 container.

    Returns a dict with: host, port, user, password, database, dsn.
    Skips the test if testcontainers is not installed.
    """
    if not _testcontainers_available():
        pytest.skip("testcontainers not installed — install test extras")

    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine") as pg:
        host = pg.get_container_host_ip()
        port = pg.get_exposed_port(5432)
        # `pg.get_connection_url()` returns the SQLAlchemy form
        # (`postgresql+psycopg2://...`) which psycopg2.connect() rejects with
        # "invalid dsn: missing '='". Build the libpq form ourselves so the
        # same DSN works for both psycopg2 (auth_api) and SQLAlchemy callers.
        info = {
            "host": host,
            "port": str(port),
            "user": pg.username,
            "password": pg.password,
            "database": pg.dbname,
            "dsn": f"postgresql://{pg.username}:{pg.password}@{host}:{port}/{pg.dbname}",
            "sqlalchemy_url": f"postgresql+psycopg2://{pg.username}:{pg.password}@{host}:{port}/{pg.dbname}",
        }
        yield info


@pytest.fixture(scope="session")
def redis_container() -> Iterator[dict[str, str]]:
    """Yield connection info for an ephemeral Redis 7 container."""
    if not _testcontainers_available():
        pytest.skip("testcontainers not installed")

    from testcontainers.redis import RedisContainer

    with RedisContainer("redis:7-alpine") as redis:
        info = {
            "host": redis.get_container_host_ip(),
            "port": str(redis.get_exposed_port(6379)),
            "url": f"redis://{redis.get_container_host_ip()}:{redis.get_exposed_port(6379)}",
        }
        yield info


@pytest.fixture(scope="session")
def qdrant_container() -> Iterator[dict[str, str]]:
    """Yield connection info for an ephemeral Qdrant container."""
    if not _testcontainers_available():
        pytest.skip("testcontainers not installed")

    from testcontainers.qdrant import QdrantContainer

    with QdrantContainer() as qc:
        yield {"url": f"http://{qc.rest_host_address}"}


@pytest.fixture(scope="session")
def kafka_container() -> Iterator[dict[str, str]]:
    """Yield bootstrap_servers for an ephemeral Kafka container.

    Uses Confluent's Kafka image. Tests that publish/consume should use
    aiokafka or kafka-python clients pointed at `bootstrap_servers`.
    """
    if not _testcontainers_available():
        pytest.skip("testcontainers not installed")

    from testcontainers.kafka import KafkaContainer

    with KafkaContainer("confluentinc/cp-kafka:7.5.0") as kafka:
        yield {"bootstrap_servers": kafka.get_bootstrap_server()}


# ─── Env override fixture ──────────────────────────────────────────────────
@pytest.fixture
def patched_env(monkeypatch: pytest.MonkeyPatch) -> "_EnvPatcher":
    """Helper for tests that need to override env vars without leaking state.

    Usage:
        def test_x(patched_env):
            patched_env({"DATABASE_URL": "postgresql://..."})
            ...
    """
    class _EnvPatcher:
        def __call__(self, overrides: dict[str, str]) -> None:
            for k, v in overrides.items():
                monkeypatch.setenv(k, v)
    return _EnvPatcher()


# ─── Live-stack credentials ────────────────────────────────────────────────
# The e2e tests log in to the dev cluster's Keycloak. Passwords come only from
# the environment (CLAUDE.md §6): the literals these tests used to carry were
# out of date (one user even had two different ones) and every login 401'd.
# Usernames are not secret, so each account keeps its demo default.
LIVE_ACCOUNTS = {
    # account: (username env, default username, password env)
    "engineer": ("E2E_USER", "helios-engineer", "E2E_PASS"),
    "admin": ("E2E_ADMIN_USER", "platform-admin", "E2E_ADMIN_PASS"),
    "tenant2_admin": ("E2E_T2_ADMIN_USER", "helios-admin", "E2E_T2_ADMIN_PASS"),
}


def live_credentials(account: str) -> tuple[str, str]:
    """(username, password) of a demo account; fails naming the unset variable."""
    # pyproject's --showlocals would print the password in a failure's traceback.
    __tracebackhide__ = True
    user_env, default_user, pass_env = LIVE_ACCOUNTS[account]
    password = os.environ.get(pass_env)
    if not password:
        pytest.fail(f"{pass_env} is not set — export the {account} password (see tests/README.md, e2e)")
    return os.environ.get(user_env, default_user), password


def live_token(keycloak_url: str, account: str) -> str:
    """A Keycloak access token for a demo account (password grant)."""
    __tracebackhide__ = True  # its locals hold the password; see live_credentials
    import httpx

    username, password = live_credentials(account)
    r = httpx.post(
        f"{keycloak_url}/realms/aeroflow/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": "aeroflow-frontend",
            "username": username,
            "password": password,
        },
        timeout=20,
    )
    body = r.json()
    if "access_token" not in body:
        pytest.fail(f"Login failed for {username}: {body}")
    return str(body["access_token"])


# ─── Live-stack opt-in for promoted scripts ────────────────────────────────
# Some tests (e.g. tenant isolation) drive a fully-running stack rather than
# spinning containers. Gate them behind LIVE_STACK_URL so CI doesn't run them
# unless the stack is actually up.
@pytest.fixture(scope="session")
def live_stack() -> dict[str, str]:
    """Return Keycloak + Kong URLs for promoted live-stack tests.

    Skip if LIVE_STACK env is not set; set it when a cluster is reachable.
    """
    if os.environ.get("LIVE_STACK") != "1":
        pytest.skip(
            "Live stack not requested — set LIVE_STACK=1 and bring up "
            "deploy/k8s manifests applied to a cluster before running."
        )
    return {
        # The dev cluster's LAN NodePorts; `make e2e` passes the same defaults.
        "keycloak": os.environ.get("LIVE_KEYCLOAK_URL", "http://10.10.55.101:30110"),
        "kong": os.environ.get("LIVE_KONG_URL", "http://10.10.55.101:30100"),
    }
