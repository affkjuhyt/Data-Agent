"""Land a fetched artifact in the same Bronze tier a manual upload does.

platform_workers is a sync psycopg2/Kafka loop (see schema_crawler) — it has
never used kb_backend's async `PostgresClient`/`MinIOClient` (SQLAlchemy
async engine + aioboto3), and importing kb_backend's application code from
here would cross a service boundary nothing else in this repo crosses
(workers only import `libs.*` and their own package — schema_crawler reads
"KBWarehouse"/"KBWarehouseConfig" the exact same way, via raw SQL against the
shared schema, never by importing kb_backend's Python).

So this module writes the identical row shape
`kb_backend/pipeline/ingestion/bronze.py`'s `upload_to_bronze()` /
`register_web_bronze()` produce, just via raw SQL + a sync MinIO client
instead of the async ORM/MinIO wrappers.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import uuid
from typing import Any
from urllib.parse import urlparse

from minio import Minio

from libs.ingestion import config as cfg
from libs.ingestion.connectors.base import FetchedArtifact

log = logging.getLogger("platform-workers.web-crawler.bronze")

_TIER_BRONZE = "bronze"


def _minio_client() -> Minio:
    parsed = urlparse(cfg.MINIO_URL)
    host = parsed.hostname or "minio"
    return Minio(
        host + (f":{parsed.port}" if parsed.port else ""),
        access_key=parsed.username or "minioadmin",
        secret_key=parsed.password or "minioadmin",
        secure=parsed.scheme == "https",
    )


def _replace_existing_by_name(cur: Any, tenant_id: str, name: str) -> None:
    """A re-crawl of the same source reuses its title as the KBData name, so
    without this the second run of ANY source hard-crashes on
    "KBData_name_key" (found by actually running two crawls back to back —
    not caught by unit tests mocking the DB away). Mirrors kb_backend's
    `_replace_existing_document_if_any` (ingestion.py) — hard-delete, not
    soft-delete, to free the unique constraint — except this is Postgres-only:
    it does NOT clean up Qdrant/Neo4j/Mongo/MinIO for a document that had
    already been promoted past Bronze. A V1 gap (see the crawl-feature plan's
    "Explicitly deferred") — a re-crawled source that had reached Gold leaves
    orphaned vectors/graph nodes behind. Acceptable for now because sources
    are freshly registered and re-crawled on a cron measured in days, not a
    hot path; worth a real trash-flush integration if that stops being true.
    """
    cur.execute(
        'SELECT data_id, path FROM "KBData" WHERE tenant_id = %s AND name = %s',
        (tenant_id, name),
    )
    existing = cur.fetchone()
    if existing is None:
        return
    old_data_id, old_path = existing
    cur.execute('DELETE FROM "KBLifecycleHistory" WHERE data_id = %s', (old_data_id,))
    cur.execute('DELETE FROM "KBData" WHERE data_id = %s', (old_data_id,))
    if old_path:
        try:
            _minio_client().remove_object(cfg.MINIO_BUCKET, old_path)
        except Exception:  # noqa: BLE001 — best-effort cleanup must not block the replace
            log.warning("_replace_existing_by_name | could not remove old object %s", old_path)


def _insert_kbdata(
    cur: Any, *, data_id: str, tenant_id: str, role_id: str, name: str, extension: str,
    source_type: str, added_by: str, abstract: str, doc_metadata: dict[str, Any], path: str | None,
    content_hash: str | None = None,
) -> None:
    _replace_existing_by_name(cur, tenant_id, name)
    cur.execute(
        """INSERT INTO "KBData"
               (data_id, tenant_id, role_id, name, extension, language, source_type,
                added_by, abstract, metadata, current_tier, ingestion_mode, path, content_hash)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (
            data_id, tenant_id, role_id, name, extension, "english", source_type,
            added_by, abstract, json.dumps(doc_metadata), _TIER_BRONZE, "document", path, content_hash,
        ),
    )
    cur.execute(
        """INSERT INTO "KBLifecycleHistory"
               (history_id, data_id, to_tier, from_tier, approved_by, notes, pipeline_metadata)
           VALUES (%s, %s, %s, NULL, %s, %s, %s)""",
        (
            str(uuid.uuid4()), data_id, _TIER_BRONZE, added_by,
            f"Ingested by scheduled source worker: {name}",
            json.dumps({"stage": "bronze", "source_type": source_type}),
        ),
    )


def register_http_file_bronze(
    conn: Any, *, tenant_id: str, role_id: str, title: str, added_by: str, artifact: FetchedArtifact,
) -> str:
    """Mirrors `upload_to_bronze(..., source_type=SourceType.DOC, ...)` —
    the worker's automated equivalent of a manual Document Hub upload.

    Short-circuits before touching MinIO/Postgres at all when the existing
    row for this name already has the same content_hash — the payoff being
    an unmodified rss/rest_api/sdmx/http_file response on every cron tick no
    longer creates MinIO churn or a KBLifecycleHistory entry."""
    if len(artifact.content_bytes) > cfg.MAX_FILE_SIZE_BYTES:
        raise ValueError(f"artifact for {title!r} exceeds MAX_FILE_SIZE_BYTES")

    content_hash = hashlib.sha256(artifact.content_bytes).hexdigest()
    with conn.cursor() as cur:
        cur.execute('SELECT data_id, content_hash FROM "KBData" WHERE tenant_id = %s AND name = %s', (tenant_id, title))
        existing = cur.fetchone()
    if existing is not None and existing[1] == content_hash:
        log.info("register_http_file_bronze | %r unchanged (content_hash match) — skipping re-ingest", title)
        return str(existing[0])

    data_id = str(uuid.uuid4())
    filename = f"{title}.{artifact.extension}"
    object_key = f"{tenant_id}/bronze/{data_id}/{filename}"

    minio = _minio_client()
    if not minio.bucket_exists(cfg.MINIO_BUCKET):
        minio.make_bucket(cfg.MINIO_BUCKET)
    minio.put_object(
        cfg.MINIO_BUCKET, object_key, io.BytesIO(artifact.content_bytes),
        length=len(artifact.content_bytes), content_type=artifact.content_type,
    )

    try:
        with conn.cursor() as cur:
            _insert_kbdata(
                cur, data_id=data_id, tenant_id=tenant_id, role_id=role_id, name=title,
                extension=artifact.extension, source_type="doc", added_by=added_by, abstract="",
                doc_metadata={
                    "source_type": "doc", "doc_type": artifact.extension,
                    "file_size": len(artifact.content_bytes),
                },
                path=object_key,
                content_hash=content_hash,
            )
        conn.commit()
    except Exception:
        conn.rollback()
        # Best-effort cleanup, same compensating pattern as upload_to_bronze:
        # MinIO write happened first, so a DB failure shouldn't leave an
        # orphaned object with no KBData row pointing at it.
        try:
            minio.remove_object(cfg.MINIO_BUCKET, object_key)
        except Exception:
            log.exception("register_http_file_bronze | failed to clean up orphaned object %s", object_key)
        raise
    return data_id


def register_web_bronze(
    conn: Any, *, tenant_id: str, role_id: str, title: str, url: str, added_by: str,
) -> str:
    """Mirrors `register_web_bronze()` in kb_backend's bronze pipeline
    exactly (same doc_metadata shape, no MinIO object — a web source's
    content is fetched again at Silver-promotion time)."""
    data_id = str(uuid.uuid4())
    with conn.cursor() as cur:
        _insert_kbdata(
            cur, data_id=data_id, tenant_id=tenant_id, role_id=role_id, name=title, extension="html",
            source_type="web", added_by=added_by, abstract="",
            doc_metadata={"source_type": "web", "url": url, "web_name": title},
            path=None,
        )
    conn.commit()
    return data_id
