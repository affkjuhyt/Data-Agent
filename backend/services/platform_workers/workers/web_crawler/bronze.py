"""Land a fetched artifact in the same Bronze tier a manual upload does.

platform_workers is a sync SQLAlchemy/Kafka loop (see schema_crawler). It
still avoids importing kb_backend's application code from here; workers only
import `libs.*` and their own package.

So this module writes the identical row shape
`kb_backend/pipeline/ingestion/bronze.py`'s `upload_to_bronze()` /
`register_web_bronze()` produce, using the shared SQLAlchemy ORM plus a sync
MinIO client.
"""
from __future__ import annotations

import hashlib
import io
import logging
import os
import uuid
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from minio import Minio
from neo4j import GraphDatabase
from pymongo import MongoClient
from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels
from sqlalchemy import delete, exists, select
from sqlalchemy.orm import Session

from libs.basemodel.services_databaseconnector.postgres_orm.knowledge_base_orm import (
    KBConflictBatchORM,
    KBConflictORM,
    KBDataORM,
    KBLifecycleHistoryORM,
    KBTextBlockORM,
    KBTextBlockVersionORM,
    KBTextTableORM,
)
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


def _as_uuid(value: Any) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _purge_promoted_traces(session: Session, data_id: str) -> None:
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
    data_uuid = _as_uuid(data_id)
    block_ids = select(KBTextBlockORM.block_id).where(KBTextBlockORM.owner_id == data_uuid)
    version_ids = select(KBTextBlockVersionORM.version_id).where(KBTextBlockVersionORM.block_id.in_(block_ids))
    session.execute(delete(KBTextTableORM).where(KBTextTableORM.version_id.in_(version_ids)))
    session.execute(delete(KBTextBlockVersionORM).where(KBTextBlockVersionORM.block_id.in_(block_ids)))
    session.execute(delete(KBTextBlockORM).where(KBTextBlockORM.owner_id == data_uuid))

    batch_ids = session.scalars(
        select(KBConflictORM.batch_id)
        .where(KBConflictORM.data_id == data_uuid, KBConflictORM.batch_id.is_not(None))
        .distinct()
    ).all()
    session.execute(delete(KBConflictORM).where(KBConflictORM.data_id == data_uuid))
    if batch_ids:
        has_conflicts = exists(
            select(KBConflictORM.conflict_id).where(KBConflictORM.batch_id == KBConflictBatchORM.batch_id)
        )
        session.execute(
            delete(KBConflictBatchORM)
            .where(KBConflictBatchORM.batch_id.in_(batch_ids))
            .where(~has_conflicts)
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


def _replace_existing_by_name(session: Session, tenant_id: str, name: str) -> None:
    """A re-crawl of the same source reuses its title as the KBData name, so
    without this the second run of ANY source hard-crashes on the
    (tenant_id, name) unique index idx_kbdata_tenant_name. Mirrors kb_backend's
    replace path."""
    existing = session.scalar(
        select(KBDataORM).where(KBDataORM.tenant_id == tenant_id, KBDataORM.name == name)
    )
    if existing is None:
        return
    old_data_id = str(existing.data_id)
    old_path = existing.path
    if existing.current_tier != _TIER_BRONZE:
        _purge_promoted_traces(session, old_data_id)
    session.execute(delete(KBLifecycleHistoryORM).where(KBLifecycleHistoryORM.data_id == existing.data_id))
    session.execute(delete(KBDataORM).where(KBDataORM.data_id == existing.data_id))
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
    session: Session, *, data_id: str, tier: str, added_by: str, name: str, extension: str | None,
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
    documents that carry bytes (`extension is None` for a web source); the hash is
    written whenever there is one, web sources included."""
    row = session.get(KBDataORM, _as_uuid(data_id))
    if row is None:
        raise ValueError(f"KBData row {data_id} no longer exists")
    base_metadata = {
        key: value
        for key, value in (row.doc_metadata or {}).items()
        if key not in _PIPELINE_STAMP_KEYS
    }
    row.doc_metadata = {**base_metadata, **doc_metadata}
    row.purpose = purpose
    row.row_version = uuid.uuid4()
    row.updated_at = datetime.now(UTC)
    if extension is not None:
        row.extension = extension
        row.path = path
    if content_hash is not None:
        row.content_hash = content_hash
    session.add(
        KBLifecycleHistoryORM(
            history_id=uuid.uuid4(),
            data_id=_as_uuid(data_id),
            to_tier=tier,
            from_tier=tier,
            approved_by=added_by,
            notes=f"Content updated in place by scheduled source worker: {name}",
            pipeline_metadata={"stage": "bronze", "update": "in_place"},
        )
    )


def _insert_kbdata(
    session: Session, *, data_id: str, tenant_id: str, role_id: str, name: str, extension: str,
    source_type: str, added_by: str, abstract: str, doc_metadata: dict[str, Any], path: str | None,
    content_hash: str | None = None, purpose: str = "production",
) -> None:
    _replace_existing_by_name(session, tenant_id, name)
    data_uuid = _as_uuid(data_id)
    session.add(
        KBDataORM(
            data_id=data_uuid,
            tenant_id=_as_uuid(tenant_id),
            role_id=_as_uuid(role_id),
            name=name,
            extension=extension,
            language="english",
            source_type=source_type,
            added_by=added_by,
            abstract=abstract,
            doc_metadata=doc_metadata,
            current_tier=_TIER_BRONZE,
            ingestion_mode="document",
            purpose=purpose,
            path=path,
            content_hash=content_hash,
        )
    )
    session.add(
        KBLifecycleHistoryORM(
            history_id=uuid.uuid4(),
            data_id=data_uuid,
            to_tier=_TIER_BRONZE,
            from_tier=None,
            approved_by=added_by,
            notes=f"Ingested by scheduled source worker: {name}",
            pipeline_metadata={"stage": "bronze", "source_type": source_type},
        )
    )


def register_http_file_bronze(
    session: Session, *, tenant_id: str, role_id: str, title: str, added_by: str, artifact: FetchedArtifact,
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
    the promote_silver worker (a different process, with no notion of `KBSource`)
    can decide whether to auto-queue Gold after Silver succeeds — see
    `libs/ingestion/consumer.py`'s `maybe_publish_auto_gold`."""
    if len(artifact.content_bytes) > cfg.MAX_FILE_SIZE_BYTES:
        raise ValueError(f"artifact for {title!r} exceeds MAX_FILE_SIZE_BYTES")

    content_hash = hashlib.sha256(artifact.content_bytes).hexdigest()
    existing = session.scalar(
        select(KBDataORM).where(KBDataORM.tenant_id == _as_uuid(tenant_id), KBDataORM.name == title)
    )
    # A soft-deleted row still occupies the name but is not "still there" —
    # short-circuiting here would hand back a Trashed, invisible data_id and
    # report a successful run for nothing (same bug fixed on the manual
    # upload path in kb_backend's ingest_bronze). Fall through to the normal
    # replace-and-insert path below instead.
    if existing is not None and not existing.is_deleted and existing.content_hash == content_hash:
        log.info("register_http_file_bronze | %r unchanged (content_hash match) — skipping re-ingest", title)
        if outcome is not None:
            outcome.update(unchanged=True, existing_tier=existing.current_tier)
        return str(existing.data_id)

    in_place = existing is not None and not existing.is_deleted
    old_path = existing.path if in_place else None
    # New content for a live document keeps its data_id; a soft-deleted one
    # still occupies the name, so that case takes the delete-and-insert path.
    data_id = str(existing.data_id) if in_place else str(uuid.uuid4())
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
        if in_place:
            _update_existing_in_place(
                session, data_id=data_id, tier=existing.current_tier, added_by=added_by, name=title,
                extension=artifact.extension, path=object_key, content_hash=content_hash,
                purpose=purpose, doc_metadata=doc_metadata,
            )
        else:
            _insert_kbdata(
                session, data_id=data_id, tenant_id=tenant_id, role_id=role_id, name=title,
                extension=artifact.extension, source_type="doc", added_by=added_by, abstract="",
                doc_metadata=doc_metadata,
                path=object_key,
                content_hash=content_hash,
                purpose=purpose,
            )
        session.commit()
    except Exception:
        session.rollback()
        # Best-effort cleanup, same compensating pattern as upload_to_bronze:
        # MinIO write happened first, so a DB failure shouldn't leave an
        # orphaned object with no KBData row pointing at it.
        try:
            minio.remove_object(cfg.MINIO_BUCKET, object_key)
        except Exception:
            log.exception("register_http_file_bronze | failed to clean up orphaned object %s", object_key)
        raise
    if old_path and old_path != object_key:
        try:
            minio.remove_object(cfg.MINIO_BUCKET, old_path)
        except Exception:  # noqa: BLE001 — the new version is live; a leftover object is only wasted space
            log.warning("register_http_file_bronze | could not remove superseded object %s", old_path)
    return data_id


def register_web_bronze(
    session: Session, *, tenant_id: str, role_id: str, title: str, url: str, added_by: str,
    source_id: str | None = None, promotion_mode: str = "manual", purpose: str = "production",
    artifact: FetchedArtifact | None = None, outcome: dict[str, Any] | None = None,
) -> str:
    """Mirrors `register_web_bronze()` in kb_backend's bronze pipeline
    exactly (same doc_metadata shape, no MinIO object — a web source's
    content is fetched again at Silver-promotion time). `source_id`/
    `promotion_mode`: see `register_http_file_bronze`'s docstring.

    `artifact` is what the connector just fetched; its hash is compared with
    the one stored for this document so a page that has not changed does not
    go through Silver (OCR/LLM summaries) and Gold again on every cron tick —
    `outcome` then gets `unchanged=True` and the kept tier, exactly like
    `register_http_file_bronze`. Nothing is compared without an artifact, and a
    document with no stored hash yet (rows from before this check) is treated
    as changed once so it gets one."""
    doc_metadata = {
        "source_type": "web", "url": url, "web_name": title,
        "source_id": source_id, "promotion_mode": promotion_mode,
    }
    content_hash = hashlib.sha256(artifact.content_bytes).hexdigest() if artifact is not None else None
    existing = session.scalar(
        select(KBDataORM)
        .where(KBDataORM.tenant_id == _as_uuid(tenant_id), KBDataORM.name == title, KBDataORM.is_deleted.is_(False))
    )
    if existing is not None:
        data_id = str(existing.data_id)
        if content_hash is not None and existing.content_hash == content_hash:
            log.info("register_web_bronze | %r unchanged (content_hash match) — skipping re-ingest", title)
            if outcome is not None:
                outcome.update(unchanged=True, existing_tier=existing.current_tier)
            return data_id
        _update_existing_in_place(
            session, data_id=data_id, tier=existing.current_tier, added_by=added_by, name=title, extension=None,
            path=None, content_hash=content_hash, purpose=purpose, doc_metadata=doc_metadata,
        )
    else:
        data_id = str(uuid.uuid4())
        _insert_kbdata(
            session, data_id=data_id, tenant_id=tenant_id, role_id=role_id, name=title, extension="html",
            source_type="web", added_by=added_by, abstract="", doc_metadata=doc_metadata,
            path=None, content_hash=content_hash, purpose=purpose,
        )
    session.commit()
    return data_id
