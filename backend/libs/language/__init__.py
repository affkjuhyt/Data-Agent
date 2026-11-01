"""The matching form of a phrase, one for the api and the worker (GEN-31).

`norm` is what context.synonym.phrase_norm, glossary_term.term_norm and value_index.label_norm hold
and what a question is matched in: NFC, lower case, accents removed (đ → d), punctuation to spaces
(but % and /, which carry meaning in "tỷ lệ %" and "9/2026"), whitespace collapsed. The api wrote
phrase_norm with its punctuation kept, so "doanh-thu" stored never met "doanh thu" asked.

`fold` is the same without turning punctuation into spaces, for the readers that need the marks:
an ISO date's dashes, a comparison's ">".
"""

from __future__ import annotations

import re
import unicodedata

_PUNCT = re.compile(r"[^\w%/]+")
_SPACES = re.compile(r"\s+")


def fold(text: str) -> str:
    text = unicodedata.normalize("NFC", text).lower().replace("đ", "d")
    stripped = "".join(
        c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn"
    )
    return _SPACES.sub(" ", stripped).strip()


def norm(text: str) -> str:
    return " ".join(_PUNCT.sub(" ", fold(text)).split())
