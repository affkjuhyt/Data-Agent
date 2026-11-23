"""DF-16: a derived dataset's columns point at the source columns they were read from, and an
ingest run's table at its source, in pipeline.lineage_edge."""

from __future__ import annotations

import datetime
import json
import uuid
from typing import Any

import pytest

from libs.dataagent_db import versioning
from services.worker_agent import column_edges, derived
from services.worker_agent.events import Event

from .conftest import Seed
from .test_answers import Bank, build, warehouse  # noqa: F401  (fixture)
from .test_derived import drain, saved

pytestmark = [pytest.mark.integration, pytest.mark.req("DF-16")]


def _loan_columns(bank: Bank) -> dict[str, uuid.UUID]:
    return {
        name: bank.seed.row(
            "catalog.column_meta",
            org_id=bank.org,
            dataset_id=bank.loans,
            dataset_version=1,
            name=name,
            ordinal=i,
            data_type=kind,
            normalized_type="decimal" if kind == "numeric" else "string",
        )
        for i, (name, kind) in enumerate(
            [("branch_code", "text"), ("balance", "numeric"), ("product_code", "text")], start=1
        )
    }


async def test_a_derived_dataset_links_each_column_to_its_source_column(
    seed: Seed,
    warehouse: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    loan = _loan_columns(bank)
    # Unqualified: `loan` is the source's default schema's (bank.loan).
    dataset = saved(
        bank,
        "du_no_theo_cn",
        derived.SQL_DERIVED,
        sql='SELECT branch_code AS "Chi nhánh", sum(balance) AS tong FROM loan GROUP BY 1',
        write_mode="replace",
    )
    await drain()
    got = seed.all(
        "SELECT e.from_id, c.name FROM pipeline.lineage_edge e"
        " JOIN catalog.column_meta c ON c.id = e.to_id"
        " WHERE e.from_kind = 'column' AND e.to_kind = 'column' AND c.dataset_id = %s"
        " AND e.via = 'derive' AND e.via_ref = %s",
        dataset,
        dataset,
    )
    # The result column "Chi nhánh" was written as its physical name.
    names = dict(
        seed.all("SELECT id, name FROM catalog.column_meta WHERE dataset_id = %s", dataset)
    )
    assert set(names.values()) == {"chi_nhanh", "tong"}
    assert set(got) == {
        (loan["branch_code"], "chi_nhanh"),
        (loan["balance"], "tong"),
    }


async def test_an_ingest_run_links_its_table_to_its_source_once(
    seed: Seed,
    warehouse: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    source = seed.one("SELECT source_id FROM catalog.dataset WHERE id = %s", bank.loans)
    job = seed.row(
        "ingest.ingestion_job",
        org_id=bank.org,
        data_space_id=bank.space,
        source_id=source,
        name="Nạp khoản vay",
        mode="full",
        source_object="loan",
        created_by=bank.user,
    )
    target = seed.row(
        "catalog.dataset",
        org_id=bank.org,
        source_id=source,
        data_space_id=bank.space,
        origin="source",
        schema_name="workspace",
        table_name=f"loan_{uuid.uuid4().hex[:6]}",
        enabled=True,
    )

    def announced(run: uuid.UUID, kind: str = "job_run") -> Event:
        return Event(
            event_id=uuid.uuid4(),
            org_id=bank.org,
            event_type=versioning.VERSION_CREATED,
            aggregate_type="dataset",
            aggregate_id=target,
            payload=json.loads(
                json.dumps(
                    {
                        "dataset_id": str(target),
                        "version": 1,
                        "physical_table": "x",
                        "created_by_kind": kind,
                        "run_id": str(run),
                        "available_at": datetime.datetime.now(datetime.UTC).isoformat(),
                    }
                )
            ),
        )

    first = seed.row("ingest.job_run", org_id=bank.org, job_id=job)
    second = seed.row("ingest.job_run", org_id=bank.org, job_id=job)
    assert await column_edges.source_edge(announced(first))
    # The next run of the same job finds the edge open: one edge per pair.
    assert not await column_edges.source_edge(announced(second))
    # A version an upload or a pipeline wrote is not an ingest's.
    assert not await column_edges.source_edge(announced(first, kind="pipeline_run"))
    assert seed.all(
        "SELECT from_kind, via, via_ref FROM pipeline.lineage_edge"
        " WHERE to_kind = 'dataset' AND to_id = %s AND valid_to IS NULL",
        target,
    ) == [("source", "ingest", job)]
