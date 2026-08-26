"""Sandbox execution via a warm Container Pool + TTL (docs/1.md "Kế hoạch
1: Sandbox Executor — Container Pool + TTL"), replacing the old
containers.run()-per-request (ephemeral) model.

Each call execs into a container already running in pool.py's
ContainerPool instead of paying container create+start cost on every
request. network_disabled/read_only/mem/cpu/pids limits are docker
container flags set once when a pool container is created and never
change across reuse — docker exec has no per-call equivalent of
--memory/--cpus, so a request asking for a memory_mb/cpus different from
the pool's fixed config is rejected outright rather than silently
ignored.

A pool container's PID 1 is an idle keep-alive process (`sleep
infinity`), never the executed code — the code runs as a separate `exec`
with its own PID inside the same namespace. The timeout is still enforced
from the outside with a hard SIGKILL (no signal handler inside the code
to intercept/ignore it), but it now targets every process in the
container except PID 1, not the whole container — PID 1 has to survive
for the container to be reusable.

After every exec — success, error, or timeout — cleanup kills whatever is
still alive (again "everything but PID 1", found by walking the
container's own /proc rather than any PID this process happened to
track, so it also catches double-forked processes) and wipes /tmp, then
verifies exactly one process and near-idle memory remain before the
container is allowed back into the pool. Any dirty verify, cleanup error,
or timeout retires the container immediately (see pool.py) instead of
reusing it.
"""
import logging
import os
import threading
from collections.abc import Callable
from typing import Any

import docker

from pool import CircuitBreaker, CircuitBreakerOpenError, ContainerPool, memory_drifted, memory_usage_bytes

logger = logging.getLogger(__name__)

SANDBOX_IMAGE = "sandbox-runner:latest"
DEFAULT_TIMEOUT_S = int(os.environ.get("SANDBOX_TIMEOUT_S", "10"))
DEFAULT_MEMORY_MB = int(os.environ.get("SANDBOX_MEMORY_MB", "256"))
DEFAULT_CPUS = float(os.environ.get("SANDBOX_CPUS", "0.5"))
DEFAULT_PIDS_LIMIT = int(os.environ.get("SANDBOX_PIDS_LIMIT", "50"))

POOL_SIZE = int(os.environ.get("SANDBOX_POOL_SIZE", "3"))
POOL_MAX_USES = int(os.environ.get("SANDBOX_POOL_MAX_USES", "50"))
POOL_MAX_AGE_S = float(os.environ.get("SANDBOX_POOL_MAX_AGE_S", str(30 * 60)))
POOL_ACQUIRE_TIMEOUT_S = float(os.environ.get("SANDBOX_POOL_ACQUIRE_TIMEOUT_S", "30"))
# docs/1.md Kế hoạch 1 mục (g): unset (default) keeps today's runtime
# unchanged. Set to "runsc" only after runsc is installed AND registered in
# the Docker daemon's /etc/docker/daemon.json AND the ALLOWED_MODULES
# syscall-compatibility pass (mục g) has been run — flipping this without
# those two steps first just makes every container fail to start.
POOL_RUNTIME = os.environ.get("SANDBOX_RUNTIME") or None

# docs/1.md Kế hoạch 1 mục (a): dedicated bridge network instead of
# --network none. OFF by default — see pool.py's ContainerPool.__init__
# docstring for why. Turning this on without also running
# ops/setup_sandbox_egress_iptables.sh on the Docker engine backing this
# pool is caught automatically at startup (pool.py's
# _verify_egress_isolation probes 169.254.169.254 and falls every
# container back to no network at all if it's reachable).
POOL_EGRESS_ENABLED = os.environ.get("SANDBOX_EGRESS_ENABLED", "false").strip().lower() == "true"
POOL_EGRESS_NETWORK = os.environ.get("SANDBOX_EGRESS_NETWORK", "sandbox-egress")
POOL_EGRESS_SUBNET = os.environ.get("SANDBOX_EGRESS_SUBNET", "172.28.0.0/24")

# docs/1.md Kế hoạch 1 mục (e), "Circuit breaker ở tầng pool" — sliding
# window over ONLY abnormal retirements (timeout/cleanup-error/dirty-verify),
# never use-count/TTL churn. Defaults are deliberately conservative
# placeholders, not a tuned baseline — the doc is explicit that X/Y/cooldown
# need to be set from real operational data (ideally log-only for a while
# before enabling the 503 rejection this enables).
POOL_BREAKER_WINDOW_S = float(os.environ.get("SANDBOX_BREAKER_WINDOW_S", "60"))
POOL_BREAKER_THRESHOLD = int(os.environ.get("SANDBOX_BREAKER_THRESHOLD", "5"))
POOL_BREAKER_COOLDOWN_S = float(os.environ.get("SANDBOX_BREAKER_COOLDOWN_S", "30"))
POOL_BREAKER_MAX_COOLDOWN_S = float(os.environ.get("SANDBOX_BREAKER_MAX_COOLDOWN_S", "300"))
# Defaults to log-only (never actually rejects) — matches the doc's own
# recommended rollout: observe CLOSED->OPEN transitions and "would reject"
# log lines against real traffic, tune the four values above, THEN set
# SANDBOX_BREAKER_ENFORCE=true.
POOL_BREAKER_ENFORCE = os.environ.get("SANDBOX_BREAKER_ENFORCE", "false").strip().lower() == "true"

# A freshly spawned container that has never run anything sits at a
# near-zero memory reading; the very first real exec (measured: `import
# pandas` alone lands at ~3x that pristine baseline, just from shell/libc/
# interpreter/library page cache the kernel has no reason to evict) has to
# clear this threshold too, on top of whatever a genuine leak would add —
# hence 6.0 rather than a tighter multiple. _cleanup() rolls pc.idle_mem_bytes
# forward on every clean pass specifically so this factor only ever has to
# absorb that one-time first-use jump, not compound across reuses.
_MEM_DRIFT_FACTOR = 6.0
_EXEC_JOIN_GRACE_S = 5

# Runs inside the pool container as the same non-root user PID 1 runs as.
# Kills every PID visible in the container's own (private) pid namespace
# except PID 1 and the script's own $$ — equivalent to "every process in
# this container's cgroup" since nothing else shares that pid namespace,
# and unlike tracking a subprocess tree, it also catches double-forked
# processes reparented away from whatever PID this process originally
# launched. Then wipes the tmpfs /tmp and reports how many non-PID1
# processes are still alive afterwards.
_KILL_AND_CLEAN_SCRIPT = (
    "for p in /proc/[0-9]*; do "
    'n=${p#/proc/}; '
    'if [ "$n" != "1" ] && [ "$n" != "$$" ]; then kill -9 "$n" 2>/dev/null; fi; '
    "done; "
    "rm -rf /tmp/* /tmp/.[!.]* /tmp/..?* 2>/dev/null; "
    "sleep 0.05; "
    "count=0; "
    "for p in /proc/[0-9]*; do "
    'n=${p#/proc/}; [ "$n" != "1" ] && [ "$n" != "$$" ] && count=$((count+1)); '
    "done; "
    'echo "REMAINING=$count"'
)


class ExecutionTimeoutError(Exception):
    pass


class SandboxRunError(Exception):
    pass


class SandboxCircuitOpenError(Exception):
    """The pool's circuit breaker is OPEN — main.py must map this to an
    actual 503, not a 200 body with success=False, so callers stop
    retrying immediately instead of piling more load onto a pool that's
    already failing (docs/1.md Kế hoạch 1 mục e)."""


_client = None
_pool: ContainerPool | None = None
_pool_lock = threading.Lock()


def _get_client() -> Any:
    """Talks to a rootless Docker-in-Docker sidecar (`sandbox-dockerd` in
    infra/docker-compose.app.yml) when SANDBOX_DOCKER_HOST is set, instead of
    the host's own /var/run/docker.sock. A compromise of this process then
    only yields root inside that sidecar's rootless user namespace, not on
    the real host (see docs/Sandbox_SchemaCrawler_Implementation.md §8.7/§9
    for what this does and does not close). Falls back to docker.from_env()
    (host socket) when unset — local dev/CI without the sidecar still works."""
    global _client
    if _client is None:
        docker_host = os.environ.get("SANDBOX_DOCKER_HOST")
        if docker_host:
            _client = docker.DockerClient(base_url=docker_host)
        else:
            _client = docker.from_env()
    return _client


def get_pool() -> ContainerPool:
    """Lazily creates the singleton ContainerPool and spins up its warm
    containers. Blocking — call via asyncio.to_thread from async code
    (main.py's lifespan does this on startup)."""
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                pool = ContainerPool(
                    client=_get_client(),
                    image=SANDBOX_IMAGE,
                    size=POOL_SIZE,
                    memory_mb=DEFAULT_MEMORY_MB,
                    cpus=DEFAULT_CPUS,
                    pids_limit=DEFAULT_PIDS_LIMIT,
                    max_uses=POOL_MAX_USES,
                    max_age_s=POOL_MAX_AGE_S,
                    acquire_timeout_s=POOL_ACQUIRE_TIMEOUT_S,
                    mem_drift_factor=_MEM_DRIFT_FACTOR,
                    runtime=POOL_RUNTIME,
                    egress_enabled=POOL_EGRESS_ENABLED,
                    egress_network=POOL_EGRESS_NETWORK,
                    egress_subnet=POOL_EGRESS_SUBNET,
                    breaker=CircuitBreaker(
                        window_s=POOL_BREAKER_WINDOW_S,
                        threshold=POOL_BREAKER_THRESHOLD,
                        cooldown_s=POOL_BREAKER_COOLDOWN_S,
                        max_cooldown_s=POOL_BREAKER_MAX_COOLDOWN_S,
                        enforce=POOL_BREAKER_ENFORCE,
                    ),
                )
                pool.start()
                _pool = pool
    return _pool


def shutdown_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.shutdown()
        _pool = None


def _cleanup(pc: Any, pool: ContainerPool) -> tuple[bool, str]:
    """Kills everything but PID 1 and wipes /tmp; returns (clean, reason)
    where clean is True only if exactly PID 1 remains and memory usage is
    back near the container's recorded idle baseline. Broad except on
    purpose: this is the request path's last line of defense before a
    container goes back into the pool, so any failure here — not just
    APIError — must come back as "not clean," never propagate and skip
    pool.release() in run_python_sandboxed's finally block."""
    try:
        exit_code, output = pc.container.exec_run(["sh", "-c", _KILL_AND_CLEAN_SCRIPT], demux=True)
    except Exception as e:
        return False, f"cleanup exec failed: {e}"

    try:
        stdout = (output[0] if isinstance(output, tuple) else output) or b""
        text = stdout.decode("utf-8", errors="replace")
        if exit_code != 0:
            return False, f"cleanup script exited {exit_code}: {text.strip()}"

        remaining = None
        for line in text.splitlines():
            if line.startswith("REMAINING="):
                try:
                    remaining = int(line.split("=", 1)[1])
                except ValueError:
                    pass
        if remaining != 0:
            return False, f"{remaining if remaining is not None else 'unknown'} process(es) survived cleanup"

        # Reads pool.mem_drift_factor (not a separate module constant) so
        # the per-request check here and the janitor's idle re-check
        # (pool.py's _janitor_pass) can never silently diverge.
        if memory_drifted(pc.container, pc.idle_mem_bytes, pool.mem_drift_factor):
            return False, "memory usage did not return near idle baseline after cleanup"

        # Roll the baseline forward on every clean pass instead of freezing
        # it at pc.idle_mem_bytes' original pristine-spawn reading. A
        # container that has never run anything sits near-zero; the very
        # first exec (even `sh -c true`) permanently grows steady-state
        # usage several-fold just from shell/libc/interpreter page cache
        # that the kernel has no reason to evict — comparing all future
        # execs back to that pristine reading would flag that one-time,
        # harmless jump as "drift" forever after and retire almost every
        # container after its first real use. Rolling the baseline instead
        # measures "did memory grow abnormally since last time this
        # container was verified clean," which is what's actually meant to
        # be caught here.
        usage = memory_usage_bytes(pc.container)
        if usage is not None:
            pc.idle_mem_bytes = usage

        return True, ""
    except Exception as e:
        return False, f"cleanup verify raised unexpectedly: {e}"


def run_python_sandboxed(
    code: str,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    memory_mb: int = DEFAULT_MEMORY_MB,
    cpus: float = DEFAULT_CPUS,
    on_output: Callable[[str, str], None] | None = None,
) -> dict[str, Any]:
    """Returns {"stdout": str, "stderr": str, "exit_code": int, "timed_out": bool}.

    `on_output(stream, text)`, if given, is called synchronously from the
    exec thread for every stdout/stderr chunk AS the container produces
    it (docs/1.md Kế hoạch 1 mục h — "stream ... stdout-stderr của lần
    exec đang chạy", not just the final result). It runs in a plain OS
    thread, not the asyncio event loop — callers wired to asyncio state
    (main.py appends to a JobQueue's in-memory list) must only do
    something that's safe to call from an arbitrary thread; scheduling a
    coroutine or touching an event loop directly from here would not be.
    """
    pool = get_pool()
    if memory_mb != pool.memory_mb or cpus != pool.cpus:
        raise SandboxRunError(
            f"sandbox pool containers are fixed at {pool.memory_mb}MB / {pool.cpus} cpus "
            "at creation (set via SANDBOX_MEMORY_MB / SANDBOX_CPUS) — per-request "
            "memory_mb/cpus overrides aren't supported by the container pool model"
        )

    try:
        pc = pool.acquire()
    except CircuitBreakerOpenError as e:
        raise SandboxCircuitOpenError(str(e)) from e
    except TimeoutError as e:
        raise SandboxRunError(str(e)) from e

    outcome: dict[str, Any] = {}
    timed_out = False
    retire_reason = None

    # try/finally guarantees pool.release() always runs — without it, any
    # unexpected exception between acquire() and here (not just the ones
    # explicitly handled below) would leak pc in pool._busy until the
    # janitor's stuck-exec sweep rescues it minutes later.
    try:
        def _do_exec() -> None:
            try:
                # Low-level exec_create/exec_start(stream=True) instead of
                # the high-level exec_run() — the latter blocks until the
                # whole exec finishes and returns output in one shot, which
                # is what made the old SSE endpoint (main.py's _sse_events)
                # only able to report job lifecycle status, never stdout/
                # stderr while the code was still running. `-u` (unbuffered)
                # matters here too: without it CPython fully buffers stdout
                # when it isn't a TTY, so nothing would actually arrive
                # chunk-by-chunk regardless of the streaming API — confirmed
                # empirically (buffered: one giant chunk at the very end;
                # unbuffered: a chunk per print(), each with the real gap
                # between them).
                api = pc.container.client.api
                exec_id = api.exec_create(
                    pc.container.id, ["python3", "-u", "-c", code], stdout=True, stderr=True,
                )["Id"]
                stdout_parts: list[bytes] = []
                stderr_parts: list[bytes] = []
                for stdout_chunk, stderr_chunk in api.exec_start(exec_id, stream=True, demux=True):
                    if stdout_chunk:
                        stdout_parts.append(stdout_chunk)
                        if on_output is not None:
                            on_output("stdout", stdout_chunk.decode("utf-8", errors="replace"))
                    if stderr_chunk:
                        stderr_parts.append(stderr_chunk)
                        if on_output is not None:
                            on_output("stderr", stderr_chunk.decode("utf-8", errors="replace"))
                # exec_run's exit_code comes back in the same call; the
                # streaming low-level API only knows it once the stream
                # above has been fully drained (process exited or the
                # kill below closed it out), via a separate inspect call.
                outcome["exit_code"] = api.exec_inspect(exec_id).get("ExitCode", -1)
                outcome["stdout"] = b"".join(stdout_parts)
                outcome["stderr"] = b"".join(stderr_parts)
            except Exception as e:
                # Broad on purpose: a docker connection blip raises
                # requests.exceptions.ConnectionError, which is NOT a
                # docker.errors.APIError subclass — narrowly catching just
                # APIError here would leave `outcome` without "stdout"/
                # "exit_code" and crash the decode below with a KeyError.
                outcome["error"] = e

        thread = threading.Thread(target=_do_exec, daemon=True)
        thread.start()
        thread.join(timeout_s)

        timed_out = thread.is_alive()
        if timed_out:
            retire_reason = "execution timed out"

        # Kill/wipe/verify runs regardless of outcome — this is what stops a
        # timed-out (or crashed) exec's processes, not container.stop().
        clean, dirty_reason = _cleanup(pc, pool)
        if timed_out:
            # The kill above should have unblocked _do_exec's exec_run() by now;
            # give it a moment to actually return before we discard its (stale) result.
            thread.join(_EXEC_JOIN_GRACE_S)

        if retire_reason is None:
            if not clean:
                retire_reason = dirty_reason
            elif outcome.get("error") is not None:
                retire_reason = f"exec_run raised: {outcome['error']}"
    finally:
        pool.release(pc, retire_reason)

    if timed_out:
        raise ExecutionTimeoutError(f"execution exceeded {timeout_s}s timeout")

    exec_error = outcome.get("error")
    if exec_error is not None:
        raise SandboxRunError(str(exec_error))

    stdout = outcome.get("stdout", b"").decode("utf-8", errors="replace")
    stderr = outcome.get("stderr", b"").decode("utf-8", errors="replace")
    exit_code = outcome.get("exit_code", -1)
    return {"stdout": stdout, "stderr": stderr, "exit_code": exit_code, "timed_out": False}
