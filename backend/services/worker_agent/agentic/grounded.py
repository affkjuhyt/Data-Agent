"""Does a certified metric measure what the question asks? The agent's form of the governed
path's grounding (understand/resolver.py): the question's quantity words, period, fillers and
chosen cuts set aside, must each be named by a metric (its name, code or an approved synonym),
or say what to do with a quantity rather than which one."""

from __future__ import annotations

from collections.abc import Iterable

# Words that say what to do with a quantity, not which one (normalised, both languages): a "why",
# a ranking, a trend or a comparison is the agent's to plan, not a metric to be named.
ANALYSIS_WORDS = frozenset(
    "co vi sao tai giam tang top cao thap nhat xu huong bien dong phan tich nguyen nhan ty trong "
    "luy ke so sanh chenh lech tong trung binh nhieu it hon bao tri dong gop anh huong "
    "why how trend compare increase decrease highest lowest total average share top rank change "
    "growth drop rise".split()
)


def unnamed(words: Iterable[str], named: set[str]) -> list[str]:
    """The quantity words neither a metric names nor the analysis itself uses."""
    return [w for w in words if w not in named and w not in ANALYSIS_WORDS]
