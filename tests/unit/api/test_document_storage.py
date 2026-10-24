"""The document store tells a missing original (404) from a store it cannot reach (503)."""

from __future__ import annotations

from typing import Any

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from services.api.kernel.problems import Problem
from services.api.modules.document.ports import MinioObjectStore

pytestmark = pytest.mark.unit


class _Client:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def __aenter__(self) -> _Client:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def get_object(self, **_: Any) -> Any:
        raise self.error


def _store(monkeypatch: pytest.MonkeyPatch, error: Exception) -> MinioObjectStore:
    store = MinioObjectStore("http://key:secret@minio:9000")
    monkeypatch.setattr(store, "_client", lambda: _Client(error))
    return store


async def test_a_missing_original_is_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    missing = ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
    with pytest.raises(Problem) as raised:
        await _store(monkeypatch, missing).download("da-raw-docs", "pilot/x.html")
    assert raised.value.status == 404


async def test_an_unreachable_store_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    down = EndpointConnectionError(endpoint_url="http://minio:9000")
    with pytest.raises(Problem) as raised:
        await _store(monkeypatch, down).download("da-raw-docs", "pilot/x.html")
    assert raised.value.status == 503
