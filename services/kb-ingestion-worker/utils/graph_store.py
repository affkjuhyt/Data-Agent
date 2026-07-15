"""Single owner of the knowledge graph: live Neo4j + its Postgres mirror.

Neo4j holds one undirected bridge per entity pair (``RELATES`` with a ``count``
property) for retrieval traversal. Postgres mirrors it for the UI and is the source
of truth for provenance: ``KBNeo4jRelationship`` carries one row per (pair, chunk)
keyed by ``block_id``, so an edge exists while >=1 row references it and its count
is ``COUNT(*)`` over the pair. Node id is deterministic — ``uuid5(tenant:kb:canonical)``
— so re-processing the same entity is idempotent.

Cross-store consistency follows the atomic-commit pattern: stage every Postgres write
in one open transaction, do the Neo4j write, then commit Postgres LAST. Any failure
rolls the Postgres transaction back so nothing persists; only when Neo4j has already
committed and the final Postgres commit then fails do we reconcile Neo4j back into
agreement with Postgres.

Nodes are created only for entities that participate in an edge; an entity with no
relationships is retrieval-inert (graph_expand reaches nodes only through edges), so
its lifetime is the lifetime of its edges. Removing a chunk drops its edge rows,
recomputes counts, deletes bridges that hit zero, and prunes the now-edgeless nodes.
"""
from __future__ import annotations

import logging
import uuid

from neo4j import AsyncDriver
from sqlalchemy import delete as sa_delete, func as sa_func, select as sa_select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from libs.basemodel.services_databaseconnector.postgres_orm.knowledge_base_orm import (
    KBNeo4jNodeORM,
    KBNeo4jRelationshipORM,
)
from libs.database_connector.model_connector import ModelServiceClient
from libs.database_connector.postgres_connector import PostgresClient

from utils.entity_types import ENTITY_TYPE_LABELS, to_entity_type

log = logging.getLogger(__name__)

_NODE_NAMESPACE = uuid.UUID("9f1a6c1e-7b2d-5e4a-9c3f-2a8d4e6b1c70")


def _as_uuid(value) -> uuid.UUID | None:
    if value is None or isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        return None


def _node_id(tenant_id: str, knowledge_base: str, canonical: str) -> uuid.UUID:
    return uuid.uuid5(_NODE_NAMESPACE, f"{tenant_id}:{knowledge_base}:{canonical}")


class GraphStore:
    def __init__(self, neo4j: AsyncDriver, postgres: PostgresClient, model: ModelServiceClient) -> None:
        self._neo4j = neo4j
        self._postgres = postgres
        self._model = model

    async def add_chunk(
        self,
        *,
        tenant_id: str,
        knowledge_base: str,
        data_id: str,
        block_id: str,
        entities: list[dict],
        relationships: list[dict],
        embed_model_id: str = "",
    ) -> None:
        """Add one chunk's entity nodes + edge bridges to both stores, with the chunk
        recorded as edge provenance. Raises on failure after leaving both stores
        consistent, so the caller can record the failure in the document history."""
        entity_by_canonical = {e["canonical"]: e for e in entities if e.get("canonical")}
        entity_pairs: list[tuple[str, str]] = []
        seen_pairs: set[tuple[str, str]] = set()
        for relationship in relationships or []:
            source, target = relationship.get("from"), relationship.get("to")
            if not source or not target or source == target:
                continue
            if source not in entity_by_canonical or target not in entity_by_canonical:
                continue
            pair = (source, target) if source <= target else (target, source)
            if pair not in seen_pairs:
                seen_pairs.add(pair)
                entity_pairs.append(pair)
        if not entity_pairs:
            return

        connected_canonicals = sorted({c for pair in entity_pairs for c in pair})
        vectors_by_canonical = await self._embed_entities(
            connected_canonicals, entity_by_canonical, embed_model_id,
        )

        neo4j_committed = False
        async with self._postgres.get_client() as session:
            try:
                await self._stage_postgres(
                    session, tenant_id, knowledge_base, data_id, block_id,
                    connected_canonicals, entity_by_canonical, entity_pairs,
                )
                counts_by_pair = await self._count_pairs(session, tenant_id, knowledge_base, entity_pairs)
                await self._write_neo4j(
                    tenant_id, knowledge_base, connected_canonicals,
                    entity_by_canonical, vectors_by_canonical, embed_model_id, counts_by_pair,
                )
                neo4j_committed = True
                await session.commit()
            except Exception:
                await session.rollback()
                if neo4j_committed:
                    await self._reconcile_neo4j(tenant_id, knowledge_base, entity_pairs)
                raise

    async def remove_chunk(self, *, tenant_id: str, knowledge_base: str, block_id: str) -> None:
        await self._remove(tenant_id, knowledge_base, KBNeo4jRelationshipORM.block_id, block_id)

    async def remove_document(self, *, tenant_id: str, knowledge_base: str, data_id: str) -> None:
        await self._remove(tenant_id, knowledge_base, KBNeo4jRelationshipORM.data_id, data_id)


    async def _embed_entities(
        self, connected_canonicals, entity_by_canonical, embed_model_id,
    ) -> dict[str, list[float] | None]:
        embed_texts = [
            (entity_by_canonical[canonical].get("description")
             or entity_by_canonical[canonical].get("display")
             or canonical).replace("_", " ")
            for canonical in connected_canonicals
        ]
        try:
            vectors = await self._model.embed(embed_texts, model_id=embed_model_id)
        except Exception as exc:
            log.warning("GraphStore: entity embed failed (%s) — nodes get no vector", exc)
            return {canonical: None for canonical in connected_canonicals}
        return {canonical: vector for canonical, vector in zip(connected_canonicals, vectors)}

    async def _stage_postgres(
        self, session, tenant_id, knowledge_base, data_id, block_id,
        connected_canonicals, entity_by_canonical, entity_pairs,
    ) -> None:
        """Stage node upserts + edge-mention inserts on the caller's open session
        (flush only — the caller commits last)."""
        node_rows = [
            {
                "node_id":          _node_id(tenant_id, knowledge_base, canonical),
                "tenant_id":        _as_uuid(tenant_id),
                "knowledge_base":   knowledge_base,
                "node_name":        canonical,
                "node_description": entity_by_canonical[canonical].get("description") or None,
                "is_deleted":       False,
            }
            for canonical in connected_canonicals
        ]
        node_statement = pg_insert(KBNeo4jNodeORM).values(node_rows)
        node_statement = node_statement.on_conflict_do_update(
            index_elements=["node_id"],
            set_={
                "node_name":        node_statement.excluded.node_name,
                "node_description": node_statement.excluded.node_description,
                "knowledge_base":   node_statement.excluded.knowledge_base,
                "tenant_id":        node_statement.excluded.tenant_id,
                "is_deleted":       False,
            },
        )
        await session.execute(node_statement)
        for canonical_a, canonical_b in entity_pairs:
            edge_statement = pg_insert(KBNeo4jRelationshipORM).values(
                from_node=_node_id(tenant_id, knowledge_base, canonical_a),
                to_node=_node_id(tenant_id, knowledge_base, canonical_b),
                block_id=_as_uuid(block_id),
                data_id=_as_uuid(data_id),
                tenant_id=_as_uuid(tenant_id),
                knowledge_base=knowledge_base,
                is_deleted=False,
            ).on_conflict_do_nothing(index_elements=["from_node", "to_node", "block_id"])
            await session.execute(edge_statement)
        await session.flush()

    async def _count_pairs(self, session, tenant_id, knowledge_base, entity_pairs) -> dict[tuple[str, str], int]:
        """Live edge count per pair, visible within the caller's transaction."""
        counts_by_pair: dict[tuple[str, str], int] = {}
        for canonical_a, canonical_b in entity_pairs:
            counts_by_pair[(canonical_a, canonical_b)] = await session.scalar(
                sa_select(sa_func.count()).select_from(KBNeo4jRelationshipORM).where(
                    KBNeo4jRelationshipORM.from_node == _node_id(tenant_id, knowledge_base, canonical_a),
                    KBNeo4jRelationshipORM.to_node == _node_id(tenant_id, knowledge_base, canonical_b),
                    KBNeo4jRelationshipORM.is_deleted.is_(False),
                )
            )
        return counts_by_pair

    async def _write_neo4j(
        self, tenant_id, knowledge_base, connected_canonicals,
        entity_by_canonical, vectors_by_canonical, embed_model_id, counts_by_pair,
    ) -> None:
        """Nodes (+ secondary label) and bridges in ONE transaction — all or nothing."""
        async with self._neo4j.session() as session:
            transaction = await session.begin_transaction()
            try:
                for canonical in connected_canonicals:
                    entity = entity_by_canonical[canonical]
                    entity_type = to_entity_type(entity.get("type"))
                    merge_result = await transaction.run(
                        "MERGE (n:Entity {canonical_name: $name, tenant_id: $tenant, knowledge_base: $kb}) "
                        "SET n.display_name = coalesce(n.display_name, $display), "
                        "    n.description = CASE WHEN $description <> '' THEN $description ELSE coalesce(n.description, '') END, "
                        "    n.vector = CASE WHEN $vector IS NULL THEN n.vector ELSE $vector END, "
                        "    n.embedding_model_id = $embed_model_id, "
                        "    n.entity_type = CASE WHEN n.entity_type IS NULL OR n.entity_type = 'concept' "
                        "                         THEN $entity_type ELSE n.entity_type END "
                        "RETURN n.entity_type AS effective_type",
                        name=canonical, tenant=tenant_id, kb=knowledge_base,
                        display=entity.get("display") or canonical,
                        description=entity.get("description") or "",
                        vector=vectors_by_canonical.get(canonical),
                        embed_model_id=embed_model_id, entity_type=entity_type,
                    )
                    record = await merge_result.single()
                    effective_label = ENTITY_TYPE_LABELS[to_entity_type(record["effective_type"] if record else entity_type)]
                    removed_labels = " ".join(
                        f"REMOVE n:{label}" for label in ENTITY_TYPE_LABELS.values() if label != effective_label
                    )
                    await transaction.run(
                        f"MATCH (n:Entity {{canonical_name: $name, tenant_id: $tenant, knowledge_base: $kb}}) "
                        f"SET n:{effective_label} {removed_labels}",
                        name=canonical, tenant=tenant_id, kb=knowledge_base,
                    )
                for (canonical_a, canonical_b), support_count in counts_by_pair.items():
                    await transaction.run(
                        "MATCH (a:Entity {canonical_name: $canonical_a, tenant_id: $tenant, knowledge_base: $kb}) "
                        "MATCH (b:Entity {canonical_name: $canonical_b, tenant_id: $tenant, knowledge_base: $kb}) "
                        "MERGE (a)-[edge:RELATES]->(b) SET edge.count = $support_count",
                        canonical_a=canonical_a, canonical_b=canonical_b,
                        tenant=tenant_id, kb=knowledge_base, support_count=support_count,
                    )
                await transaction.commit()
            except Exception:
                await transaction.rollback()
                raise

    async def _reconcile_neo4j(self, tenant_id, knowledge_base, entity_pairs) -> None:
        """The Postgres commit failed after Neo4j committed. Re-derive each touched
        pair's count from Postgres (now without this chunk) and reconcile Neo4j —
        delete bridges that fell to zero, fix the rest, prune nodes left edgeless."""
        try:
            async with self._postgres.get_client() as session:
                counts_by_pair = await self._count_pairs(session, tenant_id, knowledge_base, entity_pairs)
            async with self._neo4j.session() as session:
                transaction = await session.begin_transaction()
                try:
                    for (canonical_a, canonical_b), support_count in counts_by_pair.items():
                        cypher = (
                            "MATCH (a:Entity {canonical_name: $canonical_a, tenant_id: $tenant, knowledge_base: $kb})"
                            "-[edge:RELATES]-(b:Entity {canonical_name: $canonical_b, tenant_id: $tenant, knowledge_base: $kb}) "
                        ) + ("DELETE edge" if support_count == 0 else "SET edge.count = $support_count")
                        await transaction.run(
                            cypher, canonical_a=canonical_a, canonical_b=canonical_b,
                            tenant=tenant_id, kb=knowledge_base, support_count=support_count,
                        )
                    await transaction.run(
                        "MATCH (n:Entity {tenant_id: $tenant, knowledge_base: $kb}) WHERE NOT (n)--() DELETE n",
                        tenant=tenant_id, kb=knowledge_base,
                    )
                    await transaction.commit()
                except Exception:
                    await transaction.rollback()
                    raise
            await self._prune_postgres_orphans(tenant_id, knowledge_base)
        except Exception as exc:
            log.error("GraphStore: Neo4j reconcile after Postgres commit failure failed: %s", exc)

    async def _remove(self, tenant_id, knowledge_base, column, value) -> None:
        target = _as_uuid(value)
        if target is None:
            return
        async with self._postgres.get_client() as session:
            affected_node_pairs = (await session.execute(
                sa_select(KBNeo4jRelationshipORM.from_node, KBNeo4jRelationshipORM.to_node)
                .where(column == target).distinct()
            )).all()
            if not affected_node_pairs:
                return
            await session.execute(sa_delete(KBNeo4jRelationshipORM).where(column == target))
            await session.commit()

        async with self._postgres.get_client() as session:
            affected_node_ids = {node_id for pair in affected_node_pairs for node_id in pair}
            name_by_node_id = dict((await session.execute(
                sa_select(KBNeo4jNodeORM.node_id, KBNeo4jNodeORM.node_name)
                .where(KBNeo4jNodeORM.node_id.in_(affected_node_ids))
            )).all())
            remaining_counts: list[tuple[str, str, int]] = []
            for from_node_id, to_node_id in affected_node_pairs:
                canonical_a, canonical_b = name_by_node_id.get(from_node_id), name_by_node_id.get(to_node_id)
                if not canonical_a or not canonical_b:
                    continue
                support_count = await session.scalar(
                    sa_select(sa_func.count()).select_from(KBNeo4jRelationshipORM).where(
                        KBNeo4jRelationshipORM.from_node == from_node_id,
                        KBNeo4jRelationshipORM.to_node == to_node_id,
                        KBNeo4jRelationshipORM.is_deleted.is_(False),
                    )
                )
                remaining_counts.append((canonical_a, canonical_b, support_count))

        async with self._neo4j.session() as session:
            transaction = await session.begin_transaction()
            try:
                for canonical_a, canonical_b, support_count in remaining_counts:
                    cypher = (
                        "MATCH (a:Entity {canonical_name: $canonical_a, tenant_id: $tenant, knowledge_base: $kb})"
                        "-[edge:RELATES]-(b:Entity {canonical_name: $canonical_b, tenant_id: $tenant, knowledge_base: $kb}) "
                    ) + ("DELETE edge" if support_count == 0 else "SET edge.count = $support_count")
                    await transaction.run(
                        cypher, canonical_a=canonical_a, canonical_b=canonical_b,
                        tenant=tenant_id, kb=knowledge_base, support_count=support_count,
                    )
                await transaction.run(
                    "MATCH (n:Entity {tenant_id: $tenant, knowledge_base: $kb}) WHERE NOT (n)--() DELETE n",
                    tenant=tenant_id, kb=knowledge_base,
                )
                await transaction.commit()
            except Exception:
                await transaction.rollback()
                raise

        await self._prune_postgres_orphans(tenant_id, knowledge_base)

    async def _prune_postgres_orphans(self, tenant_id, knowledge_base) -> None:
        async with self._postgres.get_client() as session:
            await session.execute(
                text(
                    'DELETE FROM "KBNeo4jNode" n WHERE n.tenant_id = :tenant_id AND n.knowledge_base = :knowledge_base '
                    'AND NOT EXISTS (SELECT 1 FROM "KBNeo4jRelationship" r '
                    'WHERE r.from_node = n.node_id OR r.to_node = n.node_id)'
                ),
                {"tenant_id": _as_uuid(tenant_id), "knowledge_base": knowledge_base},
            )
            await session.commit()
