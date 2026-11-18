"""Does a certified metric measure what the question asks? The agent's form of the governed
path's grounding (understand/resolver.py): the question's quantity words, period, fillers and
chosen cuts set aside, must each be named by a metric (its name, code or an approved synonym),
or say what to do with a quantity rather than which one."""

from __future__ import annotations

from collections.abc import Iterable

from services.worker_agent.understand.packs import words

# Words that say what to do with a quantity, not which one (every language pack's): a "why", a
# ranking, a trend or a comparison is the agent's to plan, not a metric to be named.
ANALYSIS_WORDS = words(lambda pack: pack.analysis_words)


def unnamed(words: Iterable[str], named: set[str]) -> list[str]:
    """The quantity words neither a metric names nor the analysis itself uses."""
    return [w for w in words if w not in named and w not in ANALYSIS_WORDS]
