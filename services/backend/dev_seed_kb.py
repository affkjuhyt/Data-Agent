#!/usr/bin/env python3
"""
dev_seed_kb.py — seed a small, fully-consistent knowledge base for end-to-end testing
of the LightRAG retrieval pipeline. The mirror image of dev_reset.py.

What it creates (all scoped to the dev tenant + one knowledge base)
------------------------------------------------------------------
Postgres   KBKnowledgeBase (1) · KBData (gold docs) · KBTextBlock + KBTextBlockVersion
           (active chunks) · KBTextTable (for the table chunk)
Qdrant     'knowledge' collection points — vector = embed(summary), payload carries
           tenant_id, data_id, block_id, summary, entities, intents,
           embedding_model_id, knowledge_base, is_deleted
Neo4j      :Entity nodes keyed by (name, tenant_id, knowledge_base) with a stored
           vector = embed(description), plus :RELATES edges between them

Embeddings come from the model-service so the vectors are real and queryable.
Run dev_reset.py first for a clean slate.

Usage (inside the kb-backend container, service-name hosts):
    docker exec \
      -e POSTGRES_URL=postgresql+asyncpg://aeroflow@postgres/aeroflow_kb \
      -e QDRANT_URL=http://qdrant:6333 -e NEO4J_URL=bolt://neo4j:7687 \
      -e MODEL_SERVICE_URL=http://model-service:8001 \
      kb-test-backend python services/backend/dev_seed_kb.py
"""
from __future__ import annotations

import asyncio
import datetime
import os
import re
import sys
import uuid

# ── Connection defaults (match docker-compose.kb-ui-testing.yml) ─────────────
POSTGRES_URL      = os.getenv("POSTGRES_URL",      "postgresql+asyncpg://aeroflow@localhost:5432/aeroflow_kb")
QDRANT_URL        = os.getenv("QDRANT_URL",        "http://localhost:6333")
NEO4J_URL         = os.getenv("NEO4J_URL",         "bolt://localhost:7687")
MODEL_SERVICE_URL = os.getenv("MODEL_SERVICE_URL", "http://localhost:8001")
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "knowledge")

# Dev identities seeded by services/backend/dev_seed.py (seed_dev_data).
DEV_TENANT_ID = "00000000-0000-0000-0000-000000000001"
DEV_ROLE_ID   = "00000000-0000-0000-0000-000000000011"  # AI_ENGINEER
EMBED_MODEL_ID = os.getenv("EMBED_MODEL_ID", "1")        # placeholder tag (matches gold)

# ── Colour helpers ────────────────────────────────────────────────────────────
_USE_COLOUR = sys.stdout.isatty()
def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _USE_COLOUR else text
def ok(msg: str)   -> None: print(_c("32", "  [OK]  ") + msg)
def err(msg: str)  -> None: print(_c("31", " [ERR]  ") + msg)
def section(title: str) -> None:
    print(); print(_c("1;36", f"-- {title} ") + _c("36", "-" * max(1, 50 - len(title))))


def canon(name: str) -> str:
    """Lowercase + underscore canonical form, matching the ingest entity registry."""
    return re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")


# ── Seed dataset ────────────────────────────────────────────────────────────────
KB_NAME = "Machine Learning Research"

# Each chunk: content, summary, entities[{name,description}], relationships[[a,rel,b]],
# intents[], and an optional table {table_name, description, data:{headers, rows}}.
DOCS = [
    {
        "name": "Retrieval-Augmented Generation.md",
        "chunks": [
            {
                "content": ("RAG combines BART with DPR to retrieve passages from a Wikipedia dense index. "
                            "The system is fine-tuned end-to-end on Natural Questions and TriviaQA."),
                "summary": "RAG jointly fine-tunes BART and DPR on Wikipedia retrieval for open-domain QA.",
                "entities": [
                    {"name": "RAG",  "description": "Retrieval-augmented generation model combining retrieval and generation."},
                    {"name": "BART", "description": "Pre-trained seq2seq model used as RAG's generator."},
                    {"name": "DPR",  "description": "Dense Passage Retriever querying Wikipedia passages."},
                    {"name": "Wikipedia", "description": "Dense vector index used as the non-parametric knowledge source."},
                ],
                "relationships": [["RAG", "USES", "BART"], ["RAG", "USES", "DPR"], ["DPR", "RETRIEVES_FROM", "Wikipedia"]],
                "intents": ["information_retrieval", "question_answering"],
                "table": None,
            },
        ],
    },
    {
        "name": "Dense Retrieval.md",
        "chunks": [
            {
                "content": ("ColBERT introduces late interaction over BERT embeddings for efficient passage ranking. "
                            "It is evaluated on the MS MARCO benchmark against BM25 and DPR, and uses FAISS for ANN search."),
                "summary": "ColBERT applies late interaction over BERT for passage ranking, evaluated on MS MARCO.",
                "entities": [
                    {"name": "ColBERT", "description": "Late-interaction retriever over BERT token embeddings."},
                    {"name": "BERT",    "description": "Transformer encoder producing the token embeddings ColBERT ranks with."},
                    {"name": "MS MARCO","description": "Passage ranking benchmark used to evaluate retrievers."},
                    {"name": "FAISS",   "description": "Approximate nearest-neighbour index ColBERT queries at scale."},
                ],
                "relationships": [["ColBERT", "EXTENDS", "BERT"], ["ColBERT", "EVALUATED_ON", "MS MARCO"], ["ColBERT", "USES", "FAISS"]],
                "intents": ["information_retrieval", "benchmarking"],
                "table": None,
            },
        ],
    },
    {
        "name": "Transformer Benchmarks.md",
        "chunks": [
            {
                "content": "Table 'Encoder Benchmark' has 3 row(s) and 3 column(s): model, params, accuracy.",
                "summary": "Benchmark table comparing BERT, RoBERTa and DeBERTa on parameters and accuracy.",
                "entities": [
                    {"name": "BERT",    "description": "Baseline transformer encoder in the benchmark."},
                    {"name": "RoBERTa", "description": "Robustly optimised BERT variant in the benchmark."},
                    {"name": "DeBERTa", "description": "Disentangled-attention encoder leading the benchmark."},
                ],
                "relationships": [["RoBERTa", "OUTPERFORMS", "BERT"], ["DeBERTa", "OUTPERFORMS", "RoBERTa"]],
                "intents": ["benchmarking"],
                "table": {
                    "table_name": "Encoder Benchmark",
                    "description": "Encoder models compared by parameter count and GLUE accuracy.",
                    "data": {
                        "headers": ["model", "params", "accuracy"],
                        "rows": [["BERT", "110M", "84.5"], ["RoBERTa", "125M", "88.5"], ["DeBERTa", "140M", "90.1"]],
                    },
                },
            },
        ],
    },
]


async def _embed(client, texts: list[str]) -> list[list[float]]:
    resp = await client.post(f"{MODEL_SERVICE_URL}/embed", json={"texts": texts}, timeout=60)
    resp.raise_for_status()
    return resp.json()["embeddings"]


async def main() -> None:
    print(_c("1;33", "\n=============================================="))
    print(_c("1;33",   "   DEV KB SEED — LightRAG test data"))
    print(_c("1;33",   "=============================================="))

    import httpx
    from neo4j import AsyncGraphDatabase
    from sqlalchemy import text as sa_text
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

    from basemodel.services_databaseconnector.postgres_orm.base import Base
    import basemodel.services_databaseconnector.postgres_orm  # noqa: F401 — registers ORM
    from basemodel.services_databaseconnector.postgres_orm.knowledge_base_orm import (
        KBKnowledgeBaseORM, KBDataORM, KBTextBlockORM, KBTextBlockVersionORM, KBTextTableORM,
    )

    tenant_uuid = uuid.UUID(DEV_TENANT_ID)
    kb_id = uuid.uuid4()
    now = datetime.datetime.now(datetime.timezone.utc)

    # ── 1. Embeddings (one batched call) ──────────────────────────────────────
    section("Embeddings")
    flat_chunks = [(d, c) for d in DOCS for c in d["chunks"]]
    summary_texts = [c["summary"] for _, c in flat_chunks]
    # unique entities by canonical name → description (for node vectors)
    ent_desc: dict[str, str] = {}
    for _, c in flat_chunks:
        for e in c["entities"]:
            ent_desc.setdefault(canon(e["name"]), e["description"])
    ent_names = list(ent_desc.keys())
    async with httpx.AsyncClient() as http:
        try:
            vectors = await _embed(http, summary_texts + [ent_desc[n] for n in ent_names])
        except Exception as e:
            err(f"model-service embed failed: {e}"); return
    dim = len(vectors[0])
    summary_vecs = vectors[:len(summary_texts)]
    ent_vecs = {n: vectors[len(summary_texts) + i] for i, n in enumerate(ent_names)}
    ok(f"embedded {len(summary_texts)} summaries + {len(ent_names)} entities (dim={dim})")

    # ── 2. Postgres (schema ensure + rows) ────────────────────────────────────
    section("Postgres")
    engine = create_async_engine(POSTGRES_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)          # creates KBKnowledgeBase etc.
        await conn.execute(sa_text('ALTER TABLE "KBData" ADD COLUMN IF NOT EXISTS knowledge_base_id UUID'))
    ok("schema ensured (KBKnowledgeBase + KBData.knowledge_base_id)")

    qdrant_points: list[dict] = []
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as s:
        s.add(KBKnowledgeBaseORM(
            knowledge_base_id=kb_id, tenant_id=tenant_uuid, name=KB_NAME,
            description="Seeded test knowledge base for LightRAG retrieval.",
            is_active=True, created_by="seed",
        ))
        await s.flush()

        si = 0
        for doc in DOCS:
            data_id = uuid.uuid4()
            s.add(KBDataORM(
                data_id=data_id, tenant_id=tenant_uuid, role_id=uuid.UUID(DEV_ROLE_ID),
                knowledge_base_id=kb_id, name=doc["name"], extension="md", language="english",
                source_type="doc", added_by="seed", abstract=doc["name"], doc_metadata={"type": "Doc/markdown"},
                current_tier="gold", path=f"seed/{data_id}",
            ))
            await s.flush()
            for bi, c in enumerate(doc["chunks"]):
                block = KBTextBlockORM(owner_id=data_id, block_index=bi)
                s.add(block); await s.flush()
                ver = KBTextBlockVersionORM(
                    block_id=block.block_id, version_number=1, content=c["content"],
                    created_by="seed", table_involved=bool(c["table"]), is_active=True,
                    payload={"summary": c["summary"], "entities": [canon(e["name"]) for e in c["entities"]],
                             "intents": c["intents"], "embedding_model_id": EMBED_MODEL_ID,
                             "knowledge_base": str(kb_id)},
                )
                s.add(ver); await s.flush()
                if c["table"]:
                    s.add(KBTextTableORM(version_id=ver.version_id, table_name=c["table"]["table_name"],
                                         description=c["table"]["description"], data=c["table"]["data"]))
                qdrant_points.append({
                    "id": str(block.block_id),
                    "vector": summary_vecs[si],
                    "payload": {
                        "tenant_id": DEV_TENANT_ID, "data_id": str(data_id), "block_id": str(block.block_id),
                        "summary": c["summary"], "entities": [canon(e["name"]) for e in c["entities"]],
                        "intents": c["intents"], "embedding_model_id": EMBED_MODEL_ID,
                        "knowledge_base": str(kb_id), "is_deleted": False,
                    },
                })
                si += 1
        await s.commit()
    await engine.dispose()
    ok(f"inserted 1 knowledge base, {len(DOCS)} docs, {len(qdrant_points)} chunks")

    # ── 3. Qdrant ─────────────────────────────────────────────────────────────
    section("Qdrant")
    async with httpx.AsyncClient(base_url=QDRANT_URL, timeout=30) as q:
        ex = await q.get(f"/collections/{QDRANT_COLLECTION}")
        if ex.status_code == 404:
            await q.put(f"/collections/{QDRANT_COLLECTION}",
                        json={"vectors": {"size": dim, "distance": "Cosine"}})
            ok(f"created collection '{QDRANT_COLLECTION}' (dim={dim})")
        r = await q.put(f"/collections/{QDRANT_COLLECTION}/points?wait=true", json={"points": qdrant_points})
        r.raise_for_status()
        ok(f"upserted {len(qdrant_points)} points into '{QDRANT_COLLECTION}'")

    # ── 4. Neo4j ──────────────────────────────────────────────────────────────
    section("Neo4j")
    driver = AsyncGraphDatabase.driver(NEO4J_URL, auth=None)
    try:
        async with driver.session() as ses:
            for name in ent_names:
                await (await ses.run(
                    "MERGE (n:Entity {name:$name, tenant_id:$tid, knowledge_base:$kb}) "
                    "SET n.description=$desc, n.vector=$vec, n.embedding_model_id=$emid",
                    name=name, tid=DEV_TENANT_ID, kb=str(kb_id),
                    desc=ent_desc[name], vec=ent_vecs[name], emid=EMBED_MODEL_ID,
                )).consume()
            edges = 0
            for _, c in flat_chunks:
                for a, rel, b in c["relationships"]:
                    res = await ses.run(
                        "MATCH (x:Entity {name:$a, tenant_id:$tid, knowledge_base:$kb}) "
                        "MATCH (y:Entity {name:$b, tenant_id:$tid, knowledge_base:$kb}) "
                        "CREATE (x)-[:RELATES {rel_type:$rt, tenant_id:$tid, knowledge_base:$kb}]->(y)",
                        a=canon(a), b=canon(b), rt=rel, tid=DEV_TENANT_ID, kb=str(kb_id),
                    )
                    await res.consume(); edges += 1
        ok(f"merged {len(ent_names)} entities and {edges} relationships")
    finally:
        await driver.close()

    print()
    print(_c("1;32", f"Done. knowledge_base_id = {kb_id}"))
    print(_c("32",   f"Try a query scoped to it, or leave knowledge_base empty for all.\n"))


if __name__ == "__main__":
    asyncio.run(main())
