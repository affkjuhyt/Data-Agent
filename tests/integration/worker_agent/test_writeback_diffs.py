"""worker-agent previews a writeback (UC-06-04 step 2): the derived table in the Data Space
workspace against the target table on the customer's source (the bank of test_answers.py, read
with its read-only login), by the target's catalogued key. The request becomes `previewed` with
the counts and a sample without the personal-data column, or `failed` saying why."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

import pytest

from services.worker_agent import writeback_diffs
from services.worker_agent.events import OutboxTableSource
from services.worker_agent.main import run_once
from services.worker_agent.pipelines import physical_name

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
) -> Tables:
    """The target on the bank source and the derived table in the workspace, both catalogued;
    the derived table named as the platform names what it materialises (pipelines.physical_name)."""
    seed = bank.seed
    derived_id = uuid.uuid4()
    target, derived = f"wb_target_{derived_id.hex[:8]}", physical_name("wb", derived_id)
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
    seed.row(
        "catalog.dataset",
        id=derived_id,
        org_id=bank.org,
        data_space_id=bank.space,
        origin="derived",
        schema_name="workspace",
        table_name=derived,
        enabled=True,
    )
    return Tables(source, target, derived_id, target_set)  # type: ignore[arg-type]


@dataclass(frozen=True)
class Tables:
    source: uuid.UUID
    target: str
    derived: uuid.UUID
    target_dataset: uuid.UUID


def policy(bank: Bank, dataset: uuid.UUID, kind: str, **values: object) -> None:
    bank.seed.row(
        "governance.access_policy",
        org_id=bank.org,
        data_space_id=bank.space,
        dataset_id=dataset,
        name=f"Chính sách {kind}",
        kind=kind,
        applies_to=json.dumps({}),
        status="active",
        simulated_at="2026-10-17T08:00:00Z",
        activated_at="2026-10-17T09:00:00Z",
        activated_by=bank.user,
        **values,
    )


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
    t = tables(
        bank,
        [(1, "HN", 100, "0901"), (2, "SG", 200, "0902"), (3, "DN", 300, "0903")],
        [(1, "HN", 100, "0901"), (2, "SG", 250, "0912"), (4, "HP", 50, "0904")],
    )
    made = request(bank, t.source, f"bank.{t.target}", t.derived)
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
    t = tables(bank, rows, [*rows, (2, "SG", 5, None)])
    made = request(bank, t.source, t.target, t.derived)
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
    t = tables(bank, rows, rows, keyed=change != "unkeyed")
    if change == "too_many":
        monkeypatch.setattr(writeback_diffs, "MAX_ROWS", 1)
    name = f"bank.{t.target}" if change != "uncatalogued" else "bank.not_there"
    made = request(bank, t.source, name, t.derived)
    await drain()
    status, summary, _ = outcome(seed, made)
    assert status == "failed"
    assert says in summary["error"], summary


@pytest.mark.parametrize("crafted", ["platform_table", "another_workspace_table", "other_space"])
async def test_a_derived_table_the_platform_did_not_make_here_is_never_read(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch, crafted: str
) -> None:
    # POST /v1/derived-tables registers any schema.table as a derived dataset: only the table the
    # platform materialised for this very dataset, in this request's Data Space, is read.
    bank = build(seed, warehouse, monkeypatch)
    rows = [(1, "HN", 100, "0901")]
    t = tables(bank, rows, rows)
    derived = t.derived
    if crafted == "platform_table":
        derived = seed.row(
            "catalog.dataset",
            org_id=bank.org,
            data_space_id=bank.space,
            origin="derived",
            schema_name="catalog",
            table_name="source",
            enabled=True,
        )
    elif crafted == "another_workspace_table":
        # Another Data Space's materialised table, under a new dataset of this one.
        other = seed.row(
            "org.data_space", org_id=bank.org, code="DS-KHAC", name="Khác", steward_id=bank.user
        )
        seed.all(
            "UPDATE catalog.dataset SET data_space_id = %s WHERE id = %s RETURNING id",
            other,
            t.derived,
        )
        derived = seed.row(
            "catalog.dataset",
            org_id=bank.org,
            data_space_id=bank.space,
            origin="derived",
            schema_name="workspace",
            table_name=physical_name("wb", t.derived),
            enabled=True,
        )
    else:
        other = seed.row(
            "org.data_space", org_id=bank.org, code="DS-KHAC", name="Khác", steward_id=bank.user
        )
        seed.all(
            "UPDATE catalog.dataset SET data_space_id = %s WHERE id = %s RETURNING id",
            other,
            t.derived,
        )
    made = request(bank, t.source, f"bank.{t.target}", derived)
    await drain()
    status, summary, _ = outcome(seed, made)
    assert (status, summary) == ("failed", {"error": writeback_diffs.NOT_MATERIALISED})


async def test_a_row_policy_on_the_target_refuses_the_preview(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    # The requester sees part of the target: a diff of what they see would call the rest deletes.
    bank = build(seed, warehouse, monkeypatch)
    rows = [(1, "HN", 100, "0901"), (2, "SG", 200, "0902")]
    t = tables(bank, rows, rows)
    policy(bank, t.target_dataset, "row", condition_expr="region = 'HN'")
    made = request(bank, t.source, f"bank.{t.target}", t.derived)
    await drain()
    status, summary, _ = outcome(seed, made)
    assert status == "failed" and "giới hạn dòng" in summary["error"], summary


@pytest.mark.parametrize("kind", ["column", "mask"])
async def test_a_column_or_mask_policy_keeps_the_column_out_of_the_sample(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    t = tables(bank, [(1, "HN", 100, "0901")], [(1, "SG", 150, "0901"), (2, "HP", 7, "0902")])
    extra = {"mask_method": "null"} if kind == "mask" else {}
    policy(bank, t.target_dataset, kind, columns=["balance"], **extra)
    made = request(bank, t.source, f"bank.{t.target}", t.derived)
    await drain()
    status, summary, _ = outcome(seed, made)
    assert status == "previewed", summary
    assert (summary["inserts"], summary["updates"]) == (1, 1)
    assert "balance" not in json.dumps(summary["sample"])
    assert summary["sample"]["updates"] == [
        {"key": {"id": 1}, "before": {"region": "HN"}, "after": {"region": "SG"}}
    ]
