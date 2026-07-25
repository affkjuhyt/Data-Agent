"""Linked list of an orchestrator's completed database writes, unwound LIFO on failure.

Every step that writes to a store pushes itself onto the stage_record right after its own
write commits — never before, and never batched at the end of a larger function —
so a step that raises is simply never pushed, and unwind() only ever undoes writes
that actually happened.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

RollbackFunc = Callable[..., Awaitable[None]]


class StageNode:
    __slots__ = ("name", "rollback_func", "rollback_args", "rollback_kwargs", "prev")

    def __init__(
        self,
        name: str,
        rollback_func: RollbackFunc | None,
        rollback_args: tuple,
        rollback_kwargs: dict[str, Any],
        prev: "StageNode | None",
    ) -> None:
        self.name = name
        self.rollback_func = rollback_func
        self.rollback_args = rollback_args
        self.rollback_kwargs = rollback_kwargs
        self.prev = prev


class StageChain:
    """Push a node right after a write succeeds; unwind() calls rollback_func(*rollback_args,
    **rollback_kwargs) for every node newest-first. rollback_func must be an awaitable
    (async def) callable — unwind() awaits its return value directly."""

    def __init__(self) -> None:
        self.tail: StageNode | None = None

    def push(
        self,
        name: str,
        rollback_func: RollbackFunc | None = None,
        rollback_args: tuple = (),
        rollback_kwargs: dict[str, Any] | None = None,
    ) -> None:
        self.tail = StageNode(
            name=name, rollback_func=rollback_func, rollback_args=rollback_args,
            rollback_kwargs=rollback_kwargs or {}, prev=self.tail,
        )

    def names(self) -> list[str]:
        names: list[str] = []
        node = self.tail
        while node is not None:
            names.append(node.name)
            node = node.prev
        names.reverse()
        return names

    async def unwind(self) -> list[str]:
        rolled_back: list[str] = []
        node = self.tail
        while node is not None:
            if node.rollback_func is not None:
                await node.rollback_func(*node.rollback_args, **node.rollback_kwargs)
                rolled_back.append(node.name)
            node = node.prev
        self.tail = None
        return rolled_back
