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

import os
from collections.abc import Iterator

import pytest


# ─── Marker enforcement ────────────────────────────────────────────────────
# Every test must declare `unit` or `integration`. Fails the suite otherwise.
# Prevents a slow integration test sneaking past dev-loop `pytest -m unit`.
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for item in items:
        markers = {m.name for m in item.iter_markers()}
        if not markers & {"unit", "integration", "e2e"}:
            item.add_marker(
                pytest.mark.skip(
                    reason=(
                        f"Test {item.nodeid} has no marker. "
                        "Add @pytest.mark.unit, integration, or e2e."
                    )
                )
            )


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
        # same DSN works for both psycopg2 (auth-api) and SQLAlchemy callers.
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


# ─── Live-stack opt-in for promoted scripts ────────────────────────────────
# Some tests (e.g. tenant isolation) drive a fully-running stack rather than
# spinning containers. Gate them behind LIVE_STACK_URL so CI doesn't run them
# unless the stack is actually up.
@pytest.fixture(scope="session")
def live_stack() -> dict[str, str]:
    """Return Keycloak + Kong URLs for promoted live-stack tests.

    Skip if LIVE_STACK env is not set. CI sets it after `docker compose up`.
    """
    if os.environ.get("LIVE_STACK") != "1":
        pytest.skip(
            "Live stack not requested — set LIVE_STACK=1 and bring up "
            "docker/docker-compose.yml + .local.yml before running."
        )
    return {
        "keycloak": os.environ.get("LIVE_KEYCLOAK_URL", "http://localhost:8080"),
        "kong": os.environ.get("LIVE_KONG_URL", "http://localhost:8000"),
    }
