"""Picks the sandbox execution backend so main.py doesn't have to know which
one is in play. `docker` (default) is docker_runner.py — a warm Docker
container pool via the Docker socket / SANDBOX_DOCKER_HOST, unchanged, for a
host with a Docker daemon. `kubernetes` is k8s_runner.py — a warm Pod pool
in the dataagent-sandbox namespace, for the k8s deployment (see
k8s_pool.py's module docstring for why the Docker socket approach doesn't
work there).

    SANDBOX_BACKEND=docker      (default) docker_runner.py
    SANDBOX_BACKEND=kubernetes  k8s_runner.py
"""
import os

_BACKEND = os.environ.get("SANDBOX_BACKEND", "docker").strip().lower()

if _BACKEND == "kubernetes":
    from k8s_runner import (  # noqa: F401
        run_python_sandboxed,
        run_shell_sandboxed,
        ExecutionTimeoutError,
        SandboxRunError,
        SandboxCircuitOpenError,
        get_pool,
        shutdown_pool,
    )
elif _BACKEND == "docker":
    from docker_runner import (  # noqa: F401
        run_python_sandboxed,
        run_shell_sandboxed,
        ExecutionTimeoutError,
        SandboxRunError,
        SandboxCircuitOpenError,
        get_pool,
        shutdown_pool,
    )
else:
    raise ValueError(f"unknown SANDBOX_BACKEND: {_BACKEND!r} (expected 'docker' or 'kubernetes')")
