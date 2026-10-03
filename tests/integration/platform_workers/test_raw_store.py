"""The immutable raw layer (S2-02), against the migrated schema.

Runs the writer as crawler_svc, the role the web crawler uses for the ingest
schema on the cluster (ADR-V2-46). The object store is an in-memory stand-in
for MinIO: what is asserted is that an object is written once and never
removed, which is the crawler's side of the contract (object lock is MinIO's).
"""

from __future__ import annotations

import io
import uuid
from typing import Any
from unittest.mock import patch

import psycopg2
import pytest
from minio.error import S3Error
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from libs.ingestion.connectors.base import FetchedArtifact
from services.platform_workers.workers.web_crawler import raw

pytestmark = pytest.mark.integration

_CRAWLER_SVC_PASSWORD = "crawler-svc-test-pw"


class _Objects:
    """Records every write; has no delete, like the crawler's use of MinIO."""

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.puts = 0

    def stat_object(self, bucket_name: str, object_name: str) -> Any:
        if (bucket_name, object_name) not in self.objects:
            raise S3Error("NoSuchKey", "missing", object_name, None, None, None)  # type: ignore[arg-type]
        return object()

    def put_object(
        self,
        bucket_name: str,
        object_name: str,
        data: io.BytesIO,
        length: int,
        content_type: str = "",
    ) -> Any:
        self.puts += 1
        self.objects[(bucket_name, object_name)] = data.read(length)


def _artifact(body: bytes) -> FetchedArtifact:
    return FetchedArtifact(
        content_bytes=body,
        content_type="text/csv",
        source_url="https://example.invalid/a.csv",
        extension="csv",
    )


@pytest.fixture
def crawler_svc_dsn(migrated_db) -> str:
    conn = psycopg2.connect(migrated_db["dsn"])
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(f"ALTER ROLE crawler_svc LOGIN PASSWORD '{_CRAWLER_SVC_PASSWORD}'")
    finally:
        conn.close()
    url = make_url(migrated_db["dsn"]).set(username="crawler_svc", password=_CRAWLER_SVC_PASSWORD)
    return url.render_as_string(hide_password=False)


def _store(dsn: str) -> raw.RawStore:
    engine = create_engine(dsn.replace("postgresql://", "postgresql+psycopg2://", 1), future=True)
    return raw.RawStore(objects=_Objects(), ingest_sessions=sessionmaker(bind=engine, future=True))


def _rows(dsn: str, sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchall())
    finally:
        conn.close()


def test_one_artifact_per_content_one_fetch_per_download_nothing_removed(
    migrated_db, crawler_svc_dsn
) -> None:
    tenant, source = str(uuid.uuid4()), str(uuid.uuid4())
    store = _store(crawler_svc_dsn)

    first = store.keep(
        _artifact(b"v1"), tenant_id=tenant, source_id=source, run_id=str(uuid.uuid4())
    )
    again = store.keep(
        _artifact(b"v1"), tenant_id=tenant, source_id=source, run_id=str(uuid.uuid4())
    )
    second = store.keep(
        _artifact(b"v2"), tenant_id=tenant, source_id=source, run_id=str(uuid.uuid4())
    )

    assert first == again != second
    objects = store.objects
    assert objects.puts == 2  # type: ignore[attr-defined]
    assert set(objects.objects) == {("raw", f"{source}/{first}"), ("raw", f"{source}/{second}")}  # type: ignore[attr-defined]
    dsn = migrated_db["dsn"]
    assert sorted(r[0] for r in _rows(dsn, "SELECT sha256 FROM ingest.artifact")) == sorted(
        [first, second]
    )
    assert _rows(dsn, "SELECT count(*) FROM ingest.fetch") == [(3,)]


@pytest.mark.parametrize("role", ["crawler_svc", "ingest_svc"])
def test_no_role_updates_or_deletes_the_raw_record(migrated_db, crawler_svc_dsn, role: str) -> None:
    tenant = str(uuid.uuid4())
    _store(crawler_svc_dsn).keep(
        _artifact(b"x"), tenant_id=tenant, source_id=str(uuid.uuid4()), run_id=str(uuid.uuid4())
    )
    conn = psycopg2.connect(migrated_db["dsn"])
    try:
        with conn.cursor() as cur:
            cur.execute(f"SET ROLE {role}")
            cur.execute("SELECT set_config('app.tenant_id', %s, false)", (tenant,))
            for statement in ("UPDATE ingest.artifact SET bytes = 0", "DELETE FROM ingest.fetch"):
                cur.execute("SAVEPOINT s")
                with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                    cur.execute(statement)
                cur.execute("ROLLBACK TO SAVEPOINT s")
    finally:
        conn.rollback()
        conn.close()


def test_another_tenant_does_not_see_the_raw_record(migrated_db, crawler_svc_dsn) -> None:
    mine, other = str(uuid.uuid4()), str(uuid.uuid4())
    _store(crawler_svc_dsn).keep(
        _artifact(b"x"), tenant_id=mine, source_id=str(uuid.uuid4()), run_id=str(uuid.uuid4())
    )
    conn = psycopg2.connect(crawler_svc_dsn)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id', %s, false)", (other,))
            cur.execute("SELECT count(*) FROM ingest.artifact")
            assert cur.fetchone() == (0,)
    finally:
        conn.close()


def test_a_recrawl_keeps_the_earlier_raw_and_links_the_search_copy(
    migrated_db, app_user_dsn, crawler_svc_dsn, monkeypatch
) -> None:
    """The S2-02 acceptance check: after a re-crawl with new content, the
    earlier artifact is still there, and KBData points at the latest one."""
    from services.platform_workers.workers.web_crawler import worker as wc
    from tests.integration.platform_workers.test_web_crawler_worker import (
        _fake_artifact,
        _seed_tenant_and_source,
        _session_factory,
    )

    tenant_id, _role, source_id = _seed_tenant_and_source(migrated_db["dsn"], "Raw Recrawl Source")
    store = _store(crawler_svc_dsn)
    monkeypatch.setattr(wc, "RAW", store)
    SessionLocal = _session_factory(app_user_dsn)
    with SessionLocal() as session:
        source = wc.get_active_sources(session)[0]

    for body in (b"<html>v1</html>", b"<html>v2</html>"):

        def _fetch(
            connector_type: str, config: dict[str, Any], body: bytes = body
        ) -> FetchedArtifact:
            fetched = _fake_artifact(config["url"])
            fetched.content_bytes = body
            return fetched

        with patch.object(wc, "connector_fetch", side_effect=_fetch):
            wc.run_source(SessionLocal, source, trigger="manual")

    shas = _rows(
        migrated_db["dsn"],
        "SELECT sha256 FROM ingest.artifact WHERE source_id = %s ORDER BY first_seen_at",
        (source_id,),
    )
    assert len(shas) == 2
    assert len(store.objects.objects) == 2  # type: ignore[attr-defined]
    assert _rows(
        migrated_db["dsn"],
        'SELECT artifact_sha256 FROM "KBData" WHERE tenant_id = %s',
        (tenant_id,),
    ) == [shas[-1]]
