import logging
import os
from fastapi import HTTPException
from typing import Any, LiteralString

from libs.basemodel.services_databaseconnector.postgres_model import (
    ReadJoinRequest, WhereFilter,
)
from services.kb_backend.schemas import to_string

log = logging.getLogger(__name__)

_READONLY_PREFIXES = ("CREATE", "MERGE", "DELETE", "SET", "REMOVE", "DROP")

_MAX_NODES = int(os.getenv("NEO4J_MAX_GRAPH_NODES", "200"))


def _pick_type(labels: list[str] | None) -> str | None:
    if not labels:
        return None
    for lbl in labels:
        if lbl != "Entity":
            return lbl
    return labels[0]


async def _graph_from_postgres(postgres: Any, tenant_id: str | None) -> tuple[list[Any], list[Any]]:
    if not tenant_id:
        return [], []

    node_resp = await postgres.read(ReadJoinRequest(
        tenant_id=tenant_id,
        joins_table=["KBNeo4jNode"],
        filters=[
            WhereFilter(table_name="KBNeo4jNode", column_name="tenant_id", value=tenant_id),
            WhereFilter(table_name="KBNeo4jNode", column_name="is_deleted", value=False),
        ],
        limit=_MAX_NODES,
    ))
    nodes = [
        {
            "id":          to_string(r.get("node_id")),
            "name":        r.get("node_name", ""),
            "description": r.get("node_description"),
            "type":        None,
        }
        for r in (node_resp.data or [])
    ]
    node_ids = {n["id"] for n in nodes}

    rel_resp = await postgres.read(ReadJoinRequest(
        tenant_id=tenant_id,
        joins_table=["KBNeo4jRelationship"],
        filters=[
            WhereFilter(table_name="KBNeo4jRelationship", column_name="tenant_id", value=tenant_id),
            WhereFilter(table_name="KBNeo4jRelationship", column_name="is_deleted", value=False),
        ],
        limit=1000,
    ))
    edges: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for r in (rel_resp.data or []):
        f, t = to_string(r.get("from_node")), to_string(r.get("to_node"))
        if f not in node_ids or t not in node_ids or (f, t) in seen:
            continue
        seen.add((f, t))
        edges.append({"from": f, "to": t, "description": r.get("description"), "score": r.get("score")})
    return nodes, edges


async def get_graph(postgres: Any, neo4j: Any, tenant_id: str | None) -> dict[str, Any]:
    if not tenant_id:
        nodes, edges = await _graph_from_postgres(postgres, tenant_id)
        return {"nodes": nodes, "edges": edges}

    _GRAPH_QUERY: LiteralString = (
        "MATCH (n) WHERE n.tenant_id = $tenant_id "
        "WITH collect(DISTINCT n)[0..$max] AS ns "
        "UNWIND ns AS n "
        "OPTIONAL MATCH (n)-[r]->(m) WHERE m IN ns "
        "RETURN id(n) AS nid, labels(n) AS nlabels, "
        "coalesce(n.display_name, n.canonical_name) AS name, n.description AS description, "
        "id(m) AS mid, labels(m) AS mlabels, "
        "coalesce(m.display_name, m.canonical_name) AS mname, r.description AS rdesc, r.score AS rscore"
    )
    try:
        async with neo4j.get_client().session() as session:
            result = await session.run(_GRAPH_QUERY, tenant_id=tenant_id, max=_MAX_NODES)
            records = await result.data()

        seen_nodes: dict[int, dict[str, Any]] = {}
        edges = []
        for rec in records:
            nid = rec.get("nid")
            if nid is not None and nid not in seen_nodes:
                seen_nodes[nid] = {
                    "id":          str(nid),
                    "name":        rec.get("name", ""),
                    "description": rec.get("description"),
                    "type":        _pick_type(rec.get("nlabels")),
                }
            mid = rec.get("mid")
            if mid is not None and mid not in seen_nodes and rec.get("mname"):
                seen_nodes[mid] = {
                    "id":          str(mid),
                    "name":        rec.get("mname", ""),
                    "description": None,
                    "type":        _pick_type(rec.get("mlabels")),
                }
            if nid is not None and mid is not None:
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


async def get_schema(neo4j: Any, postgres: Any = None, tenant_id: str | None = None) -> dict[str, Any]:
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

    if postgres:
        nodes, _ = await _graph_from_postgres(postgres, tenant_id)
        entities = list(dict.fromkeys(n["name"] for n in nodes if n.get("name")))
        return {"entities": entities, "connections": {e: [] for e in entities}}
    return {"entities": [], "connections": {}}


async def run_cypher(neo4j: Any, cypher: str, tenant_id: str | None = None) -> dict[str, Any]:
    if any(cypher.strip().upper().startswith(kw) for kw in _READONLY_PREFIXES):
        raise HTTPException(status_code=400, detail="Only read-only Cypher queries are allowed")

    def _nid(n: Any) -> str:
        return str(getattr(n, "element_id", None) or n.id)

    try:
        async with neo4j.get_client().session() as session:
            result = await session.run(cypher, tenant_id=tenant_id)
            graph = await result.graph()

        nodes = [
            {
                "id":          _nid(n),
                "name":        n.get("name", ""),
                "description": n.get("description"),
                "type":        _pick_type(list(n.labels)),
            }
            for n in graph.nodes
        ][:_MAX_NODES]
        kept = {n["id"] for n in nodes}
        edges = [
            {
                "from":        _nid(r.start_node),
                "to":          _nid(r.end_node),
                "description": r.get("description"),
                "score":       r.get("score"),
            }
            for r in graph.relationships
            if _nid(r.start_node) in kept and _nid(r.end_node) in kept
        ]
        return {"nodes": nodes, "edges": edges}
    except Exception as e:
        log.warning("neo4j_ops: cypher execution failed (%s) — returning empty subgraph", e)
        return {"nodes": [], "edges": []}
