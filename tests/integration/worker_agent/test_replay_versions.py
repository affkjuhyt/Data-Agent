"""DF-06: a receipt's figure is read again at the dataset version the answer read, from that
version's table while it stands and from its Parquet snapshot after, with the asker's policies
as on the live path; with neither, it says so instead of reading today's rows."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest

from libs.dataagent_db import versioning
from libs.dataagent_db.models import query as q
from services.worker_agent import replay, snapshots, uploads
from services.worker_agent.events import Event
from services.worker_agent.query.executor import run_query
from services.worker_agent.questions import _internal_workspace_source, secrets

from .conftest import Seed

pytestmark = [pytest.mark.integration]


class FakeStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put(self, key: str, path: Path) -> bool:
        created = key not in self.objects
        self.objects[key] = path.read_bytes()
        return created

    async def get(self, key: str, path: Path) -> None:
        path.write_bytes(self.objects[key])


class World:
    def __init__(self, seed: Seed) -> None:
        self.seed = seed
        self.org = seed.row("org.organization", code=f"org-{uuid.uuid4().hex[:8]}", name="Replay")
        self.user = seed.row(
            "org.app_user", org_id=self.org, external_id="kc-r", username="r", display_name="R"
        )
        self.space = seed.row(
            "org.data_space", org_id=self.org, code="DS-R", name="R", steward_id=self.user
        )
        self.dataset = uuid.uuid4()
        self.files: dict[str, bytes] = {}

    async def upload(self, key: str, body: bytes, **extra: Any) -> None:
        self.files[key] = body
        upload = self.seed.row(
            "ingest.file_upload",
            org_id=self.org,
            data_space_id=self.space,
            uploader_id=self.user,
            file_name="chi_nhanh.csv",
            object_key=key,
            size_bytes=1,
            column_map=json.dumps(
                [{"name": "branch", "type": "string"}, {"name": "n", "type": "number"}]
            ),
            target_dataset_id=self.dataset,
            error_ratio=0,
            status="committed",
            **extra,
        )
        await uploads.handle(
            Event(
                uuid.uuid4(),
                self.org,
                uploads.COMMITTED,
                "file_upload",
                upload,
                {"uploader_id": str(self.user)},
            )
        )
        # worker-agent snapshots the version the upload announced (DF-05).
        for event_id, payload in self.seed.all(
            "SELECT id, payload FROM catalog.outbox WHERE aggregate_id = %s AND event_type = %s"
            " AND published_at IS NULL",
            self.dataset,
            versioning.VERSION_CREATED,
        ):
            await snapshots.version_created(
                Event(
                    event_id,
                    self.org,
                    versioning.VERSION_CREATED,
                    "dataset",
                    self.dataset,
                    payload,
                )
            )
            with self.seed.conn.cursor() as cur:
                cur.execute(
                    "UPDATE catalog.outbox SET published_at = now() WHERE id = %s",
                    (str(event_id),),
                )

    @property
    def name(self) -> str:
        return str(
            self.seed.one("SELECT table_name FROM catalog.dataset WHERE id = %s", self.dataset)
        )

    async def answer(self, sql: str) -> q.QueryRun:
        """The query an answer ran, as its receipt keeps it."""
        source = _internal_workspace_source(self.space, self.org)
        out = await run_query(
            org_id=self.org,
            data_space_id=self.space,
            principal_id=self.user,
            caller_kind="question",
            caller_id=uuid.uuid4(),
            source=source,
            sql=sql,
            secrets=secrets(),
        )
        assert out.ok, out.error
        return q.QueryRun(
            id=out.query_run_id,
            data_space_id=self.space,
            source_id=source.id,
            sql_generated=sql,
            metric_version_ids=[],
            as_of=None,
        )

    async def replayed(self, original: q.QueryRun, version: int) -> tuple[Any, bool]:
        return await replay.rerun_query(
            org_id=self.org,
            requested_by=self.user,
            replay_id=uuid.uuid4(),
            original=original,
            source=_internal_workspace_source(self.space, self.org),
            recorded=[{"dataset_id": str(self.dataset), "version": version}],
        )


@pytest.fixture
async def world(seed: Seed, monkeypatch: pytest.MonkeyPatch) -> World:
    w = World(seed)

    async def _read(key: str) -> bytes:
        return w.files[key]

    monkeypatch.setattr(uploads, "_read_object", _read)
    monkeypatch.setattr(snapshots, "_store", FakeStore())
    await w.upload("v1", b"branch,n\nHN,1\nSG,2\n")
    return w


@pytest.mark.req("DF-06")
async def test_replay_reads_old_version(world: World) -> None:
    sql = f'SELECT sum(n) AS value FROM workspace."{world.name}"'
    original = await world.answer(sql)
    # Before anything moved on, the live table is the version: read live.
    same, at_version = await world.replayed(original, 1)
    assert same.ok and same.rows == [["3"]] and not at_version

    await world.upload("v2", b"branch,n\nDN,30\n", replace_mode="replace")
    assert (await world.answer(sql)).id  # today's rows answer 30 now
    v1 = versioning.physical_name(world.name, 1)

    # The version's own table still stands: read there, recorded as a history run.
    kept, at_version = await world.replayed(original, 1)
    assert kept.ok, kept.error
    assert kept.rows == [["3"]] and at_version
    route, executed = world.seed.one(
        "SELECT route, sql_executed FROM query.query_run WHERE id = %s", kept.query_run_id
    )
    assert route == "history" and v1 in executed

    # Retention dropped it: its Parquet snapshot answers the same.
    with world.seed.conn.cursor() as cur:
        cur.execute(f'DROP TABLE workspace."{v1}"')
    from_file, at_version = await world.replayed(original, 1)
    assert from_file.ok, from_file.error
    assert from_file.rows == [["3"]] and at_version

    # Neither table nor snapshot: not today's rows, but why it cannot.
    with world.seed.conn.cursor() as cur:
        cur.execute(
            "DELETE FROM catalog.dataset_snapshot WHERE dataset_id = %s AND version = 1",
            (str(world.dataset),),
        )
    gone, _ = await world.replayed(original, 1)
    assert not gone.ok and "neither its table nor a snapshot" in (gone.error or "")


async def test_a_replay_from_a_snapshot_keeps_the_askers_policies(world: World) -> None:
    sql = f'SELECT * FROM workspace."{world.name}" ORDER BY n'
    original = await world.answer(sql)
    await world.upload("v2", b"branch,n\nDN,30\n", replace_mode="replace")
    with world.seed.conn.cursor() as cur:
        cur.execute(f'DROP TABLE workspace."{versioning.physical_name(world.name, 1)}"')
    # A mask on the branch since the answer: the replay shows it masked, as a live read would.
    world.seed.row(
        "governance.access_policy",
        org_id=world.org,
        data_space_id=world.space,
        dataset_id=world.dataset,
        name="Che chi nhánh",
        kind="mask",
        columns=["branch"],
        mask_method="last4",
        status="active",
        simulated_at="2026-11-19T00:00:00Z",
    )
    masked, at_version = await world.replayed(original, 1)
    assert masked.ok, masked.error
    assert at_version
    assert [c["name"] for c in masked.columns] == ["branch", "n"]
    assert masked.rows == [["****HN", "1"], ["****SG", "2"]]
