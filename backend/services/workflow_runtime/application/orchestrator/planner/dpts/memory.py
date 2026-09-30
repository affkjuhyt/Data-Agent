"""Flexible queue sizing (paper §3 "Flexible Queue for Parallelism").

    |P| = (Max(memory) - Init(memory)) / (Peak(memory) - Init(memory))

Max(Memory) and Init(Memory) are constants; Peak(Memory) is the memory used by
the previous batch generation (Peak <= Max). The queue always keeps at least
one slot for generation.

For the planner we have no GPU, so the default estimator just returns a static
size from config. The interface exists so a real deployment can plug in a vLLM
memory probe without touching the search engine.
"""

from __future__ import annotations

from typing import Protocol


class MemoryEstimator(Protocol):
    def queue_size(self) -> int:
        ...


class StaticMemoryEstimator:
    """Fixed queue size (default for the planner / tests)."""

    def __init__(self, size: int) -> None:
        self._size = max(1, size)

    def queue_size(self) -> int:
        return self._size
