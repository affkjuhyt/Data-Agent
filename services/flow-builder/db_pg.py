import os
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Optional
from uuid import UUID

from sqlalchemy import select, update as sa_update, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from libs.database_connector.postgres_connector import client
from libs.basemodel.services_databaseconnector.postgres_orm.workflow_orm import (
    AgentsORM, WorkflowsORM, WorkflowVersionsORM, AgentVersionsORM, AgentKBORM,
)

logger = logging.getLogger(__name__)

_DEV_TENANT_ID          = os.environ.get("DEV_TENANT_ID",          "00000000-0000-0000-0000-000000000001")
_DEV_RESPONDER_MODEL_ID = os.environ.get("DEV_RESPONDER_MODEL_ID", "00000000-0000-0000-0000-000000000011")


@asynccontextmanager
async def _txn(session: AsyncSession) -> AsyncGenerator[None, None]:
    """client.get_client(tenant_id=...) already autobegins a transaction (the
    set_config SELECT executes on the session), so `session.begin()` here would
    raise "A transaction is already begun on this Session". Commit/rollback the
    already-open transaction instead."""
    try:
        yield
        await session.commit()
    except Exception:
        await session.rollback()
        raise


async def init_db() -> None:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        logger.warning("DATABASE_URL not set")
        return
    client.set_url(url)
    await client.open()
    logger.info("db pool ready")


async def close_db() -> None:
    await client.close()


async def create_agent(name: str, description: str, tenant_id: str) -> dict[str, Any]:
    async with client.get_client(tenant_id=tenant_id) as session:
        async with _txn(session):
            agent = AgentsORM(
                tenant_id=UUID(tenant_id),
                name=name,
                description=description,
            )
            session.add(agent)
            await session.flush()

            workflow = WorkflowsORM(
                agent_id=agent.id,
                name="Default Workflow",
                description="",
            )
            session.add(workflow)
            await session.flush()

            wv = WorkflowVersionsORM(
                workflow_id=workflow.id,
                version=1,
                status="draft",
            )
            session.add(wv)
            await session.flush()

            av = AgentVersionsORM(
                agent_id=agent.id,
                version=1,
                status="draft",
                workflow_version_id=wv.id,
                responder_model_id=UUID(_DEV_RESPONDER_MODEL_ID),
            )
            session.add(av)
            await session.flush()

            agent.draft_version_id = av.id

            return {
                "agent_id": str(agent.id),
                "workflow_id": str(workflow.id),
                "workflow_version_id": str(wv.id),
                "agent_version_id": str(av.id),
            }


async def list_agents(tenant_id: str) -> list[dict[str, Any]]:
    async with client.get_client(tenant_id=tenant_id) as session:
        result = await session.execute(
            select(AgentsORM)
            .where(AgentsORM.tenant_id == UUID(tenant_id))
            .where(AgentsORM.deleted_at.is_(None))
            .order_by(AgentsORM.created_at.desc())
        )
        rows = result.scalars().all()
        return [
            {
                "id": str(r.id),
                "name": r.name,
                "description": r.description,
                "published_version_id": str(r.published_version_id) if r.published_version_id else None,
                "draft_version_id": str(r.draft_version_id) if r.draft_version_id else None,
                "created_at": r.created_at,
            }
            for r in rows
        ]


async def get_agent(agent_id: str, tenant_id: str | None = None) -> Optional[dict[str, Any]]:
    async with client.get_client(tenant_id=tenant_id) as session:
        result = await session.execute(
            select(AgentsORM)
            .options(
                selectinload(AgentsORM.published_version).selectinload(AgentVersionsORM.workflow_version)
                .selectinload(WorkflowVersionsORM.workflow),
                selectinload(AgentsORM.draft_version),
            )
            .where(AgentsORM.id == UUID(agent_id))
            .where(AgentsORM.deleted_at.is_(None))
        )
        agent = result.scalar_one_or_none()
        if not agent:
            return None

        active_workflow = None
        active_workflow_name = None
        if agent.published_version and agent.published_version.workflow_version:
            wf = agent.published_version.workflow_version.workflow
            if wf:
                active_workflow = str(wf.id)
                active_workflow_name = wf.name

        dv = agent.draft_version
        return {
            "id": str(agent.id),
            "name": agent.name,
            "description": agent.description,
            "published_version_id": str(agent.published_version_id) if agent.published_version_id else None,
            "draft_version_id": str(agent.draft_version_id) if agent.draft_version_id else None,
            "tenant_id": str(agent.tenant_id),
            "created_at": agent.created_at,
            "updated_at": agent.updated_at,
            "active_workflow_id": active_workflow,
            "active_workflow_name": active_workflow_name,
            # draft config fields — used by Settings tab
            "responder_model_id": str(dv.responder_model_id) if dv and dv.responder_model_id else None,
            "system_prompt_id":   str(dv.system_prompt_id)   if dv and dv.system_prompt_id   else None,
            "memory_enabled":     bool(dv.memory_enabled)     if dv else False,
        }


async def create_workflow(agent_id: str, name: str, description: str = "") -> dict[str, Any]:
    async with client.get_client() as session:
        async with _txn(session):
            wf = WorkflowsORM(
                agent_id=UUID(agent_id),
                name=name,
                description=description,
            )
            session.add(wf)
            await session.flush()

            wv = WorkflowVersionsORM(
                workflow_id=wf.id,
                version=1,
                status="draft",
            )
            session.add(wv)
            await session.flush()

            return {"workflow_id": str(wf.id), "workflow_version_id": str(wv.id)}


async def list_workflows(agent_id: str) -> list[dict[str, Any]]:
    async with client.get_client() as session:
        result = await session.execute(
            select(WorkflowsORM)
            .options(selectinload(WorkflowsORM.workflow_versions))
            .where(WorkflowsORM.agent_id == UUID(agent_id))
            .order_by(WorkflowsORM.created_at.desc())
        )
        rows = result.scalars().all()

        def _pick_version(versions: list[WorkflowVersionsORM], status: str) -> Optional[str]:
            matches = [v for v in versions if v.status == status]
            return str(max(matches, key=lambda v: v.version).id) if matches else None

        return [
            {
                "id": str(r.id),
                "name": r.name,
                "description": r.description,
                "is_active": r.is_active,
                "created_at": r.created_at,
                "draft_version_id": _pick_version(r.workflow_versions, "draft"),
                "published_version_id": _pick_version(r.workflow_versions, "published"),
            }
            for r in rows
        ]


async def update_agent_draft(agent_id: str, fields: dict[str, Any], tenant_id: str | None = None) -> dict[str, Any]:
    agent_fields = {k: v for k, v in fields.items() if k in ("name", "description")}
    av_fields = {k: v for k, v in fields.items()
                 if k in ("responder_model_id", "system_prompt_id", "guardrail_id", "memory_enabled")}

    async with client.get_client(tenant_id=tenant_id) as session:
        async with _txn(session):
            if agent_fields:
                await session.execute(
                    sa_update(AgentsORM)
                    .where(AgentsORM.id == UUID(agent_id))
                    .values(**agent_fields, updated_at=func.now())
                )
            if av_fields:
                # Cast UUID string fields to UUID
                uuid_keys = {"responder_model_id", "system_prompt_id", "guardrail_id"}
                av_values = {
                    k: (UUID(v) if v else None) if k in uuid_keys else v
                    for k, v in av_fields.items()
                }
                await session.execute(
                    sa_update(AgentVersionsORM)
                    .where(AgentVersionsORM.agent_id == UUID(agent_id))
                    .where(AgentVersionsORM.status == "draft")
                    .values(**av_values)
                )

    return {"agent_id": agent_id, "updated": list(fields.keys())}


async def unpublish_agent(agent_id: str, tenant_id: str | None = None) -> dict[str, Any]:
    async with client.get_client(tenant_id=tenant_id) as session:
        async with _txn(session):
            result = await session.execute(
                select(AgentsORM).where(AgentsORM.id == UUID(agent_id))
            )
            agent = result.scalar_one_or_none()
            if not agent:
                raise ValueError("Agent not found")
            if not agent.published_version_id:
                raise ValueError("Agent is not published")
            await session.execute(
                sa_update(AgentsORM)
                .where(AgentsORM.id == UUID(agent_id))
                .values(published_version_id=None, updated_at=func.now())
            )
    return {"agent_id": agent_id, "published": False}


async def publish_workflow_version(workflow_version_id: str, changelog: str | None = None) -> dict[str, Any]:
    async with client.get_client() as session:
        async with _txn(session):
            wv = await session.get(WorkflowVersionsORM, UUID(workflow_version_id))
            if not wv:
                raise ValueError("workflow_version not found")
            if wv.status == "published":
                raise ValueError("already published")
            if wv.status == "archived":
                raise ValueError("use republish for archived versions")

            await session.execute(
                sa_update(WorkflowVersionsORM)
                .where(WorkflowVersionsORM.workflow_id == wv.workflow_id)
                .where(WorkflowVersionsORM.status == "published")
                .values(status="archived")
            )
            wv.status = "published"
            wv.published_at = func.now()
            if changelog:
                wv.changelog = changelog

    return {"workflow_version_id": workflow_version_id, "status": "published"}


async def republish_workflow_version(workflow_version_id: str) -> dict[str, Any]:
    """Re-publish an archived version — archives current published atomically."""
    async with client.get_client() as session:
        async with _txn(session):
            wv = await session.get(WorkflowVersionsORM, UUID(workflow_version_id))
            if not wv:
                raise ValueError("workflow_version not found")
            if wv.status != "archived":
                raise ValueError("can only republish archived versions")

            await session.execute(
                sa_update(WorkflowVersionsORM)
                .where(WorkflowVersionsORM.workflow_id == wv.workflow_id)
                .where(WorkflowVersionsORM.status == "published")
                .values(status="archived")
            )
            wv.status = "published"
            wv.published_at = func.now()

    return {"workflow_version_id": workflow_version_id, "status": "published"}


async def list_workflow_versions(workflow_id: str) -> list[dict[str, Any]]:
    async with client.get_client() as session:
        result = await session.execute(
            select(WorkflowVersionsORM)
            .where(WorkflowVersionsORM.workflow_id == UUID(workflow_id))
            .order_by(WorkflowVersionsORM.version.desc())
        )
        rows = result.scalars().all()
        return [
            {
                "id": str(r.id),
                "workflow_id": str(r.workflow_id),
                "version": r.version,
                "status": r.status,
                "changelog": r.changelog,
                "created_at": r.created_at,
                "published_at": r.published_at,
            }
            for r in rows
        ]


async def create_draft_version(workflow_id: str) -> dict[str, Any]:
    async with client.get_client() as session:
        async with _txn(session):
            # Lấy max version hiện tại
            result = await session.execute(
                select(func.max(WorkflowVersionsORM.version))
                .where(WorkflowVersionsORM.workflow_id == UUID(workflow_id))
            )
            max_version = result.scalar() or 0

            # Không cho tạo draft mới nếu đã có draft
            draft_result = await session.execute(
                select(WorkflowVersionsORM)
                .where(WorkflowVersionsORM.workflow_id == UUID(workflow_id))
                .where(WorkflowVersionsORM.status == "draft")
                .limit(1)
            )
            if draft_result.scalar_one_or_none():
                raise ValueError("Already has a draft version")

            # Lấy published version hiện tại để frontend load vào React state
            published_result = await session.execute(
                select(WorkflowVersionsORM)
                .where(WorkflowVersionsORM.workflow_id == UUID(workflow_id))
                .where(WorkflowVersionsORM.status == "published")
                .limit(1)
            )
            published = published_result.scalar_one_or_none()

            wv = WorkflowVersionsORM(
                workflow_id=UUID(workflow_id),
                version=max_version + 1,
                status="draft",
            )
            session.add(wv)
            await session.flush()

        return {
            "workflow_version_id": str(wv.id),
            "version": wv.version,
            "source_version_id": str(published.id) if published else None,
        # Canvas KHÔNG được copy vào MongoDB ở đây.
        # Frontend nhận source_version_id, load canvas vào React state,
        # và chỉ POST lên MongoDB khi user ấn Save.
    }


async def delete_workflow_version(version_id: str, tenant_id: str | None = None) -> None:
    async with client.get_client(tenant_id=tenant_id) as session:
        async with _txn(session):
            wv = await session.get(WorkflowVersionsORM, UUID(version_id))
            if not wv:
                raise ValueError("workflow_version not found")
            if wv.status == "published":
                raise ValueError("Cannot delete a published version")
            await session.delete(wv)


async def delete_workflow(workflow_id: str, tenant_id: str | None = None) -> list[str]:
    async with client.get_client(tenant_id=tenant_id) as session:
        async with _txn(session):
            wf = await session.get(WorkflowsORM, UUID(workflow_id))
            if not wf:
                raise ValueError("workflow not found")
            versions_result = await session.execute(
                select(WorkflowVersionsORM)
                .where(WorkflowVersionsORM.workflow_id == UUID(workflow_id))
            )
            versions = versions_result.scalars().all()
            version_ids = [str(v.id) for v in versions]
            wv_ids = [v.id for v in versions]

            # Block xóa nếu workflow đang được published agent sử dụng
            if wv_ids:
                in_use = await session.execute(
                    select(AgentVersionsORM)
                    .where(AgentVersionsORM.workflow_version_id.in_(wv_ids))
                    .where(AgentVersionsORM.status == "published")
                    .limit(1)
                )
                if in_use.scalar_one_or_none():
                    raise ValueError("Workflow đang được agent sử dụng. Hãy republish agent với workflow khác trước.")

            # NULL out AgentVersions.workflow_version_id trước khi xóa WorkflowVersions
            if wv_ids:
                await session.execute(
                    sa_update(AgentVersionsORM)
                    .where(AgentVersionsORM.workflow_version_id.in_(wv_ids))
                    .values(workflow_version_id=None)
                )

            for v in versions:
                await session.delete(v)
            await session.delete(wf)
        return version_ids


async def delete_agent(agent_id: str, tenant_id: str | None = None) -> list[str]:
    """Hard delete agent + cascade workflows + versions. Trả về version_ids để xóa MongoDB canvas."""
    async with client.get_client(tenant_id=tenant_id) as session:
        async with _txn(session):
            agent = await session.get(AgentsORM, UUID(agent_id))
            if not agent:
                raise ValueError("agent not found")

            # Phá circular FK: Agents.published_version_id / draft_version_id → AgentVersions.id
            agent.published_version_id = None
            agent.draft_version_id = None
            await session.flush()

            # Xóa workflow_versions + workflows, thu thập version_ids cho MongoDB
            wf_result = await session.execute(
                select(WorkflowsORM).where(WorkflowsORM.agent_id == UUID(agent_id))
            )
            workflows = wf_result.scalars().all()

            version_ids: list[str] = []
            for wf in workflows:
                wv_result = await session.execute(
                    select(WorkflowVersionsORM)
                    .where(WorkflowVersionsORM.workflow_id == wf.id)
                )
                versions = wv_result.scalars().all()
                for v in versions:
                    version_ids.append(str(v.id))
                    await session.delete(v)
                await session.delete(wf)

            # Xóa AgentVersions tường minh (async không lazy-load cascade)
            av_result = await session.execute(
                select(AgentVersionsORM).where(AgentVersionsORM.agent_id == UUID(agent_id))
            )
            for av in av_result.scalars().all():
                await session.delete(av)

            await session.delete(agent)

    return version_ids


async def publish_agent(agent_id: str, workflow_id: str, kb_connection_id: str | None = None, tenant_id: str | None = None) -> dict[str, Any]:
    async with client.get_client(tenant_id=tenant_id) as session:
        async with _txn(session):
            # 1. Verify workflow belongs to agent
            wf = await session.get(WorkflowsORM, UUID(workflow_id))
            if not wf or str(wf.agent_id) != agent_id:
                raise ValueError("workflow does not belong to agent")

            # 2. Find published version (not draft)
            wv_result = await session.execute(
                select(WorkflowVersionsORM)
                .where(WorkflowVersionsORM.workflow_id == UUID(workflow_id))
                .where(WorkflowVersionsORM.status == "published")
                .order_by(WorkflowVersionsORM.version.desc())
                .limit(1)
            )
            wv = wv_result.scalar_one_or_none()
            if not wv:
                raise ValueError("No published workflow_version found — publish a workflow version first")

            # 3. Get settings from draft agent version
            av_result = await session.execute(
                select(AgentVersionsORM)
                .where(AgentVersionsORM.agent_id == UUID(agent_id))
                .where(AgentVersionsORM.status == "draft")
                .order_by(AgentVersionsORM.version.desc())
                .limit(1)
            )
            av_draft = av_result.scalar_one_or_none()

            # 4. Get next version number
            max_ver = (await session.execute(
                select(func.max(AgentVersionsORM.version))
                .where(AgentVersionsORM.agent_id == UUID(agent_id))
            )).scalar() or 0

            new_av = AgentVersionsORM(
                agent_id=UUID(agent_id),
                version=max_ver + 1,
                status="published",
                workflow_version_id=wv.id,
                responder_model_id=av_draft.responder_model_id if av_draft else UUID(_DEV_RESPONDER_MODEL_ID),
                system_prompt_id=av_draft.system_prompt_id if av_draft else None,
                guardrail_id=av_draft.guardrail_id if av_draft else None,
                memory_enabled=av_draft.memory_enabled if av_draft else False,
                llm_config=av_draft.llm_config if av_draft else {},
                retrieval_config=av_draft.retrieval_config if av_draft else {},
                published_at=func.now(),
            )
            session.add(new_av)
            await session.flush()

            # 5. Insert AgentKB if kb_connection_id provided
            if kb_connection_id:
                session.add(AgentKBORM(
                    version_id=new_av.id,
                    kb_connection_id=UUID(kb_connection_id),
                ))

            # 6. Update agent published pointer
            await session.execute(
                sa_update(AgentsORM)
                .where(AgentsORM.id == UUID(agent_id))
                .values(published_version_id=new_av.id, updated_at=func.now())
            )

            return {
                "agent_id": agent_id,
                "agent_version_id": str(new_av.id),
                "workflow_version_id": str(wv.id),
            }


async def workflow_version_belongs_to(workflow_version_id: str, tenant_id: str) -> bool:
    """Does this workflow version belong to `tenant_id`?

    The canvas lives in Mongo, keyed by workflow_version_id alone, and Mongo has
    no row-level security to fall back on the way Postgres does. So the
    ownership question has to be asked here, against the relational side that
    does know the answer: version -> workflow -> agent -> tenant.

    Without this, authenticating the canvas routes would only have narrowed
    "anyone on the host" to "any authenticated user" — every tenant's canvas
    would still be readable and writable by every other tenant's users, given a
    version id.
    """
    try:
        version_uuid = UUID(workflow_version_id)
        tenant_uuid = UUID(tenant_id)
    except (ValueError, AttributeError, TypeError):
        # A malformed id cannot own anything. Refusing beats letting a bad
        # cast raise into a 500 that reads like a server fault.
        return False

    async with client.get_client(tenant_id=tenant_id) as session:
        result = await session.execute(
            select(AgentsORM.tenant_id)
            .select_from(WorkflowVersionsORM)
            .join(WorkflowsORM, WorkflowsORM.id == WorkflowVersionsORM.workflow_id)
            .join(AgentsORM, AgentsORM.id == WorkflowsORM.agent_id)
            .where(WorkflowVersionsORM.id == version_uuid)
        )
        owner = result.scalar_one_or_none()

    # A version that does not exist and one owned by another tenant both return
    # False, and the caller turns both into the same 404. Distinguishing them
    # would confirm the existence of another tenant's workflow ids.
    return owner is not None and owner == tenant_uuid
