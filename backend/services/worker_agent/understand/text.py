"""A question's text as the Understander matches it (tab 5 G5.1): normalised form and periods.

`norm` is the matching form of a phrase (libs/language, the api's too): NFC, lower case, accents
removed (đ → d), punctuation to spaces, whitespace collapsed. context.synonym.phrase_norm,
glossary_term.term_norm and value_index.label_norm hold the same form, so a question matches
whichever way it was typed.

`find_period` reads the period a question names, relative to `today` (the question's as_of or the
day it was asked), in any language pack (understand/packs, GEN-31): an ISO date, month or quarter
first, then each pack's words: a month, a quarter, a year, a day, "this/last" week, month,
quarter and year, and a fiscal year or quarter on the Data Space's calendar (GEN-21). A plain year
or quarter is the calendar's: only the words for "fiscal" name the fiscal one. It returns the span
it read and the half-open date range; None when the question names no period.
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass

from libs import clock
from libs.language import fold, norm
from services.worker_agent.understand.packs import PACKS
from services.worker_agent.understand.packs.base import FoundPeriod, iso_period

__all__ = ["FoundPeriod", "KnowledgeHint", "find_knowledge_hint", "find_period", "norm", "words_of"]


@dataclass(frozen=True)
class KnowledgeHint:
    """A mode explicitly expressed in the question, and the words to remove before matching.

    The API still accepts an explicit ``knowledge_mode``.  This hint is the safe default for
    natural-language questions: a phrase such as “như đã nộp” must not be allowed to fall through
    to the live-data path just because the browser omitted the optional chip.
    """

    mode: str
    span: str


def find_knowledge_hint(question: str) -> KnowledgeHint | None:
    text = norm(question)
    # The period close first, in every pack: "as filed" outranks "on current data".
    for wanted in ("period_close", "latest"):
        for pack in PACKS:
            for phrase, mode in pack.knowledge:
                if mode == wanted and re.search(rf"\b{re.escape(phrase)}\b", text):
                    return KnowledgeHint(mode, phrase)
    return None


def find_period(
    question: str, today: datetime.date, cal: clock.Calendar = clock.DEFAULT
) -> FoundPeriod | None:
    if found := iso_period(fold(question)):
        return found
    t = norm(question)
    for pack in PACKS:
        if found := pack.periods(t, fold(question), today, cal):
            return found
    return None


def words_of(text: str, phrase: str) -> str:
    """The words of `text` whose normalised form is `phrase`, as the person wrote them (accents,
    case); the phrase itself when no run of words matches."""
    words = text.split()
    size = len(phrase.split())
    return next(
        (
            " ".join(words[i : i + size])
            for i in range(len(words) - size + 1)
            if norm(" ".join(words[i : i + size])) == phrase
        ),
        phrase,
    )
