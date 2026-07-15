"""
Thin wrapper exposing run_query(cypher) without a driver argument.
Initialized by app/main.py on startup.
"""
from neo4j._async.driver import AsyncDriver

from libs.basemodel.services_databaseconnector.shared_model import ResponseModel, RetryConfig

_driver: AsyncDriver | None = None


async def init(url: str) -> None:
    global _driver
    from libs.database_connector.neo4j_connector import Neo4jClient
    client = Neo4jClient()
    client.set_url(url)
    try:
        _driver = await client.open(RetryConfig(count=3))
    except ConnectionError:
        _driver = None


async def run_query(cypher: str, params: dict | None = None) -> ResponseModel:
    if _driver is None:
        return ResponseModel(code=503, error="Neo4j not connected")
    from libs.database_connector.neo4j_connector import run_query as _rq
    return await _rq(_driver, cypher, params)
