from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backends import provider

router = APIRouter()


class RerankRequest(BaseModel):
    query: str
    documents: list[str]
    top_n: int = 5


class RerankResult(BaseModel):
    index: int
    relevance_score: float


class RerankResponse(BaseModel):
    results: list[RerankResult]


@router.post("/rerank", response_model=RerankResponse)
def rerank(req: RerankRequest) -> RerankResponse:
    backend = provider.active_rerank()
    if backend is None:
        raise HTTPException(503, "Model not loaded")
    ranked = backend.rerank(req.query, req.documents, req.top_n)
    return RerankResponse(results=[RerankResult(index=i, relevance_score=s) for i, s in ranked])
