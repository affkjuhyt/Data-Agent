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
        await worker._read_source(source, "t", uuid.uuid4())
