from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
import uvicorn

from _logging import get_logger
from model_manager import KVCacheManager
from llama import Llama3Model

log = get_logger("ollama-api")

_cache: KVCacheManager | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _cache
    model = Llama3Model()
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, model.load)
    _cache = KVCacheManager(model)
    # Capture the clean initial state so direct API calls work without pre-registering
    # a system prompt. POST /api/system can still be used for KV cache optimization.
    await loop.run_in_executor(None, _cache.init_empty_state)
    log.info("Model ready. Direct calls work immediately; POST /api/system enables KV caching.")
    yield
    _cache = None


app = FastAPI(title="Llama3 Ollama-compatible API", version="1.0.0", lifespan=lifespan)


def _get_cache() -> KVCacheManager:
    if _cache is None:
        raise HTTPException(status_code=503, detail="Model not ready")
    return _cache


async def _sync_gen_to_async(sync_gen) -> AsyncIterator[bytes]:
    loop = asyncio.get_event_loop()
    queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=32)

    def producer() -> None:
        try:
            for chunk in sync_gen:
                line = (json.dumps(chunk) + "\n").encode()
                asyncio.run_coroutine_threadsafe(queue.put(line), loop).result()
        finally:
            asyncio.run_coroutine_threadsafe(queue.put(None), loop).result()

    producer_future = loop.run_in_executor(None, producer)
    try:
        while True:
            item = await queue.get()
            if item is None:
                break
            yield item
    finally:
        await producer_future


@app.post("/api/system", status_code=201)
async def api_register_system(request: Request):
    cache = _get_cache()
    body: dict[str, Any] = await request.json()
    name = body.get("name")
    prompt = body.get("prompt")
    if not name or not prompt:
        raise HTTPException(status_code=422, detail="'name' and 'prompt' are required")
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, cache.register_system_prompt, name, prompt)
    return JSONResponse({"registered": name, "system_types": cache.system.keys()}, status_code=201)


@app.get("/api/system")
async def api_list_systems():
    return JSONResponse({"system_types": _get_cache().system.keys()})


@app.post("/api/chat")
async def api_chat(request: Request):
    cache = _get_cache()
    payload: dict[str, Any] = await request.json()
    user_id = request.headers.get("X-User-Id")
    system_type = request.headers.get("X-System-Type", "default")
    if payload.get("stream"):
        return StreamingResponse(
            _sync_gen_to_async(cache.chat_stream(payload, user_id, system_type)),
            media_type="application/x-ndjson",
        )
    loop = asyncio.get_event_loop()
    return JSONResponse(await loop.run_in_executor(
        None, lambda: cache.chat(payload, user_id, system_type)
    ))


@app.post("/api/generate")
async def api_generate(request: Request):
    cache = _get_cache()
    payload: dict[str, Any] = await request.json()
    system_type = request.headers.get("X-System-Type", "default")
    if payload.get("stream"):
        return StreamingResponse(
            _sync_gen_to_async(cache.completion_stream(payload, system_type)),
            media_type="application/x-ndjson",
        )
    loop = asyncio.get_event_loop()
    return JSONResponse(await loop.run_in_executor(
        None, lambda: cache.completion(payload, system_type)
    ))


@app.post("/api/embeddings")
async def api_embeddings(request: Request):
    """Ollama-compatible embeddings endpoint. LiteLLM routes embedding calls here."""
    cache = _get_cache()
    body: dict[str, Any] = await request.json()
    prompt = body.get("prompt", "")
    if not prompt:
        raise HTTPException(status_code=422, detail="'prompt' is required")
    loop = asyncio.get_event_loop()
    try:
        embedding = await loop.run_in_executor(None, lambda: cache.embed(prompt))
    except Exception as exc:
        log.error("Embedding failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Embedding failed: {exc}")
    return JSONResponse({"model": "llama3", "embedding": embedding})


@app.get("/api/tags")
async def api_tags():
    cache = _get_cache()
    info = cache.get_client().health()
    return JSONResponse({
        "models": [{
            "name": "llama3",
            "model": "llama3",
            "modified_at": "2024-01-01T00:00:00Z",
            "size": 0,
            "digest": "",
            "details": {
                "format": "gguf",
                "family": "llama",
                "families": ["llama"],
                "parameter_size": "8B",
                "quantization_level": info.get("kv_cache_type", "f16").upper(),
            },
        }]
    })


@app.get("/health")
async def health():
    if _cache is None:
        return JSONResponse({"status": "not_loaded"}, status_code=503)
    return JSONResponse(_cache.get_client().health())


if __name__ == "__main__":
    uvicorn.run("api:app", host="0.0.0.0", port=11434, reload=False)
