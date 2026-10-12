"""The api's OpenAPI is held to tab 4 A2 (ADR-48)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import APIRouter

from services.api.app import create_app
from services.api.catalogue import catalogue, endpoint, endpoints
from services.api.catalogue.check import compare
from services.api.config import Settings
from services.api.kernel.idempotency import replay_guard

pytestmark = [pytest.mark.unit, pytest.mark.contract]


def test_snapshot_holds_all_of_a2() -> None:
    data = catalogue()
    assert len(data["endpoints"]) == 384
    assert len(data["groups"]) == 22
    assert {e["group"] for e in data["endpoints"]} == set(data["groups"])


def test_endpoint_metadata_comes_from_a2() -> None:
    kw = endpoint("POST", "/v1/questions", operation_id="createQuestion")
    assert kw["tags"] == ["Hỏi đáp"]
    assert kw["openapi_extra"] == {
        "x-use-cases": ["UC-01-01", "UC-01-04", "UC-05-04", "UC-10-01"],
        "x-capability": ["ask"],
    }
    # The capability guard, then the Idempotency-Key replay that must come after it (A1.5).
    guard, replay = kw["dependencies"]
    assert replay.dependency is replay_guard


def test_route_outside_a2_fails_at_import() -> None:
    with pytest.raises(LookupError, match="not in A2"):
        endpoint("GET", "/v1/secret-admin", operation_id="x")


def test_data_products_needs_its_group() -> None:
    # The one A2 duplicate (P06 and P07), served as one resource with `layer`.
    assert len(endpoints()[("POST", "/v1/data-products")]) == 2
    with pytest.raises(LookupError, match="several A2 groups"):
        endpoint("POST", "/v1/data-products", operation_id="createDataProduct")
    kw = endpoint(
        "POST", "/v1/data-products", operation_id="createDataProduct", group="Lớp ngữ cảnh"
    )
    assert kw["openapi_extra"]["x-capability"] == ["steward"]


def test_one_route_can_serve_both_data_product_rows() -> None:
    both = ("Lớp ngữ cảnh", "Biến đổi dữ liệu")
    with pytest.raises(ValueError, match="variant_guard"):
        endpoint("POST", "/v1/data-products", operation_id="createDataProduct", group=both)
    check = object()
    kw = endpoint(
        "POST",
        "/v1/data-products",
        operation_id="createDataProduct",
        group=both,
        variant_guard=check,
    )
    assert kw["tags"] == ["Biến đổi dữ liệu", "Lớp ngữ cảnh"]
    assert kw["dependencies"][-2] is check


def test_channel_and_client_routes_need_their_own_authentication() -> None:
    channel = next(e for rows in endpoints().values() for e in rows if "channel" in e.capability)
    with pytest.raises(NotImplementedError):
        endpoint(channel.method, channel.path, operation_id="x")
    check = object()
    kw = endpoint(channel.method, channel.path, operation_id="x", authenticate=check)
    assert kw["dependencies"][0] is check
    with pytest.raises(ValueError, match="only for channel/client"):
        endpoint("GET", "/v1/inbox", operation_id="x", authenticate=check)


def _spec(*routers: APIRouter) -> dict[str, Any]:
    app = create_app(Settings())
    for r in routers:
        app.include_router(r)
    return app.openapi()


def test_the_app_as_built_passes_the_check() -> None:
    report = compare(_spec())
    assert report.ok, report


def _unserved() -> tuple[str, str, str]:
    """An A2 endpoint the app does not serve yet, with its group. Picked from the app as built,
    so these tests keep working as groups land (a fixed path went stale with every module)."""
    served = {(m.upper(), p) for p, item in _spec()["paths"].items() for m in item}
    for (method, path), rows in sorted(endpoints().items()):
        if (
            (method, path) not in served
            and len(rows) == 1
            and not set(rows[0].capability) & {"channel", "client"}
        ):
            return method, path, rows[0].group
    pytest.skip("every user-authenticated A2 endpoint is served")


def test_served_endpoints_count_as_implemented() -> None:
    method, path, _ = _unserved()
    baseline = compare(_spec()).implemented
    probe = APIRouter()
    probe.add_api_route(
        path, lambda: None, methods=[method], **endpoint(method, path, operation_id="probe")
    )
    report = compare(_spec(probe))
    assert report.ok, report
    assert report.implemented == baseline + 1


def test_route_not_in_a2_is_reported() -> None:
    rogue = APIRouter()

    @rogue.get("/v1/rogue", operation_id="rogue")
    async def r() -> None: ...

    report = compare(_spec(rogue))
    assert not report.ok
    assert report.extra == ["GET /v1/rogue"]


def test_drift_from_a2_is_reported() -> None:
    drifted = APIRouter()
    kw = endpoint("POST", "/v1/skills", operation_id="createSkillProbe")
    kw["openapi_extra"] = {**kw["openapi_extra"], "x-capability": ["admin"]}

    @drifted.post("/v1/skills", **kw)
    async def r() -> None: ...

    report = compare(_spec(drifted))
    assert any("x-capability" in d for d in report.drift)


def test_missing_endpoint_fails_only_in_a_complete_group() -> None:
    method, path, group = _unserved()
    spec = _spec()
    assert compare(spec).ok
    report = compare(spec, complete_groups=frozenset({group}))
    assert not report.ok
    assert f"{method} {path} ({group})" in report.missing_in_complete_groups
