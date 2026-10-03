"""The immutable raw layer (S2-02, ADR-V2-21, docs/v2/14-input-data-db.md §4).

Every successful fetch keeps its bytes in MinIO bucket `raw` at
{source_id}/{sha256} and records ingest.artifact (once per distinct content) and
ingest.fetch (every time). The object is written only if absent and never
deleted, so a re-crawl that replaces a document's KB search copy (bronze.py)
leaves every earlier version reachable. The bucket is created with object lock
(minio-init), which makes "never deleted" hold even for a buggy caller.

Writes go through crawler_svc (ADR-V2-46): the web crawler's own role for the
ingest schema, which may INSERT and SELECT there and nothing else.
"""

from __future__ import annotations

import hashlib
import io
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from minio.error import S3Error
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from libs.ingestion.connectors.base import FetchedArtifact
from services.platform_workers.common import db as worker_db

RAW_BUCKET = "raw"


class ObjectStore(Protocol):
    def stat_object(self, bucket_name: str, object_name: str) -> Any: ...

    def put_object(
        self, bucket_name: str, object_name: str, data: Any, length: int, content_type: str = ...
    ) -> Any: ...


_INSERT_ARTIFACT = text(
    """
    INSERT INTO ingest.artifact (artifact_id, tenant_id, source_id, sha256, object_key, bytes,
                                 content_type, filename, fetched_from_url, first_run_id)
    VALUES (:artifact_id, :tenant_id, :source_id, :sha256, :object_key, :bytes,
            :content_type, :filename, :url, :run_id)
    ON CONFLICT (source_id, sha256) DO NOTHING
    RETURNING artifact_id
    """
)
_FIND_ARTIFACT = text(
    "SELECT artifact_id FROM ingest.artifact WHERE source_id = :source_id AND sha256 = :sha256"
)
_INSERT_FETCH = text(
    """
    INSERT INTO ingest.fetch (fetch_id, tenant_id, source_id, run_id, artifact_id, fetched_at, url)
    VALUES (:fetch_id, :tenant_id, :source_id, :run_id, :artifact_id, :fetched_at, :url)
    """
)


@dataclass
class RawStore:
    objects: ObjectStore
    ingest_sessions: sessionmaker[Session]

    def keep(self, artifact: FetchedArtifact, *, tenant_id: str, source_id: str, run_id: str) -> str:
        """Store the bytes and record the fetch; return their SHA-256."""
        sha256 = hashlib.sha256(artifact.content_bytes).hexdigest()
        key = f"{source_id}/{sha256}"
        if not self._exists(key):
            self.objects.put_object(
                RAW_BUCKET, key, io.BytesIO(artifact.content_bytes),
                length=len(artifact.content_bytes), content_type=artifact.content_type,
            )
        params = {"tenant_id": tenant_id, "source_id": source_id, "sha256": sha256, "run_id": run_id}
        with worker_db.tenant_session(self.ingest_sessions, tenant_id) as session:
            artifact_id = session.scalar(_INSERT_ARTIFACT, {
                **params,
                "artifact_id": uuid.uuid4(),
                "object_key": f"{RAW_BUCKET}/{key}",
                "bytes": len(artifact.content_bytes),
                "content_type": artifact.content_type,
                "filename": f"{sha256}.{artifact.extension}" if artifact.extension else None,
                "url": artifact.source_url,
            }) or session.scalar(_FIND_ARTIFACT, params)
            session.execute(_INSERT_FETCH, {
                **params,
                "fetch_id": uuid.uuid4(),
                "artifact_id": artifact_id,
                "fetched_at": datetime.now(UTC),
                "url": artifact.source_url,
            })
            session.commit()
        return sha256

    def _exists(self, key: str) -> bool:
        try:
            self.objects.stat_object(RAW_BUCKET, key)
        except S3Error as exc:
            if exc.code in ("NoSuchKey", "NoSuchObject"):
                return False
            raise
        return True
