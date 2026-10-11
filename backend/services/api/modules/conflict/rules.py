"""Pure rules of the conflict module: source ranking (BR-121), the suggested resolution (BR-30)
and the 90-day backtest of a ranking (UC-07-05 step 4)."""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

# BR-121: uploaded data (level 3) and external data (reference) never rank above level 1.
_NEVER_ABOVE_LEVEL_1 = frozenset({"level_3", "reference"})

# Conflict columns a resolution rule's condition may test by equality. A condition naming any
# other key does not match: a rule is applied only when it is understood (BR-30, deterministic).
CONDITION_KEYS = frozenset({"kind", "subject_key", "attribute", "detected_at_stage", "material"})


def ranking_violation(ranking: Sequence[uuid.UUID], levels: Mapping[uuid.UUID, str]) -> str | None:
    """Why the ranking breaks BR-121, or None."""
    for i, higher in enumerate(ranking):
        if levels.get(higher) not in _NEVER_ABOVE_LEVEL_1:
            continue
        if any(levels.get(lower) == "level_1" for lower in ranking[i + 1 :]):
            return (
                "Uploaded (level 3) and external (reference) sources cannot rank above a "
                "level 1 source (BR-121)."
            )
    return None


@dataclass(frozen=True)
class Candidate:
    claim_id: uuid.UUID
    source_id: uuid.UUID | None


Basis = Literal["resolution_rule", "source_authority", "no_basis"]


@dataclass(frozen=True)
class Suggestion:
    """What BR-30 would choose; claim_id None means a person decides."""

    claim_id: uuid.UUID | None
    outcome: str
    basis: Basis


def rule_matches(condition: Any, conflict: Mapping[str, Any]) -> bool:
    if not isinstance(condition, dict):
        return False
    if set(condition) - CONDITION_KEYS:
        return False
    return all(conflict.get(k) == v for k, v in condition.items())


def rule_source(action: Any) -> uuid.UUID | None:
    if not isinstance(action, dict) or "source_id" not in action:
        return None
    try:
        return uuid.UUID(str(action["source_id"]))
    except ValueError:
        return None


def _best_by_rank(
    candidates: Sequence[Candidate], ranking: Sequence[uuid.UUID]
) -> Candidate | None:
    """The single candidate whose source ranks strictly highest; None on a tie or no rank."""
    position = {source: i for i, source in enumerate(ranking)}
    ranked = sorted(
        [
            (position[c.source_id], c)
            for c in candidates
            if c.source_id is not None and c.source_id in position
        ],
        key=lambda pair: pair[0],
    )
    if not ranked:
        return None
    best_rank, best = ranked[0]
    # 3a: two claims from equally ranked sources (or the same source) need a person.
    if sum(1 for rank, _ in ranked if rank == best_rank) > 1:
        return None
    return best


def suggest(
    conflict: Mapping[str, Any],
    candidates: Sequence[Candidate],
    rules: Iterable[tuple[Any, Any]],
    ranking: Sequence[uuid.UUID] | None,
) -> Suggestion:
    """BR-30 order: an approved rule, then the source authority of the data kind; below the
    materiality threshold the preferred source is used and the conflict only noted."""
    for condition, action in rules:
        source = rule_source(action)
        if source is None or not rule_matches(condition, conflict):
            continue
        chosen = [c for c in candidates if c.source_id == source]
        if len(chosen) == 1:
            return Suggestion(chosen[0].claim_id, "auto_resolved", "resolution_rule")
    best = _best_by_rank(candidates, ranking or [])
    if best is None:
        return Suggestion(None, "resolved_by_human", "no_basis")
    outcome = "auto_resolved" if conflict.get("material") else "noted"
    return Suggestion(best.claim_id, outcome, "source_authority")


def backtest(
    conflicts: Iterable[Sequence[Candidate]], ranking: Sequence[uuid.UUID]
) -> dict[str, Any]:
    """How the ranking would have treated past conflicts: how many it settles and for which
    source (UC-07-05 step 4)."""
    total = settled = 0
    by_source: dict[str, int] = {}
    for candidates in conflicts:
        total += 1
        best = _best_by_rank(candidates, ranking)
        if best is None or best.source_id is None:
            continue
        settled += 1
        key = str(best.source_id)
        by_source[key] = by_source.get(key, 0) + 1
    return {
        "window_days": 90,
        "conflicts": total,
        "would_auto_resolve": settled,
        "needs_person": total - settled,
        "chosen_by_source": by_source,
    }
