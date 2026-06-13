"""
Bronze stage: validate → upload to MinIO → register in Postgres as BRONZE KBData.
"""
from __future__ import annotations

import logging
import uuid

from pydantic import BaseModel

from basemodel.services_databaseconnector.postgres_model import (
    KBDataInsert,
    KBLifecycleHistoryInsert,
    Language,
    SourceType,
    Tier,
)
from services.database_connector.minio_connector import MinIOClient
from services.database_connector.postgres_connector import PostgresClient
from services.backend.pipeline.ingestion import config as cfg


class BronzeUploadRequest(BaseModel):
    tenant_id:    str
    role_id:      str
    name:         str
    extension:    str
    language:     str
    source_type:  str
    added_by:     str
    abstract:     str
    doc_metadata: dict
    file_bytes:   bytes
    content_type: str = "application/octet-stream"
    # Knowledge base (kind of knowledge) this document belongs to. None = unscoped.
    knowledge_base_id: str | None = None

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
    filename: str,
    extension: str,
    source_type: SourceType,
    tenant_id: str,
    role_id: str,
    added_by: str,
    abstract: str,
    language: Language,
    doc_metadata,  # MetadataType instance (Document | Image | Video | Web | Warehouse)
    knowledge_base_id: str | None = None,
) -> dict:
    """
    Upload raw file to MinIO and register as BRONZE-tier KBData.
    Returns: {data_id, path, name, layer}
    """
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
        name=filename,
        extension=extension,
        language=language,
        source_type=source_type,
        added_by=added_by,
        abstract=abstract,
        doc_metadata=doc_metadata,
        current_tier=Tier.BRONZE,
        path=object_key,
    )
    data_res = await postgres.insert(kb_insert)
    if data_res.code != 200:
        await minio.delete(cfg.MINIO_BUCKET, object_key)
        raise RuntimeError(f"Postgres KBData insert failed: {data_res.error}")

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
    ))
    if hist_res.code != 200:
        log.warning("Lifecycle history insert failed data_id=%s: %s", actual_data_id, hist_res.error)

    log.info("Bronze registered data_id=%s tenant=%s", actual_data_id, tenant_id)
    return {
        "data_id": actual_data_id,
        "path": object_key,
        "name": filename,
        "layer": Tier.BRONZE.value,
    }
