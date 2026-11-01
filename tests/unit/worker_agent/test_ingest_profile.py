"""An ingest profile and the levels it inherits (libs/tabular/profile.py, GEN-11)."""

from __future__ import annotations

import pytest

from libs.tabular import profile

pytestmark = [pytest.mark.unit]


def test_what_no_level_says_follows_from_the_organizations_locale() -> None:
    vi = profile.resolve("vi-VN")
    assert (vi.decimal_mark, vi.date_order, vi.ocr_languages) == (",", "dmy", ("vie", "eng"))
    us = profile.resolve("en-US")
    assert (us.decimal_mark, us.date_order, us.ocr_languages) == (".", "mdy", ("eng",))
    assert profile.resolve("en-GB").date_order == "dmy"
    assert profile.resolve("de-DE").ocr_languages == ("deu", "eng")
    assert profile.resolve(None).locale == profile.FALLBACK_LOCALE


def test_a_lower_level_overrides_only_the_keys_it_names() -> None:
    space = {"locale": "en-US"}
    collection = {"ocr_languages": ["jpn"]}
    resolved = profile.resolve("vi-VN", space, collection)
    assert resolved.locale == "en-US" and resolved.decimal_mark == "."
    assert resolved.ocr_languages == ("jpn",)
    assert profile.resolve("vi-VN", {"decimal_mark": "."}, {}).decimal_mark == "."


def test_accept_language_names_the_locale_then_its_language_then_english() -> None:
    assert profile.resolve("vi-VN").accept_language == "vi-VN,vi;q=0.9,en;q=0.8"
    assert profile.resolve("en").accept_language == "en"


@pytest.mark.parametrize(
    ("raw", "why"),
    [
        ([], "object"),
        ({"currency": "VND"}, "unknown keys"),
        ({"locale": "Vietnamese"}, "BCP 47"),
        ({"decimal_mark": ";"}, "decimal_mark"),
        ({"date_order": "ymd"}, "date_order"),
        ({"ocr_languages": []}, "tesseract"),
        ({"ocr_languages": ["vi"]}, "tesseract"),
    ],
)
def test_a_malformed_profile_is_refused(raw: object, why: str) -> None:
    with pytest.raises(ValueError, match=why):
        profile.validate(raw)
