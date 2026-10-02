"""Fixtures shared by the integration suites (the Postgres container itself is in
tests/conftest.py)."""
from __future__ import annotations

import pytest

from tests.integration._db import migrated_database


@pytest.fixture
def migrated_db(postgres_container) -> dict[str, str]:
    """The suite's Postgres at alembic head, holding only what the migrations insert.

    The chain runs once per container; later tests get a truncate-and-reload of
    that state (tests.integration._db.migrated_database).
    """
    migrated_database(postgres_container["dsn"])
    return postgres_container
