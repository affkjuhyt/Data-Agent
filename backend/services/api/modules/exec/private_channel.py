"""The private-question channel of UC-16-02 over Redis: the PrivateQuestions port of ports.py.

Contract with worker-agent (agreed 13/10/2026):
- `exec:pq:{id}` holds the question as JSON {id, org_id, owner_id, text, context, as_of,
  data_space_id}; the api LPUSHes the id to `exec:pq:queue`. worker-agent takes ids with BLMOVE
  into `exec:pq:processing` and LREMs them when done.
- worker-agent XADDs the answer's events to the stream `exec:pq:{id}:events`, each entry
  {event, data} like worker-agent's progress stream, with MAXLEN ~100.
- Every key lives 900 s (BR-193: text and answer at most 15 minutes, never in a table, the outbox
  or a log). Nothing here logs the text.
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any

from services.api.kernel.sse import Event
from services.api.modules.exec.ports import ModelUnavailable, PrivateQuestion

TTL_SECONDS = 900
QUEUE = "exec:pq:queue"


def _question_key(question_id: uuid.UUID) -> str:
    return f"exec:pq:{question_id}"


def _text(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


class RedisPrivateQuestions:
    def __init__(self, client: Any, max_waiting: int = 20):
        self._redis = client
        # The local model serves a few questions at a time; past this the asker is told to wait
        # rather than queued for longer than the 15 minutes the question lives.
        self._max_waiting = max_waiting

    async def submit(self, question: PrivateQuestion) -> None:
        if await self._redis.llen(QUEUE) >= self._max_waiting:
            raise ModelUnavailable()
        payload = {
            "id": str(question.id),
            "org_id": str(question.org_id),
            "owner_id": str(question.owner_id),
            "text": question.text,
            "context": question.context,
            "as_of": question.as_of,
            "data_space_id": str(question.data_space_id) if question.data_space_id else None,
        }
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.set(_question_key(question.id), json.dumps(payload), ex=TTL_SECONDS)
            pipe.lpush(QUEUE, str(question.id))
            await pipe.execute()

    async def events(
        self, *, org_id: uuid.UUID, owner_id: uuid.UUID, question_id: uuid.UUID
    ) -> list[Event] | None:
        raw = await self._redis.get(_question_key(question_id))
        if raw is None:
            return None
        question = json.loads(_text(raw))
        # Only its asker reads it; anyone else learns no more than that it is gone.
        if question.get("org_id") != str(org_id) or question.get("owner_id") != str(owner_id):
            return None
        entries = await self._redis.xrange(f"{_question_key(question_id)}:events", "-", "+")
        events: list[Event] = []
        # The route resumes by a number; an answer has far fewer events than MAXLEN, so the
        # position in the stream is stable for the question's life.
        for position, (_entry_id, fields) in enumerate(entries, start=1):
            fields = {_text(k): _text(v) for k, v in fields.items()}
            try:
                data = json.loads(fields.get("data", "{}"))
            except ValueError:
                continue
            if isinstance(data, dict):
                events.append(Event(str(position), fields.get("event", "progress"), data))
        return events


def private_questions_from_env() -> RedisPrivateQuestions | None:
    """The adapter when Redis is configured; None keeps the routes' 503 (model_unavailable)."""
    url = os.environ.get("REDIS_URL")
    if not url:
        return None
    import redis.asyncio as redis

    return RedisPrivateQuestions(
        redis.from_url(url, decode_responses=True),
        int(os.environ.get("EXEC_PQ_MAX_WAITING", "20")),
    )
