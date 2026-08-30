"""Unit tests for pool.py's sandbox-egress network wiring (docs/1.md
"Kế hoạch 1" mục a) — network creation, container attachment, and the
automatic startup probe that falls the whole pool back to no network at
all when default-deny isn't actually in effect. No real Docker daemon
involved (fake client) so this stays fast/deterministic and covers both
branches (probe passes / probe fails) without needing real iptables
rules applied anywhere — tests/integration/test_sandbox_pool.py covers
network creation against a real Docker daemon.
"""
from __future__ import annotations

import itertools
import pathlib
import sys
from types import SimpleNamespace

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SVC_DIR = str(REPO_ROOT / "backend" / "services" / "sandbox_executor")


@pytest.fixture(autouse=True)
def _sys_path():
    added = [p for p in (_SVC_DIR,) if p not in sys.path]
    sys.path[:0] = added
    sys.modules.pop("pool", None)
    yield
    sys.modules.pop("pool", None)
    for p in added:
        if p in sys.path:
            sys.path.remove(p)


class FakeContainer:
    _ids = itertools.count()

    def __init__(self, network=None, network_disabled=False, probe_exit_code=0):
        self.id = f"fake{next(FakeContainer._ids)}"
        self.status = "running"
        self.removed = False
        self.network = network
        self.network_disabled = network_disabled
        self._probe_exit_code = probe_exit_code

    def reload(self):
        pass

    def remove(self, force=False):
        self.removed = True

    def stats(self, stream=False):
        return {"memory_stats": {"usage": 1000}}

    def exec_run(self, cmd, demux=False):
        # pool.py's egress probe: ["python3", "-c", _EGRESS_PROBE_CODE]
        if cmd[:2] == ["python3", "-c"] and "socket" in cmd[2]:
            return (self._probe_exit_code, b"")
        return (0, b"REMAINING=0\n")


class FakeNetworks:
    def __init__(self):
        self.created: dict[str, SimpleNamespace] = {}
        self.create_calls: list[dict] = []

    def get(self, name):
        import docker.errors
        if name not in self.created:
            raise docker.errors.NotFound("no such network")
        return self.created[name]

    def create(self, name, **kwargs):
        self.create_calls.append({"name": name, **kwargs})
        net = SimpleNamespace(name=name)
        self.created[name] = net
        return net


class FakeContainers:
    def __init__(self, probe_exit_code_holder: list[int]):
        self._probe_exit_code_holder = probe_exit_code_holder
        self.run_calls: list[dict] = []

    def run(self, image, cmd, **kwargs):
        self.run_calls.append(kwargs)
        return FakeContainer(
            network=kwargs.get("network"),
            network_disabled=kwargs.get("network_disabled", False),
            probe_exit_code=self._probe_exit_code_holder[0],
        )

    def list(self, all=True, filters=None):
        return []


class FakeClient:
    """`probe_blocked=True` simulates a correctly-configured DOCKER-USER
    deny-all (pool.py's egress probe exec returns 0/blocked); `False`
    simulates no iptables rules applied at all (probe returns 1/reachable,
    matching what was actually observed against a real Docker daemon with
    a freshly created bridge network and zero DOCKER-USER rules)."""

    def __init__(self, probe_blocked: bool = True):
        self._probe_exit_code_holder = [0 if probe_blocked else 1]
        self.networks = FakeNetworks()
        self.containers = FakeContainers(self._probe_exit_code_holder)


def _pool(client, **kwargs):
    import pool
    kwargs.setdefault("client", client)
    kwargs.setdefault("image", "fake:latest")
    kwargs.setdefault("size", 2)
    kwargs.setdefault("memory_mb", 256)
    kwargs.setdefault("cpus", 0.5)
    kwargs.setdefault("pids_limit", 50)
    kwargs.setdefault("janitor_interval_s", 10_000)  # keep the janitor from firing mid-test
    return pool.ContainerPool(**kwargs)


@pytest.mark.unit
class TestEgressDisabledByDefault:
    def test_no_network_calls_and_containers_use_network_disabled(self):
        client = FakeClient()
        p = _pool(client)  # egress_enabled defaults to False
        try:
            p.start()
            assert client.networks.create_calls == []
            assert all(call.get("network_disabled") is True for call in client.containers.run_calls)
            assert all(call.get("network") is None for call in client.containers.run_calls)
        finally:
            p.shutdown()


@pytest.mark.unit
class TestEgressEnabledProbePasses:
    def test_creates_network_with_expected_spec_and_attaches_containers(self):
        client = FakeClient(probe_blocked=True)
        p = _pool(client, egress_enabled=True, egress_network="sandbox-egress", egress_subnet="172.28.0.0/24")
        try:
            p.start()
            assert len(client.networks.create_calls) == 1
            call = client.networks.create_calls[0]
            assert call["name"] == "sandbox-egress"
            assert call["driver"] == "bridge"
            assert call["options"] == {"com.docker.network.bridge.enable_icc": "false"}

            assert p.egress_enabled is True, "probe passed — egress must stay enabled"
            assert all(c.get("network") == "sandbox-egress" for c in client.containers.run_calls)
            assert all(c.get("network_disabled") is None for c in client.containers.run_calls)
        finally:
            p.shutdown()

    def test_reuses_existing_network_without_recreating(self):
        client = FakeClient(probe_blocked=True)
        client.networks.created["sandbox-egress"] = SimpleNamespace(name="sandbox-egress")
        p = _pool(client, egress_enabled=True)
        try:
            p.start()
            assert client.networks.create_calls == [], "must not recreate a network that already exists"
        finally:
            p.shutdown()


@pytest.mark.unit
class TestEgressEnabledProbeFails:
    def test_falls_back_to_no_network_for_every_container(self):
        """Mirrors what was actually observed against a real Docker
        daemon: a freshly created bridge network has full NAT internet
        access by default until DOCKER-USER rules are added — the probe
        (pool.py's _verify_egress_isolation) must catch that and refuse
        to trust the network rather than silently running open."""
        client = FakeClient(probe_blocked=False)
        p = _pool(client, egress_enabled=True, size=2)
        try:
            p.start()
            assert p.egress_enabled is False, "probe failed — must fall back, not stay enabled"

            # _respawn_all() must have replaced every container under the
            # new (disabled) branch — no leftover container still
            # attached to the egress network.
            pc1 = p.acquire()
            pc2 = p.acquire()
            try:
                for pc in (pc1, pc2):
                    assert pc.container.network_disabled is True
                    assert pc.container.network is None
            finally:
                p.release(pc1, None)
                p.release(pc2, None)
        finally:
            p.shutdown()

    def test_network_create_failure_also_falls_back_gracefully(self):
        """A Docker API error while creating the network (not just a
        failed isolation probe) must degrade to no network rather than
        crash pool startup."""
        client = FakeClient(probe_blocked=True)

        def _boom(name, **kwargs):
            raise RuntimeError("docker daemon unreachable")

        client.networks.create = _boom
        p = _pool(client, egress_enabled=True)
        try:
            p.start()
            assert p.egress_enabled is False
            assert all(c.get("network_disabled") is True for c in client.containers.run_calls)
        finally:
            p.shutdown()
