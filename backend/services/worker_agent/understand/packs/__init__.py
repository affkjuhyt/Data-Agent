"""The language packs the Understander reads a question with (GEN-31): every pack, Vietnamese
first, so a Vietnamese team's question asked in English is read as well. A pack is added here and
nowhere else; base.py says what one holds."""

from __future__ import annotations

from services.worker_agent.understand.packs import en, vi
from services.worker_agent.understand.packs.base import Pack

PACKS: tuple[Pack, ...] = (vi.PACK, en.PACK)
