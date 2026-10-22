"""Derived datasets are built when saved (UC-06-02, UC-06-05): the editor's SQL or Explore's
filtered table is read on its source as the person who saved, written to the dataset's own
workspace table, enabled, and linked in lineage to what it was read from."""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from services.worker_agent import derived
from services.worker_agent.events import OutboxTableSource
from services.worker_agent.main import run_once

from .conftest import Seed
from .test_answers import Bank, build, warehouse  # noqa: F401  (fixture)

pytestmark = [pytest.mark.integration]


async def drain() -> None:
    source = OutboxTableSource(schema="catalog", event_types=frozenset(derived.HANDLERS))
    while await run_once(source, derived.HANDLERS, batch=20):
        pass


def saved(bank: Bank, name: str, event: str, **payload: Any) -> uuid.UUID:
    dataset = bank.seed.row(
        "catalog.dataset",
        org_id=bank.org,
        data_space_id=bank.space,
        origin="derived",
        schema_name="workspace",
        table_name=name,
        display_name=name,
        enabled=False,
    )
    bank.seed.row(
        "catalog.outbox",
        org_id=bank.org,
        aggregate_type="dataset",
        aggregate_id=dataset,
        event_type=event,
        payload=json.dumps(
            {
                "dataset_id": str(dataset),
                "data_space_id": str(bank.space),
                "requested_by": str(bank.user),
                **payload,
            }
        ),
    )
    return dataset


@pytest.mark.req("AC-06-02-01")
async def test_sql_saved_from_the_editor_becomes_a_table_with_lineage(
    seed: Seed,
    warehouse: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    dataset = saved(
        bank,
        "du_no_sach",
        derived.SQL_DERIVED,
        sql="SELECT branch_code, balance FROM loan WHERE product_code <> 'NOI_BO'",
        write_mode="replace",
    )
    await drain()
    enabled, table, rows = seed.one(
        "SELECT enabled, table_name, row_estimate FROM catalog.dataset WHERE id = %s", dataset
    )
    assert enabled and table.startswith("p_du_no_sach_") and rows == 6
    assert seed.one(f"SELECT sum(balance) FROM workspace.{table}") == 3500
    assert (
        seed.one(
            "SELECT via FROM pipeline.lineage_edge WHERE from_id = %s AND to_id = %s",
            bank.loans,
            dataset,
        )
        == "derive"
    )


async def test_explore_saves_filtered_rows_without_duplicates(
    seed: Seed,
    warehouse: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    dataset = saved(
        bank,
        "chi_nhanh_td",
        derived.DERIVE,
        source_dataset_id=str(bank.loans),
        filters=[{"column": "product_code", "op": "eq", "value": "TD"}],
        drop_duplicates=True,
    )
    await drain()
    table = seed.one("SELECT table_name FROM catalog.dataset WHERE id = %s AND enabled", dataset)
    assert seed.one(f"SELECT count(*) FROM workspace.{table}") == 6
    assert seed.one(f"SELECT count(DISTINCT product_code) FROM workspace.{table}") == 1


async def test_a_dataset_that_cannot_be_built_says_why_and_stays_disabled(
    seed: Seed,
    warehouse: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    dataset = saved(bank, "gop", derived.SQL_DERIVED, sql="SELECT * FROM loan", write_mode="merge")
    await drain()
    enabled, description = seed.one(
        "SELECT enabled, description FROM catalog.dataset WHERE id = %s", dataset
    )
    assert not enabled and description.startswith("Chưa tạo được bảng: ghi gộp")
