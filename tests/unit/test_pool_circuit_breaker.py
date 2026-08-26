"""Unit tests for pool.py's CircuitBreaker and its wiring into
ContainerPool.acquire()/release() (docs/1.md "Kế hoạch 1" mục e). No real
Docker daemon involved — a fake client stands in so these stay in the
`unit` bucket and run fast/deterministically. tests/integration/
test_sandbox_pool.py covers the pool end to end against a real Docker
daemon; this file isolates the breaker's state machine and its exact
wiring point (which retire reasons count as "abnormal" vs. ordinary
churn) instead.
"""
from __future__ import annotations

import itertools
import pathlib
import sys
import time

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SVC_DIR = str(REPO_ROOT / "services" / "sandbox-executor")


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

    def __init__(self):
        self.id = f"fake{next(FakeContainer._ids)}"
        self.status = "running"
        self.removed = False

    def reload(self):
        pass

    def remove(self, force=False):
        self.removed = True

    def stats(self, stream=False):
        return {"memory_stats": {"usage": 1000}}


class FakeContainers:
    def run(self, image, cmd, **kwargs):
        return FakeContainer()

    def list(self, all=True, filters=None):
        return []


class FakeClient:
    def __init__(self):
        self.containers = FakeContainers()


# -- CircuitBreaker in isolation ---------------------------------------------
#
# enforce=True is passed explicitly everywhere below that expects
# before_acquire() to actually raise/admit-a-probe — the class defaults to
# enforce=False (log-only) per docs/1.md mục e's recommended rollout
# ("nên chạy log-only một thời gian trước khi bật reject"), so these tests
# opt in to the enforcing behavior they're specifically verifying.
# TestLogOnlyMode below covers the enforce=False default itself.

@pytest.mark.unit
class TestCircuitBreakerStandalone:
    def test_closed_by_default_and_before_acquire_is_not_a_probe(self):
        import pool
        breaker = pool.CircuitBreaker(threshold=3, window_s=60, cooldown_s=30, enforce=True)
        assert breaker.is_open is False
        assert breaker.before_acquire() is False

    def test_opens_only_once_threshold_abnormal_events_reached(self):
        import pool
        breaker = pool.CircuitBreaker(threshold=3, window_s=60, cooldown_s=30, enforce=True)
        breaker.record_abnormal()
        breaker.record_abnormal()
        assert breaker.is_open is False, "must not trip before the threshold is reached"
        breaker.record_abnormal()
        assert breaker.is_open is True

    def test_open_rejects_immediately_before_cooldown_elapses(self):
        import pool
        breaker = pool.CircuitBreaker(threshold=1, window_s=60, cooldown_s=10, enforce=True)
        breaker.record_abnormal()
        assert breaker.is_open is True
        with pytest.raises(pool.CircuitBreakerOpenError):
            breaker.before_acquire()

    def test_half_open_after_cooldown_admits_exactly_one_probe(self):
        import pool
        breaker = pool.CircuitBreaker(threshold=1, window_s=60, cooldown_s=0.05, half_open_trials=1, enforce=True)
        breaker.record_abnormal()
        time.sleep(0.15)
        assert breaker.before_acquire() is True, "first caller after cooldown should be admitted as a probe"
        with pytest.raises(pool.CircuitBreakerOpenError):
            breaker.before_acquire()  # a second concurrent caller must not get another probe slot

    def test_half_open_success_closes_breaker_and_resets_window(self):
        import pool
        breaker = pool.CircuitBreaker(threshold=1, window_s=60, cooldown_s=0.05, half_open_trials=1, enforce=True)
        breaker.record_abnormal()
        time.sleep(0.15)
        breaker.before_acquire()
        breaker.record_probe_result(healthy=True)
        assert breaker.is_open is False
        assert breaker.before_acquire() is False, "CLOSED again — ordinary acquire, no probe bookkeeping"

    def test_half_open_failure_reopens_with_backed_off_cooldown(self):
        import pool
        breaker = pool.CircuitBreaker(
            threshold=1, window_s=60, cooldown_s=0.05, max_cooldown_s=10, half_open_trials=1, enforce=True
        )
        breaker.record_abnormal()
        time.sleep(0.15)
        breaker.before_acquire()
        breaker.record_probe_result(healthy=False)
        assert breaker.is_open is True
        with pytest.raises(pool.CircuitBreakerOpenError):
            breaker.before_acquire()  # immediately after reopening — new (longer) cooldown hasn't elapsed

    def test_events_outside_the_sliding_window_are_pruned(self):
        import pool
        breaker = pool.CircuitBreaker(threshold=2, window_s=0.05, cooldown_s=30, enforce=True)
        breaker.record_abnormal()
        time.sleep(0.15)  # first event ages out of the window
        breaker.record_abnormal()
        assert breaker.is_open is False, "the aged-out first event must not count toward the threshold"


@pytest.mark.unit
class TestLogOnlyMode:
    """enforce=False is the default (docs/1.md mục e's recommended
    rollout) — the state machine still runs and logs transitions/would-be
    rejections, but before_acquire() never raises and never admits a
    probe, so nothing is actually gated on an untuned threshold."""

    def test_default_is_log_only(self):
        import pool
        breaker = pool.CircuitBreaker(threshold=1, window_s=60, cooldown_s=30)
        assert breaker.enforce is False

    def test_open_state_is_reached_but_before_acquire_never_raises(self):
        import pool
        breaker = pool.CircuitBreaker(threshold=1, window_s=60, cooldown_s=10_000)
        breaker.record_abnormal()
        assert breaker.is_open is True, "the state machine still tracks OPEN for observability"
        assert breaker.before_acquire() is False, "log-only: never raises, never admits a probe"

    def test_log_only_would_reject_warning_is_emitted(self, caplog):
        import pool
        breaker = pool.CircuitBreaker(threshold=1, window_s=60, cooldown_s=10_000)
        breaker.record_abnormal()
        with caplog.at_level("WARNING"):
            breaker.before_acquire()
        assert any("WOULD reject" in r.message for r in caplog.records)


# -- CircuitBreaker wired into ContainerPool.acquire()/release() ------------

@pytest.mark.unit
class TestContainerPoolBreakerWiring:
    def _pool(self, **breaker_kwargs):
        import pool
        breaker_kwargs.setdefault("enforce", True)  # these tests verify enforcing behavior specifically
        breaker = pool.CircuitBreaker(**breaker_kwargs)
        p = pool.ContainerPool(
            client=FakeClient(), image="fake:latest", size=1,
            memory_mb=256, cpus=0.5, pids_limit=50,
            max_uses=1000, max_age_s=10_000,
            janitor_interval_s=10_000,  # keeps the background janitor from firing mid-test
            breaker=breaker,
        )
        p.start()
        return p

    def test_caller_supplied_retire_reason_counts_as_abnormal(self):
        p = self._pool(threshold=2, window_s=60, cooldown_s=30)
        try:
            pc = p.acquire()
            p.release(pc, "execution timed out")  # caller-supplied -> abnormal (docs mục e condition 5)
            assert p.breaker.is_open is False
            pc = p.acquire()
            p.release(pc, "execution timed out")
            assert p.breaker.is_open is True
        finally:
            p.shutdown()

    def test_use_count_churn_does_not_count_as_abnormal(self):
        """docs/1.md mục e is explicit: use-count/TTL retirement (1)(2) is
        ordinary turnover and must NOT feed the breaker, only (3)(4)(5)
        (dirty verify / cleanup error / timeout) do."""
        p = self._pool(threshold=1, window_s=60, cooldown_s=30)
        p.max_uses = 1  # every release() now hits the use-count threshold pool.py derives internally
        try:
            pc = p.acquire()
            p.release(pc, None)  # retire_reason derived internally, NOT caller-supplied
            assert p.breaker.is_open is False
        finally:
            p.shutdown()

    def test_open_breaker_rejects_acquire_before_touching_idle_queue(self):
        import pool
        p = self._pool(threshold=1, window_s=60, cooldown_s=10_000)
        try:
            pc = p.acquire()
            p.release(pc, "cleanup exec failed: boom")
            assert p.breaker.is_open is True
            with pytest.raises(pool.CircuitBreakerOpenError):
                p.acquire()
        finally:
            p.shutdown()

    def test_probe_timeout_releases_its_half_open_slot(self):
        """A HALF_OPEN probe that times out waiting for an idle container
        (the sole container is still busy) must still release the
        half-open slot and count as a failed probe — otherwise the
        breaker would stay wedged forever waiting for a release() that
        will never come for that probe (acquire()'s `except TimeoutError`
        branch in pool.py)."""
        import pool
        p = self._pool(threshold=1, window_s=60, cooldown_s=0.05, half_open_trials=1)
        try:
            pc = p.acquire()  # holds the pool's only container busy for the rest of this test
            p.breaker.record_abnormal()
            assert p.breaker.is_open is True
            time.sleep(0.15)  # cooldown elapses -> the next acquire() is admitted as a HALF_OPEN probe

            p.acquire_timeout_s = 0.05
            with pytest.raises(TimeoutError):
                p.acquire()  # no idle container available (the only one is held by `pc`)

            # The timed-out probe must have reported itself as a failed
            # probe, not left the breaker's half-open slot permanently
            # consumed — a follow-up call sees OPEN-with-cooldown again,
            # not a wedged "probe already in flight".
            assert p.breaker.is_open is True
            with pytest.raises(pool.CircuitBreakerOpenError):
                p.acquire()
        finally:
            p.shutdown()

    def test_stats_reports_idle_busy_and_breaker_state(self):
        """Backs the ops-dashboard GET /status endpoint (docs/1.md mục h)."""
        p = self._pool(threshold=1, window_s=60, cooldown_s=30)
        try:
            baseline = p.stats()
            assert baseline == {
                "size": 1, "idle": 1, "busy": 0,
                "egress_enabled": False, "runtime": "default",
                "circuit_breaker": {"state": "closed", "enforce": True},
            }

            pc = p.acquire()
            mid = p.stats()
            assert mid["idle"] == 0 and mid["busy"] == 1

            p.release(pc, "execution timed out")
            after = p.stats()
            assert after["circuit_breaker"]["state"] == "open"
        finally:
            p.shutdown()


@pytest.mark.unit
class TestCircuitBreakerState:
    def test_state_property_reflects_transitions(self):
        import pool
        breaker = pool.CircuitBreaker(threshold=1, window_s=60, cooldown_s=30, enforce=True)
        assert breaker.state == "closed"
        breaker.record_abnormal()
        assert breaker.state == "open"
