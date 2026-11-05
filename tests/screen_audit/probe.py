"""Classify API responses for the screen audit harness."""

from __future__ import annotations

import json
from typing import Any

import httpx

from tests.screen_audit.catalog import Screen

LABELS = (
    "content",
    "empty",
    "access_denied_ok",
    "access_as_load_error",
    "forbidden_plain",
    "server_error",
    "no_seed_id",
    "hidden_rail",
    "not_found",
    "no_probe",
    "http_other",
)


def is_empty_body(body: Any) -> bool:
    if body is None:
        return True
    if isinstance(body, list):
        return len(body) == 0
    if not isinstance(body, dict):
        return False
    if "items" in body:
        items = body["items"]
        return isinstance(items, list) and len(items) == 0
    # A detail object with an id is content.
    if body.get("id") or body.get("key"):
        return False
    return False


def first_id(body: Any) -> str | None:
    if isinstance(body, list) and body:
        row = body[0]
        if isinstance(row, dict):
            value = row.get("id") or row.get("key")
            return str(value) if value is not None else None
        return None
    if isinstance(body, dict):
        items = body.get("items")
        if isinstance(items, list) and items:
            row = items[0]
            if isinstance(row, dict):
                value = row.get("id") or row.get("key") or row.get("resource_id")
                return str(value) if value is not None else None
        value = body.get("id") or body.get("key")
        return str(value) if value is not None else None
    return None


def classify_response(
    *,
    status: int,
    body: Any,
    wires_access_denied: bool,
    needs_id: bool,
    resolved_id: str | None,
) -> str:
    if needs_id and not resolved_id:
        return "no_seed_id"
    if status >= 500:
        return "server_error"
    if status == 404:
        return "not_found"
    if status == 403:
        access = body.get("access") if isinstance(body, dict) else None
        if access:
            return "access_denied_ok" if wires_access_denied else "access_as_load_error"
        return "forbidden_plain"
    if status == 200:
        return "empty" if is_empty_body(body) else "content"
    if status == 0:
        return "no_probe"
    return "http_other"


def expand_path(path: str, record_id: str | None) -> str:
    if record_id is None:
        return path
    return path.replace("{id}", record_id).replace("{key}", record_id)


def get_json(
    client: httpx.Client,
    kong: str,
    path: str,
    headers: dict[str, str],
) -> tuple[int, Any]:
    url = f"{kong}{path}"
    if "page_size=" not in path:
        url = f"{url}{'&' if '?' in path else '?'}page_size=20"
    try:
        response = client.get(url, headers=headers, timeout=20)
    except httpx.HTTPError as exc:
        return 0, {"error": str(exc)}
    try:
        body: Any = response.json()
    except json.JSONDecodeError:
        body = {"raw": response.text[:300]}
    return response.status_code, body


def resolve_id(
    client: httpx.Client,
    kong: str,
    screen: Screen,
    headers: dict[str, str],
) -> str | None:
    if not screen.needs_id:
        return None
    if screen.seed_id:
        return screen.seed_id
    list_path = screen.id_from
    if not list_path:
        # Fall back to the first primary get without a path param.
        for path in screen.primary_gets:
            if "{" not in path:
                list_path = path
                break
    if not list_path:
        return None
    status, body = get_json(client, kong, list_path, headers)
    if status != 200:
        return None
    return first_id(body)


def probe_screen(
    client: httpx.Client,
    kong: str,
    screen: Screen,
    headers: dict[str, str],
    *,
    record_id: str | None,
) -> tuple[str, int, str]:
    """Return (label, status, detail) for the screen's primary GETs.

    Uses the worst label across gets (server_error > access_* > empty > content).
    """
    if not screen.primary_gets:
        return "no_probe", 0, ""

    if screen.needs_id and not record_id:
        return "no_seed_id", 0, "no id from list"

    priority = {
        "server_error": 100,
        "access_as_load_error": 90,
        "access_denied_ok": 85,
        "forbidden_plain": 80,
        "not_found": 70,
        "no_seed_id": 60,
        "http_other": 50,
        "empty": 40,
        "content": 30,
        "no_probe": 10,
        "hidden_rail": 5,
    }
    best_label = "no_probe"
    best_status = 0
    details: list[str] = []

    for path in screen.primary_gets:
        expanded = expand_path(path, record_id)
        if "{" in expanded:
            return "no_seed_id", 0, f"unresolved path {path}"
        status, body = get_json(client, kong, expanded, headers)
        label = classify_response(
            status=status,
            body=body,
            wires_access_denied=screen.wires_access_denied,
            needs_id=False,  # already resolved above
            resolved_id=record_id,
        )
        details.append(f"{expanded}:{status}:{label}")
        if priority.get(label, 0) >= priority.get(best_label, 0):
            best_label = label
            best_status = status

    return best_label, best_status, "; ".join(details)
