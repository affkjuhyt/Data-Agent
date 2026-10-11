"""HTTP client for sandbox_executor — the single way workflow_runtime reaches
it (sandbox_schema_docs.docx §1.2 hub-and-spoke isolation).

Extracted from handlers/code_interpreter.py so handlers/data.py (DB Connector /
text_to_sql) can reuse exactly the same submit+poll transport rather than
growing a second, slightly-different copy. Both handlers now go through here,
so there is one place where the 202+job_id protocol, the backpressure 503 and
the poll deadline are implemented.

sandbox_executor's POST /execute/{kind} enqueues and returns 202 + {job_id}
immediately rather than holding one HTTP connection open for the whole
acquire+exec/query duration. Callers poll GET .../{job_id} with backoff until a
terminal status. The contract one layer up is unchanged: the agent's tool call
still blocks until a final result.
"""
import asyncio
import os
import time
import weakref
from typing import Any

import httpx

from libs.shared.service_identity import get_secret, mint_service_assertion

SANDBOX_EXECUTOR_URL = os.environ.get("SANDBOX_EXECUTOR_URL", "http://localhost:8260")

# A metric or schema query usually finishes in tens of ms; a 200 ms first wait
# was most of its latency. Backoff still reaches the 1 s cap within ~8 polls.
_POLL_INITIAL_DELAY_S = 0.05
_POLL_MAX_DELAY_S = 1.0

# One pooled client per event loop and timeout: a fresh AsyncClient per query
# paid a TCP connect on every submit. httpx clients are bound to the loop that
# first used them, hence per loop (weakly, so a closed loop's clients go too).
_shared_clients: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[float, httpx.AsyncClient]]" = (
    weakref.WeakKeyDictionary()
)


def shared_client(timeout: float) -> httpx.AsyncClient:
    per_loop = _shared_clients.setdefault(asyncio.get_running_loop(), {})
    client = per_loop.get(timeout)
    if client is None or client.is_closed:
        client = httpx.AsyncClient(timeout=timeout)
        per_loop[timeout] = client
    return client
# Budget on top of the tool's own execution timeout to cover queueing delay
# (worker pull latency, or a circuit-breaker HALF_OPEN cooldown wait) that sits
# in front of acquire+exec — it isn't part of timeout_s itself.
POLL_OVERHEAD_S = 15


async def submit_and_poll(
    http: httpx.AsyncClient,
    path: str,
    body: dict[str, Any],
    overall_timeout_s: float,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """POSTs to enqueue, then polls GET {path}/{job_id} with backoff until a
    terminal status or overall_timeout_s elapses. Returns the terminal response
    body with `_http_status` merged in, so callers can tell a circuit-breaker
    503 (sandbox_executor overloaded, back off) apart from an ordinary handled
    failure (success: false) without re-deriving it.
    """
    resp = await http.post(f"{SANDBOX_EXECUTOR_URL}{path}", json=body, headers=headers)
    if resp.status_code == 503:
        # Queue was already full (backend/services/sandbox_executor/ops/ mục i backpressure) — rejected
        # before a job_id was even minted, nothing to poll.
        data: dict[str, Any] = resp.json()
        data["_http_status"] = 503
        return data
    resp.raise_for_status()
    job_id = resp.json()["job_id"]

    deadline = time.monotonic() + overall_timeout_s
    delay = _POLL_INITIAL_DELAY_S
    while True:
        poll = await http.get(f"{SANDBOX_EXECUTOR_URL}{path}/{job_id}", headers=headers)
        data = poll.json()
        data["_http_status"] = poll.status_code
        if data.get("status") in ("queued", "running"):
            if time.monotonic() >= deadline:
                return {
                    "success": False,
                    "error": f"timed out waiting for sandbox_executor job {job_id} to finish",
                    "_http_status": 504,
                }
            await asyncio.sleep(delay)
            delay = min(delay * 1.5, _POLL_MAX_DELAY_S)
            continue
        return data


def service_assertion_headers(identity: dict[str, Any]) -> dict[str, str]:
    """Create the short-lived assertion required by sandbox_executor."""
    assertion = mint_service_assertion(
        service="workflow_runtime",
        tenant_id=str(identity.get("tenant_id") or ""),
        user_id=str(identity.get("user_id") or "anonymous"),
        roles=list(identity.get("user_roles") or []),
        secret=get_secret(),
    )
    return {"X-Service-Assertion": assertion}
