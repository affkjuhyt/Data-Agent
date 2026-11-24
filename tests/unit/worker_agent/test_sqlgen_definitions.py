"""S2 and S3 (24/11/2026): the SQL writer reads the semantic layer's definition of the quantity
the question names, its tables linked first; half the candidates read the plain lexical view, so
a definition and a word match that disagree split the vote.

The 23/11 ablation: "Doanh thu đại lý năm 2012" linked the reseller dimension by its words and all
six candidates summed a reseller's declared annual revenue."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from services.worker_agent.llm import Reply
from services.worker_agent.sqlgen import answer as sqlanswer
from services.worker_agent.sqlgen import generate
from services.worker_agent.sqlgen.schema import (
    Col,
    Definition,
    Schema,
    Tbl,
    link,
    named_definitions,
)

pytestmark = [pytest.mark.unit]

FACT = Tbl(
    uuid.uuid4(),
    "public",
    "fact_reseller_sales",
    "Bán qua kênh bán buôn",
    (Col("sales_amount", "Thành tiền", "number"), Col("order_date", "Ngày đặt", "date")),
)
DIM = Tbl(
    uuid.uuid4(),
    "public",
    "dim_reseller",
    "Đại lý",
    (Col("annual_revenue", "Doanh thu năm", "number"), Col("year_opened", "Năm mở", "number")),
)
REVENUE = Definition(
    "Doanh thu đại lý",
    ("doanh thu dai ly",),
    frozenset({FACT.dataset_id}),
    "Doanh thu đại lý = SUM(sales_amount) FROM public.fact_reseller_sales (period on order_date)",
)
TOTAL = Definition("Doanh thu", ("doanh thu",), frozenset({FACT.dataset_id}), "Doanh thu = …")
SCHEMA = Schema([DIM, FACT], definitions=[REVENUE, TOTAL])


def test_the_most_specific_definition_named_is_kept() -> None:
    assert named_definitions(" doanh thu dai ly nam 2012 ", [REVENUE, TOTAL]) == [REVENUE]
    assert named_definitions(" doanh thu nam 2013 ", [REVENUE, TOTAL]) == [TOTAL]


def test_the_definition_links_its_fact_table_first_and_brings_its_text() -> None:
    linked = link("Doanh thu đại lý năm 2012", SCHEMA)
    assert linked.tables[0] is FACT
    assert linked.definitions == [REVENUE.text]
    assert generate.schema_text(linked).startswith(f"BUSINESS DEFINITION {REVENUE.text}")
    # The plain view (S3) is the words alone: the reseller dimension leads it.
    assert linked.plain is not None and linked.plain.tables[0] is DIM
    assert linked.plain.definitions == []


def test_no_definition_named_no_plain_view() -> None:
    linked = link("Năm mở của đại lý", SCHEMA)
    assert linked.definitions == [] and linked.plain is None


def test_one_source_keeps_both_views() -> None:
    linked = link("Doanh thu đại lý năm 2012", SCHEMA)
    kept, _ = sqlanswer._keep_one_source(linked, {FACT.dataset_id: None, DIM.dataset_id: None})
    assert kept.definitions == [REVENUE.text] and kept.plain is not None
    merged = kept.merged()
    assert {t.name for t in merged.tables} == {"fact_reseller_sales", "dim_reseller"}


class Recorder:
    def __init__(self) -> None:
        self.users: list[str] = []

    async def json_chat(
        self, role: str, system: str, user: str, output: type[Any], **kw: Any
    ) -> Any:
        self.users.append(user)
        return Reply(
            output.model_validate({"sql": "SELECT 1 AS x"}), model="m", tokens=1, latency_ms=1
        )


async def test_each_way_writes_once_with_the_definition_and_once_plain() -> None:
    linked = link("Doanh thu đại lý năm 2012", SCHEMA)
    model = Recorder()
    await generate.candidates(
        model, "Doanh thu đại lý năm 2012", linked, "postgres", [], plain=linked.plain
    )  # type: ignore[arg-type]
    with_definition = [u for u in model.users if "BUSINESS DEFINITION" in u]
    assert len(model.users) == 6 and len(with_definition) == 3


def test_a_winner_reading_none_of_the_defined_tables_is_to_be_checked() -> None:
    from services.worker_agent.sqlgen import run

    linked = link("Doanh thu đại lý năm 2012", SCHEMA)
    assert linked.defined_tables == ["public.fact_reseller_sales"]
    assert run._reads_any(
        "SELECT SUM(sales_amount) FROM public.fact_reseller_sales",
        linked.defined_tables,
        "postgres",
    )
    assert not run._reads_any(
        "SELECT SUM(annual_revenue) FROM public.dim_reseller", linked.defined_tables, "postgres"
    )
