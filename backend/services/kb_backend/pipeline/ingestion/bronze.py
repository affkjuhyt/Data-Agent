from __future__ import annotations

from typing import Any
import hashlib
import logging
import uuid

from pydantic import BaseModel

from libs.basemodel.services_databaseconnector.postgres_model import (
    KBDataInsert,
    KBLifecycleHistoryInsert,
    Language,
    SourceType,
    Tier,
)
from libs.database_connector.minio_connector import MinIOClient
from libs.database_connector.postgres_connector import PostgresClient
from services.kb_backend.pipeline.ingestion import config as cfg


class BronzeUploadRequest(BaseModel):
    tenant_id:    str
    role_id:      str
    name:         str
    filename:     str
    extension:    str
    language:     str
    source_type:  str
    added_by:     str
    abstract:     str
    doc_metadata: dict[str, Any]
    file_bytes:   bytes
    content_type: str = "application/octet-stream"
    knowledge_base_id: str | None = None
    ingestion_mode: str = "document"
    purpose: str = "production"


log = logging.getLogger(__name__)

_SOURCE_CONTENT_TYPE: dict[SourceType, str] = {
    SourceType.DOC: "application/octet-stream",
    SourceType.WEB: "text/html",
    SourceType.IMAGE: "image/jpeg",
    SourceType.VIDEO: "video/mp4",
    SourceType.WAREHOUSE: "application/octet-stream",
}


async def upload_to_bronze(
    minio: MinIOClient,
    postgres: PostgresClient,
    file_data: bytes,
    name: str,
    filename: str,
    extension: str,
    source_type: SourceType,
    tenant_id: str,
    role_id: str,
    added_by: str,
    abstract: str,
    language: Language,
    doc_metadata: Any,
    knowledge_base_id: str | None = None,
    ingestion_mode: str = "document",
    purpose: str = "production",
) -> dict[str, Any]:
    if len(file_data) > cfg.MAX_FILE_SIZE_BYTES:
        raise ValueError(
            f"File size {len(file_data)} bytes exceeds the "
            f"{cfg.MAX_FILE_SIZE_BYTES // (1024 * 1024)} MB limit"
        )

    data_id = str(uuid.uuid4())
    object_key = f"{tenant_id}/bronze/{data_id}/{filename}"
    content_type = _SOURCE_CONTENT_TYPE.get(source_type, "application/octet-stream")

    await minio.ensure_bucket(cfg.MINIO_BUCKET)
    await minio.upload(cfg.MINIO_BUCKET, object_key, file_data, content_type)
    log.info("Bronze MinIO upload complete data_id=%s key=%s bytes=%d", data_id, object_key, len(file_data))

    kb_insert = KBDataInsert(
        tenant_id=tenant_id,
        role_id=role_id,
        knowledge_base_id=knowledge_base_id or None,
        name=name,
        extension=extension,
        language=language,
        source_type=source_type,
        added_by=added_by,
        abstract=abstract,
        doc_metadata=doc_metadata,
        current_tier=Tier.BRONZE,
        ingestion_mode=ingestion_mode,
        purpose=purpose,
        path=object_key,
        # Same dedup column the scheduled-source worker uses (web_crawler/bronze.py)
        # — router/knowledge/ingestion.py's ingest_bronze checks it against the
        # existing row BEFORE calling this function, so re-uploading byte-identical
        # content never reaches here at all; still stored on every insert so a
        # future re-upload has something to compare against.
        content_hash=hashlib.sha256(file_data).hexdigest(),
    )
    # Document and its first lifecycle row share one transaction: a document
    # with no history was previously possible whenever the second insert failed.
    async with postgres.get_client(tenant_id=tenant_id) as session:
        data_res = await postgres.insert(kb_insert, session=session)
        if data_res.code != 200:
            await minio.delete(cfg.MINIO_BUCKET, object_key)
            raise RuntimeError(f"Postgres KBData insert failed: {data_res.error}")

        assert data_res.data is not None
        actual_data_id: str = data_res.data["data_id"]

        hist_res = await postgres.insert(KBLifecycleHistoryInsert(
            data_id=actual_data_id,
            to_tier=Tier.BRONZE,
            from_tier=None,
            approved_by=added_by,
            notes=f"Ingested as bronze: {filename} ({source_type.value})",
            pipeline_metadata={
                "stage":       "bronze",
                "source_type": source_type.value,
                "extension":   extension,
                "file_size":   len(file_data),
                "filename":    filename,
            },
        ), session=session)
        if hist_res.code != 200:
            await minio.delete(cfg.MINIO_BUCKET, object_key)
            raise RuntimeError(f"Postgres lifecycle history insert failed: {hist_res.error}")
        await session.commit()

    log.info("Bronze registered data_id=%s tenant=%s", actual_data_id, tenant_id)
    return {
        "data_id": actual_data_id,
        "path": object_key,
        "name": name,
        "layer": Tier.BRONZE.value,
    }
