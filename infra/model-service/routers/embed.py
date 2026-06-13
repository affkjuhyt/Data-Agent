import uuid as _uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from models import get_embed, get_embed_for_name, is_ready
import config as cfg
import registry

router = APIRouter()


class EmbedRequest(BaseModel):
    texts: list[str]
    # UUID of the KBModel row that owns the target collection.
    # When provided the service validates the model is loaded and uses it.
    # When absent the default model (cfg.EMBED_MODEL) is used.
    model_id: str | None = None


class EmbedResponse(BaseModel):
    embeddings: list[list[float]]
    model_id: str | None  # KBModel UUID that produced the embeddings


@router.post("/embed", response_model=EmbedResponse)
def embed(req: EmbedRequest) -> EmbedResponse:
    if not is_ready():
        raise HTTPException(503, "Model not loaded")

    if req.model_id:
        try:
            requested_uuid = _uuid.UUID(req.model_id)
        except ValueError:
            raise HTTPException(422, f"model_id '{req.model_id}' is not a valid UUID")

        model_name = registry.get_model_name_for_id(requested_uuid)
        if model_name is None:
            raise HTTPException(
                422,
                f"model_id {requested_uuid} not in registry — "
                "DB sync not yet complete or model not seeded in KBModel",
            )

        embed_model = get_embed_for_name(model_name)
        if embed_model is None:
            raise HTTPException(
                503, f"Model '{model_name}' registered but not yet loaded — retry shortly",
            )

        responded_id = req.model_id
    else:
        embed_model = get_embed()
        own_uuid = registry.get_model_id_for_name(cfg.EMBED_MODEL)
        responded_id = str(own_uuid) if own_uuid else None

    if not req.texts:
        return EmbedResponse(embeddings=[], model_id=responded_id)

    vecs = embed_model.encode(req.texts, normalize_embeddings=True)
    return EmbedResponse(embeddings=vecs.tolist(), model_id=responded_id)
