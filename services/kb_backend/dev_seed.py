"""
Dev/test seed data — inserts fixed-UUID rows for Tenants, RolePermissions,
Qdrant/Neo4j connections, and KB policies so every service works out of the box
without needing external connections seeded manually.

All UUIDs are stable and deterministic — any environment running this seed
produces the same IDs, making cross-service dev configs predictable.
"""
from __future__ import annotations

import logging
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

log = logging.getLogger(__name__)

# Matches the `tenant_id` attribute on the seeded `aeroflow` realm users so a
# Kong-authenticated UI session resolves to the tenant that owns this seed data.
DEV_TENANT_ID = uuid.UUID("a0000000-0000-0000-0000-000000000001")

DEV_ROLE_PERMISSION_IDS: dict[str, uuid.UUID] = {
    "PLATFORM_ADMIN":    uuid.UUID("00000000-0000-0000-0000-000000000010"),
    "AI_ENGINEER":       uuid.UUID("00000000-0000-0000-0000-000000000011"),
    "BUSINESS_OPERATOR": uuid.UUID("00000000-0000-0000-0000-000000000012"),
    "EXECUTIVE":         uuid.UUID("00000000-0000-0000-0000-000000000013"),
}

# Embedding model IDs defined in init.sql
_BGE_LARGE_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")

# ── Qdrant ────────────────────────────────────────────────────────────────────
DEV_QDRANT_CONN_ID = uuid.UUID("00000000-0000-0001-0000-000000000001")
DEV_QDRANT_COL_IDS = [
    uuid.UUID("00000000-0000-0001-0000-000000000002"),  # active  production collection
    uuid.UUID("00000000-0000-0001-0000-000000000003"),  # inactive archive collection
]

# ── Neo4j ─────────────────────────────────────────────────────────────────────
DEV_NEO4J_CONN_ID = uuid.UUID("00000000-0000-0002-0000-000000000001")

_ORG = uuid.UUID("00000000-0000-0002-0000-000000000002")
_PER = uuid.UUID("00000000-0000-0002-0000-000000000003")
_POL = uuid.UUID("00000000-0000-0002-0000-000000000004")
_CON = uuid.UUID("00000000-0000-0002-0000-000000000005")
_LOC = uuid.UUID("00000000-0000-0002-0000-000000000006")
_REG = uuid.UUID("00000000-0000-0002-0000-000000000007")
_DEP = uuid.UUID("00000000-0000-0002-0000-000000000008")
_AGR = uuid.UUID("00000000-0000-0002-0000-000000000009")

_NEO4J_NODES = [
    (_ORG, "Organization", "Top-level enterprise entity"),
    (_PER, "Person",       "Individual employee or contact"),
    (_POL, "Policy",       "Corporate governance policy"),
    (_CON, "Contract",     "Legal agreement document"),
    (_LOC, "Location",     "Geographic site or office"),
    (_REG, "Regulation",   "Regulatory compliance framework"),
    (_DEP, "Department",   "Organizational business unit"),
    (_AGR, "Agreement",    "Bilateral commercial agreement"),
]

_NEO4J_RELS = [
    (_ORG, _PER, 0.90, "[WORKS_FOR]"),
    (_ORG, _POL, 0.85, "[GOVERNED_BY]"),
    (_ORG, _CON, 0.80, "[PART_OF]"),
    (_PER, _LOC, 0.75, "[LOCATED_IN]"),
    (_POL, _REG, 0.90, "[COMPLIES_WITH]"),
]

# ── Knowledge Base / Policies ─────────────────────────────────────────────────
DEV_KB_ID = uuid.UUID("00000000-0000-0003-0000-000000000001")

DEV_FILTER_POLICY_IDS = [
    uuid.UUID("00000000-0000-0004-0000-000000000001"),
    uuid.UUID("00000000-0000-0004-0000-000000000002"),
]
DEV_EXTRACTION_POLICY_ID = uuid.UUID("00000000-0000-0005-0000-000000000001")


async def seed_dev_data(engine: AsyncEngine) -> None:
    """Idempotent: inserts rows only if they don't already exist."""
    async with engine.begin() as conn:

        # ── Tenants ───────────────────────────────────────────────────────────
        await conn.execute(text("""
            INSERT INTO "Tenants" (id, name, slug, data_residency, is_active)
            VALUES (:id, :name, :slug, :dr, true)
            ON CONFLICT (id) DO NOTHING
        """), {"id": DEV_TENANT_ID, "name": "Dev Tenant", "slug": "dev", "dr": "Asia-SE1"})

        # ── RolePermissions ───────────────────────────────────────────────────
        for role_name, perm_id in DEV_ROLE_PERMISSION_IDS.items():
            await conn.execute(text("""
                INSERT INTO "RolePermissions" (id, role_id, resource, action)
                VALUES (:id, :role_id, :resource, :action)
                ON CONFLICT (id) DO NOTHING
            """), {"id": perm_id, "role_id": role_name, "resource": "kb_data", "action": "write"})

        # ── KBKnowledgeBase ───────────────────────────────────────────────────
        await conn.execute(text("""
            INSERT INTO "KBKnowledgeBase" (knowledge_base_id, tenant_id, name, description, is_active, created_by)
            VALUES (:kb_id, :tenant_id, 'General Knowledge Base', 'Default knowledge base for dev tenant', true, 'system')
            ON CONFLICT (knowledge_base_id) DO NOTHING
        """), {"kb_id": DEV_KB_ID, "tenant_id": DEV_TENANT_ID})

        # ── KBFilterPolicy ────────────────────────────────────────────────────
        filter_policies = [
            (DEV_FILTER_POLICY_IDS[0], "PII Filter",    "natural_language",
             '{"keywords": ["SSN", "passport", "credit card"]}', True),
            (DEV_FILTER_POLICY_IDS[1], "Legal Stopwords", "exact_word",
             '{"keywords": ["confidential", "restricted", "draft"]}', False),
        ]
        for pid, name, fmt, cfg, active in filter_policies:
            await conn.execute(text("""
                INSERT INTO "KBFilterPolicy"
                    (policy_id, tenant_id, policy_name, configformat, config, is_active, language)
                VALUES (:id, :tid, :name, :fmt, CAST(:cfg AS jsonb), :active, 'english')
                ON CONFLICT (policy_id) DO NOTHING
            """), {"id": pid, "tid": DEV_TENANT_ID, "name": name,
                   "fmt": fmt, "cfg": cfg, "active": active})

        # ── KBExtractionPolicy ────────────────────────────────────────────────
        await conn.execute(text("""
            INSERT INTO "KBExtractionPolicy"
                (policy_id, tenant_id, policy_name, policy_type, language)
            VALUES (:id, :tid, 'Default Extraction', 'entity_node', 'english')
            ON CONFLICT (policy_id) DO NOTHING
        """), {"id": DEV_EXTRACTION_POLICY_ID, "tid": DEV_TENANT_ID})

        # ── KBModel (embedding models — FK target for Qdrant/Neo4j) ─────────────
        for model_id, model_name, task_type in [
            (_BGE_LARGE_ID,                              "BAAI/bge-large-en-v1.5", "embedding"),
            (uuid.UUID("00000000-0000-0000-0000-000000000002"), "BAAI/bge-small-en-v1.5", "embedding"),
        ]:
            await conn.execute(text("""
                INSERT INTO "KBModel" (model_id, model_name, task_type)
                VALUES (:model_id, :name, :task_type)
                ON CONFLICT (model_id) DO NOTHING
            """), {"model_id": model_id, "name": model_name, "task_type": task_type})

        # ── KBQdrantConnection ────────────────────────────────────────────────
        await conn.execute(text("""
            INSERT INTO "KBQdrantConnection"
                (connection_id, tenant_id, is_active, total_collection)
            VALUES (:cid, :tid, true, 2)
            ON CONFLICT (connection_id) DO NOTHING
        """), {"cid": DEV_QDRANT_CONN_ID, "tid": DEV_TENANT_ID})

        # ── KBQdrantCollection ────────────────────────────────────────────────
        qdrant_collections = [
            (DEV_QDRANT_COL_IDS[0], "aeroflow_kb_prod",    True,  "cosine", 0, 1024),
            (DEV_QDRANT_COL_IDS[1], "aeroflow_kb_archive", False, "cosine", 0, 1024),
        ]
        for col_id, col_name, active, metric, points, dims in qdrant_collections:
            await conn.execute(text("""
                INSERT INTO "KBQdrantCollection"
                    (collection_id, connection_id, collection_name, is_active,
                     similarity_metric, points_count, vector_dimension, embedding_model_id)
                VALUES (:cid, :conn_id, :name, :active, :metric, :points, :dims, :model_id)
                ON CONFLICT (collection_id) DO NOTHING
            """), {
                "cid": col_id, "conn_id": DEV_QDRANT_CONN_ID, "name": col_name,
                "active": active, "metric": metric, "points": points,
                "dims": dims, "model_id": _BGE_LARGE_ID,
            })

        # ── KBNeo4jConnection ─────────────────────────────────────────────────
        await conn.execute(text("""
            INSERT INTO "KBNeo4jConnection"
                (connection_id, tenant_id, is_connected, total_node, total_edge, embedding_model_id)
            VALUES (:cid, :tid, true, :nodes, :edges, :model_id)
            ON CONFLICT (connection_id) DO NOTHING
        """), {
            "cid": DEV_NEO4J_CONN_ID, "tid": DEV_TENANT_ID,
            "nodes": len(_NEO4J_NODES), "edges": len(_NEO4J_RELS),
            "model_id": _BGE_LARGE_ID,
        })

        # ── KBNeo4jNode ───────────────────────────────────────────────────────
        for node_id, node_name, node_desc in _NEO4J_NODES:
            await conn.execute(text("""
                INSERT INTO "KBNeo4jNode"
                    (node_id, connection_id, node_name, node_description)
                VALUES (:nid, :conn_id, :name, :desc)
                ON CONFLICT (node_id) DO NOTHING
            """), {"nid": node_id, "conn_id": DEV_NEO4J_CONN_ID,
                   "name": node_name, "desc": node_desc})

        # ── KBNeo4jRelationship ───────────────────────────────────────────────
        for from_id, to_id, score, desc in _NEO4J_RELS:
            await conn.execute(text("""
                INSERT INTO "KBNeo4jRelationship"
                    (from_node, to_node, score, description)
                VALUES (:from_id, :to_id, :score, :desc)
                ON CONFLICT (from_node, to_node) DO NOTHING
            """), {"from_id": from_id, "to_id": to_id, "score": score, "desc": desc})

    log.info("dev_seed: all seed tables populated OK")
