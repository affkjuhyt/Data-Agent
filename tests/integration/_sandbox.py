"""Gate for the integration tests that start real sandbox-runner containers.

The image name comes from SANDBOX_RUNNER_IMAGE, the variable sandbox_executor
itself reads (docker_runner.py, k8s_runner.py), because the tag differs per
host: a plain `docker build` gives `sandbox-runner:latest`, the deploy builds
`dataagent/sandbox-runner:latest`. The gate used to hardcode the first and also
required the docker CLI on PATH, which the SDK never uses — so inside a service
image these tests always skipped, however the daemon was reachable.
"""
from __future__ import annotations

import os

SANDBOX_RUNNER_IMAGE = os.environ.get("SANDBOX_RUNNER_IMAGE", "sandbox-runner:latest")
SKIP_REASON = (
    f"Docker daemon or the {SANDBOX_RUNNER_IMAGE} image not available "
    "(set SANDBOX_RUNNER_IMAGE to the tag this host built)"
)


def docker_and_image_available() -> bool:
    try:
        import docker

        docker.from_env().images.get(SANDBOX_RUNNER_IMAGE)
        return True
    except Exception:
        return False
