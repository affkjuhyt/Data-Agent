"""Container Pool Manager (docs/1.md "Kế hoạch 1: Sandbox Executor —
Container Pool + TTL").

A fixed-size set of "warm" sandbox containers, each started once with the
same hard security/resource flags the old ephemeral model applied per
request (network_disabled, read_only, mem/cpu/pids limits,
no-new-privileges) — those flags are container-creation-time only and
never change across a container's reuse. Callers exec into an already-
running container instead of paying containers.run()'s create+start cost
on every request.

Reuse means the kernel no longer destroys the whole namespace after every
call, so this module bounds exposure a different way: use-count and
wall-clock TTL cap how long any one container lives, and any dirty
post-cleanup state (leftover processes, memory that didn't return near
idle, a cleanup step that itself errored, or a timeout) retires the
container immediately (destroy + spawn a replacement) instead of
returning it to the pool. A background janitor thread re-checks the pool
independently of the request path, for containers that get stuck holding
an exec too long or that should have been retired but weren't.
"""
from __future__ import annotations

import collections
import enum
import logging
import queue
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import docker.errors
import docker.types

logger = logging.getLogger(__name__)

# docs/1.md Kế hoạch 1 mục a: used by ContainerPool._verify_egress_isolation()
# as an automatic startup gate. Two DIFFERENT targets, for two different
# failure modes:
#   - 169.254.169.254: the doc's own canonical "must always be blocked"
#     example (cloud metadata/SSRF). But on a bare Docker bridge network
#     with no real cloud metadata service listening there, this address
#     is simply *unroutable* regardless of whether any iptables rule
#     exists at all — confirmed empirically (docker network create +
#     connect to it fails with no DOCKER-USER rules present whatsoever).
#     Testing ONLY this address would report "isolation verified" even on
#     a network with zero egress restriction, which is worse than not
#     testing at all (false confidence).
#   - 1.1.1.1:443: an ordinary public host that is NEVER a legitimate
#     PrivateLink CIDR entry. A fresh Docker bridge network NATs to the
#     real internet by default — this address IS reachable there unless a
#     default-deny DOCKER-USER rule is actually in effect. This is the
#     target that actually proves (or disproves) default-deny.
# Both must be unreachable for the probe to pass.
_EGRESS_PROBE_TARGETS = (("169.254.169.254", 80), ("1.1.1.1", 443))
_EGRESS_PROBE_CODE = (
    "import socket, sys\n"
    "targets = " + repr(_EGRESS_PROBE_TARGETS) + "\n"
    "for host, port in targets:\n"
    "    try:\n"
    "        socket.create_connection((host, port), timeout=2)\n"
    "    except OSError:\n"
    "        continue\n"
    "    else:\n"
    "        sys.exit(1)\n"  # reachable — iptables allow-list isn't actually applied
    "sys.exit(0)\n"  # every probe target correctly unreachable
)

IDLE_CMD = ["sleep", "infinity"]

_SPAWN_RETRIES = 3
_SPAWN_RETRY_DELAY_S = 1.0

# docs/1.md Kế hoạch 1 mục (a)/(f): fixed labels set once at container
# creation so the Janitor can reconcile against Docker's own view of the
# world ("container thực sự đang chạy mang nhãn pool") instead of only
# trusting this process's in-memory state, and so a human/tool on the host
# can tell at a glance which containers belong to the sandbox pool.
LABEL_POOL = "dataagent.sandbox_pool"
LABEL_POOL_ID = "dataagent.pool_id"
LABEL_CREATED_AT = "dataagent.created_at"


class PoolSpawnError(Exception):
    """A pool container couldn't be created — usually a missing/bad image
    or an unreachable Docker daemon. Distinct from APIError so callers
    (main.py's lifespan) get one clear, actionable message instead of a
    raw docker exception."""


class CircuitBreakerOpenError(Exception):
    """Raised by acquire() when the pool's circuit breaker (docs/1.md
    Kế hoạch 1 mục e) is OPEN — the caller must reject the request
    immediately (503/backpressure) without ever touching the idle queue."""


def memory_usage_bytes(container) -> int | None:
    try:
        stats = container.stats(stream=False)
        return stats.get("memory_stats", {}).get("usage")
    except Exception:
        # Broad on purpose — a stats-read failure (docker connection blip,
        # container gone) should degrade to "unknown," never crash the
        # caller (spawn-time baseline capture, or a cleanup verify check).
        return None


def memory_drifted(container, baseline_bytes: int, factor: float) -> bool:
    """True if current memory usage is more than `factor`x the idle
    baseline recorded right after the container was created — a proxy for
    "cleanup didn't actually bring this container back to idle"."""
    if baseline_bytes <= 0:
        return False
    usage = memory_usage_bytes(container)
    if usage is None:
        return False
    return usage > baseline_bytes * factor


@dataclass
class PooledContainer:
    container: object
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    created_at: float = field(default_factory=time.monotonic)
    use_count: int = 0
    idle_mem_bytes: int = 0
    acquired_at: float | None = None
    # Set by acquire() when the circuit breaker admitted this checkout as a
    # HALF_OPEN probe; release() reads (and clears) it to report the
    # outcome back to the breaker instead of counting it as an ordinary
    # abnormal-retirement event.
    is_probe: bool = False


class _BreakerState(enum.Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """docs/1.md Kế hoạch 1 mục (e), "Circuit breaker ở tầng pool".

    Counts ONLY the abnormal retirement reasons the doc lists as (3)(4)(5)
    — verify-not-clean, cleanup error, timeout — in a sliding window,
    deliberately excluding (1)(2) use-count/TTL churn (that's expected
    turnover, not a signal anything is wrong). Enough abnormal retirements
    in a short window trips OPEN, which rejects new acquire() calls
    immediately (before they'd ever touch the idle queue) instead of
    piling more load onto a pool that's already failing. After a cooldown
    it allows a small number of HALF_OPEN probe requests through; a clean
    release closes it again, an abnormal one reopens with a longer
    (backed-off) cooldown.

    Deliberately does not gate the Janitor (pool.py's `_janitor_pass`) —
    the doc is explicit that background reconciliation must keep running
    independent of request-path breaker state.
    """

    def __init__(
        self,
        window_s: float = 60.0,
        threshold: int = 5,
        cooldown_s: float = 30.0,
        max_cooldown_s: float = 300.0,
        half_open_trials: int = 1,
        enforce: bool = False,
    ):
        self.window_s = window_s
        self.threshold = threshold
        self.cooldown_s = cooldown_s
        self.max_cooldown_s = max_cooldown_s
        self.half_open_trials = half_open_trials
        # docs/1.md mục e: "X/Y/cooldown cần tune theo baseline vận hành
        # thực tế (nên chạy log-only một thời gian trước khi bật reject)".
        # Defaults to False on purpose — the threshold/window/cooldown
        # values above are placeholders, not a tuned baseline, and an
        # untuned breaker that actually rejects traffic can itself cause
        # an outage (false-positive on a benign load spike). The state
        # machine still runs and logs every transition either way — only
        # whether before_acquire() actually raises is gated by this flag.
        self.enforce = enforce

        self._lock = threading.Lock()
        self._events: collections.deque[float] = collections.deque()
        self._state = _BreakerState.CLOSED
        self._opened_at: float | None = None
        self._current_cooldown_s = cooldown_s
        self._half_open_inflight = 0

    def _prune(self, now: float) -> None:
        while self._events and now - self._events[0] > self.window_s:
            self._events.popleft()

    def before_acquire(self) -> bool:
        """Returns True if this call is admitted as a HALF_OPEN probe (the
        caller's eventual release() must report it via
        record_probe_result()). Raises CircuitBreakerOpenError if the
        breaker is OPEN, cooldown hasn't elapsed yet, and `enforce` is
        True; in log-only mode (enforce=False) this never raises and
        never admits a probe — it only logs what it would have done, so
        the state machine (and its logging) can be observed against real
        traffic before anything is actually rejected."""
        now = time.monotonic()
        with self._lock:
            if self._state == _BreakerState.CLOSED:
                return False

            if not self.enforce:
                if self._state == _BreakerState.OPEN and (
                    self._opened_at is None or now - self._opened_at < self._current_cooldown_s
                ):
                    logger.warning(
                        "sandbox pool | circuit breaker WOULD reject this request (OPEN, log-only mode — "
                        "set SANDBOX_BREAKER_ENFORCE=true once thresholds are tuned to actually enforce)"
                    )
                return False

            if self._state == _BreakerState.OPEN:
                if self._opened_at is not None and now - self._opened_at >= self._current_cooldown_s:
                    self._state = _BreakerState.HALF_OPEN
                    self._half_open_inflight = 0
                    logger.warning("sandbox pool | circuit breaker OPEN -> HALF_OPEN (cooldown elapsed, probing)")
                else:
                    raise CircuitBreakerOpenError(
                        "sandbox pool circuit breaker is open — rejecting new work until cooldown elapses"
                    )

            # HALF_OPEN: admit a bounded number of concurrent probes.
            if self._half_open_inflight >= self.half_open_trials:
                raise CircuitBreakerOpenError(
                    "sandbox pool circuit breaker is half-open — a probe request is already in flight"
                )
            self._half_open_inflight += 1
            return True

    def record_abnormal(self) -> None:
        """Feed one abnormal-retirement event (doc's conditions 3/4/5) into
        the sliding window; trips the breaker to OPEN if it crosses
        `threshold` events within `window_s`."""
        now = time.monotonic()
        with self._lock:
            self._events.append(now)
            self._prune(now)
            if self._state != _BreakerState.OPEN and len(self._events) >= self.threshold:
                self._state = _BreakerState.OPEN
                self._opened_at = now
                logger.error(
                    "sandbox pool | circuit breaker CLOSED -> OPEN: %d abnormal retirement(s) in the last %.0fs "
                    "(threshold %d) — rejecting new requests for %.0fs",
                    len(self._events), self.window_s, self.threshold, self._current_cooldown_s,
                )

    def record_probe_result(self, healthy: bool) -> None:
        """Called when a HALF_OPEN-admitted request finishes. A healthy
        result closes the breaker and resets backoff; an unhealthy one
        reopens it with a longer cooldown (capped)."""
        with self._lock:
            self._half_open_inflight = max(0, self._half_open_inflight - 1)
            if self._state != _BreakerState.HALF_OPEN:
                return
            if healthy:
                self._state = _BreakerState.CLOSED
                self._events.clear()
                self._current_cooldown_s = self.cooldown_s
                logger.warning("sandbox pool | circuit breaker HALF_OPEN -> CLOSED (probe succeeded)")
            else:
                self._state = _BreakerState.OPEN
                self._opened_at = time.monotonic()
                self._current_cooldown_s = min(self._current_cooldown_s * 2, self.max_cooldown_s)
                logger.error(
                    "sandbox pool | circuit breaker HALF_OPEN -> OPEN (probe failed) — next cooldown %.0fs",
                    self._current_cooldown_s,
                )

    @property
    def is_open(self) -> bool:
        with self._lock:
            return self._state == _BreakerState.OPEN

    @property
    def state(self) -> str:
        """"closed"/"open"/"half_open" — for an ops dashboard; `is_open`
        alone can't distinguish OPEN from HALF_OPEN."""
        with self._lock:
            return self._state.value


class ContainerPool:
    def __init__(
        self,
        client,
        image: str,
        size: int,
        memory_mb: int,
        cpus: float,
        pids_limit: int,
        max_uses: int = 50,
        max_age_s: float = 30 * 60,
        acquire_timeout_s: float = 30.0,
        janitor_interval_s: float = 15.0,
        stuck_exec_s: float = 120.0,
        mem_drift_factor: float = 6.0,
        runtime: str | None = None,
        breaker: CircuitBreaker | None = None,
        egress_enabled: bool = False,
        egress_network: str = "sandbox-egress",
        egress_subnet: str = "172.28.0.0/24",
    ):
        self._client = client
        self.image = image
        self.size = size
        self.memory_mb = memory_mb
        self.cpus = cpus
        self.pids_limit = pids_limit
        self.max_uses = max_uses
        self.max_age_s = max_age_s
        self.acquire_timeout_s = acquire_timeout_s
        self.janitor_interval_s = janitor_interval_s
        self.stuck_exec_s = stuck_exec_s
        self.mem_drift_factor = mem_drift_factor
        # docs/1.md Kế hoạch 1 mục (a): dedicated bridge network (never the
        # default Docker/compose bridge) instead of network_disabled=True —
        # OFF by default. Nothing in this codebase's AST whitelist
        # (security_visitor.py) currently permits importing a DB driver or
        # raw socket/urllib/requests inside agent code, so there is no
        # existing feature that needs this; it exists for when one does.
        # When turned on, start() verifies isolation before trusting it
        # (see _verify_egress_isolation) rather than assuming the operator
        # already ran ops/setup_sandbox_egress_iptables.sh correctly.
        self.egress_enabled = egress_enabled
        self.egress_network = egress_network
        self.egress_subnet = egress_subnet
        # docs/1.md Kế hoạch 1 mục (g): gVisor (`runsc`) instead of the
        # default runtime, because the pool model keeps containers alive
        # far longer than the old ephemeral-per-request model did. None
        # (the default) keeps today's runtime unchanged — operators opt in
        # via SANDBOX_RUNTIME once `runsc` is installed and registered with
        # the Docker daemon (see docs/1.md mục g for the daemon.json step
        # and the required syscall-compatibility test pass).
        self.runtime = runtime
        # docs/1.md Kế hoạch 1 mục (a): stable per-pool-instance id, stamped
        # onto every container this pool spawns as the `dataagent.pool_id`
        # label so the Janitor can reconcile against Docker's own view
        # instead of only this process's in-memory state.
        self.pool_id = uuid.uuid4().hex[:12]
        self.breaker = breaker or CircuitBreaker()

        self._idle: queue.Queue[PooledContainer] = queue.Queue()
        self._busy: dict[str, PooledContainer] = {}
        self._all: dict[str, PooledContainer] = {}
        self._lock = threading.Lock()
        self._stopped = threading.Event()
        self._janitor_thread: threading.Thread | None = None

    # -- lifecycle ------------------------------------------------------
    def start(self) -> None:
        if self.egress_enabled:
            self._ensure_egress_network()

        # Independent Docker API calls — spawn concurrently instead of
        # paying N sequential container-create round-trips on every
        # service startup/restart.
        spawned: list[PooledContainer] = []
        errors: list[PoolSpawnError] = []
        with ThreadPoolExecutor(max_workers=self.size or 1) as pool_executor:
            futures = [pool_executor.submit(self._spawn) for _ in range(self.size)]
            for f in futures:
                try:
                    spawned.append(f.result())
                except PoolSpawnError as e:
                    errors.append(e)

        if errors:
            # Fail fast and clean — a pool that can't reach its configured
            # size can't safely serve requests. Destroy whatever DID spawn
            # so a startup failure doesn't leak warm containers that
            # nothing will ever clean up (shutdown_pool() never runs if
            # the service never finishes starting).
            for pc in spawned:
                self._destroy(pc)
            raise errors[0]

        for pc in spawned:
            self._idle.put(pc)
        self._janitor_thread = threading.Thread(
            target=self._janitor_loop, daemon=True, name="sandbox-pool-janitor"
        )
        self._janitor_thread.start()
        logger.info("sandbox pool | started with %d warm container(s)", self.size)

        if self.egress_enabled:
            self._verify_egress_isolation()

    def shutdown(self) -> None:
        self._stopped.set()
        if self._janitor_thread is not None:
            self._janitor_thread.join(timeout=self.janitor_interval_s + 5)
        with self._lock:
            containers = list(self._all.values())
            self._all.clear()
            self._busy.clear()
        for pc in containers:
            self._destroy(pc)
        logger.info("sandbox pool | shut down, %d container(s) removed", len(containers))

    def stats(self) -> dict:
        """Snapshot for an ops dashboard/health check — not used on the
        request path, so an approximate read (no cross-field locking
        beyond each individual access) is fine here."""
        with self._lock:
            idle_count = self._idle.qsize()
            busy_count = len(self._busy)
        return {
            "size": self.size,
            "idle": idle_count,
            "busy": busy_count,
            "egress_enabled": self.egress_enabled,
            "runtime": self.runtime or "default",
            "circuit_breaker": {
                "state": self.breaker.state,
                "enforce": self.breaker.enforce,
            },
        }

    # -- egress network (docs/1.md mục a) --------------------------------

    def _ensure_egress_network(self) -> None:
        """Dedicated bridge network for the pool — never the default
        Docker/compose bridge — with inter-container communication
        disabled (enable_icc=false) so one compromised pool container
        can't pivot straight to another tenant's container sitting idle
        in the same pool. Idempotent: reuses the network if it already
        exists (e.g. a previous service run created it and it survived a
        restart)."""
        try:
            self._client.networks.get(self.egress_network)
            logger.info("sandbox pool | reusing existing egress network %s", self.egress_network)
            return
        except docker.errors.NotFound:
            pass
        except Exception as e:
            logger.error(
                "sandbox pool | could not check for existing egress network %s — falling back to no "
                "network for this run: %s", self.egress_network, e,
            )
            self.egress_enabled = False
            return

        try:
            ipam = docker.types.IPAMConfig(
                pool_configs=[docker.types.IPAMPool(subnet=self.egress_subnet)]
            )
            self._client.networks.create(
                self.egress_network,
                driver="bridge",
                ipam=ipam,
                options={"com.docker.network.bridge.enable_icc": "false"},
            )
            logger.info(
                "sandbox pool | created egress network %s (subnet %s, icc=false)",
                self.egress_network, self.egress_subnet,
            )
        except Exception as e:
            logger.error(
                "sandbox pool | failed to create egress network %s — falling back to no network "
                "for this run: %s", self.egress_network, e,
            )
            self.egress_enabled = False

    def _verify_egress_isolation(self) -> None:
        """docs/1.md mục a: "Trước khi rollout: test rule iptables trực
        tiếp trong 1 container của pool (...chặn mọi đích khác kể cả
        169.254.169.254...)" — implemented as an automatic startup gate
        rather than a manual pre-rollout step. ops/setup_sandbox_egress_
        iptables.sh must be run on whatever Docker engine actually backs
        this pool for the DOCKER-USER rules to exist; this method never
        assumes that happened. If the cloud metadata endpoint is
        reachable from a pool container, the allow-list isn't actually in
        effect — trust nothing and fall every container in the pool back
        to no network at all rather than run with silently-unrestricted
        egress."""
        try:
            pc = self.acquire()
        except (TimeoutError, CircuitBreakerOpenError) as e:
            logger.error(
                "sandbox pool | egress isolation probe: could not acquire a container to test (%s) — "
                "falling back to no network for every container", e,
            )
            self.egress_enabled = False
            self._respawn_all()
            return

        blocked = False
        try:
            exit_code, _ = pc.container.exec_run(["python3", "-c", _EGRESS_PROBE_CODE])
            blocked = exit_code == 0
        except Exception as e:
            logger.error("sandbox pool | egress isolation probe exec failed: %s", e)
        finally:
            self.release(pc, None)

        if blocked:
            logger.info(
                "sandbox pool | egress isolation verified: %s all unreachable from pool containers, as required",
                _EGRESS_PROBE_TARGETS,
            )
            return

        logger.error(
            "sandbox pool | EGRESS ISOLATION PROBE FAILED — at least one of %s was reachable from a pool "
            "container. The DOCKER-USER iptables allow-list (ops/setup_sandbox_egress_iptables.sh) has not "
            "been applied on the Docker engine backing this pool. Falling back to no network at all "
            "(network_disabled=True) for every container until this is fixed and the service restarted.",
            _EGRESS_PROBE_TARGETS,
        )
        self.egress_enabled = False
        self._respawn_all()

    def _respawn_all(self) -> None:
        """Destroys every currently tracked container and spawns `size`
        fresh ones under _spawn()'s CURRENT network branch (egress_enabled
        may have just been flipped False by _verify_egress_isolation).
        Only called at startup, before the pool is serving any request —
        there is nothing in `_busy` yet at that point."""
        with self._lock:
            stale = list(self._all.values())
            self._all.clear()
            self._busy.clear()
        try:
            while True:
                self._idle.get_nowait()
        except queue.Empty:
            pass
        for pc in stale:
            self._destroy(pc)
        for _ in range(self.size):
            self._idle.put(self._spawn())
        logger.info("sandbox pool | respawned %d container(s) without egress network", self.size)

    def _spawn(self) -> PooledContainer:
        """Raises PoolSpawnError (never a raw docker exception) so callers
        get one clear, actionable failure instead of main.py's lifespan
        crashing on an opaque ImageNotFound/APIError traceback."""
        try:
            run_kwargs = dict(
                detach=True,
                mem_limit=f"{self.memory_mb}m",    # --memory
                nano_cpus=int(self.cpus * 1e9),    # --cpus
                read_only=True,                # --read-only
                tmpfs={"/tmp": "size=64m"},    # --tmpfs /tmp:size=64m
                pids_limit=self.pids_limit,    # --pids-limit
                security_opt=["no-new-privileges"],
                labels={
                    LABEL_POOL: "true",
                    LABEL_POOL_ID: self.pool_id,
                    LABEL_CREATED_AT: str(int(time.time())),
                },
            )
            # docs/1.md mục a: dedicated bridge network instead of --network
            # none, ONLY when egress_enabled (default False — see __init__).
            # Re-checked on every _spawn() call (not just once at pool
            # construction) so a mid-run fallback from
            # _verify_egress_isolation() correctly produces network_disabled
            # containers for every subsequent replacement too.
            if self.egress_enabled:
                run_kwargs["network"] = self.egress_network
            else:
                run_kwargs["network_disabled"] = True    # --network none
            if self.runtime:
                run_kwargs["runtime"] = self.runtime
            container = self._client.containers.run(self.image, IDLE_CMD, **run_kwargs)
        except Exception as e:
            # Broad on purpose — a Docker connection blip (not just a
            # missing image) must also come back as one clear PoolSpawnError,
            # not an assortment of raw docker/requests exception types.
            raise PoolSpawnError(
                f"could not create sandbox pool container from image '{self.image}' — "
                f"build it first (docker build -t {self.image} services/sandbox-executor/sandbox_runner/): {e}"
            ) from e

        pc = PooledContainer(container=container)
        pc.idle_mem_bytes = memory_usage_bytes(container) or 0
        with self._lock:
            self._all[pc.id] = pc
        return pc

    def _destroy(self, pc: PooledContainer) -> None:
        try:
            pc.container.remove(force=True)
        except Exception as e:
            # Broad on purpose (see acquire()'s reload() comment) — this must
            # never raise past callers like shutdown()'s destroy-all loop or
            # start()'s cleanup-on-partial-failure path.
            logger.warning("sandbox pool | container %s cleanup-on-destroy failed: %s", pc.id, e)
        with self._lock:
            self._all.pop(pc.id, None)

    def _replace(self, pc: PooledContainer) -> None:
        self._destroy(pc)
        for attempt in range(1, _SPAWN_RETRIES + 1):
            try:
                self._idle.put(self._spawn())
                return
            except PoolSpawnError as e:
                if attempt == _SPAWN_RETRIES:
                    logger.error(
                        "sandbox pool | failed to spawn replacement container after %d attempt(s) — "
                        "pool now running %d container(s) short of its configured size %d: %s",
                        attempt, self.size - len(self._all), self.size, e,
                    )
                else:
                    logger.warning(
                        "sandbox pool | spawn attempt %d/%d for replacement container failed, retrying: %s",
                        attempt, _SPAWN_RETRIES, e,
                    )
                    time.sleep(_SPAWN_RETRY_DELAY_S)

    # -- acquire/release --------------------------------------------------
    def acquire(self) -> PooledContainer:
        # Raises CircuitBreakerOpenError immediately — before ever touching
        # the idle queue — when the breaker is OPEN (docs/1.md mục e: "TỪ
        # CHỐI NGAY (503) ... KHÔNG thử acquire()"). Returns True only for
        # the bounded HALF_OPEN probe slot, which release() below must
        # report back via record_probe_result().
        is_probe = self.breaker.before_acquire()
        succeeded = False
        try:
            deadline = time.monotonic() + self.acquire_timeout_s
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"no idle sandbox container available within {self.acquire_timeout_s}s")
                try:
                    pc = self._idle.get(timeout=remaining)
                except queue.Empty:
                    raise TimeoutError(f"no idle sandbox container available within {self.acquire_timeout_s}s")

                try:
                    pc.container.reload()
                    healthy = pc.container.status == "running"
                except Exception:
                    # Broad on purpose: a connection blip (e.g. requests.exceptions
                    # .ConnectionError, which isn't an APIError subclass) must still
                    # route this container to _replace() below, not escape and
                    # orphan it outside both _busy and _idle tracking.
                    healthy = False

                if not healthy:
                    logger.warning("sandbox pool | idle container %s unhealthy — retiring", pc.id)
                    self._replace(pc)
                    continue

                pc.acquired_at = time.monotonic()
                pc.is_probe = is_probe
                with self._lock:
                    self._busy[pc.id] = pc
                succeeded = True
                return pc
        finally:
            # `finally` (not a narrow `except TimeoutError`) so a probe's
            # HALF_OPEN slot is released on *any* exit path that isn't a
            # successful return — not just the expected TimeoutError, but
            # also whatever unexpected exception might escape the loop
            # above (e.g. a bug in _replace()/_spawn()). Otherwise a probe
            # that fails via a path this code didn't anticipate leaks its
            # slot and wedges the breaker in HALF_OPEN forever.
            if is_probe and not succeeded:
                self.breaker.record_probe_result(healthy=False)

    def release(self, pc: PooledContainer, retire_reason: str | None) -> None:
        with self._lock:
            self._busy.pop(pc.id, None)
        pc.acquired_at = None
        pc.use_count += 1

        # Only a caller-supplied reason (timeout / cleanup error / dirty
        # verify — docs/1.md mục e conditions 3-5) counts as "abnormal" for
        # the circuit breaker. A reason pool.py derives itself just below
        # (use-count/TTL) is ordinary churn — conditions (1)(2) — and must
        # NOT feed the breaker.
        was_probe = pc.is_probe
        pc.is_probe = False
        if was_probe:
            self.breaker.record_probe_result(healthy=retire_reason is None)
        elif retire_reason is not None:
            self.breaker.record_abnormal()

        if retire_reason is None:
            age_s = time.monotonic() - pc.created_at
            if pc.use_count >= self.max_uses:
                retire_reason = f"use-count {pc.use_count} >= {self.max_uses}"
            elif age_s >= self.max_age_s:
                retire_reason = f"age {age_s:.0f}s >= {self.max_age_s:.0f}s"

        if retire_reason is not None:
            logger.info("sandbox pool | retiring container %s: %s", pc.id, retire_reason)
            self._replace(pc)
        else:
            self._idle.put(pc)

    # -- janitor ------------------------------------------------------------
    def _janitor_loop(self) -> None:
        while not self._stopped.wait(self.janitor_interval_s):
            try:
                self._janitor_pass()
            except Exception:
                logger.exception("sandbox pool | janitor pass failed")

    def _janitor_pass(self) -> None:
        now = time.monotonic()

        self._reconcile_labels()

        # Containers stuck holding an exec far longer than any legitimate
        # request/timeout should take — the request path may have crashed
        # before it could clean up and release.
        with self._lock:
            stuck = [
                pc for pc in self._busy.values()
                if pc.acquired_at is not None and now - pc.acquired_at > self.stuck_exec_s
            ]
        for pc in stuck:
            logger.warning(
                "sandbox pool | container %s stuck busy for %.0fs — force retiring",
                pc.id, now - pc.acquired_at,
            )
            with self._lock:
                self._busy.pop(pc.id, None)
            self._replace(pc)

        # Idle containers that should already have been retired (TTL/use-count
        # missed by the request path) or whose resource usage has drifted
        # while sitting idle.
        drained: list[PooledContainer] = []
        try:
            while True:
                drained.append(self._idle.get_nowait())
        except queue.Empty:
            pass

        for pc in drained:
            age_s = now - pc.created_at
            expired = pc.use_count >= self.max_uses or age_s >= self.max_age_s
            drifted = not expired and memory_drifted(pc.container, pc.idle_mem_bytes, self.mem_drift_factor)
            if expired or drifted:
                reason = "expired (janitor sweep)" if expired else "memory drift while idle (janitor sweep)"
                logger.info("sandbox pool | janitor retiring idle container %s: %s", pc.id, reason)
                self._replace(pc)
            else:
                self._idle.put(pc)

        self._top_up()

    def _top_up(self) -> None:
        """_replace() gives up after _SPAWN_RETRIES exhausted attempts
        (e.g. a Docker daemon overloaded at that moment) and just logs an
        error, permanently leaving the pool short of self.size with no
        other path back to full strength. Each janitor pass retries the
        shortfall so a transient spawn failure self-heals on the next
        tick instead of quietly shrinking the pool forever."""
        with self._lock:
            shortfall = self.size - len(self._all)
        for _ in range(shortfall):
            try:
                self._idle.put(self._spawn())
            except PoolSpawnError as e:
                logger.error(
                    "sandbox pool | janitor top-up: failed to spawn replacement container — "
                    "pool still running short of its configured size %d: %s",
                    self.size, e,
                )
                break

    def _reconcile_labels(self) -> None:
        """docs/1.md Kế hoạch 1 mục (a)/(f): cross-check this pool's
        internal state against Docker's own view of which containers
        actually carry this pool's labels, instead of only trusting
        in-memory tracking. Catches the case the doc calls out explicitly
        — "container mồ côi còn sót lại sau khi process restart": a
        fresh process starts with empty `_all`/`_idle`/`_busy` but the
        previous process's containers are still running under the old
        label. Reverse drift (tracked here but Docker no longer has it)
        is logged only, not force-replaced from this loop — mutating pool
        state concurrently with acquire()/release() from here is exactly
        the kind of thing the janitor should stay out of; the existing
        acquire()-time reload() health check and the stuck-exec sweep
        above already retire that case safely when it's next touched.
        """
        try:
            labeled = self._client.containers.list(
                all=True, filters={"label": [f"{LABEL_POOL_ID}={self.pool_id}"]}
            )
        except Exception as e:
            logger.warning("sandbox pool | janitor label reconciliation: could not list containers: %s", e)
            return

        with self._lock:
            tracked_docker_ids = {pc.container.id for pc in self._all.values()}

        docker_ids = {c.id for c in labeled}

        orphans = [c for c in labeled if c.id not in tracked_docker_ids]
        for c in orphans:
            logger.warning(
                "sandbox pool | janitor found orphaned labeled container %s (not in this process's "
                "tracked state, likely left over from a previous process lifetime) — removing",
                c.id[:12],
            )
            try:
                c.remove(force=True)
            except Exception as e:
                logger.warning("sandbox pool | janitor failed to remove orphan container %s: %s", c.id[:12], e)

        missing = tracked_docker_ids - docker_ids
        for docker_id in missing:
            logger.warning(
                "sandbox pool | janitor: container %s is tracked internally but Docker no longer reports it "
                "under this pool's label (removed out-of-band?) — will be caught by acquire()'s health check "
                "or the stuck-exec sweep",
                docker_id[:12],
            )
