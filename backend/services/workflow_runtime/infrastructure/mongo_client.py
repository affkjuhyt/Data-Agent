import logging
import os
from typing import Any, cast

from libs.database_connector.mongo_connector import client

logger = logging.getLogger(__name__)


async def init_mongo() -> None:
    url = os.environ.get("MONGODB_URL", "mongodb://localhost:27017/dataagent")
    client.set_url(url)
    await client.open()


async def close_mongo() -> None:
    await client.close()


async def load_flow_nodes(workflow_version_id: str) -> list[dict[str, Any]]:
    if not client.is_connected() or not workflow_version_id:
        return []
    try:
        db = client.get_client()
        nodes = await db.flow_nodes.find(
            {"workflow_version_id": workflow_version_id}, {"_id": 0}
        ).to_list(None)
        return cast(list[dict[str, Any]], nodes)
    except Exception as e:
        logger.warning("mongo load_flow_nodes failed: %s", e)
        return []


async def load_flow_edges(workflow_version_id: str) -> list[dict[str, Any]]:
    if not client.is_connected() or not workflow_version_id:
        return []
    try:
        db = client.get_client()
        edges = await db.flow_edges.find(
            {"workflow_version_id": workflow_version_id}, {"_id": 0}
        ).to_list(None)
        return cast(list[dict[str, Any]], edges)
    except Exception as e:
        logger.warning("mongo load_flow_edges failed: %s", e)
        return []


async def save_canvas(workflow_version_id: str, nodes: list[Any], edges: list[Any]) -> None:
    """Replace the canvas of one workflow version (authoring side of flow_nodes/flow_edges)."""
    db = client.get_client()
    await db.flow_nodes.delete_many({"workflow_version_id": workflow_version_id})
    await db.flow_edges.delete_many({"workflow_version_id": workflow_version_id})

    if nodes:
        await db.flow_nodes.insert_many([
            {**n, "workflow_version_id": workflow_version_id} for n in nodes
        ])
    if edges:
        await db.flow_edges.insert_many([
            {**e, "workflow_version_id": workflow_version_id} for e in edges
        ])


async def load_canvas(workflow_version_id: str) -> dict[str, Any]:
    db = client.get_client()
    nodes = await db.flow_nodes.find(
        {"workflow_version_id": workflow_version_id}, {"_id": 0}
    ).to_list(None)
    edges = await db.flow_edges.find(
        {"workflow_version_id": workflow_version_id}, {"_id": 0}
    ).to_list(None)
    return {"nodes": nodes, "edges": edges}
