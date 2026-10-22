"""Pipelines run for real (UC-06-01, UC-06-02): SQL steps chained on the source of their input,
rows counted per step, the money totals held by every step that is not a filter, and the result
saved as a workspace table registered in the catalogue and in lineage."""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from services.worker_agent import pipelines
from services.worker_agent.events import OutboxTableSource
from services.worker_agent.main import run_once

from .conftest import Seed
from .test_answers import Bank, build, warehouse  # noqa: F401  (fixture)

pytestmark = [pytest.mark.integration]

SELECT = {
    "kind": "compute",
    "description": "Lấy khoản vay",
    "code": "SELECT branch_code, group_code, balance, product_code FROM loan",
}
DROP_INTERNAL = {
    "kind": "filter",
    "description": "Bỏ cho vay nội bộ",
    "code": "SELECT * FROM t WHERE product_code <> 'NOI_BO'",
    "drop_condition": "product_code = 'NOI_BO'",
}
DOUBLE = {
    "kind": "compute",
    "description": "Nhân đôi (sai)",
    "code": "SELECT branch_code, balance * 2 AS balance FROM t",
}


async def drain() -> None:
    source = OutboxTableSource(schema="pipeline", event_types=frozenset(pipelines.HANDLERS))
    while await run_once(source, pipelines.HANDLERS, batch=20):
        pass


def pipeline(bank: Bank, steps: list[dict[str, Any]]) -> uuid.UUID:
    with bank.seed.conn.cursor() as cur:
        pid = uuid.uuid4()
        cur.execute(
            "INSERT INTO pipeline.pipeline (id, org_id, data_space_id, name, owner_id,"
            " input_dataset_ids, steps, scope) VALUES (%s,%s,%s,%s,%s,%s::uuid[],%s,'data_space')",
            (
                str(pid),
                str(bank.org),
                str(bank.space),
                f"p-{pid.hex[:6]}",
                str(bank.user),
                [str(bank.loans)],
                json.dumps(steps),
            ),
        )
    return pid


def request(bank: Bank, pid: uuid.UUID, event: str, mode: str = "full", **extra: Any) -> uuid.UUID:
    run = bank.seed.row(
        "pipeline.pipeline_run", org_id=bank.org, pipeline_id=pid, mode=mode, status="running"
    )
    bank.seed.row(
        "pipeline.outbox",
        org_id=bank.org,
        aggregate_type="pipeline_run",
        aggregate_id=run,
        event_type=event,
        payload=json.dumps(
            {
                "pipeline_run_id": str(run),
                "pipeline_id": str(pid),
                "data_space_id": str(bank.space),
                "requested_by": str(bank.user),
                **extra,
            }
        ),
    )
    return run


@pytest.mark.req("AC-06-01-01")
async def test_a_run_counts_rows_and_records_what_a_filter_drops(
    seed: Seed,
    warehouse: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    run = request(bank, pipeline(bank, [SELECT, DROP_INTERNAL]), pipelines.RUN)
    await drain()
    status, rows_in, rows_out, dropped, totals = seed.one(
        "SELECT status, rows_in, rows_out, rows_dropped, totals_check FROM pipeline.pipeline_run"
        " WHERE id = %s",
        run,
    )
    assert (status, rows_in, rows_out) == ("succeeded", 7, 6)
    assert dropped == [
        {"step": 2, "kind": "filter", "rows": 1, "condition": "product_code = 'NOI_BO'"}
    ]
    assert totals["matched"] is True


@pytest.mark.req("AC-06-01-04")
async def test_a_step_that_moves_a_money_total_is_caught(
    seed: Seed,
    warehouse: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    run = request(bank, pipeline(bank, [SELECT, DROP_INTERNAL, DOUBLE]), pipelines.RUN)
    await drain()
    status, totals = seed.one(
        "SELECT status, totals_check FROM pipeline.pipeline_run WHERE id = %s", run
    )
    assert status == "succeeded" and totals["matched"] is False
    moved = [c for c in totals["columns"] if not c["matched"]]
    assert moved == [
        {"column": "balance", "matched": False, "step": 3, "before": "3500.00", "after": "7000.00"}
    ]


@pytest.mark.req("AC-06-02-01")
async def test_saving_writes_the_table_registers_it_and_links_its_inputs(
    seed: Seed,
    warehouse: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    pid = pipeline(bank, [SELECT, DROP_INTERNAL])
    request(bank, pid, pipelines.MATERIALIZE, table_name="khoan_vay_sach", scope="data_space")
    await drain()
    status, target = seed.one(
        "SELECT status, target_dataset_id FROM pipeline.pipeline WHERE id = %s", pid
    )
    assert status == "active" and target is not None
    table, shown = seed.one(
        "SELECT table_name, display_name FROM catalog.dataset"
        " WHERE id = %s AND schema_name = 'workspace'",
        target,
    )
    # The physical name is the server's, unique to the dataset; the chosen one is displayed.
    assert shown == "khoan_vay_sach" and table.startswith("p_khoan_vay_sach_")
    assert seed.one(f"SELECT count(*) FROM workspace.{table}") == 6
    assert seed.one(f"SELECT sum(balance) FROM workspace.{table}") == 3500
    assert (
        seed.one(
            "SELECT via FROM pipeline.lineage_edge WHERE from_id = %s AND to_id = %s",
            bank.loans,
            target,
        )
        == "pipeline"
    )


async def test_two_spaces_saving_the_same_name_never_share_a_table(
    seed: Seed,
    warehouse: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # One after the other: each build points the secret reader at its own organisation.
    for steps in ([SELECT, DROP_INTERNAL], [SELECT]):
        bank = build(seed, warehouse, monkeypatch)
        request(bank, pipeline(bank, steps), pipelines.MATERIALIZE, table_name="chung")
        await drain()
    tables = [
        r[0]
        for r in seed.all(
            "SELECT table_name FROM catalog.dataset WHERE display_name = 'chung' ORDER BY created_at"
        )
    ]
    assert len(tables) == 2 and tables[0] != tables[1]
    assert [seed.one(f"SELECT count(*) FROM workspace.{t}") for t in tables] == [6, 7]


async def test_a_filter_that_changes_columns_is_held_to_the_totals(
    seed: Seed,
    warehouse: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    sneaky = {**DOUBLE, "kind": "filter", "drop_condition": "không bỏ gì"}
    run = request(bank, pipeline(bank, [SELECT, sneaky]), pipelines.RUN)
    await drain()
    totals = seed.one("SELECT totals_check FROM pipeline.pipeline_run WHERE id = %s", run)
    assert totals["matched"] is False


async def test_python_steps_fail_with_the_reason(
    seed: Seed,
    warehouse: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    step = {**SELECT, "language": "python", "code": "df = df"}
    run = request(bank, pipeline(bank, [step]), pipelines.RUN)
    await drain()
    status, error = seed.one("SELECT status, error FROM pipeline.pipeline_run WHERE id = %s", run)
    assert status == "failed" and "Python steps" in error
