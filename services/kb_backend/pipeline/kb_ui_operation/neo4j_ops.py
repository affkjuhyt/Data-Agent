"""Business logic for Neo4j graph visualization and Cypher execution."""
import logging
import os
from fastapi import HTTPException
from typing import LiteralString

from basemodel.services_databaseconnector.postgres_model import (
    ReadJoinRequest, WhereFilter,
)
from services.kb_backend.UI_model.response import to_string

log = logging.getLogger(__name__)

_READONLY_PREFIXES = ("CREATE", "MERGE", "DELETE", "SET", "REMOVE", "DROP")

# Hard cap on the number of nodes returned to the UI. The graph canvas degrades
# badly past a few hundred nodes, so the backend enforces this for every path
# (live Neo4j, Postgres fallback, and ad-hoc Cypher) regardless of the query.
_MAX_NODES = int(os.getenv("NEO4J_MAX_GRAPH_NODES", "200"))


def _pick_type(labels) -> str | None:
    """A node's display type = its first specific label. 'Entity' is a base label
    shared by everything, so prefer a more specific one when present."""
    if not labels:
        return None
    for lbl in labels:
        if lbl != "Entity":
            return lbl
    return labels[0]


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
        limit=_MAX_NODES,
    ))
    nodes = [
        {
            "id":          to_string(r.get("node_id")),
            "name":        r.get("node_name", ""),
            "description": r.get("node_description"),
            "type":        None,  # Postgres node registry has no label/type column
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

    # Cap distinct nodes at $max first, then only keep edges that stay within
    # that capped set — so the UI never receives more than _MAX_NODES nodes.
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

        seen_nodes: dict[int, dict] = {}
        edges: list[dict] = []
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


async def run_cypher(neo4j, cypher: str, tenant_id: str | None = None) -> dict:
    """Run a read-only Cypher query and return the matched subgraph as
    {nodes, edges} — the same shape as /graph — so the UI can render the query
    result straight onto the canvas. Returns an EMPTY subgraph (not an error)
    when Neo4j is unavailable or the query yields no graph, so the client can
    fall back to a local traversal of the already-loaded graph.

    `tenant_id` is bound as the $tenant_id parameter so builder-generated queries
    stay scoped to the caller's tenant — same isolation the /graph endpoint uses."""
    if any(cypher.strip().upper().startswith(kw) for kw in _READONLY_PREFIXES):
        raise HTTPException(status_code=400, detail="Only read-only Cypher queries are allowed")

    def _nid(n) -> str:
        return str(getattr(n, "element_id", None) or n.id)

    try:
        async with neo4j.get_client().session() as session:
            # Passing tenant_id is harmless when the query doesn't reference it;
            # Neo4j only errors on *missing* referenced params, not unused ones.
            result = await session.run(cypher, tenant_id=tenant_id)
            graph = await result.graph()   # nodes + relationships traversed by the query

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
