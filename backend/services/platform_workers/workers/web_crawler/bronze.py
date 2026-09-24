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
import os
import uuid
from typing import Any
from urllib.parse import urlparse

from minio import Minio
from neo4j import GraphDatabase
from pymongo import MongoClient
from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from libs.ingestion import config as cfg
from libs.ingestion.connectors.base import FetchedArtifact

log = logging.getLogger("platform-workers.web-crawler.bronze")

_TIER_BRONZE = "bronze"
_NEO4J_URL = os.getenv("NEO4J_URL", "bolt://neo4j:7687")
_MONGO_URL = os.getenv("MONGO_URL", "mongodb://mongo:27017/dataagent")


def _minio_client() -> Minio:
    parsed = urlparse(cfg.MINIO_URL)
    host = parsed.hostname or "minio"
    return Minio(
        host + (f":{parsed.port}" if parsed.port else ""),
        access_key=parsed.username or "minioadmin",
        secret_key=parsed.password or "minioadmin",
        secure=parsed.scheme == "https",
    )


def _purge_promoted_traces(cur: Any, data_id: str) -> None:
    """A document already promoted past Bronze has traces in Postgres child
    tables plus Qdrant/Neo4j/Mongo; deleting only its `KBData` row leaves them
    all orphaned, and once it had reached Gold the `KBTextBlock` foreign key
    makes the `KBData` delete itself fail outright (hit for real this
    session: a source re-crawled after manual promotion to Gold). Deletion
    order mirrors kb_backend's `trash_ops.py` (`_delete_document` /
    `_purge_vectors_and_graph`) and this session's own hand-run cleanup SQL —
    Postgres children first (children-of-children before parents, so no FK
    violation), then the other three stores. Each store is independent and
    best-effort past the point where Postgres itself is consistent: a
    surviving Qdrant point or Neo4j edge for a data_id whose KBData row is
    already gone is inert (never returned by a tenant-scoped, joined query),
    just wasted space, same tradeoff `trash_ops.py`'s blob purge already makes."""
    cur.execute(
        """DELETE FROM "KBTextTable" WHERE version_id IN (
               SELECT v.version_id FROM "KBTextBlockVersion" v
               JOIN "KBTextBlock" b ON b.block_id = v.block_id
               WHERE b.owner_id = %s)""",
        (data_id,),
    )
    cur.execute(
        """DELETE FROM "KBTextBlockVersion" WHERE block_id IN (
               SELECT block_id FROM "KBTextBlock" WHERE owner_id = %s)""",
        (data_id,),
    )
    cur.execute('DELETE FROM "KBTextBlock" WHERE owner_id = %s', (data_id,))
    cur.execute(
        """SELECT DISTINCT batch_id FROM "KBConflict" WHERE data_id = %s AND batch_id IS NOT NULL""",
        (data_id,),
    )
    batch_ids = [row[0] for row in cur.fetchall()]
    cur.execute('DELETE FROM "KBConflict" WHERE data_id = %s', (data_id,))
    if batch_ids:
        # psycopg2 adapts a plain Python list to a `text[]` literal, which
        # Postgres refuses to compare against the `uuid` column ("operator
        # does not exist: uuid = text") — hit for real on every re-crawl of
        # a document with recorded conflicts (RSS Kinh tế, FRED, ... failed
        # every single scheduled run until this cast was added).
        cur.execute(
            """DELETE FROM "KBConflictBatch" WHERE batch_id = ANY(%s::uuid[])
                   AND NOT EXISTS (SELECT 1 FROM "KBConflict" c WHERE c.batch_id = "KBConflictBatch".batch_id)""",
            ([str(b) for b in batch_ids],),
        )

    try:
        qdrant = QdrantClient(url=cfg.QDRANT_URL)
        point_filter = qmodels.Filter(must=[qmodels.FieldCondition(key="data_id", match=qmodels.MatchValue(value=data_id))])
        qdrant.delete(collection_name=cfg.QDRANT_COLLECTION, points_selector=qmodels.FilterSelector(filter=point_filter))
        qdrant.delete(collection_name=cfg.QDRANT_DOC_COLLECTION, points_selector=qmodels.FilterSelector(filter=point_filter))
    except Exception:  # noqa: BLE001 — best-effort, see docstring
        log.warning("_purge_promoted_traces | qdrant cleanup failed for data_id=%s", data_id, exc_info=True)

    try:
        with GraphDatabase.driver(_NEO4J_URL) as driver:
            driver.execute_query("MATCH ()-[r:RELATES {data_id: $did}]-() DELETE r", did=data_id)
    except Exception:  # noqa: BLE001 — best-effort, see docstring
        log.warning("_purge_promoted_traces | neo4j cleanup failed for data_id=%s", data_id, exc_info=True)

    try:
        with MongoClient(_MONGO_URL) as mongo:
            mongo.get_default_database().get_collection("kb_silver_staging").delete_many({"data_id": data_id})
    except Exception:  # noqa: BLE001 — best-effort, see docstring
        log.warning("_purge_promoted_traces | mongo staging cleanup failed for data_id=%s", data_id, exc_info=True)


def _replace_existing_by_name(cur: Any, tenant_id: str, name: str) -> None:
    """A re-crawl of the same source reuses its title as the KBData name, so
    without this the second run of ANY source hard-crashes on
    "KBData_name_key" (found by actually running two crawls back to back —
    not caught by unit tests mocking the DB away). Mirrors kb_backend's
    `_replace_existing_document_if_any` (ingestion.py) — hard-delete, not
    soft-delete, to free the unique constraint. Unlike a fresh Bronze-only
    document, one already promoted past Bronze needs `_purge_promoted_traces`
    first (see its docstring) — without it, a document that had reached Gold
    makes this delete fail on the `KBTextBlock` foreign key, and one at
    Silver silently leaves orphaned chunks/vectors behind.
    """
    cur.execute(
        'SELECT data_id, path, current_tier FROM "KBData" WHERE tenant_id = %s AND name = %s',
        (tenant_id, name),
    )
    existing = cur.fetchone()
    if existing is None:
        return
    old_data_id, old_path, old_tier = existing
    if old_tier != _TIER_BRONZE:
        _purge_promoted_traces(cur, old_data_id)
    cur.execute('DELETE FROM "KBLifecycleHistory" WHERE data_id = %s', (old_data_id,))
    cur.execute('DELETE FROM "KBData" WHERE data_id = %s', (old_data_id,))
    if old_path:
        try:
            _minio_client().remove_object(cfg.MINIO_BUCKET, old_path)
        except Exception:  # noqa: BLE001 — best-effort cleanup must not block the replace
            log.warning("_replace_existing_by_name | could not remove old object %s", old_path)


# Keys the pipeline stamps on KBData.metadata for the run that just finished.
# A new version starts clean, or the old "failed"/"succeeded" would describe
# content that has not been processed yet.
_PIPELINE_STAMP_KEYS = ("last_pipeline_stage", "last_pipeline_status", "last_pipeline_error")


def _update_existing_in_place(
    cur: Any, *, data_id: str, tier: str, added_by: str, name: str, extension: str | None,
    path: str | None, content_hash: str | None, purpose: str, doc_metadata: dict[str, Any],
) -> None:
    """New content for an existing document: keep its row and `data_id`.

    Replacing by delete + insert gave every re-crawl a new `data_id`, which
    stranded anything still holding the old one (a browser's Pipeline Activity
    entry sat at 85% forever) and removed the old content before the new one
    was usable, so a failed Silver/Gold left nothing behind. Here the row
    points at the new object and the tier is left alone: whatever Silver/Gold
    already produced keeps serving until Gold's `_cleanup_previous_promotion`
    swaps it for the new version. Extension/path/hash are only rewritten for
    documents that carry bytes (`extension is None` for a web source)."""
    stripped = "metadata"
    for key in _PIPELINE_STAMP_KEYS:
        stripped = f"({stripped} - '{key}')"
    sets = [f"metadata = coalesce({stripped}, '{{}}'::jsonb) || %s::jsonb", "purpose = %s",
            "row_version = gen_random_uuid()", "updated_at = now()"]
    params: list[Any] = [json.dumps(doc_metadata), purpose]
    if extension is not None:
        sets += ["extension = %s", "path = %s", "content_hash = %s"]
        params += [extension, path, content_hash]
    params.append(data_id)
    cur.execute(f'UPDATE "KBData" SET {", ".join(sets)} WHERE data_id = %s', tuple(params))
    cur.execute(
        """INSERT INTO "KBLifecycleHistory"
               (history_id, data_id, to_tier, from_tier, approved_by, notes, pipeline_metadata)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (
            str(uuid.uuid4()), data_id, tier, tier, added_by,
            f"Content updated in place by scheduled source worker: {name}",
            json.dumps({"stage": "bronze", "update": "in_place"}),
        ),
    )


def _insert_kbdata(
    cur: Any, *, data_id: str, tenant_id: str, role_id: str, name: str, extension: str,
    source_type: str, added_by: str, abstract: str, doc_metadata: dict[str, Any], path: str | None,
    content_hash: str | None = None, purpose: str = "production",
) -> None:
    _replace_existing_by_name(cur, tenant_id, name)
    cur.execute(
        """INSERT INTO "KBData"
               (data_id, tenant_id, role_id, name, extension, language, source_type,
                added_by, abstract, metadata, current_tier, ingestion_mode, purpose, path, content_hash)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (
            data_id, tenant_id, role_id, name, extension, "english", source_type,
            added_by, abstract, json.dumps(doc_metadata), _TIER_BRONZE, "document", purpose, path, content_hash,
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
    outcome: dict[str, Any] | None = None, source_id: str | None = None, promotion_mode: str = "manual",
    purpose: str = "production",
) -> str:
    """Mirrors `upload_to_bronze(..., source_type=SourceType.DOC, ...)` —
    the worker's automated equivalent of a manual Document Hub upload.

    Short-circuits before touching MinIO/Postgres at all when the existing
    row for this name already has the same content_hash — the payoff being
    an unmodified rss/rest_api/sdmx/http_file response on every cron tick no
    longer creates MinIO churn or a KBLifecycleHistory entry. When that
    happens `outcome` (if given) gets `unchanged=True` and the tier the kept
    document is at, so the run can say why nothing new reached Bronze.

    `source_id`/`promotion_mode` are stamped into `doc_metadata` so
    `kb_promote_silver` (a different service, with no notion of `KBSource`)
    can decide whether to auto-queue Gold after Silver succeeds — see
    `libs/ingestion/consumer.py`'s `maybe_publish_auto_gold`."""
    if len(artifact.content_bytes) > cfg.MAX_FILE_SIZE_BYTES:
        raise ValueError(f"artifact for {title!r} exceeds MAX_FILE_SIZE_BYTES")

    content_hash = hashlib.sha256(artifact.content_bytes).hexdigest()
    with conn.cursor() as cur:
        cur.execute(
            'SELECT data_id, content_hash, current_tier, is_deleted, path FROM "KBData" WHERE tenant_id = %s AND name = %s',
            (tenant_id, title),
        )
        existing = cur.fetchone()
    # A soft-deleted row still occupies the name but is not "still there" —
    # short-circuiting here would hand back a Trashed, invisible data_id and
    # report a successful run for nothing (same bug fixed on the manual
    # upload path in kb_backend's ingest_bronze). Fall through to the normal
    # replace-and-insert path below instead.
    if existing is not None and not existing[3] and existing[1] == content_hash:
        log.info("register_http_file_bronze | %r unchanged (content_hash match) — skipping re-ingest", title)
        if outcome is not None:
            outcome.update(unchanged=True, existing_tier=existing[2])
        return str(existing[0])

    in_place = existing is not None and not existing[3]
    # New content for a live document keeps its data_id; a soft-deleted one
    # still occupies the name, so that case takes the delete-and-insert path.
    data_id = str(existing[0]) if in_place else str(uuid.uuid4())
    filename = f"{title}.{artifact.extension}"
    # The content hash in the key keeps the old object addressable until the row
    # points at the new one; it is only removed after that commit.
    object_key = (
        f"{tenant_id}/bronze/{data_id}/{content_hash[:12]}/{filename}" if in_place
        else f"{tenant_id}/bronze/{data_id}/{filename}"
    )

    minio = _minio_client()
    if not minio.bucket_exists(cfg.MINIO_BUCKET):
        minio.make_bucket(cfg.MINIO_BUCKET)
    minio.put_object(
        cfg.MINIO_BUCKET, object_key, io.BytesIO(artifact.content_bytes),
        length=len(artifact.content_bytes), content_type=artifact.content_type,
    )

    doc_metadata = {
        "source_type": "doc", "doc_type": artifact.extension,
        "file_size": len(artifact.content_bytes),
        "source_id": source_id, "promotion_mode": promotion_mode,
    }
    try:
        with conn.cursor() as cur:
            if in_place:
                _update_existing_in_place(
                    cur, data_id=data_id, tier=existing[2], added_by=added_by, name=title,
                    extension=artifact.extension, path=object_key, content_hash=content_hash,
                    purpose=purpose, doc_metadata=doc_metadata,
                )
            else:
                _insert_kbdata(
                    cur, data_id=data_id, tenant_id=tenant_id, role_id=role_id, name=title,
                    extension=artifact.extension, source_type="doc", added_by=added_by, abstract="",
                    doc_metadata=doc_metadata,
                    path=object_key,
                    content_hash=content_hash,
                    purpose=purpose,
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
    old_path = existing[4] if in_place else None
    if old_path and old_path != object_key:
        try:
            minio.remove_object(cfg.MINIO_BUCKET, old_path)
        except Exception:  # noqa: BLE001 — the new version is live; a leftover object is only wasted space
            log.warning("register_http_file_bronze | could not remove superseded object %s", old_path)
    return data_id


def register_web_bronze(
    conn: Any, *, tenant_id: str, role_id: str, title: str, url: str, added_by: str,
    source_id: str | None = None, promotion_mode: str = "manual", purpose: str = "production",
) -> str:
    """Mirrors `register_web_bronze()` in kb_backend's bronze pipeline
    exactly (same doc_metadata shape, no MinIO object — a web source's
    content is fetched again at Silver-promotion time). `source_id`/
    `promotion_mode`: see `register_http_file_bronze`'s docstring."""
    doc_metadata = {
        "source_type": "web", "url": url, "web_name": title,
        "source_id": source_id, "promotion_mode": promotion_mode,
    }
    with conn.cursor() as cur:
        cur.execute(
            'SELECT data_id, current_tier FROM "KBData" WHERE tenant_id = %s AND name = %s AND NOT is_deleted',
            (tenant_id, title),
        )
        existing = cur.fetchone()
        if existing is not None:
            # Nothing is stored for a web source (Silver fetches the page), so
            # "new content" is just the next Silver run over the same document.
            data_id = str(existing[0])
            _update_existing_in_place(
                cur, data_id=data_id, tier=existing[1], added_by=added_by, name=title, extension=None,
                path=None, content_hash=None, purpose=purpose, doc_metadata=doc_metadata,
            )
        else:
            data_id = str(uuid.uuid4())
            _insert_kbdata(
                cur, data_id=data_id, tenant_id=tenant_id, role_id=role_id, name=title, extension="html",
                source_type="web", added_by=added_by, abstract="", doc_metadata=doc_metadata,
                path=None, purpose=purpose,
            )
    conn.commit()
    return data_id
