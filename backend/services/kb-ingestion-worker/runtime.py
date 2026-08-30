"""Runtime orchestration for kb-ingestion-worker."""
from __future__ import annotations

import asyncio

from bootstrap import WorkerRuntime
from utils.worker import consume_loop


def _require_ready(runtime: WorkerRuntime) -> None:
    if runtime.docling_parser is None or runtime.graph is None or runtime.result_cache is None:
        raise RuntimeError("worker runtime has not been started")


async def run_forever(runtime: WorkerRuntime) -> None:
    _require_ready(runtime)
    assert runtime.docling_parser is not None
    assert runtime.graph is not None

    conflict_task = asyncio.create_task(
        runtime.conflict_loop(
            postgres=runtime.postgres,
            qdrant_svc=runtime.qdrant,
            graph=runtime.graph,
            model=runtime.model,
            mongo=runtime.mongo,
        ),
        name="conflict-resolver",
    )

    try:
        await consume_loop(
            consumer=runtime.consumer,
            minio=runtime.minio,
            postgres=runtime.postgres,
            mongo=runtime.mongo,
            qdrant_svc=runtime.qdrant,
            graph=runtime.graph,
            model=runtime.model,
            docling_parser=runtime.docling_parser,
            producer=runtime.producer,
            result_cache=runtime.result_cache,
        )
    finally:
        conflict_task.cancel()
        await asyncio.gather(conflict_task, *runtime.health_check_tasks, return_exceptions=True)
        await runtime.postgres.close()
        await runtime.qdrant.close()
        await runtime.neo4j.close()
        await runtime.mongo.close()
        await runtime.minio.close()
        await runtime.model.close()
        await runtime.consumer.close()
        await runtime.producer.close()
        await runtime.redis.close()
        await runtime.logger.logging("Worker shutdown complete")
