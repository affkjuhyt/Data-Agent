"""The api's OpenAPI is held to tab 4 A2 (ADR-48)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import APIRouter

from services.api.app import create_app
from services.api.catalogue import catalogue, endpoint, endpoints
from services.api.catalogue.check import compare
from services.api.config import Settings

from .conftest import probe_router

pytestmark = [pytest.mark.unit, pytest.mark.contract]


def test_snapshot_holds_all_of_a2() -> None:
    data = catalogue()
    assert len(data["endpoints"]) == 379
    assert len(data["groups"]) == 22
    assert {e["group"] for e in data["endpoints"]} == set(data["groups"])


def test_endpoint_metadata_comes_from_a2() -> None:
    kw = endpoint("POST", "/v1/questions", operation_id="createQuestion")
    assert kw["tags"] == ["Hỏi đáp"]
    assert kw["openapi_extra"] == {
        "x-use-cases": ["UC-01-01", "UC-01-04", "UC-05-04", "UC-10-01"],
        "x-capability": ["ask"],
    }
    assert len(kw["dependencies"]) == 1


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


def test_channel_and_client_routes_wait_for_their_authentication() -> None:
    channel = next(e for rows in endpoints().values() for e in rows if "channel" in e.capability)
    with pytest.raises(NotImplementedError):
        endpoint(channel.method, channel.path, operation_id="x")


def _spec(*routers: APIRouter) -> dict[str, Any]:
    app = create_app(Settings())
    for r in routers:
        app.include_router(r)
    return app.openapi()


def test_the_app_as_built_passes_the_check() -> None:
    report = compare(_spec())
    assert report.ok, report


def test_served_endpoints_count_as_implemented() -> None:
    baseline = compare(_spec()).implemented
    report = compare(_spec(probe_router()))
    assert report.ok, report
    assert report.implemented == baseline + 2


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
    spec = _spec()
    assert compare(spec).ok
    report = compare(spec, complete_groups=frozenset({"Thu thập"}))
    assert not report.ok
    assert "GET /v1/crawl-sources (Thu thập)" in report.missing_in_complete_groups
