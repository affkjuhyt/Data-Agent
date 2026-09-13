"""Silver-stage data loading, persistence, and rollback helpers."""
from __future__ import annotations

import uuid
from typing import Any, cast

from sqlalchemy import update as sa_update

from libs.shared.row_version import with_new_version
from libs.basemodel.services_databaseconnector.postgres_model import (
    KBLifecycleHistoryInsert,
    ReadJoinRequest,
    SourceType,
    Tier,
    WhereFilter,
)
from libs.basemodel.services_databaseconnector.postgres_orm.knowledge_base_orm import KBDataORM
from libs.database_connector.mongo_connector import MongoClient
from libs.database_connector.postgres_connector import PostgresClient
from libs.ingestion.stage_chain import StageChain


async def _load_kb_data(
    postgres: PostgresClient,
    tenant_id: str,
    data_id: str,
) -> dict[str, Any]:
    data_response = await postgres.read(ReadJoinRequest(
        tenant_id=tenant_id,
        joins_table=["KBData"],
        filters=[
            WhereFilter(
                table_name="KBData",
                column_name="data_id",
                value=uuid.UUID(data_id),
            )
        ],
        limit=1,
    ))
    if data_response.code != 200 or not data_response.data:
        raise ValueError(f"KBData not found: data_id={data_id}")
    return cast(dict[str, Any], data_response.data[0])


async def _rollback_silver_staging(mongo: MongoClient, data_id: str) -> None:
    try:
        await mongo.get_client()["kb_silver_staging"].delete_one({"data_id": data_id})
    except Exception:
        pass


async def _rollback_silver_tier(postgres: PostgresClient, data_id: str) -> None:
    async with postgres.get_client() as session:
        try:
            await session.execute(
                sa_update(KBDataORM)
                .where(KBDataORM.data_id == uuid.UUID(data_id))
                .values(**with_new_version(current_tier=Tier.BRONZE.value))
            )
            await session.commit()
        except Exception:
            await session.rollback()


async def _persist_silver(
    postgres: PostgresClient,
    mongo: MongoClient,
    *,
    data_id: str,
    tenant_id: str,
    source_type: SourceType,
    accepted_chunks: list[dict[str, Any]],
    raw_chunk_count: int,
    approved_by: str,
    stage_record: StageChain,
) -> None:
    database = mongo.get_client()
    rejected_count = raw_chunk_count - len(accepted_chunks)
    tables_found = sum(1 for chunk in accepted_chunks if chunk.get("table_involved"))

    try:
        await database["kb_silver_staging"].replace_one(
            {"data_id": data_id},
            {
                "data_id": data_id,
                "tenant_id": tenant_id,
                "source_type": source_type.value,
                "chunks": accepted_chunks,
            },
            upsert=True,
        )
    except Exception as exc:
        raise RuntimeError(f"silver staging write failed: {exc}") from exc
    stage_record.push(
        f"silver_staging_written_{data_id}",
        rollback_func=_rollback_silver_staging,
        rollback_args=(mongo, data_id),
    )

    async with postgres.get_client() as session:
        try:
            await session.execute(
                sa_update(KBDataORM)
                .where(KBDataORM.data_id == uuid.UUID(data_id))
                .values(**with_new_version(current_tier=Tier.SILVER.value))
            )
            await session.commit()
        except Exception as exc:
            await session.rollback()
            raise RuntimeError(f"silver tier update failed: {exc}") from exc
    stage_record.push(
        f"silver_tier_updated_{data_id}",
        rollback_func=_rollback_silver_tier,
        rollback_args=(postgres, data_id),
    )

    try:
        await postgres.insert(KBLifecycleHistoryInsert(
            data_id=data_id,
            to_tier=Tier.SILVER,
            from_tier=Tier.BRONZE,
            approved_by=approved_by,
            notes=(
                f"Promoted to silver: {len(accepted_chunks)} chunks accepted, "
                f"{rejected_count} rejected"
            ),
            pipeline_metadata={
                "stage": "silver",
                "source_type": source_type.value,
                "chunks_accepted": len(accepted_chunks),
                "chunks_rejected": rejected_count,
                "chunks_total": raw_chunk_count,
                "tables_found": tables_found,
            },
        ))
    except Exception:
        pass
