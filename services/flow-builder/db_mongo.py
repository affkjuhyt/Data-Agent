from typing import Any
import os
from libs.database_connector.mongo_connector import client


async def init_mongo() -> None:
    url = os.environ.get("MONGODB_URL", "mongodb://localhost:27017/dataagent")
    client.set_url(url)
    await client.open()


async def close_mongo() -> None:
    await client.close()


async def save_canvas(workflow_version_id: str, nodes: list[Any], edges: list[Any]) -> None:
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
