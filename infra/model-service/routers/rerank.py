from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from models import get_rerank, is_ready
import config as cfg
import remote

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
    # Remote proxy: forward to a GPU server (Colab + cloudflared) when configured.
    if cfg.REMOTE_RERANK_URL:
        if not req.documents:
            return RerankResponse(results=[])
        data = remote.post(cfg.REMOTE_RERANK_URL, "/rerank",
                           {"query": req.query, "documents": req.documents, "top_n": req.top_n})
        return RerankResponse(results=[RerankResult(**r) for r in data.get("results", [])])

    if not is_ready():
        raise HTTPException(503, "Model not loaded")
    if not req.documents:
        return RerankResponse(results=[])
    pairs = [(req.query, doc) for doc in req.documents]
    scores = get_rerank().predict(pairs).tolist()
    top_n = min(req.top_n, len(scores))
    ranked = sorted(
        [{"index": i, "relevance_score": float(s)} for i, s in enumerate(scores)],
        key=lambda x: x["relevance_score"],
        reverse=True,
    )[:top_n]
    return RerankResponse(results=[RerankResult(**r) for r in ranked])
