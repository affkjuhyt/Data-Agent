"""Consumes kb.resolve.conflict and runs the background conflict-resolution poll."""
from __future__ import annotations

import asyncio
import logging

from libs.ingestion import config as cfg
from libs.ingestion.runtime import Component, WorkerRuntime, build_runtime, startup
from services.kb_backend.workers.conflict_resolution.handler import build_conflict_handler
from services.kb_backend.workers.conflict_resolution.polling_loop import conflict_resolve_loop

log = logging.getLogger(__name__)

COMPONENTS = {
    Component.POSTGRES,
    Component.QDRANT,
    Component.NEO4J,
    Component.MONGO,
    Component.MODEL,
    Component.KAFKA,
    Component.GRAPH,
}


async def main() -> None:
    runtime: WorkerRuntime = build_runtime()
    try:
        await startup(runtime, COMPONENTS)
    except Exception as exc:
        await runtime.logger.logging(f"conflict_resolution startup failed: {exc}", level="ERROR")
        await runtime.stage_record.unwind()
        raise

    poll_task = asyncio.create_task(
        conflict_resolve_loop(
            postgres=runtime.postgres,
            qdrant_svc=runtime.qdrant,
            graph=runtime.graph,
            model=runtime.model,
            mongo=runtime.mongo,
        ),
        name="conflict-poll",
    )

    await runtime.logger.logging(
        f"conflict_resolution ready — listening on {cfg.KAFKA_TOPIC_CONFLICT_RESOLVE}"
    )
    try:
        await runtime.consumer.start_consuming(
            topics=[cfg.KAFKA_TOPIC_CONFLICT_RESOLVE],
            handler=build_conflict_handler(runtime),
            concurrency=cfg.CONFLICT_CONCURRENCY,
        )
    finally:
        poll_task.cancel()
        await asyncio.gather(poll_task, return_exceptions=True)


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
