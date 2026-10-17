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
