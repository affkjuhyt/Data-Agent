import asyncio
import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

import config as cfg
import model_sync
import models
import remote
from routers import embed, generate, health, rerank

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("model-service")


async def _probe_llm_backend() -> None:
    """Validate the LLM gateway at startup so a missing/broken REMOTE_LLM_URL is
    surfaced once in the logs — instead of only as a per-request 503 that fails
    every ingestion/lightrag LLM call. Runs in a thread (blocking urllib) and
    never blocks startup or serving."""
    status = await asyncio.to_thread(remote.probe_llm)
    if not status["configured"]:
        log.warning(
            "LLM gateway DISABLED: REMOTE_LLM_URL is not set — POST /generate will "
            "return 503 and every ingestion/lightrag LLM call will fail. Set "
            "REMOTE_LLM_URL (+ REMOTE_LLM_KEY / REMOTE_LLM_MODEL) to enable it."
        )
    elif status["ready"]:
        log.info("LLM gateway OK: %s serving model=%s", cfg.REMOTE_LLM_URL, status["model"])
    else:
        log.warning(
            "LLM gateway configured (REMOTE_LLM_URL=%s) but unreachable: %s — "
            "/generate will error until the backend is up.",
            cfg.REMOTE_LLM_URL, status.get("error"),
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    models.load(cfg.EMBED_MODEL, cfg.RERANK_MODEL)
    # Register this model in Postgres in the background — Postgres may not be
    # ready yet, so the retry loop runs independently without blocking serving.
    asyncio.create_task(model_sync.register_with_retry(), name="model-registry")
    # Probe the LLM gateway once (background) to fail loud on misconfiguration.
    asyncio.create_task(_probe_llm_backend(), name="llm-probe")
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="model-service", lifespan=lifespan)

    app.include_router(health.router)
    app.include_router(embed.router)
    app.include_router(rerank.router)
    app.include_router(generate.router)

    return app


if __name__ == "__main__":
    uvicorn.run("app:create_app", factory=True, host="0.0.0.0", port=cfg.PORT)
