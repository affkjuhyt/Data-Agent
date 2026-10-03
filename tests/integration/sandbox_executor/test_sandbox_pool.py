"""Integration tests for the Container Pool + TTL cleanup mechanism
(docs/1.md "Kế hoạch 1") — specifically `docker_runner._cleanup()` /
`pool.py`'s ContainerPool, which is the *only* thing standing between one
tenant's leftover processes and the next request that reuses the same
container. Drives a real ContainerPool (size 1, built directly rather
than via docker_runner's shared env-configured singleton, so acquire/
release behavior is deterministic) against the real Docker daemon and the
`sandbox-runner:latest` image; skipped otherwise (same gate as the other
sandbox_executor integration tests).

This exercises `_cleanup()`/`ContainerPool` directly rather than going
through main.py's `/execute/python` HTTP endpoint, so it can plant a
double-forked orphan process via a raw shell exec — the AST security
validator (security_visitor.py, covered by its own unit tests) would
otherwise block the `os`/`subprocess` access needed to do that from
Python.
"""
from __future__ import annotations

import pathlib
import sys

import docker
import pytest

from tests.integration._sandbox import SANDBOX_RUNNER_IMAGE, SKIP_REASON, docker_and_image_available

pytestmark = pytest.mark.integration

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
_SVC_DIR = str(REPO_ROOT / "backend" / "services" / "sandbox_executor")
_IMAGE = SANDBOX_RUNNER_IMAGE


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_and_image_available(), reason=SKIP_REASON),
]


@pytest.fixture
def one_container_pool():
    """A standalone size-1 ContainerPool, independent of docker_runner's
    shared env-configured singleton — makes acquire/release deterministic
    (there's only ever one container to get back) instead of depending on
    SANDBOX_POOL_SIZE and queue ordering across N warm containers."""
    added = [p for p in (_SVC_DIR,) if p not in sys.path]
    sys.path[:0] = added
    try:
        sys.modules.pop("pool", None)
        sys.modules.pop("docker_runner", None)
        import docker_runner as dr
        import pool as pool_mod

        client = docker.from_env()
        pool = pool_mod.ContainerPool(
            client=client,
            image=_IMAGE,
            size=1,
            memory_mb=256,
            cpus=0.5,
            pids_limit=50,
        )
        pool.start()
        try:
            yield pool, dr
        finally:
            pool.shutdown()
    finally:
        for p in added:
            if p in sys.path:
                sys.path.remove(p)
        sys.modules.pop("pool", None)
        sys.modules.pop("docker_runner", None)


def test_cleanup_signals_dirty_on_double_forked_orphan(one_container_pool):
    """The whole point of walking /proc instead of tracking a subprocess
    tree (docker_runner.py's _KILL_AND_CLEAN_SCRIPT) is to catch processes
    that escaped whatever PID this process happened to launch. A
    subshell-backgrounded orphan (`(sleep 30 &)`) proves exactly that: by
    the time cleanup runs, its immediate parent has already exited and it
    has been reparented to PID 1 (`sleep infinity`), with no process-tree
    link back to the `sh -c` invocation that spawned it.

    The kill loop DOES send it SIGKILL — but PID 1 here is a plain `sleep`,
    not an init/reaper, so the now-dead orphan lingers as a zombie in
    /proc until something calls wait() on it, which PID 1 never does.
    _cleanup() must therefore report "not clean" (a live PID-count of 0
    isn't achievable for this container anymore) rather than a false
    "clean" — this is the documented safety valve (docs/1.md Kế hoạch 1,
    §e.3) that retires a container instead of trusting it's actually
    empty."""
    pool, dr = one_container_pool
    pc = pool.acquire()
    try:
        exit_code, _ = pc.container.exec_run(["sh", "-c", "(sleep 30 &) ; true"], demux=True)
        assert exit_code == 0

        exit_code, output = pc.container.exec_run(["sh", "-c", "ls /proc | grep -E '^[0-9]+$' | wc -l"], demux=True)
        stdout = (output[0] if isinstance(output, tuple) else output) or b""
        assert int(stdout.decode().strip()) > 1, "the orphaned sleep should still be alive before cleanup"

        clean, reason = dr._cleanup(pc, pool)
        assert not clean, "cleanup should NOT report clean — the killed orphan is a zombie PID 1 never reaps"
        assert "survived cleanup" in reason, reason
    finally:
        # Mirrors run_python_sandboxed's real behavior: a not-clean result
        # retires the container instead of returning it to the pool.
        pool.release(pc, reason)


def test_cleanup_wipes_tmp(one_container_pool):
    pool, dr = one_container_pool
    pc = pool.acquire()
    try:
        exit_code, _ = pc.container.exec_run(["sh", "-c", "echo leftover > /tmp/leaked_file"], demux=True)
        assert exit_code == 0

        clean, reason = dr._cleanup(pc, pool)
        assert clean, f"cleanup should have succeeded: {reason}"

        exit_code, _ = pc.container.exec_run(["sh", "-c", "test ! -e /tmp/leaked_file"], demux=True)
        assert exit_code == 0, "/tmp should have been wiped by cleanup"
    finally:
        pool.release(pc, None)


PROBE_COMMAND = (
    # Any egress at all is a failure. python3 rather than curl/wget: the
    # sandbox image is python:slim and carries neither.
    """python3 -c "import socket; socket.create_connection(('1.1.1.1', 80), 2)\""""
)


def test_input_files_are_staged_into_a_read_only_container(one_container_pool):
    """The mechanism, tested where the mechanism actually lives.

    docker refuses put_archive on any container created with --read-only
    ("container rootfs is marked read-only") even when the target is a writable
    tmpfs mount, so _stage_files writes through an exec's stdin instead. Against
    the archive API this test fails outright, which is the point of having it:
    no unit test can tell the two apart.
    """
    pool, dr = one_container_pool
    pc = pool.acquire()
    try:
        dr._stage_files(pc, {"input.csv": b"region,amount\nnorth,10\n"})

        exit_code, output = pc.container.exec_run(["sh", "-c", "cat /tmp/input.csv"], demux=True)
        stdout = (output[0] if isinstance(output, tuple) else output) or b""
        assert exit_code == 0, "staged file is not readable inside the container"
        assert b"north,10" in stdout

        # Staging must not have loosened anything: the root filesystem is still
        # read-only, which is why the archive API refused in the first place.
        exit_code, _ = pc.container.exec_run(["sh", "-c", "touch /rootfs-probe"], demux=True)
        assert exit_code != 0, "root filesystem became writable"
    finally:
        pool.release(pc, None)


def test_a_staged_file_does_not_outlive_its_request(one_container_pool):
    """Containers are reused. An input staged for one tenant's question that
    survived into the next request would be a data leak with a queue in front
    of it."""
    pool, dr = one_container_pool
    pc = pool.acquire()
    try:
        dr._stage_files(pc, {"input.csv": b"secret\n"})

        clean, reason = dr._cleanup(pc, pool)
        assert clean, f"cleanup should have succeeded: {reason}"

        exit_code, _ = pc.container.exec_run(["sh", "-c", "test ! -e /tmp/input.csv"], demux=True)
        assert exit_code == 0, "the staged file survived the post-exec wipe"
    finally:
        pool.release(pc, None)


def test_a_traversing_input_name_is_refused(one_container_pool):
    """The name becomes the redirect target of a shell write inside the
    container. The request model validates it too; this is the layer that would
    have to hold if that ever stopped being true."""
    pool, dr = one_container_pool
    pc = pool.acquire()
    try:
        with pytest.raises(dr.SandboxRunError):
            dr._stage_files(pc, {"../escape.csv": b"x"})
    finally:
        pool.release(pc, None)


def test_shell_commands_run_in_the_pool_container(one_container_pool, monkeypatch):
    """workflow_runtime's `bash` tool used to be subprocess.run(shell=True) in
    workflow_runtime's own process. This is where it runs now."""
    pool, dr = one_container_pool
    monkeypatch.setattr(dr, "get_pool", lambda: pool)

    result = dr.run_shell_sandboxed("echo hi && echo oops >&2 && exit 3", timeout_s=10)

    assert result["exit_code"] == 3
    assert "hi" in result["stdout"]
    assert "oops" in result["stderr"]
    assert result["timed_out"] is False


def test_a_shell_command_has_no_network(one_container_pool, monkeypatch):
    """No AST validator stands in front of a shell command, and none can — a
    command has no structure to inspect. The container is the control, so the
    property is checked here, from inside a command trying to break it."""
    pool, dr = one_container_pool
    monkeypatch.setattr(dr, "get_pool", lambda: pool)

    result = dr.run_shell_sandboxed(
        PROBE_COMMAND,
        timeout_s=10,
    )

    assert result["exit_code"] != 0, "the sandbox reached the network"


def test_a_shell_command_runs_in_the_requested_workdir(one_container_pool, monkeypatch):
    """The bash tool's `cwd` config names a path inside the sandbox now."""
    pool, dr = one_container_pool
    monkeypatch.setattr(dr, "get_pool", lambda: pool)

    result = dr.run_shell_sandboxed("pwd", timeout_s=10, workdir="/tmp")

    assert result["stdout"].strip() == "/tmp"


def test_clean_container_is_reused_not_retired(one_container_pool):
    pool, dr = one_container_pool
    pc = pool.acquire()
    container_id_before = pc.container.id

    clean, reason = dr._cleanup(pc, pool)
    assert clean, f"expected a clean container: {reason}"
    pool.release(pc, None)

    reacquired = pool.acquire()
    try:
        assert reacquired.container.id == container_id_before, "the same (reused) container should come back, not a replacement"
    finally:
        pool.release(reacquired, None)


def test_dirty_container_is_retired_and_replaced(one_container_pool):
    """A process that survives the kill loop (simulated here by pids_limit
    exhaustion making the cleanup exec itself fail to even spawn) must
    retire the container rather than silently returning it to the pool."""
    pool, dr = one_container_pool
    pc = pool.acquire()
    container_id_before = pc.container.id
    pool.release(pc, "forced retirement for test")

    reacquired = pool.acquire()
    try:
        assert reacquired.container.id != container_id_before, "a retired container must be replaced, not reused"
    finally:
        pool.release(reacquired, None)


def test_spawned_containers_carry_expected_labels(one_container_pool):
    """docs/1.md Kế hoạch 1 mục a/f: labels are what let the Janitor
    reconcile against Docker's own view instead of only trusting this
    process's in-memory state, and let operators/other host tooling tell
    at a glance which containers belong to the sandbox pool."""
    pool, dr = one_container_pool
    import pool as pool_mod

    pc = pool.acquire()
    try:
        pc.container.reload()
        labels = pc.container.labels
        assert labels.get(pool_mod.LABEL_POOL) == "true"
        assert labels.get(pool_mod.LABEL_POOL_ID) == pool.pool_id
        assert labels.get(pool_mod.LABEL_CREATED_AT, "").isdigit()
    finally:
        pool.release(pc, None)


def test_janitor_removes_orphaned_labeled_container(one_container_pool):
    """An orphan is a container Docker reports under this pool's label
    that the in-memory pool state doesn't track — e.g. left over from a
    previous process lifetime after a restart (docs/1.md mục a/f's own
    example). Simulated here by spawning a container out of band with the
    same pool_id label, bypassing pool._all entirely, then running a
    janitor label-reconciliation pass directly and confirming Docker no
    longer has it."""
    pool, dr = one_container_pool
    import pool as pool_mod

    client = docker.from_env()
    orphan = client.containers.run(
        _IMAGE, pool_mod.IDLE_CMD, detach=True,
        labels={
            pool_mod.LABEL_POOL: "true",
            pool_mod.LABEL_POOL_ID: pool.pool_id,
            pool_mod.LABEL_CREATED_AT: "0",
        },
    )
    try:
        pool._reconcile_labels()
        with pytest.raises(docker.errors.NotFound):
            orphan.reload()
    finally:
        try:
            orphan.remove(force=True)
        except docker.errors.NotFound:
            pass


def test_reconcile_logs_but_does_not_touch_containers_missing_from_docker(one_container_pool, caplog):
    """Reverse drift (tracked internally, but Docker's labeled list no
    longer shows it — e.g. removed out-of-band) is logged only. pool.py
    deliberately leaves this case to the existing acquire()-time health
    check / stuck-exec sweep rather than mutating pool state from inside
    the janitor's label scan (see _reconcile_labels()'s own docstring for
    why: racing acquire()/release() from here is exactly what the janitor
    should stay out of)."""
    pool, dr = one_container_pool
    pc = pool.acquire()
    try:
        pc.container.remove(force=True)  # gone from Docker; pool.py doesn't know yet

        with caplog.at_level("WARNING"):
            pool._reconcile_labels()

        assert any("no longer reports it" in r.message for r in caplog.records)
    finally:
        # The pool still thinks this container is busy/tracked; drop it
        # from tracking directly so shutdown() doesn't try to remove an
        # already-gone container.
        pool._busy.pop(pc.id, None)
        pool._all.pop(pc.id, None)


@pytest.fixture
def egress_pool():
    """Same shape as one_container_pool, but with egress_enabled=True and
    a test-only network name so it can't collide with (or leak into) a
    real deployment's `sandbox-egress`. Always tears the network down —
    whether the test left it enabled or the isolation probe fell it back
    to disabled."""
    added = [p for p in (_SVC_DIR,) if p not in sys.path]
    sys.path[:0] = added
    network_name = "sandbox-egress-test"
    try:
        sys.modules.pop("pool", None)
        import pool as pool_mod

        client = docker.from_env()
        pool = pool_mod.ContainerPool(
            client=client,
            image=_IMAGE,
            size=1,
            memory_mb=256,
            cpus=0.5,
            pids_limit=50,
            egress_enabled=True,
            egress_network=network_name,
        )
        pool.start()
        try:
            yield pool
        finally:
            pool.shutdown()
            try:
                client.networks.get(network_name).remove()
            except docker.errors.NotFound:
                pass
    finally:
        for p in added:
            if p in sys.path:
                sys.path.remove(p)
        sys.modules.pop("pool", None)


# One xdist worker for both: they share the fixed "sandbox-egress-test" network.
@pytest.mark.xdist_group("sandbox-egress-test")
def test_egress_network_is_created_with_expected_spec(egress_pool):
    """docs/1.md mục a: dedicated bridge network, never the default
    Docker bridge, with inter-container communication disabled
    (enable_icc=false) so one compromised pool container can't pivot
    straight to another tenant's container sitting idle in the same
    pool."""
    pool = egress_pool
    client = docker.from_env()
    net = client.networks.get(pool.egress_network)
    net.reload()
    assert net.attrs["Driver"] == "bridge"
    assert net.attrs["Options"].get("com.docker.network.bridge.enable_icc") == "false"


# One xdist worker for both: they share the fixed "sandbox-egress-test" network.
@pytest.mark.xdist_group("sandbox-egress-test")
def test_egress_probe_falls_back_when_iptables_not_applied(egress_pool):
    """No DOCKER-USER rules exist for the test network in this test run
    (this repo's tests never modify host iptables — see
    ops/setup_sandbox_egress_iptables.sh, which is meant to be run by an
    operator against whatever Docker engine backs a real deployment).
    A fresh Docker bridge network NATs to the real internet by default,
    so pool.py's startup probe (_verify_egress_isolation) MUST detect
    that egress is unrestricted here and fall every container back to
    no network at all — confirms the fail-safe behavior end to end
    against a real Docker daemon, not just the fake-client unit tests in
    test_pool_egress.py."""
    pool = egress_pool
    assert pool.egress_enabled is False, (
        "the isolation probe should have detected unrestricted internet egress "
        "on this freshly created network (no iptables rules applied) and disabled it"
    )
    pc = pool.acquire()
    try:
        pc.container.reload()
        assert pc.container.attrs["NetworkSettings"]["Networks"] == {}, (
            "fallback containers must have no network attached at all"
        )
    finally:
        pool.release(pc, None)
