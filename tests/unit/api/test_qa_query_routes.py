"""API-02 routes expose the implemented question, answer and query surfaces."""

from services.api.app import create_app
from services.api.config import Settings

import pytest

pytestmark = pytest.mark.unit


def test_api02_core_routes_are_registered() -> None:
    app = create_app(Settings(), token_verifier=lambda: None)  # type: ignore[arg-type]
    routes = {(next(iter(route.methods)), route.path) for route in app.routes if route.path.startswith("/v1/")}
    assert {
        ("POST", "/v1/questions"),
        ("GET", "/v1/questions/{id}"),
        ("GET", "/v1/questions/{id}/stream"),
        ("PATCH", "/v1/questions/{id}/plan"),
        ("GET", "/v1/conversations"),
        ("GET", "/v1/answers/{id}"),
        ("GET", "/v1/answers/{id}/lineage"),
        ("POST", "/v1/answers/{id}/feedback"),
        ("GET", "/v1/answers/{id}/receipt"),
        ("GET", "/v1/query-runs/{id}"),
        ("GET", "/v1/query-runs/{id}/sql"),
        ("GET", "/v1/query-runs/{id}/rows"),
    } <= routes
