async def main() -> None:
    from bootstrap import build_runtime, startup
    from runtime import run_forever

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
