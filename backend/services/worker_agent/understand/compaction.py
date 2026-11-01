"""Structured summary of older Ask turns for the Understander (AC-01-04-05).

Keeps metrics, cuts, filters, periods, and numbers that already appear in the
question text. Never invents figures — answer numbers stay on the figure path
(BR-171); this only preserves what the person already wrote or what chips named.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

# Same shape llm_reader uses: question text + plan chips.
Turn = tuple[str, Sequence[dict[str, object]]]

_NUMBER = re.compile(r"(?<![A-Za-z0-9_])" r"(?:\d{1,3}(?:[.,]\d{3})+|\d+)(?:[.,]\d+)?" r"(?:\s*%)?")


@dataclass
class CompactionSummary:
    """What older turns still need to tell the model."""

    turn_count: int
    metrics: list[str] = field(default_factory=list)
    dimensions: list[str] = field(default_factory=list)
    filters: list[str] = field(default_factory=list)
    periods: list[str] = field(default_factory=list)
    numbers: list[str] = field(default_factory=list)

    def as_line(self) -> str:
        parts = [
            f"{self.turn_count} câu trước đó hỏi về: "
            + (", ".join(self.metrics) if self.metrics else "không rõ chỉ số")
        ]
        if self.dimensions:
            parts.append(f"theo {', '.join(self.dimensions)}")
        if self.filters:
            parts.append(f"lọc {', '.join(self.filters)}")
        if self.periods:
            parts.append(f"kỳ {', '.join(self.periods)}")
        if self.numbers:
            parts.append(f"số đã nêu {', '.join(self.numbers)}")
        return "; ".join(parts) + "."


def _labels(chips: Sequence[dict[str, object]], kind: str) -> list[str]:
    return [str(c.get("label") or c.get("value")) for c in chips if c.get("kind") == kind]


def _numbers_in(text: str) -> list[str]:
    """Numbers the person wrote that look like figures, not turn indexes or years.

    Skips bare years and small integers without a percent or decimal (``câu số 1``),
    same generosity spirit as answer number provenance — keep signal, drop ordinal noise.
    """
    out: list[str] = []
    for match in _NUMBER.finditer(text or ""):
        raw = match.group(0).strip()
        body = raw.rstrip("%").strip()
        digits = re.sub(r"[^\d]", "", body)
        if len(digits) == 4 and digits.startswith(("19", "20")) and "%" not in raw:
            continue
        has_fraction = bool(re.search(r"[.,]\d", body))
        has_percent = raw.endswith("%")
        try:
            whole = int(re.sub(r"[.,].*", "", body.replace(",", "")) or "0")
        except ValueError:
            whole = 0
        if not has_fraction and not has_percent and whole < 100:
            continue
        if raw not in out:
            out.append(raw)
    return out


def summarize_older(turns: Sequence[Turn]) -> CompactionSummary | None:
    """Build a CompactionSummary from older turns, or None when there are none."""
    if not turns:
        return None
    metrics = dict.fromkeys(m for _, chips in turns for m in _labels(chips, "metric"))
    dimensions = dict.fromkeys(d for _, chips in turns for d in _labels(chips, "dimension"))
    filters = dict.fromkeys(f for _, chips in turns for f in _labels(chips, "filter"))
    periods = dict.fromkeys(p for _, chips in turns for p in _labels(chips, "period"))
    numbers = dict.fromkeys(n for text, _ in turns for n in _numbers_in(text))
    return CompactionSummary(
        turn_count=len(turns),
        metrics=list(metrics),
        dimensions=list(dimensions),
        filters=list(filters),
        periods=list(periods),
        numbers=list(numbers),
    )
