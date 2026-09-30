"""Consumes kb.promote.silver and runs the bronze -> silver pipeline."""
from __future__ import annotations

import asyncio
import logging

from libs.ingestion import config as cfg
from libs.ingestion.runtime import Component, WorkerRuntime, build_runtime, startup
from services.kb_backend.workers.promote_silver.handler import build_silver_handler

log = logging.getLogger(__name__)

COMPONENTS = {
    Component.POSTGRES,
    Component.MONGO,
    Component.MINIO,
    Component.MODEL,
    Component.KAFKA,
    Component.DOCLING,
}


async def main() -> None:
    runtime: WorkerRuntime = build_runtime()
    try:
        await startup(runtime, COMPONENTS)
    except Exception as exc:
        await runtime.logger.logging(f"promote_silver startup failed: {exc}", level="ERROR")
        await runtime.stage_record.unwind()
        raise

    await runtime.logger.logging(f"promote_silver ready — listening on {cfg.KAFKA_TOPIC_SILVER}")
    await runtime.consumer.start_consuming(
        topics=[cfg.KAFKA_TOPIC_SILVER],
        handler=build_silver_handler(runtime),
        concurrency=cfg.SILVER_CONCURRENCY,
    )


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
