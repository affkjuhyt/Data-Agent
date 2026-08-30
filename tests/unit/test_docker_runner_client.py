"""_get_client() (sandbox_schema_docs.docx §1.4, PR review fix §8.7) — should
prefer the rootless Docker-in-Docker sidecar (SANDBOX_DOCKER_HOST, set in
deploy/docker/docker-compose.app.yml's sandbox-executor service) over the host's own
/var/run/docker.sock, so a compromised sandbox-executor no longer reaches
host root through the socket. docker.from_env()/docker.DockerClient are
mocked — no real Docker daemon needed for this branching logic.
"""
from __future__ import annotations

import pathlib
import sys
from unittest.mock import MagicMock

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SVC_DIR = str(REPO_ROOT / "backend" / "services" / "sandbox-executor")


@pytest.fixture(autouse=True)
def _sys_path():
    added = [p for p in (_SVC_DIR,) if p not in sys.path]
    sys.path[:0] = added
    sys.modules.pop("docker_runner", None)
    yield
    sys.modules.pop("docker_runner", None)
    for p in added:
        if p in sys.path:
            sys.path.remove(p)


@pytest.mark.unit
def test_falls_back_to_host_socket_when_unset(monkeypatch):
    monkeypatch.delenv("SANDBOX_DOCKER_HOST", raising=False)
    import docker_runner as dr

    from_env_spy = MagicMock(return_value=MagicMock())
    client_spy = MagicMock()
    monkeypatch.setattr(dr.docker, "from_env", from_env_spy)
    monkeypatch.setattr(dr.docker, "DockerClient", client_spy)

    dr._get_client()

    from_env_spy.assert_called_once()
    client_spy.assert_not_called()


@pytest.mark.unit
def test_uses_rootless_sidecar_when_set(monkeypatch):
    monkeypatch.setenv("SANDBOX_DOCKER_HOST", "tcp://sandbox-dockerd:2375")
    import docker_runner as dr

    from_env_spy = MagicMock()
    client_spy = MagicMock(return_value=MagicMock())
    monkeypatch.setattr(dr.docker, "from_env", from_env_spy)
    monkeypatch.setattr(dr.docker, "DockerClient", client_spy)

    dr._get_client()

    client_spy.assert_called_once_with(base_url="tcp://sandbox-dockerd:2375")
    from_env_spy.assert_not_called()


@pytest.mark.unit
def test_client_is_cached_across_calls(monkeypatch):
    monkeypatch.delenv("SANDBOX_DOCKER_HOST", raising=False)
    import docker_runner as dr

    from_env_spy = MagicMock(return_value=MagicMock())
    monkeypatch.setattr(dr.docker, "from_env", from_env_spy)

    first = dr._get_client()
    second = dr._get_client()

    assert first is second
    from_env_spy.assert_called_once()
