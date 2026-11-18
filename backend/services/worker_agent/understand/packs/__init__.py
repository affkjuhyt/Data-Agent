"""The language packs the Understander reads a question with (GEN-31): every pack, Vietnamese
first, so a Vietnamese team's question asked in English is read as well. A pack is added here and
nowhere else; base.py says what one holds."""

from __future__ import annotations

import re
from collections.abc import Callable

from services.worker_agent.understand.packs import en, vi
from services.worker_agent.understand.packs.base import Pack

PACKS: tuple[Pack, ...] = (vi.PACK, en.PACK)


def search(text: str, pattern: Callable[[Pack], re.Pattern[str]]) -> re.Match[str] | None:
    """The first match of one of every pack's patterns ("falls", "counted") in normalised text."""
    for pack in PACKS:
        if m := pattern(pack).search(text):
            return m
    return None


def words(of: Callable[[Pack], frozenset[str]]) -> frozenset[str]:
    """One of every pack's word sets ("filler", "cut_ends"), all languages together."""
    return frozenset[str]().union(*(of(pack) for pack in PACKS))
