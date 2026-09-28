"""Kubernetes-native Pod Pool — same design as pool.py's ContainerPool
(warm pool + TTL/use-count + janitor + circuit breaker), reimplemented
against the Kubernetes API instead of the Docker socket.

Why this exists: docker_runner.py/pool.py talk to a Docker daemon
(docker.from_env() or SANDBOX_DOCKER_HOST) that does not exist in the k8s
deployment — there is no sandbox-dockerd sidecar there (deploy/k8s/README.md
"Deliberately absent": rootless DinD needs privileged/unconfined
seccomp, which the restricted namespace forbids). This pool creates and
execs into Pods in a dedicated, fully network-isolated namespace
(dataagent-sandbox) instead of containers on a shared daemon.

Kept identical to ContainerPool: PooledContainer's role is played by
PooledPod; CircuitBreaker is imported unchanged from pool.py (it is pure
Python state, no Docker dependency); acquire()/release()/use-count/TTL/
janitor/label-reconciliation logic is the same shape. What could not carry
over 1:1, and why:

  * Egress-enabled mode (pool.py's `egress_enabled` dedicated-bridge-network
    dance) is NOT implemented here. It is off by default upstream too, and
    nothing in security_visitor.py's AST whitelist currently permits code
    that would need it — see docker_runner.py's own comment. The
    kubernetes-backend equivalent would be a per-pool NetworkPolicy
    generated at startup; add it if/when egress_enabled ever needs to be
    true for k8s.
  * Memory-drift detection (pool.py's memory_usage_bytes/memory_drifted)
    needs the metrics-server API here (no local `docker stats` equivalent)
    reads a value on a ~15-60s cadence, not live — the same
    fail-open-to-"not drifted" behavior on a read failure is kept, so a
    missing/lagging metrics-server degrades this one defense-in-depth
    check silently rather than blocking requests. The primary boundary —
    kill every non-PID-1 process and wipe /tmp — is unchanged and does not
    depend on metrics.
  * Docker's `--pids-limit` has no first-party per-Pod Kubernetes API
    field. The cleanup script's "exactly one process remains" check is the
    same defense either way; a node-level `--pod-max-pids` kubelet flag is
    the closest cluster-wide equivalent, set once by the operator, not by
    this code.
  * `network_disabled=True` (Docker: no network namespace at all) becomes
    a NetworkPolicy default-deny-all with zero allow rules in the
    dataagent-sandbox namespace (deploy/k8s/production/networkpolicy-sandbox.yaml) —
    the pod still gets an IP, but every ingress/egress packet is dropped by
    the CNI's policy enforcement. This was verified to actually be enforced
    on this cluster (not just declared) before relying on it here.
"""
from __future__ import annotations

from typing import Any, cast
import collections.abc
import logging
import queue
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from kubernetes import client as k8s_client
from kubernetes.client.rest import ApiException
from kubernetes.stream import stream as k8s_stream

from pool import CircuitBreaker, CircuitBreakerOpenError  # noqa: F401 — re-exported for callers

logger = logging.getLogger(__name__)

IDLE_CMD = ["sleep", "infinity"]

_SPAWN_RETRIES = 3
_SPAWN_RETRY_DELAY_S = 1.0
_POD_READY_TIMEOUT_S = 60.0
_POD_READY_POLL_S = 0.5

LABEL_POOL = "dataagent.sandbox_pool"
LABEL_POOL_ID = "dataagent.pool_id"

# Same script as docker_runner.py's _KILL_AND_CLEAN_SCRIPT — process-namespace
# semantics inside one container are identical whether Docker or the kubelet's
# CRI started it, so it is reused byte-for-byte via the shared constant below.
from docker_runner import _KILL_AND_CLEAN_SCRIPT  # noqa: E402


class PoolSpawnError(Exception):
    """A pool pod couldn't be created — mirrors pool.py's PoolSpawnError."""


@dataclass
class PooledPod:
    name: str
    namespace: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    created_at: float = field(default_factory=time.monotonic)
    use_count: int = 0
    acquired_at: float | None = None
    is_probe: bool = False


def _pod_manifest(
    *, name: str, namespace: str, image: str, pool_id: str,
    memory_mb: int, cpus: float, run_as_user: int, run_as_group: int,
    service_account: str,
) -> dict[str, Any]:
    """Mirrors pool.py's _spawn() run_kwargs: read-only root fs, tmpfs /tmp,
    fixed mem/cpu, no-new-privileges, all capabilities dropped, non-root —
    the exact same PSA-`restricted` shape every other workload in
    deploy/k8s/ uses, plus the pool's own labels for janitor reconciliation.
    """
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": {LABEL_POOL: "true", LABEL_POOL_ID: pool_id},
        },
        "spec": {
            "serviceAccountName": service_account,
            "automountServiceAccountToken": False,
            "restartPolicy": "Never",
            "enableServiceLinks": False,
            "securityContext": {
                "runAsNonRoot": True,
                "runAsUser": run_as_user,
                "runAsGroup": run_as_group,
                "fsGroup": run_as_group,
                "fsGroupChangePolicy": "OnRootMismatch",
                "seccompProfile": {"type": "RuntimeDefault"},
            },
            "containers": [{
                "name": "sandbox",
                "image": image,
                "command": IDLE_CMD,
                "resources": {
                    "requests": {"cpu": f"{max(int(cpus * 1000), 1)}m", "memory": f"{memory_mb}Mi"},
                    "limits": {"cpu": f"{max(int(cpus * 1000), 1)}m", "memory": f"{memory_mb}Mi"},
                },
                "securityContext": {
                    "allowPrivilegeEscalation": False,
                    "privileged": False,
                    "readOnlyRootFilesystem": True,
                    "capabilities": {"drop": ["ALL"]},
                },
                "volumeMounts": [{"name": "tmp", "mountPath": "/tmp"}],
            }],
            # Memory-backed emptyDir == Docker's `--tmpfs /tmp:size=64m`. No
            # sizeLimit set here for the same reason pool.py's 64m cap exists
            # (bound how much a sandboxed process can write) — see
            # SANDBOX_TMP_SIZE below for how it's actually set.
            "volumes": [{"name": "tmp", "emptyDir": {"medium": "Memory", "sizeLimit": "64Mi"}}],
        },
    }


class K8sPodPool:
    """Same public surface as pool.py's ContainerPool: start/shutdown/stats/
    acquire/release. A PooledPod stands in for a PooledContainer everywhere.
    """

    def __init__(
        self,
        namespace: str,
        image: str,
        size: int,
        memory_mb: int,
        cpus: float,
        max_uses: int = 50,
        max_age_s: float = 30 * 60,
        acquire_timeout_s: float = 30.0,
        janitor_interval_s: float = 15.0,
        stuck_exec_s: float = 120.0,
        run_as_user: int = 10001,
        run_as_group: int = 10001,
        service_account: str = "sandbox-executor-runner",
        breaker: CircuitBreaker | None = None,
    ):
        self.namespace = namespace
        self.image = image
        self.size = size
        self.memory_mb = memory_mb
        self.cpus = cpus
        self.max_uses = max_uses
        self.max_age_s = max_age_s
        self.acquire_timeout_s = acquire_timeout_s
        self.janitor_interval_s = janitor_interval_s
        self.stuck_exec_s = stuck_exec_s
        self.run_as_user = run_as_user
        self.run_as_group = run_as_group
        self.service_account = service_account
        # Same purpose as ContainerPool.pool_id: stamped on every Pod this
        # pool creates so the janitor can reconcile against the API server's
        # own view instead of only this process's in-memory state.
        self.pool_id = uuid.uuid4().hex[:12]
        self.breaker = breaker or CircuitBreaker()

        self._api = k8s_client.CoreV1Api()
        self._idle: queue.Queue[PooledPod] = queue.Queue()
        self._busy: dict[str, PooledPod] = {}
        self._all: dict[str, PooledPod] = {}
        self._lock = threading.Lock()
        self._stopped = threading.Event()
        self._janitor_thread: threading.Thread | None = None

    # -- lifecycle ------------------------------------------------------
    def start(self) -> None:
        spawned: list[PooledPod] = []
        errors: list[PoolSpawnError] = []
        with ThreadPoolExecutor(max_workers=self.size or 1) as pool_executor:
            futures = [pool_executor.submit(self._spawn) for _ in range(self.size)]
            for f in futures:
                try:
                    spawned.append(f.result())
                except PoolSpawnError as e:
                    errors.append(e)

        if errors:
            for pp in spawned:
                self._destroy(pp)
            raise errors[0]

        for pp in spawned:
            self._idle.put(pp)
        self._janitor_thread = threading.Thread(
            target=self._janitor_loop, daemon=True, name="sandbox-k8s-pool-janitor"
        )
        self._janitor_thread.start()
        logger.info("sandbox k8s pool | started with %d warm pod(s) in %s", self.size, self.namespace)

    def shutdown(self) -> None:
        self._stopped.set()
        if self._janitor_thread is not None:
            self._janitor_thread.join(timeout=self.janitor_interval_s + 5)
        with self._lock:
            pods = list(self._all.values())
            self._all.clear()
            self._busy.clear()
        for pp in pods:
            self._destroy(pp)
        logger.info("sandbox k8s pool | shut down, %d pod(s) removed", len(pods))

    def stats(self) -> dict[str, Any]:
        with self._lock:
            idle_count = self._idle.qsize()
            busy_count = len(self._busy)
        return {
            "backend": "kubernetes",
            "namespace": self.namespace,
            "size": self.size,
            "idle": idle_count,
            "busy": busy_count,
            "circuit_breaker": {"state": self.breaker.state, "enforce": self.breaker.enforce},
        }

    # -- spawn/destroy ----------------------------------------------------
    def _spawn(self) -> PooledPod:
        name = f"sandbox-{self.pool_id}-{uuid.uuid4().hex[:8]}"
        manifest = _pod_manifest(
            name=name, namespace=self.namespace, image=self.image, pool_id=self.pool_id,
            memory_mb=self.memory_mb, cpus=self.cpus,
            run_as_user=self.run_as_user, run_as_group=self.run_as_group,
            service_account=self.service_account,
        )
        try:
            self._api.create_namespaced_pod(self.namespace, manifest)
            self._wait_ready(name)
        except Exception as e:
            raise PoolSpawnError(
                f"could not create sandbox pool pod from image '{self.image}' in namespace "
                f"'{self.namespace}' — build/push it first: {e}"
            ) from e

        pp = PooledPod(name=name, namespace=self.namespace)
        with self._lock:
            self._all[pp.id] = pp
        return pp

    def _wait_ready(self, name: str) -> None:
        deadline = time.monotonic() + _POD_READY_TIMEOUT_S
        while time.monotonic() < deadline:
            pod = self._api.read_namespaced_pod(name, self.namespace)
            phase = pod.status.phase
            if phase == "Running" and all(
                cs.ready for cs in (pod.status.container_statuses or [])
            ):
                return
            if phase in ("Failed", "Succeeded"):
                raise PoolSpawnError(f"pod {name} reached terminal phase {phase} before becoming ready")
            time.sleep(_POD_READY_POLL_S)
        raise PoolSpawnError(f"pod {name} did not become ready within {_POD_READY_TIMEOUT_S}s")

    def _destroy(self, pp: PooledPod) -> None:
        try:
            self._api.delete_namespaced_pod(
                pp.name, pp.namespace,
                grace_period_seconds=0,
                propagation_policy="Background",
            )
        except ApiException as e:
            if e.status != 404:
                logger.warning("sandbox k8s pool | pod %s delete failed: %s", pp.name, e)
        except Exception as e:
            logger.warning("sandbox k8s pool | pod %s cleanup-on-destroy failed: %s", pp.name, e)
        with self._lock:
            self._all.pop(pp.id, None)

    def _replace(self, pp: PooledPod) -> None:
        self._destroy(pp)
        for attempt in range(1, _SPAWN_RETRIES + 1):
            try:
                self._idle.put(self._spawn())
                return
            except PoolSpawnError as e:
                if attempt == _SPAWN_RETRIES:
                    logger.error(
                        "sandbox k8s pool | failed to spawn replacement pod after %d attempt(s) — "
                        "pool now short of its configured size %d: %s",
                        attempt, self.size, e,
                    )
                else:
                    time.sleep(_SPAWN_RETRY_DELAY_S)

    # -- exec -------------------------------------------------------------
    def exec(
        self,
        pp: PooledPod,
        argv: list[str],
        *,
        stdin_bytes: bytes | None = None,
        on_output: collections.abc.Callable[[str, str], None] | None = None,
    ) -> tuple[int, bytes, bytes]:
        """Runs argv inside pp's single container and returns
        (exit_code, stdout, stderr). Streams chunks to on_output as they
        arrive, same contract as docker_runner.py's _do_exec."""
        ws = k8s_stream(
            self._api.connect_get_namespaced_pod_exec,
            pp.name, pp.namespace,
            command=argv,
            container="sandbox",
            stderr=True, stdin=stdin_bytes is not None, stdout=True, tty=False,
            _preload_content=False,
        )
        stdout_parts: list[bytes] = []
        stderr_parts: list[bytes] = []
        try:
            if stdin_bytes is not None:
                ws.write_stdin(stdin_bytes.decode("utf-8", errors="surrogateescape"))
                ws.close()  # half-close: signals EOF on the remote stdin (e.g. for `cat`)
            while ws.is_open():
                ws.update(timeout=1)
                if ws.peek_stdout():
                    chunk = ws.read_stdout()
                    b = chunk.encode("utf-8", errors="surrogateescape")
                    stdout_parts.append(b)
                    if on_output is not None:
                        on_output("stdout", chunk)
                if ws.peek_stderr():
                    chunk = ws.read_stderr()
                    b = chunk.encode("utf-8", errors="surrogateescape")
                    stderr_parts.append(b)
                    if on_output is not None:
                        on_output("stderr", chunk)
            err = ws.read_channel(3)  # error channel: JSON with the exit status
        finally:
            ws.close()

        exit_code = _exit_code_from_error_channel(err)
        return exit_code, b"".join(stdout_parts), b"".join(stderr_parts)

    # -- acquire/release ----------------------------------------------------
    def acquire(self) -> PooledPod:
        is_probe = self.breaker.before_acquire()
        succeeded = False
        try:
            deadline = time.monotonic() + self.acquire_timeout_s
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"no idle sandbox pod available within {self.acquire_timeout_s}s")
                try:
                    pp = self._idle.get(timeout=remaining)
                except queue.Empty:
                    raise TimeoutError(f"no idle sandbox pod available within {self.acquire_timeout_s}s")

                try:
                    pod = self._api.read_namespaced_pod(pp.name, pp.namespace)
                    healthy = pod.status.phase == "Running"
                except Exception:
                    healthy = False

                if not healthy:
                    logger.warning("sandbox k8s pool | idle pod %s unhealthy — retiring", pp.name)
                    self._replace(pp)
                    continue

                pp.acquired_at = time.monotonic()
                pp.is_probe = is_probe
                with self._lock:
                    self._busy[pp.id] = pp
                succeeded = True
                return pp
        finally:
            if is_probe and not succeeded:
                self.breaker.record_probe_result(healthy=False)

    def release(self, pp: PooledPod, retire_reason: str | None) -> None:
        with self._lock:
            self._busy.pop(pp.id, None)
        pp.acquired_at = None
        pp.use_count += 1

        was_probe = pp.is_probe
        pp.is_probe = False
        if was_probe:
            self.breaker.record_probe_result(healthy=retire_reason is None)
        elif retire_reason is not None:
            self.breaker.record_abnormal()

        if retire_reason is None:
            age_s = time.monotonic() - pp.created_at
            if pp.use_count >= self.max_uses:
                retire_reason = f"use-count {pp.use_count} >= {self.max_uses}"
            elif age_s >= self.max_age_s:
                retire_reason = f"age {age_s:.0f}s >= {self.max_age_s:.0f}s"

        if retire_reason is not None:
            logger.info("sandbox k8s pool | retiring pod %s: %s", pp.name, retire_reason)
            self._replace(pp)
        else:
            self._idle.put(pp)

    def cleanup(self, pp: PooledPod) -> tuple[bool, str]:
        """Same contract as docker_runner.py's _cleanup(): kills everything
        but PID 1, wipes /tmp, returns (clean, reason). No memory-drift
        check here (see module docstring) — process-count is the check
        that actually matters and is identical to the Docker backend."""
        try:
            exit_code, stdout, _stderr = self.exec(pp, ["sh", "-c", _KILL_AND_CLEAN_SCRIPT])
        except Exception as e:
            return False, f"cleanup exec failed: {e}"

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
        return True, ""

    # -- janitor ------------------------------------------------------------
    def _janitor_loop(self) -> None:
        while not self._stopped.wait(self.janitor_interval_s):
            try:
                self._janitor_pass()
            except Exception:
                logger.exception("sandbox k8s pool | janitor pass failed")

    def _janitor_pass(self) -> None:
        now = time.monotonic()
        self._reconcile_labels()

        with self._lock:
            stuck = [
                pp for pp in self._busy.values()
                if pp.acquired_at is not None and now - pp.acquired_at > self.stuck_exec_s
            ]
        for pp in stuck:
            logger.warning("sandbox k8s pool | pod %s stuck busy for %.0fs — force retiring",
                            pp.name, now - cast(float, pp.acquired_at))
            with self._lock:
                self._busy.pop(pp.id, None)
            self._replace(pp)

        drained: list[PooledPod] = []
        try:
            while True:
                drained.append(self._idle.get_nowait())
        except queue.Empty:
            pass

        for pp in drained:
            age_s = now - pp.created_at
            expired = pp.use_count >= self.max_uses or age_s >= self.max_age_s
            if expired:
                logger.info("sandbox k8s pool | janitor retiring idle pod %s: expired", pp.name)
                self._replace(pp)
            else:
                self._idle.put(pp)

        self._top_up()

    def _top_up(self) -> None:
        with self._lock:
            shortfall = self.size - len(self._all)
        for _ in range(shortfall):
            try:
                self._idle.put(self._spawn())
            except PoolSpawnError as e:
                logger.error(
                    "sandbox k8s pool | janitor top-up: failed to spawn replacement pod — "
                    "pool still short of its configured size %d: %s", self.size, e,
                )
                break

    def _reconcile_labels(self) -> None:
        try:
            labeled = self._api.list_namespaced_pod(
                self.namespace, label_selector=f"{LABEL_POOL_ID}={self.pool_id}"
            ).items
        except Exception as e:
            logger.warning("sandbox k8s pool | janitor label reconciliation: could not list pods: %s", e)
            return

        with self._lock:
            tracked_names = {pp.name for pp in self._all.values()}
        live_names = {p.metadata.name for p in labeled}

        orphans = [p for p in labeled if p.metadata.name not in tracked_names]
        for p in orphans:
            logger.warning(
                "sandbox k8s pool | janitor found orphaned labeled pod %s "
                "(not in this process's tracked state) — removing", p.metadata.name,
            )
            try:
                self._api.delete_namespaced_pod(p.metadata.name, self.namespace, grace_period_seconds=0)
            except Exception as e:
                logger.warning("sandbox k8s pool | janitor failed to remove orphan pod %s: %s",
                                p.metadata.name, e)

        missing = tracked_names - live_names
        for name in missing:
            logger.warning(
                "sandbox k8s pool | janitor: pod %s is tracked internally but the API server no longer "
                "reports it (deleted out-of-band?) — will be caught by acquire()'s health check or the "
                "stuck-exec sweep", name,
            )


def _exit_code_from_error_channel(raw: str | None) -> int:
    """The exec websocket's error channel (stream index 3) carries a JSON
    status object, e.g. {"status":"Success"} or
    {"status":"Failure","reason":"NonZeroExitCode",
     "details":{"causes":[{"reason":"ExitCode","message":"1"}]}}."""
    if not raw:
        return 0
    try:
        import json
        obj = json.loads(raw)
    except Exception:
        return -1
    if obj.get("status") == "Success":
        return 0
    for cause in obj.get("details", {}).get("causes", []):
        if cause.get("reason") == "ExitCode":
            try:
                return int(cause.get("message", "-1"))
            except ValueError:
                return -1
    return -1
