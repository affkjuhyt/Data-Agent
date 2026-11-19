"""What one agent turn may spend (AE-02, docs/blueprint/ASK_ENGINE_SPEC.md §2): model tokens,
seconds of wall time and tool steps, by who asked.

Measured on DS-AW (12/11/2026) the agent spent 22.6k tokens and 16 s on average where the governed
path spent 536 tokens and 2.3 s, and nothing stopped a turn that kept planning. The router (AE-01)
already keeps single-metric questions off the agent; this keeps the questions it does get within
what the person waits for: an executive gets a quick answer, an analyst a longer one, a turn asked
to go deep the longest.

The budget is checked where the agent decides what to do next (engine.after_act, after_review,
the replan route), never in the middle of a step: a running query or model call finishes. Spent,
the turn composes from what it already found (present_gate still decides whether that is an
answer or a hand-over to the governed path), and the run records why it stopped
(agent.agent_run.stop_reason budget_tokens, budget_duration or budget_steps).

Tokens are the turn's own, counted by turn_trace from every model call, from the moment the budget
is set.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from services.worker_agent import turn_trace

Stop = Literal["budget_tokens", "budget_duration", "budget_steps"]


@dataclass(frozen=True)
class Limits:
    tokens: int
    seconds: float
    steps: int


# Proposed in ASK_ENGINE_SPEC §2 (provisional, to be rebased on measured runs like the eval
# thresholds): an executive waits seconds, an analyst a minute, a deep turn three.
LIMITS: dict[str, Limits] = {
    "executive": Limits(tokens=15_000, seconds=30, steps=6),
    "analyst": Limits(tokens=40_000, seconds=60, steps=8),
    "deep": Limits(tokens=120_000, seconds=180, steps=12),
}


@dataclass
class Budget:
    limits: Limits
    clock: Callable[[], float] = time.monotonic
    started: float = 0.0
    tokens_at_start: int = 0
    # Set once the budget is spent: the first reason found stays the reason.
    stopped: Stop | None = field(default=None)

    @classmethod
    def for_(
        cls, audience: str, *, deep: bool, clock: Callable[[], float] = time.monotonic
    ) -> Budget:
        limits = LIMITS["deep"] if deep else LIMITS.get(audience, LIMITS["analyst"])
        return cls(limits, clock, clock(), turn_trace.tokens_used())

    def tokens(self) -> int:
        return turn_trace.tokens_used() - self.tokens_at_start

    def seconds(self) -> float:
        return self.clock() - self.started

    def spent(self, step_runs: int) -> Stop | None:
        """Why the turn must stop now, or None. Once spent it stays spent."""
        if self.stopped is None:
            if self.tokens() >= self.limits.tokens:
                self.stopped = "budget_tokens"
            elif self.seconds() >= self.limits.seconds:
                self.stopped = "budget_duration"
            elif step_runs >= self.limits.steps:
                self.stopped = "budget_steps"
        return self.stopped
