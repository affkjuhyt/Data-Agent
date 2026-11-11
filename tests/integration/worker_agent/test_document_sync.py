"""A document source synced from an object store (UC-05-01 1a), on the V3 DDL as svc_document.

The bucket and the platform's raw store are fakes at the S3 boundary; under test is what Postgres
holds afterwards: new and changed files become documents the way an upload makes them (row,
original in da-raw-docs, document.received), files gone from the source are removed, and the
source records when it last synced or that it failed.
"""

from __future__ import annotations

import datetime
import hashlib
import uuid
from dataclasses import dataclass, field
from typing import Any, BinaryIO

import pytest

from services.worker_agent import questions
from services.worker_agent.documents import sync
from services.worker_agent.events import Event
from services.worker_agent.query.secrets import StaticReader

from .conftest import Seed

pytestmark = [pytest.mark.integration]

EARLIER = datetime.datetime(2026, 10, 1, tzinfo=datetime.UTC)
LAST_SYNC = datetime.datetime(2026, 10, 2, tzinfo=datetime.UTC)
LATER = datetime.datetime(2026, 10, 3, tzinfo=datetime.UTC)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class FakeBucket:
    files: dict[str, tuple[bytes, datetime.datetime]]
    reads: list[str] = field(default_factory=list)

    async def objects(self, limit: int) -> tuple[list[sync.SourceObject], bool]:
        found = [
            sync.SourceObject(key, len(data), modified)
            for key, (data, modified) in sorted(self.files.items())
        ]
        return found[:limit], len(found) <= limit

    async def fetch(self, key: str, into: BinaryIO, max_bytes: int) -> None:
        self.reads.append(key)
        into.write(self.files[key][0][:max_bytes])


@dataclass
class FakeRaw:
    objects: dict[tuple[str, str], bytes] = field(default_factory=dict)

    async def exists(self, bucket: str, key: str) -> bool:
        return (bucket, key) in self.objects

    async def put(self, bucket: str, key: str, data: BinaryIO, size: int) -> None:
        self.objects[(bucket, key)] = data.read()


@dataclass
class Library:
    org: uuid.UUID
    user: uuid.UUID
    space: uuid.UUID
    collection: uuid.UUID
    source: uuid.UUID
    ref: str


def library(seed: Seed, *, location: str = "s3://phap-che/quyet-dinh/", **source: Any) -> Library:
    org = seed.row("org.organization", code=f"org-{uuid.uuid4().hex[:8]}", name="Ngân hàng thử")
    user = seed.row("org.app_user", org_id=org, external_id="kc-a", username="a", display_name="A")
    space = seed.row("org.data_space", org_id=org, code="DS-NH", name="Ngân hàng", steward_id=user)
    collection = seed.row(
        "document.collection",
        org_id=org,
        data_space_id=space,
        code="laws",
        name="Văn bản pháp quy",
        purpose="tra cứu",
        qdrant_collection=f"doc_{uuid.uuid4().hex}",
        embed_model="bge-m3",
    )
    source_id = uuid.uuid4()
    ref = f"secret/tenants/{org}/sources/document-source/{source_id}"
    source.setdefault("secret_ref", ref)
    seed.row(
        "document.document_source",
        id=source_id,
        org_id=org,
        collection_id=collection,
        kind=source.pop("kind", "object_store"),
        location=location,
        sync_cron="0 2 * * *",
        last_synced_at=LAST_SYNC,
        **source,
    )
    return Library(org, user, space, collection, source_id, ref)


def synced(seed: Seed, lib: Library, name: str, data: bytes) -> uuid.UUID:
    """A document an earlier sync of the source made."""
    return seed.row(
        "document.document",
        org_id=lib.org,
        collection_id=lib.collection,
        document_source_id=lib.source,
        sha256=sha(data),
        media="pdf",
        file_name=name,
        object_key=f"{lib.org}/{lib.collection}/{sha(data)}",
        size_bytes=len(data),
        status="ready",
        created_at=EARLIER,
    )


def event(lib: Library) -> Event:
    return Event(
        uuid.uuid4(),
        lib.org,
        sync.SYNC_REQUESTED,
        "document_source",
        lib.source,
        {
            "document_source_id": str(lib.source),
            "collection_id": str(lib.collection),
            "data_space_id": str(lib.space),
            "requested_by": str(lib.user),
            "status": "active",
        },
    )


@pytest.fixture
def raw(monkeypatch: pytest.MonkeyPatch) -> FakeRaw:
    store = FakeRaw()
    monkeypatch.setattr(sync, "raw_store", lambda: store)
    return store


def given(
    monkeypatch: pytest.MonkeyPatch, lib: Library, bucket: FakeBucket, *, secret: bool = True
) -> list[tuple[sync.Location, Any]]:
    opened: list[tuple[sync.Location, Any]] = []

    def open_bucket(location: sync.Location, credentials: Any) -> FakeBucket:
        opened.append((location, credentials))
        return bucket

    monkeypatch.setattr(sync, "open_bucket", open_bucket)
    secrets = {lib.ref: {"user": "AKIA-DOC", "password": "s3cret"}} if secret else {}
    monkeypatch.setattr(questions, "_secrets", StaticReader(secrets))
    return opened


# AC-05-01-03
@pytest.mark.req("AC-05-01-03")
async def test_new_and_changed_files_are_ingested_and_a_file_gone_is_removed(
    seed: Seed, raw: FakeRaw, monkeypatch: pytest.MonkeyPatch
) -> None:
    lib = library(seed)
    same, old_b, new_b, new_c = b"%PDF a", b"%PDF b v1", b"%PDF b v2", b"PK docx c"
    kept = synced(seed, lib, "a.pdf", same)
    replaced = synced(seed, lib, "2026/b.pdf", old_b)
    gone = synced(seed, lib, "gone.pdf", b"%PDF gone")
    bucket = FakeBucket(
        {
            "quyet-dinh/a.pdf": (same, EARLIER),
            "quyet-dinh/2026/b.pdf": (new_b, LATER),
            "quyet-dinh/c.docx": (new_c, LATER),
            "quyet-dinh/archive.rar": (b"Rar!", LATER),
            "quyet-dinh/empty.pdf": (b"", LATER),
            "quyet-dinh/": (b"", LATER),
        }
    )
    opened = given(monkeypatch, lib, bucket)

    await sync.sync_source(event(lib))

    [(location, credentials)] = opened
    assert (location.bucket, location.prefix, location.endpoint) == (
        "phap-che",
        "quyet-dinh/",
        None,
    )
    assert credentials.user == "AKIA-DOC"
    # The unchanged file, older than the last sync and of the same size, is not even read.
    assert sorted(bucket.reads) == ["quyet-dinh/2026/b.pdf", "quyet-dinh/c.docx"]
    by_id = {
        row[0]: row[1:]
        for row in seed.all(
            "SELECT id, file_name, status, deleted_at IS NOT NULL FROM document.document"
            " WHERE collection_id = %s",
            lib.collection,
        )
    }
    assert by_id[kept] == ("a.pdf", "ready", False)
    assert by_id[replaced] == ("2026/b.pdf", "removed", True)
    assert by_id[gone] == ("gone.pdf", "removed", True)
    fresh = seed.all(
        "SELECT file_name, status, sha256, media, document_source_id, created_by, object_key,"
        " size_bytes, id FROM document.document"
        " WHERE collection_id = %s AND deleted_at IS NULL AND id <> %s ORDER BY file_name",
        lib.collection,
        kept,
    )
    assert [r[:8] for r in fresh] == [
        (
            "2026/b.pdf",
            "received",
            sha(new_b),
            "pdf",
            lib.source,
            lib.user,
            f"{lib.org}/{lib.collection}/{sha(new_b)}",
            len(new_b),
        ),
        (
            "c.docx",
            "received",
            sha(new_c),
            "docx",
            lib.source,
            lib.user,
            f"{lib.org}/{lib.collection}/{sha(new_c)}",
            len(new_c),
        ),
    ]
    # The originals are in the raw bucket, content-addressed like an upload's.
    assert raw.objects == {
        ("da-raw-docs", f"{lib.org}/{lib.collection}/{sha(new_b)}"): new_b,
        ("da-raw-docs", f"{lib.org}/{lib.collection}/{sha(new_c)}"): new_c,
    }
    # worker-document is asked to preprocess each, as after an upload.
    events = seed.all(
        "SELECT aggregate_id, payload->>'media', payload->>'sha256', payload->>'data_space_id'"
        " FROM document.outbox WHERE org_id = %s AND event_type = 'document.received'"
        " ORDER BY payload->>'media'",
        lib.org,
    )
    assert events == [
        (fresh[1][8], "docx", sha(new_c), str(lib.space)),
        (fresh[0][8], "pdf", sha(new_b), str(lib.space)),
    ]
    status, last = seed.one(
        "SELECT status, last_synced_at FROM document.document_source WHERE id = %s", lib.source
    )
    assert status == "active" and last > LATER

    # Run again: nothing changed at the source, so nothing is read or added.
    bucket.reads.clear()
    bucket.files["quyet-dinh/2026/b.pdf"] = (new_b, EARLIER)
    bucket.files["quyet-dinh/c.docx"] = (new_c, EARLIER)
    await sync.sync_source(event(lib))
    assert bucket.reads == []
    assert (
        seed.one(
            "SELECT count(*) FROM document.document WHERE collection_id = %s"
            " AND deleted_at IS NULL",
            lib.collection,
        )
        == 3
    )


async def test_a_sync_is_bounded_per_run_and_skips_what_an_upload_would_refuse(
    seed: Seed, raw: FakeRaw, monkeypatch: pytest.MonkeyPatch
) -> None:
    lib = library(seed, location="https://10.20.0.5:9000/kho/")
    monkeypatch.setattr(sync, "MAX_FILES_PER_SYNC", 1)
    monkeypatch.setattr(sync, "MAX_FILE_BYTES", 10)
    bucket = FakeBucket(
        {
            "1.pdf": (b"%PDF 1", LATER),
            "2.pdf": (b"%PDF 2", LATER),
            "huge.pdf": (b"%PDF far too large", LATER),
        }
    )
    opened = given(monkeypatch, lib, bucket)

    await sync.sync_source(event(lib))

    [(location, _)] = opened
    assert (location.endpoint, location.bucket, location.prefix) == (
        "https://10.20.0.5:9000",
        "kho",
        "",
    )
    # One file this run, the next one the next run; the oversized one never.
    assert bucket.reads == ["1.pdf"]
    names = seed.all(
        "SELECT file_name FROM document.document WHERE collection_id = %s", lib.collection
    )
    assert names == [("1.pdf",)]
    await sync.sync_source(event(lib))
    assert bucket.reads == ["1.pdf", "2.pdf"]
    assert (
        seed.one("SELECT status FROM document.document_source WHERE id = %s", lib.source)
        == "active"
    )


async def test_a_listing_cut_short_removes_nothing(
    seed: Seed, raw: FakeRaw, monkeypatch: pytest.MonkeyPatch
) -> None:
    lib = library(seed)
    kept = synced(seed, lib, "z.pdf", b"%PDF z")
    monkeypatch.setattr(sync, "MAX_LISTED", 1)
    # z.pdf is past the listing's limit, not gone: it must stay.
    given(
        monkeypatch,
        lib,
        FakeBucket(
            {"quyet-dinh/a.pdf": (b"%PDF a", LATER), "quyet-dinh/z.pdf": (b"%PDF z", EARLIER)}
        ),
    )
    await sync.sync_source(event(lib))
    assert seed.one("SELECT status FROM document.document WHERE id = %s", kept) == "ready"


async def test_a_source_whose_secret_cannot_be_read_is_marked_in_error(
    seed: Seed, raw: FakeRaw, monkeypatch: pytest.MonkeyPatch
) -> None:
    lib = library(seed)
    bucket = FakeBucket({"quyet-dinh/a.pdf": (b"%PDF a", LATER)})
    opened = given(monkeypatch, lib, bucket, secret=False)
    await sync.sync_source(event(lib))
    assert opened == [] and raw.objects == {}
    status, last = seed.one(
        "SELECT status, last_synced_at FROM document.document_source WHERE id = %s", lib.source
    )
    assert status == "error" and last == LAST_SYNC

    # Another organisation's secret is refused before OpenBao is asked.
    foreign = library(seed, secret_ref=f"secret/tenants/{uuid.uuid4()}/sources/x")
    given(monkeypatch, foreign, bucket)
    await sync.sync_source(event(foreign))
    assert (
        seed.one("SELECT status FROM document.document_source WHERE id = %s", foreign.source)
        == "error"
    )


async def test_a_paused_source_or_another_kind_is_left_alone(
    seed: Seed, raw: FakeRaw, monkeypatch: pytest.MonkeyPatch
) -> None:
    paused = library(seed, status="paused")
    smb = library(seed, kind="smb_folder", location="smb://fileserver/phap-che")
    bucket = FakeBucket({"quyet-dinh/a.pdf": (b"%PDF a", LATER)})
    opened = given(monkeypatch, paused, bucket)
    await sync.sync_source(event(paused))
    await sync.sync_source(event(smb))
    assert opened == []
    assert seed.all(
        "SELECT status FROM document.document_source WHERE id IN (%s, %s) ORDER BY status",
        paused.source,
        smb.source,
    ) == [("active",), ("paused",)]


def test_a_location_names_the_endpoint_bucket_and_prefix() -> None:
    assert sync.parse_location("phap-che") == sync.Location(None, "phap-che", "")
    assert sync.parse_location("phap-che/quyet-dinh") == sync.Location(
        None, "phap-che", "quyet-dinh/"
    )
    assert sync.parse_location("http://10.0.0.5:9000/kho/a/b/") == sync.Location(
        "http://10.0.0.5:9000", "kho", "a/b/"
    )
    for bad in ("s3://", "https://user:pw@host/kho", "ftp://host/kho", "s3://../x", ""):
        with pytest.raises(sync.SourceRefused):
            sync.parse_location(bad)


async def test_an_endpoint_inside_the_platform_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MINIO_URL", "http://key:secret@minio.database.svc:9000")
    for endpoint in (
        "http://127.0.0.1:9000",
        "http://169.254.169.254",
        "http://minio.database.svc:9000",
        "http://localhost:9000",
    ):
        with pytest.raises(sync.SourceRefused):
            await sync.check_endpoint(endpoint)
    # An on-premises store on the bank's own network is the usual case and is allowed.
    await sync.check_endpoint("http://10.20.0.5:9000")
