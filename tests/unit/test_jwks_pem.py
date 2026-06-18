"""Unit tests for jwks-refresher JWK→PEM conversion.

Guards the M6 fix: the RSA public key is rebuilt from the JWK modulus/exponent
and serialized to SPKI PEM by `cryptography`. A regression here means Kong gets
a malformed `rsa_public_key` and silently rejects every JWT, so we assert the
PEM round-trips back to the exact same key and that the active key is selected.
"""
from __future__ import annotations

import base64
import json

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import load_pem_public_key

from ._loader import SERVICES, load_module_from_path

pytestmark = pytest.mark.unit

# main.py calls setup_observability() at import time (needs opentelemetry, which
# isn't installed for unit tests) — stub it. shared.atomic_io is real & pure.
jwks = load_module_from_path(
    "jwks_main",
    SERVICES / "jwks-refresher" / "main.py",
    extra_syspath=[SERVICES],
    stubs={"shared.observability": {"setup_observability": lambda *a, **k: None}},
)


def _b64url_uint(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _jwk(public_key: rsa.RSAPublicKey, kid: str, use: str = "sig") -> dict:
    nums = public_key.public_numbers()
    return {
        "kty": "RSA",
        "use": use,
        "kid": kid,
        "n": _b64url_uint(nums.n),
        "e": _b64url_uint(nums.e),
    }


class _FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *_exc) -> bool:
        return False

    def read(self) -> bytes:
        return self._payload


def _patch_jwks(monkeypatch, jwks_doc: dict) -> None:
    import urllib.request

    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *_a, **_k: _FakeResponse(json.dumps(jwks_doc).encode()),
    )


class TestB64UrlToInt:
    def test_roundtrip(self) -> None:
        assert jwks.b64url_to_int(_b64url_uint(65537)) == 65537

    def test_handles_missing_padding(self) -> None:
        # 'AQAB' is the standard base64url for exponent 65537 (no padding).
        assert jwks.b64url_to_int("AQAB") == 65537


class TestJwksToPem:
    def test_pem_roundtrips_to_same_key(self, monkeypatch) -> None:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048).public_key()
        _patch_jwks(monkeypatch, {"keys": [_jwk(key, "kid-1")]})

        kid, pem = jwks.jwks_to_pem("http://example/certs")

        assert kid == "kid-1"
        assert "-----BEGIN PUBLIC KEY-----" in pem
        loaded = load_pem_public_key(pem.encode())
        assert loaded.public_numbers() == key.public_numbers()

    def test_selects_last_sig_key(self, monkeypatch) -> None:
        old = rsa.generate_private_key(public_exponent=65537, key_size=2048).public_key()
        active = rsa.generate_private_key(public_exponent=65537, key_size=2048).public_key()
        _patch_jwks(
            monkeypatch,
            {"keys": [_jwk(old, "old"), _jwk(active, "active")]},
        )

        kid, pem = jwks.jwks_to_pem("http://example/certs")

        assert kid == "active"
        assert load_pem_public_key(pem.encode()).public_numbers() == active.public_numbers()

    def test_ignores_enc_keys_prefers_sig(self, monkeypatch) -> None:
        sig = rsa.generate_private_key(public_exponent=65537, key_size=2048).public_key()
        enc = rsa.generate_private_key(public_exponent=65537, key_size=2048).public_key()
        # enc key listed last; sig filter must still pick the sig key.
        _patch_jwks(
            monkeypatch,
            {"keys": [_jwk(sig, "sig-1", use="sig"), _jwk(enc, "enc-1", use="enc")]},
        )

        kid, _pem = jwks.jwks_to_pem("http://example/certs")
        assert kid == "sig-1"
