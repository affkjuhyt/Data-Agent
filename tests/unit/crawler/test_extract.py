"""Records out of a stored page by its schema version: Vietnamese numbers, reasons for review and
the random sample (UC-14-02, UC-14-04)."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from services.crawler import extract

pytestmark = [pytest.mark.unit]

FIELDS = [
    {"name": "bank", "type": "text", "required": True, "key": True, "locator": "td.bank"},
    {
        "name": "rate_12m",
        "type": "percent",
        "required": True,
        "locator": "td.r12",
        "value_constraints": {"min": 0, "max": 15},
    },
    {"name": "updated", "type": "date", "required": False, "locator": "td.day"},
]

PAGE = """<html><body><table class="lai-suat">
<tr><th>Ngân hàng</th><th>12 tháng</th><th>Ngày</th></tr>
<tr><td class="bank">Ngân hàng A</td><td class="r12">4,80%</td><td class="day">10/10/2026</td></tr>
<tr><td class="bank">Ngân hàng B</td><td class="r12">48</td><td class="day">10/10/2026</td></tr>
<tr><td class="bank">Ngân hàng C</td><td class="r12"></td><td class="day">10/10/2026</td></tr>
</table></body></html>""".encode()


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("4,8", Decimal("4.8")),
        ("4.8 %/năm", Decimal("4.8")),
        ("1.234,5", Decimal("1234.5")),
        ("1,234.5", Decimal("1234.5")),
        ("1.234.567", Decimal("1234567")),
        ("không có", None),
    ],
)
def test_numbers_read_as_vietnamese_and_english_pages_write_them(
    text: str, value: Decimal | None
) -> None:
    assert extract.number(text) == value


def test_an_html_table_gives_one_record_per_row_with_its_reasons() -> None:
    records = extract.extract(PAGE, "text/html; charset=utf-8", "table.lai-suat tr", FIELDS)
    # The header row has no key cell: it is not a record of anything.
    assert [r.entity_key for r in records] == ["Ngân hàng A", "Ngân hàng B", "Ngân hàng C"]
    a, b, c = records
    assert a.values == {"bank": "Ngân hàng A", "rate_12m": "4.80", "updated": "2026-10-10"}
    assert a.review_reasons == [] and a.record_confidence == 1.0
    # AC-14-04-02: 48 %/năm outside 0-15 goes to review as out_of_domain.
    assert b.review_reasons == ["out_of_domain"]
    assert c.values["rate_12m"] is None
    assert c.review_reasons == ["low_confidence", "missing_required"]


def test_xpath_selectors_and_locators_work_like_css() -> None:
    fields = [
        {"name": "bank", "type": "text", "key": True, "locator": "/td[1]"},
        {"name": "rate_12m", "type": "percent", "locator": "/td[2]"},
    ]
    records = extract.extract(PAGE, "text/html", "//table[@class='lai-suat']/tr[td]", fields)
    assert [(r.entity_key, r.values["rate_12m"]) for r in records][:1] == [("Ngân hàng A", "4.80")]


def test_an_api_answer_is_read_by_json_path() -> None:
    body = json.dumps(
        {"data": {"rows": [{"bank": "A", "r": {"m12": 4.8}}, {"bank": "B", "r": {}}]}}
    )
    fields = [
        {"name": "bank", "type": "text", "key": True, "required": True, "locator": "bank"},
        {"name": "rate_12m", "type": "percent", "required": True, "locator": "r.m12"},
    ]
    records = extract.extract(body.encode(), "application/json", "$.data.rows[*]", fields)
    assert [(r.entity_key, r.values["rate_12m"]) for r in records] == [("A", "4.8"), ("B", None)]
    assert records[1].review_reasons == ["low_confidence", "missing_required"]


def test_a_page_of_another_kind_is_refused() -> None:
    with pytest.raises(extract.ExtractionError):
        extract.extract(b"%PDF-1.7", "application/pdf", "table tr", FIELDS)
    with pytest.raises(extract.ExtractionError):
        extract.extract(PAGE, "text/html", "table[", FIELDS)


def _records(clean: int, flagged: int) -> list[extract.Record]:
    return [
        extract.Record(str(i), {}, {}, 1.0, ["low_confidence"] if i < flagged else [])
        for i in range(clean + flagged)
    ]


@pytest.mark.req("AC-14-04-03")
def test_the_random_sample_is_two_percent_of_the_clean_records_and_at_least_five() -> None:
    big = _records(500, 20)
    assert len(extract.sample(big, "run-1")) == 10
    assert sum(r.review_reasons == ["random_sample"] for r in big) == 10
    small = _records(120, 0)
    assert len(extract.sample(small, "run-2")) == 5
    # The same run draws the same sample: a retried claim does not reshuffle the queue.
    again = _records(120, 0)
    extract.sample(again, "run-2")
    assert [r.review_reasons for r in again] == [r.review_reasons for r in small]
