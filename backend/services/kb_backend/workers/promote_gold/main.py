"""Consumes kb.promote.gold and runs the silver -> gold pipeline."""
from __future__ import annotations

import asyncio
import logging

from libs.ingestion import config as cfg
from libs.ingestion.runtime import Component, WorkerRuntime, build_runtime, startup
from services.kb_backend.workers.promote_gold.handler import build_gold_handler

log = logging.getLogger(__name__)

COMPONENTS = {
    Component.POSTGRES,
    Component.QDRANT,
    Component.NEO4J,
    Component.MONGO,
    Component.MODEL,
    Component.KAFKA,
    Component.REDIS,
    Component.GRAPH,
}


async def main() -> None:
    runtime: WorkerRuntime = build_runtime()
    runtime.outbox_name = "kb-promote-gold"
    try:
        await startup(runtime, COMPONENTS)
    except Exception as exc:
        await runtime.logger.logging(f"promote_gold startup failed: {exc}", level="ERROR")
        await runtime.stage_record.unwind()
        raise

    await runtime.logger.logging(f"promote_gold ready — listening on {cfg.KAFKA_TOPIC_GOLD}")
    await runtime.consumer.start_consuming(
        topics=[cfg.KAFKA_TOPIC_GOLD],
        handler=build_gold_handler(runtime),
        concurrency=cfg.GOLD_CONCURRENCY,
    )


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
