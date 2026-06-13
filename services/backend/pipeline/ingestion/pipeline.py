"""
IngestionPipeline — stateless orchestrator for bronze ingestion only.
Silver and gold promotion run in the kb-ingestion-worker service.
"""
from __future__ import annotations

import logging

from basemodel.services_databaseconnector.postgres_model import Language, SourceType
from services.database_connector.minio_connector import MinIOClient
from services.database_connector.postgres_connector import PostgresClient

from services.backend.pipeline.ingestion.bronze import BronzeUploadRequest, upload_to_bronze

log = logging.getLogger(__name__)


class IngestionPipeline:
    """Stateless. All services injected at call-time."""

    async def bronze(
        self,
        req: BronzeUploadRequest,
        minio: MinIOClient,
        postgres: PostgresClient,
    ) -> dict:
        return await upload_to_bronze(
            minio=minio,
            postgres=postgres,
            file_data=req.file_bytes,
            filename=req.name,
            extension=req.extension,
            source_type=SourceType(req.source_type),
            tenant_id=req.tenant_id,
            role_id=req.role_id,
            added_by=req.added_by,
            abstract=req.abstract,
            language=Language(req.language),
            doc_metadata=req.doc_metadata,
            knowledge_base_id=req.knowledge_base_id,
        )
