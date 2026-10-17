"""The semantic compiler turns a metric version and a question's cut into one SQL statement."""

from __future__ import annotations

import datetime
import uuid

import pytest
import sqlglot

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

pytestmark = [pytest.mark.unit]

LOANS, BRANCHES = uuid.uuid4(), uuid.uuid4()
LOAN_ENTITY, BRANCH_ENTITY = uuid.uuid4(), uuid.uuid4()
REGION = Dimension(uuid.uuid4(), "vung", "Vùng", "region_name", BRANCH_ENTITY)
DAY = Dimension(uuid.uuid4(), "ngay", "Ngày", "as_of_date", LOAN_ENTITY, is_time=True)
PRODUCT = Dimension(uuid.uuid4(), "san_pham", "Sản phẩm", "product_code", LOAN_ENTITY)

LAYER = Layer(
    measures={
        "no_xau": Measure(
            "no_xau", "SUM(CASE WHEN group_code >= 3 THEN balance ELSE 0 END)", LOAN_ENTITY
        ),
        "du_no": Measure("du_no", "SUM(balance)", LOAN_ENTITY),
    },
    dimensions={d.code: d for d in (REGION, DAY, PRODUCT)},
    entity_tables={
        LOAN_ENTITY: Table(LOANS, "dw", "loan_balance"),
        BRANCH_ENTITY: Table(BRANCHES, "dw", "branch"),
    },
    joins=(Join(LOANS, ("branch_code",), BRANCHES, ("code",)),),
)


def metric(**over: object) -> MetricVersion:
    base: dict[str, object] = {
        "id": uuid.uuid4(),
        "metric_code": "NPL_RATIO",
        "display_name": "Tỷ lệ nợ xấu",
        "expression": "no_xau / du_no * 100",
        "filters": ({"dimension": "san_pham", "op": "!=", "values": ["NOI_BO"]},),
        "unit": "%",
        "decimals": 2,
        "period_semantics": "end_of_period",
        "allowed_dimension_ids": frozenset({REGION.id, PRODUCT.id}),
        "certified": True,
    }
    base.update(over)
    return MetricVersion(**base)  # type: ignore[arg-type]


def test_a_ratio_cut_by_region_at_the_end_of_a_month() -> None:
    out = compile_metric(
        Request(
            metric(),
            group_by=["vung"],
            period=Period(datetime.date(2026, 9, 1), datetime.date(2026, 10, 1)),
        ),
        LAYER,
        dialect="postgres",
    )
    tree = sqlglot.parse_one(out.sql, read="postgres")
    assert out.columns == ("vung", "value")
    assert '"dw"."loan_balance" AS t0' in out.sql and 'LEFT JOIN "dw"."branch" AS t1' in out.sql
    # The measures replaced the codes; the internal-loan filter and the last-day rule are there.
    assert "SUM(balance)" in out.sql.replace("t0.", "") and "no_xau" not in out.sql
    assert "NOT" in out.sql and "'NOI_BO'" in out.sql and "MAX(" in out.sql
    assert tree.args.get("group") is not None
    assert len(out.sql_sha256) == 64 and set(out.dataset_ids) == {LOANS, BRANCHES}


def test_a_flow_metric_sums_over_the_period() -> None:
    out = compile_metric(
        Request(
            metric(period_semantics="sum_over_period"),
            period=Period(datetime.date(2026, 1, 1), datetime.date(2026, 4, 1)),
        ),
        LAYER,
        dialect="postgres",
    )
    assert "MAX(" not in out.sql and ">= CAST('2026-01-01' AS DATE)" in out.sql


MONTH_TEXT = Dimension(
    uuid.uuid4(), "thang", "Tháng", "month", LOAN_ENTITY, is_time=True, value_type="string"
)
TEXT_TIME_LAYER = Layer(
    measures=LAYER.measures,
    dimensions={d.code: d for d in (REGION, MONTH_TEXT, PRODUCT)},
    entity_tables=LAYER.entity_tables,
    joins=LAYER.joins,
)


def test_a_time_column_stored_as_text_is_compared_as_text() -> None:
    # An ingested '2026-10' cannot be compared with a DATE (text >= date has no operator).
    out = compile_metric(
        Request(
            metric(period_semantics="sum_over_period", filters=()),
            period=Period(datetime.date(2026, 10, 1), datetime.date(2026, 11, 1)),
        ),
        TEXT_TIME_LAYER,
        dialect="postgres",
    )
    assert "AS DATE" not in out.sql
    assert "t0.month >= '2026-10'" in out.sql and "t0.month < '2026-11'" in out.sql


def test_a_text_time_column_is_not_widened_past_the_period() -> None:
    # Six days of October must not match the whole month '2026-10': both bounds keep the day.
    out = compile_metric(
        Request(
            metric(period_semantics="sum_over_period", filters=()),
            period=Period(datetime.date(2026, 10, 1), datetime.date(2026, 10, 7)),
        ),
        TEXT_TIME_LAYER,
        dialect="postgres",
    )
    assert "t0.month >= '2026-10-01'" in out.sql and "t0.month < '2026-10-07'" in out.sql


def test_a_text_time_column_aligned_on_years_compares_years() -> None:
    out = compile_metric(
        Request(
            metric(period_semantics="end_of_period", filters=()),
            period=Period(datetime.date(2026, 1, 1), datetime.date(2027, 1, 1)),
        ),
        TEXT_TIME_LAYER,
        dialect="postgres",
    )
    assert "AS DATE" not in out.sql and ">= '2026'" in out.sql and "< '2027'" in out.sql


def test_a_balance_without_a_period_is_read_on_the_last_day_with_data() -> None:
    # "the latest data" for a price or balance is its last day, never the max over all history.
    out = compile_metric(Request(metric(filters=())), LAYER, dialect="postgres")
    assert "t0.as_of_date = ((SELECT MAX(l.as_of_date) FROM" in out.sql
    assert "AS DATE" not in out.sql  # no range: the whole table's last day


def test_a_flow_without_a_period_is_not_cut_to_a_day() -> None:
    out = compile_metric(
        Request(metric(period_semantics="sum_over_period", filters=())), LAYER, dialect="postgres"
    )
    assert "MAX(" not in out.sql and "WHERE" not in out.sql


def test_a_filter_value_is_quoted_never_spliced() -> None:
    out = compile_metric(
        Request(metric(), filters={"vung": ["Miền Bắc'; DROP TABLE x; --"]}),
        LAYER,
        dialect="postgres",
    )
    assert "'Miền Bắc''; DROP TABLE x; --'" in out.sql
    assert len(sqlglot.parse(out.sql, read="postgres")) == 1


@pytest.mark.parametrize(
    ("request_", "code"),
    [
        # BR-81 holds for filters too, and an empty list allows no cut at all.
        (
            Request(
                metric(allowed_dimension_ids=frozenset({PRODUCT.id})), filters={"vung": ["MB"]}
            ),
            "CONTRACT_MISMATCH",
        ),
        (
            Request(metric(allowed_dimension_ids=frozenset()), group_by=["san_pham"]),
            "CONTRACT_MISMATCH",
        ),
        (
            Request(metric(allowed_dimension_ids=frozenset({PRODUCT.id})), group_by=["vung"]),
            "CONTRACT_MISMATCH",
        ),
        (
            Request(
                metric(expression="no_xau / khong_co"),
            ),
            "CONTRACT_MISMATCH",
        ),
        (Request(metric(), group_by=["khong_co"]), "UNRESOLVED_ENTITY"),
        (Request(metric(), group_by=["ngay"], grain="week"), "CONTRACT_MISMATCH"),
    ],
)
def test_what_cannot_be_compiled_says_why(request_: Request, code: str) -> None:
    with pytest.raises(CompileError) as caught:
        compile_metric(request_, LAYER, dialect="postgres")
    assert caught.value.code == code


def test_no_approved_join_is_no_join_path() -> None:
    layer = Layer(LAYER.measures, LAYER.dimensions, LAYER.entity_tables, joins=())
    with pytest.raises(CompileError) as caught:
        compile_metric(Request(metric(), group_by=["vung"]), layer, dialect="postgres")
    assert caught.value.code == "NO_JOIN_PATH"


def test_a_filter_on_another_tables_dimension_joins_it() -> None:
    out = compile_metric(
        Request(metric(), filters={"vung": ["Miền Bắc"]}), LAYER, dialect="postgres"
    )
    assert 'LEFT JOIN "dw"."branch" AS t1' in out.sql and "t1.region_name IN" in out.sql
