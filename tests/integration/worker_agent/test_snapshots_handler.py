"""DF-05: a version written whole is announced, and worker-agent turns it into its snapshot row,
its file and `dataset.snapshot_created`, once, whatever the redeliveries."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest

from libs.dataagent_db import versioning
from libs.snapshot.store import StoreUnavailable
from services.worker_agent import snapshots, uploads
from services.worker_agent.events import Event

from .conftest import Seed

pytestmark = [pytest.mark.integration]


class FakeStore:
    def __init__(self, refuse: bool = False) -> None:
        self.objects: dict[str, bytes] = {}
        self.refuse = refuse

    async def put(self, key: str, path: Path) -> bool:
        if self.refuse:
            raise StoreUnavailable("the object store is unreachable: 503")
        created = key not in self.objects
        self.objects[key] = path.read_bytes()
        return created

    async def get(self, key: str, path: Path) -> None:
        path.write_bytes(self.objects[key])


async def _uploaded(seed: Seed, monkeypatch: pytest.MonkeyPatch) -> tuple[uuid.UUID, uuid.UUID]:
    org = seed.row("org.organization", code=f"org-{uuid.uuid4().hex[:8]}", name="Snapshots")
    user = seed.row("org.app_user", org_id=org, external_id="kc-s", username="s", display_name="S")
    space = seed.row("org.data_space", org_id=org, code="DS-S", name="S", steward_id=user)
    dataset = uuid.uuid4()
    upload = seed.row(
        "ingest.file_upload",
        org_id=org,
        data_space_id=space,
        uploader_id=user,
        file_name="chi_nhanh.csv",
        object_key="k",
        size_bytes=1,
        column_map=json.dumps(
            [{"name": "branch", "type": "string"}, {"name": "n", "type": "number"}]
        ),
        target_dataset_id=dataset,
        error_ratio=0,
        status="committed",
    )

    async def _read(_key: str) -> bytes:
        return b"branch,n\nHN,1\nSG,2\n"

    monkeypatch.setattr(uploads, "_read_object", _read)
    await uploads.handle(
        Event(
            uuid.uuid4(), org, uploads.COMMITTED, "file_upload", upload, {"uploader_id": str(user)}
        )
    )
    return org, dataset


def _announced(seed: Seed, dataset: uuid.UUID) -> tuple[uuid.UUID, dict[str, Any]]:
    row = seed.all(
        "SELECT id, payload FROM catalog.outbox WHERE aggregate_id = %s AND event_type = %s",
        dataset,
        versioning.VERSION_CREATED,
    )
    assert len(row) == 1
    return row[0][0], row[0][1]


async def test_a_new_version_becomes_one_snapshot_whatever_the_redeliveries(
    seed: Seed, monkeypatch: pytest.MonkeyPatch
) -> None:
    org, dataset = await _uploaded(seed, monkeypatch)
    event_id, payload = _announced(seed, dataset)
    name = seed.one("SELECT table_name FROM catalog.dataset WHERE id = %s", dataset)
    assert payload["version"] == 1 and payload["created_by_kind"] == "upload"
    assert payload["physical_table"] == versioning.physical_name(name, 1)

    store = FakeStore()
    monkeypatch.setattr(snapshots, "_store", store)
    event = Event(event_id, org, versioning.VERSION_CREATED, "dataset", dataset, payload)
    await snapshots.version_created(event)
    await snapshots.version_created(event)  # redelivered
    rows = seed.all(
        "SELECT status, row_count, sha256, object_key, created_by_kind FROM catalog.dataset_snapshot"
        " WHERE dataset_id = %s",
        dataset,
    )
    assert len(rows) == 1
    status, count, sha, key, kind = rows[0]
    assert (status, count, kind) == ("ok", 2, "upload") and key in store.objects
    assert key == f"{org}/{dataset}/v1/{sha[:12]}.parquet"
    created = seed.all(
        "SELECT payload FROM catalog.outbox WHERE aggregate_id = %s AND event_type = %s",
        dataset,
        snapshots.SNAPSHOT_CREATED,
    )
    assert len(created) == 1 and created[0][0]["sha256"] == sha and created[0][0]["row_count"] == 2


async def test_a_store_that_refuses_leaves_a_skipped_row_not_a_failed_event(
    seed: Seed, monkeypatch: pytest.MonkeyPatch
) -> None:
    org, dataset = await _uploaded(seed, monkeypatch)
    event_id, payload = _announced(seed, dataset)
    monkeypatch.setattr(snapshots, "_store", FakeStore(refuse=True))
    await snapshots.version_created(
        Event(event_id, org, versioning.VERSION_CREATED, "dataset", dataset, payload)
    )
    assert seed.all(
        "SELECT status, row_count, object_key FROM catalog.dataset_snapshot WHERE dataset_id = %s",
        dataset,
    ) == [("skipped", 2, None)]
    (created,) = seed.all(
        "SELECT payload FROM catalog.outbox WHERE aggregate_id = %s AND event_type = %s",
        dataset,
        snapshots.SNAPSHOT_CREATED,
    )
    assert created[0]["snapshot_id"] is None and created[0]["status"] == "skipped"
