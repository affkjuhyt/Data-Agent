"""Column and mask policies applied in the SQL (query/scope.rewrite_column_policies), on
PostgreSQL: the ways past a check on result names the security review found, run for real, never
return a raw value of a masked column, and a hidden column cannot be read at all."""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from sqlalchemy import select

from libs.dataagent_db import scoped_session
from libs.dataagent_db.models import catalog
from services.worker_agent.query.executor import run_query
from services.worker_agent.query.secrets import StaticReader

from . import test_answers
from .conftest import Seed
from .test_answers import PASSWORD, Bank, build

pytestmark = [pytest.mark.integration]

warehouse = test_answers.warehouse
LOAN_COLUMNS = ["branch_code", "group_code", "balance", "product_code", "as_of_date"]
RAW_BRANCHES = ("HN1", "HN2", "SG1")


def catalogue_columns(seed: Seed, bank: Bank) -> None:
    """The loan table's columns in the catalog, as a schema scan writes them."""
    for ordinal, name in enumerate(LOAN_COLUMNS, 1):
        seed.row(
            "catalog.column_meta",
            org_id=bank.org,
            dataset_id=bank.loans,
            dataset_version=1,
            name=name,
            ordinal=ordinal,
            data_type="text",
            normalized_type="string",
        )


def policy(seed: Seed, bank: Bank, kind: str, column: str, method: str | None = None) -> None:
    seed.row(
        "governance.access_policy",
        org_id=bank.org,
        data_space_id=bank.space,
        dataset_id=bank.loans,
        name=f"{kind} {column}",
        kind=kind,
        applies_to=json.dumps({}),
        columns=[column],
        mask_method=method,
        status="active",
        simulated_at="2026-10-17T08:00:00Z",
        activated_at="2026-10-17T09:00:00Z",
        activated_by=bank.user,
    )


async def run(bank: Bank, sql: str) -> Any:
    async with scoped_session("svc_query", bank.org, bank.user) as session:
        source = await session.scalar(
            select(catalog.Source).where(catalog.Source.data_space_id == bank.space)
        )
    assert source is not None
    return await run_query(
        org_id=bank.org,
        data_space_id=bank.space,
        principal_id=bank.user,
        source=source,
        sql=sql,
        secrets=StaticReader({source.secret_ref: {"user": "loans_reader", "password": PASSWORD}}),
        caller_kind="ad_hoc",
        caller_id=uuid.uuid4(),
    )


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT branch_code AS x FROM bank.loan",
        "SELECT upper(branch_code) FROM bank.loan",
        "SELECT row_to_json(l) FROM bank.loan AS l",
        "SELECT row_to_json(l.*) FROM bank.loan AS l",
        "SELECT CAST(l AS text) FROM bank.loan AS l",
        "SELECT * FROM bank.loan AS l(a, b, c, d, e)",
        "SELECT code FROM bank.branch WHERE false UNION ALL SELECT branch_code FROM bank.loan",
        "WITH x AS (SELECT * FROM bank.loan) SELECT x.branch_code AS y FROM x",
    ],
)
async def test_no_way_round_returns_a_raw_value_of_a_masked_column(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch, sql: str
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    catalogue_columns(seed, bank)
    policy(seed, bank, "mask", "branch_code", "hash")
    outcome = await run(bank, sql)
    if not outcome.ok:
        # The read-only guard's function allowlist refuses some of them first (row_to_json):
        # refused is as good, so long as nothing came back.
        assert "is not allowed" in (outcome.error or "") and not outcome.rows, outcome.error
        return
    cells = [str(cell) for row in outcome.rows for cell in row]
    assert cells
    assert not [c for c in cells if any(raw in c for raw in RAW_BRANCHES)], cells


async def test_a_masked_column_compares_masked_and_the_others_read_as_they_are(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    catalogue_columns(seed, bank)
    policy(seed, bank, "mask", "branch_code", "hash")
    found = await run(bank, "SELECT count(*) FROM bank.loan WHERE branch_code = 'HN1'")
    assert found.ok and found.rows == [[0]]
    plain = await run(bank, "SELECT DISTINCT product_code FROM bank.loan ORDER BY 1")
    assert plain.ok and [r[0] for r in plain.rows] == ["NOI_BO", "TD"]


@pytest.mark.parametrize(
    "sql",
    ["SELECT balance FROM bank.loan", "SELECT sum(balance) FROM bank.loan"],
)
async def test_a_hidden_column_cannot_be_read(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch, sql: str
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    catalogue_columns(seed, bank)
    policy(seed, bank, "column", "balance")
    assert not (await run(bank, sql)).ok


async def test_a_covered_table_without_catalogued_columns_is_refused(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    policy(seed, bank, "mask", "branch_code", "hash")
    outcome = await run(bank, "SELECT product_code FROM bank.loan")
    assert not outcome.ok and "not in the catalog" in (outcome.error or "")
