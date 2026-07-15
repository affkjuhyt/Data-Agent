"""/api/knowledge/neo4j — graph visualization and Cypher query runner."""
from fastapi import APIRouter, Depends, Request

from libs.basemodel.services_databaseconnector.shared_model import ResponseModel
from services.kb_backend.schemas import Neo4jQueryRequest
from services.kb_backend.dependencies.auth import authenticate
from services.kb_backend.dependencies.services import svc
from services.kb_backend.operations.neo4j_ops import get_graph, get_schema, run_cypher

router = APIRouter(prefix="/api/knowledge/neo4j", tags=["Knowledge-Neo4j"])


@router.get("/graph", response_model=ResponseModel)
async def get_graph_route(request: Request, ctx: dict = Depends(authenticate)):
    result = await get_graph(
        postgres=svc(request, "postgres"),
        neo4j=svc(request, "neo4j"),
        tenant_id=ctx["tenant_id"],
    )
    return ResponseModel(code=200, data=result)


@router.get("/schema", response_model=ResponseModel)
async def get_schema_route(request: Request, ctx: dict = Depends(authenticate)):
    result = await get_schema(
        neo4j=svc(request, "neo4j"),
        postgres=svc(request, "postgres"),
        tenant_id=ctx["tenant_id"],
    )
    return ResponseModel(code=200, data=result)


@router.post("/query", response_model=ResponseModel)
async def run_cypher_route(
    body: Neo4jQueryRequest, request: Request, ctx: dict = Depends(authenticate),
):
    result = await run_cypher(
        neo4j=svc(request, "neo4j"),
        cypher=body.cypher,
        tenant_id=ctx["tenant_id"],
    )
    return ResponseModel(code=200, data=result)
