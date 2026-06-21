from __future__ import annotations

import asyncio
import threading
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Generic, Iterator, Protocol, TypeVar, runtime_checkable
from llama_cpp import LlamaState
from _logging import get_logger
from llama import (
    Llama3Model,
    UserMessageList,
    parse_chat_payload,
    parse_generate_payload,
)

log = get_logger("kv-cache")

T = TypeVar("T")
@runtime_checkable
class PrecachingStorage(Protocol[T]):
    def save(self, key: str, value: T) -> None: ...
    def get(self, key: str) -> T | None: ...
    def delete(self, key: str) -> None: ...
    def has(self, key: str) -> bool: ...
    def keys(self) -> list[str]: ...
    def clear(self) -> None: ...


class InMemoryStorage(Generic[T]):
    def __init__(self) -> None:
        self._store: dict[str, T] = {}

    def save(self, key: str, value: T) -> None:
        self._store[key] = value

    def get(self, key: str) -> T | None:
        return self._store.get(key)

    def delete(self, key: str) -> None:
        self._store.pop(key, None)

    def has(self, key: str) -> bool:
        return key in self._store

    def keys(self) -> list[str]:
        return list(self._store.keys())

    def clear(self) -> None:
        self._store.clear()


def _delta_input(
        messages: list[dict],
        offset: int,
        original_user_input: Any,
) -> Any:
    return UserMessageList(messages, offset=offset) if offset else original_user_input


class KVCacheManager:
    __slots__ = ("_client", "system", "conversation", "_lock", "_empty_state")

    def __init__(
            self,
            client: Llama3Model,
            system_storage: PrecachingStorage[LlamaState] | None = None,
            conversation_storage: PrecachingStorage[tuple[LlamaState, int]] | None = None,
    ) -> None:
        self._client = client
        self.system: PrecachingStorage[LlamaState] = system_storage or InMemoryStorage()
        self.conversation: PrecachingStorage[tuple[LlamaState, int]] = conversation_storage or InMemoryStorage()
        self._lock = threading.Lock()
        self._empty_state: LlamaState | None = None

    def init_empty_state(self) -> None:
        """Capture the model's initial state (no tokens evaluated). Used as fallback when no
        system prompt has been pre-registered, so direct API calls work without setup."""
        with self._lock:
            self._empty_state = self._client.persist_state()
        log.info("Empty KV state saved — direct calls are now possible without pre-registration.")

    def get_client(self):
        return self._client

    def register_system_prompt(self, system_type: str, system_prompt: str) -> None:
        log.info("Computing KV snapshot for system type %r …", system_type)
        with self._lock:
            self.system.save(system_type, self._client.precaching_system_prompt(system_prompt))
        log.info("System type %r registered", system_type)

    def _save_conversation(self, user_id: str, message_count: int) -> None:
        self.conversation.save(user_id, (self._client.persist_state(), message_count))

    def _kv_start(self, user_id: str | None, system_type: str) -> tuple[LlamaState, int, bool]:
        """Returns (state, message_offset, is_fresh).
        is_fresh=True means no system KV snapshot was loaded — callers must include
        system messages in the text they feed to the model."""
        if user_id and self.conversation.has(user_id):
            state, offset = self.conversation.get(user_id)  # type: ignore[misc]
            return state, offset, False
        state = self.system.get(system_type)
        if state is not None:
            return state, 0, False
        if self._empty_state is None:
            raise RuntimeError(
                "Model not initialized — call init_empty_state() after loading the model."
            )
        return self._empty_state, 0, True

    def embed(self, text: str) -> list[float]:
        with self._lock:
            return self._client.embed(text)

    def chat(
            self,
            payload: dict[str, Any],
            user_id: str | None = None,
            system_type: str = "default",
    ) -> dict[str, Any]:
        kwargs = parse_chat_payload(payload)
        messages = kwargs["user_input"].messages
        formatter = kwargs["formatter"]
        gen_kwargs = kwargs["gen_kwargs"]
        max_tok = kwargs["max_tokens"]

        with self._lock:
            state, offset, is_fresh = self._kv_start(user_id=user_id, system_type=system_type)
            self._client.reload_state(state)

            # Fresh start: no system KV snapshot loaded — include system message in the prompt.
            # Pre-cached start: system tokens already in context — only send new user turns.
            if is_fresh:
                user_input = UserMessageList(messages, offset=0, include_system=True)
            else:
                user_input = _delta_input(messages, offset, kwargs["user_input"])

            input_tokens = self._client.prefill(user_input)
            result = self._client.decode_reply(input_tokens, formatter, gen_kwargs, max_tok)
            if user_id:
                self._client.eval_assistant_reply(result["message"]["content"])
                self._save_conversation(user_id, len(messages) + 1)
            return result

    def chat_stream(
            self,
            payload: dict[str, Any],
            user_id: str | None = None,
            system_type: str = "default",
    ) -> Iterator[dict[str, Any]]:
        kwargs = parse_chat_payload(payload)
        messages = kwargs["user_input"].messages
        formatter = kwargs["formatter"]
        gen_kwargs = kwargs["gen_kwargs"]
        max_tokens = kwargs["max_tokens"]

        with self._lock:
            state, offset, is_fresh = self._kv_start(user_id=user_id, system_type=system_type)
            self._client.reload_state(state)

            if is_fresh:
                user_input = UserMessageList(messages, offset=0, include_system=True)
            else:
                user_input = _delta_input(messages, offset, kwargs["user_input"])

            self._client.prefill(user_input)
            reply_parts: list[str] = []
            for chunk in self._client.stream_decode_reply(
                    formatter=formatter, gen_kwargs=gen_kwargs, max_tokens=max_tokens):
                reply_parts.append(chunk["message"]["content"])
                yield chunk
            if user_id:
                self._client.eval_assistant_reply("".join(reply_parts))
                self._save_conversation(user_id, len(messages) + 1)
        # LiteLLM's Ollama provider requires a final done:true chunk to stop
        # reading the NDJSON stream and return the accumulated content.
        yield formatter.format("")

    def completion(
            self,
            payload: dict[str, Any],
            system_type: str = "default",
    ) -> dict[str, Any]:
        kwargs = parse_generate_payload(payload)
        with self._lock:
            # For generate mode, LiteLLM already merges system+user into a single prompt string,
            # so fresh vs. pre-cached doesn't change anything here.
            state, _, _fresh = self._kv_start(None, system_type)
            self._client.reload_state(state)
            return self._client.response(**kwargs)

    def completion_stream(
            self,
            payload: dict[str, Any],
            system_type: str = "default",
    ) -> Iterator[dict[str, Any]]:
        kwargs = parse_generate_payload(payload)
        with self._lock:
            state, _, _fresh = self._kv_start(None, system_type)
            self._client.reload_state(state)
            yield from self._client.stream_response(**kwargs)
        yield kwargs["formatter"].format("")


class SharedSystemRegistry:
    """Thread-safe store of system-prompt name → text, shared by all pool instances.
    Each instance uses this to lazily pre-warm KV snapshots it hasn't computed yet."""

    def __init__(self) -> None:
        self._prompts: dict[str, str] = {}
        self._lock = threading.Lock()

    def register(self, name: str, text: str) -> None:
        with self._lock:
            self._prompts[name] = text

    def items(self) -> list[tuple[str, str]]:
        with self._lock:
            return list(self._prompts.items())

    def keys(self) -> list[str]:
        with self._lock:
            return list(self._prompts.keys())


class ModelPool:
    """Fixed-size pool of KVCacheManagers backed by an asyncio.Queue.

    Requests call acquire(), which blocks until a free instance is available,
    then yields it. When the caller's context exits the instance is returned to
    the queue so the next waiting request can pick it up.

    All instances share a SharedSystemRegistry so a system prompt registered once
    is lazily pre-warmed on each instance the first time it handles a request after
    that registration.
    """

    def __init__(self, pool_size: int) -> None:
        self._pool_size = pool_size
        self._queue: asyncio.Queue[KVCacheManager] = asyncio.Queue(maxsize=pool_size)
        self._registry = SharedSystemRegistry()
        self._model_info: dict[str, Any] = {}

    async def build(self) -> None:
        loop = asyncio.get_event_loop()
        for idx in range(self._pool_size):
            log.info("Building model instance %d/%d …", idx + 1, self._pool_size)
            model = Llama3Model()
            await loop.run_in_executor(None, model.load)
            cache = KVCacheManager(model)
            await loop.run_in_executor(None, cache.init_empty_state)
            if idx == 0:
                self._model_info = cache.get_client().health()
            await self._queue.put(cache)
        log.info("Model pool ready — %d instance(s)", self._pool_size)

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[KVCacheManager]:
        """Pull a free instance from the pool, sync any pending system prompts,
        yield it, then return it to the pool. Blocks if all instances are busy."""
        cache = await self._queue.get()
        await self._sync_prompts(cache)
        try:
            yield cache
        finally:
            await self._queue.put(cache)

    async def _sync_prompts(self, cache: KVCacheManager) -> None:
        loop = asyncio.get_event_loop()
        for name, text in self._registry.items():
            if not cache.system.has(name):
                log.info("Instance syncing system prompt %r …", name)
                await loop.run_in_executor(None, cache.register_system_prompt, name, text)

    async def register_system_prompt(self, name: str, text: str) -> None:
        """Store text in shared registry. Each instance pre-warms lazily on next acquire."""
        self._registry.register(name, text)

    def system_types(self) -> list[str]:
        return self._registry.keys()

    def status(self) -> dict[str, Any]:
        return {
            **self._model_info,
            "pool_size": self._pool_size,
            "available": self._queue.qsize(),
        }
