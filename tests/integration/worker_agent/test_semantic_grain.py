"""Compiler 2 (R1-06) against PostgreSQL, on the bank of test_answers.py: a balance by month, an
average over a period, a metric over two tables, and "theo tháng" read off the question.

The bank's loans: 31/08/2026 one balance of 500 in the North; 30/09/2026 3.000 outside internal
lending (North 2.000, South 1.000). Revenue, monthly as text: 2026-08 40, 2026-09 220."""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from . import test_answers
from .conftest import Seed
from .test_answers import Bank, answer_of, build, drain
from .test_follow_ups import plan_of

pytestmark = [pytest.mark.integration]

warehouse = test_answers.warehouse


def add_metric(
    bank: Bank,
    code: str,
    name: str,
    expression: str,
    *,
    semantics: str,
    grain: str = "day",
    unit: str = "tỷ đồng",
) -> uuid.UUID:
    seed = bank.seed
    owner = seed.one("SELECT certified_by FROM context.metric_version LIMIT 1")
    region = seed.one(
        "SELECT id FROM context.semantic_dimension WHERE data_space_id = %s AND code = 'vung'",
        bank.space,
    )
    m = seed.row(
        "context.metric",
        org_id=bank.org,
        data_space_id=bank.space,
        code=code,
        owner_id=owner,
        current_version=1,
        status="active",
    )
    seed.row(
        "context.metric_version",
        org_id=bank.org,
        metric_id=m,
        version=1,
        display_name=name,
        expression=expression,
        unit=unit,
        decimals=0,
        period_semantics=semantics,
        time_grain=grain,
        status="certified",
        certified_by=owner,
        certified_at="2026-06-01T00:00:00Z",
        valid_from="2026-06-01T00:00:00Z",
        allowed_dimensions=[region],
        filters=json.dumps([{"dimension": "san_pham", "op": "!=", "values": ["NOI_BO"]}])
        if "du_no" in expression
        else json.dumps([]),
    )
    return m


def add_revenue(bank: Bank) -> None:
    """The monthly revenue table, its month stored as text, as a second entity."""
    seed = bank.seed
    source = seed.one("SELECT source_id FROM catalog.dataset WHERE id = %s", bank.loans)
    revenue = seed.row(
        "catalog.dataset",
        org_id=bank.org,
        data_space_id=bank.space,
        source_id=source,
        origin="source",
        schema_name="bank",
        table_name="revenue",
        history_kind="none",
        enabled=True,
    )
    for ordinal, (name, data_type, normalized) in enumerate(
        (
            ("region", "text", "string"),
            ("net_revenue", "numeric", "decimal"),
            ("month", "text", "string"),
        )
    ):
        seed.row(
            "catalog.column_meta",
            org_id=bank.org,
            dataset_id=revenue,
            dataset_version=1,
            name=name,
            ordinal=ordinal,
            data_type=data_type,
            normalized_type=normalized,
        )
    entity = seed.row(
        "context.semantic_entity",
        org_id=bank.org,
        data_space_id=bank.space,
        code="doanh_thu",
        name="Doanh thu",
        primary_dataset_id=revenue,
        key_columns=["region", "month"],
    )
    seed.row(
        "context.semantic_measure",
        org_id=bank.org,
        data_space_id=bank.space,
        entity_id=entity,
        code="net_revenue",
        name="net_revenue",
        expression="SUM(net_revenue)",
        aggregation="sum",
        additivity="full",
    )
    seed.row(
        "context.semantic_dimension",
        org_id=bank.org,
        data_space_id=bank.space,
        entity_id=entity,
        code="thang",
        name="tháng",
        expression="month",
        is_time=True,
    )


def values(figures: list[dict[str, Any]], code: str | None = None) -> dict[str, float]:
    return {
        (f["dimensions"].get(code, "") if code else ""): float(f["value"])
        for f in figures
        if "expr" not in f["source"] and f["value"] is not None
    }


async def test_a_balance_by_month_is_each_months_last_day_not_a_sum(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    q = bank.ask("Dư nợ theo tháng năm 2026")
    await drain()
    status, kind, text, figures, _, _ = answer_of(seed, q)
    assert (status, kind) == ("answered", "data"), text
    assert values(figures, "ngay") == {"2026-08": 500.0, "2026-09": 3000.0}
    chips, steps = plan_of(seed, q)
    assert ("grain", "month") in [(c["kind"], c["value"]) for c in chips]
    assert steps[0]["args"]["grain"] == "month" and steps[0]["args"]["group_by"] == ["ngay"]
    spec = seed.one("SELECT chart_spec FROM qa.answer WHERE question_id = %s", q)
    assert (spec["type"], spec["x"]) == ("line", "ngay")


async def test_a_quarter_and_a_year_are_labelled_as_periods(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    quarter = bank.ask("Dư nợ theo quý năm 2026")
    year = bank.ask("Dư nợ theo năm")
    await drain()
    assert values(answer_of(seed, quarter)[3], "ngay") == {"2026-Q3": 3000.0}
    assert values(answer_of(seed, year)[3], "ngay") == {"2026": 3000.0}


async def test_an_average_balance_is_the_mean_of_its_days(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    add_metric(bank, "DU_NO_BQ", "Dư nợ bình quân", "du_no", semantics="average_over_period")
    q = bank.ask("Dư nợ bình quân năm 2026")
    by_region = bank.ask("Dư nợ bình quân theo vùng năm 2026")
    await drain()
    status, kind, text, figures, _, _ = answer_of(seed, q)
    assert (status, kind) == ("answered", "data"), text
    # Two days with data, 500 and 3.000: the mean, neither their sum nor the last day.
    assert values(figures) == {"": 1750.0}
    # The South has data on one day only: its mean is over the days it has.
    assert values(answer_of(seed, by_region)[3], "vung") == {"Miền Bắc": 1250.0, "Miền Nam": 1000.0}


async def test_a_metric_over_two_tables_is_aggregated_per_table(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    add_revenue(bank)
    add_metric(
        bank,
        "DT_TREN_DU_NO",
        "Doanh thu trên dư nợ",
        "net_revenue / du_no * 100",
        semantics="sum_over_period",
        grain="month",
        unit="%",
    )
    q = bank.ask("Doanh thu trên dư nợ theo tháng năm 2026")
    await drain()
    status, kind, text, figures, _, _ = answer_of(seed, q)
    assert (status, kind) == ("answered", "data"), text
    # Revenue 40 / balance 500 in August, 220 / 3.000 in September: each table summed alone, then
    # divided; joined first, September's 2 revenue rows would multiply its 5 loan rows.
    # The cut is along the first measure's table's time ("tháng" of revenue); the loans meet it on
    # the month's label.
    got = values(figures, "thang")
    assert got.keys() == {"2026-08", "2026-09"}
    assert got["2026-08"] == pytest.approx(8.0) and got["2026-09"] == pytest.approx(220 / 30)


async def test_a_grain_on_a_metric_without_time_is_refused_not_totalled(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    seed.one(
        "DELETE FROM context.semantic_dimension WHERE data_space_id = %s AND code = 'ngay' "
        "RETURNING id",
        bank.space,
    )
    q = bank.ask("Dư nợ theo tháng")
    await drain()
    status, kind, text, _, _, _ = answer_of(seed, q)
    assert (status, kind) == ("refused", "refusal") and "chiều thời gian" in text, text
