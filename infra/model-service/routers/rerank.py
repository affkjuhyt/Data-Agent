from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backends import registry, Capability

router = APIRouter()


class RerankRequest(BaseModel):
    query: str
    documents: list[str]
    top_n: int = 5
    # UUID of the KBModel rerank row. When absent the default reranker is used.
    model_id: str | None = None


class RerankResult(BaseModel):
    index: int
    relevance_score: float


class RerankResponse(BaseModel):
    results: list[RerankResult]


@router.post("/rerank", response_model=RerankResponse)
def rerank(req: RerankRequest) -> RerankResponse:
    entry = registry.by_id(req.model_id) if req.model_id else registry.default(Capability.RERANK)
    if entry is None:
        if req.model_id:
            raise HTTPException(422, f"rerank model_id '{req.model_id}' not in registry")
        raise HTTPException(503, "no rerank model loaded")
    ranked = entry.caller.rerank(req.query, req.documents, req.top_n)
    return RerankResponse(results=[RerankResult(index=i, relevance_score=s) for i, s in ranked])
