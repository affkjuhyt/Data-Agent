from __future__ import annotations

import json
import uuid
from urllib.parse import urlsplit

import pytest

from services.worker_ingest import worker
from services.worker_ingest.events import Event
from services.worker_ingest.secrets import Credentials

from .conftest import Seed

pytestmark = pytest.mark.integration


async def test_created_job_materializes_rows_and_registers_a_local_dataset(
    seed: Seed, monkeypatch: pytest.MonkeyPatch, ddl_server: dict[str, str]
) -> None:
    source_table = f"source_{uuid.uuid4().hex[:8]}"
    dsn = ddl_server["dataagent"]
    parts = urlsplit(dsn)
    with seed.conn.cursor() as cur:
        cur.execute(
            f'CREATE TABLE public."{source_table}" (branch text, balance numeric, month text, '
            "as_of date, n int, open boolean, at timestamptz)"
        )
        cur.execute(
            f'INSERT INTO public."{source_table}" VALUES '
            "('HN', 10, '2026-09', '2026-09-30', 1, true, now()), "
            "('SG', 20, '2026-09', '2026-09-30', 2, false, now())"
        )

    org = seed.row("org.organization", code=f"org-{uuid.uuid4().hex[:8]}", name="Ingest test")
    user = seed.row(
        "org.app_user",
        org_id=org,
        external_id="kc-ingest",
        username="ingest",
        display_name="Ingest",
    )
    space = seed.row("org.data_space", org_id=org, code="DS-ING", name="Ingest", steward_id=user)
    secret_ref = f"secret/tenants/{org}/sources/local"
    source = seed.row(
        "catalog.source",
        org_id=org,
        data_space_id=space,
        name="Local source",
        kind="postgresql",
        host=parts.hostname,
        port=parts.port,
        database_name=parts.path.lstrip("/"),
        conn_options=json.dumps({"schema": "public", "sslmode": "disable"}),
        secret_ref=secret_ref,
        mode="ingest",
        status="active",
    )
    job = seed.row(
        "ingest.ingestion_job",
        org_id=org,
        data_space_id=space,
        source_id=source,
        name="Branch balances",
        mode="full",
        source_object=source_table,
        created_by=user,
        updated_by=user,
    )

    def _credentials() -> Credentials:
        return Credentials(str(urlsplit(dsn).username), str(urlsplit(dsn).password))

    async def _read(_self: worker.Reader, _org: uuid.UUID, _ref: str) -> Credentials:
        return _credentials()

    monkeypatch.setattr(worker.Reader, "read", _read)
    # One row a batch (RES-16): the asyncpg cursor is read in batches, the first replacing the
    # table and the others appending to it.
    monkeypatch.setattr(worker, "BATCH_ROWS", 1)

    await worker.handle(
        Event(
            uuid.uuid4(),
            org,
            "ingestion_job.created",
            "ingestion_job",
            job,
            {"job_id": str(job), "principal_id": str(user)},
        )
    )

    assert seed.one("SELECT status FROM ingest.job_run WHERE job_id = %s", job) == "succeeded"
    dataset = seed.one(
        "SELECT table_name FROM catalog.dataset WHERE org_id = %s AND origin = 'derived'", org
    )
    assert dataset is not None
    assert seed.one(f'SELECT count(*) FROM workspace."{dataset}"') == 2
    # The source's types are kept where the workspace can hold them, and every column is in the
    # catalog: the semantic compiler reads a text month as text, a date as a date.
    assert seed.all(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_schema = 'workspace' AND table_name = %s ORDER BY ordinal_position",
        dataset,
    ) == [
        ("branch", "text"),
        ("balance", "numeric"),
        ("month", "text"),
        ("as_of", "date"),
        ("n", "numeric"),
        ("open", "boolean"),
        ("at", "text"),
    ]
    assert seed.all(
        "SELECT c.name, c.normalized_type FROM catalog.column_meta c "
        "JOIN catalog.dataset d ON d.id = c.dataset_id WHERE d.table_name = %s ORDER BY c.ordinal",
        dataset,
    ) == [
        ("branch", "string"),
        ("balance", "decimal"),
        ("month", "string"),
        ("as_of", "date"),
        ("n", "decimal"),
        ("open", "boolean"),
        ("at", "string"),
    ]


async def test_an_incremental_job_reads_only_what_is_above_its_cursor(
    seed: Seed, monkeypatch: pytest.MonkeyPatch, ddl_server: dict[str, str]
) -> None:
    """RES-17: the upper bound is fixed when a run starts, the rows above the last one appended,
    the cursor kept with the data; a run with nothing new writes nothing."""
    source_table = f"orders_{uuid.uuid4().hex[:8]}"
    dsn = ddl_server["dataagent"]
    parts = urlsplit(dsn)
    with seed.conn.cursor() as cur:
        cur.execute(
            f'CREATE TABLE public."{source_table}" (id int, amount numeric, at timestamptz)'
        )
        cur.execute(
            f'INSERT INTO public."{source_table}" VALUES '
            "(1, 10, '2026-10-01 08:00+07'), (2, 20, '2026-10-02 08:00+07'),"
            " (3, 30, '2026-10-03 08:00+07')"
        )
    org = seed.row("org.organization", code=f"org-{uuid.uuid4().hex[:8]}", name="Incremental")
    user = seed.row(
        "org.app_user", org_id=org, external_id="kc-inc", username="inc", display_name="Inc"
    )
    space = seed.row("org.data_space", org_id=org, code="DS-INC", name="Inc", steward_id=user)
    source = seed.row(
        "catalog.source",
        org_id=org,
        data_space_id=space,
        name="Local source",
        kind="postgresql",
        host=parts.hostname,
        port=parts.port,
        database_name=parts.path.lstrip("/"),
        conn_options=json.dumps({"schema": "public", "sslmode": "disable"}),
        secret_ref=f"secret/tenants/{org}/sources/local",
        mode="ingest",
        status="active",
    )
    job = seed.row(
        "ingest.ingestion_job",
        org_id=org,
        data_space_id=space,
        source_id=source,
        name="Orders",
        mode="incremental",
        cursor_column="at",
        source_object=source_table,
        created_by=user,
        updated_by=user,
    )

    async def _read(_self: worker.Reader, _org: uuid.UUID, _ref: str) -> Credentials:
        return Credentials(str(parts.username), str(parts.password))

    monkeypatch.setattr(worker.Reader, "read", _read)
    monkeypatch.setattr(worker, "BATCH_ROWS", 2)

    async def run() -> None:
        await worker.handle(
            Event(
                uuid.uuid4(),
                org,
                "ingestion_job.created",
                "ingestion_job",
                job,
                {"job_id": str(job), "principal_id": str(user)},
            )
        )

    await run()
    table = seed.one("SELECT table_name FROM catalog.dataset WHERE org_id = %s", org)
    assert seed.one(f'SELECT count(*) FROM workspace."{table}"') == 3
    first = seed.one("SELECT cursor_value FROM ingest.ingestion_job WHERE id = %s", job)
    assert first is not None and first.startswith("2026-10-03")

    with seed.conn.cursor() as cur:
        cur.execute(f"INSERT INTO public.\"{source_table}\" VALUES (4, 40, '2026-10-04 08:00+07')")
    await run()
    assert seed.one(f'SELECT count(*) FROM workspace."{table}"') == 4
    assert (
        seed.one(
            "SELECT rows_read FROM ingest.job_run WHERE job_id = %s AND cursor_from = %s",
            job,
            first,
        )
        == 1
    )

    await run()
    assert seed.one(f'SELECT count(*) FROM workspace."{table}"') == 4
    assert (
        seed.one(
            "SELECT count(*) FROM ingest.job_run WHERE job_id = %s AND status = 'succeeded'", job
        )
        == 3
    )
