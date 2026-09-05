from __future__ import annotations

from typing import Any
import logging

from libs.basemodel.services_databaseconnector.postgres_model import Language, SourceType
from libs.database_connector.minio_connector import MinIOClient
from libs.database_connector.postgres_connector import PostgresClient

from services.kb_backend.pipeline.ingestion.bronze import (
    BronzeUploadRequest, WebBronzeRequest, upload_to_bronze, register_web_bronze,
)

log = logging.getLogger(__name__)


class IngestionPipeline:
    async def bronze(
        self,
        req: BronzeUploadRequest,
        minio: MinIOClient,
        postgres: PostgresClient,
    ) -> dict[str, Any]:
        return await upload_to_bronze(
            minio=minio,
            postgres=postgres,
            file_data=req.file_bytes,
            name=req.name,
            filename=req.filename,
            extension=req.extension,
            source_type=SourceType(req.source_type),
            tenant_id=req.tenant_id,
            role_id=req.role_id,
            added_by=req.added_by,
            abstract=req.abstract,
            language=Language(req.language),
            doc_metadata=req.doc_metadata,
            knowledge_base_id=req.knowledge_base_id,
            ingestion_mode=req.ingestion_mode,
        )

    async def bronze_web(
        self,
        req: WebBronzeRequest,
        postgres: PostgresClient,
    ) -> dict[str, Any]:
        return await register_web_bronze(
            postgres=postgres,
            name=req.name,
            url=req.url,
            web_name=req.web_name,
            tenant_id=req.tenant_id,
            role_id=req.role_id,
            added_by=req.added_by,
            abstract=req.abstract,
            language=Language(req.language),
            knowledge_base_id=req.knowledge_base_id,
        )
