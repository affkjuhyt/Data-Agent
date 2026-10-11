"""The api's request shapes are held to tab 4 A3, the way test_catalogue holds its operations to A2.

A3 names, per endpoint, the body fields ("*" required) and the query parameters. For every
operation of a group in COMPLETE_GROUPS the generated OpenAPI must take exactly A3's body fields,
require the ones A3 marks "*", and accept A3's query parameters. Query parameters beyond A3 are
allowed: A1.4 lets every list filter on a field of the same name and page with page_size,
page_token and order_by.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from services.api.app import create_app
from services.api.catalogue.check import COMPLETE_GROUPS, operations
from services.api.config import Settings

pytestmark = [pytest.mark.unit, pytest.mark.contract]

A3 = json.loads(
    (
        Path(__file__).resolve().parents[3] / "backend/services/api/catalogue/a3_contract.json"
    ).read_text(encoding="utf-8")
)["endpoints"]


def _resolve(schema: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    while "$ref" in schema:
        schema = spec["components"]["schemas"][schema["$ref"].rsplit("/", 1)[1]]
    return schema


def _body(op: dict[str, Any], spec: dict[str, Any]) -> tuple[set[str], set[str]]:
    content = op.get("requestBody", {}).get("content", {})
    media = content.get("application/json") or next(iter(content.values()), None)
    if not media:
        return set(), set()
    schema = _resolve(media["schema"], spec)
    return set(schema.get("properties", {})), set(schema.get("required", []))


def _query(op: dict[str, Any]) -> set[str]:
    return {p["name"] for p in op.get("parameters", []) if p.get("in") == "query"}


def _responses(op: dict[str, Any]) -> set[int]:
    return {int(status) for status in op.get("responses", {}) if status.isdigit()}


def contract_gaps(spec: dict[str, Any], groups: frozenset[str]) -> list[str]:
    gaps = []
    for (method, path), op in sorted(operations(spec).items()):
        row = A3.get(f"{method} {path}")
        if row is None or not set(row["groups"]) & groups:
            continue
        fields, required = _body(op, spec)
        want = set(row["body"])
        want_required = {name for name, req in row["body"].items() if req}
        where = f"{method} {path}"
        if want - fields:
            gaps.append(f"{where}: body lacks {sorted(want - fields)}")
        if fields - want:
            gaps.append(f"{where}: body has fields A3 does not name {sorted(fields - want)}")
        if want_required - required:
            gaps.append(f"{where}: A3 requires {sorted(want_required - required)}")
        if set(row["query"]) - _query(op):
            gaps.append(f"{where}: query lacks {sorted(set(row['query']) - _query(op))}")
        want_statuses = {status for status, _ in row["errors"]}
        if want_statuses - _responses(op):
            gaps.append(f"{where}: responses lack statuses {sorted(want_statuses - _responses(op))}")
    return gaps


def test_complete_groups_take_the_request_shapes_of_a3() -> None:
    spec = create_app(Settings()).openapi()
    gaps = contract_gaps(spec, COMPLETE_GROUPS)
    assert not gaps, "\n".join(gaps)


def test_the_a3_snapshot_covers_a2() -> None:
    a2 = json.loads(
        (
            Path(__file__).resolve().parents[3] / "backend/services/api/catalogue/a2_endpoints.json"
        ).read_text(encoding="utf-8")
    )["endpoints"]
    a2_keys = {f"{e['method']} {e['path']}" for e in a2}
    # POST /mcp is the MCP transport, described in A2 only.
    assert a2_keys - set(A3) == {"POST /mcp"}
    assert set(A3) <= a2_keys
