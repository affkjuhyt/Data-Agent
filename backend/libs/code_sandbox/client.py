"""The worker's side of the python-sandbox service: the code and its datasets go in, one
Execution comes back. The service applies the same static check; it is run here first so a
refused code costs no round trip.

The answer is read as untrusted: code that got past the checks controls what the pod sends, so
the body is capped while it is read and its shape is checked before anything in it is used."""

from __future__ import annotations

import json
from typing import Any

import httpx

from libs.code_sandbox.local import MAX_RESULT_ROWS, MAX_STDOUT, TIMEOUT_S, Execution, check

# The service's own timeout, plus the time to send the datasets and read the answer.
REQUEST_TIMEOUT_S = TIMEOUT_S + 10
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_COLUMNS = 200
SCALARS = (str, int, float, bool, type(None))


def _failed(why: str) -> Execution:
    return Execution(False, [], [], False, "", why)


def _shaped(body: Any) -> Execution:
    if not isinstance(body, dict):
        return _failed("sandbox trả kết quả sai dạng")
    columns, rows = body.get("columns") or [], body.get("rows") or []
    error, stdout = body.get("error"), body.get("stdout") or ""
    if (
        not isinstance(columns, list)
        or len(columns) > MAX_COLUMNS
        or not all(isinstance(c, str) for c in columns)
        or not isinstance(rows, list)
        or len(rows) > MAX_RESULT_ROWS
        or not all(
            isinstance(r, list)
            and len(r) == len(columns)
            and all(isinstance(v, SCALARS) for v in r)
            for r in rows
        )
        or not (error is None or isinstance(error, str))
        or not isinstance(stdout, str)
    ):
        return _failed("sandbox trả kết quả sai dạng")
    return Execution(
        ok=body.get("ok") is True,
        columns=columns,
        rows=rows,
        truncated=body.get("truncated") is True,
        stdout=stdout[:MAX_STDOUT],
        error=error[:500] if error else None,
    )


async def run(url: str, code: str, datasets: dict[str, dict[str, Any]]) -> Execution:
    check(code)
    try:
        async with (
            httpx.AsyncClient(timeout=REQUEST_TIMEOUT_S) as client,
            client.stream(
                "POST", url.rstrip("/") + "/run", json={"code": code, "datasets": datasets}
            ) as response,
        ):
            if response.status_code != 200:
                return _failed(f"sandbox trả {response.status_code}")
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body += chunk
                if len(body) > MAX_RESPONSE_BYTES:
                    return _failed("kết quả của sandbox quá lớn")
    except httpx.HTTPError as exc:
        return _failed(f"sandbox không trả lời ({type(exc).__name__})")
    try:
        return _shaped(json.loads(body))
    except ValueError:
        return _failed("không đọc được kết quả của sandbox")
