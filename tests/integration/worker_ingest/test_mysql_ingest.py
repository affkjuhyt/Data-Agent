"""A MySQL table is loaded into the workspace through the drivers worker-agent uses (GEN-12):
worker-ingest refused every engine but PostgreSQL until libs/source_drivers was shared."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from typing import Any

import pytest

from services.worker_ingest import worker
from services.worker_ingest.events import Event
from services.worker_ingest.secrets import Credentials

from .conftest import Seed

pytestmark = pytest.mark.integration

SETUP = """
CREATE TABLE sales (`Chi nhánh` VARCHAR(20), amount DECIMAL(12, 2), sold_on DATE, qty INT,
                    paid BOOLEAN, note VARCHAR(20));
INSERT INTO sales VALUES ('HN', 10.50, '2026-09-30', 1, TRUE, NULL),
                         ('SG', 20.00, '2026-09-29', 2, FALSE, 'x');
"""


@pytest.fixture(scope="module")
def mysql() -> Iterator[dict[str, Any]]:
    try:
        from testcontainers.mysql import MySqlContainer
    except ImportError:
        pytest.skip("testcontainers mysql not installed")
    with MySqlContainer("mysql:8.4", dbname="shop", username="reader", password="pw") as box:
        import pymysql

        conn = pymysql.connect(
            host=box.get_container_host_ip(),
            port=int(box.get_exposed_port(3306)),
            user="root",
            password=box.root_password,
            database="shop",
            autocommit=True,
        )
        with conn.cursor() as cursor:
            for statement in filter(str.strip, SETUP.split(";")):
                cursor.execute(statement)
        conn.close()
        yield {"host": box.get_container_host_ip(), "port": int(box.get_exposed_port(3306))}


async def test_a_mysql_table_is_loaded_and_catalogued(
    seed: Seed, monkeypatch: pytest.MonkeyPatch, mysql: dict[str, Any]
) -> None:
    org = seed.row("org.organization", code=f"org-{uuid.uuid4().hex[:8]}", name="MySQL ingest")
    user = seed.row(
        "org.app_user", org_id=org, external_id="kc-my", username="my", display_name="My"
    )
    space = seed.row("org.data_space", org_id=org, code="DS-MY", name="MySQL", steward_id=user)
    source = seed.row(
        "catalog.source",
        org_id=org,
        data_space_id=space,
        name="Shop MySQL",
        kind="mysql",
        host=mysql["host"],
        port=mysql["port"],
        database_name="shop",
        conn_options=json.dumps({"sslmode": "disable"}),
        secret_ref=f"secret/tenants/{org}/sources/shop",
        mode="ingest",
        status="active",
    )
    job = seed.row(
        "ingest.ingestion_job",
        org_id=org,
        data_space_id=space,
        source_id=source,
        name="Sales",
        mode="full",
        source_object="sales",
        created_by=user,
        updated_by=user,
    )

    async def _read(_self: worker.Reader, _org: uuid.UUID, _ref: str) -> Credentials:
        return Credentials("reader", "pw")

    monkeypatch.setattr(worker.Reader, "read", _read)
    # One row a batch (RES-16): the table is created by the first batch and appended to by the
    # second, and the second is checked against the types the first set.
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

    status = seed.one("SELECT status, error FROM ingest.job_run WHERE job_id = %s", job)
    assert status[0] == "succeeded", status
    dataset = seed.one(
        "SELECT table_name FROM catalog.dataset WHERE org_id = %s AND origin = 'derived'", org
    )
    assert seed.all(f'SELECT chi_nhanh, amount FROM workspace."{dataset}" ORDER BY 1') == [
        ("HN", 10.5),
        ("SG", 20),
    ] or seed.all(f'SELECT chi_nhanh, amount::text FROM workspace."{dataset}" ORDER BY 1') == [
        ("HN", "10.50"),
        ("SG", "20.00"),
    ]
    # The types are read from the values the driver gives: MySQL's BOOLEAN is TINYINT(1), a number.
    assert seed.all(
        "SELECT c.name, c.normalized_type FROM catalog.column_meta c JOIN catalog.dataset d"
        " ON d.id = c.dataset_id WHERE d.table_name = %s ORDER BY c.ordinal",
        dataset,
    ) == [
        ("chi_nhanh", "string"),
        ("amount", "decimal"),
        ("sold_on", "date"),
        ("qty", "decimal"),
        ("paid", "decimal"),
        ("note", "string"),
    ]


async def test_an_engine_without_a_driver_is_refused() -> None:
    source = worker.catalog.Source(kind="oracle", conn_options={})
    with pytest.raises(ValueError, match="not wired"):
        async for _ in worker._source_batches(source, "t", uuid.uuid4()):
            pass


async def test_a_table_over_the_ceiling_fails_and_writes_nothing(
    seed: Seed, monkeypatch: pytest.MonkeyPatch, mysql: dict[str, Any]
) -> None:
    org = seed.row("org.organization", code=f"org-{uuid.uuid4().hex[:8]}", name="Ceiling")
    user = seed.row(
        "org.app_user", org_id=org, external_id="kc-cap", username="cap", display_name="Cap"
    )
    space = seed.row("org.data_space", org_id=org, code="DS-CAP", name="Cap", steward_id=user)
    source = seed.row(
        "catalog.source",
        org_id=org,
        data_space_id=space,
        name="Shop MySQL",
        kind="mysql",
        host=mysql["host"],
        port=mysql["port"],
        database_name="shop",
        conn_options=json.dumps({"sslmode": "disable"}),
        secret_ref=f"secret/tenants/{org}/sources/shop",
        mode="ingest",
        status="active",
    )
    job = seed.row(
        "ingest.ingestion_job",
        org_id=org,
        data_space_id=space,
        source_id=source,
        name="Capped",
        mode="full",
        source_object="sales",
        created_by=user,
        updated_by=user,
    )

    async def _read(_self: worker.Reader, _org: uuid.UUID, _ref: str) -> Credentials:
        return Credentials("reader", "pw")

    monkeypatch.setattr(worker.Reader, "read", _read)
    monkeypatch.setattr(worker, "BATCH_ROWS", 1)
    monkeypatch.setattr(worker, "MAX_ROWS", 1)
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
    status, error = seed.one("SELECT status, error FROM ingest.job_run WHERE job_id = %s", job)
    assert status == "failed" and "more than 1 rows" in error
    assert seed.one("SELECT count(*) FROM catalog.dataset WHERE org_id = %s", org) == 0


async def test_a_mysql_incremental_job_keeps_its_cursor(
    seed: Seed, monkeypatch: pytest.MonkeyPatch, mysql: dict[str, Any]
) -> None:
    """RES-17 through a driver: the bounds are literals, the cursor the source's own maximum."""
    org = seed.row("org.organization", code=f"org-{uuid.uuid4().hex[:8]}", name="MySQL inc")
    user = seed.row(
        "org.app_user", org_id=org, external_id="kc-myi", username="myi", display_name="MyI"
    )
    space = seed.row("org.data_space", org_id=org, code="DS-MYI", name="MyI", steward_id=user)
    source = seed.row(
        "catalog.source",
        org_id=org,
        data_space_id=space,
        name="Shop MySQL",
        kind="mysql",
        host=mysql["host"],
        port=mysql["port"],
        database_name="shop",
        conn_options=json.dumps({"sslmode": "disable"}),
        secret_ref=f"secret/tenants/{org}/sources/shop",
        mode="ingest",
        status="active",
    )
    job = seed.row(
        "ingest.ingestion_job",
        org_id=org,
        data_space_id=space,
        source_id=source,
        name="Sales by day",
        mode="incremental",
        cursor_column="sold_on",
        source_object="sales",
        created_by=user,
        updated_by=user,
    )

    async def _read(_self: worker.Reader, _org: uuid.UUID, _ref: str) -> Credentials:
        return Credentials("reader", "pw")

    monkeypatch.setattr(worker.Reader, "read", _read)
    for _ in range(2):
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
    assert (
        seed.one("SELECT cursor_value FROM ingest.ingestion_job WHERE id = %s", job) == "2026-09-30"
    )
    table = seed.one("SELECT table_name FROM catalog.dataset WHERE org_id = %s", org)
    assert seed.one(f'SELECT count(*) FROM workspace."{table}"') == 2
    assert seed.all(
        "SELECT rows_read FROM ingest.job_run WHERE job_id = %s ORDER BY started_at", job
    ) == [(2,), (0,)]
