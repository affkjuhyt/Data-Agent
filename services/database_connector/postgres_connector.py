import asyncio
import uuid
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
    value = f.value
    # Convert string to UUID if column is UUID type and value is string
    if isinstance(value, str) and hasattr(col.type, 'python_type') and col.type.python_type == uuid.UUID:
        try:
            value = uuid.UUID(value)
        except (ValueError, TypeError):
            pass  # Not a UUID string, use original value
    return _OP_MAP[f.operator](col, value)


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
        tenant_uuid = uuid.UUID(data.tenant_id) if isinstance(data.tenant_id, str) else data.tenant_id
        stmt = stmt.where(orm_cls.tenant_id == tenant_uuid)
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
        tenant_uuid = uuid.UUID(data.tenant_id) if isinstance(data.tenant_id, str) else data.tenant_id
        stmt = stmt.where(from_cls.tenant_id == tenant_uuid)
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


client = PostgresClient()
