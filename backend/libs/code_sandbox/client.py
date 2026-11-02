"""The worker's side of the python-sandbox service: the code and its datasets go in, one
Execution comes back. The service applies the same static check; it is run here first so a
refused code costs no round trip."""

from __future__ import annotations

from typing import Any

import httpx

from libs.code_sandbox.local import TIMEOUT_S, Execution, check

# The service's own timeout, plus the time to send the datasets and read the answer.
REQUEST_TIMEOUT_S = TIMEOUT_S + 10


async def run(url: str, code: str, datasets: dict[str, dict[str, Any]]) -> Execution:
    check(code)
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_S) as client:
            response = await client.post(
                url.rstrip("/") + "/run", json={"code": code, "datasets": datasets}
            )
    except httpx.HTTPError as exc:
        return Execution(False, [], [], False, "", f"sandbox không trả lời ({type(exc).__name__})")
    if response.status_code != 200:
        return Execution(False, [], [], False, "", f"sandbox trả {response.status_code}")
    body = response.json()
    return Execution(
        ok=bool(body.get("ok")),
        columns=list(body.get("columns") or []),
        rows=list(body.get("rows") or []),
        truncated=bool(body.get("truncated")),
        stdout=str(body.get("stdout") or ""),
        error=body.get("error"),
    )
