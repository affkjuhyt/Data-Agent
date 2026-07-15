"""
Dict-based wrapper for router.py.
Provides read/read_join/create/update/delete with simple table-name + dict API.
"""
from typing import Any

from sqlalchemy import select, update as sa_update, delete as sa_delete
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from libs.basemodel.services_databaseconnector.postgres_model import REGISTRY, ReadJoinRequest
from libs.basemodel.services_databaseconnector.shared_model import ResponseModel
import libs.basemodel.services_databaseconnector.postgres_orm  # noqa — registers ORM models

_factory: async_sessionmaker | None = None


def init(url: str) -> None:
    global _factory
    engine = create_async_engine(url, pool_pre_ping=True)
    _factory = async_sessionmaker(engine, expire_on_commit=False)


def _get_factory() -> async_sessionmaker:
    if _factory is None:
        raise RuntimeError("postgres db_client not initialized — call init(url) first")
    return _factory


def _to_dict(instance: Any) -> dict[str, Any]:
    return {c.name: getattr(instance, c.name) for c in instance.__table__.columns}


async def read(table: str, filters: dict[str, Any]) -> ResponseModel[Any]:
    filters = dict(filters)
    limit = filters.pop("limit", 200)
    schema = REGISTRY.get(table)
    if schema is None:
        return ResponseModel(code=400, error=f"Unknown table: {table}")
    orm_cls = schema.orm
    conditions = [getattr(orm_cls, k) == v for k, v in filters.items()]
    stmt = select(orm_cls).where(*conditions).limit(limit)
    async with _get_factory()() as session:
        result = await session.execute(stmt)
        rows = result.scalars().all()
    return ResponseModel(code=200, data=[_to_dict(r) for r in rows])


async def read_join(query: dict[str, Any]) -> ResponseModel[Any]:
    q = dict(query)
    if isinstance(q.get("order_by"), str):
        q.pop("order_by")
    req = ReadJoinRequest.model_validate(q)
    from libs.database_connector.postgres_connector import _prepare_read
    prep = _prepare_read(req)
    async with _get_factory()() as session:
        result = await session.execute(prep.compiled_query)
        rows = result.mappings().all()
    return ResponseModel(code=200, data=[dict(r) for r in rows])


async def create(table: str, data: dict[str, Any]) -> ResponseModel[Any]:
    schema = REGISTRY.get(table)
    if schema is None:
        return ResponseModel(code=400, error=f"Unknown table: {table}")
    orm_cls = schema.orm
    valid = {c.name for c in orm_cls.__table__.columns}
    instance = orm_cls(**{k: v for k, v in data.items() if k in valid})
    async with _get_factory()() as session:
        session.add(instance)
        try:
            await session.commit()
            await session.refresh(instance)
        except Exception as e:
            await session.rollback()
            return ResponseModel(code=400, error=str(e))
    pks = [c.name for c in orm_cls.__table__.primary_key]
    return ResponseModel(code=200, data={pk: getattr(instance, pk) for pk in pks})


async def update(table: str, data: dict[str, Any]) -> ResponseModel[Any]:
    schema = REGISTRY.get(table)
    if schema is None:
        return ResponseModel(code=400, error=f"Unknown table: {table}")
    orm_cls = schema.orm
    pk_names = {c.name for c in orm_cls.__table__.primary_key}
    pk_vals = {k: v for k, v in data.items() if k in pk_names}
    upd_vals = {k: v for k, v in data.items() if k not in pk_names}
    if not pk_vals or not upd_vals:
        return ResponseModel(code=400, error="update requires PK field(s) and at least one value to set")
    conditions = [getattr(orm_cls, k) == v for k, v in pk_vals.items()]
    stmt = sa_update(orm_cls).where(*conditions).values(**upd_vals)
    async with _get_factory()() as session:
        try:
            await session.execute(stmt)
            await session.commit()
        except Exception as e:
            await session.rollback()
            return ResponseModel(code=400, error=str(e))
    return ResponseModel(code=200)


async def delete(table: str, data: dict[str, Any]) -> ResponseModel[Any]:
    schema = REGISTRY.get(table)
    if schema is None:
        return ResponseModel(code=400, error=f"Unknown table: {table}")
    orm_cls = schema.orm
    conditions = [getattr(orm_cls, k) == v for k, v in data.items()]
    if not conditions:
        return ResponseModel(code=400, error="delete requires at least one field")
    stmt = sa_delete(orm_cls).where(*conditions)
    async with _get_factory()() as session:
        try:
            await session.execute(stmt)
            await session.commit()
        except Exception as e:
            await session.rollback()
            return ResponseModel(code=400, error=str(e))
    return ResponseModel(code=200)
