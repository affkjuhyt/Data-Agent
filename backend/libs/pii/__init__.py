"""Personal data in text, by country pack, checked by value (GEN-14).

A pure leaf: no I/O, no dependency, so every service can use it (worker-document masks document
text with it, worker-agent's schema scan keeps personal values out of the catalogue).

Until GEN-14 every run of 12 or 9 digits was a Vietnamese CCCD or CMND and every 10 digits after a
0 a phone, so an invoice, order or account-statement number was masked away with the personal
data. A pattern now only names a candidate; the value decides:

  * GLOBAL (always): an e-mail; a card number that passes Luhn; an IBAN whose mod-97 check holds;
    an IPv4 address.
  * VN: a CCCD is 12 digits whose first three are a province code and whose fourth a
    century-and-sex code; a CMND (9 digits, no checksum) only with its word in front ("CMND",
    "chứng minh", "CMT"); a phone only with a Vietnamese mobile prefix (03, 05, 07, 08, 09) or a
    landline area code (02x), after 0, +84 or 0084.
  * US: an SSN as 3-2-4 digits outside the ranges never issued (000, 666, 9xx; 00; 0000).

Which packs read a text is the reader's ingest profile (`pii_packs`, else the region of its locale,
else its language: vi-VN or vi → VN); GLOBAL always applies. A locale that names no pack keeps the
default (GLOBAL and VN): an unresolved locale never masks less than before the packs existed.

  * ``mask(text, types, packs)``  -> (masked_text, {type: count})
  * ``scan(text, types, packs)``  -> {"counts", "total", "preview"}: never the raw value.
  * ``personal_share(values, packs)``: the share of `values` that hold personal data.
  * ``column_label(values, packs)``: the catalogue's pii_label for a column of such values.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

# Canonical type identifiers. Order is the tie-break when two matches overlap (earlier wins).
PII_TYPES: tuple[str, ...] = ("EMAIL", "CREDIT_CARD", "IBAN", "CCCD", "CMND", "SSN", "PHONE", "IP")
PACKS: tuple[str, ...] = ("GLOBAL", "VN", "US")
DEFAULT_PACKS: tuple[str, ...] = ("GLOBAL", "VN")

_REDACTION = "[REDACTED:{type}]"
_PRIORITY = {t: i for i, t in enumerate(PII_TYPES)}


def _luhn_ok(digits: str) -> bool:
    total = 0
    parity = len(digits) % 2
    for i, ch in enumerate(digits):
        d = ord(ch) - 48
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _card(match: re.Match[str], _text: str) -> bool:
    digits = re.sub(r"\D", "", match.group(0))
    return 13 <= len(digits) <= 19 and _luhn_ok(digits)


def _iban(match: re.Match[str], _text: str) -> bool:
    code = re.sub(r"\s", "", match.group(0)).upper()
    moved = code[4:] + code[:4]
    number = "".join(str(int(c, 36)) for c in moved)
    return len(code) >= 15 and int(number) % 97 == 1


# Province and city codes of the CCCD's first three digits (Thông tư 07/2016/TT-BCA).
_VN_PROVINCES = frozenset(
    "001 002 004 006 008 010 011 012 014 015 017 019 020 022 024 025 026 027 030 031 033 034 035 "
    "036 037 038 040 042 044 045 046 048 049 051 052 054 056 058 060 062 064 066 067 068 070 072 "
    "074 075 077 079 080 082 083 084 086 087 089 091 092 093 094 095 096".split()
)


def _cccd(match: re.Match[str], _text: str) -> bool:
    digits = match.group(0)
    # The fourth digit is the century and sex of the holder: 0-3 for births 1900-2099.
    return digits[:3] in _VN_PROVINCES and digits[3] in "0123"


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFC", text).lower().replace("đ", "d")
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")


_CMND_WORDS = re.compile(
    r"(?:cmnd|cmt|chung minh(?: nhan dan| thu)?|so dinh danh)\W{0,5}(?:so\W{0,3})?$"
)


def _cmnd(match: re.Match[str], text: str) -> bool:
    # Nine digits carry no checksum: only the word in front makes them an identity number.
    before = _fold(text[max(0, match.start() - 40) : match.start()])
    return bool(_CMND_WORDS.search(before.rstrip(" :#.-")))


def _ssn(match: re.Match[str], _text: str) -> bool:
    area, group, serial = match.group(1), match.group(2), match.group(3)
    return area not in ("000", "666") and area[0] != "9" and group != "00" and serial != "0000"


Check = Callable[[re.Match[str], str], bool]


@dataclass(frozen=True)
class _Rule:
    type: str
    pattern: re.Pattern[str]
    check: Check | None = None


_RULES: dict[str, tuple[_Rule, ...]] = {
    "GLOBAL": (
        _Rule("EMAIL", re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")),
        _Rule("CREDIT_CARD", re.compile(r"\b\d(?:[ \-]?\d){12,18}\b"), _card),
        _Rule(
            "IBAN",
            re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,4})?\b"),
            _iban,
        ),
        _Rule(
            "IP",
            re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b"),
        ),
    ),
    "VN": (
        _Rule("CCCD", re.compile(r"\b\d{12}\b"), _cccd),
        _Rule("CMND", re.compile(r"\b\d{9}\b"), _cmnd),
        # A mobile prefix (03, 05, 07, 08, 09) and 8 digits, or a landline area code (02x) and 8.
        _Rule(
            "PHONE",
            re.compile(
                r"(?<![\w])(?:\+84|0084|0)[ .\-]?(?:[35789]\d|2\d[ .\-]?\d)(?:[ .\-]?\d){7}(?![\w])"
            ),
        ),
    ),
    "US": (_Rule("SSN", re.compile(r"\b(\d{3})-(\d{2})-(\d{4})\b"), _ssn),),
}


# A locale's language when its region names no pack.
_LANGUAGE_PACKS = {"vi": "VN", "en": "US"}


def packs_for(locale: str | None, wanted: Sequence[str] | None = None) -> tuple[str, ...]:
    """The packs a reader applies: `wanted` (an ingest profile's pii_packs, chosen on purpose),
    else the pack of `locale`'s region, else of its language, else DEFAULT_PACKS; GLOBAL always.
    Fail closed: a locale nothing maps falls back to the default set, never to GLOBAL alone."""
    if wanted:
        chosen = [p.upper() for p in wanted if p.upper() in _RULES]
    else:
        parts = (locale or "").replace("_", "-").split("-")
        region = parts[-1].upper() if len(parts) > 1 else ""
        language = _LANGUAGE_PACKS.get(parts[0].lower(), "")
        chosen = [region] if region in _RULES else [language] if language else []
        if not chosen:
            return DEFAULT_PACKS
    return tuple(dict.fromkeys(["GLOBAL", *chosen]))


def _normalize_types(types: Iterable[str] | None) -> tuple[str, ...]:
    if types is None:
        return PII_TYPES
    wanted = {t.strip().upper() for t in types if t and t.strip()}
    return tuple(t for t in PII_TYPES if t in wanted)


def _collect_spans(
    text: str, types: tuple[str, ...], packs: Sequence[str]
) -> list[tuple[int, int, str]]:
    """Non-overlapping (start, end, type) spans, the higher priority first on a conflict."""
    raw: list[tuple[int, int, str]] = []
    active = set(types)
    for pack in packs_for(None, packs):
        for rule in _RULES[pack]:
            if rule.type not in active:
                continue
            for m in rule.pattern.finditer(text):
                if rule.check is None or rule.check(m, text):
                    raw.append((m.start(), m.end(), rule.type))
    raw.sort(key=lambda s: (s[0], _PRIORITY[s[2]], -(s[1] - s[0])))
    chosen: list[tuple[int, int, str]] = []
    last_end = -1
    for start, end, ptype in raw:
        if start >= last_end:
            chosen.append((start, end, ptype))
            last_end = end
    return chosen


def mask(
    text: str, types: Iterable[str] | None = None, packs: Sequence[str] = DEFAULT_PACKS
) -> tuple[str, dict[str, int]]:
    """`text` with its personal data redacted, and a count per type. Non-string or empty input
    is returned unchanged."""
    if not text or not isinstance(text, str):
        return text, {}
    wanted = _normalize_types(types)
    spans = _collect_spans(text, wanted, packs) if wanted else []
    if not spans:
        return text, {}
    counts: dict[str, int] = {}
    out: list[str] = []
    cursor = 0
    for start, end, ptype in spans:
        out.append(text[cursor:start])
        out.append(_REDACTION.format(type=ptype))
        counts[ptype] = counts.get(ptype, 0) + 1
        cursor = end
    out.append(text[cursor:])
    return "".join(out), counts


def mask_deep(
    value: Any, types: Iterable[str] | None = None, packs: Sequence[str] = DEFAULT_PACKS
) -> tuple[Any, dict[str, int]]:
    """Every string inside `value` (dicts, lists, tuples) redacted, as a copy, with the counts."""
    wanted = _normalize_types(types)
    counts: dict[str, int] = {}

    def walk(v: Any) -> Any:
        if isinstance(v, str):
            masked, c = mask(v, wanted, packs)
            for k, n in c.items():
                counts[k] = counts.get(k, 0) + n
            return masked
        if isinstance(v, dict):
            return {k: walk(x) for k, x in v.items()}
        if isinstance(v, list):
            return [walk(x) for x in v]
        if isinstance(v, tuple):
            return tuple(walk(x) for x in v)
        return v

    if not wanted:
        return value, {}
    return walk(value), counts


def scan(
    text: str, types: Iterable[str] | None = None, packs: Sequence[str] = DEFAULT_PACKS
) -> dict[str, Any]:
    """Counts, total and a masked preview of the first 400 characters; never a raw value."""
    masked, counts = mask(text, types, packs)
    return {"counts": counts, "total": sum(counts.values()), "preview": masked[:400]}


def personal_share(values: Iterable[object], packs: Sequence[str] = DEFAULT_PACKS) -> float:
    """The share of the non-empty `values` that hold personal data (0 when there are none)."""
    texts = [str(v) for v in values if v is not None and str(v).strip()]
    if not texts:
        return 0.0
    return sum(1 for t in texts if scan(t, packs=packs)["total"]) / len(texts)


# The catalogue's pii_label (catalog.column_meta) for each type.
_LABELS = {
    "CCCD": "national_id",
    "CMND": "national_id",
    "SSN": "national_id",
    "PHONE": "phone",
    "EMAIL": "email",
    "CREDIT_CARD": "account_no",
    "IBAN": "account_no",
}


def column_label(values: Iterable[object], packs: Sequence[str] = DEFAULT_PACKS) -> str | None:
    """The pii_label of the type most of `values` hold, "other" for an IP; None when none do."""
    counts: dict[str, int] = {}
    for v in values:
        if v is None or not str(v).strip():
            continue
        for ptype, n in scan(str(v), packs=packs)["counts"].items():
            counts[ptype] = counts.get(ptype, 0) + n
    if not counts:
        return None
    return _LABELS.get(max(counts, key=lambda t: (counts[t], -_PRIORITY[t])), "other")


# RES-24: how sure a match is decides how much of a column it takes. A checked value (a card that
# passes Luhn, an IBAN mod 97, a CCCD's province and century) still occurs by chance in numbers
# that are not personal (one random 16-digit number in ten passes Luhn), so it is never one hit:
# a share of the column, and that many distinct values. An e-mail address is unmistakable; a phone,
# a CMND or an IP address is weakest.
_STRONG = frozenset({"EMAIL"})
_CHECKED = frozenset({"CREDIT_CARD", "IBAN", "CCCD", "SSN"})
_SHARE = {"strong": 0.2, "checked": 0.3, "weak": 0.5}
_MIN_DISTINCT = {"strong": 2, "checked": 3, "weak": 3}


def _strength(ptype: str) -> str:
    return "strong" if ptype in _STRONG else "checked" if ptype in _CHECKED else "weak"


@dataclass(frozen=True)
class ColumnDecision:
    """Whether a column holds personal data, from a sample of its values, and the evidence: the
    type found most, its share of the values and how many distinct values carried it."""

    label: str | None
    pii_type: str | None
    share: float
    distinct_hits: int


def column_decision(
    values: Iterable[object], packs: Sequence[str] = DEFAULT_PACKS
) -> ColumnDecision:
    texts = [str(v) for v in values if v is not None and str(v).strip()]
    if len(set(texts)) < 2:
        return ColumnDecision(None, None, 0.0, 0)
    hits: dict[str, set[str]] = {}
    for t in texts:
        for ptype in scan(t, packs=packs)["counts"]:
            hits.setdefault(ptype, set()).add(t)
    best: ColumnDecision | None = None
    for ptype, carried in hits.items():
        share = sum(1 for t in texts if t in carried) / len(texts)
        strength = _strength(ptype)
        # A column with fewer distinct values than the bar needs them all to carry the type.
        enough = min(_MIN_DISTINCT[strength], len(set(texts)))
        if share >= _SHARE[strength] and len(carried) >= enough:
            found = ColumnDecision(
                _LABELS.get(ptype, "other"), ptype, round(share, 4), len(carried)
            )
            if best is None or (found.share, -_PRIORITY[ptype]) > (
                best.share,
                -_PRIORITY[best.pii_type or ptype],
            ):
                best = found
    return best or ColumnDecision(None, None, 0.0, 0)
