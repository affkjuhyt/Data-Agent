"""A query result reused for a while (query/executor.py, cache by the executed SQL): the same
question asked again is answered from the run before it, named in result_ref, while a tile
refresh and a receipt replay always read the source, and a different set of policies never
shares a result."""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from sqlalchemy import select

from libs.dataagent_db import scoped_session
from libs.dataagent_db.models import catalog
from services.worker_agent.query.executor import CACHE_PREFIX, run_query
from services.worker_agent.query.secrets import StaticReader

from . import test_answers
from .conftest import Seed
from .test_answers import PASSWORD, Bank, activate, answer_of, build, drain
from .test_column_masking import catalogue_columns

pytestmark = [pytest.mark.integration]

warehouse = test_answers.warehouse
SQL = "SELECT branch_code, balance FROM bank.loan WHERE as_of_date = '2026-09-30'"


async def source_of(bank: Bank) -> catalog.Source:
    async with scoped_session("svc_query", bank.org, bank.user) as session:
        source = await session.scalar(
            select(catalog.Source).where(catalog.Source.data_space_id == bank.space)
        )
    assert source is not None
    return source


async def run(bank: Bank, source: catalog.Source, caller_kind: str) -> Any:
    outcome = await run_query(
        org_id=bank.org,
        data_space_id=bank.space,
        principal_id=bank.user,
        source=source,
        sql=SQL,
        secrets=StaticReader({source.secret_ref: {"user": "loans_reader", "password": PASSWORD}}),
        caller_kind=caller_kind,  # type: ignore[arg-type]
        caller_id=uuid.uuid4(),
    )
    assert outcome.ok, outcome.error
    return outcome


def ref_of(seed: Seed, run_id: uuid.UUID) -> str | None:
    return seed.one("SELECT result_ref FROM query.query_run WHERE id = %s", run_id)


async def test_the_same_question_again_is_answered_from_the_run_before_it(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    activate(bank)
    first = bank.ask("Dư nợ theo vùng tháng 9/2026")
    second = bank.ask("Dư nợ theo vùng tháng 9/2026")
    await drain()
    with seed.conn.cursor() as cur:
        cur.execute(
            "SELECT id, result_ref, result_hash, row_count FROM query.query_run"
            " WHERE caller_id IN (%s, %s) ORDER BY started_at",
            (str(first), str(second)),
        )
        runs = cur.fetchall()
    (one, one_ref, one_hash, one_rows), (two, two_ref, two_hash, two_rows) = runs
    assert one_ref is None and two_ref == f"{CACHE_PREFIX}{one}"
    assert (two_hash, two_rows) == (one_hash, one_rows)
    # The second run keeps its own copy of the rows, so its answer traces to its own run.
    assert seed.one("SELECT count(*) FROM query.query_result WHERE query_run_id = %s", two) == 1
    figures = [answer_of(seed, q)[3] for q in (first, second)]
    assert [f["value"] for f in figures[0]] == [f["value"] for f in figures[1]]


async def test_a_tile_refresh_and_a_receipt_replay_always_read_the_source(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    source = await source_of(bank)
    asked = await run(bank, source, "agent_run")
    again = await run(bank, source, "agent_run")
    assert ref_of(seed, again.query_run_id) == f"{CACHE_PREFIX}{asked.query_run_id}"
    for caller_kind in ("tile", "receipt_replay"):
        fresh = await run(bank, source, caller_kind)
        assert ref_of(seed, fresh.query_run_id) is None
        assert fresh.result_hash == asked.result_hash


async def test_no_cache_when_its_time_is_zero(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    source = await source_of(bank)
    monkeypatch.setenv("QUERY_CACHE_TTL_S", "0")
    await run(bank, source, "agent_run")
    again = await run(bank, source, "agent_run")
    assert ref_of(seed, again.query_run_id) is None


async def test_a_result_with_other_policies_applied_is_not_shared(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    catalogue_columns(seed, bank)  # masks are applied in the SQL, over these columns
    source = await source_of(bank)
    await run(bank, source, "agent_run")
    # A mask policy changes the result after the query, not its SQL: the key must still differ.
    seed.row(
        "governance.access_policy",
        org_id=bank.org,
        data_space_id=bank.space,
        dataset_id=bank.loans,
        name="Che mã chi nhánh",
        kind="mask",
        applies_to=json.dumps({}),
        columns=["branch_code"],
        mask_method="null",
        status="active",
        simulated_at="2026-10-17T08:00:00Z",
        activated_at="2026-10-17T09:00:00Z",
        activated_by=bank.user,
    )
    masked = await run(bank, source, "agent_run")
    assert ref_of(seed, masked.query_run_id) is None
    assert masked.columns[0]["name"] == "branch_code"
    assert all(row[0] is None for row in masked.rows)


async def test_a_run_from_before_a_policy_changed_is_not_reused(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    catalogue_columns(seed, bank)  # masks are applied in the SQL, over these columns
    source = await source_of(bank)
    policy = seed.row(
        "governance.access_policy",
        org_id=bank.org,
        data_space_id=bank.space,
        dataset_id=bank.loans,
        name="Che số dư",
        kind="mask",
        applies_to=json.dumps({}),
        columns=["balance"],
        mask_method="null",
        status="active",
        simulated_at="2026-10-17T08:00:00Z",
        activated_at="2026-10-17T09:00:00Z",
        activated_by=bank.user,
    )
    first = await run(bank, source, "agent_run")
    assert ref_of(seed, (await run(bank, source, "agent_run")).query_run_id) == (
        f"{CACHE_PREFIX}{first.query_run_id}"
    )
    # The same policy, edited in place: what it masks changed, so the earlier result is not reused.
    with seed.conn.cursor() as cur:
        cur.execute(
            "UPDATE governance.access_policy SET columns = ARRAY['branch_code'], updated_at = now()"
            " WHERE id = %s",
            (str(policy),),
        )
    after = await run(bank, source, "agent_run")
    assert ref_of(seed, after.query_run_id) is None
    assert all(row[0] is None for row in after.rows) and after.rows[0][1] is not None
