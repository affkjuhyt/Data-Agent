"""A1.5 Idempotency-Key: a POST sent again with the same key within 24 hours gets the first
response back instead of running twice; the same key with another body answers 422
idempotency_mismatch.

The key is claimed in integration.idempotency_key (svc_integration, under the caller's
organisation) and committed before the handler runs, so a duplicate sent while the first is still
running sees the claim and answers 409 instead of racing it. The first response's status, a few
headers and its body are kept in a ResponseStore for 24 hours (Redis in deployment: the table holds
only `response_ref`, design tab 3b); a 5xx releases the claim so the client may retry.

The key belongs to the caller: principal_id is derived from the token's subject (a user's or a
service account's), so two people using the same key never see each other's responses. A request
whose token does not verify is passed on untouched and refused by the route's own authentication.

PL-08, fail closed (redis_client.fail_open): a keyed POST whose store is missing or does not answer
a ping gets 503 before its key is claimed or its handler runs, so nothing is written; a replay whose
stored body cannot be read gets 503, not a conflict. When the store fails only after the handler
succeeded, the claim is kept with the status and no body: a retry then answers 409 instead of
running the write a second time. With API_REDIS_FAIL_OPEN the key is ignored without a store and
that claim is released, as before.
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import json
import logging
import os
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from fastapi import Request, Response
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from libs.dataagent_db import scoped_session, uuid7
from libs.dataagent_db.models import integration as ig
from services.api.kernel.auth import bearer_claims
from services.api.kernel.problems import Problem, problem_response
from services.api.kernel.redis_client import fail_open, unavailable

logger = logging.getLogger(__name__)

HEADER = "Idempotency-Key"
MAX_KEY_LENGTH = 128
TTL = datetime.timedelta(hours=24)
ROLE = "svc_integration"
# Replayed with the body; everything else of the first response is not part of the contract.
KEPT_HEADERS = ("content-type", "location", "etag")
# Fixed namespace for principal ids derived from token subjects (never changes: it keys stored rows).
_SUBJECT_NAMESPACE = uuid.UUID("6f1c2b0e-5d7a-4e43-9a51-0c8a1f3d2b77")


class ResponseStore(Protocol):
    async def put(self, ref: str, value: str, ttl: datetime.timedelta) -> None: ...

    async def get(self, ref: str) -> str | None: ...

    async def ping(self) -> None:
        """Raise when the store cannot be reached."""
        ...


class RedisResponseStore:
    def __init__(self, url: str) -> None:
        from services.api.kernel.redis_client import redis_from_url

        self._redis = redis_from_url(url)

    async def put(self, ref: str, value: str, ttl: datetime.timedelta) -> None:
        await self._redis.set(ref, value, ex=int(ttl.total_seconds()))

    async def get(self, ref: str) -> str | None:
        value = await self._redis.get(ref)
        return str(value) if value is not None else None

    async def ping(self) -> None:
        await self._redis.ping()


def response_store_from_env() -> ResponseStore | None:
    url = os.environ.get("REDIS_URL")
    if not url:
        if fail_open():
            logger.warning("api | REDIS_URL is not set: Idempotency-Key is not honoured")
        else:
            logger.error("api | REDIS_URL is not set: every POST with an Idempotency-Key gets 503")
        return None
    return RedisResponseStore(url)


def _caller(claims: dict[str, Any]) -> tuple[uuid.UUID, uuid.UUID] | None:
    try:
        org_id = uuid.UUID(str(claims["org_id"]))
        subject = str(claims["sub"])
    except (KeyError, ValueError):
        return None
    return org_id, uuid.uuid5(_SUBJECT_NAMESPACE, subject)


def _request_hash(request: Request, body: bytes) -> str:
    digest = hashlib.sha256()
    for part in (
        request.method.encode(),
        request.url.path.encode(),
        request.url.query.encode(),
        # The same body in another Data Space is another request.
        request.headers.get("X-Data-Space", "").encode(),
    ):
        digest.update(part + b"\n")
    digest.update(body)
    return digest.hexdigest()


def _replay(stored: str) -> Response:
    kept = json.loads(stored)
    response = Response(
        content=base64.b64decode(kept["body"]),
        status_code=int(kept["status"]),
        headers=kept["headers"],
    )
    response.headers["Idempotent-Replayed"] = "true"
    return response


@dataclass(frozen=True)
class _Claim:
    """Exactly one is set: run the request under row_id, answer now, or replay the stored ref."""

    row_id: uuid.UUID | None = None
    answer: Response | None = None
    replay_ref: str | None = None


async def _claim(org_id: uuid.UUID, principal_id: uuid.UUID, key: str, request_hash: str) -> _Claim:
    now = datetime.datetime.now(datetime.UTC)
    ik = ig.IdempotencyKey
    async with scoped_session(ROLE, org_id) as session:
        # An expired claim of the same key is gone, so the key may be used again.
        await session.execute(
            delete(ik).where(
                ik.org_id == org_id,
                ik.principal_id == principal_id,
                ik.key == key,
                ik.expires_at <= now,
            )
        )
        row_id = uuid7()
        inserted = await session.scalar(
            insert(ik)
            .values(
                id=row_id,
                org_id=org_id,
                key=key,
                principal_id=principal_id,
                request_hash=request_hash,
                expires_at=now + TTL,
            )
            .on_conflict_do_nothing(index_elements=["org_id", "principal_id", "key"])
            .returning(ik.id)
        )
        if inserted is not None:
            return _Claim(row_id=inserted)
        first = await session.scalar(
            select(ik).where(ik.org_id == org_id, ik.principal_id == principal_id, ik.key == key)
        )
    if first is None:  # released between the insert and the read
        return _Claim(
            answer=_problem(409, "conflict", "The first request with this key just failed; retry.")
        )
    if first.request_hash != request_hash:
        return _Claim(
            answer=_problem(
                422, "idempotency_mismatch", "This Idempotency-Key was sent with another request."
            )
        )
    if first.response_ref is None:
        if first.response_status is not None:
            # Done, but its response could not be kept (PL-08): never run it a second time.
            return _Claim(
                answer=_problem(
                    409,
                    "conflict",
                    "The first request with this key succeeded but its response was not kept; "
                    "it is not run again.",
                )
            )
        return _Claim(
            answer=_problem(409, "conflict", "The first request with this key is still running.")
        )
    return _Claim(replay_ref=first.response_ref)


class IdempotentAnswer(Exception):
    """Raised by replay_guard; its handler returns the response decided by the middleware."""

    def __init__(self, response: Response) -> None:
        self.response = response


async def replay_guard(request: Request) -> None:
    """Route dependency (catalogue.endpoint puts it after the auth and capability guards)."""
    answer = getattr(request.state, "idempotent_answer", None)
    if answer is not None:
        raise IdempotentAnswer(answer)


async def on_idempotent_answer(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, IdempotentAnswer)
    return exc.response


def _problem(status: int, code: str, detail: str) -> Response:
    return problem_response(Problem(status, code, detail))


async def idempotency_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
    store: ResponseStore | None,
) -> Response:
    key = request.headers.get(HEADER)
    if key is None or request.method != "POST" or not request.url.path.startswith("/v1/"):
        return await call_next(request)
    if store is None and fail_open():
        return await call_next(request)
    if not key or len(key) > MAX_KEY_LENGTH:
        return problem_response(
            Problem(400, "invalid_request", f"{HEADER} is 1 to {MAX_KEY_LENGTH} characters.")
        )
    claims = await bearer_claims(request)
    if claims is None:
        return await call_next(request)
    caller = _caller(claims)
    if caller is None:
        return await call_next(request)
    org_id, principal_id = caller
    if store is None:
        return problem_response(unavailable(HEADER))
    if not fail_open():
        # Checked before the claim: a write that could not be guarded is not run at all.
        try:
            await store.ping()
        except Exception:  # noqa: BLE001 - any store failure refuses the write
            logger.warning("api | idempotency response store unavailable", exc_info=True)
            return problem_response(unavailable(HEADER))
    body = await request.body()
    claim = await _claim(org_id, principal_id, key, _request_hash(request, body))
    if claim.row_id is None:
        # Not answered here: the route runs its authentication and capability check first, and
        # replay_guard answers after them, so a caller who has since lost access gets their 401
        # or 403, never the stored response.
        answer = claim.answer
        if claim.replay_ref is not None:
            try:
                stored = await store.get(claim.replay_ref)
            except Exception:  # noqa: BLE001 - Redis outage
                logger.warning(
                    "api | idempotency response store unavailable on replay", exc_info=True
                )
                if not fail_open():
                    request.state.idempotent_answer = problem_response(unavailable(HEADER))
                    return await call_next(request)
                stored = None
            answer = (
                _replay(stored)
                if stored is not None
                else _problem(
                    409, "conflict", "The first response with this key is no longer kept."
                )
            )
        request.state.idempotent_answer = answer
        return await call_next(request)
    try:
        response = await call_next(request)
    except BaseException:
        await _release(org_id, claim.row_id)
        raise
    return await _finish(store, org_id, claim.row_id, response)


async def _release(org_id: uuid.UUID, row_id: uuid.UUID) -> None:
    async with scoped_session(ROLE, org_id) as session:
        await session.execute(delete(ig.IdempotencyKey).where(ig.IdempotencyKey.id == row_id))


async def _finish(
    store: ResponseStore, org_id: uuid.UUID, row_id: uuid.UUID, response: Response
) -> Response:
    chunks = [chunk async for chunk in response.body_iterator]  # type: ignore[attr-defined]
    content = b"".join(c if isinstance(c, bytes) else c.encode() for c in chunks)
    headers = {k: v for k, v in response.headers.items() if k.lower() in KEPT_HEADERS}
    if response.status_code >= 500:
        # A failure is not an answer: release the key so a retry runs again.
        await _release(org_id, row_id)
    else:
        # The handler already succeeded. A Redis blip must not turn that into
        # ``internal_error`` for the client (Ask always sends Idempotency-Key): the 2xx body is
        # returned either way. Fail closed, the claim keeps the status with no body, so a retry
        # answers 409 rather than running the write again; fail open, it is released as before.
        ref = f"idempotency:{row_id}"
        try:
            await store.put(
                ref,
                json.dumps(
                    {
                        "status": response.status_code,
                        "headers": headers,
                        "body": base64.b64encode(content).decode(),
                    }
                ),
                TTL,
            )
        except Exception:  # noqa: BLE001 - store outage after success
            logger.warning(
                "api | idempotency response store unavailable after success", exc_info=True
            )
            if fail_open():
                await _release(org_id, row_id)
            else:
                async with scoped_session(ROLE, org_id) as session:
                    row = await session.get(ig.IdempotencyKey, row_id)
                    if row is not None:
                        row.response_status = response.status_code
        else:
            ik = ig.IdempotencyKey
            async with scoped_session(ROLE, org_id) as session:
                row = await session.get(ik, row_id)
                if row is not None:
                    row.response_status, row.response_ref = response.status_code, ref
    return Response(
        content=content,
        status_code=response.status_code,
        headers=dict(response.headers),
        media_type=response.media_type,
    )
