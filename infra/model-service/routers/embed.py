from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backends import provider

router = APIRouter()


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
