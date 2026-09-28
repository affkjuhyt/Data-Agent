"""JSON and XML documents are parsed as text in Silver, not by Docling.

Real failures: a World Bank JSON response and the baochinhphu.vn RSS feed both
came back "document backend could not parse the input", because Docling reads
JSON only in its own schema and XML only in a few domain schemas.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from libs.ingestion.silver.parsing import STRUCTURED_TEXT_EXTENSIONS, _pack_blocks, _parse_structured_text

pytestmark = pytest.mark.unit

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/"><channel>
<title><![CDATA[Kinh tế - RSS Feed]]></title><link>https://baochinhphu.vn/kinh-te.htm</link>
<item><title><![CDATA[Phát triển làng nghề]]></title>
<description><![CDATA[Làng nghề được xác định là một trong những động lực]]></description>
<link><![CDATA[https://baochinhphu.vn/a.htm]]></link>
<enclosure url="https://cdn/x.jpg" length="0" type="image/jpeg"/><pubDate>9/20/2026 10:13:00 PM</pubDate></item>
<item><title><![CDATA[Forbes vinh danh]]></title><link><![CDATA[https://baochinhphu.vn/b.htm]]></link></item>
</channel></rss>""".encode()

WORLD_BANK = json.dumps([
    {"page": 1, "pages": 1, "per_page": 50, "total": 3},
    [{"indicator": {"id": "NY.GDP.MKTP.CN", "value": "GDP (current LCU)"},
      "country": {"id": "VN", "value": "Viet Nam"}, "date": "2024", "value": 12847202000000000}],
]).encode()


def _parse(raw: bytes, ext: str) -> list[dict]:
    return asyncio.run(_parse_structured_text(raw, ext))


def test_rss_items_become_readable_text() -> None:
    chunks = _parse(RSS, "xml")
    text = "\n\n".join(c["content"] for c in chunks)
    assert "title: Phát triển làng nghề" in text
    assert "link: https://baochinhphu.vn/a.htm" in text
    assert "description: Làng nghề" in text
    assert "title: Forbes vinh danh" in text
    assert "https://cdn/x.jpg" not in text  # the enclosure is not a text field


def test_json_keeps_records_and_utf8_readable() -> None:
    text = _parse(WORLD_BANK, "json")[0]["content"]
    assert '"NY.GDP.MKTP.CN"' in text and '"Viet Nam"' in text and "12847202000000000" in text


def test_chunk_shape_matches_what_silver_expects() -> None:
    chunk = _parse(RSS, "xml")[0]
    assert set(chunk) == {"block_index", "content", "table_involved", "table"}
    assert chunk["table_involved"] is False and chunk["table"] is None


def test_large_input_is_split_within_the_budget() -> None:
    big = json.dumps([{"row": i, "text": "x" * 200} for i in range(200)]).encode()
    chunks = _parse(big, "json")
    assert len(chunks) > 1
    assert all(len(c["content"]) <= 4000 for c in chunks)
    assert [c["block_index"] for c in chunks] == list(range(len(chunks)))


def test_single_oversized_line_is_hard_cut() -> None:
    chunks = _pack_blocks(["y" * 9000])
    assert [len(c["content"]) for c in chunks] == [4000, 4000, 1000]


def test_invalid_json_falls_back_to_its_text() -> None:
    assert _parse(b"not json at all", "json")[0]["content"] == "not json at all"


def test_xml_entities_are_not_expanded() -> None:
    bomb = b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "SECRET">]><rss><channel><item><title>&a;</title></item></channel></rss>'
    text = "".join(c["content"] for c in _parse(bomb, "xml"))
    assert "SECRET" not in text


def test_empty_feed_yields_no_chunks() -> None:
    assert _parse(b'<?xml version="1.0"?><rss><channel></channel></rss>', "xml") == []


def test_only_json_and_xml_are_routed_here() -> None:
    assert STRUCTURED_TEXT_EXTENSIONS == {"json", "xml"}
