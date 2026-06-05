import asyncio
from typing import Any

from sqlalchemy import select, update as sa_update, delete as sa_delete, text
from sqlalchemy.engine import CursorResult
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import selectinload, attributes as sa_attributes
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from services.log_set_up import create_logger
from basemodel.services_databaseconnector.shared_model import RetryConfig, HealthCheckLoopConfig, ResponseModel
from pydantic import BaseModel
from basemodel.services_databaseconnector.postgres_model import (
    REGISTRY, MODEL_TO_TABLE, ReadJoinRequest, SelectInLoadRequest, DMLPreparation, DQLPreparation,
    FilterOperator, TransactionOp, TransactionJobResult,
)
import basemodel.services_databaseconnector.postgres_orm  # noqa: F401 — registers ORM classes into REGISTRY

log = create_logger("services.postgres", "service_postgres")


# Helpers
def _orm_to_dict(instance) -> dict[str, Any]:
    return {c.name: getattr(instance, c.name) for c in instance.__table__.columns}


def _orm_to_dict_deep(instance) -> dict[str, Any]:
    result = {c.name: getattr(instance, c.name) for c in instance.__table__.columns}
    state = sa_attributes.instance_state(instance)
    mapper = sa_inspect(type(instance))
    for key, val in state.dict.items():
        if key in result:
            continue
        table_name = (
            mapper.relationships[key].mapper.class_.__tablename__
            if key in mapper.relationships
            else key
        )
        if isinstance(val, list):
            result[table_name] = [_orm_to_dict_deep(v) for v in val]
        elif val is not None and hasattr(val, "__table__"):
            result[table_name] = _orm_to_dict_deep(val)
    return result


def _rel_key_to(orm_cls, target_cls) -> str:
    for rel in sa_inspect(orm_cls).relationships:
        if rel.mapper.class_ is target_cls:
            return rel.key
    raise AttributeError(
        f"No relationship from {orm_cls.__tablename__} to {target_cls.__tablename__}"
    )


def _build_load_option(root_orm_cls, path: str):
    table_names = path.split(".")
    current_cls = root_orm_cls
    option = None
    for table_name in table_names:
        if table_name not in REGISTRY:
            raise AttributeError(f"Unknown table in load path: {table_name}")
        target_cls = REGISTRY[table_name].orm
        rel_key = _rel_key_to(current_cls, target_cls)
        option = (
            selectinload(getattr(current_cls, rel_key))
            if option is None
            else option.selectinload(getattr(current_cls, rel_key))
        )
        current_cls = target_cls
    return option


def _resolve(data: BaseModel):
    table_name = MODEL_TO_TABLE.get(type(data))
    if table_name is None:
        raise AttributeError(f"Unregistered model: {type(data).__name__}")
    return table_name, REGISTRY[table_name].orm


# Query Compilers

_OP_MAP = {
    FilterOperator.EQ: lambda col, val: col == val,
    FilterOperator.NE: lambda col, val: col != val,
    FilterOperator.GT: lambda col, val: col > val,
    FilterOperator.LT: lambda col, val: col < val,
    FilterOperator.GTE: lambda col, val: col >= val,
    FilterOperator.LTE: lambda col, val: col <= val,
}


def _apply_op(col, f):
    return _OP_MAP[f.operator](col, f.value)


def _validate_order_by(order_by, allowed_tables: set[str]) -> None:
    if order_by is None:
        return
    if order_by.table_name not in allowed_tables:
        raise AttributeError(
            f"order_by references table '{order_by.table_name}' which is not part of this query"
        )
    schema = REGISTRY[order_by.table_name]
    valid_columns = {c.name for c in schema.orm.__table__.columns}
    if order_by.column not in valid_columns:
        raise AttributeError(
            f"order_by column '{order_by.column}' does not exist on '{order_by.table_name}'"
        )

def _prepare_insert(data: BaseModel) -> DMLPreparation:
    table_name, orm_cls = _resolve(data)
    return DMLPreparation(
        instance=orm_cls(**data.model_dump(mode="json")),
        orm_cls=orm_cls,
        table_name=table_name,
    )


def _prepare_deep_search(data: SelectInLoadRequest) -> DQLPreparation:
    schema = REGISTRY.get(data.table)
    if schema is None:
        raise AttributeError(f"Unknown table: {data.table}")
    orm_cls = schema.orm

    _validate_order_by(data.order_by, {data.table})

    load_options = [_build_load_option(orm_cls, path) for path in data.load_paths]

    stmt = select(orm_cls).options(*load_options)

    if data.tenant_id is not None:
        stmt = stmt.where(orm_cls.tenant_id == data.tenant_id)
    for f in data.filters:
        stmt = stmt.where(_apply_op(getattr(orm_cls, f.column_name), f))
    if data.cursor is not None:
        stmt = stmt.where(orm_cls.inserted_at > data.cursor)

    stmt = stmt.order_by(
        text(
            f'"{data.order_by.table_name}".{data.order_by.column} {data.order_by.order.value}') if data.order_by else orm_cls.inserted_at.asc()
    ).limit(data.limit)
    return DQLPreparation(compiled_query=stmt)


def _prepare_soft_delete(data: BaseModel) -> DMLPreparation:
    table_name, orm_cls = _resolve(data)
    try:
        orm_cls.is_deleted
    except AttributeError:
        raise AttributeError(f"{orm_cls.__tablename__} does not support soft delete — missing is_deleted column")
    fields = data.model_dump()
    pk_fields = {k: v for k, v in fields.items() if v is not None}

    conditions = [
        orm_cls.is_deleted == False,
        *[getattr(orm_cls, k) == v for k, v in pk_fields.items()],
    ]
    return DMLPreparation(table_name=table_name, compiled_query=sa_update(orm_cls).where(*conditions).values(is_deleted=True))


def _prepare_delete(data: BaseModel) -> DMLPreparation:
    table_name, orm_cls = _resolve(data)
    fields = data.model_dump()
    conditions = [getattr(orm_cls, k) == v for k, v in fields.items() if v is not None]
    if not conditions:
        raise AttributeError("delete requires at least one field to identify the record")
    return DMLPreparation(table_name=table_name, compiled_query=sa_delete(orm_cls).where(*conditions))


def _prepare_flush(table_name: str) -> DMLPreparation:
    schema = REGISTRY.get(table_name)
    if schema is None:
        raise AttributeError(f"Unknown table: {table_name}")
    orm_cls = schema.orm
    try:
        orm_cls.is_deleted
    except AttributeError:
        raise AttributeError(f"{orm_cls.__tablename__} does not support flush — missing is_deleted column")
    return DMLPreparation(compiled_query=sa_delete(orm_cls).where(orm_cls.is_deleted == True))


def _prepare_read(data: ReadJoinRequest) -> DQLPreparation:
    unknown = [t for t in data.joins_table if t not in REGISTRY]
    if unknown:
        raise AttributeError(f"Unknown table(s): {unknown}")

    _validate_order_by(data.order_by, set(data.joins_table))

    orm_map = {t: REGISTRY[t].orm for t in data.joins_table}
    from_cls = orm_map[data.joins_table[0]]

    if data.selected_columns:
        # use alias if provided, otherwise just the column name (caller adds alias for ambiguous joins)
        cols = [
            getattr(orm_map[c.table_name], c.column_name).label(c.alias or c.column_name)
            for c in data.selected_columns
        ]
        stmt = select(*cols).select_from(from_cls)
    else:
        # enumerate columns explicitly so mappings() returns flat dicts not ORM objects
        cols = [col for orm_cls in orm_map.values() for col in orm_cls.__table__.columns]
        stmt = select(*cols).select_from(from_cls)

    # single table → loop skipped; multi-table → SQLAlchemy resolves ON from FK metadata
    for i in range(1, len(data.joins_table)):
        join_cls = orm_map[data.joins_table[i]]
        stmt = stmt.join(join_cls)

    if data.tenant_id is not None:
        stmt = stmt.where(from_cls.tenant_id == data.tenant_id)
    for f in data.filters:
        stmt = stmt.where(_apply_op(getattr(orm_map[f.table_name], f.column_name), f))
    if data.cursor is not None:
        stmt = stmt.where(from_cls.inserted_at > data.cursor)
    # exclude soft-deleted rows if the root table supports it
    try:
        stmt = stmt.where(from_cls.is_deleted == False)
    except AttributeError:
        pass

    stmt = stmt.order_by(
        text(
            f'"{data.order_by.table_name}".{data.order_by.column} {data.order_by.order.value}') if data.order_by else from_cls.inserted_at.asc()
    )
    return DQLPreparation(compiled_query=stmt.limit(data.limit))


class PostgresTransaction:
    __slots__ = ("_session_factory", "_jobs")

    def __init__(self, session_factory: async_sessionmaker):
        self._session_factory = session_factory
        self._jobs: list[tuple[TransactionOp, BaseModel]] = []

    def add(self, op: TransactionOp, data: BaseModel) -> "PostgresTransaction":
        table_name = MODEL_TO_TABLE.get(type(data))
        if table_name is None:
            raise ValueError(f"Unregistered model: {type(data).__name__}")
        schema = REGISTRY[table_name]
        expected = schema.insert if op == TransactionOp.INSERT else schema.delete
        if expected is not type(data):
            raise ValueError(
                f"{type(data).__name__} is not a valid {op.value} model "
                f"for table '{table_name}' — expected {expected.__name__}"
            )
        self._jobs.append((op, data))
        return self

    async def commit(self) -> ResponseModel:
        # Phase 1 — compile all jobs before touching the DB
        preps: list[tuple[TransactionOp, Any]] = []
        for op, data in self._jobs:
            try:
                if op == TransactionOp.INSERT:
                    preps.append((op, _prepare_insert(data)))
                elif op == TransactionOp.SOFT_DELETE:
                    preps.append((op, _prepare_soft_delete(data)))
                elif op == TransactionOp.DELETE:
                    preps.append((op, _prepare_delete(data)))
            except AttributeError as e:
                return ResponseModel(code=400, error=f"Validation failed: {e}")

        # Phase 2 — execute atomically
        async with self._session_factory() as session:
            try:
                jobs: list[TransactionJobResult] = []
                for op, prep in preps:
                    if op == TransactionOp.INSERT:
                        session.add(prep.instance)
                        await session.flush()
                        jobs.append(TransactionJobResult(method=op, table=prep.table_name, success=True))
                    elif op == TransactionOp.SOFT_DELETE:
                        result: Any = await session.execute(prep.compiled_query)
                        if result.rowcount == 0:
                            raise ValueError("Record not found or already deleted")
                        jobs.append(TransactionJobResult(method=op, table=prep.table_name, success=True))
                    elif op == TransactionOp.DELETE:
                        await session.execute(prep.compiled_query)
                        jobs.append(TransactionJobResult(method=op, table=prep.table_name, success=True))
                await session.commit()
                return ResponseModel(code=200, data=jobs)
            except Exception as e:
                await session.rollback()
                return ResponseModel(code=400, error=f"Transaction rolled back: {e}")


class PostgresClient:
    __slots__ = ("_engine", "_session_factory", "_connection_wait", "_healthy", "_connected", "_timeout", "_url",)

    def __init__(self):
        self._engine: AsyncEngine | None = None
        self._session_factory: async_sessionmaker[AsyncSession] | None = None
        self._timeout: int = 10
        self._connection_wait: int = 5
        self._healthy: bool = False
        self._connected: bool = False
        self._url: str | None = None

    async def open(self, retry: RetryConfig | None = RetryConfig()) -> AsyncSession:
        if self._url is None:
            raise RuntimeError("Postgres URL is not yet set")

        for attempt in range(retry.count):
            try:
                self._engine = create_async_engine(self._url, pool_pre_ping=True)
                self._session_factory = async_sessionmaker(self._engine, expire_on_commit=False)
                async with self._engine.connect() as conn:
                    await conn.execute(text("SELECT 1"))
                self._connected = True
                self._healthy = True
                log.info("Postgres connection established")
                return self.get_client()
            except OperationalError as e:
                log.info(f"Attempt #{attempt + 1}/{retry.count}: Postgres connection failed — {e}")
                await self._engine.dispose()
                self._engine = None
                self._session_factory = None
            await asyncio.sleep(self._connection_wait)
        raise ConnectionError(f"Could not connect to Postgres after {retry.count} attempts")

    async def close(self) -> None:
        if self._engine:
            try:
                await self._engine.dispose()
            except Exception as e:
                log.info(f"Postgres engine dispose error (ignored): {e}")
            finally:
                self._engine = None
                self._session_factory = None
        self._connected = False
        self._healthy = False

    async def health_check_loop(self, config: HealthCheckLoopConfig | None = HealthCheckLoopConfig()) -> None:
        log.info("Postgres health check loop started")
        while True:
            try:
                await self._check_health(config)
                self._healthy = True
            except Exception as e:
                self._healthy = False
                log.warning(f"Postgres unhealthy: {e} — attempting reconnect")
                await self._reconnect()
            await asyncio.sleep(config.interval)

    def set_url(self, url: str) -> None:
        if url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        self._url = url

    def is_healthy(self) -> bool:
        return self._healthy

    def is_connected(self) -> bool:
        return self._connected

    def get_client(self) -> AsyncSession:
        if self._session_factory is None:
            raise RuntimeError("PostgresClient is not open — call open() first")
        return self._session_factory()

    async def _check_health(self, config: HealthCheckLoopConfig) -> None:
        async with self._engine.connect() as conn:
            await asyncio.wait_for(
                conn.execute(text("SELECT 1")),
                timeout=config.timeout_for_health_check,
            )

    async def _reconnect(self) -> None:
        await self.close()
        try:
            await self.open(RetryConfig(count=1))
        except ConnectionError as e:
            log.warning(f"Postgres reconnect failed: {e}")

    # Operations
    def create_transaction(self) -> PostgresTransaction:
        if self._session_factory is None:
            raise RuntimeError("PostgresClient is not open — call open() first")
        return PostgresTransaction(self._session_factory)

    async def insert(self, data: BaseModel) -> ResponseModel:
        try:
            prep = _prepare_insert(data)
        except AttributeError as e:
            return ResponseModel(code=400, error=str(e))

        async with self.get_client() as session:
            session.add(prep.instance)
            try:
                await session.commit()
                await session.refresh(prep.instance)
            except IntegrityError:
                await session.rollback()
                return ResponseModel(code=409, error=f"Record already exists in {prep.table_name}")
        pks = [col.name for col in prep.orm_cls.__table__.primary_key]
        return ResponseModel(code=200, data={pk: str(getattr(prep.instance, pk)) for pk in pks if
                                             getattr(prep.instance, pk) is not None})

    async def soft_delete(self, data: BaseModel) -> ResponseModel:
        try:
            prep = _prepare_soft_delete(data)
        except AttributeError as e:
            return ResponseModel(code=400, error=str(e))

        async with self.get_client() as session:
            try:
                result: Any | CursorResult = await session.execute(prep.compiled_query)
                await session.commit()
            except Exception:
                await session.rollback()
                raise
        if result.rowcount == 0:
            return ResponseModel(code=404, error="Record not found or already deleted")
        return ResponseModel(code=200)

    async def delete(self, data: BaseModel) -> ResponseModel:
        try:
            prep = _prepare_delete(data)
        except AttributeError as e:
            return ResponseModel(code=400, error=str(e))

        async with self.get_client() as session:
            try:
                await session.execute(prep.compiled_query)
                await session.commit()
            except Exception:
                await session.rollback()
                raise
        return ResponseModel(code=200)

    async def read_deep(self, data: SelectInLoadRequest) -> ResponseModel:
        try:
            prep = _prepare_deep_search(data)
        except AttributeError as e:
            return ResponseModel(code=400, error=str(e))

        async with self.get_client() as session:
            result = await session.execute(prep.compiled_query)
            rows = result.scalars().all()
        return ResponseModel(code=200, data=[_orm_to_dict_deep(r) for r in rows])

    async def flush(self, table_name: str) -> ResponseModel:
        try:
            prep = _prepare_flush(table_name)
        except AttributeError as e:
            return ResponseModel(code=400, error=str(e))

        async with self.get_client() as session:
            try:
                await session.execute(prep.compiled_query)
                await session.commit()
            except Exception:
                await session.rollback()
                raise
        return ResponseModel(code=200)

    async def read(self, data: ReadJoinRequest) -> ResponseModel:
        try:
            prep = _prepare_read(data)
        except AttributeError as e:
            return ResponseModel(code=400, error=str(e))

        async with self.get_client() as session:
            result = await session.execute(prep.compiled_query)
            rows = result.mappings().all()
        return ResponseModel(code=200, data=[dict(r) for r in rows])

    # ── Flow-builder business methods ─────────────────────────────────────────

    async def create_agent(self, name: str, description: str, tenant_id: str) -> dict:
        from uuid import UUID
        from sqlalchemy import func as sa_func
        from basemodel.services_databaseconnector.postgres_orm.workflow_orm import (
            AgentsORM, WorkflowsORM, WorkflowVersionsORM, AgentVersionsORM,
        )
        import os
        _DEV_RESPONDER_MODEL_ID = os.environ.get("DEV_RESPONDER_MODEL_ID", "00000000-0000-0000-0000-000000000011")

        async with self.get_client() as session:
            async with session.begin():
                agent = AgentsORM(tenant_id=UUID(tenant_id), name=name, description=description)
                session.add(agent)
                await session.flush()

                workflow = WorkflowsORM(agent_id=agent.id, name="Default Workflow", description="")
                session.add(workflow)
                await session.flush()

                wv = WorkflowVersionsORM(workflow_id=workflow.id, version=1, status="draft")
                session.add(wv)
                await session.flush()

                av = AgentVersionsORM(
                    agent_id=agent.id, version=1, status="draft",
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

    async def list_agents(self, tenant_id: str) -> list[dict]:
        from uuid import UUID
        from sqlalchemy import select
        from basemodel.services_databaseconnector.postgres_orm.workflow_orm import AgentsORM

        async with self.get_client() as session:
            result = await session.execute(
                select(AgentsORM)
                .where(AgentsORM.tenant_id == UUID(tenant_id))
                .where(AgentsORM.deleted_at.is_(None))
                .order_by(AgentsORM.created_at.desc())
            )
            rows = result.scalars().all()
        return [
            {
                "id": str(r.id), "name": r.name, "description": r.description,
                "published_version_id": str(r.published_version_id) if r.published_version_id else None,
                "draft_version_id": str(r.draft_version_id) if r.draft_version_id else None,
                "created_at": r.created_at,
            }
            for r in rows
        ]

    async def get_agent(self, agent_id: str) -> dict | None:
        from uuid import UUID
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload
        from basemodel.services_databaseconnector.postgres_orm.workflow_orm import (
            AgentsORM, AgentVersionsORM, WorkflowVersionsORM,
        )

        async with self.get_client() as session:
            result = await session.execute(
                select(AgentsORM)
                .options(
                    selectinload(AgentsORM.published_version)
                    .selectinload(AgentVersionsORM.workflow_version)
                    .selectinload(WorkflowVersionsORM.workflow)
                )
                .where(AgentsORM.id == UUID(agent_id))
                .where(AgentsORM.deleted_at.is_(None))
            )
            agent = result.scalar_one_or_none()
        if not agent:
            return None

        active_workflow = active_workflow_name = None
        if agent.published_version and agent.published_version.workflow_version:
            wf = agent.published_version.workflow_version.workflow
            if wf:
                active_workflow = str(wf.id)
                active_workflow_name = wf.name

        return {
            "id": str(agent.id), "name": agent.name, "description": agent.description,
            "published_version_id": str(agent.published_version_id) if agent.published_version_id else None,
            "draft_version_id": str(agent.draft_version_id) if agent.draft_version_id else None,
            "tenant_id": str(agent.tenant_id),
            "created_at": agent.created_at, "updated_at": agent.updated_at,
            "active_workflow_id": active_workflow, "active_workflow_name": active_workflow_name,
        }

    async def create_workflow(self, agent_id: str, name: str, description: str = "") -> dict:
        from uuid import UUID
        from basemodel.services_databaseconnector.postgres_orm.workflow_orm import WorkflowsORM, WorkflowVersionsORM

        async with self.get_client() as session:
            async with session.begin():
                wf = WorkflowsORM(agent_id=UUID(agent_id), name=name, description=description)
                session.add(wf)
                await session.flush()
                wv = WorkflowVersionsORM(workflow_id=wf.id, version=1, status="draft")
                session.add(wv)
                await session.flush()
        return {"workflow_id": str(wf.id), "workflow_version_id": str(wv.id)}

    async def list_workflows(self, agent_id: str) -> list[dict]:
        from uuid import UUID
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload
        from basemodel.services_databaseconnector.postgres_orm.workflow_orm import WorkflowsORM

        async with self.get_client() as session:
            result = await session.execute(
                select(WorkflowsORM)
                .options(selectinload(WorkflowsORM.workflow_versions))
                .where(WorkflowsORM.agent_id == UUID(agent_id))
                .order_by(WorkflowsORM.created_at.desc())
            )
            rows = result.scalars().all()

        def _pick(versions, status):
            matches = [v for v in versions if v.status == status]
            return str(max(matches, key=lambda v: v.version).id) if matches else None

        return [
            {
                "id": str(r.id), "name": r.name, "description": r.description,
                "is_active": r.is_active, "created_at": r.created_at,
                "draft_version_id": _pick(r.workflow_versions, "draft"),
                "published_version_id": _pick(r.workflow_versions, "published"),
            }
            for r in rows
        ]

    async def update_agent_draft(self, agent_id: str, fields: dict) -> dict:
        from uuid import UUID
        from sqlalchemy import update as sa_update, func as sa_func
        from basemodel.services_databaseconnector.postgres_orm.workflow_orm import AgentsORM, AgentVersionsORM

        agent_fields = {k: v for k, v in fields.items() if k in ("name", "description")}
        av_fields    = {k: v for k, v in fields.items()
                        if k in ("responder_model_id", "system_prompt_id", "guardrail_id", "memory_enabled")}

        async with self.get_client() as session:
            async with session.begin():
                if agent_fields:
                    await session.execute(
                        sa_update(AgentsORM)
                        .where(AgentsORM.id == UUID(agent_id))
                        .values(**agent_fields, updated_at=sa_func.now())
                    )
                if av_fields:
                    uuid_keys = {"responder_model_id", "system_prompt_id", "guardrail_id"}
                    av_values = {k: UUID(v) if k in uuid_keys and v else v for k, v in av_fields.items()}
                    await session.execute(
                        sa_update(AgentVersionsORM)
                        .where(AgentVersionsORM.agent_id == UUID(agent_id))
                        .where(AgentVersionsORM.status == "draft")
                        .values(**av_values)
                    )
        return {"agent_id": agent_id, "updated": list(fields.keys())}

    async def list_workflow_versions(self, workflow_id: str) -> list[dict]:
        from uuid import UUID
        from sqlalchemy import select
        from basemodel.services_databaseconnector.postgres_orm.workflow_orm import WorkflowVersionsORM

        async with self.get_client() as session:
            result = await session.execute(
                select(WorkflowVersionsORM)
                .where(WorkflowVersionsORM.workflow_id == UUID(workflow_id))
                .order_by(WorkflowVersionsORM.version.desc())
            )
            rows = result.scalars().all()
        return [
            {
                "id": str(r.id), "workflow_id": str(r.workflow_id), "version": r.version,
                "status": r.status, "changelog": r.changelog,
                "created_at": r.created_at, "published_at": r.published_at,
            }
            for r in rows
        ]

    async def create_draft_version(self, workflow_id: str) -> dict:
        from uuid import UUID
        from sqlalchemy import select, func as sa_func
        from basemodel.services_databaseconnector.postgres_orm.workflow_orm import WorkflowVersionsORM

        async with self.get_client() as session:
            async with session.begin():
                result = await session.execute(
                    select(sa_func.max(WorkflowVersionsORM.version))
                    .where(WorkflowVersionsORM.workflow_id == UUID(workflow_id))
                )
                max_version = result.scalar() or 0

                draft_result = await session.execute(
                    select(WorkflowVersionsORM)
                    .where(WorkflowVersionsORM.workflow_id == UUID(workflow_id))
                    .where(WorkflowVersionsORM.status == "draft").limit(1)
                )
                if draft_result.scalar_one_or_none():
                    raise ValueError("Already has a draft version")

                published_result = await session.execute(
                    select(WorkflowVersionsORM)
                    .where(WorkflowVersionsORM.workflow_id == UUID(workflow_id))
                    .where(WorkflowVersionsORM.status == "published").limit(1)
                )
                published = published_result.scalar_one_or_none()

                wv = WorkflowVersionsORM(workflow_id=UUID(workflow_id), version=max_version + 1, status="draft")
                session.add(wv)
                await session.flush()

        return {
            "workflow_version_id": str(wv.id), "version": wv.version,
            "source_version_id": str(published.id) if published else None,
        }

    async def publish_workflow_version(self, workflow_version_id: str, changelog: str | None = None) -> dict:
        from uuid import UUID
        from sqlalchemy import update as sa_update, func as sa_func
        from basemodel.services_databaseconnector.postgres_orm.workflow_orm import WorkflowVersionsORM

        async with self.get_client() as session:
            async with session.begin():
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
                wv.published_at = sa_func.now()
                if changelog:
                    wv.changelog = changelog

        return {"workflow_version_id": workflow_version_id, "status": "published"}

    async def republish_workflow_version(self, workflow_version_id: str) -> dict:
        from uuid import UUID
        from sqlalchemy import update as sa_update, func as sa_func
        from basemodel.services_databaseconnector.postgres_orm.workflow_orm import WorkflowVersionsORM

        async with self.get_client() as session:
            async with session.begin():
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
                wv.published_at = sa_func.now()

        return {"workflow_version_id": workflow_version_id, "status": "published"}

    async def delete_workflow_version(self, version_id: str) -> None:
        from uuid import UUID
        from basemodel.services_databaseconnector.postgres_orm.workflow_orm import WorkflowVersionsORM

        async with self.get_client() as session:
            async with session.begin():
                wv = await session.get(WorkflowVersionsORM, UUID(version_id))
                if not wv:
                    raise ValueError("workflow_version not found")
                if wv.status == "published":
                    raise ValueError("Cannot delete a published version")
                await session.delete(wv)

    async def delete_workflow(self, workflow_id: str) -> list[str]:
        from uuid import UUID
        from sqlalchemy import select
        from basemodel.services_databaseconnector.postgres_orm.workflow_orm import WorkflowsORM, WorkflowVersionsORM

        async with self.get_client() as session:
            async with session.begin():
                wf = await session.get(WorkflowsORM, UUID(workflow_id))
                if not wf:
                    raise ValueError("workflow not found")
                versions_result = await session.execute(
                    select(WorkflowVersionsORM).where(WorkflowVersionsORM.workflow_id == UUID(workflow_id))
                )
                versions = versions_result.scalars().all()
                version_ids = [str(v.id) for v in versions]
                for v in versions:
                    await session.delete(v)
                await session.delete(wf)
        return version_ids

    async def delete_agent(self, agent_id: str) -> list[str]:
        from uuid import UUID
        from sqlalchemy import select
        from basemodel.services_databaseconnector.postgres_orm.workflow_orm import AgentsORM, WorkflowsORM, WorkflowVersionsORM

        async with self.get_client() as session:
            async with session.begin():
                agent = await session.get(AgentsORM, UUID(agent_id))
                if not agent:
                    raise ValueError("agent not found")
                wf_result = await session.execute(
                    select(WorkflowsORM).where(WorkflowsORM.agent_id == UUID(agent_id))
                )
                workflows = wf_result.scalars().all()
                version_ids: list[str] = []
                for wf in workflows:
                    wv_result = await session.execute(
                        select(WorkflowVersionsORM).where(WorkflowVersionsORM.workflow_id == wf.id)
                    )
                    for v in wv_result.scalars().all():
                        version_ids.append(str(v.id))
                        await session.delete(v)
                    await session.delete(wf)
                await session.delete(agent)
        return version_ids

    async def publish_agent(self, agent_id: str, workflow_id: str) -> dict:
        import os
        from uuid import UUID
        from sqlalchemy import select, update as sa_update, func as sa_func
        from basemodel.services_databaseconnector.postgres_orm.workflow_orm import (
            AgentsORM, WorkflowsORM, WorkflowVersionsORM, AgentVersionsORM,
        )
        _DEV_RESPONDER_MODEL_ID = os.environ.get("DEV_RESPONDER_MODEL_ID", "00000000-0000-0000-0000-000000000011")

        async with self.get_client() as session:
            async with session.begin():
                wf = await session.get(WorkflowsORM, UUID(workflow_id))
                if not wf or str(wf.agent_id) != agent_id:
                    raise ValueError("workflow_id does not belong to agent_id")

                wv_result = await session.execute(
                    select(WorkflowVersionsORM)
                    .where(WorkflowVersionsORM.workflow_id == UUID(workflow_id))
                    .where(WorkflowVersionsORM.status == "draft")
                    .order_by(WorkflowVersionsORM.version.desc()).limit(1)
                )
                wv = wv_result.scalar_one_or_none()
                if not wv:
                    raise ValueError("No draft workflow_version found")

                wv.status = "published"
                wv.published_at = sa_func.now()

                av_result = await session.execute(
                    select(AgentVersionsORM)
                    .where(AgentVersionsORM.agent_id == UUID(agent_id))
                    .where(AgentVersionsORM.status == "draft")
                    .order_by(AgentVersionsORM.version.desc()).limit(1)
                )
                av_draft = av_result.scalar_one_or_none()

                new_version        = (av_draft.version + 1) if av_draft else 1
                responder_model_id = av_draft.responder_model_id if av_draft else UUID(_DEV_RESPONDER_MODEL_ID)
                system_prompt_id   = av_draft.system_prompt_id if av_draft else None
                guardrail_id       = av_draft.guardrail_id if av_draft else None
                memory_enabled     = av_draft.memory_enabled if av_draft else False
                llm_config         = av_draft.llm_config if av_draft else {}
                retrieval_config   = av_draft.retrieval_config if av_draft else {}

                new_av = AgentVersionsORM(
                    agent_id=UUID(agent_id), version=new_version, status="published",
                    workflow_version_id=wv.id, responder_model_id=responder_model_id,
                    system_prompt_id=system_prompt_id, guardrail_id=guardrail_id,
                    memory_enabled=memory_enabled, llm_config=llm_config,
                    retrieval_config=retrieval_config,
                )
                session.add(new_av)
                await session.flush()

                await session.execute(
                    sa_update(AgentsORM)
                    .where(AgentsORM.id == UUID(agent_id))
                    .values(published_version_id=new_av.id, updated_at=sa_func.now())
                )

        return {
            "agent_id": agent_id,
            "agent_version_id": str(new_av.id),
            "workflow_version_id": str(wv.id),
        }

    # ── Workflow-runtime business methods ─────────────────────────────────────

    async def validate_agent_tenant(self, agent_id: str, tenant_id: str) -> bool:
        from uuid import UUID
        from sqlalchemy import select
        from basemodel.services_databaseconnector.postgres_orm.workflow_orm import AgentsORM

        if not self.is_connected():
            return True
        async with self.get_client() as session:
            result = await session.execute(
                select(AgentsORM.id)
                .where(AgentsORM.id == UUID(agent_id))
                .where(AgentsORM.tenant_id == UUID(tenant_id))
                .where(AgentsORM.is_active.is_(True))
                .where(AgentsORM.deleted_at.is_(None))
            )
            return result.first() is not None

    async def create_conversation(
        self,
        agent_version_id: str = None,
        user_ref: str = "anonymous",
        channel: str = "web",
        tenant_id: str = None,
    ) -> str | None:
        import os
        from basemodel.services_databaseconnector.postgres_model import ConversationsInsert
        from basemodel.services_databaseconnector.postgres_orm.auth_release_orm import MembersORM
        from sqlalchemy import select
        from uuid import UUID

        _DEV_TENANT_ID    = os.getenv("DEV_TENANT_ID",    "00000000-0000-0000-0000-000000000001")
        _DEV_AGENT_VER_ID = os.getenv("DEV_AGENT_VER_ID", "00000000-0000-0000-0000-000000000040")

        if not self.is_connected():
            return None

        tenant_id = tenant_id or _DEV_TENANT_ID

        if user_ref != "anonymous":
            async with self.get_client() as session:
                result = await session.execute(
                    select(MembersORM.tenant_role)
                    .where(MembersORM.tenant_id == UUID(tenant_id))
                    .where(MembersORM.user_id == user_ref)
                    .where(MembersORM.is_active.is_(True))
                )
                row = result.first()
                if not row:
                    return None

        result = await self.insert(ConversationsInsert(
            tenant_id=tenant_id,
            agent_version_id=agent_version_id or _DEV_AGENT_VER_ID,
            user_ref=user_ref,
            channel=channel,
            status="active",
        ))
        if result.code != 200:
            return None
        return result.data.get("id")

    async def save_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        tokens: int = 0,
        latency_ms: int = 0,
        metadata: dict | None = None,
    ) -> str | None:
        from basemodel.services_databaseconnector.postgres_model import MessagesInsert

        if not self.is_connected():
            return None
        result = await self.insert(MessagesInsert(
            conversation_id=conversation_id,
            role=role,
            content={"text": content},
            msg_metadata=metadata or {},
            tokens_used=tokens,
            latency_ms=latency_ms,
        ))
        if result.code != 200:
            return None
        return result.data.get("id")

    async def save_trace(
        self,
        message_id: str,
        trace_index: int,
        tool_name: str,
        input: dict,
        output: dict,
        status: str = "success",
        latency_ms: int = 0,
    ) -> None:
        from basemodel.services_databaseconnector.postgres_model import AgentTracesInsert

        if not self.is_connected():
            return
        result = await self.insert(AgentTracesInsert(
            message_id=message_id,
            trace_index=trace_index,
            tool_name=tool_name,
            input=input,
            output=output,
            status=status,
            latency_ms=latency_ms,
        ))
        if result.code not in (200, 409):
            log.error("save_trace failed: %s", result.error)


client = PostgresClient()
