import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

import basemodel.services_databaseconnector.postgres_orm  # noqa: F401 — registers ORM models
from basemodel.services_databaseconnector.postgres_orm.base import Base

import services.database_connector.postgres.db_client as _pg
import services.database_connector.neo4j.db_client as _neo
import services.database_connector.qdrant.db_client as _qd


@asynccontextmanager
async def lifespan(app: FastAPI):
    pg_url = os.environ.get(
        "POSTGRES_URL",
        "postgresql+asyncpg://admin:dev123@postgres/dataagent",
    )

    # Create all ORM-registered tables in the target database.
    kb_engine = create_async_engine(pg_url)
    async with kb_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await kb_engine.dispose()

    # Step 3: initialize db clients used by router.py.
    _pg.init(pg_url)
    await _neo.init(os.environ.get("NEO4J_URI", "bolt://neo4j:7687"))
    _qd.init(os.environ.get("QDRANT_URL", "http://qdrant:6333"))

    yield


app = FastAPI(title="AeroFlow KB API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from app.router import router

app.include_router(router)
