"""Cross-replica graph-cache invalidation over Redis pub/sub.

Phase 1 keys the workflow_runtime graph cache by (agent_id, workflow_version_id),
so *publishing a new version* already rebuilds the graph everywhere — the new id
yields a new cache key on every replica. The gap this module closes is the
*same-version* edit (republish): the id doesn't change, so each replica keeps
serving its stale compiled graph until restart.

flow_builder calls `publish_graph_invalidation(...)` after a republish; every
workflow_runtime replica runs `run_graph_invalidation_listener(...)` and evicts
the matching cache entry. Both sides are best-effort: Redis being down must never
break a publish or a request — it only means invalidation falls back to the
version-keyed behavior (correct on the next version bump).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress

log = logging.getLogger(__name__)

# Channel carrying a single workflow_version_id per message.
GRAPH_INVALIDATE_CHANNEL = "workflow_runtime:graph-invalidate"


async def publish_graph_invalidation(redis_url: str, workflow_version_id: str) -> bool:
    """Broadcast that `workflow_version_id`'s compiled graph is stale.

    Best-effort: returns True on success, False if Redis was unreachable (logged,
    never raised) so callers can fire-and-forget from a request handler.
    """
    if not redis_url or not workflow_version_id:
        return False
    try:
        import redis.asyncio as aioredis

        client = aioredis.from_url(redis_url)
        try:
            await client.publish(GRAPH_INVALIDATE_CHANNEL, workflow_version_id)
            return True
        finally:
            await client.aclose()
    except Exception as e:  # noqa: BLE001 — invalidation is advisory, never fatal
        log.warning("graph invalidation publish failed for %s: %s", workflow_version_id, e)
        return False


async def run_graph_invalidation_listener(
    redis_url: str,
    on_invalidate: Callable[[str], None] | Callable[[str], Awaitable[None]],
    *,
    reconnect_delay: float = 5.0,
) -> None:
    """Subscribe to the invalidation channel and dispatch each id to `on_invalidate`.

    Runs until cancelled. Reconnects with a fixed delay if Redis drops, so a Redis
    restart doesn't permanently kill invalidation. `on_invalidate` may be sync or
    async; exceptions from it are logged and swallowed so one bad message can't
    tear down the listener.
    """
    if not redis_url:
        log.info("graph invalidation listener disabled (no REDIS_URL)")
        return

    import redis.asyncio as aioredis

    while True:
        client = aioredis.from_url(redis_url)
        try:
            pubsub = client.pubsub()
            await pubsub.subscribe(GRAPH_INVALIDATE_CHANNEL)
            log.info("graph invalidation listener subscribed to %s", GRAPH_INVALIDATE_CHANNEL)
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                data = message.get("data")
                wv_id = data.decode() if isinstance(data, (bytes, bytearray)) else str(data)
                if not wv_id:
                    continue
                try:
                    result = on_invalidate(wv_id)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception as e:  # noqa: BLE001
                    log.warning("on_invalidate failed for %s: %s", wv_id, e)
        except asyncio.CancelledError:
            await client.aclose()
            raise
        except Exception as e:  # noqa: BLE001 — reconnect on any transport error
            log.warning(
                "graph invalidation listener error (reconnecting in %ss): %s", reconnect_delay, e
            )
            with suppress(Exception):
                await client.aclose()
            await asyncio.sleep(reconnect_delay)
