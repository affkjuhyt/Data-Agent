"""worker-agent previews a writeback (UC-06-04 step 2): the derived table in the Data Space
workspace against the target table on the customer's source (the bank of test_answers.py, read
with its read-only login), by the target's catalogued key. The request becomes `previewed` with
the counts and a sample without the personal-data column, or `failed` saying why."""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from services.worker_agent import writeback_diffs
from services.worker_agent.events import OutboxTableSource
from services.worker_agent.main import run_once

from . import test_answers
from .conftest import Seed
from .test_answers import Bank, build

pytestmark = [pytest.mark.integration]

warehouse = test_answers.warehouse

COLUMNS = [
    ("id", "integer", "integer", True, None),
    ("region", "text", "string", False, None),
    ("balance", "numeric", "decimal", False, None),
    ("phone", "text", "string", False, "phone"),
]


def _catalogue(bank: Bank, dataset: uuid.UUID, columns: list[tuple[Any, ...]]) -> None:
    for i, (name, data_type, normalized, key, pii) in enumerate(columns, start=1):
        bank.seed.row(
            "catalog.column_meta",
            org_id=bank.org,
            dataset_id=dataset,
            dataset_version=1,
            name=name,
            ordinal=i,
            data_type=data_type,
            normalized_type=normalized,
            is_key=key,
            pii_label=pii,
        )


def tables(
    bank: Bank,
    target_rows: list[tuple[Any, ...]],
    derived_rows: list[tuple[Any, ...]],
    *,
    keyed: bool = True,
) -> tuple[uuid.UUID, str, uuid.UUID]:
    """The target on the bank source and the derived table in the workspace, both catalogued."""
    seed = bank.seed
    suffix = uuid.uuid4().hex[:8]
    target, derived = f"wb_target_{suffix}", f"wb_derived_{suffix}"
    body = "id int, region text, balance numeric(18,2), phone text"
    with seed.conn.cursor() as cur:
        cur.execute(f"CREATE TABLE bank.{target} ({body})")
        cur.execute(f"GRANT SELECT ON bank.{target} TO loans_reader")
        cur.execute(f'CREATE TABLE workspace."{derived}" ({body})')
        cur.execute(f'GRANT SELECT ON workspace."{derived}" TO svc_query')
        for row in target_rows:
            cur.execute(f"INSERT INTO bank.{target} VALUES (%s, %s, %s, %s)", row)
        for row in derived_rows:
            cur.execute(f'INSERT INTO workspace."{derived}" VALUES (%s, %s, %s, %s)', row)
    source = seed.one(
        "SELECT id FROM catalog.source WHERE data_space_id = %s AND name = 'Kho tín dụng'",
        bank.space,
    )
    target_set = seed.row(
        "catalog.dataset",
        org_id=bank.org,
        data_space_id=bank.space,
        source_id=source,
        origin="source",
        schema_name="bank",
        table_name=target,
        enabled=True,
    )
    _catalogue(bank, target_set, [(*c[:3], keyed and c[3], c[4]) for c in COLUMNS])
    derived_set = seed.row(
        "catalog.dataset",
        org_id=bank.org,
        data_space_id=bank.space,
        origin="derived",
        schema_name="workspace",
        table_name=derived,
        enabled=True,
    )
    return source, target, derived_set  # type: ignore[return-value]


def request(bank: Bank, source: uuid.UUID, target: str, derived: uuid.UUID) -> uuid.UUID:
    made = bank.seed.row(
        "pipeline.writeback_request",
        org_id=bank.org,
        data_space_id=bank.space,
        source_id=source,
        target_table=target,
        from_dataset_id=derived,
        requested_by=bank.user,
        status="draft",
    )
    bank.seed.row(
        "pipeline.outbox",
        org_id=bank.org,
        aggregate_type="writeback_request",
        aggregate_id=made,
        event_type=writeback_diffs.EVENT_TYPE,
        payload=json.dumps({"writeback_request_id": str(made)}),
    )
    return made


async def drain() -> None:
    source = OutboxTableSource(schema="pipeline", event_types=frozenset(writeback_diffs.HANDLERS))
    while await run_once(source, writeback_diffs.HANDLERS, batch=50):
        pass


def outcome(seed: Seed, made: uuid.UUID) -> tuple[Any, ...]:
    return seed.one(  # type: ignore[return-value]
        "SELECT status, diff_summary, deletes FROM pipeline.writeback_request WHERE id = %s", made
    )


@pytest.mark.req("AC-06-04-05")
async def test_the_preview_counts_by_key_and_hides_personal_data(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    source, target, derived = tables(
        bank,
        [(1, "HN", 100, "0901"), (2, "SG", 200, "0902"), (3, "DN", 300, "0903")],
        [(1, "HN", 100, "0901"), (2, "SG", 250, "0912"), (4, "HP", 50, "0904")],
    )
    made = request(bank, source, f"bank.{target}", derived)
    await drain()
    status, summary, deletes = outcome(seed, made)
    assert status == "previewed", summary
    assert {k: summary[k] for k in ("inserts", "updates", "deletes", "unchanged", "key")} == {
        "inserts": 1,
        "updates": 1,
        "deletes": 1,
        "unchanged": 1,
        "key": ["id"],
    }
    # The deletes are the summary's, which :submit refuses (BR-113); the column stays 0.
    assert deletes == 0
    assert summary["sample"]["updates"] == [
        {"key": {"id": 2}, "before": {"balance": "200.00"}, "after": {"balance": "250.00"}}
    ]
    assert "phone" not in json.dumps(summary)
    assert summary["sample"]["deletes"][0]["key"] == {"id": 3}


async def test_an_unqualified_target_is_read_in_the_sources_schema(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    rows = [(1, "HN", 100, "0901")]
    source, target, derived = tables(bank, rows, [*rows, (2, "SG", 5, None)])
    made = request(bank, source, target, derived)
    await drain()
    status, summary, _ = outcome(seed, made)
    assert status == "previewed", summary
    assert (summary["inserts"], summary["deletes"], summary["unchanged"]) == (1, 0, 1)


@pytest.mark.parametrize(
    ("change", "says"),
    [
        ("unkeyed", "chưa có khoá chính"),
        ("uncatalogued", "chưa có trong danh mục"),
        ("too_many", "quá cỡ xem trước"),
    ],
)
async def test_what_cannot_be_compared_fails_saying_why(
    seed: Seed,
    warehouse: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    change: str,
    says: str,
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    rows = [(1, "HN", 100, "0901"), (2, "SG", 200, "0902")]
    source, target, derived = tables(bank, rows, rows, keyed=change != "unkeyed")
    if change == "too_many":
        monkeypatch.setattr(writeback_diffs, "MAX_ROWS", 1)
    name = f"bank.{target}" if change != "uncatalogued" else "bank.not_there"
    made = request(bank, source, name, derived)
    await drain()
    status, summary, _ = outcome(seed, made)
    assert status == "failed"
    assert says in summary["error"], summary
