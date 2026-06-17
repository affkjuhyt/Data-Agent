#!/usr/bin/env python3
"""
dev_reset.py — wipe all pipeline test data for a clean run.

What gets cleared
-----------------
Postgres   KBData  (CASCADE → KBTextBlock, KBTextBlockVersion, KBTextTable,
                    KBLifecycleHistory, KBConflict, KBConflictBatch)
MongoDB    kb_silver_staging · kb_ingestion_logs
           pipeline_events   · pipeline_event_seq
Qdrant     delete all points from 'knowledge' + 'entity_registry' collections
Neo4j      MATCH (n:Entity) DETACH DELETE n
MinIO      delete every object in the 'knowledge-base' bucket

Nothing else (Tenants, RolePermissions, KBModel, policies, …) is touched,
so re-seeded baseline data survives.

Usage
-----
    # from repo root, with docker stack running:
    python services/kb_backend/dev_reset.py

    # override connection strings if needed:
    POSTGRES_URL=postgresql://aeroflow@localhost/aeroflow_kb  \\
    MONGO_URL=mongodb://localhost:27017/kb_staging            \\
    python services/kb_backend/dev_reset.py
"""
from __future__ import annotations

import asyncio
import os
import sys

# ── Connection defaults (match docker-compose.kb-ui-testing.yml) ─────────────
POSTGRES_URL  = os.getenv("POSTGRES_URL",  "postgresql://aeroflow@localhost:5432/aeroflow_kb")
MONGO_URL     = os.getenv("MONGO_URL",     "mongodb://localhost:27017/kb_staging")
QDRANT_URL    = os.getenv("QDRANT_URL",    "http://localhost:6333")
NEO4J_URL     = os.getenv("NEO4J_URL",     "bolt://localhost:7687")
MINIO_URL     = os.getenv("MINIO_URL",     "localhost:9000")
MINIO_USER    = os.getenv("MINIO_USER",    "minio")
MINIO_PASS    = os.getenv("MINIO_PASS",    "miniominio")
MINIO_BUCKET  = os.getenv("MINIO_BUCKET",  "knowledge-base")

QDRANT_COLLECTIONS = ["knowledge", "knowledge_documents", "entity_registry", "query_cache"]

# ── Colour helpers ────────────────────────────────────────────────────────────
_USE_COLOUR = sys.stdout.isatty()

def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _USE_COLOUR else text

def ok(msg: str)   -> None: print(_c("32", f"  [OK]  ") + msg)
def skip(msg: str) -> None: print(_c("33", f" [SKIP] ") + msg)
def err(msg: str)  -> None: print(_c("31", f" [ERR]  ") + msg)
def section(title: str) -> None:
    print()
    print(_c("1;36", f"-- {title} ") + _c("36", "-" * max(1, 50 - len(title))))


# ── Postgres ──────────────────────────────────────────────────────────────────

async def reset_postgres() -> None:
    section("Postgres")
    try:
        import asyncpg  # type: ignore
    except ImportError:
        skip("asyncpg not installed — skipping Postgres reset")
        return

    try:
        conn = await asyncpg.connect(POSTGRES_URL)
    except Exception as e:
        err(f"Cannot connect: {e}")
        return

    # Order matters: children before parents due to FK constraints.
    # TRUNCATE … CASCADE would handle this, but being explicit is safer.
    tables = [
        "KBConflict",
        "KBConflictBatch",
        "KBTextTable",
        "KBTextBlockVersion",
        "KBTextBlock",
        "KBLifecycleHistory",
        "KBData",
    ]

    try:
        # Use CASCADE on the root tables in case FK graph has other paths.
        sql = 'TRUNCATE TABLE ' + ', '.join(f'"{t}"' for t in tables) + ' RESTART IDENTITY CASCADE'
        await conn.execute(sql)
        ok(f"Truncated {len(tables)} tables: {', '.join(tables)}")
    except Exception as e:
        err(f"Truncate failed: {e}")
    finally:
        await conn.close()


# ── MongoDB ───────────────────────────────────────────────────────────────────

async def reset_mongo() -> None:
    section("MongoDB")
    try:
        from motor.motor_asyncio import AsyncIOMotorClient  # type: ignore
    except ImportError:
        skip("motor not installed — skipping MongoDB reset")
        return

    try:
        client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=4000)
        await client.admin.command("ping")
    except Exception as e:
        err(f"Cannot connect: {e}")
        return

    db_name = MONGO_URL.rsplit("/", 1)[-1].split("?")[0] or "kb_staging"
    db = client[db_name]

    collections = [
        "kb_silver_staging",
        "kb_ingestion_logs",
        "pipeline_events",
        "pipeline_event_seq",
    ]

    for col in collections:
        try:
            result = await db[col].delete_many({})
            ok(f"{col}: {result.deleted_count} documents deleted")
        except Exception as e:
            err(f"{col}: {e}")

    client.close()


# ── Qdrant ────────────────────────────────────────────────────────────────────

async def reset_qdrant() -> None:
    section("Qdrant")
    try:
        import httpx  # type: ignore
    except ImportError:
        skip("httpx not installed — skipping Qdrant reset")
        return

    async with httpx.AsyncClient(base_url=QDRANT_URL, timeout=10) as client:
        # List existing collections first so we don't error on missing ones
        try:
            resp = await client.get("/collections")
            resp.raise_for_status()
            existing = {c["name"] for c in resp.json().get("result", {}).get("collections", [])}
        except Exception as e:
            err(f"Cannot list collections: {e}")
            return

        for col in QDRANT_COLLECTIONS:
            if col not in existing:
                skip(f"collection '{col}' does not exist — skipping")
                continue
            try:
                resp = await client.post(
                    f"/collections/{col}/points/delete",
                    json={"filter": {}},  # empty filter = match all
                )
                resp.raise_for_status()
                data = resp.json()
                status = data.get("result", {}).get("status", "?")
                ok(f"collection '{col}': all points deleted (status={status})")
            except Exception as e:
                err(f"collection '{col}': {e}")


# ── Neo4j ─────────────────────────────────────────────────────────────────────

async def reset_neo4j() -> None:
    section("Neo4j")
    try:
        from neo4j import AsyncGraphDatabase  # type: ignore
    except ImportError:
        skip("neo4j driver not installed — skipping Neo4j reset")
        return

    try:
        driver = AsyncGraphDatabase.driver(NEO4J_URL, auth=None)
        await driver.verify_connectivity()
    except Exception as e:
        err(f"Cannot connect: {e}")
        return

    try:
        async with driver.session() as session:
            # Count first so we can report
            count_res = await session.run("MATCH (n:Entity) RETURN count(n) AS c")
            row = await count_res.single()
            node_count = row["c"] if row else 0

            rel_res = await session.run("MATCH ()-[r:RELATES]->() RETURN count(r) AS c")
            row = await rel_res.single()
            rel_count = row["c"] if row else 0

            await session.run("MATCH (n:Entity) DETACH DELETE n")
            ok(f"Deleted {node_count} Entity nodes and {rel_count} RELATES relationships")
    except Exception as e:
        err(f"Cypher delete failed: {e}")
    finally:
        await driver.close()


# ── MinIO ─────────────────────────────────────────────────────────────────────

async def reset_minio() -> None:
    section("MinIO")
    try:
        from minio import Minio  # type: ignore
        from minio.error import S3Error  # type: ignore
    except ImportError:
        skip("minio package not installed — skipping MinIO reset")
        return

    try:
        mc = Minio(MINIO_URL, access_key=MINIO_USER, secret_key=MINIO_PASS, secure=False)
        if not mc.bucket_exists(MINIO_BUCKET):
            skip(f"bucket '{MINIO_BUCKET}' does not exist")
            return
    except Exception as e:
        err(f"Cannot connect: {e}")
        return

    try:
        objects = list(mc.list_objects(MINIO_BUCKET, recursive=True))
        if not objects:
            ok(f"bucket '{MINIO_BUCKET}' is already empty")
            return

        errors = list(mc.remove_objects(
            MINIO_BUCKET,
            [__import__("minio.deleteobjects", fromlist=["DeleteObject"]).DeleteObject(o.object_name) for o in objects],
        ))
        if errors:
            for e in errors:
                err(f"  {e.object_name}: {e}")
        else:
            ok(f"bucket '{MINIO_BUCKET}': {len(objects)} objects deleted")
    except Exception as e:
        # Fallback: delete one by one
        deleted = 0
        failed  = 0
        for obj in mc.list_objects(MINIO_BUCKET, recursive=True):
            try:
                mc.remove_object(MINIO_BUCKET, obj.object_name)
                deleted += 1
            except Exception:
                failed += 1
        ok(f"bucket '{MINIO_BUCKET}': {deleted} deleted, {failed} failed")


# ── Entry point ───────────────────────────────────────────────────────────────

async def main() -> None:
    print(_c("1;33", "\n=============================================="))
    print(_c("1;33",   "   DEV PIPELINE DATA RESET"))
    print(_c("1;33",   "=============================================="))

    await reset_postgres()
    await reset_mongo()
    await reset_qdrant()
    await reset_neo4j()
    await reset_minio()

    print()
    print(_c("1;32", "Done. All pipeline data cleared — ready for a fresh test run."))
    print()


if __name__ == "__main__":
    asyncio.run(main())
