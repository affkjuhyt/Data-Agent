import asyncio

from services.auth_api.infrastructure.config import POOL_MAX, log
from services.auth_api.infrastructure.db import ensure_settings_table
from services.auth_api.services.kong_sync import sync_kong_ip_restrictions
from services.auth_api.services.rotation import auto_rotation_loop


async def sync_kong_on_startup() -> None:
    await asyncio.sleep(15)
    try:
        await asyncio.to_thread(sync_kong_ip_restrictions, "")
        log.info("startup_kong_sync_done")
    except Exception as exc:
        log.warning("startup_kong_sync_failed", error=str(exc))


async def startup() -> None:
    try:
        import anyio

        anyio.to_thread.current_default_thread_limiter().total_tokens = POOL_MAX
    except Exception as exc:
        log.warning("threadpool_limiter_set_failed", error=str(exc))

    ensure_settings_table()
    asyncio.create_task(auto_rotation_loop())
    asyncio.create_task(sync_kong_on_startup())
