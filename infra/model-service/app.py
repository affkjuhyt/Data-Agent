import asyncio
import logging
from contextlib import asynccontextmanager
import uvicorn
from fastapi import APIRouter, FastAPI, HTTPException
import config as cfg
from backends import registry, bootstrap, sync_with_retry, chat_status, Capability
from backends.base import (
    EmbedRequest, EmbedResponse,
    RerankRequest, RerankResult, RerankResponse,
    GenerateRequest, GenerateResponse,
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("model-service")

router = APIRouter()

@router.get("/health")
def health() -> dict:
    ready = registry.has(Capability.EMBED) and registry.has(Capability.RERANK)
    return {"status": "ok" if ready else "loading", "llm": chat_status(registry)}


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

@router.post("/rerank", response_model=RerankResponse)
def rerank(req: RerankRequest) -> RerankResponse:
    entry = registry.by_id(req.model_id) if req.model_id else registry.default(Capability.RERANK)
    if entry is None:
        if req.model_id:
            raise HTTPException(422, f"rerank model_id '{req.model_id}' not in registry")
        raise HTTPException(503, "no rerank model loaded")
    ranked = entry.caller.rerank(req.query, req.documents, req.top_n)
    return RerankResponse(results=[RerankResult(index=i, relevance_score=s) for i, s in ranked])


@router.post("/generate", response_model=GenerateResponse)
def generate(req: GenerateRequest) -> GenerateResponse:
    entry = registry.by_name(req.model) if req.model else registry.default(Capability.CHAT)
    if entry is None:
        if req.model:
            raise HTTPException(422, f"chat model '{req.model}' not in registry")
        raise HTTPException(503, "no LLM backend available (set REMOTE_LLM_URL)")

    if req.json_schema is not None:
        response_format = {"type": "json_schema",
                           "json_schema": {"name": "out", "strict": True, "schema": req.json_schema}}
    elif req.json_mode:
        response_format = {"type": "json_object"}
    else:
        response_format = None

    if req.messages is not None:
        content = entry.caller.chat(req.messages, response_format,
                                    max_tokens=req.max_tokens, temperature=req.temperature)
    else:
        content = entry.caller.generate(req.prompt or "", req.system_prompt, response_format,
                                        max_tokens=req.max_tokens, temperature=req.temperature)
    return GenerateResponse(content=content)


def _probe_llm_backend() -> None:
    status = chat_status(registry)
    if not status["configured"]:
        log.warning(
            "LLM gateway DISABLED: no chat model registered (no REMOTE_LLM_URL and no "
            "llama-server) — POST /generate returns 503 and every ingestion/lightrag LLM "
            "call fails."
        )
    elif status["ready"]:
        log.info("LLM gateway OK: serving model=%s", status["model"])
    else:
        log.warning("LLM gateway configured but the chat model isn't ready yet — "
                    "/generate errors until the backend is up.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # register model
    bootstrap(registry)
    #Load model
    asyncio.create_task(sync_with_retry(registry), name="registry-sync")
    _probe_llm_backend()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="model-service", lifespan=lifespan)
    app.include_router(router)
    return app


if __name__ == "__main__":
    uvicorn.run("app:create_app", factory=True, host="0.0.0.0", port=cfg.PORT)
