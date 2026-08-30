"""Unit tests for auth_api business logic in auth_core.

Covers the pure security-relevant helpers: API-key generation (prefix/scope
encoding, randomness), bcrypt hashing (verifiable, salted), OpenBao Transit key
naming (sanitization + length cap), and vault path layout. No DB / OpenBao /
Kong needed — these are the parts that should never silently regress.
"""
from __future__ import annotations

import bcrypt
import pytest

from ._loader import SERVICES, load_module_from_path

pytestmark = pytest.mark.unit

# auth_api lives in a hyphenated dir and imports `from shared.auth import ...`,
# so it needs services/ on sys.path. shared.auth only pulls in fastapi.
auth_core = load_module_from_path(
    "auth_core",
    SERVICES / "auth_api" / "auth_core.py",
    extra_syspath=[SERVICES],
)


class TestGenKey:
    def test_scope_prefixes(self) -> None:
        assert auth_core._gen_key("full_access")[1].startswith("sk-prod-")
        assert auth_core._gen_key("read_only")[1].startswith("sk-ro-")
        assert auth_core._gen_key("admin_platform")[1].startswith("sk-adm-")

    def test_unknown_scope_falls_back_to_key(self) -> None:
        _, prefix = auth_core._gen_key("nonsense")
        assert prefix.startswith("sk-key-")

    def test_full_key_starts_with_prefix(self) -> None:
        raw, prefix = auth_core._gen_key("read_only")
        assert raw.startswith(prefix)

    def test_full_key_length(self) -> None:
        # prefix = "sk-<scope>-" + 4 random letters, then 32 random chars.
        raw, prefix = auth_core._gen_key("full_access")
        assert len(raw) == len(prefix) + 32

    def test_keys_are_unique(self) -> None:
        keys = {auth_core._gen_key("read_only")[0] for _ in range(200)}
        assert len(keys) == 200, "generated keys collided — randomness broken"


class TestHashKey:
    def test_hash_verifies_with_bcrypt(self) -> None:
        raw = "sk-prod-abcd" + "x" * 32
        hashed = auth_core._hash_key(raw)
        assert bcrypt.checkpw(raw.encode(), hashed.encode())

    def test_wrong_key_does_not_verify(self) -> None:
        hashed = auth_core._hash_key("correct-key")
        assert not bcrypt.checkpw(b"wrong-key", hashed.encode())

    def test_same_input_distinct_hashes(self) -> None:
        # Distinct random salts → ciphertext differs even for identical input.
        assert auth_core._hash_key("same") != auth_core._hash_key("same")


class TestTransitKeyName:
    def test_lowercases_and_dashes(self) -> None:
        name = auth_core._transit_key_name("tenant-abcdef12", "Signing_Key One")
        assert name == "tenant-a-signing-key-one"

    def test_truncates_tenant_to_8_chars(self) -> None:
        name = auth_core._transit_key_name("0123456789abcdef", "k")
        assert name.startswith("01234567-")

    def test_caps_total_length_at_63(self) -> None:
        name = auth_core._transit_key_name("tenant12", "k" * 200)
        assert len(name) <= 63


class TestVaultPathAndConstants:
    def test_vault_path_layout(self) -> None:
        assert auth_core._vault_path("t1", "BEARER_TOKEN") == "tenants/t1/BEARER_TOKEN"

    def test_valid_scopes_match_prefix_map(self) -> None:
        assert auth_core.VALID_SCOPES == set(auth_core.SCOPE_PREFIX)

    def test_transit_types_are_valid_key_types(self) -> None:
        assert auth_core._TRANSIT_TYPES <= auth_core.VALID_KEY_TYPES
