"""Business logic for Neo4j graph visualization and Cypher execution."""
import logging
from fastapi import HTTPException
from typing import LiteralString

from basemodel.services_databaseconnector.postgres_model import (
    ReadJoinRequest, WhereFilter,
)
from services.backend.UI_model.response import to_string

log = logging.getLogger(__name__)

_READONLY_PREFIXES = ("CREATE", "MERGE", "DELETE", "SET", "REMOVE", "DROP")


async def _graph_from_postgres(postgres, tenant_id: str | None) -> tuple[list, list]:
    conn_resp = await postgres.read(ReadJoinRequest(
        tenant_id=tenant_id, joins_table=["KBNeo4jConnection"], limit=10,
    ))
    if conn_resp.code != 200 or not conn_resp.data:
        return [], []

    conn_id = to_string(conn_resp.data[0].get("connection_id"))

    node_resp = await postgres.read(ReadJoinRequest(
        joins_table=["KBNeo4jNode"],
        filters=[WhereFilter(table_name="KBNeo4jNode", column_name="connection_id", value=conn_id)],
        limit=500,
    ))
    nodes = [
        {
            "id":          to_string(r.get("node_id")),
            "name":        r.get("node_name", ""),
            "description": r.get("node_description"),
        }
        for r in (node_resp.data or [])
    ]
    node_ids = {n["id"] for n in nodes}

    rel_resp = await postgres.read(ReadJoinRequest(
        joins_table=["KBNeo4jRelationship"], limit=1000,
    ))
    edges = [
        {
            "from":        to_string(r.get("from_node")),
            "to":          to_string(r.get("to_node")),
            "description": r.get("description"),
            "score":       r.get("score"),
        }
        for r in (rel_resp.data or [])
        if to_string(r.get("from_node")) in node_ids
        and to_string(r.get("to_node")) in node_ids
    ]
    return nodes, edges


async def get_graph(postgres, neo4j, tenant_id: str | None) -> dict:
    if not tenant_id:
        # No tenant context — fall back to Postgres registry immediately
        nodes, edges = await _graph_from_postgres(postgres, tenant_id)
        return {"nodes": nodes, "edges": edges}

    _GRAPH_QUERY: LiteralString = (
        "MATCH (n) WHERE n.tenant_id = $tenant_id "
        "OPTIONAL MATCH (n)-[r]->(m) WHERE m.tenant_id = $tenant_id "
        "RETURN n.name AS name, n.description AS description, id(n) AS nid, "
        "id(m) AS mid, m.name AS mname, r.description AS rdesc, r.score AS rscore "
        "LIMIT 500"
    )
    try:
        async with neo4j.get_client().session() as session:
            result = await session.run(_GRAPH_QUERY, tenant_id=tenant_id)
            records = await result.data()

        seen_nodes: dict[int, dict] = {}
        edges: list[dict] = []
        for rec in records:
            nid = rec.get("nid")
            if nid and nid not in seen_nodes:
                seen_nodes[nid] = {
                    "id":          str(nid),
                    "name":        rec.get("name", ""),
                    "description": rec.get("description"),
                }
            mid = rec.get("mid")
            if mid and mid not in seen_nodes and rec.get("mname"):
                seen_nodes[mid] = {"id": str(mid), "name": rec.get("mname", ""), "description": None}
            if nid and mid:
                edges.append({
                    "from":        str(nid),
                    "to":          str(mid),
                    "description": rec.get("rdesc"),
                    "score":       rec.get("rscore"),
                })
        return {"nodes": list(seen_nodes.values()), "edges": edges}

    except Exception as e:
        log.warning("neo4j_ops: live graph failed (%s) — falling back to Postgres registry", e)
        nodes, edges = await _graph_from_postgres(postgres, tenant_id)
        return {"nodes": nodes, "edges": edges}


async def get_schema(neo4j, postgres=None, tenant_id: str | None = None) -> dict:
    _LABEL_QUERY: LiteralString = "CALL db.labels() YIELD label RETURN label LIMIT 100"
    _REL_QUERY:   LiteralString = "CALL db.relationshipTypes() YIELD relationshipType RETURN relationshipType LIMIT 100"
    try:
        async with neo4j.get_client().session() as session:
            labels_result = await session.run(_LABEL_QUERY)
            labels = [r["label"] async for r in labels_result]
            rels_result = await session.run(_REL_QUERY)
            rels = [r["relationshipType"] async for r in rels_result]
        if labels:
            return {"entities": labels, "connections": {lbl: rels for lbl in labels}}
    except Exception as e:
        log.warning("neo4j_ops: schema query failed: %s", e)

    # Fall back to Postgres node registry when Neo4j is unavailable or empty
    if postgres:
        nodes, _ = await _graph_from_postgres(postgres, tenant_id)
        entities = list(dict.fromkeys(n["name"] for n in nodes if n.get("name")))
        return {"entities": entities, "connections": {e: [] for e in entities}}
    return {"entities": [], "connections": {}}


async def run_cypher(neo4j, cypher: str) -> dict:
    if any(cypher.strip().upper().startswith(kw) for kw in _READONLY_PREFIXES):
        raise HTTPException(status_code=400, detail="Only read-only Cypher queries are allowed")
    try:
        async with neo4j.get_client().session() as session:
            result = await session.run(cypher)
            rows = await result.data()
        return {"rows": rows[:200]}
    except Exception as e:
        log.error("neo4j_ops: cypher execution failed: %s", e)
        raise HTTPException(status_code=400, detail="Query execution failed")
