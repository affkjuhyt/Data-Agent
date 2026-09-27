"""Unit tests for shared.db_context._validated_uuid.

This is the SQL-injection defense for tenant-scoped RLS: the tenant id is
interpolated into ``SET app.tenant_id`` / passed to ``set_config``, so anything
that isn't a real UUID must be rejected before it reaches Postgres.
"""
from __future__ import annotations

import pytest

from libs.shared.db_context import _validated_uuid

pytestmark = pytest.mark.unit

VALID = "12345678-1234-1234-1234-123456789abc"


def test_accepts_valid_uuid_and_returns_it() -> None:
    assert _validated_uuid(VALID) == VALID


def test_accepts_uppercase_uuid() -> None:
    assert _validated_uuid(VALID.upper()) == VALID.upper()


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "not-a-uuid",
        "12345678-1234-1234-1234-123456789ab",   # too short
        "12345678-1234-1234-1234-123456789abcd",  # too long
        "12345678_1234_1234_1234_123456789abc",   # wrong separators
        "'; DROP TABLE api_keys; --",             # injection payload
        f"{VALID}'; DROP TABLE x; --",            # valid prefix + payload
        "g2345678-1234-1234-1234-123456789abc",   # non-hex char
    ],
)
def test_rejects_invalid_input(bad: str) -> None:
    with pytest.raises(ValueError):
        _validated_uuid(bad)


@pytest.mark.parametrize("not_str", [None, 123, b"12345678-1234-1234-1234-123456789abc", ["x"]])
def test_rejects_non_string(not_str: object) -> None:
    with pytest.raises(ValueError):
        _validated_uuid(not_str)  # type: ignore[arg-type]
