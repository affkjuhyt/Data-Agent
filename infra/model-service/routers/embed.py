from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backends import registry, Capability

router = APIRouter()


class EmbedRequest(BaseModel):
    texts: list[str]
    # UUID of the KBModel embedding row. When absent the default embed model is used.
    model_id: str | None = None


class EmbedResponse(BaseModel):
    embeddings: list[list[float]]
    model_id: str | None  # KBModel UUID that produced the embeddings (echoed for remote)


@router.post("/embed", response_model=EmbedResponse)
def embed(req: EmbedRequest) -> EmbedResponse:
    entry = registry.by_id(req.model_id) if req.model_id else registry.default(Capability.EMBED)
    if entry is None:
        if req.model_id:
            raise HTTPException(422, f"embedding model_id '{req.model_id}' not in registry "
                                     "(DB sync not complete or not seeded in KBModel)")
        raise HTTPException(503, "no embedding model loaded")
    embeddings = entry.caller.embed(req.texts)
    used_id = str(entry.model_id) if entry.model_id else req.model_id
    return EmbedResponse(embeddings=embeddings, model_id=used_id)
