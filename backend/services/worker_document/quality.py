"""The quality checks of a document's text (DI-13, DOCUMENT_INTELLIGENCE_SPEC §9), deterministic.

`garbled`: a passage of at least GARBLED_MIN_CHARS whose characters are more than GARBLED_SHARE
outside the locale's alphabet (control and private-use characters, the replacement character,
letters of another script than Latin for vi and en): a broken text layer or a wrong OCR language.
§9 also names a dictionary check (share of the language pack's 5 000 common words); there is no
such list in the packs yet, so only the alphabet rule runs.

`empty`: a chunk with nothing to find: under EMPTY_MIN_CHARS once stripped, or only digits and
symbols (a page number, a row of dots). It is not indexed.
"""

from __future__ import annotations

import unicodedata

GARBLED_MIN_CHARS = 80
GARBLED_SHARE = 0.2
EMPTY_MIN_CHARS = 20


def _foreign(c: str) -> bool:
    if c == "�":
        return True
    category = unicodedata.category(c)
    if category[0] == "C" and c not in "\n\r\t":
        return True  # control, format, private use, unassigned
    if category[0] == "L":
        return "LATIN" not in unicodedata.name(c, "")
    return False


def garbled(text: str) -> bool:
    body = text.strip()
    if len(body) < GARBLED_MIN_CHARS:
        return False
    foreign = sum(1 for c in body if _foreign(c))
    return foreign / len(body) > GARBLED_SHARE


def empty(text: str) -> bool:
    body = text.strip()
    if len(body) < EMPTY_MIN_CHARS:
        return True
    return not any(unicodedata.category(c)[0] == "L" for c in body)
