"""Workspace rules the database cannot hold: access levels and the tile dependency graph.

Pure functions, so they are unit-tested without Postgres; routes.py applies them.
"""

from __future__ import annotations

import datetime
import uuid
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

RANK = {"view": 0, "comment": 1, "edit": 2, "owner": 3}

# ws_participant.role → the level it grants. agent_helper is an agent's seat, never a user's.
ROLE_PERMISSION = {"owner": "owner", "editor": "edit", "viewer": "view"}


def permission_of(
    *,
    is_owner: bool,
    share_permission: str | None,
    share_expires_at: datetime.datetime | None,
    participant_role: str | None,
    now: datetime.datetime,
) -> str | None:
    """The caller's level on a workspace, None when it has none.

    A user share is the grant and carries the expiry (BR-89); the participant row a share
    also creates is only the roster, so it never outlives an expired share.
    """
    if is_owner:
        return "owner"
    if share_permission is not None:
        if share_expires_at is not None and share_expires_at <= now:
            return None
        return share_permission
    if participant_role is not None:
        return ROLE_PERMISSION.get(participant_role)
    return None


def at_least(permission: str, needed: str) -> bool:
    return RANK[permission] >= RANK[needed]


class CycleError(ValueError):
    pass


def topological_order(
    tiles: Sequence[tuple[uuid.UUID, int, Iterable[uuid.UUID]]],
) -> list[uuid.UUID]:
    """Tiles (id, position, depends_on) in an order where every tile follows the ones it
    depends on (UC-02-02 step 2); ties keep the notebook order. A dependency on a tile not in
    the list (deleted) is ignored."""
    ids = {tile_id for tile_id, _, _ in tiles}
    position = {tile_id: pos for tile_id, pos, _ in tiles}
    deps = {
        tile_id: {d for d in depends_on if d in ids and d != tile_id}
        for tile_id, _, depends_on in tiles
    }
    order: list[uuid.UUID] = []
    ready = sorted((t for t, d in deps.items() if not d), key=lambda t: (position[t], t))
    remaining = {t: set(d) for t, d in deps.items() if d}
    while ready:
        current = ready.pop(0)
        order.append(current)
        freed = []
        for t, d in remaining.items():
            d.discard(current)
            if not d:
                freed.append(t)
        for t in freed:
            del remaining[t]
        ready = sorted([*ready, *freed], key=lambda t: (position[t], t))
    if remaining:
        raise CycleError("the tile dependencies form a cycle")
    return order


def dependents(graph: Mapping[uuid.UUID, Iterable[uuid.UUID]], root: uuid.UUID) -> set[uuid.UUID]:
    """Every tile that depends on `root`, directly or through other tiles."""
    found: set[uuid.UUID] = set()
    frontier = [root]
    while frontier:
        current = frontier.pop()
        for tile_id, depends_on in graph.items():
            if current in depends_on and tile_id not in found and tile_id != root:
                found.add(tile_id)
                frontier.append(tile_id)
    return found


def creates_cycle(
    graph: Mapping[uuid.UUID, Iterable[uuid.UUID]],
    tile_id: uuid.UUID,
    depends_on: Iterable[uuid.UUID],
) -> bool:
    """Would giving `tile_id` these dependencies close a loop?"""
    wanted = set(depends_on)
    return tile_id in wanted or bool(wanted & dependents(graph, tile_id))


def failed_checks(checks: Any) -> list[str]:
    """Names of the session-report checks that did not pass. A check is "pass"/"fail" or an
    object with a "status" (the worker adds details beside it)."""
    if not isinstance(checks, dict):
        return []
    failed = []
    for name, value in checks.items():
        status = value.get("status") if isinstance(value, dict) else value
        if status != "pass":
            failed.append(str(name))
    return sorted(failed)
