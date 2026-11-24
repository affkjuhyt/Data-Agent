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
(agent.agent_run.stop_reason budget_tokens, budget_duration, budget_steps or budget_cost).

PL-12: the organisation's max_cost_per_run (governance.ai_setting, set on the admin AI screen)
caps what a turn's model calls may cost, priced by turn_trace (LLM_PRICES, a GPU second for the
models we serve). Unset, a turn has no cost limit.

Tokens are the turn's own, counted by turn_trace from every model call, from the moment the budget
is set.
"""

from __future__ import annotations

import decimal
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy import select

from libs.dataagent_db.models import governance
from services.worker_agent import turn_trace

Stop = Literal["budget_tokens", "budget_duration", "budget_steps", "budget_cost"]
COST_KEY = "max_cost_per_run"


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
    # PL-12: the organisation's max_cost_per_run, None for no limit; and the cost at the start.
    max_cost: decimal.Decimal | None = None
    cost_at_start: decimal.Decimal = decimal.Decimal(0)

    @classmethod
    def for_(
        cls,
        audience: str,
        *,
        deep: bool,
        clock: Callable[[], float] = time.monotonic,
        max_cost: decimal.Decimal | None = None,
    ) -> Budget:
        limits = LIMITS["deep"] if deep else LIMITS.get(audience, LIMITS["analyst"])
        return cls(
            limits,
            clock,
            clock(),
            turn_trace.tokens_used(),
            max_cost=max_cost,
            cost_at_start=turn_trace.cost_spent(),
        )

    def cost(self) -> decimal.Decimal:
        return turn_trace.cost_spent() - self.cost_at_start

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
            elif self.max_cost is not None and self.cost() >= self.max_cost:
                self.stopped = "budget_cost"
        return self.stopped


async def org_max_cost(session: Any) -> decimal.Decimal | None:
    """The organisation's max_cost_per_run (the session is scoped to it), None when unset or not a
    positive number (the admin screen stores a number of zero or more; 0 sets no limit)."""
    raw = await session.scalar(
        select(governance.AiSetting.value).where(governance.AiSetting.key == COST_KEY)
    )
    if isinstance(raw, bool) or not isinstance(raw, int | float | str):
        return None
    try:
        value = decimal.Decimal(str(raw))
    except decimal.InvalidOperation:
        return None
    return value if value.is_finite() and value > 0 else None
