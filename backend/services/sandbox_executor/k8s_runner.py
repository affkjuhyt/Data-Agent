"""Kubernetes backend for sandbox execution — same public interface as
docker_runner.py (run_python_sandboxed, run_shell_sandboxed, get_pool,
shutdown_pool, and the three exception types) so main.py needs no changes
beyond which module sandbox_runner_backend.py imports from. See
k8s_pool.py's module docstring for why this backend exists and where its
guarantees differ from the Docker one.
"""
import logging
import os
import threading
from collections.abc import Callable
from typing import Any

from kubernetes import config as k8s_config

from input_names import validate_input_name
from k8s_pool import K8sPodPool, PooledPod, CircuitBreaker, CircuitBreakerOpenError

logger = logging.getLogger(__name__)

SANDBOX_NAMESPACE = os.environ.get("SANDBOX_K8S_NAMESPACE", "dataagent-sandbox")
SANDBOX_IMAGE = os.environ.get("SANDBOX_RUNNER_IMAGE", "REGISTRY/dataagent/sandbox-runner:latest")
SANDBOX_SERVICE_ACCOUNT = os.environ.get("SANDBOX_RUNNER_SERVICE_ACCOUNT", "sandbox-runner")
# Matches deploy/k8s/production/service/sandbox-runner.yaml's securityContext.runAsUser/Group.
SANDBOX_RUNNER_UID = int(os.environ.get("SANDBOX_RUNNER_UID", "10001"))
SANDBOX_RUNNER_GID = int(os.environ.get("SANDBOX_RUNNER_GID", "10001"))

SANDBOX_INPUT_DIR = "/tmp"
DEFAULT_TIMEOUT_S = int(os.environ.get("SANDBOX_TIMEOUT_S", "10"))
DEFAULT_MEMORY_MB = int(os.environ.get("SANDBOX_MEMORY_MB", "256"))
DEFAULT_CPUS = float(os.environ.get("SANDBOX_CPUS", "0.5"))

POOL_SIZE = int(os.environ.get("SANDBOX_POOL_SIZE", "3"))
POOL_MAX_USES = int(os.environ.get("SANDBOX_POOL_MAX_USES", "50"))
POOL_MAX_AGE_S = float(os.environ.get("SANDBOX_POOL_MAX_AGE_S", str(30 * 60)))
POOL_ACQUIRE_TIMEOUT_S = float(os.environ.get("SANDBOX_POOL_ACQUIRE_TIMEOUT_S", "30"))

POOL_BREAKER_WINDOW_S = float(os.environ.get("SANDBOX_BREAKER_WINDOW_S", "60"))
POOL_BREAKER_THRESHOLD = int(os.environ.get("SANDBOX_BREAKER_THRESHOLD", "5"))
POOL_BREAKER_COOLDOWN_S = float(os.environ.get("SANDBOX_BREAKER_COOLDOWN_S", "30"))
POOL_BREAKER_MAX_COOLDOWN_S = float(os.environ.get("SANDBOX_BREAKER_MAX_COOLDOWN_S", "300"))
POOL_BREAKER_ENFORCE = os.environ.get("SANDBOX_BREAKER_ENFORCE", "false").strip().lower() == "true"

_EXEC_JOIN_GRACE_S = 5


class ExecutionTimeoutError(Exception):
    pass


class SandboxRunError(Exception):
    pass


class SandboxCircuitOpenError(Exception):
    """Same role as docker_runner.py's: main.py maps this to a 503."""


_pool: K8sPodPool | None = None
_pool_lock = threading.Lock()
_kube_loaded = False


def _ensure_kube_config() -> None:
    global _kube_loaded
    if _kube_loaded:
        return
    # In-cluster: reads the ServiceAccount token/CA mounted at the standard
    # path. This is why sandbox-executor.yaml must set
    # automountServiceAccountToken: true — the one deliberate exception to
    # the platform's default (see deploy/k8s/README.md's Identity section;
    # openbao is the other, for the same reason: it has to call the API
    # server, so it has to mount its token).
    k8s_config.load_incluster_config()
    _kube_loaded = True


def get_pool() -> K8sPodPool:
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _ensure_kube_config()
                pool = K8sPodPool(
                    namespace=SANDBOX_NAMESPACE,
                    image=SANDBOX_IMAGE,
                    size=POOL_SIZE,
                    memory_mb=DEFAULT_MEMORY_MB,
                    cpus=DEFAULT_CPUS,
                    max_uses=POOL_MAX_USES,
                    max_age_s=POOL_MAX_AGE_S,
                    acquire_timeout_s=POOL_ACQUIRE_TIMEOUT_S,
                    run_as_user=SANDBOX_RUNNER_UID,
                    run_as_group=SANDBOX_RUNNER_GID,
                    service_account=SANDBOX_SERVICE_ACCOUNT,
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


def _stage_files(pool: K8sPodPool, pp: PooledPod, files: dict[str, bytes]) -> None:
    """Same contract as docker_runner.py's _stage_files: write each input
    through an exec's stdin (`cat > path`), mode 0444, single path segment
    names only. Kept per-file (not a tar-in-one-exec) to match the existing
    behavior and error granularity exactly."""
    for name, content in files.items():
        try:
            validate_input_name(name)
        except ValueError as e:
            raise SandboxRunError(str(e)) from e

        exit_code, _stdout, _stderr = pool.exec(
            pp,
            ["sh", "-c", 'cat > "$1" && chmod 444 "$1"', "sh", f"{SANDBOX_INPUT_DIR}/{name}"],
            stdin_bytes=content,
        )
        if exit_code != 0:
            raise SandboxRunError(f"staging {name!r} into the sandbox failed (exit {exit_code})")


def run_python_sandboxed(
    code: str,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    memory_mb: int = DEFAULT_MEMORY_MB,
    cpus: float = DEFAULT_CPUS,
    on_output: Callable[[str, str], None] | None = None,
    files: dict[str, bytes] | None = None,
) -> dict[str, Any]:
    return _run_sandboxed(
        ["python3", "-u", "-c", code],
        timeout_s=timeout_s, memory_mb=memory_mb, cpus=cpus, on_output=on_output, files=files,
    )


def run_shell_sandboxed(
    command: str,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    memory_mb: int = DEFAULT_MEMORY_MB,
    cpus: float = DEFAULT_CPUS,
    on_output: Callable[[str, str], None] | None = None,
    workdir: str | None = None,
) -> dict[str, Any]:
    # k8s exec has no native "workdir" kwarg (unlike Docker's exec_create),
    # so cd happens inside the shell — workdir/command are passed as
    # separate argv elements (never concatenated into the script text) so
    # neither can be read as shell syntax.
    if workdir:
        argv = ["sh", "-c", 'cd "$1" && shift && exec sh -c "$1"', "sh", workdir, command]
    else:
        argv = ["sh", "-c", command]
    return _run_sandboxed(
        argv, timeout_s=timeout_s, memory_mb=memory_mb, cpus=cpus, on_output=on_output,
    )


def _run_sandboxed(
    argv: list[str],
    *,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    memory_mb: int = DEFAULT_MEMORY_MB,
    cpus: float = DEFAULT_CPUS,
    on_output: Callable[[str, str], None] | None = None,
    files: dict[str, bytes] | None = None,
) -> dict[str, Any]:
    pool = get_pool()
    if memory_mb != pool.memory_mb or cpus != pool.cpus:
        raise SandboxRunError(
            f"sandbox pool pods are fixed at {pool.memory_mb}MB / {pool.cpus} cpus "
            "at creation (set via SANDBOX_MEMORY_MB / SANDBOX_CPUS) — per-request "
            "memory_mb/cpus overrides aren't supported by the pod pool model"
        )

    try:
        pp = pool.acquire()
    except CircuitBreakerOpenError as e:
        raise SandboxCircuitOpenError(str(e)) from e
    except TimeoutError as e:
        raise SandboxRunError(str(e)) from e

    outcome: dict[str, Any] = {}
    timed_out = False
    retire_reason = None

    try:
        if files:
            try:
                _stage_files(pool, pp, files)
            except Exception as e:
                retire_reason = f"input staging failed: {e}"
                raise SandboxRunError(f"could not stage input files into the sandbox: {e}") from e

        def _do_exec() -> None:
            try:
                exit_code, stdout, stderr = pool.exec(pp, argv, on_output=on_output)
                outcome["exit_code"] = exit_code
                outcome["stdout"] = stdout
                outcome["stderr"] = stderr
            except Exception as e:
                outcome["error"] = e

        thread = threading.Thread(target=_do_exec, daemon=True)
        thread.start()
        thread.join(timeout_s)

        timed_out = thread.is_alive()
        if timed_out:
            retire_reason = "execution timed out"

        # Regardless of outcome — this is what actually stops a timed-out
        # exec's processes (there is no pod.stop() equivalent invoked here;
        # killing every non-PID-1 process from inside is the boundary,
        # same as the Docker backend).
        clean, dirty_reason = pool.cleanup(pp)
        if timed_out:
            thread.join(_EXEC_JOIN_GRACE_S)

        if retire_reason is None:
            if not clean:
                retire_reason = dirty_reason
            elif outcome.get("error") is not None:
                retire_reason = f"exec raised: {outcome['error']}"
    finally:
        pool.release(pp, retire_reason)

    if timed_out:
        raise ExecutionTimeoutError(f"execution exceeded {timeout_s}s timeout")

    exec_error = outcome.get("error")
    if exec_error is not None:
        raise SandboxRunError(str(exec_error))

    stdout = outcome.get("stdout", b"").decode("utf-8", errors="replace")
    stderr = outcome.get("stderr", b"").decode("utf-8", errors="replace")
    exit_code = outcome.get("exit_code", -1)
    return {"stdout": stdout, "stderr": stderr, "exit_code": exit_code, "timed_out": False}
