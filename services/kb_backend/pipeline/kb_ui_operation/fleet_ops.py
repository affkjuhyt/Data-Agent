"""Business logic for the Fleet dashboard stats aggregation."""
import logging
import uuid

from sqlalchemy import select as sa_select, func as sa_func

from basemodel.services_databaseconnector.postgres_model import ReadJoinRequest, WhereFilter
from basemodel.services_databaseconnector.postgres_orm.knowledge_base_orm import (
    KBDataORM,
    KBWarehouseORM,
)
from services.kb_backend.UI_model.response import FleetStats, FleetContent

log = logging.getLogger(__name__)


async def _gold_source_counts(postgres, tenant_id: str | None) -> dict[str, int]:
    """Gold-tier doc count per source_type via a single GROUP BY — NOT by fetching
    rows and counting in Python (that capped at the read limit and silently dropped
    everything past it, undercounting documents and missing rarer types like web)."""
    counts: dict[str, int] = {}
    try:
        async with postgres.get_client() as session:
            stmt = (
                sa_select(KBDataORM.source_type, sa_func.count())
                .where(KBDataORM.current_tier == "gold", KBDataORM.is_deleted.is_(False))
            )
            if tenant_id:
                stmt = stmt.where(KBDataORM.tenant_id == uuid.UUID(tenant_id))
            stmt = stmt.group_by(KBDataORM.source_type)
            for source_type, count in (await session.execute(stmt)).all():
                counts[source_type] = count
    except Exception as e:
        log.warning("fleet_ops: gold source counts failed: %s", e)
    return counts


async def _warehouse_count(postgres) -> int:
    """Connected warehouses live in KBWarehouse (which has no tenant_id column, so
    this is a global non-deleted count — matches the prior behaviour)."""
    try:
        async with postgres.get_client() as session:
            return int(await session.scalar(
                sa_select(sa_func.count())
                .select_from(KBWarehouseORM)
                .where(KBWarehouseORM.is_deleted.is_(False))
            ) or 0)
    except Exception as e:
        log.warning("fleet_ops: warehouse count failed: %s", e)
        return 0


async def _neo4j_counts(neo4j, tenant_id: str | None) -> tuple[int, int]:
    """LIVE node + relationship counts from Neo4j. The previous code summed the
    cached total_node/total_edge columns on KBNeo4jConnection rows, which are
    stamped once and never reconciled — they drifted to tiny stale values (e.g.
    14/12) while the real graph held thousands."""
    nodes = rels = 0
    try:
        driver = neo4j.get_client()
        async with driver.session() as session:
            if tenant_id:
                n = await (await session.run(
                    "MATCH (n:Entity {tenant_id: $tid}) RETURN count(n) AS c", tid=tenant_id)).single()
                r = await (await session.run(
                    "MATCH (:Entity {tenant_id: $tid})-[rel]->() RETURN count(rel) AS c", tid=tenant_id)).single()
            else:
                n = await (await session.run("MATCH (n) RETURN count(n) AS c")).single()
                r = await (await session.run("MATCH ()-[rel]->() RETURN count(rel) AS c")).single()
            nodes = int(n["c"]) if n else 0
            rels = int(r["c"]) if r else 0
    except Exception as e:
        log.warning("fleet_ops: neo4j live count failed: %s", e)
    return nodes, rels


async def get_fleet_stats(postgres, qdrant, neo4j, tenant_id: str | None) -> dict:
    counts = await _gold_source_counts(postgres, tenant_id)
    wh_count = await _warehouse_count(postgres)

    qdrant_rows: list = []
    try:
        result = await qdrant.get_client().get_collections()
        qdrant_rows = result.collections
    except Exception as e:
        log.warning("fleet_ops: qdrant count failed: %s", e)

    neo4j_nodes, neo4j_relationships = await _neo4j_counts(neo4j, tenant_id)

    # Count batches that actually still have pending conflicts — derived from
    # KBConflict rows, not the KBConflictBatch.status column. The batch row is
    # stamped PENDING at creation and never reconciled, so duplicate-only or
    # already-drained batches linger as PENDING and overcount. This mirrors the
    # ConflictWorkspace "pending" tab, which groups pending conflicts by batch.
    unresolved_batches = 0
    try:
        cf_resp = await postgres.read(ReadJoinRequest(
            tenant_id=tenant_id,
            joins_table=["KBConflict"],
            filters=[WhereFilter(table_name="KBConflict", column_name="status", value="pending")],
            limit=1000,
        ))
        unresolved_batches = len({
            r.get("batch_id") for r in (cf_resp.data or []) if r.get("batch_id") is not None
        })
    except Exception as e:
        log.warning("fleet_ops: conflicts count failed: %s", e)

    return FleetStats(
        content=FleetContent(
            documents=counts.get("doc", 0),
            web=counts.get("web", 0),
            media=counts.get("image", 0) + counts.get("video", 0),
            warehouses=wh_count,
        ),
        qdrant_collections=len(qdrant_rows),
        neo4j_nodes=neo4j_nodes,
        neo4j_relationships=neo4j_relationships,
        unresolved_conflict_batches=unresolved_batches,
    ).model_dump()
