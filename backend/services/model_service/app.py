from typing import Any, AsyncIterator
import asyncio
import logging
import threading
import time
from contextlib import asynccontextmanager
import uvicorn
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse
import config as cfg
from backends import registry, bootstrap, sync_with_retry, chat_status, Capability
from backends.base import (
    EmbedRequest, EmbedResponse,
    RerankRequest, RerankResult, RerankResponse,
    GenerateRequest, GenerateResponse,
    OAIEmbedRequest, OAIEmbedObject, OAIEmbedResponse,
    EmbedCaller, RerankCaller, ChatCaller,
)
from backends.embed_service import EmbedService

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("model_service")

router = APIRouter()


def _default_encode(texts: list[str]) -> list[list[float]]:
    """Resolve the current default embed caller at call time (the model loads
    asynchronously after startup, so this can't be bound once)."""
    entry = registry.default(Capability.EMBED)
    if entry is None or not isinstance(entry.caller, EmbedCaller):
        raise RuntimeError("no embedding model loaded")
    vectors: list[list[float]] = entry.caller.embed(texts)
    return vectors


# Cache + dynamic-batch wrapper for the default embedding path (the hot path).
_embed_service = EmbedService(
    _default_encode,
    cache_size=cfg.EMBED_CACHE_SIZE,
    batch_max=cfg.EMBED_BATCH_MAX,
    batch_wait_ms=cfg.EMBED_BATCH_WAIT_MS,
    cache_enabled=cfg.EMBED_CACHE_ENABLED,
    batch_enabled=cfg.EMBED_BATCH_ENABLED,
)

# LLM gateway (canonicalize/extraction) usage counters — the REAL production
# signal for the wired model, recorded on every /generate call. Guarded by a lock
# because FastAPI runs sync routes in a threadpool.
_llm_lock = threading.Lock()
_llm_stats: dict[str, Any] = {
    "total": 0, "errors": 0, "ms_sum": 0.0, "ms_last": 0.0, "model": "", "tokens": 0,
}


def _record_llm(ms: float, model: str, ok: bool, out_len: int) -> None:
    with _llm_lock:
        _llm_stats["total"] += 1
        if not ok:
            _llm_stats["errors"] += 1
        else:
            _llm_stats["ms_sum"] += ms
            _llm_stats["ms_last"] = ms
            _llm_stats["tokens"] += out_len
        if model:
            _llm_stats["model"] = model


@router.get("/health")
def health() -> JSONResponse:
    # bootstrap(registry) runs synchronously in lifespan() before the app
    # starts accepting connections (create_app() below) — by the time any
    # request reaches here, embed/rerank registration has already either
    # succeeded or permanently failed (register_embedding_models has no
    # retry of its own; only the DB id-sync task does, and that's a
    # different thing — see backends/sync.py). "loading" is therefore not a
    # transient window a real 503 would false-positive during: a TEI probe
    # that failed at boot (e.g. a momentary network hiccup) never retries,
    # so k8s never sees a body it can act on and the pod stays "ready"
    # forever with a permanently broken /embed and /rerank unless something
    # restarts it. Returning a real 503 lets liveness/readinessProbe do that
    # (deploy/k8s/production/service/model-service.yaml uses this same endpoint for
    # both) instead of only ever returning 200.
    ready = registry.has(Capability.EMBED) and registry.has(Capability.RERANK)
    body = {"status": "ok" if ready else "loading", "llm": chat_status(registry)}
    return JSONResponse(body, status_code=200 if ready else 503)


@router.get("/metrics")
def metrics_endpoint() -> PlainTextResponse:
    """Prometheus metrics — embed cache hit-rate + dynamic-batch efficiency +
    the compute device (always remote-api: this service runs no local model)."""
    s = _embed_service.stats()
    cache = s.get("cache", {})
    batch = s.get("batcher", {})
    lines = [
        "# TYPE kb_embed_cache_hits counter",
        f"kb_embed_cache_hits {cache.get('hits', 0)}",
        "# TYPE kb_embed_cache_misses counter",
        f"kb_embed_cache_misses {cache.get('misses', 0)}",
        "# TYPE kb_embed_cache_hit_rate gauge",
        f"kb_embed_cache_hit_rate {cache.get('hit_rate', 0.0)}",
        "# TYPE kb_embed_cache_size gauge",
        f"kb_embed_cache_size {cache.get('size', 0)}",
        "# TYPE kb_embed_batches counter",
        f"kb_embed_batches {batch.get('batches', 0)}",
        "# TYPE kb_embed_batched_texts counter",
        f"kb_embed_batched_texts {batch.get('batched_texts', 0)}",
        "# TYPE kb_embed_avg_batch gauge",
        f"kb_embed_avg_batch {batch.get('avg_batch', 0.0)}",
        "# TYPE kb_model_device gauge",
        'kb_model_device{device="remote-api"} 1',
    ]

    # LLM gateway (canonicalize) — the wired model's real usage + latency.
    with _llm_lock:
        g = dict(_llm_stats)
    ok = g["total"] - g["errors"]
    avg_ms = (g["ms_sum"] / ok) if ok else 0.0
    ml = f'model="{g["model"] or cfg.REMOTE_LLM_MODEL or "none"}"'
    lines += [
        "# TYPE kb_llm_generate_total counter",
        f"kb_llm_generate_total {g['total']}",
        "# TYPE kb_llm_generate_errors_total counter",
        f"kb_llm_generate_errors_total {g['errors']}",
        "# TYPE kb_llm_generate_ms_avg gauge",
        f"kb_llm_generate_ms_avg {avg_ms:.3f}",
        "# TYPE kb_llm_generate_ms_last gauge",
        f"kb_llm_generate_ms_last {g['ms_last']:.3f}",
        "# TYPE kb_llm_generate_chars_total counter",
        f"kb_llm_generate_chars_total {g['tokens']}",
        "# TYPE kb_llm_gateway_model gauge",
        f"kb_llm_gateway_model{{{ml}}} 1",
    ]
    return PlainTextResponse("\n".join(lines) + "\n")


@router.post("/embed", response_model=EmbedResponse)
async def embed(req: EmbedRequest) -> EmbedResponse:
    entry = registry.by_id(req.model_id) if req.model_id else registry.default(Capability.EMBED)
    if entry is None:
        if req.model_id:
            raise HTTPException(422, f"embedding model_id '{req.model_id}' not in registry "
                                     "(DB sync not complete or not seeded in KBModel)")
        raise HTTPException(503, "no embedding model loaded")
    if not isinstance(entry.caller, EmbedCaller):
        raise HTTPException(500, f"model '{entry.name}' is not an embedding backend")
    # Default embed model → cache + dynamic batch (the hot retrieval path). An
    # explicitly-requested non-default model bypasses the shared cache/batcher.
    if req.model_id and entry is not registry.default(Capability.EMBED):
        embeddings = entry.caller.embed(req.texts)
    else:
        embeddings = await _embed_service.embed(entry.name, req.texts)
    used_id = str(entry.model_id) if entry.model_id else req.model_id
    return EmbedResponse(embeddings=embeddings, model_id=used_id)


@router.post("/v1/embeddings", response_model=OAIEmbedResponse)
async def openai_embeddings(req: OAIEmbedRequest) -> OAIEmbedResponse:
    """OpenAI-compatible shape over the same remote default embedder (goes
    through the shared cache/batcher like /embed)."""
    texts = [req.input] if isinstance(req.input, str) else req.input
    entry = registry.default(Capability.EMBED)
    if entry is None:
        raise HTTPException(503, "no embedding backend (set REMOTE_EMBED_URL)")
    try:
        vectors = await _embed_service.embed(entry.name, texts)
    except Exception as exc:
        raise HTTPException(502, str(exc))
    total_tokens = sum(len(t.split()) for t in texts)
    return OAIEmbedResponse(
        data=[OAIEmbedObject(index=i, embedding=v) for i, v in enumerate(vectors)],
        model=req.model,
        usage={"prompt_tokens": total_tokens, "total_tokens": total_tokens},
    )


@router.post("/rerank", response_model=RerankResponse)
def rerank(req: RerankRequest) -> RerankResponse:
    entry = registry.by_id(req.model_id) if req.model_id else registry.default(Capability.RERANK)
    if entry is None:
        if req.model_id:
            raise HTTPException(422, f"rerank model_id '{req.model_id}' not in registry")
        raise HTTPException(503, "no rerank model loaded")
    if not isinstance(entry.caller, RerankCaller):
        raise HTTPException(500, f"model '{entry.name}' is not a rerank backend")
    ranked = entry.caller.rerank(req.query, req.documents, req.top_n)
    return RerankResponse(results=[RerankResult(index=i, relevance_score=s) for i, s in ranked])


@router.post("/generate", response_model=GenerateResponse)
def generate(req: GenerateRequest) -> GenerateResponse:
    entry = registry.by_name(req.model) if req.model else registry.default(Capability.CHAT)
    if entry is None:
        if req.model:
            raise HTTPException(422, f"chat model '{req.model}' not in registry")
        raise HTTPException(503, "no LLM backend available (set REMOTE_LLM_URL)")
    if not isinstance(entry.caller, ChatCaller):
        raise HTTPException(500, f"model '{entry.name}' is not a chat backend")

    if req.json_schema is not None:
        response_format = {"type": "json_schema",
                           "json_schema": {"name": "out", "strict": True, "schema": req.json_schema}}
    elif req.json_mode:
        response_format = {"type": "json_object"}
    else:
        response_format = None

    _t0 = time.perf_counter()
    try:
        if req.messages is not None:
            content = entry.caller.chat(req.messages, response_format,
                                        max_tokens=req.max_tokens, temperature=req.temperature)
        else:
            content = entry.caller.generate(req.prompt or "", req.system_prompt, response_format,
                                            max_tokens=req.max_tokens, temperature=req.temperature)
    except Exception:
        _record_llm((time.perf_counter() - _t0) * 1000, entry.name, ok=False, out_len=0)
        raise
    _record_llm((time.perf_counter() - _t0) * 1000, entry.name, ok=True, out_len=len(content))
    return GenerateResponse(content=content)


def _probe_llm_backend() -> None:
    status = chat_status(registry)
    if not status["configured"]:
        log.warning(
            "LLM gateway DISABLED: no chat model registered (REMOTE_LLM_URL unset) — "
            "POST /generate returns 503 and every ingestion/lightrag LLM call fails."
        )
    elif status["ready"]:
        log.info("LLM gateway OK: serving model=%s", status["model"])
    else:
        log.warning("LLM gateway configured but the chat model isn't ready yet — "
                    "/generate errors until the backend is up.")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # register model
    bootstrap(registry)
    #Load model
    asyncio.create_task(sync_with_retry(registry), name="registry-sync")
    _embed_service.start()  # launch the dynamic-batch flusher on this loop
    _probe_llm_backend()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="model_service", lifespan=lifespan)
    app.include_router(router)
    return app


if __name__ == "__main__":
    uvicorn.run("app:create_app", factory=True, host="0.0.0.0", port=cfg.PORT)
