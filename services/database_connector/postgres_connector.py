import asyncio
import uuid
from typing import Any

from sqlalchemy import select, update as sa_update, delete as sa_delete, text, tuple_, func
from sqlalchemy.engine import CursorResult
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import selectinload, attributes as sa_attributes
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

import logging
from basemodel.services_databaseconnector.shared_model import RetryConfig, HealthCheckLoopConfig, ResponseModel
from pydantic import BaseModel
from basemodel.services_databaseconnector.postgres_model import (
    REGISTRY, MODEL_TO_TABLE, ReadJoinRequest, SelectInLoadRequest, DMLPreparation, DQLPreparation,
    FilterOperator, TransactionOp, TransactionJobResult,
)
import basemodel.services_databaseconnector.postgres_orm  # noqa: F401 — registers ORM classes into REGISTRY

log = logging.getLogger(__name__)


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
    FilterOperator.IN: lambda col, val: col.in_(val if isinstance(val, (list, tuple, set)) else [val]),
}


def _apply_op(col, f):
    value = f.value
    is_uuid_col = hasattr(col.type, 'python_type') and col.type.python_type == uuid.UUID
    # Convert string→UUID for UUID columns. For IN, coerce each element of the list.
    if is_uuid_col:
        def _coerce(v):
            if isinstance(v, str):
                try:
                    return uuid.UUID(v)
                except (ValueError, TypeError):
                    return v
            return v
        if f.operator == FilterOperator.IN and isinstance(value, (list, tuple, set)):
            value = [_coerce(v) for v in value]
        else:
            value = _coerce(value)
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

def _apply_keyset(stmt, root_cls, cursor, order_by):
    """Keyset pagination with a stable tiebreaker.

    Orders by (inserted_at, <pk>) and, when a cursor (the last row of the prior
    page) is given, filters WHERE (inserted_at, pk) > (cur_ts, cur_id). The pk
    tiebreaker is essential: inserted_at is NOT unique (bulk-seeded rows share a
    timestamp), so a single-column `inserted_at > cur` skips every row tied at
    the boundary — that was the page-2-empty bug. The pk ordering is applied on
    page 1 too, so the order the cursor is derived from matches the continuation.
    """
    pk_col  = sa_inspect(root_cls).primary_key[0]
    pk_name = pk_col.name
    pk_attr = getattr(root_cls, pk_name)
    ks_attr = root_cls.inserted_at  # the column advance() records into the cursor
    asc = order_by is None or str(order_by.order.value).upper() == "ASC"

    if cursor is not None:
        if isinstance(cursor, dict):
            cur_ts, cur_id = cursor.get("inserted_at"), cursor.get(pk_name)
        else:  # legacy single-column cursor (datetime)
            cur_ts, cur_id = cursor, None
        if cur_ts is not None and cur_id is not None:
            boundary = tuple_(ks_attr, pk_attr)
            stmt = stmt.where(boundary > (cur_ts, cur_id) if asc else boundary < (cur_ts, cur_id))
        elif cur_ts is not None:
            stmt = stmt.where(ks_attr > cur_ts if asc else ks_attr < cur_ts)

    if order_by is not None:
        primary = text(f'"{order_by.table_name}".{order_by.column} {order_by.order.value}')
    else:
        primary = ks_attr.asc()
    return stmt.order_by(primary, pk_attr.asc() if asc else pk_attr.desc())


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
    stmt = _apply_keyset(stmt, orm_cls, data.cursor, data.order_by).limit(data.limit)
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
    # exclude soft-deleted rows if the root table supports it
    try:
        stmt = stmt.where(from_cls.is_deleted == False)
    except AttributeError:
        pass

    stmt = _apply_keyset(stmt, from_cls, data.cursor, data.order_by)
    return DQLPreparation(compiled_query=stmt.limit(data.limit))


def _prepare_count(data: ReadJoinRequest) -> DQLPreparation:
    """COUNT(*) over the same joins/filters as _prepare_read (no keyset/limit).
    Powers `total` for the numbered pager — keyset itself never needs this, but the
    UI does to render '1 … N'."""
    unknown = [t for t in data.joins_table if t not in REGISTRY]
    if unknown:
        raise AttributeError(f"Unknown table(s): {unknown}")

    orm_map = {t: REGISTRY[t].orm for t in data.joins_table}
    from_cls = orm_map[data.joins_table[0]]

    stmt = select(func.count()).select_from(from_cls)
    for i in range(1, len(data.joins_table)):
        stmt = stmt.join(orm_map[data.joins_table[i]])

    if data.tenant_id is not None:
        tenant_uuid = uuid.UUID(data.tenant_id) if isinstance(data.tenant_id, str) else data.tenant_id
        stmt = stmt.where(from_cls.tenant_id == tenant_uuid)
    for f in data.filters:
        stmt = stmt.where(_apply_op(getattr(orm_map[f.table_name], f.column_name), f))
    try:
        stmt = stmt.where(from_cls.is_deleted == False)
    except AttributeError:
        pass
    return DQLPreparation(compiled_query=stmt)


def _prepare_count(data: ReadJoinRequest) -> DQLPreparation:
    """COUNT(*) over the same joins/filters as _prepare_read (no keyset/limit/offset).
    Used to compute `total` for numbered pagination."""
    unknown = [t for t in data.joins_table if t not in REGISTRY]
    if unknown:
        raise AttributeError(f"Unknown table(s): {unknown}")

    orm_map = {t: REGISTRY[t].orm for t in data.joins_table}
    from_cls = orm_map[data.joins_table[0]]

    stmt = select(func.count()).select_from(from_cls)
    for i in range(1, len(data.joins_table)):
        stmt = stmt.join(orm_map[data.joins_table[i]])

    if data.tenant_id is not None:
        tenant_uuid = uuid.UUID(data.tenant_id) if isinstance(data.tenant_id, str) else data.tenant_id
        stmt = stmt.where(from_cls.tenant_id == tenant_uuid)
    for f in data.filters:
        stmt = stmt.where(_apply_op(getattr(orm_map[f.table_name], f.column_name), f))
    try:
        stmt = stmt.where(from_cls.is_deleted == False)
    except AttributeError:
        pass
    return DQLPreparation(compiled_query=stmt)


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


from contextlib import asynccontextmanager

@asynccontextmanager
async def _tenant_session(factory, tenant_id: str):
    async with factory() as session:
        await session.execute(text("SELECT set_config('app.tenant_id', :tid, false)"), {"tid": str(tenant_id)})
        await session.commit()
        try:
            yield session
        finally:
            try:
                await session.rollback()
                await session.execute(text("SELECT set_config('app.tenant_id', '', false)"))
                await session.commit()
            except Exception:
                pass


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

    def get_client(self, tenant_id: str | None = None):
        if self._session_factory is None:
            raise RuntimeError("PostgresClient is not open — call open() first")
        if tenant_id is None:
            return self._session_factory()
        return _tenant_session(self._session_factory, tenant_id)

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

        tenant_id = str(getattr(data, "tenant_id", None) or "")
        pks = [col.name for col in prep.orm_cls.__table__.primary_key]
        pk_data: dict = {}
        async with self.get_client(tenant_id=tenant_id or None) as session:
            session.add(prep.instance)
            try:
                await session.commit()
                await session.refresh(prep.instance)
                # Read PK values inside the session before _tenant_session.finally
                # calls rollback() which expires all instance attributes.
                pk_data = {pk: str(getattr(prep.instance, pk)) for pk in pks if
                           getattr(prep.instance, pk) is not None}
            except IntegrityError as e:
                await session.rollback()
                # Distinguish the constraint that failed (Postgres SQLSTATE via the
                # underlying asyncpg error) instead of blanket-labelling everything
                # "already exists" — a foreign-key or not-null failure is a very
                # different problem from a duplicate.
                sqlstate = getattr(getattr(e, "orig", None), "sqlstate", None)
                if sqlstate == "23505":      # unique_violation
                    return ResponseModel(code=409, error=f"Record already exists in {prep.table_name}")
                if sqlstate == "23503":      # foreign_key_violation
                    return ResponseModel(code=409, error=f"{prep.table_name}: references a record that does not exist")
                if sqlstate == "23502":      # not_null_violation
                    return ResponseModel(code=400, error=f"{prep.table_name}: a required field is missing")
                if sqlstate == "23514":      # check_violation
                    return ResponseModel(code=400, error=f"{prep.table_name}: a value failed a constraint check")
                log.warning("insert integrity error in %s (sqlstate=%s): %s", prep.table_name, sqlstate, e)
                return ResponseModel(code=409, error=f"Integrity error in {prep.table_name}")
        return ResponseModel(code=200, data=pk_data)

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

    async def read_count(self, data: ReadJoinRequest) -> ResponseModel:
        """Total row count for the same filters as read() — for numbered pagination."""
        try:
            prep = _prepare_count(data)
        except AttributeError as e:
            return ResponseModel(code=400, error=str(e))

        async with self.get_client() as session:
            total = (await session.execute(prep.compiled_query)).scalar_one()
        return ResponseModel(code=200, data=int(total or 0))


client = PostgresClient()
