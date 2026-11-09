"""Blast-radius → HITL disposition (OP-3 / docs/research/agent-ops-from-system-prompts.md).

Single source of truth for whether a capability may run alone, must pause for a person, or is
outside product scope. Mirrors 01 §2.2 (read/compute auto; send_external and context writes need
approval; write_source never) and BR-70 (write_derived needs a checkpoint unless act_then_report).
Autonomy participates: suggest_only always pauses before a derived write.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

Disposition = Literal["auto", "hitl", "forbid"]

# Capability levels granted on agent tools (agent.agent_tool.permission_level).
Capability = Literal["read", "write_derived", "send_external", "write_source"]


def disposition(
    level: str,
    *,
    autonomy: str,
    checkpoints: Sequence[str],
    requires_checkpoint: bool,
    over_budget: bool = False,
) -> Disposition:
    """Map capability x autonomy x config → auto | hitl | forbid.

    `over_budget` covers query/LLM cost past the Data Space budget (01 §2.2): confirm or stop.
    """
    if level == "write_source":
        return "forbid"
    if over_budget:
        return "hitl"
    if level == "send_external":
        return "hitl"
    if level == "read":
        return "auto"
    if level != "write_derived":
        # Unknown levels do not invent a pause (same as the prior needs_checkpoint default).
        return "auto"

    named = "before_write_derived" in checkpoints
    if autonomy == "suggest_only":
        return "hitl"
    if autonomy == "act_then_report":
        return "hitl" if requires_checkpoint or named else "auto"
    # propose_approve (default) and any other value: pause when the tool or config asks.
    return "hitl" if requires_checkpoint or named else "auto"


def needs_checkpoint(
    level: str,
    *,
    autonomy: str,
    checkpoints: Sequence[str],
    requires_checkpoint: bool,
    over_budget: bool = False,
) -> bool:
    """Whether the tool call waits for a person. Forbidden levels raise (never auto-run)."""
    d = disposition(
        level,
        autonomy=autonomy,
        checkpoints=checkpoints,
        requires_checkpoint=requires_checkpoint,
        over_budget=over_budget,
    )
    if d == "forbid":
        raise ValueError(f"capability {level!r} is outside product scope (01 §2.2)")
    return d == "hitl"
