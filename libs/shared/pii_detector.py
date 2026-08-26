"""PII detection & masking — pure, dependency-free utility.

Lives in ``libs/shared`` (a leaf package) so every service can reuse it:
``auth-api`` imports it as ``shared.pii_detector`` (the service mounts
``libs/shared`` under the bare ``shared`` name), the KB ingestion worker as
``libs.shared.pii_detector``.

The detector is intentionally regex-based (no Presidio/spaCy dependency) and
tuned for the Vietnamese market: Vietnamese phone numbers and the 9-digit CMND /
12-digit CCCD national-ID formats are first-class, alongside the usual
email / credit-card / IP patterns.

Two entry points:
  * ``mask(text, types)``  -> (masked_text, {type: count}) — used at ingestion
    time to redact chunk text before it is embedded or persisted.
  * ``scan(text, types)``  -> {"counts": {type: n}, "total": n, "preview": str}
    — used by the compliance API to preview what *would* be redacted. It never
    echoes the raw matched value back, only a masked preview.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional

# Canonical PII type identifiers. Order here is also the tie-break priority when
# two patterns overlap on the same span (earlier = higher priority).
PII_TYPES: tuple[str, ...] = (
    "EMAIL",
    "CREDIT_CARD",
    "CCCD",
    "CMND",
    "PHONE",
    "IP",
)

_REDACTION = "[REDACTED:{type}]"

# ── Patterns ──────────────────────────────────────────────────────────────────
# Each entry: (type, compiled regex). The regexes are deliberately anchored on
# word boundaries so they do not chew into surrounding tokens. Digit groups may
# be separated by space / dot / hyphen (common in the wild).
_EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"
)
# Credit card: 13–19 digits, optionally grouped by space/hyphen. Validated with
# the Luhn checksum below to keep false positives (e.g. long ID runs) out.
_CARD_RE = re.compile(r"\b\d(?:[ \-]?\d){12,18}\b")
# Vietnamese national IDs. CCCD = 12 digits, CMND (older) = 9 digits.
_CCCD_RE = re.compile(r"\b\d{12}\b")
_CMND_RE = re.compile(r"\b\d{9}\b")
# Vietnamese phone: optional +84 / 0084 / leading 0, then 9 subscriber digits,
# tolerant of space/dot/hyphen separators.
_PHONE_RE = re.compile(
    r"(?<![\w])(?:\+84|0084|0)(?:[ .\-]?\d){9}(?![\w])"
)
_IPV4_RE = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b"
)

_PATTERNS: tuple[tuple[str, "re.Pattern[str]"], ...] = (
    ("EMAIL", _EMAIL_RE),
    ("CREDIT_CARD", _CARD_RE),
    ("CCCD", _CCCD_RE),
    ("CMND", _CMND_RE),
    ("PHONE", _PHONE_RE),
    ("IP", _IPV4_RE),
)

_PRIORITY = {t: i for i, t in enumerate(PII_TYPES)}


def _luhn_ok(digits: str) -> bool:
    """Luhn checksum — used to gate CREDIT_CARD matches so arbitrary long digit
    runs are not misreported as card numbers."""
    total = 0
    parity = len(digits) % 2
    for i, ch in enumerate(digits):
        d = ord(ch) - 48
        if d < 0 or d > 9:
            return False
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _normalize_types(types: Optional[Iterable[str]]) -> tuple[str, ...]:
    if types is None:
        return PII_TYPES
    wanted = {t.strip().upper() for t in types if t and t.strip()}
    # Preserve canonical ordering; ignore unknown labels.
    return tuple(t for t in PII_TYPES if t in wanted)


def _collect_spans(text: str, types: tuple[str, ...]) -> list[tuple[int, int, str]]:
    """Return non-overlapping (start, end, type) spans, highest priority first
    on conflict, left-to-right otherwise."""
    raw: list[tuple[int, int, str]] = []
    active = set(types)
    for ptype, pattern in _PATTERNS:
        if ptype not in active:
            continue
        for m in pattern.finditer(text):
            if ptype == "CREDIT_CARD":
                digits = re.sub(r"\D", "", m.group(0))
                if not (13 <= len(digits) <= 19) or not _luhn_ok(digits):
                    continue
            raw.append((m.start(), m.end(), ptype))

    # Resolve overlaps: sort by start, then by type priority, then by longer span.
    raw.sort(key=lambda s: (s[0], _PRIORITY[s[2]], -(s[1] - s[0])))
    chosen: list[tuple[int, int, str]] = []
    last_end = -1
    for start, end, ptype in raw:
        if start >= last_end:
            chosen.append((start, end, ptype))
            last_end = end
    return chosen


def mask(text: str, types: Optional[Iterable[str]] = None) -> tuple[str, dict[str, int]]:
    """Redact PII in ``text``. Returns the masked text and a per-type hit count.

    ``types`` restricts which categories are redacted (default: all). Unknown
    labels are ignored. Non-string / empty input is returned unchanged.
    """
    if not text or not isinstance(text, str):
        return text, {}
    wanted = _normalize_types(types)
    if not wanted:
        return text, {}

    spans = _collect_spans(text, wanted)
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


def mask_deep(value: Any, types: Optional[Iterable[str]] = None) -> tuple[Any, dict[str, int]]:
    """Recursively redact PII in every string reached inside ``value`` (nested
    dicts / lists / tuples). Non-string leaves (ints, bools, None) are returned
    unchanged. Returns a masked *copy* plus aggregate per-type counts.

    Used to redact structured chunk payloads at ingestion — table cells,
    extracted entity names/descriptions, relationship descriptions — so PII does
    not survive in the graph, Qdrant payload or Postgres tables.
    """
    wanted = _normalize_types(types)
    counts: dict[str, int] = {}

    def _walk(v: Any) -> Any:
        if isinstance(v, str):
            masked, c = mask(v, wanted)
            for k, n in c.items():
                counts[k] = counts.get(k, 0) + n
            return masked
        if isinstance(v, dict):
            return {k: _walk(x) for k, x in v.items()}
        if isinstance(v, list):
            return [_walk(x) for x in v]
        if isinstance(v, tuple):
            return tuple(_walk(x) for x in v)
        return v

    if not wanted:
        return value, {}
    return _walk(value), counts


def scan(text: str, types: Optional[Iterable[str]] = None) -> dict[str, Any]:
    """Detect (but do not persist) PII. Returns counts, total, and a masked
    preview of the first ~400 chars. Never returns the raw matched values."""
    masked, counts = mask(text, types)
    total = sum(counts.values())
    return {
        "counts": counts,
        "total": total,
        "preview": masked[:400],
    }
