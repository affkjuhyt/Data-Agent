from __future__ import annotations

import pathlib
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SVC_DIR = str(REPO_ROOT / "backend" / "services" / "platform_workers")

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _sys_path():
    added = [p for p in (_SVC_DIR,) if p not in sys.path]
    sys.path[:0] = added
    for name in ("app",):
        sys.modules.pop(name, None)
    yield
    for name in ("app",):
        sys.modules.pop(name, None)
    for p in added:
        if p in sys.path:
            sys.path.remove(p)


def test_worker_name_from_env_trims_whitespace():
    import app

    assert app.worker_name_from_env({"WORKER": " audit "}) == "audit"


def test_resolve_worker_module_returns_known_targets():
    import app

    assert app.resolve_worker_module("memory") == "services.platform_workers.workers.memory"
    assert app.resolve_worker_module("schema-crawler") == "services.platform_workers.workers.schema_crawler"
    assert app.resolve_worker_module("audit") == "services.platform_workers.workers.audit"


def test_unknown_worker_message_lists_options():
    import app

    message = app.unknown_worker_message("nope")
    assert "nope" in message
    assert "memory" in message
    assert "schema-crawler" in message
    assert "audit" in message
