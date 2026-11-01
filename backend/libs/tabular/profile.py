"""How a Data Space reads what it ingests: an ingest profile and the levels it inherits (GEN-11).

org.data_space, document.collection and acquire.crawl_source each hold an `ingest_profile`
object. A key a level leaves out comes from the level above: a collection or crawl source, then
its Data Space, then the organization's locale (organization.locale), whose language and region
decide the rest. So an English-language Data Space says `{"locale": "en-US"}` once, and one
collection of Japanese scans adds `{"ocr_languages": ["jpn"]}`.

Only keys a reader uses are accepted: decimal_mark and date_order (uploads, crawled figures),
ocr_languages (worker-document's OCR), locale (and the Accept-Language a crawl sends), pii_packs
(the country packs personal data is found with, libs/pii, GEN-14; else the locale's region).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from libs import pii
from libs.tabular import DateOrder

# When neither a level nor the organization names a locale: the market the product ships to.
FALLBACK_LOCALE = "vi-VN"
KEYS = frozenset({"locale", "decimal_mark", "date_order", "ocr_languages", "pii_packs"})

_LOCALE = re.compile(r"^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")
_TESSERACT = re.compile(r"^[a-z]{3}(?:_[a-z]+)?$")
# Decimal comma by language (CLDR): the rest write a point.
_DECIMAL_COMMA = frozenset(
    {"vi", "de", "fr", "es", "it", "pt", "nl", "ru", "pl", "tr", "id", "da", "sv", "nb", "fi", "cs"}
)
# Month first by locale; everyone else writes the day first in a numeric date.
_MONTH_FIRST = frozenset({"en-US", "en-PH", "en-CA"})
_OCR = {
    "vi": "vie",
    "en": "eng",
    "de": "deu",
    "fr": "fra",
    "es": "spa",
    "pt": "por",
    "it": "ita",
    "nl": "nld",
    "ru": "rus",
    "ja": "jpn",
    "ko": "kor",
    "zh": "chi_sim",
    "th": "tha",
    "id": "ind",
}


@dataclass(frozen=True)
class Profile:
    locale: str
    decimal_mark: str
    date_order: DateOrder
    ocr_languages: tuple[str, ...]
    # The libs/pii packs a reader finds personal data with, GLOBAL included.
    pii_packs: tuple[str, ...] = pii.DEFAULT_PACKS

    @property
    def accept_language(self) -> str:
        """The Accept-Language a crawl sends: the locale, its language, then English."""
        language = self.locale.split("-")[0]
        tags = list(dict.fromkeys([self.locale, language, "en"]))
        return ",".join(t if i == 0 else f"{t};q={1 - i / 10:.1f}" for i, t in enumerate(tags))


def validate(raw: Any) -> dict[str, Any]:
    """The profile as stored, with only its known keys; ValueError says what is wrong."""
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ValueError("ingest_profile is an object")
    unknown = set(raw) - KEYS
    if unknown:
        raise ValueError(f"ingest_profile has unknown keys {sorted(unknown)}")
    out: dict[str, Any] = {}
    if (locale := raw.get("locale")) is not None:
        if not isinstance(locale, str) or not _LOCALE.fullmatch(locale):
            raise ValueError("locale is a BCP 47 tag such as vi-VN or en-US")
        out["locale"] = locale
    if (mark := raw.get("decimal_mark")) is not None:
        if mark not in (".", ","):
            raise ValueError("decimal_mark is '.' or ','")
        out["decimal_mark"] = mark
    if (order := raw.get("date_order")) is not None:
        if order not in ("dmy", "mdy"):
            raise ValueError("date_order is dmy or mdy")
        out["date_order"] = order
    if (languages := raw.get("ocr_languages")) is not None:
        if (
            not isinstance(languages, list)
            or not 1 <= len(languages) <= 5
            or not all(isinstance(x, str) and _TESSERACT.fullmatch(x) for x in languages)
        ):
            raise ValueError("ocr_languages is 1 to 5 tesseract codes such as vie, eng, jpn")
        out["ocr_languages"] = list(dict.fromkeys(languages))
    if (packs := raw.get("pii_packs")) is not None:
        if (
            not isinstance(packs, list)
            or not 1 <= len(packs) <= len(pii.PACKS)
            or not all(isinstance(p, str) and p.upper() in pii.PACKS for p in packs)
        ):
            raise ValueError(f"pii_packs is a list of {', '.join(pii.PACKS)}")
        out["pii_packs"] = list(dict.fromkeys(p.upper() for p in packs))
    return out


def resolve(org_locale: str | None, *levels: Mapping[str, Any] | None) -> Profile:
    """The profile a reader uses: `levels` from the top (Data Space) down (collection or crawl
    source), each overriding the one before; what none says follows from the locale."""
    merged: dict[str, Any] = {}
    for level in levels:
        if level:
            merged.update(validate(level))
    locale = merged.get("locale") or org_locale or FALLBACK_LOCALE
    language = locale.split("-")[0].lower()
    ocr = [_OCR[language]] if language in _OCR else []
    return Profile(
        locale=locale,
        decimal_mark=merged.get("decimal_mark") or ("," if language in _DECIMAL_COMMA else "."),
        date_order="mdy"
        if merged.get("date_order", "mdy" if locale in _MONTH_FIRST else "dmy") == "mdy"
        else "dmy",
        ocr_languages=tuple(merged.get("ocr_languages") or dict.fromkeys([*ocr, "eng"])),
        pii_packs=pii.packs_for(locale, merged.get("pii_packs")),
    )


async def load(session: Any, data_space_id: Any, *levels: Mapping[str, Any] | None) -> Profile:
    """The profile for a Data Space's reader, read in `session` (any role with org_read): the
    organization's locale and the space's own profile, then `levels` below it."""
    from sqlalchemy import select

    from libs.dataagent_db.models import org

    found = (
        await session.execute(
            select(org.Organization.locale, org.DataSpace.ingest_profile)
            .join(org.DataSpace, org.DataSpace.org_id == org.Organization.id)
            .where(org.DataSpace.id == data_space_id)
        )
    ).first()
    locale, space = (found[0], found[1]) if found is not None else (None, None)
    return resolve(locale, space, *levels)
