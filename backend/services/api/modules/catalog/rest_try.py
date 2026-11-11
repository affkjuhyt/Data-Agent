"""The REST connector's try (POST /v1/connectors/rest:try, UC-04-01) over Redis: api side.

The call itself is worker-agent's (services/worker_agent/rest_tries.py): it is the workload whose
AppRole reads a source's key in OpenBao, which the api's may write but never read
(dataagent-api-policy). The try is still answered in the request that asks for it, so nothing is
written anywhere: no outbox event, no row. Contract with the worker:

- the api SETs `connector:try:{id}` = JSON {id, org_id, data_space_id, requested_by, request}
  (TTL 60 s) and LPUSHes the id to `connector:try:queue`;
- the worker BLMOVEs ids into `connector:try:processing`, makes the call, RPUSHes one JSON answer
  to `connector:try:{id}:result` (TTL 60 s) and deletes the request: {"ok": {status,
  duration_ms, body, mapping}}, {"refused": reason} when the request may not be made, or
  {"failed": reason} when the API could not be reached;
- the api BLPOPs the answer for at most WAIT_SECONDS, then drops both keys whatever happened.

The request holds the headers the person typed (never a credential: the route refuses those) and
lives a minute in Redis only, like the private questions of UC-16-02 (exec/private_channel.py).
"""

from __future__ import annotations

import functools
import json
import os
import re
from typing import Any, Protocol
from urllib.parse import urlsplit

TTL_SECONDS = 60
QUEUE = "connector:try:queue"
# Above the worker's own 10 s limit on the call, with room for the OpenBao read and the queue.
WAIT_SECONDS = 15.0


AUTH_KINDS = frozenset({"bearer", "api_key", "oauth2_client"})
# Headers that carry a credential: the key is the secret_ref's, never a value typed in the form
# (ApiConnectPage keeps the request with the source, in clear).
CREDENTIAL_HEADERS = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "cookie",
        "x-api-key",
        "api-key",
        "apikey",
        "x-auth-token",
        "x-access-token",
    }
)
# The connection is the worker's to set (it pins the address and names the host itself).
WORKER_HEADERS = frozenset({"host", "connection", "content-length", "transfer-encoding"})
MAX_HEADERS = 20
_HEADER_NAME = re.compile(r"[A-Za-z0-9!#$%&'*+.^_`|~-]{1,100}")


class RequestRefused(ValueError):
    """The request may not be tried as it is; the message says why, in the screen's language."""


_KEY_SEGMENT = re.compile(r"[A-Za-z0-9_-]+")


def rest_key_prefix(org_id: object, data_space_id: object) -> str:
    """Where a Data Space's REST keys live in OpenBao. The key is sent as a header to a host the
    person types, so it is an allowlist: a secret anywhere else of the organisation (a database
    account, a model provider's key, an account no source uses yet) is never one. The api's
    AppRole cannot write here (dataagent-api-policy); keys are put by whoever runs OpenBao.
    worker-agent's rest_tries.py repeats the check before it reads the key."""
    return f"secret/tenants/{org_id}/rest/{data_space_id}/"


def check_rest_key(ref: str, org_id: object, data_space_id: object) -> str:
    prefix = rest_key_prefix(org_id, data_space_id)
    rest = ref[len(prefix) :] if isinstance(ref, str) and ref.startswith(prefix) else ""
    if not rest or not all(_KEY_SEGMENT.fullmatch(s) for s in rest.split("/")):
        raise RequestRefused(f"secret_ref phải là khoá REST của Data Space này, dưới {prefix}.")
    return ref


def checked_request(request: dict[str, Any]) -> dict[str, Any]:
    """What the worker is given of the form's request: https URL, query, typed headers without a
    credential, the auth kind and the secret_ref; anything else it holds is dropped."""
    url = request.get("url")
    parts = urlsplit(url) if isinstance(url, str) and len(url) <= 2_000 else None
    if parts is None or parts.scheme != "https" or not parts.hostname:
        raise RequestRefused("URL phải là địa chỉ https.")
    if parts.username or parts.password:
        raise RequestRefused("Không ghi tài khoản trong URL; dùng secret_ref.")
    query = request.get("query") or ""
    if not isinstance(query, str) or len(query) > 2_000:
        raise RequestRefused("Tham số truy vấn không hợp lệ.")
    auth = request.get("auth")
    if auth not in AUTH_KINDS:
        raise RequestRefused("Kiểu xác thực phải là bearer, api_key hoặc oauth2_client.")
    headers = request.get("headers") or []
    if not isinstance(headers, list) or len(headers) > MAX_HEADERS:
        raise RequestRefused(f"Tối đa {MAX_HEADERS} header.")
    kept: list[dict[str, str]] = []
    for item in headers:
        name = item.get("name") if isinstance(item, dict) else None
        value = item.get("value") if isinstance(item, dict) else None
        if not isinstance(name, str) or not _HEADER_NAME.fullmatch(name.strip()):
            raise RequestRefused("Tên header không hợp lệ.")
        if not isinstance(value, str) or len(value) > 1_000 or "\r" in value or "\n" in value:
            raise RequestRefused(f"Giá trị header {name.strip()} không hợp lệ.")
        lowered = name.strip().lower()
        if lowered in CREDENTIAL_HEADERS:
            # Its value is not repeated: it is the credential that must not be typed here.
            raise RequestRefused(
                f"Header {name.strip()} mang bí mật; lưu bí mật trong OpenBao và chọn secret_ref."
            )
        if lowered in WORKER_HEADERS:
            raise RequestRefused(f"Header {name.strip()} do hệ thống đặt.")
        kept.append({"name": name.strip(), "value": value})
    records_path = request.get("records_path")
    if records_path is not None and (not isinstance(records_path, str) or len(records_path) > 200):
        raise RequestRefused("Đường dẫn bản ghi không hợp lệ.")
    secret_ref = request.get("secret_ref")
    if not isinstance(secret_ref, str) or not secret_ref:
        raise RequestRefused("Chọn secret_ref chứa khoá của API.")
    return {
        "url": url,
        "query": query,
        "auth": auth,
        "secret_ref": secret_ref,
        "headers": kept,
        "records_path": records_path or None,
    }


class TriesBusy(Exception):
    """Too many tries are waiting for the worker."""


class RestTries(Protocol):
    async def run(self, job: dict[str, Any], wait_seconds: float) -> dict[str, Any] | None:
        """The worker's answer to `job`, or None when none came within `wait_seconds`."""
        ...


def _key(try_id: str) -> str:
    return f"connector:try:{try_id}"


class RedisRestTries:
    def __init__(self, client: Any, max_waiting: int = 20) -> None:
        self._redis = client
        # A try is someone waiting at a form; past this many the form is told to try again
        # rather than queued behind calls that may each take ten seconds.
        self._max_waiting = max_waiting

    async def run(self, job: dict[str, Any], wait_seconds: float) -> dict[str, Any] | None:
        if await self._redis.llen(QUEUE) >= self._max_waiting:
            raise TriesBusy()
        key = _key(str(job["id"]))
        try:
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.set(key, json.dumps(job, ensure_ascii=False), ex=TTL_SECONDS)
                pipe.lpush(QUEUE, str(job["id"]))
                await pipe.execute()
            answer = await self._redis.blpop([f"{key}:result"], timeout=wait_seconds)
        finally:
            await self._redis.delete(key, f"{key}:result")
        if answer is None:
            return None
        value = json.loads(answer[1])
        return value if isinstance(value, dict) else None


@functools.cache
def _from_url(url: str, max_waiting: int) -> RedisRestTries:
    from services.api.kernel.redis_client import redis_from_url

    return RedisRestTries(redis_from_url(url), max_waiting)


def rest_tries() -> RestTries | None:
    """The channel when Redis is configured; None makes the route answer 503."""
    url = os.environ.get("REDIS_URL", "").strip()
    if not url:
        return None
    return _from_url(url, int(os.environ.get("CONNECTOR_TRY_MAX_WAITING", "20")))
