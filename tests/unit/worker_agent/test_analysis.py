"""Share of total and accumulations over a metric (semantic/analysis.py), run on sqlite so the
figures are checked, not only the SQL's shape; and the words that ask for them
(understand/analysis.py)."""

from __future__ import annotations

import datetime
import sqlite3
import uuid
from collections.abc import Iterator

import pytest

from services.worker_agent.semantic.analysis import (
    RUNNING_TOTAL,
    SHARE_OF_TOTAL,
    compile_running,
    compile_share,
    is_additive,
    to_date,
)
from services.worker_agent.semantic.compiler import (
    CompileError,
    Dimension,
    Join,
    Layer,
    Measure,
    MetricVersion,
    Period,
    Request,
    Table,
    compile_metric,
)
from services.worker_agent.understand.analysis import TO_DATE, find_analysis

pytestmark = [pytest.mark.unit]

SALES, BRANCHES = uuid.uuid4(), uuid.uuid4()
SALE, BRANCH = uuid.uuid4(), uuid.uuid4()
REGION = Dimension(uuid.uuid4(), "vung", "Vùng", "region", BRANCH)
CATEGORY = Dimension(uuid.uuid4(), "nhom", "Nhóm hàng", "category", SALE)
DAY = Dimension(uuid.uuid4(), "ngay", "Ngày", "sold_on", SALE, is_time=True, value_type="string")

LAYER = Layer(
    measures={
        "doanh_thu": Measure("doanh_thu", "SUM(amount)", SALE),
        "so_don": Measure("so_don", "COUNT(order_id)", SALE),
        "so_khach": Measure("so_khach", "COUNT(DISTINCT customer)", SALE),
    },
    dimensions={d.code: d for d in (REGION, CATEGORY, DAY)},
    entity_tables={SALE: Table(SALES, "dw", "sales"), BRANCH: Table(BRANCHES, "dw", "branch")},
    joins=(Join(SALES, ("branch",), BRANCHES, ("code",)),),
)

# Two regions, two categories, one sale each in Nov and Dec 2025 and in January to March 2026.
ROWS = [
    (f"O{i}", day, branch, category, amount, f"K{i % 3}")
    for i, (day, branch, category, amount) in enumerate(
        [
            ("2025-11-10", "HN", "A", 100),
            ("2025-12-10", "HN", "A", 50),
            ("2026-01-05", "HN", "A", 10),
            ("2026-01-20", "HCM", "B", 30),
            ("2026-02-03", "HN", "B", 20),
            ("2026-02-14", "HCM", "A", 40),
            ("2026-03-09", "HN", "A", 60),
            ("2026-03-30", "HCM", "B", 40),
        ]
    )
]


@pytest.fixture
def db() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(":memory:")
    conn.execute("ATTACH DATABASE ':memory:' AS dw")
    conn.execute(
        "CREATE TABLE dw.sales (order_id TEXT, sold_on TEXT, branch TEXT, category TEXT,"
        " amount INTEGER, customer TEXT)"
    )
    conn.executemany("INSERT INTO dw.sales VALUES (?, ?, ?, ?, ?, ?)", ROWS)
    conn.execute("CREATE TABLE dw.branch (code TEXT, region TEXT)")
    conn.executemany("INSERT INTO dw.branch VALUES (?, ?)", [("HN", "Bắc"), ("HCM", "Nam")])
    yield conn
    conn.close()


def metric(expression: str = "doanh_thu", **over: object) -> MetricVersion:
    base: dict[str, object] = {
        "id": uuid.uuid4(),
        "metric_code": "DOANH_THU",
        "display_name": "Doanh thu",
        "expression": expression,
        "filters": (),
        "unit": "đ",
        "decimals": 0,
        "period_semantics": "sum_over_period",
        "allowed_dimension_ids": frozenset({REGION.id, CATEGORY.id}),
        "certified": True,
    }
    base.update(over)
    return MetricVersion(**base)  # type: ignore[arg-type]


Q1 = Period(datetime.date(2026, 1, 1), datetime.date(2026, 4, 1))
MARCH = Period(datetime.date(2026, 3, 1), datetime.date(2026, 4, 1))


def rows(db: sqlite3.Connection, sql: str) -> list[tuple[object, ...]]:
    return [tuple(r) for r in db.execute(sql).fetchall()]


def test_a_share_is_of_the_members_total_and_the_value_is_the_plain_questions(
    db: sqlite3.Connection,
) -> None:
    request = Request(metric(), group_by=["vung"], period=Q1)
    out = compile_share(request, LAYER, dialect="sqlite")
    assert out.columns == ("vung", "value", "share")
    got = rows(db, out.sql)
    # Bắc 10+20+60 = 90, Nam 30+40+40 = 110, of 200.
    assert got == [("Bắc", 90, 45.0), ("Nam", 110, 55.0)]
    plain = rows(db, compile_metric(request, LAYER, dialect="sqlite").sql)
    assert [(r[0], r[1]) for r in got] == plain


def test_a_share_of_a_ranked_cut_is_of_every_members_total(db: sqlite3.Connection) -> None:
    out = compile_share(
        Request(metric(), group_by=["vung"], period=Q1, top_n=1), LAYER, dialect="sqlite"
    )
    # The top region's share of all regions, not of itself (which would be 100%).
    assert rows(db, out.sql) == [("Nam", 110, 55.0)]


def test_a_share_cut_by_month_is_of_each_months_total(db: sqlite3.Connection) -> None:
    out = compile_share(
        Request(metric(), group_by=["ngay", "nhom"], period=Q1, grain="month"),
        LAYER,
        dialect="sqlite",
    )
    got = {(r[0], r[1]): r[3] for r in rows(db, out.sql)}
    assert got[("2026-01", "A")] == pytest.approx(25) and got[("2026-01", "B")] == 75
    assert sum(v for (month, _), v in got.items() if month == "2026-02") == pytest.approx(100)


def test_a_count_adds_up_and_a_distinct_count_or_a_ratio_does_not() -> None:
    assert is_additive(metric("so_don"), LAYER)
    assert not is_additive(metric("so_khach"), LAYER)
    assert not is_additive(metric("doanh_thu / so_don"), LAYER)


@pytest.mark.parametrize(
    ("request_", "words"),
    [
        (Request(metric("doanh_thu / so_don"), group_by=["vung"], period=Q1), "không cộng được"),
        (Request(metric(), period=Q1), "Hãy nêu chiều cần chia"),
        (
            Request(metric(period_semantics="average_over_period"), group_by=["vung"], period=Q1),
            "bình quân theo ngày",
        ),
    ],
)
def test_a_share_that_would_not_be_a_share_is_refused(request_: Request, words: str) -> None:
    with pytest.raises(CompileError) as raised:
        compile_share(request_, LAYER, dialect="sqlite")
    assert raised.value.code == "CONTRACT_MISMATCH" and words in str(raised.value)


def test_a_running_total_accumulates_along_the_months_per_member(db: sqlite3.Connection) -> None:
    out = compile_running(
        Request(metric(), group_by=["vung", "ngay"], period=Q1, grain="month"),
        LAYER,
        dialect="sqlite",
    )
    assert out.columns == ("vung", "ngay", "value", "cumulative")
    got = {(r[0], r[1]): (r[2], r[3]) for r in rows(db, out.sql)}
    assert got[("Bắc", "2026-01")] == (10, 10)
    assert got[("Bắc", "2026-02")] == (20, 30)
    assert got[("Bắc", "2026-03")] == (60, 90)
    assert got[("Nam", "2026-03")] == (40, 110)


def test_a_year_to_date_cut_reads_the_year_before_the_period_and_restarts_each_year(
    db: sqlite3.Connection,
) -> None:
    # March only, accumulated from January: 10+30 + 20+40 + 60+40 = 200 by the end of March.
    out = compile_running(
        Request(metric(), group_by=["ngay"], period=MARCH, grain="month"),
        LAYER,
        dialect="sqlite",
        restart="year",
    )
    assert rows(db, out.sql) == [("2026-03", 100, 200)]
    # Across the new year the accumulation starts again in January.
    across = compile_running(
        Request(
            metric(),
            group_by=["ngay"],
            period=Period(datetime.date(2025, 12, 1), datetime.date(2026, 2, 1)),
            grain="month",
        ),
        LAYER,
        dialect="sqlite",
        restart="year",
    )
    assert rows(db, across.sql) == [("2025-12", 50, 150), ("2026-01", 40, 40)]


@pytest.mark.parametrize(
    ("request_", "restart", "words"),
    [
        (Request(metric(), group_by=["vung"], period=Q1), None, "cách chia theo thời gian"),
        (
            Request(metric(period_semantics="end_of_period"), group_by=["ngay"], period=Q1),
            None,
            "số dư hoặc bình quân",
        ),
        (Request(metric(), group_by=["ngay"], period=Q1, grain="month"), "quarter", "đầu quý"),
    ],
)
def test_a_running_total_that_has_no_meaning_is_refused(
    request_: Request, restart: str | None, words: str
) -> None:
    with pytest.raises(CompileError) as raised:
        compile_running(request_, LAYER, dialect="sqlite", restart=restart)
    assert raised.value.code == "CONTRACT_MISMATCH" and words in str(raised.value)


def test_to_date_runs_from_the_start_of_the_unit_of_the_last_day(db: sqlite3.Connection) -> None:
    september = Period(datetime.date(2026, 9, 1), datetime.date(2026, 10, 1))
    assert to_date(september, "year", metric()) == Period(datetime.date(2026, 1, 1), september.end)
    assert to_date(september, "quarter", metric()).start == datetime.date(2026, 7, 1)
    ytd = to_date(MARCH, "year", metric())
    assert rows(db, compile_metric(Request(metric(), period=ytd), LAYER, dialect="sqlite").sql) == [
        (200,)
    ]
    with pytest.raises(CompileError, match="số dư tại một ngày"):
        to_date(MARCH, "year", metric(period_semantics="end_of_period"))


@pytest.mark.parametrize(
    ("question", "kind", "unit", "rest"),
    [
        (
            "Tỷ trọng dư nợ theo vùng tháng 9/2026",
            SHARE_OF_TOTAL,
            None,
            "dư nợ theo vùng tháng 9/2026",
        ),
        ("cơ cấu doanh thu theo nhóm hàng", SHARE_OF_TOTAL, None, "doanh thu theo nhóm hàng"),
        ("phần trăm trên tổng doanh thu theo vùng", SHARE_OF_TOTAL, None, "doanh thu theo vùng"),
        ("Doanh thu luỹ kế từ đầu năm đến nay", TO_DATE, "year", "Doanh thu"),
        ("doanh thu từ đầu quý", TO_DATE, "quarter", "doanh thu"),
        ("doanh thu YTD tháng 9", TO_DATE, "year", "doanh thu tháng 9"),
        (
            "doanh thu luỹ kế theo tháng năm 2026",
            RUNNING_TOTAL,
            None,
            "doanh thu theo tháng năm 2026",
        ),
        ("số đơn cộng dồn", RUNNING_TOTAL, None, "số đơn"),
    ],
)
def test_the_words_that_ask_for_an_analysis_are_read_and_taken_out(
    question: str, kind: str, unit: str | None, rest: str
) -> None:
    found = find_analysis(question)
    assert found is not None
    assert (found.kind, found.unit, found.rest) == (kind, unit, rest)


def test_chiem_bao_nhieu_percent_is_a_weak_share_that_keeps_the_question() -> None:
    question = "Khoản vay khó đòi chiếm bao nhiêu phần trăm ở mỗi miền?"
    found = find_analysis(question)
    assert found is not None
    assert (found.kind, found.weak, found.rest) == (SHARE_OF_TOTAL, True, question)
    strong = find_analysis("Tỷ trọng dư nợ theo vùng")
    assert strong is not None and not strong.weak


def test_a_question_without_an_analysis_has_none() -> None:
    assert find_analysis("dư nợ theo vùng tháng 9/2026") is None
