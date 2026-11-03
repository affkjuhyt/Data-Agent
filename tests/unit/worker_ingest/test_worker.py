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


def test_a_failure_says_whether_the_person_must_act() -> None:
    import asyncpg

    from libs import source_drivers
    from services.worker_ingest import worker
    from services.worker_ingest.secrets import SecretUnavailable

    assert worker.failure_kind(ValueError("source_object must be a table identifier")) == "config"
    assert worker.failure_kind(SecretUnavailable("no secret")) == "config"
    assert worker.failure_kind(asyncpg.UndefinedTableError("no table")) == "config"
    assert worker.failure_kind(asyncpg.InvalidPasswordError("bad login")) == "config"
    assert worker.failure_kind(asyncpg.QueryCanceledError("timeout")) == "transient"
    assert worker.failure_kind(ConnectionRefusedError()) == "transient"
    assert worker.failure_kind(TimeoutError()) == "transient"
    assert (
        worker.failure_kind(source_drivers.DriverError("mysql query failed (OperationalError)"))
        == "transient"
    )
    assert (
        worker.failure_kind(
            source_drivers.DriverError("mysql TLS verification needs conn_options.ssl_ca")
        )
        == "config"
    )
    assert worker.failure_kind(asyncpg.DataError("bad value")) == "system"


def test_a_retyped_column_is_drift_and_a_new_or_dropped_one_is_not() -> None:
    from services.worker_ingest import worker

    existing = {"id": "numeric", "amount": "numeric", "gone": "text"}
    worker.check_drift(
        existing,
        [
            {"name": "id", "label": "id", "type": "numeric"},
            {"name": "amount", "label": "amount", "type": "numeric"},
            {"name": "added", "label": "added", "type": "date"},
        ],
    )
    with pytest.raises(worker.SchemaDrift, match="amount numeric → text"):
        worker.check_drift(existing, [{"name": "amount", "label": "amount", "type": "text"}])
    assert worker.failure_kind(worker.SchemaDrift("x")) == "config"
