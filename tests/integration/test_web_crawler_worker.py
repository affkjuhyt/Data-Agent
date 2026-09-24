"""Integration test for the Web Crawler Worker (platform_workers
WORKER=web-crawler) — real Postgres via testcontainers, real migration
chain. The network fetch itself is monkeypatched (no real HTTP/Playwright
call — deterministic, no dependency on a live URL's uptime); everything
downstream of that (Bronze landing, KBSourceRun bookkeeping, re-crawl
replace) is real.

The re-crawl scenario here is not incidental: running the same source twice
back-to-back is exactly what caught a real bug during manual testing — the
worker's Bronze insert crashed on "KBData_name_key" (a name reused by a
second crawl of the same source) because nothing deleted the prior row
first. `libs.ingestion.connectors` unit tests mock the DB away entirely, so
they could not have caught this; only a real Postgres insert could.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import uuid
from collections.abc import Iterator
from unittest.mock import patch

import psycopg2
import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "backend" / "alembic" / "alembic.ini"


def _async_dsn(libpq_dsn: str) -> str:
    return "postgresql+asyncpg://" + libpq_dsn[len("postgresql://"):]


def _run_alembic(dsn: str, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "DATABASE_URL": _async_dsn(dsn), "PYTHONPATH": str(REPO_ROOT)}
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ALEMBIC_INI), *args],
        capture_output=True, text=True, env=env, cwd=str(REPO_ROOT), timeout=60,
    )


@pytest.fixture
def fresh_db(postgres_container) -> Iterator[dict[str, str]]:
    conn = psycopg2.connect(postgres_container["dsn"])
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            # kb_structured is a second schema the migrations create (opt-in
            # structured-table ingestion) with default privileges owned by
            # app_user — dropping only `public` leaves those behind, and a
            # session-scoped container shared by more than one test in this
            # file then fails "role app_user cannot be dropped" on the second
            # migration run.
            cur.execute("DROP SCHEMA IF EXISTS kb_structured CASCADE")
            cur.execute("DROP SCHEMA IF EXISTS public CASCADE")
            cur.execute("CREATE SCHEMA public")
            cur.execute("GRANT ALL ON SCHEMA public TO public")
            cur.execute("DROP ROLE IF EXISTS app_user")
    finally:
        conn.close()
    yield postgres_container


@pytest.fixture
def migrated_db(fresh_db) -> dict[str, str]:
    result = _run_alembic(fresh_db["dsn"], "upgrade", "head")
    assert result.returncode == 0, f"alembic upgrade failed:\n{result.stdout}\n{result.stderr}"
    return fresh_db


def _seed_tenant_and_source(
    dsn: str, title: str, connector_type: str = "web_crawl", extra_config: dict | None = None,
) -> tuple[str, str, str]:
    tenant_id, role_row_id, source_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO "Tenants" (id, name, slug, data_residency) VALUES (%s, %s, %s, %s)""",
                (tenant_id, "Crawler Test Tenant", f"crawler-test-{tenant_id[:8]}", "Asia-SE1"),
            )
            cur.execute(
                """INSERT INTO "RolePermissions" (id, role_id, resource, action)
                   VALUES (%s, 'PLATFORM_ADMIN', '_role_anchor', 'label')""",
                (role_row_id,),
            )
            cur.execute(
                """INSERT INTO "KBSource" (source_id, tenant_id, role_id, connector_type, title, created_by)
                   VALUES (%s, %s, %s, %s, %s, 'tester')""",
                (source_id, tenant_id, role_row_id, connector_type, title),
            )
            import json
            config = {"url": "https://example.invalid/page", "cron": "0 3 * * *", "render": False}
            config.update(extra_config or {})
            cur.execute(
                """INSERT INTO "KBSourceConfig" (source_id, version_number, is_active, config, created_by)
                   VALUES (%s, 1, true, %s::jsonb, 'tester')""",
                (source_id, json.dumps(config)),
            )
    finally:
        conn.close()
    return tenant_id, role_row_id, source_id


@pytest.fixture
def worker_module():
    """`tests/integration/conftest.py`'s session-autouse `_auth_api_sys_path`
    puts `backend/services` AND `backend/services/auth_api` on `sys.path` so
    auth_api's own flat-WORKDIR-style imports resolve — but `auth_api` has
    its OWN nested `services/` package
    (`backend/services/auth_api/services/__init__.py`), and with
    `.../auth_api` on the path, a fresh `import services` binds to THAT
    nested package instead of the real top-level one at `backend/services`,
    so `services.platform_workers` doesn't exist under it. Temporarily
    removing both polluting entries and clearing any stale `services*`
    sys.modules cache sidesteps this without touching the other fixture
    (other integration tests may depend on its current behavior)."""
    polluting = [str(REPO_ROOT / "backend" / "services"), str(REPO_ROOT / "backend" / "services" / "auth_api")]
    removed = [p for p in polluting if p in sys.path]
    for p in removed:
        sys.path.remove(p)
    stale = [name for name in sys.modules if name == "services" or name.startswith("services.")]
    saved = {name: sys.modules.pop(name) for name in stale}
    try:
        from services.platform_workers.workers.web_crawler import worker as wc
        yield wc
    finally:
        for p in removed:
            if p not in sys.path:
                sys.path.insert(0, p)
        # Restore exactly what was cached before (a later auth_api test
        # re-importing `services` should still get ITS nested package, not
        # the top-level one we just bound for platform_workers).
        for name in [n for n in sys.modules if n == "services" or n.startswith("services.")]:
            del sys.modules[name]
        sys.modules.update(saved)


def _fake_artifact(url: str):
    from libs.ingestion.connectors.base import FetchedArtifact
    return FetchedArtifact(
        content_bytes=b"<html><body>fake content</body></html>",
        content_type="text/html", source_url=url, extension="html",
        extra={"raw_len": 100, "content_len": 20, "tag_stripped_pct": 0.8},
    )


def test_run_source_lands_a_bronze_document(migrated_db, worker_module):
    tenant_id, _role_id, source_id = _seed_tenant_and_source(migrated_db["dsn"], "Regression Test Source")
    conn = psycopg2.connect(migrated_db["dsn"])
    try:
        with patch(
            "services.platform_workers.workers.web_crawler.worker.connector_fetch",
            side_effect=lambda connector_type, config: _fake_artifact(config["url"]),
        ):
            with conn.cursor() as cur:
                sources = worker_module.get_active_sources(cur)
            assert len(sources) == 1
            worker_module.run_source(conn, sources[0], trigger="scheduled")

        with conn.cursor() as cur:
            cur.execute('SELECT status, data_ids, quality_metrics FROM "KBSourceRun" WHERE source_id = %s', (source_id,))
            run = cur.fetchone()
            assert run is not None
            assert run[0] == "success"
            assert len(run[1]) == 1

            cur.execute('SELECT name, source_type, tenant_id FROM "KBData" WHERE tenant_id = %s', (tenant_id,))
            doc = cur.fetchone()
            assert doc[0] == "Regression Test Source"
            assert doc[1] == "web"
            assert str(doc[2]) == tenant_id
    finally:
        conn.close()


def test_recrawling_the_same_source_replaces_instead_of_crashing(migrated_db, worker_module):
    """The actual bug: running the same source twice used to crash on
    "KBData_name_key" because the prior document with the same name was
    never removed before the new insert."""
    tenant_id, _role_id, source_id = _seed_tenant_and_source(migrated_db["dsn"], "Recrawled Source")
    conn = psycopg2.connect(migrated_db["dsn"])
    try:
        with patch(
            "services.platform_workers.workers.web_crawler.worker.connector_fetch",
            side_effect=lambda connector_type, config: _fake_artifact(config["url"]),
        ):
            with conn.cursor() as cur:
                source = worker_module.get_active_sources(cur)[0]

            worker_module.run_source(conn, source, trigger="scheduled")
            worker_module.run_source(conn, source, trigger="manual")  # must NOT raise

        with conn.cursor() as cur:
            cur.execute('SELECT status FROM "KBSourceRun" WHERE source_id = %s ORDER BY started_at', (source_id,))
            statuses = [r[0] for r in cur.fetchall()]
            assert statuses == ["success", "success"], statuses

            cur.execute('SELECT COUNT(*) FROM "KBData" WHERE tenant_id = %s', (tenant_id,))
            assert cur.fetchone()[0] == 1, "re-crawl must replace, not duplicate, the document"
    finally:
        conn.close()


def test_a_failed_run_is_retried_before_its_next_cron_tick(migrated_db, worker_module):
    """V2: crawl_due_sources must pick a failed source back up once
    next_retry_at has passed, even though its cron isn't due yet — the whole
    point of retry/backoff being separate from the normal schedule."""
    tenant_id, _role_id, source_id = _seed_tenant_and_source(
        migrated_db["dsn"], "Retry Test Source", extra_config={"cron": "0 3 1 1 *"},  # once a year — never "due" here
    )
    conn = psycopg2.connect(migrated_db["dsn"])
    try:
        with patch(
            "services.platform_workers.workers.web_crawler.worker.connector_fetch",
            side_effect=RuntimeError("simulated fetch failure"),
        ):
            worker_module.crawl_due_sources(conn)  # first attempt: cron-due (no prior run), fails

        with conn.cursor() as cur:
            cur.execute(
                'SELECT status, retry_count, next_retry_at FROM "KBSourceRun" WHERE source_id = %s', (source_id,),
            )
            status, retry_count, next_retry_at = cur.fetchone()
            assert (status, retry_count) == ("failed", 0)
            assert next_retry_at is not None

            # Not due for retry yet — crawl_due_sources must not dispatch again.
            with patch(
                "services.platform_workers.workers.web_crawler.worker.connector_fetch",
                side_effect=AssertionError("must not be called before next_retry_at"),
            ):
                worker_module.crawl_due_sources(conn)
            cur.execute('SELECT COUNT(*) FROM "KBSourceRun" WHERE source_id = %s', (source_id,))
            assert cur.fetchone()[0] == 1

            # Fast-forward the backoff clock, then it must retry (as retry_count=1)
            # and this time succeed.
            cur.execute('UPDATE "KBSourceRun" SET next_retry_at = now() - interval \'1 second\' WHERE source_id = %s', (source_id,))
        conn.commit()

        with patch(
            "services.platform_workers.workers.web_crawler.worker.connector_fetch",
            side_effect=lambda connector_type, config: _fake_artifact(config["url"]),
        ):
            worker_module.crawl_due_sources(conn)

        with conn.cursor() as cur:
            cur.execute(
                'SELECT status, retry_count FROM "KBSourceRun" WHERE source_id = %s ORDER BY started_at', (source_id,),
            )
            rows = cur.fetchall()
            assert [r[0] for r in rows] == ["failed", "success"]
            assert rows[1][1] == 1, "the retry attempt must carry retry_count=1"
    finally:
        conn.close()


def test_recursive_source_enqueues_and_drains_discovered_links(migrated_db, worker_module):
    """V2: a `recursive` web_crawl source's own run enqueues its page's
    discovered links, and drain_crawl_queue works through them as their own
    KBSourceRun('recursive') attempts."""
    tenant_id, _role_id, source_id = _seed_tenant_and_source(
        migrated_db["dsn"], "Recursive Test Source",
        extra_config={"recursive": True, "max_depth": 1, "same_domain_only": True},
    )
    conn = psycopg2.connect(migrated_db["dsn"])

    def _fake_with_links(connector_type, config):
        from libs.ingestion.connectors.base import FetchedArtifact
        url = config["url"]
        links = [] if "page-2" in url else ["https://example.invalid/page-2"]
        return FetchedArtifact(
            content_bytes=b"<html><body>fake</body></html>", content_type="text/html",
            source_url=url, extension="html", extra={"discovered_links": links},
        )

    try:
        with patch(
            "services.platform_workers.workers.web_crawler.worker.connector_fetch", side_effect=_fake_with_links,
        ):
            with conn.cursor() as cur:
                source = worker_module.get_active_sources(cur)[0]
            worker_module.run_source(conn, source, trigger="scheduled")

            with conn.cursor() as cur:
                cur.execute('SELECT url, depth, status FROM "KBSourceCrawlQueue" WHERE source_id = %s', (source_id,))
                queued = cur.fetchall()
            assert queued == [("https://example.invalid/page-2", 1, "pending")]

            worker_module.drain_crawl_queue(conn)

        with conn.cursor() as cur:
            cur.execute('SELECT status FROM "KBSourceCrawlQueue" WHERE source_id = %s', (source_id,))
            assert cur.fetchone()[0] == "done"

            cur.execute(
                'SELECT trigger, status FROM "KBSourceRun" WHERE source_id = %s ORDER BY started_at', (source_id,),
            )
            runs = cur.fetchall()
            assert runs == [("scheduled", "success"), ("recursive", "success")]

            # depth already at max_depth (1) — the drained page's own (empty)
            # discovered_links plus the depth cap means no further rows queued.
            cur.execute('SELECT COUNT(*) FROM "KBSourceCrawlQueue" WHERE source_id = %s', (source_id,))
            assert cur.fetchone()[0] == 1
    finally:
        conn.close()


class _NoopMinio:
    def __init__(self) -> None:
        self.removed: list[str] = []

    def bucket_exists(self, _bucket: str) -> bool:
        return True

    def put_object(self, *_a, **_kw) -> None:
        pass

    def remove_object(self, _bucket: str, key: str) -> None:
        self.removed.append(key)


def test_changed_content_updates_a_promoted_document_in_place(migrated_db, worker_module):
    """Real Postgres: the UPDATE builds `metadata` with jsonb `-` and `||`
    (a class of statement mocked cursors cannot vouch for). A re-crawl with new
    bytes must keep the row's data_id and tier, strip the previous run's
    pipeline stamps, keep unrelated metadata, and log the change."""
    import json
    from libs.ingestion.connectors.base import FetchedArtifact

    tenant_id, _role, source_id = _seed_tenant_and_source(
        migrated_db["dsn"], "Rates", connector_type="http_file", extra_config={"url": "https://example.invalid/rates.xml"},
    )
    versions = iter([b"<rates>1</rates>", b"<rates>2</rates>"])

    def fetch(_connector_type, config):
        return FetchedArtifact(
            content_bytes=next(versions), content_type="text/xml", source_url=config["url"], extension="xml",
        )

    minio = _NoopMinio()
    conn = psycopg2.connect(migrated_db["dsn"])
    try:
        with patch("services.platform_workers.workers.web_crawler.worker.connector_fetch", side_effect=fetch), \
             patch("services.platform_workers.workers.web_crawler.bronze._minio_client", return_value=minio):
            with conn.cursor() as cur:
                source = worker_module.get_active_sources(cur)[0]
            worker_module.run_source(conn, source, trigger="scheduled")

            with conn.cursor() as cur:
                cur.execute('SELECT data_id, path, row_version FROM "KBData" WHERE tenant_id = %s', (tenant_id,))
                data_id, first_path, first_version = cur.fetchone()
                cur.execute(
                    """UPDATE "KBData" SET current_tier = 'gold',
                              metadata = metadata || %s::jsonb WHERE data_id = %s""",
                    (json.dumps({"last_pipeline_status": "succeeded", "last_pipeline_stage": "gold", "intents": ["x"]}), data_id),
                )
            conn.commit()

            worker_module.run_source(conn, source, trigger="manual")

        with conn.cursor() as cur:
            cur.execute(
                'SELECT data_id, current_tier, path, content_hash, jsonb_typeof(metadata), metadata, row_version '
                'FROM "KBData" WHERE tenant_id = %s', (tenant_id,),
            )
            rows = cur.fetchall()
            assert len(rows) == 1
            new_id, tier, path, _hash, kind, metadata, version = rows[0]
            assert new_id == data_id, "the document keeps its data_id across a re-crawl"
            assert tier == "gold", "what Silver/Gold produced keeps serving until re-promotion"
            assert path != first_path and path.startswith(f"{tenant_id}/bronze/{data_id}/")
            assert kind == "object"
            assert metadata["source_id"] == source_id and metadata["intents"] == ["x"]
            assert "last_pipeline_status" not in metadata and "last_pipeline_stage" not in metadata
            assert version != first_version

            cur.execute('SELECT notes FROM "KBLifecycleHistory" WHERE data_id = %s ORDER BY 1', (data_id,))
            notes = [r[0] for r in cur.fetchall()]
            assert any("updated in place" in n for n in notes), notes
        assert first_path in minio.removed, "the superseded object is removed after the swap"
    finally:
        conn.close()
