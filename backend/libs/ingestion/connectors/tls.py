"""Fetch helper that works around a real, common failure on Vietnamese
government/exchange sources (hit for real on hnx.vn's bond-auction report):
the server sends only its leaf certificate, not the intermediate CA cert
("SSL: CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate").
Browsers paper over this by fetching the missing intermediate themselves via
the certificate's Authority Information Access (AIA) extension; Python's ssl
module does not, so every connector hit this hard-fail with no ingest path
around it.

This is deliberately narrow: only the exact "missing intermediate" verify
codes (20/21) trigger the AIA fetch-and-retry. Any other TLS failure (expired
cert, hostname mismatch, untrusted root) is a real problem and must keep
failing loudly, not be silently worked around."""
from __future__ import annotations

import ssl
from typing import Any

import certifi
import httpx

_MISSING_INTERMEDIATE_CODES = {20, 21}  # X509_V_ERR_UNABLE_TO_GET_ISSUER_CERT_LOCALLY / _VERIFY_THE_FIRST_CERT

# Process-local: an SSLContext per host that already needed the AIA fetch, so
# a source re-crawled every few minutes doesn't re-fetch the same
# intermediate on every single run.
_context_cache: dict[str, ssl.SSLContext] = {}


def _verify_code(exc: BaseException) -> int | None:
    cause = exc.__cause__ or exc.__context__
    while cause is not None:
        if isinstance(cause, ssl.SSLCertVerificationError):
            return cause.verify_code
        cause = cause.__cause__ or cause.__context__
    return None


def _fetch_missing_intermediate_context(host: str, port: int) -> ssl.SSLContext | None:
    """Best-effort: pull the leaf cert (unverified — only to read its AIA
    extension, never trusted for anything else), fetch the CA-issuer cert it
    points at, and hand back a context that trusts certifi's roots plus that
    one intermediate. Returns None if anything about this doesn't pan out,
    so the caller re-raises the original, real TLS error instead of masking
    it with a confusing new one."""
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization
    from cryptography.x509.oid import AuthorityInformationAccessOID, ExtensionOID

    try:
        leaf_pem = ssl.get_server_certificate((host, port))
        leaf = x509.load_pem_x509_certificate(leaf_pem.encode())
        aia = leaf.extensions.get_extension_for_oid(ExtensionOID.AUTHORITY_INFORMATION_ACCESS).value
        issuer_url = next(
            desc.access_location.value
            for desc in aia
            if desc.access_method == AuthorityInformationAccessOID.CA_ISSUERS
        )
        intermediate_der = httpx.get(issuer_url, timeout=10).content
        intermediate = x509.load_der_x509_certificate(intermediate_der)
        intermediate_pem = intermediate.public_bytes(serialization.Encoding.PEM).decode()
    except Exception:  # noqa: BLE001 — any failure here just means "can't fix it, re-raise the original"
        return None

    ctx = ssl.create_default_context(cafile=certifi.where())
    try:
        ctx.load_verify_locations(cadata=intermediate_pem)
    except ssl.SSLError:
        return None
    return ctx


def get(url: str, **kwargs: Any) -> httpx.Response:
    """Drop-in replacement for `httpx.Client(...).get(url, ...)` (a fresh,
    short-lived client per call, matching every connector's existing usage)
    that retries once with a fetched intermediate cert on the specific
    missing-intermediate failure, and re-raises everything else untouched."""
    from urllib.parse import urlparse

    client_kwargs = {k: v for k, v in kwargs.items() if k in ("headers", "timeout", "follow_redirects")}
    request_kwargs = {k: v for k, v in kwargs.items() if k == "params"}

    host = urlparse(url).hostname or ""
    cached = _context_cache.get(host)
    if cached is not None:
        client_kwargs["verify"] = cached

    try:
        with httpx.Client(**client_kwargs) as client:
            response = client.get(url, **request_kwargs)
            response.raise_for_status()
            return response
    except httpx.ConnectError as exc:
        if host in _context_cache or _verify_code(exc) not in _MISSING_INTERMEDIATE_CODES:
            raise
        port = urlparse(url).port or 443
        ctx = _fetch_missing_intermediate_context(host, port)
        if ctx is None:
            raise
        _context_cache[host] = ctx
        with httpx.Client(**{**client_kwargs, "verify": ctx}) as client:
            response = client.get(url, **request_kwargs)
            response.raise_for_status()
            return response
