#!/usr/bin/env python3
"""
migrate_kb_schema.py — migrate EXISTING stored data to the new KB storage schema
IN PLACE, without re-embedding or re-promoting anything.

What it changes
---------------
Postgres
  • KBData.metadata['intents']            ← document-level intents (read from the
                                            existing doc-summary Qdrant point).
  • KBTextBlockVersion.payload.entities   ← backfilled from the chunk Qdrant point
                                            (same canonical-name list).
  • KBTextBlockVersion.payload.intents    ← REMOVED (intents are document-level now).

Qdrant
  • knowledge (chunks)        — drop payload key  `intents`  (keep entities + ent_*).
  • knowledge_documents (docs)— drop payload keys `name`, `entities`, every `ent_*`.
  • entity_registry           — backfill `embedding_model_id` (env EMBED_MODEL_ID)
                                and `knowledge_base` ("") on points missing them.
  • query_cache               — unchanged (already conforms).

Neo4j
  • :Entity   n.name → n.canonical_name (display_name backfilled from name first),
              drop n.data_ids and n.aliases.

Vectors are never touched — no embedding, no re-promotion. Idempotent: safe to
re-run (already-migrated rows are left as-is).

Usage
-----
    # from repo root, with the docker stack running (or against localhost):
    python services/kb_backend/migrate_kb_schema.py

    # override connection strings / model id if needed:
    POSTGRES_URL=postgresql://aeroflow@localhost:5432/aeroflow_kb \
    QDRANT_URL=http://localhost:6333 NEO4J_URL=bolt://localhost:7687 \
    EMBED_MODEL_ID=<uuid> python services/kb_backend/migrate_kb_schema.py

    # preview only, change nothing:
    python services/kb_backend/migrate_kb_schema.py --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import Counter

# Windows consoles default to cp1252 and choke on the arrows / em-dashes below.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# ── Connection defaults (match docker-compose.kb-ui-testing.yml) ─────────────
POSTGRES_URL   = os.getenv("POSTGRES_URL",  "postgresql://aeroflow@localhost:5432/aeroflow_kb")
QDRANT_URL     = os.getenv("QDRANT_URL",    "http://localhost:6333")
NEO4J_URL      = os.getenv("NEO4J_URL",     "bolt://localhost:7687")
EMBED_MODEL_ID = os.getenv("EMBED_MODEL_ID", "")  # for entity_registry backfill

CHUNK_COLLECTION  = os.getenv("QDRANT_COLLECTION",     "knowledge")
DOC_COLLECTION    = os.getenv("QDRANT_DOC_COLLECTION", "knowledge_documents")
ENTITY_COLLECTION = os.getenv("ENTITY_COLLECTION",     "entity_registry")

# Unscoped data (no knowledge_base) is labelled with this default bucket so it is
# never blank. Matches the ingestion default (gold._get_data_row).
DEFAULT_KB = os.getenv("DEFAULT_KNOWLEDGE_BASE", "General Knowledge Base")

# Closed entity-type vocabulary → the per-type chunk/doc payload fields.
ENT_TYPE_FIELDS = [
    "ent_person", "ent_organization", "ent_location", "ent_law_document",
    "ent_system_product", "ent_event", "ent_time_period", "ent_concept",
]

DRY_RUN = False

# ── Colour helpers ────────────────────────────────────────────────────────────
_USE_COLOUR = sys.stdout.isatty()

def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _USE_COLOUR else text

def ok(msg: str)   -> None: print(_c("32", "  [OK]  ") + msg)
def skip(msg: str) -> None: print(_c("33", " [SKIP] ") + msg)
def err(msg: str)  -> None: print(_c("31", " [ERR]  ") + msg)
def info(msg: str) -> None: print(_c("36", " [INFO] ") + msg)
def section(title: str) -> None:
    print()
    print(_c("1;36", f"-- {title} ") + _c("36", "-" * max(1, 50 - len(title))))


# ── Qdrant REST helpers ─────────────────────────────────────────────────────
async def _qdrant_collection_exists(client, name: str) -> bool:
    r = await client.get("/collections")
    r.raise_for_status()
    names = {c["name"] for c in r.json().get("result", {}).get("collections", [])}
    return name in names


async def _qdrant_scroll(client, collection: str):
    """Yield every point {id, payload} of a collection (paged, vectors excluded)."""
    offset = None
    while True:
        body = {"limit": 256, "with_payload": True, "with_vector": False}
        if offset is not None:
            body["offset"] = offset
        r = await client.post(f"/collections/{collection}/points/scroll", json=body)
        r.raise_for_status()
        result = r.json().get("result", {})
        for pt in result.get("points", []):
            yield pt
        offset = result.get("next_page_offset")
        if offset is None:
            break


async def _qdrant_delete_keys(client, collection: str, keys: list[str]) -> None:
    """Remove payload keys from every point in the collection."""
    if DRY_RUN:
        info(f"[dry-run] would delete keys {keys} from all points of '{collection}'")
        return
    r = await client.post(
        f"/collections/{collection}/points/payload/delete?wait=true",
        json={"keys": keys, "filter": {}},  # empty filter = all points
    )
    r.raise_for_status()


async def _qdrant_set_if_empty(client, collection: str, key: str, value) -> None:
    """Set payload key=value only on points where the key is currently empty."""
    if DRY_RUN:
        info(f"[dry-run] would set {key}={value!r} on '{collection}' points missing it")
        return
    r = await client.post(
        f"/collections/{collection}/points/payload?wait=true",
        json={"payload": {key: value},
              "filter": {"must": [{"is_empty": {"key": key}}]}},
    )
    r.raise_for_status()


async def _qdrant_backfill_kb(client, collection: str, value: str) -> None:
    """Set knowledge_base=value only on points where it is missing OR an empty
    string — real (UUID) knowledge_base values are left untouched."""
    if DRY_RUN:
        info(f"[dry-run] would set knowledge_base={value!r} on '{collection}' unscoped points")
        return
    r = await client.post(
        f"/collections/{collection}/points/payload?wait=true",
        json={"payload": {"knowledge_base": value},
              "filter": {"should": [
                  {"is_empty": {"key": "knowledge_base"}},
                  {"key": "knowledge_base", "match": {"value": ""}},
              ]}},
    )
    r.raise_for_status()


# ── Step 1: read the new source-of-truth maps from Qdrant ────────────────────
async def _read_qdrant_maps(client) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Returns (block_id -> entities) from the chunk collection and
    (data_id -> intents) from the doc collection — used to backfill Postgres
    BEFORE we strip those keys from Qdrant."""
    block_entities: dict[str, list[str]] = {}
    doc_intents: dict[str, list[str]] = {}

    if await _qdrant_collection_exists(client, CHUNK_COLLECTION):
        n = 0
        async for pt in _qdrant_scroll(client, CHUNK_COLLECTION):
            payload = pt.get("payload") or {}
            ents = payload.get("entities")
            if isinstance(ents, list):
                block_entities[str(pt["id"])] = [str(e) for e in ents]
            n += 1
        info(f"chunk collection '{CHUNK_COLLECTION}': scanned {n} points")
    else:
        skip(f"chunk collection '{CHUNK_COLLECTION}' missing")

    if await _qdrant_collection_exists(client, DOC_COLLECTION):
        n = 0
        async for pt in _qdrant_scroll(client, DOC_COLLECTION):
            payload = pt.get("payload") or {}
            data_id = str(payload.get("data_id") or pt["id"])
            ints = payload.get("intents")
            if isinstance(ints, list):
                doc_intents[data_id] = [str(i) for i in ints]
            n += 1
        info(f"doc collection '{DOC_COLLECTION}': scanned {n} points")
    else:
        skip(f"doc collection '{DOC_COLLECTION}' missing")

    return block_entities, doc_intents


# ── Step 2: Postgres backfill ────────────────────────────────────────────────
async def migrate_postgres(block_entities: dict[str, list[str]],
                           doc_intents: dict[str, list[str]]) -> None:
    section("Postgres")
    try:
        import asyncpg  # type: ignore
    except ImportError:
        skip("asyncpg not installed — skipping Postgres")
        return
    try:
        conn = await asyncpg.connect(POSTGRES_URL)
    except Exception as e:
        err(f"Cannot connect: {e}")
        return

    try:
        # Active versions joined to their document.
        rows = await conn.fetch(
            'SELECT v.version_id, v.block_id, v.payload, b.owner_id '
            'FROM "KBTextBlockVersion" v '
            'JOIN "KBTextBlock" b ON b.block_id = v.block_id '
            'WHERE v.is_active = true AND v.is_deleted = false'
        )

        ver_updates: list[tuple] = []          # (entities_json, version_id)
        intents_by_doc: dict[str, list[str]] = {}
        for r in rows:
            block_id = str(r["block_id"])
            data_id  = str(r["owner_id"])
            payload  = r["payload"]
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except Exception:
                    payload = {}
            payload = payload or {}

            # entities: prefer the Qdrant chunk point's list, else whatever the
            # version payload already carries.
            ents = block_entities.get(block_id, payload.get("entities") or [])
            ver_updates.append((json.dumps([str(e) for e in ents]), r["version_id"]))

            # Collect any per-chunk intents still on the version payload so a
            # document with no doc-point still gets its intents at doc level.
            pi = payload.get("intents")
            if isinstance(pi, list) and pi:
                intents_by_doc.setdefault(data_id, []).extend(str(x) for x in pi)

        # version payload: set entities (from Qdrant), drop intents.
        if ver_updates and not DRY_RUN:
            await conn.executemany(
                'UPDATE "KBTextBlockVersion" '
                "SET payload = (coalesce(payload, '{}'::jsonb) - 'intents') "
                "             || jsonb_build_object('entities', $1::jsonb) "
                'WHERE version_id = $2',
                ver_updates,
            )
        ok(f"{'[dry-run] ' if DRY_RUN else ''}version payloads updated: {len(ver_updates)} "
           f"(entities backfilled, intents removed)")

        # document intents: prefer the doc-point list; fall back to the union of
        # per-chunk intents we just collected. Top 5 by frequency.
        doc_ids = set(doc_intents) | set(intents_by_doc)
        updated_docs = 0
        for data_id in doc_ids:
            if doc_intents.get(data_id):
                intents = doc_intents[data_id]
            else:
                intents = [i for i, _ in Counter(intents_by_doc.get(data_id, [])).most_common(5)]
            if not intents:
                continue
            if DRY_RUN:
                updated_docs += 1
                continue
            res = await conn.execute(
                'UPDATE "KBData" '
                "SET metadata = coalesce(metadata, '{}'::jsonb) "
                "             || jsonb_build_object('intents', $1::jsonb) "
                'WHERE data_id = $2',
                json.dumps(intents), data_id,
            )
            if res.endswith("1"):
                updated_docs += 1
        ok(f"{'[dry-run] ' if DRY_RUN else ''}KBData.metadata.intents set on {updated_docs} document(s)")
    except Exception as e:
        err(f"Postgres migration failed: {e}")
    finally:
        await conn.close()


# ── Step 3: Qdrant payload cleanup ───────────────────────────────────────────
async def migrate_qdrant() -> None:
    section("Qdrant")
    try:
        import httpx  # type: ignore
    except ImportError:
        skip("httpx not installed — skipping Qdrant")
        return

    async with httpx.AsyncClient(base_url=QDRANT_URL, timeout=30) as client:
        # chunk collection — drop per-chunk intents (entities + ent_* stay);
        # label unscoped points with the default knowledge_base.
        if await _qdrant_collection_exists(client, CHUNK_COLLECTION):
            await _qdrant_delete_keys(client, CHUNK_COLLECTION, ["intents"])
            await _qdrant_backfill_kb(client, CHUNK_COLLECTION, DEFAULT_KB)
            ok(f"'{CHUNK_COLLECTION}': removed 'intents'; knowledge_base→{DEFAULT_KB!r} where unscoped")
        else:
            skip(f"'{CHUNK_COLLECTION}' missing")

        # doc collection — drop name, entities, every ent_*, and the legacy
        # `enriched` flag (not in the spec); ensure embedding_model_id exists;
        # label unscoped points with the default knowledge_base.
        if await _qdrant_collection_exists(client, DOC_COLLECTION):
            await _qdrant_delete_keys(client, DOC_COLLECTION, ["name", "entities", "enriched", *ENT_TYPE_FIELDS])
            await _qdrant_set_if_empty(client, DOC_COLLECTION, "embedding_model_id", EMBED_MODEL_ID or "")
            await _qdrant_backfill_kb(client, DOC_COLLECTION, DEFAULT_KB)
            ok(f"'{DOC_COLLECTION}': removed name/entities/ent_*/enriched; "
               f"embedding_model_id ensured; knowledge_base→{DEFAULT_KB!r} where unscoped")
        else:
            skip(f"'{DOC_COLLECTION}' missing")

        # entity_registry — ensure embedding_model_id key exists (default "" to
        # match the chunk points) + backfill knowledge_base.
        if await _qdrant_collection_exists(client, ENTITY_COLLECTION):
            await _qdrant_set_if_empty(client, ENTITY_COLLECTION, "embedding_model_id", EMBED_MODEL_ID or "")
            ok(f"'{ENTITY_COLLECTION}': embedding_model_id={(EMBED_MODEL_ID or '')!r} where missing")
            await _qdrant_backfill_kb(client, ENTITY_COLLECTION, DEFAULT_KB)
            ok(f"'{ENTITY_COLLECTION}': knowledge_base→{DEFAULT_KB!r} where unscoped")
        else:
            skip(f"'{ENTITY_COLLECTION}' missing")


# ── Step 4: Neo4j rename + property cleanup ──────────────────────────────────
async def migrate_neo4j() -> None:
    section("Neo4j")
    try:
        from neo4j import AsyncGraphDatabase  # type: ignore
    except ImportError:
        skip("neo4j driver not installed — skipping Neo4j")
        return
    try:
        driver = AsyncGraphDatabase.driver(NEO4J_URL, auth=None)
        await driver.verify_connectivity()
    except Exception as e:
        err(f"Cannot connect: {e}")
        return

    # display_name first (needs n.name), then rename to canonical_name, then prune,
    # then label unscoped nodes/relationships with the default knowledge_base.
    statements = [
        ("backfill display_name from name",
         "MATCH (n:Entity) WHERE n.name IS NOT NULL AND n.display_name IS NULL "
         "SET n.display_name = n.name"),
        ("rename name -> canonical_name",
         "MATCH (n:Entity) WHERE n.name IS NOT NULL "
         "SET n.canonical_name = coalesce(n.canonical_name, n.name) "
         "REMOVE n.name"),
        ("drop data_ids",
         "MATCH (n:Entity) WHERE n.data_ids IS NOT NULL REMOVE n.data_ids"),
        ("drop aliases",
         "MATCH (n:Entity) WHERE n.aliases IS NOT NULL REMOVE n.aliases"),
        ("default knowledge_base on nodes",
         "MATCH (n:Entity) WHERE n.knowledge_base IS NULL OR n.knowledge_base = '' "
         "SET n.knowledge_base = $kb"),
        ("default knowledge_base on relationships",
         "MATCH ()-[r:RELATES]->() WHERE r.knowledge_base IS NULL OR r.knowledge_base = '' "
         "SET r.knowledge_base = $kb"),
    ]
    try:
        async with driver.session() as session:
            for label, cypher in statements:
                if DRY_RUN:
                    info(f"[dry-run] would run: {label}")
                    continue
                res = await session.run(cypher, kb=DEFAULT_KB)
                summary = await res.consume()
                props = summary.counters.properties_set
                ok(f"{label} (properties_set={props})")
    except Exception as e:
        err(f"Neo4j migration failed: {e}")
    finally:
        await driver.close()


# ── Main ─────────────────────────────────────────────────────────────────────
async def main() -> None:
    print(_c("1;35", "KB schema migration — in place, no re-embed / no re-promote"))
    if DRY_RUN:
        print(_c("33", "  (dry-run: nothing will be written)"))

    # Postgres backfill reads entities/intents from Qdrant BEFORE Qdrant strips
    # those keys, so this ordering matters.
    try:
        import httpx  # type: ignore
        async with httpx.AsyncClient(base_url=QDRANT_URL, timeout=30) as client:
            section("Reading current Qdrant payloads")
            block_entities, doc_intents = await _read_qdrant_maps(client)
    except ImportError:
        skip("httpx not installed — Postgres backfill will use version payloads only")
        block_entities, doc_intents = {}, {}
    except Exception as e:
        err(f"Could not read Qdrant maps: {e}")
        block_entities, doc_intents = {}, {}

    await migrate_postgres(block_entities, doc_intents)
    await migrate_qdrant()
    await migrate_neo4j()

    print()
    print(_c("1;32", "Done."))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="preview only, change nothing")
    args = ap.parse_args()
    DRY_RUN = args.dry_run
    asyncio.run(main())
