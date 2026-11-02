from __future__ import annotations

import uuid

import pytest

from libs.dataagent_db.models import ingest
from services.worker_ingest.worker import HANDLERS, _identifier, _table_name

pytestmark = pytest.mark.unit


def test_source_object_identifiers_are_strict() -> None:
    assert _identifier("branch_balances") == "branch_balances"
    with pytest.raises(ValueError):
        _identifier("branch balances")
    with pytest.raises(ValueError):
        _identifier("branch;drop table source")


def test_materialized_table_name_is_stable_and_scoped_to_job() -> None:
    job = ingest.IngestionJob(
        id=uuid.UUID("018f8d4d-5f1b-7f7f-8a45-8c64bd7b1020"), name="Branch balances"
    )
    assert _table_name(job) == "ingest_branch_balances_018f8d4d"


def test_worker_consumes_only_events_it_can_materialize() -> None:
    assert set(HANDLERS) == {"ingestion_job.created", "job_run.backfill_requested"}


def test_a_cursor_literal_is_a_number_or_a_moment_and_nothing_else() -> None:
    from services.worker_ingest import worker

    assert worker._literal("42") == "42"
    assert worker._literal("-3.5") == "-3.5"
    assert worker._literal("2026-09-30") == "'2026-09-30'"
    assert (
        worker._literal("2026-10-03 01:00:00.123456+00:00") == "'2026-10-03 01:00:00.123456+00:00'"
    )
    for hostile in ("x' OR '1'='1", "2026-09-30\\' OR 1=1 -- ", "abc", "1; DROP TABLE t"):
        with pytest.raises(ValueError, match="number, a date or a timestamp"):
            worker._literal(hostile)
