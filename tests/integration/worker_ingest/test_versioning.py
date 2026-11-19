"""DF-02: a full run builds workspace.<name>__v<n> and points the view workspace.<name> at it in its
own transaction; the version before stays readable, a run that fails changes nothing a reader
sees, and a table written before versioning becomes the version it held instead of being dropped."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import pytest

from libs.dataagent_db import versioning
from services.worker_ingest import worker
from services.worker_ingest.events import Event
from services.worker_ingest.secrets import Credentials

from .conftest import Seed

pytestmark = pytest.mark.integration


@dataclass
class Job:
    org: uuid.UUID
    user: uuid.UUID
    id: uuid.UUID
    source_table: str


def _job(seed: Seed, dsn: str, monkeypatch: pytest.MonkeyPatch, mode: str = "full") -> Job:
    source_table = f"source_{uuid.uuid4().hex[:8]}"
    parts = urlsplit(dsn)
    with seed.conn.cursor() as cur:
        cur.execute(f'CREATE TABLE public."{source_table}" (branch text, n int)')
        cur.execute(f"INSERT INTO public.\"{source_table}\" VALUES ('HN', 1), ('SG', 2)")
    org = seed.row("org.organization", code=f"org-{uuid.uuid4().hex[:8]}", name="Versions")
    user = seed.row("org.app_user", org_id=org, external_id="kc-v", username="v", display_name="V")
    space = seed.row("org.data_space", org_id=org, code="DS-V", name="V", steward_id=user)
    source = seed.row(
        "catalog.source",
        org_id=org,
        data_space_id=space,
        name="Local",
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
        name="Branches",
        mode=mode,
        source_object=source_table,
        created_by=user,
        updated_by=user,
        **({"cursor_column": "n"} if mode == "incremental" else {}),
    )

    async def _read(_self: worker.Reader, _org: uuid.UUID, _ref: str) -> Credentials:
        return Credentials(str(parts.username), str(parts.password))

    monkeypatch.setattr(worker.Reader, "read", _read)
    monkeypatch.setattr(worker, "BATCH_ROWS", 1)
    return Job(org, user, job, source_table)


async def _run(job: Job) -> None:
    await worker.handle(
        Event(
            uuid.uuid4(),
            job.org,
            "ingestion_job.created",
            "ingestion_job",
            job.id,
            {"job_id": str(job.id), "principal_id": str(job.user)},
        )
    )


def _dataset(seed: Seed, job: Job) -> tuple[str, int]:
    return seed.all(
        "SELECT table_name, current_version FROM catalog.dataset WHERE org_id = %s", job.org
    )[0]


def _relkind(seed: Seed, name: str) -> Any:
    return seed.one(
        "SELECT c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
        " WHERE n.nspname = 'workspace' AND c.relname = %s",
        name,
    )


def _versions(seed: Seed, job: Job) -> list[tuple[Any, ...]]:
    return seed.all(
        "SELECT v.version, v.change_kind, v.physical_table, v.valid_to IS NOT NULL"
        " FROM catalog.dataset_version v JOIN catalog.dataset d ON d.id = v.dataset_id"
        " WHERE d.org_id = %s ORDER BY v.version",
        job.org,
    )


async def test_two_full_runs_leave_two_versions_and_the_view_on_the_second(
    seed: Seed, monkeypatch: pytest.MonkeyPatch, ddl_server: dict[str, str]
) -> None:
    job = _job(seed, ddl_server["dataagent"], monkeypatch)
    await _run(job)
    name, current = _dataset(seed, job)
    v1, v2 = versioning.physical_name(name, 1), versioning.physical_name(name, 2)
    assert current == 1 and _relkind(seed, name) == "v"
    assert seed.one(f'SELECT count(*) FROM workspace."{name}"') == 2

    with seed.conn.cursor() as cur:
        cur.execute(f"INSERT INTO public.\"{job.source_table}\" VALUES ('DN', 3)")
    await _run(job)
    assert _dataset(seed, job) == (name, 2)
    assert seed.one(f'SELECT count(*) FROM workspace."{name}"') == 3
    # The version before is still there, as it was.
    assert seed.one(f'SELECT count(*) FROM workspace."{v1}"') == 2
    assert _versions(seed, job) == [(1, "initial", v1, True), (2, "reload", v2, False)]
    # DF-05: each version is announced for its snapshot, with the run that wrote it.
    announced = seed.all(
        "SELECT payload FROM catalog.outbox WHERE org_id = %s AND event_type = %s"
        " ORDER BY created_at",
        job.org,
        versioning.VERSION_CREATED,
    )
    assert [
        (a[0]["version"], a[0]["physical_table"], a[0]["created_by_kind"]) for a in announced
    ] == [
        (1, v1, "job_run"),
        (2, v2, "job_run"),
    ]
    # Readers of the workspace read the view as they read the table.
    assert seed.one("SELECT has_table_privilege('svc_query', %s, 'SELECT')", f"workspace.{name}")


async def test_a_run_that_fails_changes_nothing_a_reader_sees(
    seed: Seed, monkeypatch: pytest.MonkeyPatch, ddl_server: dict[str, str]
) -> None:
    job = _job(seed, ddl_server["dataagent"], monkeypatch)
    await _run(job)
    name, _ = _dataset(seed, job)

    real = worker._source_batches

    async def broken(*args: Any, **kwargs: Any):  # type: ignore[no-untyped-def]
        sent = 0
        async for columns, batch in real(*args, **kwargs):
            if sent:
                raise ValueError("the source went away mid-run")
            sent += 1
            yield columns, batch

    monkeypatch.setattr(worker, "_source_batches", broken)
    await _run(job)
    assert (
        seed.one(
            "SELECT status FROM ingest.job_run WHERE job_id = %s ORDER BY started_at DESC LIMIT 1",
            job.id,
        )
        == "failed"
    )
    assert _dataset(seed, job) == (name, 1)
    assert seed.one(f'SELECT count(*) FROM workspace."{name}"') == 2
    assert _relkind(seed, versioning.physical_name(name, 2)) is None
    assert [v[:2] for v in _versions(seed, job)] == [(1, "initial")]


async def test_a_table_written_before_versioning_becomes_its_version(
    seed: Seed, monkeypatch: pytest.MonkeyPatch, ddl_server: dict[str, str]
) -> None:
    job = _job(seed, ddl_server["dataagent"], monkeypatch)
    monkeypatch.setattr(versioning, "ENABLED", False)
    await _run(job)
    name, _ = _dataset(seed, job)
    assert _relkind(seed, name) == "r"
    assert _versions(seed, job) == [(1, "initial", None, False)]

    monkeypatch.setattr(versioning, "ENABLED", True)
    await _run(job)
    v1 = versioning.physical_name(name, 1)
    assert _relkind(seed, name) == "v" and _relkind(seed, v1) == "r"
    assert seed.one(f'SELECT count(*) FROM workspace."{v1}"') == 2
    assert _versions(seed, job) == [
        (1, "initial", v1, True),
        (2, "reload", versioning.physical_name(name, 2), False),
    ]


async def test_an_incremental_run_appends_to_the_current_version_and_shows_new_columns(
    seed: Seed, monkeypatch: pytest.MonkeyPatch, ddl_server: dict[str, str]
) -> None:
    job = _job(seed, ddl_server["dataagent"], monkeypatch)
    await _run(job)  # full: version 1
    name, _ = _dataset(seed, job)
    v1 = versioning.physical_name(name, 1)
    with seed.conn.cursor() as cur:
        cur.execute(
            "UPDATE ingest.ingestion_job SET mode = 'incremental', cursor_column = 'n',"
            " cursor_value = '2' WHERE id = %s",
            (str(job.id),),
        )
        cur.execute(f'ALTER TABLE public."{job.source_table}" ADD COLUMN region text')
        cur.execute(f"INSERT INTO public.\"{job.source_table}\" VALUES ('DN', 3, 'Trung')")
    await _run(job)
    assert seed.one(f'SELECT count(*) FROM workspace."{v1}"') == 3
    assert seed.one(f'SELECT region FROM workspace."{name}" WHERE n = 3') == "Trung"
    versions = _versions(seed, job)
    assert versions[-1][:3] == (2, "additive", v1)
    assert _dataset(seed, job) == (name, 2)


def test_a_version_name_fits_postgres() -> None:
    long = "ingest_" + "x" * 45 + "_abcdef12"
    assert len(versioning.physical_name(long, versioning.MAX_VERSION)) == 63
    with pytest.raises(ValueError):
        versioning.physical_name(long, versioning.MAX_VERSION + 1)


def test_long_names_alike_in_55_characters_keep_their_own_versions(seed: Seed) -> None:
    # Two ingest tables of two jobs whose stems start alike differ only in the job id at the end:
    # their versions must not share a table (security reviews of 5d59b877 and ba8fee4c).
    one, two = ("ingest_" + "s" * 45 + f"_{job}" for job in ("0a1b2c3d", "0a1b2c3e"))
    assert one[:55] == two[:55]
    assert versioning.version_base(one) != versioning.version_base(two)
    # A plain stem has 54 characters at most and a hashed one 55: one never equals the other.
    assert versioning.version_base("a" * 54) == "a" * 54
    assert len(versioning.version_base("a" * 55)) == 55 != len("a" * 54)
    # The routine derives the same base: it takes the worker's name and refuses the other job's.
    physical = versioning.physical_name(one, 1)
    with seed.conn.cursor() as cur:
        cur.execute("SET ROLE da_owner")
        cur.execute(f'CREATE TABLE workspace."{physical}" (n int)')
        cur.execute(f'CREATE TABLE workspace."{versioning.physical_name(two, 1)}" (n int)')
        cur.execute("RESET ROLE")
        cur.execute("SELECT set_config('app.org_id', %s, false)", (str(uuid.uuid4()),))
        cur.execute("SELECT public.da_swap_version(%s, %s)", (one, physical))
    assert _relkind(seed, one) == "v"
    with pytest.raises(Exception, match="version"), seed.conn.cursor() as cur:
        cur.execute(
            "SELECT public.da_swap_version(%s, %s)", (one, versioning.physical_name(two, 1))
        )


async def test_only_the_organisation_of_a_dataset_swaps_its_view(
    seed: Seed, monkeypatch: pytest.MonkeyPatch, ddl_server: dict[str, str]
) -> None:
    # The workspace schema is every organisation's: another one cannot point a dataset's view at
    # a table of its own, nor rename the dataset's table as a legacy version.
    job = _job(seed, ddl_server["dataagent"], monkeypatch)
    await _run(job)
    name, _ = _dataset(seed, job)
    with seed.conn.cursor() as cur:
        cur.execute("SET ROLE da_owner")
        cur.execute(f'CREATE TABLE workspace."{versioning.physical_name(name, 9)}" (n int)')
        cur.execute("RESET ROLE")
        cur.execute("SELECT set_config('app.org_id', %s, false)", (str(uuid.uuid4()),))
    with pytest.raises(Exception, match="another organisation"), seed.conn.cursor() as cur:
        cur.execute(
            "SELECT public.da_swap_version(%s, %s)", (name, versioning.physical_name(name, 9))
        )
    assert seed.one(f'SELECT count(*) FROM workspace."{name}"') == 2
