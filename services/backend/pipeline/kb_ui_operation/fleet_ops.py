"""Business logic for the Fleet dashboard stats aggregation."""
import logging
from basemodel.services_databaseconnector.postgres_model import ReadJoinRequest
from services.backend.UI_model.response import FleetStats, FleetContent

log = logging.getLogger(__name__)


def _shape_fleet_stats(gold_docs: list, qdrant_rows: list, neo_conns: list, batches: list) -> FleetStats:
    counts: dict[str, int] = {}
    for d in gold_docs:
        st = d.get("source_type", "doc")
        counts[st] = counts.get(st, 0) + 1
    return FleetStats(
        content=FleetContent(
            documents=counts.get("doc", 0),
            web=counts.get("web", 0),
            media=counts.get("image", 0) + counts.get("video", 0),
            warehouses=counts.get("warehouse", 0),
        ),
        qdrant_collections=len(qdrant_rows),
        neo4j_nodes=sum(r.get("total_node", 0) for r in neo_conns),
        neo4j_relationships=sum(r.get("total_edge", 0) for r in neo_conns),
        unresolved_conflict_batches=sum(1 for b in batches if b.get("status") in ("pending", "awaiting")),
    )


async def get_fleet_stats(postgres, qdrant, tenant_id: str | None) -> dict:
    docs_resp = await postgres.read(ReadJoinRequest(
        tenant_id=tenant_id, joins_table=["KBData"], limit=1000,
    ))
    gold_docs = [
        r for r in (docs_resp.data or [])
        if (r.get("current_tier") or "") == "gold"
    ]

    # Count connected warehouses (stored in KBWarehouse, not KBData)
    wh_resp = await postgres.read(ReadJoinRequest(joins_table=["KBWarehouse"], limit=1000))
    wh_count = sum(1 for r in (wh_resp.data or []) if not r.get("is_deleted"))
    # Inject as synthetic gold warehouse rows so _shape_fleet_stats counts them
    for _ in range(wh_count):
        gold_docs.append({"source_type": "warehouse", "current_tier": "gold"})

    qdrant_rows: list = []
    try:
        result = await qdrant.get_client().get_collections()
        qdrant_rows = result.collections
    except Exception as e:
        log.warning("fleet_ops: qdrant count failed: %s", e)

    neo_conns: list = []
    try:
        nc_resp = await postgres.read(ReadJoinRequest(
            tenant_id=tenant_id, joins_table=["KBNeo4jConnection"], limit=10,
        ))
        neo_conns = nc_resp.data or []
    except Exception as e:
        log.warning("fleet_ops: neo4j stats failed: %s", e)

    batches: list = []
    try:
        cf_resp = await postgres.read(ReadJoinRequest(
            tenant_id=tenant_id, joins_table=["KBConflictBatch"], limit=200,
        ))
        batches = cf_resp.data or []
    except Exception as e:
        log.warning("fleet_ops: conflicts count failed: %s", e)

    return _shape_fleet_stats(gold_docs, qdrant_rows, neo_conns, batches).model_dump()
