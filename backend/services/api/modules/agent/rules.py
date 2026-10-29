"""The agent rules the database cannot hold: run state machine, BR-70, BR-76, BR-170 ceilings and
the trigger preview of UC-13-03. Pure functions, so they are tested without a database."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from services.api.kernel.problems import FieldError, Problem

# agent_run.status (tab 3b D4.8). Terminal runs never move again.
ACTIVE_RUN = frozenset({"queued", "planning", "running", "waiting_human", "paused"})
TERMINAL_RUN = frozenset({"succeeded", "failed", "cancelled"})
PAUSABLE = frozenset({"queued", "planning", "running", "waiting_human"})
# UC-13-06 step 4: the plan is edited while the run holds still (paused, or stopped at a checkpoint).
PLAN_EDITABLE = frozenset({"paused", "waiting_human"})

# BR-170: background runs stop at 60 steps, 400 000 tokens, 30 minutes; agent_config.limits may
# only lower them. There is no per-organisation ceiling table yet, so these are the ceilings.
LIMIT_CEILINGS: dict[str, int] = {"max_steps": 60, "max_tokens": 400_000, "max_duration_min": 30}

# BR-148: default rollback gates of a deployment.
DEFAULT_GATES: dict[str, int] = {
    "error_rate_increase_pts": 5,
    "needs_human_increase_pts": 10,
    "cost_per_run_increase_pct": 30,
    "min_runs": 20,
}

# UC-13-03 E1: a trigger that fires more than this per day warns about the budget.
DAILY_FIRE_WARNING = 50


def transition(status: str, allowed: Iterable[str], action: str) -> None:
    if status not in allowed:
        raise Problem(409, "invalid_state", f"A run in status {status} cannot {action}.")


def resume_status(started: bool) -> str:
    # A run paused before the worker picked it up goes back to the queue; one paused mid-way
    # continues from its current step.
    return "running" if started else "queued"


def check_tools(autonomy: str, tools: Iterable[Mapping[str, Any]]) -> None:
    """BR-70: a write_derived tool needs a checkpoint unless the agent acts then reports.
    send_external without a checkpoint is refused by the database CHECK."""
    for tool in tools:
        if (
            tool["permission_level"] == "write_derived"
            and not tool.get("requires_checkpoint")
            and autonomy != "act_then_report"
        ):
            raise Problem(
                422,
                "business_rule_violated",
                f"Tool {tool['tool']} writes derived data and needs a checkpoint (BR-70).",
            )


def check_limits(limits: Mapping[str, Any]) -> None:
    """BR-170: limits lower the organisation's ceilings, never raise them."""
    for key, ceiling in LIMIT_CEILINGS.items():
        value = limits.get(key)
        if value is None:
            continue
        if not isinstance(value, int | float) or isinstance(value, bool) or value <= 0:
            raise Problem(
                422,
                "validation_failed",
                f"limits.{key} must be a positive number.",
                errors=[
                    FieldError(field=f"limits.{key}", code="invalid", message="Positive number.")
                ],
            )
        if value > ceiling:
            raise Problem(
                422,
                "business_rule_violated",
                f"limits.{key} = {value} exceeds the ceiling {ceiling} (BR-170).",
            )


# BR-76: no personal data or concrete balances in a memory. A citizen id (CMND 9 digits, CCCD 12),
# an account-like run of 8+ digits, or an amount with a currency is refused; the model-side
# redaction of what agents extract themselves is Agent runtime's job.
_PERSONAL = (
    re.compile(r"(?<!\d)\d{8,19}(?!\d)"),
    re.compile(
        r"\d[\d.,\s]*\s*(?:vnd|vnđ|đồng|đ|usd|\$|eur|ty|tỷ|trieu|triệu)(?![a-zà-ỹ])",
        re.IGNORECASE,
    ),
)


def check_memory_content(content: str) -> None:
    compact = re.sub(r"(?<=\d)[ .-](?=\d)", "", content)
    if any(p.search(compact) for p in _PERSONAL):
        raise Problem(
            422,
            "business_rule_violated",
            "A memory may not hold personal identifiers, account numbers or balances (BR-76).",
        )
