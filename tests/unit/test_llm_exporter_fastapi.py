from __future__ import annotations

import importlib
import pathlib
import sys

import pytest

pytestmark = pytest.mark.unit

pytest.importorskip("fastapi", reason="fastapi not installed in this environment")

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SVC_DIR = str(REPO_ROOT / "backend" / "services" / "llm-exporter")


@pytest.fixture
def exporter(monkeypatch):
    monkeypatch.syspath_prepend(_SVC_DIR)
    sys.modules.pop("exporter", None)
    module = importlib.import_module("exporter")
    yield module
    sys.modules.pop("exporter", None)


def test_health_and_metrics_are_served_by_fastapi(exporter, monkeypatch):
    from fastapi.testclient import TestClient

    started = {"called": False}
    monkeypatch.setattr(exporter, "start_probe_loop", lambda: started.__setitem__("called", True))

    with TestClient(exporter.app) as client:
        health = client.get("/health")
        metrics = client.get("/metrics")

    assert started["called"] is True
    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert metrics.status_code == 200
    assert metrics.headers["content-type"].startswith("text/plain; version=0.0.4")
    assert "# HELP kb_llm_up" in metrics.text
