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


class FormulaMemoryEstimator:
    """Computes |P| from the paper's formula using live Peak(Memory).
    ``update_peak`` is called after each generation layer with the memory the
    last batch consumed; ``queue_size`` then re-derives the parallelism budget.
    """

    def __init__(self, init_mem: float, max_mem: float, peak_mem: float | None = None) -> None:
        if max_mem <= init_mem:
            raise ValueError("max_mem must exceed init_mem")
        self.init_mem = init_mem
        self.max_mem = max_mem
        # Seed Peak just above Init so the first layer gets the largest budget.
        self.peak_mem = peak_mem if peak_mem is not None else init_mem + (max_mem - init_mem) / 8

    def update_peak(self, peak_mem: float) -> None:
        # Clamp to (init, max]; Peak(Memory) <= Max(Memory) per the paper.
        self.peak_mem = min(max(peak_mem, self.init_mem + 1e-9), self.max_mem)

    def queue_size(self) -> int:
        size = (self.max_mem - self.init_mem) / (self.peak_mem - self.init_mem)
        return max(1, int(size))  # always at least one slot for generation
