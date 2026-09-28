"""hnx.vn (and other misconfigured VN government sites) send only their leaf
certificate, not the intermediate CA — a real failure hit live on the
"Kết quả đấu thầu trái phiếu Chính phủ" source. `tls.get()` retries once with
a fetched intermediate on exactly that failure signature and nothing else.
"""
from __future__ import annotations

import ssl
from typing import Any, ClassVar

import httpx
import pytest

from libs.ingestion.connectors import tls

pytestmark = pytest.mark.unit


def _missing_intermediate_error() -> httpx.ConnectError:
    cause = ssl.SSLCertVerificationError("unable to get local issuer certificate")
    cause.verify_code = 20
    err = httpx.ConnectError("connection failed")
    err.__cause__ = cause
    return err


def _other_ssl_error() -> httpx.ConnectError:
    cause = ssl.SSLCertVerificationError("certificate has expired")
    cause.verify_code = 10  # X509_V_ERR_CERT_HAS_EXPIRED — not our narrow case
    err = httpx.ConnectError("connection failed")
    err.__cause__ = cause
    return err


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    tls._context_cache.clear()


class _FakeResponse:
    def raise_for_status(self) -> None:
        pass


class _FakeClient:
    """Each entry in `behaviors` is either an exception to raise or a response
    to return, consumed one per `get()` call — models a client that fails on
    its first attempt (missing intermediate) and succeeds on the retry."""

    _behaviors: ClassVar[list[Any]] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, *_: Any) -> None:
        return None

    def get(self, url: str, **_: Any) -> _FakeResponse:
        behavior = _FakeClient._behaviors.pop(0)
        if isinstance(behavior, Exception):
            raise behavior
        return behavior


def test_normal_fetch_succeeds_without_any_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeClient._behaviors = [_FakeResponse()]
    monkeypatch.setattr(httpx, "Client", _FakeClient)
    monkeypatch.setattr(tls, "_fetch_missing_intermediate_context", lambda *a: pytest.fail("should not be called"))

    response = tls.get("https://example.com/x")
    assert isinstance(response, _FakeResponse)


def test_missing_intermediate_retries_once_with_fetched_context(monkeypatch: pytest.MonkeyPatch) -> None:
    success = _FakeResponse()
    _FakeClient._behaviors = [_missing_intermediate_error(), success]
    monkeypatch.setattr(httpx, "Client", _FakeClient)
    sentinel_ctx = object()
    monkeypatch.setattr(tls, "_fetch_missing_intermediate_context", lambda host, port: sentinel_ctx)

    response = tls.get("https://hnx.vn/report.aspx")
    assert response is success
    assert tls._context_cache["hnx.vn"] is sentinel_ctx


def test_second_call_to_same_host_reuses_the_cached_context_and_never_fails_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel_ctx = object()
    tls._context_cache["hnx.vn"] = sentinel_ctx
    success = _FakeResponse()
    _FakeClient._behaviors = [success]
    monkeypatch.setattr(httpx, "Client", _FakeClient)
    monkeypatch.setattr(tls, "_fetch_missing_intermediate_context", lambda *a: pytest.fail("should not be called"))

    response = tls.get("https://hnx.vn/report2.aspx")
    assert response is success


def test_unrelated_ssl_failure_is_never_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """An expired cert or hostname mismatch is a real problem — retrying
    with a fetched intermediate wouldn't fix it and would just mask the
    actual error, so this must propagate unchanged."""
    err = _other_ssl_error()
    _FakeClient._behaviors = [err]
    monkeypatch.setattr(httpx, "Client", _FakeClient)
    monkeypatch.setattr(tls, "_fetch_missing_intermediate_context", lambda *a: pytest.fail("should not be called"))

    with pytest.raises(httpx.ConnectError) as excinfo:
        tls.get("https://example.com/x")
    assert excinfo.value is err


def test_when_the_intermediate_cannot_be_resolved_the_original_error_still_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    err = _missing_intermediate_error()
    _FakeClient._behaviors = [err]
    monkeypatch.setattr(httpx, "Client", _FakeClient)
    monkeypatch.setattr(tls, "_fetch_missing_intermediate_context", lambda *a: None)

    with pytest.raises(httpx.ConnectError) as excinfo:
        tls.get("https://hnx.vn/report.aspx")
    assert excinfo.value is err
