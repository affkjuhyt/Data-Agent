from typing import Union

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer

from backends import provider

router = APIRouter()

_bge_m3: SentenceTransformer | None = None


def _get_bge_m3() -> SentenceTransformer:
    global _bge_m3
    if _bge_m3 is None:
        _bge_m3 = SentenceTransformer("BAAI/bge-m3", cache_folder="/app/models", local_files_only=True)
    return _bge_m3


class EmbedRequest(BaseModel):
    texts: list[str]
    # UUID of the KBModel row that owns the target collection.
    # When provided the service validates the model is loaded and uses it.
    # When absent the default model (cfg.EMBED_MODEL) is used.
    # Remote backends echo it back unchanged (the remote GPU owns the model).
    model_id: str | None = None


class EmbedResponse(BaseModel):
    embeddings: list[list[float]]
    model_id: str | None  # KBModel UUID that produced the embeddings


@router.post("/embed", response_model=EmbedResponse)
def embed(req: EmbedRequest) -> EmbedResponse:
    backend = provider.active_embed()
    if backend is None:
        raise HTTPException(503, "Model not loaded")
    try:
        embeddings, model_id = backend.embed(req.texts, req.model_id)
    except ValueError as exc:        # invalid model_id UUID
        raise HTTPException(422, str(exc))
    except LookupError as exc:        # model_id not in registry
        raise HTTPException(422, str(exc))
    except RuntimeError as exc:       # registered but not loaded yet
        raise HTTPException(503, str(exc))
    return EmbedResponse(embeddings=embeddings, model_id=model_id)


# ── OpenAI-compatible /v1/embeddings — dùng BGE-M3 cho memory services ───────

class _OAIEmbedRequest(BaseModel):
    input: Union[str, list[str]]
    model: str = "bge-m3"


class _OAIEmbedObject(BaseModel):
    object: str = "embedding"
    index: int
    embedding: list[float]


class _OAIEmbedResponse(BaseModel):
    object: str = "list"
    data: list[_OAIEmbedObject]
    model: str
    usage: dict


@router.post("/v1/embeddings", response_model=_OAIEmbedResponse)
def openai_embeddings(req: _OAIEmbedRequest) -> _OAIEmbedResponse:
    texts = [req.input] if isinstance(req.input, str) else req.input
    try:
        model = _get_bge_m3()
        vectors = model.encode(texts, normalize_embeddings=True)
    except Exception as exc:
        raise HTTPException(500, str(exc))
    total_tokens = sum(len(t.split()) for t in texts)
    return _OAIEmbedResponse(
        data=[_OAIEmbedObject(index=i, embedding=v.tolist()) for i, v in enumerate(vectors)],
        model=req.model,
        usage={"prompt_tokens": total_tokens, "total_tokens": total_tokens},
    )
