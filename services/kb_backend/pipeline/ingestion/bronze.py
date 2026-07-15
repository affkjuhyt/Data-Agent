"""
Bronze stage: validate → upload to MinIO → register in Postgres as BRONZE KBData.
"""
from __future__ import annotations

import logging
import uuid

from pydantic import BaseModel

from libs.basemodel.services_databaseconnector.postgres_model import (
    KBDataInsert,
    KBLifecycleHistoryInsert,
    Language,
    SourceType,
    Tier,
    Web,
)
from libs.database_connector.minio_connector import MinIOClient
from libs.database_connector.postgres_connector import PostgresClient
from services.kb_backend.pipeline.ingestion import config as cfg


class BronzeUploadRequest(BaseModel):
    tenant_id:    str
    role_id:      str
    name:         str           # document display name (user-supplied Knowledge Name)
    filename:     str           # original upload filename — used only for object storage
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


class WebBronzeRequest(BaseModel):
    tenant_id:    str
    role_id:      str
    name:         str           # document display name (Knowledge Name)
    url:          str           # the web portal to crawl at bronze→silver
    web_name:     str = ""
    language:     str = "english"
    added_by:     str
    abstract:     str = ""
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
    name: str,
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

    ``name`` is the document display name (user-supplied Knowledge Name);
    ``filename`` is the original upload filename, used only for the object key.
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
        name=name,
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
        "name": name,
        "layer": Tier.BRONZE.value,
    }


async def register_web_bronze(
    postgres: PostgresClient,
    name: str,
    url: str,
    web_name: str,
    tenant_id: str,
    role_id: str,
    added_by: str,
    abstract: str,
    language: Language,
    knowledge_base_id: str | None = None,
) -> dict:
    """Register a web portal as BRONZE-tier KBData.

    Unlike file sources there is no MinIO object — the source IS the live URL,
    stored in doc_metadata. The bronze→silver step fetches it, strips boilerplate
    to markdown, and Docling-chunks it (see silver._parse_web).
    Returns: {data_id, name, url, layer}
    """
    metadata_obj = Web(source_type=SourceType.WEB, url=url, web_name=web_name or name)

    kb_insert = KBDataInsert(
        tenant_id=tenant_id,
        role_id=role_id,
        knowledge_base_id=knowledge_base_id or None,
        name=name,
        extension="html",
        language=language,
        source_type=SourceType.WEB,
        added_by=added_by,
        abstract=abstract,
        doc_metadata=metadata_obj,
        current_tier=Tier.BRONZE,
        path=None,  # no object store key for a live web source
    )
    data_res = await postgres.insert(kb_insert)
    if data_res.code != 200:
        raise RuntimeError(f"Postgres KBData insert failed: {data_res.error}")

    actual_data_id: str = data_res.data["data_id"]

    hist_res = await postgres.insert(KBLifecycleHistoryInsert(
        data_id=actual_data_id,
        to_tier=Tier.BRONZE,
        from_tier=None,
        approved_by=added_by,
        notes=f"Ingested web portal as bronze: {url}",
        pipeline_metadata={"stage": "bronze", "source_type": SourceType.WEB.value, "url": url},
    ))
    if hist_res.code != 200:
        log.warning("Lifecycle history insert failed data_id=%s: %s", actual_data_id, hist_res.error)

    log.info("Bronze registered web data_id=%s url=%s tenant=%s", actual_data_id, url, tenant_id)
    return {
        "data_id": actual_data_id,
        "name": name,
        "url": url,
        "layer": Tier.BRONZE.value,
    }
