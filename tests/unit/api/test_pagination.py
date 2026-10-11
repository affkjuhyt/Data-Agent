"""Cursor lists (A1.4)."""

from __future__ import annotations

from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from services.api.app import create_app
from services.api.config import Settings
from services.api.kernel.pagination import (
    MAX_PAGE_SIZE,
    Page,
    PageRequest,
    decode_page_token,
    encode_page_token,
    page_request,
)

pytestmark = [pytest.mark.unit, pytest.mark.contract]


@pytest.fixture
def paged() -> TestClient:
    app: FastAPI = create_app(Settings())

    @app.get("/paged")
    async def paged(page: Annotated[PageRequest, Depends(page_request)]) -> Page[int]:
        start = int((page.cursor or {}).get("after", 0))
        items = list(range(start + 1, start + 1 + page.page_size))
        return Page[int](items=items, next_page_token=encode_page_token({"after": items[-1]}))

    return TestClient(app)


def test_token_round_trip() -> None:
    cursor = {"created_at": "2026-10-11T08:00:00Z", "id": "0192"}
    assert decode_page_token(encode_page_token(cursor)) == cursor


def test_empty_token_means_first_page_and_end() -> None:
    assert encode_page_token(None) == ""
    assert decode_page_token("") is None


def test_next_page_follows_the_token(paged: TestClient) -> None:
    first = paged.get("/paged", params={"page_size": 2}).json()
    assert first["items"] == [1, 2]
    second = paged.get(
        "/paged", params={"page_size": 2, "page_token": first["next_page_token"]}
    ).json()
    assert second["items"] == [3, 4]


def test_default_page_size_is_50(paged: TestClient) -> None:
    assert len(paged.get("/paged").json()["items"]) == 50


@pytest.mark.parametrize("size", [0, MAX_PAGE_SIZE + 1])
def test_page_size_out_of_range_is_validation_failed(paged: TestClient, size: int) -> None:
    r = paged.get("/paged", params={"page_size": size})
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "validation_failed"
    assert body["errors"][0]["field"] == "page_size"


@pytest.mark.parametrize("token", ["not base64 !!", "WzEsMl0"])  # junk; a JSON list, not an object
def test_foreign_token_is_400(paged: TestClient, token: str) -> None:
    r = paged.get("/paged", params={"page_token": token})
    assert r.status_code == 400
    assert r.json()["errors"][0]["field"] == "page_token"
