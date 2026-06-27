import asyncio
import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

import config as cfg
from backends import registry, bootstrap, sync_with_retry, chat_status
from routers import embed, generate, health, rerank

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("model-service")


def _probe_llm_backend() -> None:
    """Surface the LLM gateway state once at startup (derived from the registry, no
    network) so a missing/broken REMOTE_LLM_URL is loud in the logs instead of only a
    per-request 503. The actual reachability check happened during bootstrap/sync."""
    status = chat_status(registry)
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
            "LLM gateway configured (REMOTE_LLM_URL=%s) but the chat model isn't "
            "registered yet — background discovery will retry; /generate errors until then.",
            cfg.REMOTE_LLM_URL,
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Synchronous, no-DB: register default embed/rerank (local unless REMOTE_*_URL is
    # set) + chat, so the service is ready immediately. Loads local model weights here.
    bootstrap(registry)
    # Background: pull every embed/rerank model from Postgres (KBModel) so their UUIDs
    # resolve — Postgres may not be ready yet, so this retries without blocking serving.
    asyncio.create_task(sync_with_retry(registry), name="registry-sync")
    _probe_llm_backend()
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
