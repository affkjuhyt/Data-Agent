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


def test_api03_workspace_core_routes_are_registered() -> None:
    app = create_app(Settings(), token_verifier=lambda: None)  # type: ignore[arg-type]
    routes = {(next(iter(route.methods)), route.path) for route in app.routes if route.path.startswith("/v1/")}
    assert {
        ("GET", "/v1/workspaces"),
        ("POST", "/v1/workspaces"),
        ("GET", "/v1/workspaces/{id}"),
        ("PATCH", "/v1/workspaces/{id}"),
        ("POST", "/v1/workspaces/{id}/tiles"),
        ("PATCH", "/v1/tiles/{id}"),
        ("POST", "/v1/tiles/{id}:run"),
        ("POST", "/v1/workspaces/{id}:refresh"),
        ("PUT", "/v1/workspaces/{id}/shares"),
        ("DELETE", "/v1/workspaces/{id}/shares/{principal_id}"),
        ("POST", "/v1/workspaces/{id}/participants"),
        ("POST", "/v1/tiles/{id}/comments"),
        ("PATCH", "/v1/comments/{id}"),
        ("POST", "/v1/workspaces/{id}/reports"),
        ("POST", "/v1/session-reports/{id}:check"),
        ("POST", "/v1/session-reports/{id}:publish"),
    } <= routes


def test_api04_inbox_and_eval_routes_are_registered() -> None:
    app = create_app(Settings(), token_verifier=lambda: None)  # type: ignore[arg-type]
    routes = {(next(iter(route.methods)), route.path) for route in app.routes if route.path.startswith("/v1/")}
    assert {
        ("GET", "/v1/inbox"),
        ("GET", "/v1/hitl-requests/{id}"),
        ("POST", "/v1/hitl-requests/{id}:resolve"),
        ("GET", "/v1/proposals/{id}"),
        ("POST", "/v1/proposals/{id}:decide"),
        ("POST", "/v1/eval-sets/{id}/items"),
        ("POST", "/v1/eval-sets/{id}/runs"),
        ("GET", "/v1/eval-runs/{id}"),
        ("GET", "/v1/error-book-entries"),
        ("POST", "/v1/error-book-entries"),
        ("GET", "/v1/error-book-entries/{id}"),
        ("PATCH", "/v1/error-book-entries/{id}"),
        ("POST", "/v1/error-book-entries/{id}:mark-fixed"),
        ("POST", "/v1/error-book-entries/{id}:add-regression"),
        ("POST", "/v1/error-book-entries/{id}:dismiss"),
        ("POST", "/v1/error-book-entries/{id}:propose-to-pack"),
    } <= routes
