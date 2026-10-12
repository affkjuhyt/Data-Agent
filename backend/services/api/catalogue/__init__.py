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

from fastapi import Depends

from services.api.kernel.auth import CAPABILITIES, require
from services.api.kernel.idempotency import replay_guard
from services.api.kernel.problems import ProblemBody

CATALOGUE_FILE = Path(__file__).with_name("a2_endpoints.json")
A3_FILE = Path(__file__).with_name("a3_contract.json")

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
        (409, "Conflict"),
        (410, "Gone"),
        (412, "Precondition failed"),
        (422, "Validation failed"),
        (429, "Rate limited"),
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
def a3_errors() -> dict[str, list[tuple[int, str]]]:
    """ "METHOD path" → the errors A3 lists for it, as (status, code)."""
    with A3_FILE.open(encoding="utf-8") as f:
        data: dict[str, Any] = json.load(f)
    return {
        key: [(int(status), str(code)) for status, code in row["errors"]]
        for key, row in data["endpoints"].items()
    }


def _responses(method: str, path: str) -> dict[int | str, dict[str, Any]]:
    """The common problem responses plus the statuses A3 names for this endpoint (413, 423,
    503, …), so the generated spec says what the route answers."""
    out = dict(PROBLEM_RESPONSES)
    for status, code in a3_errors().get(f"{method.upper()} {path}", []):
        if status not in out:
            out[status] = {**_PROBLEM, "description": code}
    return out


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
    method: str,
    path: str,
    *,
    operation_id: str,
    group: str | tuple[str, ...] | None = None,
    authenticate: Any = None,
    variant_guard: Any = None,
) -> dict[str, Any]:
    """Route kwargs for one A2 endpoint: `@router.post(path, **endpoint("POST", path, operation_id=…))`.

    `authenticate` is required for, and only accepted on, a `channel` or `client`
    endpoint: a FastAPI dependency (`Depends(...)`) that verifies the channel signature
    or the api_client token. User tokens are no substitute for either.

    `variant_guard` is required when `group` names several rows served as one operation:
    a dependency that reads the variant (e.g. `layer`) and enforces that row's capability.
    The route gate alone only knows the caller holds some row's capability."""
    rows = endpoints().get((method.upper(), path))
    if not rows:
        raise LookupError(
            f"{method.upper()} {path} is not in A2; add it to tab 4 and a2_endpoints.json first (ADR-48)"
        )
    if group is not None:
        wanted = (group,) if isinstance(group, str) else tuple(group)
        rows = [r for r in rows if r.group in wanted]
        if len(rows) != len(wanted):
            raise LookupError(f"{method.upper()} {path} is not in A2 group(s) {wanted!r}")
    elif len(rows) > 1:
        raise LookupError(f"{method.upper()} {path} is in several A2 groups; pass group=")
    ep = rows[0]
    # Several rows served as one operation (decided duplicates, e.g. /v1/data-products): the
    # gate admits a capability of any row, so the variant's own check must come with it, or a
    # caller holding one row's capability would reach the other variant.
    if len(rows) == 1:
        if variant_guard is not None:
            raise ValueError(f"{method.upper()} {path}: variant_guard= is only for merged rows")
        guards = [_guard(ep, authenticate)]
    else:
        caps = tuple(dict.fromkeys(c for r in rows for c in r.capability if c != ANY_USER))
        if variant_guard is None or not caps or authenticate is not None:
            raise ValueError(
                f"{method.upper()} {path}: merged rows need variant_guard= and a capability per row"
            )
        guards = [require(*caps), variant_guard]
    if method.upper() == "POST":
        # After the guards: a replayed Idempotency-Key answer is only for a caller who would
        # still be let in (kernel/idempotency.py).
        guards.append(Depends(replay_guard))
    return {
        "operation_id": operation_id,
        "tags": list(dict.fromkeys(r.group for r in rows)),
        "summary": ep.summary,
        "responses": _responses(method, path),
        "openapi_extra": {
            "x-use-cases": [f"UC-{u}" for u in ep.use_cases],
            "x-capability": list(ep.capability),
        },
        "dependencies": guards,
    }


def _guard(ep: Endpoint, authenticate: Any = None) -> Any:
    caps = tuple(c for c in ep.capability if c != ANY_USER)
    if any(c in (CHANNEL, CLIENT) for c in caps):
        # The module that owns the channel or client supplies its own check; without one,
        # serving the route would silently accept user tokens in their place.
        if authenticate is None:
            raise NotImplementedError(
                f"{ep.method} {ep.path}: a {CHANNEL}/{CLIENT} endpoint needs authenticate="
            )
        return authenticate
    if authenticate is not None:
        raise ValueError(
            f"{ep.method} {ep.path}: authenticate= is only for {CHANNEL}/{CLIENT} endpoints"
        )
    unknown = set(caps) - CAPABILITIES
    if unknown:
        raise ValueError(f"{ep.method} {ep.path}: unknown capabilities {sorted(unknown)} in A2")
    return require(*caps)
