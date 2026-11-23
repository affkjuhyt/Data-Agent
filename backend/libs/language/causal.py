"""Sentences that word a cause, one rule for the answer and for its scorer (AL-08, AC-03-01-07).

An answer reads numbers: a contribution, a correlation, a change. None of them is a cause, and a
sentence that says one is ("doanh thu giảm do khách hàng rời đi", "điều này dẫn đến…") claims
what no query showed. Causes belong in a hypothesis block, where the reader can check them; in the
answer's text such a sentence is dropped before it is stored, as an untraced number is
(agentic/verify.fill). The eval's causal scorer (model/eval/scorers/causal.py) counts the same
sentences, so what the runtime lets through is what the gate measures.
"""

from __future__ import annotations

import re
import unicodedata

# Causal wording, on the accent-free text. "do tỷ lệ … thay đổi" is the rate/mix split of the
# arithmetic, not a claim about the world, so a bare "do" is not on the list.
CAUSAL = re.compile(
    r"\b(?:gay ra|dan den|dan toi|khien cho|khien|lam cho|boi vi|vi vay|vi the|"
    r"nguyen nhan (?:la|chinh|chu yeu)|la nguyen nhan|bat nguon tu|xuat phat tu)\b"
)
# The method's own disclaimer names "nguyên nhân" to deny it; that sentence is allowed.
DISCLAIMER = re.compile(r"khong phai nguyen nhan|ket luan nguyen nhan can kiem dinh")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def _plain(text: str) -> str:
    text = unicodedata.normalize("NFD", text.lower().replace("đ", "d"))
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


def is_causal(sentence: str) -> bool:
    plain = _plain(sentence)
    return bool(CAUSAL.search(plain)) and not DISCLAIMER.search(plain)


def causal_sentences(text: str) -> list[str]:
    """The sentences that word a cause."""
    return [s for s in _SENTENCE.split(text) if is_causal(s)]


def without_causes(text: str) -> tuple[str, int]:
    """The text without its causal sentences, and how many were dropped. Lines are kept apart:
    a markdown list loses the item, not its neighbours."""
    dropped = 0
    lines = []
    for line in text.split("\n"):
        kept = []
        for sentence in _SENTENCE.split(line):
            if is_causal(sentence):
                dropped += 1
            else:
                kept.append(sentence)
        lines.append(" ".join(kept))
    return "\n".join(lines), dropped
