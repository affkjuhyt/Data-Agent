"""The matching form of a phrase, one for the api and the worker (GEN-31).

`norm` is what context.synonym.phrase_norm, glossary_term.term_norm and value_index.label_norm hold
and what a question is matched in: NFC, lower case, accents removed (đ → d), punctuation to spaces
(but % and /, which carry meaning in "tỷ lệ %" and "9/2026"), whitespace collapsed. The api wrote
phrase_norm with its punctuation kept, so "doanh-thu" stored never met "doanh thu" asked.

`fold` is the same without turning punctuation into spaces, for the readers that need the marks:
an ISO date's dashes, a comparison's ">".

`number` writes a figure the way the organisation's locale reads it (GEN-41): "1.234.567,89" in
vi-VN, "1,234,567.89" in en-US. A figure keeps its full value elsewhere (A1.1); this is only how
it reads in a sentence.

`use_locale` sets the locale of what is being written for the work in hand (a ContextVar): the
worker sets it per event to the organisation's, so a number, a chip's label or a refusal's
default sentence follows the organisation without each caller being handed it.
"""

from __future__ import annotations

import contextlib
import decimal
import re
import unicodedata
from collections.abc import Iterator
from contextvars import ContextVar

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


DEFAULT_LOCALE = "vi-VN"
_CURRENT: ContextVar[str | None] = ContextVar("dataagent_locale", default=None)


def current_locale() -> str:
    return _CURRENT.get() or DEFAULT_LOCALE


def current_language() -> str:
    return current_locale().split("-")[0].lower()


@contextlib.contextmanager
def use_locale(locale: str | None) -> Iterator[None]:
    token = _CURRENT.set(locale)
    try:
        yield
    finally:
        _CURRENT.reset(token)


# Languages that write a decimal comma and group thousands with a dot (or a space, for fr):
# the rest write a decimal point and group with a comma.
_COMMA_DECIMAL = frozenset(
    "vi id de es it pt nl tr da nb sv fi pl cs ro ru uk el hr sl sk hu".split()
)
_SPACE_GROUP = frozenset("fr nb sv fi pl cs ru uk sk".split())


def marks(locale: str | None) -> tuple[str, str]:
    """(thousands separator, decimal mark) of a locale tag such as vi-VN, en-US, fr-FR."""
    language = (locale or current_locale()).split("-")[0].lower()
    if language in _SPACE_GROUP:
        return "\u00a0", ","
    if language in _COMMA_DECIMAL:
        return ".", ","
    return ",", "."


def number(
    value: decimal.Decimal | int | float, decimals: int = 0, locale: str | None = None
) -> str:
    """`value` rounded half up to `decimals` places, written in `locale`'s marks."""
    exact = value if isinstance(value, decimal.Decimal) else decimal.Decimal(str(value))
    q = exact.quantize(decimal.Decimal(1).scaleb(-decimals), rounding=decimal.ROUND_HALF_UP)
    whole, _, frac = f"{abs(q):f}".partition(".")
    group, point = marks(locale)
    grouped = f"{int(whole):,}".replace(",", group)
    sign = "-" if q < 0 else ""
    return f"{sign}{grouped}{point}{frac}" if frac else f"{sign}{grouped}"
