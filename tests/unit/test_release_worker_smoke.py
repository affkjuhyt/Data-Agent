"""Smoke tests for release_worker's release_core.

release_worker lives in a hyphenated dir, reads its config from env at import
time, and imports kafka / psycopg2 / shared.* at module level — so the module
is loaded through ``_loader`` with stand-ins for those, and every stand-in is
removed from ``sys.modules`` again afterwards so other tests see the real
packages.

Guards the G2 cleanup: no Celery layer, no Mongo constants, and the
``get_pg_conn`` context manager closes the connection on every exit path
(psycopg2's own ``with conn:`` commits/rolls back but never closes).
"""
from __future__ import annotations

import sys
import types
from collections.abc import Iterator

import pytest

from ._loader import SERVICES, load_module_from_path

pytestmark = pytest.mark.unit

_REQUIRED_ENV = {
    "DATABASE_URL": "postgresql://u:p@localhost:5432/test",
    "KAFKA_BOOTSTRAP": "kafka:9092",
    "KAFKA_USERNAME": "release_worker",
    "KAFKA_PASSWORD": "secret",
    "MINIO_ACCESS_KEY": "minio",
    "MINIO_SECRET_KEY": "minio_secret",
}

_STUBBED = ("kafka", "psycopg2", "boto3", "shared", "shared.auth", "shared.observability")


class _NoopSpan:
    def __enter__(self) -> "_NoopSpan":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def set_attribute(self, *_a: object, **_k: object) -> None:
        return None


class _NoopTracer:
    def start_as_current_span(self, *_a: object, **_k: object) -> _NoopSpan:
        return _NoopSpan()


@pytest.fixture(scope="module")
def release_core() -> Iterator[types.ModuleType]:
    saved_modules = {name: sys.modules.get(name) for name in _STUBBED}
    saved_modules["release_core"] = sys.modules.get("release_core")

    mp = pytest.MonkeyPatch()
    for key, value in _REQUIRED_ENV.items():
        mp.setenv(key, value)

    stubs = {
        "kafka": {"KafkaConsumer": object, "KafkaProducer": object},
        "psycopg2": {"connect": lambda *_a, **_k: None},
        "boto3": {},
        "shared": {},
        "shared.auth": {"get_current_user": lambda: {}},
        "shared.observability": {
            "setup_observability": lambda *_a, **_k: None,
            "get_tracer": lambda *_a, **_k: _NoopTracer(),
        },
    }
    try:
        module = load_module_from_path(
            "release_core",
            SERVICES / "release_worker" / "release_core.py",
            extra_syspath=[SERVICES / "release_worker"],
            stubs=stubs,
        )
        yield module
    finally:
        mp.undo()
        for name, original in saved_modules.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


class FakeConnection:
    """Mimics the psycopg2 connection surface get_pg_conn relies on."""

    def __init__(self) -> None:
        self.closed = False
        self.committed = False
        self.rolled_back = False

    def __enter__(self) -> "FakeConnection":
        return self

    def __exit__(self, exc_type: object, *_rest: object) -> None:
        if exc_type is None:
            self.committed = True
        else:
            self.rolled_back = True

    def close(self) -> None:
        self.closed = True


class TestModuleShape:
    def test_imports(self, release_core: types.ModuleType) -> None:
        assert callable(release_core.get_pg_conn)
        assert callable(release_core.start_kafka_consumer)

    def test_celery_layer_is_gone(self, release_core: types.ModuleType) -> None:
        for name in ("celery_app", "Celery", "execute_pipeline", "execute_rollback",
                     "detect_config_drift", "CELERY_BROKER"):
            assert not hasattr(release_core, name), name
        assert "celery" not in sys.modules

    def test_mongo_constants_are_gone(self, release_core: types.ModuleType) -> None:
        assert not hasattr(release_core, "MONGO_URI")
        assert not hasattr(release_core, "MONGO_DB")

    def test_all_names_resolve(self, release_core: types.ModuleType) -> None:
        # Routers do `from release_core import *`; a stale __all__ entry would
        # raise AttributeError at app import.
        missing = [n for n in release_core.__all__ if not hasattr(release_core, n)]
        assert missing == []


class TestGetPgConn:
    def test_closes_after_success_and_commits(
        self, release_core: types.ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = FakeConnection()
        monkeypatch.setattr(release_core.psycopg2, "connect", lambda dsn: fake)

        with release_core.get_pg_conn() as conn:
            assert conn is fake
            assert not fake.closed

        assert fake.closed
        assert fake.committed and not fake.rolled_back

    def test_closes_after_exception_and_rolls_back(
        self, release_core: types.ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = FakeConnection()
        monkeypatch.setattr(release_core.psycopg2, "connect", lambda dsn: fake)

        with pytest.raises(RuntimeError, match="boom"):
            with release_core.get_pg_conn():
                raise RuntimeError("boom")

        assert fake.closed
        assert fake.rolled_back and not fake.committed

    def test_uses_configured_dsn(
        self, release_core: types.ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[str] = []

        def connect(dsn: str) -> FakeConnection:
            seen.append(dsn)
            return FakeConnection()

        monkeypatch.setattr(release_core.psycopg2, "connect", connect)
        with release_core.get_pg_conn():
            pass
        assert seen == [_REQUIRED_ENV["DATABASE_URL"]]
