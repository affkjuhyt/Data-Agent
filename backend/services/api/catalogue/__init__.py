"""A2, the endpoint catalogue of tab 4, as the api's routing contract (ADR-48).

`a2_endpoints.json` is a snapshot of A2. A route never restates what A2 says:
`endpoint("POST", "/v1/questions", operation_id=...)` returns the route kwargs
(tag = A2 group, summary, x-use-cases, x-capability, problem responses) and
the capability check, all read from the snapshot. The check that guards a
request is therefore the "Quyền" column itself, and a route that is not in A2
fails at import time instead of shipping. `check.py` compares the generated
OpenAPI with the whole of A2.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from services.api.kernel.auth import CAPABILITIES, require
from services.api.kernel.problems import ProblemBody

CATALOGUE_FILE = Path(__file__).with_name("a2_endpoints.json")

# "Quyền" values that name how the caller authenticates, not a capability (A1.2).
ANY_USER = "—"
CHANNEL = "channel"
CLIENT = "client"

_PROBLEM = {"model": ProblemBody, "content": {"application/problem+json": {}}}
PROBLEM_RESPONSES: dict[int | str, dict[str, Any]] = {
    status: {**_PROBLEM, "description": description}
    for status, description in (
        (400, "Invalid request"),
        (401, "Unauthenticated"),
        (403, "Forbidden"),
        (404, "Not found"),
        (422, "Validation failed"),
    )
}


@dataclass(frozen=True)
class Endpoint:
    group: str
    method: str
    path: str
    summary: str
    use_cases: tuple[str, ...]
    capability: tuple[str, ...]


@cache
def catalogue() -> dict[str, Any]:
    with CATALOGUE_FILE.open(encoding="utf-8") as f:
        data: dict[str, Any] = json.load(f)
    return data


@cache
def endpoints() -> dict[tuple[str, str], list[Endpoint]]:
    """(METHOD, path) → A2 rows. A list: A2 holds one known duplicate,
    POST /v1/data-products in both Biến đổi dữ liệu (P06) and Lớp ngữ cảnh
    (P07), served as one resource with a `layer` field (decided 11/10/2026)."""
    out: dict[tuple[str, str], list[Endpoint]] = {}
    for row in catalogue()["endpoints"]:
        ep = Endpoint(
            group=row["group"],
            method=row["method"],
            path=row["path"],
            summary=row["summary"],
            use_cases=tuple(row["use_cases"]),
            capability=tuple(row["capability"]),
        )
        out.setdefault((ep.method, ep.path), []).append(ep)
    return out


def endpoint(
    method: str, path: str, *, operation_id: str, group: str | None = None
) -> dict[str, Any]:
    """Route kwargs for one A2 endpoint: `@router.post(path, **endpoint("POST", path, operation_id=…))`."""
    rows = endpoints().get((method.upper(), path))
    if not rows:
        raise LookupError(
            f"{method.upper()} {path} is not in A2; add it to tab 4 and a2_endpoints.json first (ADR-48)"
        )
    if group is not None:
        rows = [r for r in rows if r.group == group]
        if not rows:
            raise LookupError(f"{method.upper()} {path} is not in A2 group {group!r}")
    if len(rows) > 1:
        raise LookupError(f"{method.upper()} {path} is in several A2 groups; pass group=")
    ep = rows[0]
    return {
        "operation_id": operation_id,
        "tags": [ep.group],
        "summary": ep.summary,
        "responses": PROBLEM_RESPONSES,
        "openapi_extra": {
            "x-use-cases": [f"UC-{u}" for u in ep.use_cases],
            "x-capability": list(ep.capability),
        },
        "dependencies": [_guard(ep)],
    }


def _guard(ep: Endpoint) -> Any:
    caps = tuple(c for c in ep.capability if c != ANY_USER)
    if any(c in (CHANNEL, CLIENT) for c in caps):
        # Channel (HMAC) and client (api_client token) authentication arrive
        # with the integration module; serving such a route before then would
        # silently accept user tokens in their place.
        raise NotImplementedError(
            f"{ep.method} {ep.path}: {CHANNEL}/{CLIENT} authentication is not built yet"
        )
    unknown = set(caps) - CAPABILITIES
    if unknown:
        raise ValueError(f"{ep.method} {ep.path}: unknown capabilities {sorted(unknown)} in A2")
    return require(*caps)
