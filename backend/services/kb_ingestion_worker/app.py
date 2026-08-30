async def main() -> None:
    from services.kb_ingestion_worker.bootstrap import build_runtime, startup
    from services.kb_ingestion_worker.runtime import run_forever

    runtime = build_runtime()
    try:
        await startup(runtime)
    except Exception as exc:
        await runtime.logger.logging(f"Worker startup failed: {exc}", level="ERROR")
        await runtime.stage_record.unwind()
        raise

    await run_forever(runtime)


def run() -> None:
    import asyncio

    asyncio.run(main())
