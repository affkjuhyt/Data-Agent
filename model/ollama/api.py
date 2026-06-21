from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
import uvicorn

from _logging import get_logger
from model_manager import ModelPool

log = get_logger("ollama-api")

POOL_SIZE = int(os.getenv("POOL_SIZE", "1"))

_pool: ModelPool | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _pool
    _pool = ModelPool(POOL_SIZE)
    await _pool.build()
    log.info("Pool of %d model instance(s) ready.", POOL_SIZE)
    yield
    _pool = None


app = FastAPI(title="Llama3 Ollama-compatible API", version="1.0.0", lifespan=lifespan)


def _get_pool() -> ModelPool:
    if _pool is None:
        raise HTTPException(status_code=503, detail="Model pool not ready")
    return _pool


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
    pool = _get_pool()
    body: dict[str, Any] = await request.json()
    name = body.get("name")
    prompt = body.get("prompt")
    if not name or not prompt:
        raise HTTPException(status_code=422, detail="'name' and 'prompt' are required")
    await pool.register_system_prompt(name, prompt)
    return JSONResponse({"registered": name, "system_types": pool.system_types()}, status_code=201)


@app.get("/api/system")
async def api_list_systems():
    return JSONResponse({"system_types": _get_pool().system_types()})


@app.post("/api/chat")
async def api_chat(request: Request):
    pool = _get_pool()
    payload: dict[str, Any] = await request.json()
    user_id = request.headers.get("X-User-Id")
    system_type = request.headers.get("X-System-Type", "default")

    if payload.get("stream"):
        async def _stream() -> AsyncIterator[bytes]:
            # Hold the pool instance for the full duration of the stream.
            async with pool.acquire() as cache:
                async for chunk in _sync_gen_to_async(
                    cache.chat_stream(payload, user_id, system_type)
                ):
                    yield chunk
        return StreamingResponse(_stream(), media_type="application/x-ndjson")

    async with pool.acquire() as cache:
        loop = asyncio.get_event_loop()
        return JSONResponse(await loop.run_in_executor(
            None, lambda: cache.chat(payload, user_id, system_type)
        ))


@app.post("/api/generate")
async def api_generate(request: Request):
    pool = _get_pool()
    payload: dict[str, Any] = await request.json()
    system_type = request.headers.get("X-System-Type", "default")

    if payload.get("stream"):
        async def _stream() -> AsyncIterator[bytes]:
            async with pool.acquire() as cache:
                async for chunk in _sync_gen_to_async(
                    cache.completion_stream(payload, system_type)
                ):
                    yield chunk
        return StreamingResponse(_stream(), media_type="application/x-ndjson")

    async with pool.acquire() as cache:
        loop = asyncio.get_event_loop()
        return JSONResponse(await loop.run_in_executor(
            None, lambda: cache.completion(payload, system_type)
        ))


@app.post("/api/embeddings")
async def api_embeddings(request: Request):
    """Ollama-compatible embeddings endpoint. LiteLLM routes embedding calls here."""
    pool = _get_pool()
    body: dict[str, Any] = await request.json()
    prompt = body.get("prompt", "")
    if not prompt:
        raise HTTPException(status_code=422, detail="'prompt' is required")
    async with pool.acquire() as cache:
        loop = asyncio.get_event_loop()
        try:
            embedding = await loop.run_in_executor(None, lambda: cache.embed(prompt))
        except Exception as exc:
            log.error("Embedding failed: %s", exc)
            raise HTTPException(status_code=500, detail=f"Embedding failed: {exc}")
    return JSONResponse({"model": "llama3", "embedding": embedding})


@app.get("/api/tags")
async def api_tags():
    pool = _get_pool()
    info = pool.status()
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
    if _pool is None:
        return JSONResponse({"status": "not_loaded"}, status_code=503)
    return JSONResponse({**_pool.status(), "status": "ready"})


if __name__ == "__main__":
    uvicorn.run("api:app", host="0.0.0.0", port=11434, reload=False)
