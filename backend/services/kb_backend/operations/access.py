from typing import Any
import uuid

from fastapi import HTTPException
from sqlalchemy import select as sa_select

from libs.basemodel.services_databaseconnector.postgres_model import WhereFilter, FilterOperator
from libs.basemodel.services_databaseconnector.postgres_orm.knowledge_base_orm import KBDataORM
from services.kb_backend.config import ROLE_PERMISSION_IDS

ROLE_PRIORITY = ("PLATFORM_ADMIN", "AI_ENGINEER", "EXECUTIVE", "BUSINESS_OPERATOR")


def visible_role_ids(role_id: str) -> list[str]:
    ranked_role_ids = [ROLE_PERMISSION_IDS[role_name] for role_name in ROLE_PRIORITY]
    if role_id not in ranked_role_ids:
        return [role_id]
    return ranked_role_ids[ranked_role_ids.index(role_id):]


def _visible_role_uuids(role_id: str) -> list[uuid.UUID]:
    return [uuid.UUID(visible_role_id) for visible_role_id in visible_role_ids(role_id)]


def document_role_filter(role_id: str | None) -> list[WhereFilter]:
    if not role_id:
        return []
    return [WhereFilter(
        table_name="KBData", column_name="role_id",
        value=visible_role_ids(role_id), operator=FilterOperator.IN,
    )]


async def kbdata_row_exists(
        postgres: Any,
        document_id: str,
        tenant_id: str,
        role_id: str | None,
        exclude_deleted: bool = False,
) -> bool:
    conditions = [
        KBDataORM.data_id == uuid.UUID(document_id),
        KBDataORM.tenant_id == uuid.UUID(tenant_id),
    ]
    if role_id:
        conditions.append(KBDataORM.role_id.in_(_visible_role_uuids(role_id)))
    if exclude_deleted:
        conditions.append(KBDataORM.is_deleted.is_(False))
    async with postgres.get_client() as session:
        row = await session.scalar(sa_select(KBDataORM.data_id).where(*conditions).limit(1))
    return row is not None


async def assert_document_access(postgres: Any, document_id: str, tenant_id: str, role_id: str | None) -> None:
    if not await kbdata_row_exists(postgres, document_id, tenant_id, role_id):
        raise HTTPException(status_code=404, detail="Document not found")


async def document_tier(postgres: Any, document_id: str, tenant_id: str, role_id: str | None) -> str:
    conditions = [
        KBDataORM.data_id == uuid.UUID(document_id),
        KBDataORM.tenant_id == uuid.UUID(tenant_id),
    ]
    if role_id:
        conditions.append(KBDataORM.role_id.in_(_visible_role_uuids(role_id)))
    async with postgres.get_client() as session:
        tier = await session.scalar(sa_select(KBDataORM.current_tier).where(*conditions).limit(1))
    if tier is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return str(getattr(tier, "value", tier)).lower()


async def assert_child_scoped(
        postgres: Any,
        id_column: Any,
        id_value: str,
        scope_column: Any,
        scope_value: str,
    not_found_detail: str = "Not found",
) -> None:
    async with postgres.get_client() as session:
        row = await session.scalar(
            sa_select(id_column)
            .where(id_column == uuid.UUID(id_value), scope_column == uuid.UUID(scope_value))
            .limit(1)
        )
    if row is None:
        raise HTTPException(status_code=404, detail=not_found_detail)
