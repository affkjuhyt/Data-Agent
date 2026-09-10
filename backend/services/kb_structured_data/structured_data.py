"""Consumes kb.structured.propose and proposes a table schema for review."""
from __future__ import annotations

import asyncio
import logging

from libs.ingestion import config as cfg
from libs.ingestion.runtime import Component, WorkerRuntime, build_runtime, startup
from services.kb_structured_data.libs.handler import build_structured_handler

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
        await runtime.logger.logging(f"structured_data startup failed: {exc}", level="ERROR")
        await runtime.stage_record.unwind()
        raise

    await runtime.logger.logging(
        f"structured_data ready — listening on {cfg.KAFKA_TOPIC_STRUCTURED_PROPOSE}"
    )
    await runtime.consumer.start_consuming(
        topics=[cfg.KAFKA_TOPIC_STRUCTURED_PROPOSE],
        handler=build_structured_handler(runtime),
        concurrency=cfg.SILVER_CONCURRENCY,
    )


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
