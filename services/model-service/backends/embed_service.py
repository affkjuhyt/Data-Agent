"""Embedding cache + dynamic batching — lever #1 of KB-SCALE-10K.md.

The measured retrieval cost is 66% query-embedding (594 ms cold). Two cheap wins:

* **Cache** repeated query embeddings (search traffic is Zipfian — the same
  queries recur) so most requests skip the model entirely.
* **Dynamically batch** the cache-*misses* that arrive concurrently into a single
  ``model.encode([...])`` call — 5-10x more throughput per GPU/CPU under load,
  because one forward pass amortises across many in-flight requests.

In-process implementations here (stdlib only). For a multi-replica deployment the
cache grows an L2 (Redis) behind the same ``EmbeddingCache`` interface — the call
sites don't change.
"""
from __future__ import annotations

import asyncio
import threading
from collections import OrderedDict
from typing import Any, Callable, Optional, cast

EncodeFn = Callable[[list[str]], list[list[float]]]
_CacheKey = tuple[str, str]  # (model_key, normalized_text)


def normalize(text: str) -> str:
    """Whitespace-fold for a stable cache key (does not alter the embedded text
    beyond collapsing runs of whitespace — safe, and lifts the hit rate)."""
    return " ".join(text.split())


class EmbeddingCache:
    """Thread-safe LRU: (model_key, normalized_text) -> vector."""

    def __init__(self, maxsize: int) -> None:
        self._max = max(1, maxsize)
        self._data: "OrderedDict[_CacheKey, list[float]]" = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: _CacheKey) -> Optional[list[float]]:
        with self._lock:
            vec = self._data.get(key)
            if vec is None:
                self.misses += 1
                return None
            self._data.move_to_end(key)
            self.hits += 1
            return vec

    def put(self, key: _CacheKey, vec: list[float]) -> None:
        with self._lock:
            self._data[key] = vec
            self._data.move_to_end(key)
            while len(self._data) > self._max:
                self._data.popitem(last=False)

    def stats(self) -> dict[str, Any]:
        with self._lock:
            total = self.hits + self.misses
            return {
                "size": len(self._data),
                "max": self._max,
                "hits": self.hits,
                "misses": self.misses,
                "hit_rate": round(self.hits / total, 4) if total else 0.0,
            }


class DynamicBatcher:
    """Coalesce concurrent embed requests into one ``encode`` call.

    Each ``submit`` enqueues its texts + a future; a single background loop pulls
    one item then greedily drains more for up to ``max_wait_ms`` (or until
    ``max_batch`` texts), runs the (blocking) encode in a thread, and fans the
    ordered results back out to each caller's future.
    """

    def __init__(self, encode_fn: EncodeFn, max_batch: int, max_wait_ms: float) -> None:
        self._encode = encode_fn
        self._max_batch = max(1, max_batch)
        self._max_wait = max(0.0, max_wait_ms) / 1000.0
        self._q: "asyncio.Queue[tuple[list[str], asyncio.Future[list[list[float]]]]]" = asyncio.Queue()
        self._started = False
        self.batches = 0
        self.batched_texts = 0

    def start(self) -> None:
        if not self._started:
            self._started = True
            asyncio.create_task(self._run(), name="embed-batcher")

    async def submit(self, texts: list[str]) -> list[list[float]]:
        loop = asyncio.get_running_loop()
        fut: "asyncio.Future[list[list[float]]]" = loop.create_future()
        await self._q.put((texts, fut))
        return await fut

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            texts0, fut0 = await self._q.get()
            batch: list[tuple[list[str], asyncio.Future[list[list[float]]]]] = [(texts0, fut0)]
            count = len(texts0)
            deadline = loop.time() + self._max_wait
            while count < self._max_batch:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    break
                try:
                    item = await asyncio.wait_for(self._q.get(), remaining)
                except asyncio.TimeoutError:
                    break
                batch.append(item)
                count += len(item[0])

            all_texts: list[str] = []
            spans: list[tuple[int, int, asyncio.Future[list[list[float]]]]] = []
            for texts, fut in batch:
                spans.append((len(all_texts), len(texts), fut))
                all_texts.extend(texts)

            try:
                vecs = await loop.run_in_executor(None, self._encode, all_texts)
                self.batches += 1
                self.batched_texts += len(all_texts)
                for start, n, fut in spans:
                    if not fut.done():
                        fut.set_result(vecs[start:start + n])
            except Exception as exc:  # noqa: BLE001 — propagate to every waiter
                for _start, _n, fut in spans:
                    if not fut.done():
                        fut.set_exception(exc)

    def stats(self) -> dict[str, Any]:
        return {
            "batches": self.batches,
            "batched_texts": self.batched_texts,
            "avg_batch": round(self.batched_texts / self.batches, 2) if self.batches else 0.0,
            "max_batch": self._max_batch,
            "max_wait_ms": round(self._max_wait * 1000, 1),
        }


class EmbedService:
    """Cache-then-batch wrapper around an embedding ``encode`` function."""

    def __init__(
        self,
        encode_fn: EncodeFn,
        *,
        cache_size: int,
        batch_max: int,
        batch_wait_ms: float,
        cache_enabled: bool = True,
        batch_enabled: bool = True,
    ) -> None:
        self._encode = encode_fn
        self._cache = EmbeddingCache(cache_size) if cache_enabled else None
        self._batcher = DynamicBatcher(encode_fn, batch_max, batch_wait_ms) if batch_enabled else None

    def start(self) -> None:
        if self._batcher is not None:
            self._batcher.start()

    async def embed(self, model_key: str, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        results: list[Optional[list[float]]] = [None] * len(texts)
        miss_idx: list[int] = []
        miss_txt: list[str] = []
        for i, text in enumerate(texts):
            key = (model_key, normalize(text))
            cached = self._cache.get(key) if self._cache is not None else None
            if cached is not None:
                results[i] = cached
            else:
                miss_idx.append(i)
                miss_txt.append(text)

        if miss_txt:
            if self._batcher is not None:
                vecs = await self._batcher.submit(miss_txt)
            else:
                loop = asyncio.get_running_loop()
                vecs = await loop.run_in_executor(None, self._encode, miss_txt)
            for j, i in enumerate(miss_idx):
                results[i] = vecs[j]
                if self._cache is not None:
                    self._cache.put((model_key, normalize(texts[i])), vecs[j])

        return [cast(list[float], r) for r in results]

    def stats(self) -> dict[str, Any]:
        return {
            "cache": self._cache.stats() if self._cache is not None else {"enabled": False},
            "batcher": self._batcher.stats() if self._batcher is not None else {"enabled": False},
        }
