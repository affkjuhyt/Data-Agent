"""The REST connector's try (POST /v1/connectors/rest:try, UC-04-01), worker side.

Contract with the api (services/api/modules/catalog/rest_try.py): the request waits at
`connector:try:{id}` and its id in `connector:try:queue`; this worker BLMOVEs ids into
`connector:try:processing`, makes the call and RPUSHes one answer to `connector:try:{id}:result`
(60 s), which the api is BLPOPping: {"ok": {status, duration_ms, body, mapping}}, {"refused":
reason} or {"failed": reason}, the reasons in the screen's language. Nothing is written to a
table, the outbox or a log; the key is read from OpenBao for the call and dropped.

The person types the URL, so the call is held to what cannot turn it into a way in: https only,
to a host whose every address is public (not the cluster, this host, a private range or the
cloud metadata endpoint), connected to the address that was checked (a DNS answer that changes
between the check and the connection cannot move it inside) with TLS and Host still naming the
host. One GET, no redirect followed (it would carry the key elsewhere: its status is the answer),
TIMEOUT_SECONDS in all, at most MAX_BYTES read. An OAuth client's token request is held to the
same rules.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import ipaddress
import json
import logging
import re
import socket
import time
import unicodedata
import uuid
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import httpx

from libs import pii
from services.worker_agent import questions
from services.worker_agent.query.secrets import SecretUnavailable

logger = logging.getLogger(__name__)

QUEUE = "connector:try:queue"
PROCESSING = "connector:try:processing"
TTL_SECONDS = 60
TIMEOUT_SECONDS = 10.0
MAX_BYTES = 256 * 1024
PREVIEW_CHARS = 4_000
MAX_FIELDS = 100
NESTED = "Giá trị lồng nhau, lưu dạng JSON"
EMPTY = "Bản ghi mẫu để trống trường này"
# Names that resolve inside the cluster or the host (crawler/fetch.py keeps the same list).
INTERNAL_SUFFIXES = (".localhost", ".local", ".internal", ".svc", ".cluster.local")
# Never sent from the form's headers: the key is the secret_ref's (the api refuses them first).
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
        "host",
        "connection",
        "content-length",
        "transfer-encoding",
    }
)
_KEY_SEGMENT = re.compile(r"[A-Za-z0-9_-]+")
_HEADER_NAME = re.compile(r"[A-Za-z0-9!#$%&'*+.^_`|~-]{1,100}")
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}([T ][0-9:.]+(Z|[+-]\d{2}:?\d{2})?)?")


class Refused(Exception):
    """The request may not be made; the message is the reason the screen shows."""


def _transport() -> httpx.AsyncBaseTransport | None:
    """httpx's own transport; the tests answer through a MockTransport instead."""
    return None


async def _resolve(host: str, port: int) -> list[str]:
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        return []
    return [str(info[4][0]) for info in infos]


def _public(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return ip.is_global and not (ip.is_multicast or ip.is_reserved or ip.is_unspecified)


async def checked_address(url: str) -> str:
    """The address to connect to for `url`, or Refused: not https, credentials in the URL, a host
    inside the platform by name, or one of its addresses not public. IPv4 first."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower().rstrip(".")
    if parts.scheme != "https" or not host:
        raise Refused("Chỉ gọi được địa chỉ https.")
    if parts.username or parts.password:
        raise Refused("Không ghi tài khoản trong URL; dùng secret_ref.")
    if host == "localhost" or host.endswith(INTERNAL_SUFFIXES):
        raise Refused("Không gọi được địa chỉ nội bộ; chỉ địa chỉ công khai.")
    try:
        literal: str | None = str(ipaddress.ip_address(host))
    except ValueError:
        literal = None
    addresses = [literal] if literal else await _resolve(host, parts.port or 443)
    if not addresses:
        raise Refused("Không phân giải được tên máy chủ của API.")
    if not all(_public(a) for a in addresses):
        raise Refused("Không gọi được địa chỉ nội bộ; chỉ địa chỉ công khai.")
    v4 = [a for a in addresses if ":" not in a]
    return (v4 or addresses)[0]


def _with_query(url: str, query: str) -> str:
    """The URL with the form's query appended. A `{{last_run}}` placeholder (the incremental
    load's) is a day ago for a try."""
    if not query:
        return url
    since = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=1)).isoformat(
        timespec="seconds"
    )
    query = query.lstrip("?&").replace("{{last_run}}", quote(since, safe=""))
    parts = urlsplit(url)
    joined = f"{parts.query}&{query}" if parts.query else query
    return urlunsplit((parts.scheme, parts.netloc, parts.path, joined, ""))


async def _send(
    method: str,
    url: str,
    headers: dict[str, str],
    *,
    data: dict[str, str] | None = None,
) -> tuple[int, bytes, bool]:
    """One request to the checked address of `url`'s host: (status, body up to MAX_BYTES, cut)."""
    address = await checked_address(url)
    parts = urlsplit(url)
    host = parts.hostname or ""
    pinned_host = f"[{address}]" if ":" in address else address
    netloc = f"{pinned_host}:{parts.port}" if parts.port else pinned_host
    pinned = urlunsplit((parts.scheme, netloc, parts.path or "/", parts.query, ""))
    sent = {**headers, "Host": f"{host}:{parts.port}" if parts.port else host}
    async with httpx.AsyncClient(
        timeout=TIMEOUT_SECONDS,
        follow_redirects=False,
        trust_env=False,
        transport=_transport(),
    ) as client:
        request = client.build_request(
            method, pinned, headers=sent, data=data, extensions={"sni_hostname": host}
        )
        response = await client.send(request, stream=True)
        try:
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body += chunk
                if len(body) > MAX_BYTES:
                    return response.status_code, bytes(body[:MAX_BYTES]), True
            return response.status_code, bytes(body), False
        finally:
            await response.aclose()


async def _oauth_token(fields: dict[str, str]) -> str:
    token_url = fields.get("token_url", "")
    # Its own fields only: a source account's user and password are not a client's credentials.
    client_id, client_secret = fields.get("client_id"), fields.get("client_secret")
    if not token_url or not client_id or not client_secret:
        raise Refused("Bí mật OAuth cần token_url, client_id và client_secret.")
    form = {"grant_type": "client_credentials"}
    if fields.get("scope"):
        form["scope"] = fields["scope"]
    status, body, _ = await _send(
        "POST",
        token_url,
        {"Accept": "application/json"},
        data={**form, "client_id": client_id, "client_secret": client_secret},
    )
    try:
        token = json.loads(body).get("access_token") if status == 200 else None
    except (ValueError, AttributeError):
        token = None
    if not isinstance(token, str) or not token:
        # The token endpoint's body is not repeated: it may echo the client secret.
        raise Refused(f"Máy chủ OAuth không cấp token (HTTP {status}).")
    return token


def check_rest_key(ref: str, org_id: Any, data_space_id: Any) -> None:
    """The api's allowlist again (catalog/rest_try.py check_rest_key): this worker's AppRole
    reads every secret of the organisation, so a job naming a database account or a model
    provider's key must not reach OpenBao, whatever put it on the queue."""
    try:
        prefix = f"secret/tenants/{uuid.UUID(str(org_id))}/rest/{uuid.UUID(str(data_space_id))}/"
    except ValueError as exc:
        raise Refused("Yêu cầu gọi thử không nêu Data Space.") from exc
    rest = ref[len(prefix) :] if ref.startswith(prefix) else ""
    if not rest or not all(_KEY_SEGMENT.fullmatch(s) for s in rest.split("/")):
        raise Refused(f"secret_ref phải là khoá REST của Data Space này, dưới {prefix}.")


async def _auth_headers(
    org_id: Any, data_space_id: Any, auth: str, secret_ref: str
) -> dict[str, str]:
    check_rest_key(secret_ref, org_id, data_space_id)
    try:
        fields = await questions.secrets().fields(org_id, secret_ref)
    except SecretUnavailable as exc:
        raise Refused("Không đọc được bí mật của secret_ref trong OpenBao.") from exc
    if auth == "bearer":
        # Never `password`: a source account's password would leave as a bearer token.
        token = fields.get("token") or fields.get("api_key")
        if not token:
            raise Refused("Bí mật không có token.")
        return {"Authorization": f"Bearer {token}"}
    if auth == "api_key":
        key = fields.get("api_key") or fields.get("token")
        header = fields.get("header") or "X-API-Key"
        if not key or not _HEADER_NAME.fullmatch(header):
            raise Refused("Bí mật không có api_key (và header hợp lệ nếu có).")
        return {header: key}
    if auth == "oauth2_client":
        return {"Authorization": f"Bearer {await _oauth_token(fields)}"}
    raise Refused("Kiểu xác thực phải là bearer, api_key hoặc oauth2_client.")


def _column(name: str) -> str:
    plain = unicodedata.normalize("NFKD", name.replace("đ", "d").replace("Đ", "D"))
    ascii_ = plain.encode("ascii", "ignore").decode().lower()
    column = re.sub(r"[^a-z0-9]+", "_", ascii_).strip("_")[:63]
    return column or "field"


def _type(value: Any) -> tuple[str, str | None]:
    if isinstance(value, bool):
        return "boolean", None
    if isinstance(value, int | float):
        return "number", None
    if isinstance(value, str):
        return ("date" if _ISO_DATE.fullmatch(value.strip()) else "text"), None
    if value is None:
        return "text", EMPTY
    return "text", NESTED


def mapping_of(data: Any, records_path: str | None) -> list[dict[str, Any]]:
    """The fields of the first record: under `records_path` (dotted, "result.items") when the
    request names one, else the top-level array, the first array of objects of the top-level
    object, or that object itself. Paths read as the mapping's: "data[].card_id"."""
    prefix = ""
    node = data
    if records_path:
        for part in records_path.strip(".").split("."):
            node = node.get(part) if isinstance(node, dict) else None
        prefix = f"{records_path.strip('.')}"
    elif isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, list) and value and isinstance(value[0], dict):
                node, prefix = value, key
                break
    if isinstance(node, list):
        if not node or not isinstance(node[0], dict):
            return []
        record, prefix = node[0], f"{prefix}[]."
    elif isinstance(node, dict):
        record, prefix = node, f"{prefix}." if prefix else ""
    else:
        return []
    out = []
    for key, value in list(record.items())[:MAX_FIELDS]:
        kind, note = _type(value)
        out.append(
            {"path": f"{prefix}{key}", "column": _column(str(key)), "type": kind, "note": note}
        )
    return out


def _preview(body: bytes, cut: bool, records_path: str | None) -> tuple[str, list[dict[str, Any]]]:
    text = body.decode("utf-8", errors="replace")
    if cut:
        # Half a JSON document parses as nothing: show its start and say why there is no mapping.
        masked, _ = pii.mask(text[:PREVIEW_CHARS])
        return f"{masked}\n… (phản hồi dài hơn 256 KB, đã cắt; không gợi ý được ánh xạ)", []
    try:
        data = json.loads(text)
    except ValueError:
        masked, _ = pii.mask(text[:PREVIEW_CHARS])
        return masked, []
    # Personal data in the answer is masked before it reaches the screen; the mapping reads the
    # types from the unmasked values.
    shown, _ = pii.mask_deep(data)
    pretty = json.dumps(shown, ensure_ascii=False, indent=2)
    if len(pretty) > PREVIEW_CHARS:
        pretty = pretty[:PREVIEW_CHARS] + "\n…"
    return pretty, mapping_of(data, records_path)


async def try_request(job: dict[str, Any]) -> dict[str, Any]:
    """The answer to one try, never raising: ok, refused or failed."""
    request = job.get("request") or {}
    try:
        url = _with_query(str(request.get("url") or ""), str(request.get("query") or ""))
        await checked_address(url)
        headers = {
            str(h["name"]).strip(): str(h["value"])
            for h in request.get("headers") or []
            if isinstance(h, dict)
            and _HEADER_NAME.fullmatch(str(h.get("name", "")).strip())
            and str(h.get("name", "")).strip().lower() not in CREDENTIAL_HEADERS
            and "\r" not in str(h.get("value", ""))
            and "\n" not in str(h.get("value", ""))
        }
        started = time.monotonic()
        async with asyncio.timeout(TIMEOUT_SECONDS):
            auth = await _auth_headers(
                job["org_id"],
                job.get("data_space_id"),
                str(request.get("auth")),
                str(request.get("secret_ref") or ""),
            )
            status, body, cut = await _send("GET", url, {**headers, **auth})
        duration_ms = int((time.monotonic() - started) * 1000)
    except Refused as exc:
        return {"refused": str(exc)}
    except TimeoutError:
        return {"failed": f"API không trả lời trong {int(TIMEOUT_SECONDS)} giây."}
    except (httpx.HTTPError, OSError) as exc:
        # The library's message can name the address or quote the request; the type is enough.
        return {"failed": f"Không kết nối được tới API ({type(exc).__name__})."}
    preview, mapping = _preview(body, cut, request.get("records_path"))
    return {
        "ok": {"status": status, "duration_ms": duration_ms, "body": preview, "mapping": mapping}
    }


def _key(try_id: str) -> str:
    return f"connector:try:{try_id}"


async def handle(redis: Any, try_id: str) -> None:
    raw = await redis.get(_key(try_id))
    if raw is None:
        return  # the api stopped waiting and dropped it
    try:
        job = json.loads(raw)
        job["org_id"] = uuid.UUID(str(job["org_id"]))
        answer = await try_request(job)
    except (ValueError, KeyError, TypeError):
        answer = {"refused": "Yêu cầu gọi thử không hợp lệ."}
    async with redis.pipeline(transaction=True) as pipe:
        pipe.rpush(f"{_key(try_id)}:result", json.dumps(answer, ensure_ascii=False))
        pipe.expire(f"{_key(try_id)}:result", TTL_SECONDS)
        pipe.delete(_key(try_id))
        await pipe.execute()


async def run(redis: Any, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            try_id = await redis.blmove(QUEUE, PROCESSING, 1, "RIGHT", "LEFT")
        except Exception:  # noqa: BLE001 - Redis down: keep the loop, retry after a pause
            logger.warning("worker-agent | connector try queue unavailable", exc_info=True)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=2)
            continue
        if try_id is None:
            continue
        try:
            await handle(redis, str(try_id))
        except Exception as exc:  # noqa: BLE001 - one try must not stop the loop; the api times out
            # The type only: a message could quote the request's headers.
            logger.warning("worker-agent | connector try failed (%s)", type(exc).__name__)
        finally:
            await redis.lrem(PROCESSING, 1, try_id)
