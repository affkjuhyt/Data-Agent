#!/usr/bin/env python3
"""
dev_reset.py — wipe all pipeline test data for a clean run, using the SAME
document-flush path as the Trash "permanent delete" (``trash_ops.flush``).

Why reuse flush
---------------
The Trash bin's flush is the single, authoritative multi-store delete for a
document. Driving the reset through it means the reset can never drift from the
real delete path: whatever flush cleans (and in whatever order), the reset cleans
identically. For every KBData row (any tenant, incl. soft-deleted) we run the
document-level flush:

  Qdrant   delete chunk + doc points by data_id  (knowledge, knowledge_documents)
  Neo4j    delete this document's RELATES edges   (KB-shared :Entity nodes survive)
  Postgres delete KBTextBlock → (versions, tables via ON DELETE CASCADE),
           KBTable, KBLifecycleHistory, KBData
  MinIO    delete the source object
  Mongo    delete kb_silver_staging / kb_ingestion_logs / pipeline_events by data_id

Full-reset extras (what per-document flush intentionally does NOT touch, because
they are shared or not document-scoped):

  Qdrant   clear entity_registry + query_cache collections
  Neo4j    delete any remaining (now-orphan) :Entity nodes
  Postgres clear KBConflict + KBConflictBatch (no FK to KBData → never cascaded)
  Mongo    clear pipeline_event_seq

Preserved (baseline / re-seeded data): Tenants, RolePermissions, KBModel,
KBKnowledgeBase, filter/extraction policies, KBWarehouse(+configs).

Usage
-----
    # Run INSIDE the kb-backend container (it has the package + connectors and
    # reads the same POSTGRES_URL / QDRANT_URL / … the service uses):
    docker exec e2e-kb-backend python /app/services/kb_backend/dev_reset.py

    # Connection strings are read from the service env / light_rag config; override
    # any of POSTGRES_URL / MONGO_URL / MINIO_URL (QDRANT_URL / NEO4J_URL come from
    # services.kb_backend.pipeline.light_rag.config) if running elsewhere.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys

from sqlalchemy import select as sa_select, delete as sa_delete
from qdrant_client import models as qmodels

from libs.basemodel.services_databaseconnector.shared_model import RetryConfig
from libs.basemodel.services_databaseconnector.postgres_orm.knowledge_base_orm import (
    KBDataORM, KBConflictORM, KBConflictBatchORM,
)
from libs.database_connector.postgres_connector import PostgresClient
from libs.database_connector.qdrant_connector import QdrantClient as QdrantService
from libs.database_connector.neo4j_connector import Neo4jClient
from libs.database_connector.mongo_connector import MongoClient
from libs.database_connector.minio_connector import MinIOClient
from services.kb_backend.pipeline.light_rag import config as cfg
from services.kb_backend.operations import trash_ops

# Connection strings — same sources the service uses (env + light_rag config).
POSTGRES_URL = os.getenv("POSTGRES_URL", "postgresql+asyncpg://admin:dev123@postgres/aeroflow_kb")
MONGO_URL    = os.getenv("MONGO_URL",    "mongodb://mongo:27017/kb_staging")
MINIO_URL    = os.getenv("MINIO_URL",    "http://minio:miniominio@minio:9000")

# Shared / non-doc-scoped Qdrant collections flush leaves alone — cleared fully here.
_EXTRA_QDRANT = ["entity_registry", "query_cache"]

# Quiet the worker-style INFO logging from trash_ops so the reset output stays readable.
logging.basicConfig(level=logging.WARNING)

# ── Colour helpers ──────────────────────────────────────────────────────────────
_USE_COLOUR = sys.stdout.isatty()

def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _USE_COLOUR else text

def ok(msg: str)   -> None: print(_c("32", "  [OK]  ") + msg)
def skip(msg: str) -> None: print(_c("33", " [SKIP] ") + msg)
def err(msg: str)  -> None: print(_c("31", " [ERR]  ") + msg)
def section(title: str) -> None:
    print()
    print(_c("1;36", f"-- {title} ") + _c("36", "-" * max(1, 50 - len(title))))


async def _open_clients() -> tuple[PostgresClient, QdrantService, Neo4jClient, MongoClient, MinIOClient]:
    retry = RetryConfig()
    postgres = PostgresClient(); postgres.set_url(POSTGRES_URL); await postgres.open(retry)
    qdrant   = QdrantService();  qdrant.set_url(cfg.QDRANT_URL);  await qdrant.open(retry)
    neo4j    = Neo4jClient();    neo4j.set_url(cfg.NEO4J_URL);    await neo4j.open(retry)
    mongo    = MongoClient();    mongo.set_url(MONGO_URL);        await mongo.open(retry)
    minio    = MinIOClient();    minio.set_url(MINIO_URL);        await minio.open(retry)
    return postgres, qdrant, neo4j, mongo, minio


async def _flush_all_documents(
    postgres: PostgresClient, qdrant: QdrantService, neo4j: Neo4jClient,
    mongo: MongoClient, minio: MinIOClient,
) -> None:
    """Run the Trash document-flush for EVERY KBData row (any tenant, incl.
    soft-deleted) — the exact path the UI permanent-delete uses."""
    section("Documents (via trash_ops.flush)")
    async with postgres.get_client() as session:
        rows = (await session.execute(
            sa_select(KBDataORM.data_id, KBDataORM.tenant_id)
        )).all()
    docs = [(str(r.data_id), str(r.tenant_id)) for r in rows]

    if not docs:
        ok("no documents to flush")
        return

    flushed = failed = 0
    for data_id, tenant_id in docs:
        try:
            await trash_ops.flush(
                postgres, qdrant, neo4j,
                level="document", item_id=data_id, tenant_id=tenant_id,
                minio=minio, mongo=mongo,
            )
            flushed += 1
        except Exception as e:
            failed += 1
            err(f"flush {data_id}: {e}")
    ok(f"flushed {flushed}/{len(docs)} documents" + (f" ({failed} failed)" if failed else ""))


async def _reset_extras(
    postgres: PostgresClient, qdrant: QdrantService, neo4j: Neo4jClient, mongo: MongoClient,
) -> None:
    """Clear the shared / non-doc-scoped state flush does not touch."""
    # Qdrant — shared collections (entity_registry, query_cache).
    section("Qdrant (shared collections)")
    client = qdrant.get_client()
    for col in _EXTRA_QDRANT:
        try:
            if not await client.collection_exists(col):
                skip(f"collection '{col}' does not exist")
                continue
            await client.delete(
                collection_name=col,
                points_selector=qmodels.FilterSelector(filter=qmodels.Filter()),  # empty = all
            )
            ok(f"collection '{col}': all points deleted")
        except Exception as e:
            err(f"collection '{col}': {e}")

    # Neo4j — any remaining (orphan) :Entity nodes. Per-doc flush only drops a
    # document's RELATES edges; KB-shared entity nodes survive each flush, so a
    # full reset removes whatever is left.
    section("Neo4j (orphan entities)")
    try:
        async with neo4j.get_client().session() as s:
            res = await s.run("MATCH (n:Entity) RETURN count(n) AS c")
            n = (await res.single())["c"]
            await (await s.run("MATCH (n:Entity) DETACH DELETE n")).consume()
        ok(f"deleted {n} remaining :Entity node(s)")
    except Exception as e:
        err(f"entity cleanup: {e}")

    # Postgres — conflicts have no FK to KBData, so flush never removed them.
    section("Postgres (conflicts)")
    try:
        async with postgres.get_client() as session:
            await session.execute(sa_delete(KBConflictORM))
            await session.execute(sa_delete(KBConflictBatchORM))
            await session.commit()
        ok("cleared KBConflict + KBConflictBatch")
    except Exception as e:
        err(f"conflict cleanup: {e}")

    # Mongo — the per-document collections are cleared by flush; the seq counter is not.
    section("Mongo (event sequence)")
    try:
        res = await mongo.get_client()["pipeline_event_seq"].delete_many({})
        ok(f"pipeline_event_seq: {res.deleted_count} document(s) deleted")
    except Exception as e:
        err(f"pipeline_event_seq: {e}")


async def main() -> None:
    print(_c("1;33", "\n=============================================="))
    print(_c("1;33",   "   DEV PIPELINE DATA RESET (flush path)"))
    print(_c("1;33",   "=============================================="))

    postgres = qdrant = neo4j = mongo = minio = None
    try:
        postgres, qdrant, neo4j, mongo, minio = await _open_clients()
        await _flush_all_documents(postgres, qdrant, neo4j, mongo, minio)
        await _reset_extras(postgres, qdrant, neo4j, mongo)
    finally:
        for c in (postgres, qdrant, neo4j, mongo, minio):
            if c is not None:
                try:
                    await c.close()
                except Exception:
                    pass

    print()
    print(_c("1;32", "Done. All pipeline data cleared via the flush path — ready for a fresh test run."))
    print()


if __name__ == "__main__":
    asyncio.run(main())
