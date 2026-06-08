import os
import logging
from services.database_connector.mongo_connector import client

logger = logging.getLogger(__name__)


async def init_mongo():
    url = os.environ.get("MONGODB_URL", "mongodb://localhost:27017/dataagent")
    client.set_url(url)
    await client.open()


async def close_mongo():
    await client.close()


async def load_flow_nodes(workflow_version_id: str) -> list[dict]:
    if not client.is_connected() or not workflow_version_id:
        return []
    try:
        db = client.get_client()
        nodes = await db.flow_nodes.find(
            {"workflow_version_id": workflow_version_id}, {"_id": 0}
        ).to_list(None)
        return nodes
    except Exception as e:
        logger.warning("mongo load_flow_nodes failed: %s", e)
        return []


async def load_flow_edges(workflow_version_id: str) -> list[dict]:
    if not client.is_connected() or not workflow_version_id:
        return []
    try:
        db = client.get_client()
        edges = await db.flow_edges.find(
            {"workflow_version_id": workflow_version_id}, {"_id": 0}
        ).to_list(None)
        return edges
    except Exception as e:
        logger.warning("mongo load_flow_edges failed: %s", e)
        return []
