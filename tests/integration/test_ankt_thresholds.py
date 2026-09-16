"""The EXCLUDE constraint behind period-scoped thresholds (T4, ADR-T4).

`applicable_thresholds` picks at most one rule per level because the database
guarantees there is at most one. That guarantee is the thing under test here,
and it only exists in Postgres: the constraint has to refuse an overlapping row
no matter which writer sends it, so that "which ceiling applied to 2025" can
never have two answers.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import uuid
from collections.abc import Iterator
from decimal import Decimal
from urllib.parse import urlsplit, urlunsplit

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

asyncpg = pytest.importorskip("asyncpg", reason="asyncpg not installed")
pytest.importorskip("testcontainers", reason="testcontainers not installed")

pytestmark = pytest.mark.integration

TENANT = uuid.uuid4()
_APP_PASSWORD = "rls-test"


@pytest.fixture(scope="module")
def dsn() -> Iterator[str]:
    import psycopg2
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine") as container:
        owner = container.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")
        result = subprocess.run(
            [sys.executable, "-m", "alembic",
             "-c", str(REPO_ROOT / "backend" / "alembic" / "alembic.ini"),
             "upgrade", "head"],
            cwd=str(REPO_ROOT),
            env={**os.environ, "DATABASE_URL": owner},
            capture_output=True, text=True, timeout=180,
        )
        assert result.returncode == 0, result.stderr

        connection = psycopg2.connect(owner)
        connection.autocommit = True
        with connection.cursor() as cursor:
            cursor.execute(f"ALTER ROLE app_user PASSWORD '{_APP_PASSWORD}'")
        connection.close()

        parsed = urlsplit(owner)
        yield urlunsplit(parsed._replace(
            netloc=f"app_user:{_APP_PASSWORD}@{parsed.hostname}:{parsed.port}"))


@pytest.fixture
async def conn(dsn: str):
    connection = await asyncpg.connect(dsn)
    await connection.execute(f"SET app.tenant_id = '{TENANT}'")
    try:
        yield connection
    finally:
        for table in ("threshold", "indicator"):
            await connection.execute(f"DELETE FROM ankt.{table}")
        await connection.close()


async def _indicator(conn, code: str = "public_debt_to_gdp") -> str:
    return await conn.fetchval(
        """INSERT INTO ankt.indicator (tenant_id, code, name, unit, decimal_precision,
                                       stock_or_flow)
           VALUES ($1, $2, 'Nợ công so với GDP', 'percent', 1, 'ratio')
           RETURNING indicator_id""",
        TENANT, code,
    )


async def _threshold(conn, indicator_id, level: str, value: str,
                     from_year: int, to_year: int | None, label: str) -> str:
    return await conn.fetchval(
        """INSERT INTO ankt.threshold (tenant_id, indicator_id, level, operator, value,
                                       unit, valid_from_year, valid_to_year,
                                       legal_basis_label)
           VALUES ($1, $2, $3, '<=', $4, 'percent', $5, $6, $7) RETURNING threshold_id""",
        TENANT, indicator_id, level, Decimal(value), from_year, to_year, label,
    )


# ── the constraint ────────────────────────────────────────────────────────────

async def test_consecutive_periods_coexist(conn):
    """2021-2025 and 2026-2030 touch but do not overlap."""
    indicator_id = await _indicator(conn)

    await _threshold(conn, indicator_id, "limit", "60", 2021, 2025, "NQ 23/2021/QH15")
    await _threshold(conn, indicator_id, "limit", "60", 2026, 2030, "NQ 26/2026/QH16")

    assert await conn.fetchval("SELECT count(*) FROM ankt.threshold") == 2


async def test_two_limits_covering_the_same_year_are_refused(conn):
    """The ambiguity the constraint exists to make unrepresentable."""
    indicator_id = await _indicator(conn)
    await _threshold(conn, indicator_id, "limit", "60", 2021, 2025, "NQ 23/2021/QH15")

    with pytest.raises(asyncpg.ExclusionViolationError):
        await _threshold(conn, indicator_id, "limit", "55", 2025, 2030, "overlaps 2025")


async def test_an_open_ended_rule_blocks_every_later_one(conn):
    indicator_id = await _indicator(conn)
    await _threshold(conn, indicator_id, "limit", "60", 2026, None, "current")

    with pytest.raises(asyncpg.ExclusionViolationError):
        await _threshold(conn, indicator_id, "limit", "55", 2031, 2035, "later")


async def test_a_warning_may_share_a_period_with_a_limit(conn):
    """Different levels are different rules — both govern 2021-2025 at once."""
    indicator_id = await _indicator(conn)

    await _threshold(conn, indicator_id, "limit", "60", 2021, 2025, "NQ 23/2021/QH15")
    await _threshold(conn, indicator_id, "warning", "55", 2021, 2025, "NQ 23/2021/QH15")

    assert await conn.fetchval("SELECT count(*) FROM ankt.threshold") == 2


async def test_two_indicators_may_share_a_period(conn):
    debt = await _indicator(conn, "public_debt_to_gdp")
    service = await _indicator(conn, "debt_service_to_revenue")

    await _threshold(conn, debt, "limit", "60", 2021, 2025, "NQ 23/2021/QH15")
    await _threshold(conn, service, "limit", "25", 2021, 2025, "NQ 23/2021/QH15")

    assert await conn.fetchval("SELECT count(*) FROM ankt.threshold") == 2


async def test_a_range_that_ends_before_it_starts_is_refused(conn):
    """DataError, not CheckViolationError: Postgres builds the generated
    `applies_to` before it evaluates `threshold_years_ordered`, so int4range
    rejects the row first. The CHECK stays as the readable statement of intent
    — and as the guard if the generated column is ever reshaped."""
    indicator_id = await _indicator(conn)

    with pytest.raises(asyncpg.DataError, match="lower bound must be less than"):
        await _threshold(conn, indicator_id, "limit", "60", 2025, 2021, "backwards")

    assert await conn.fetchval("SELECT count(*) FROM ankt.threshold") == 0


# ── the generated range ───────────────────────────────────────────────────────

async def test_the_stored_range_is_inclusive_of_the_final_year(conn):
    """Authored as "2021-2025" the way a resolution is written; stored
    half-open so ranges are comparable."""
    indicator_id = await _indicator(conn)
    await _threshold(conn, indicator_id, "limit", "60", 2021, 2025, "NQ 23/2021/QH15")

    assert await conn.fetchval(
        "SELECT applies_to @> 2025 FROM ankt.threshold") is True
    assert await conn.fetchval(
        "SELECT applies_to @> 2026 FROM ankt.threshold") is False


async def test_an_open_end_stores_an_unbounded_range(conn):
    indicator_id = await _indicator(conn)
    await _threshold(conn, indicator_id, "limit", "60", 2026, None, "current")

    assert await conn.fetchval("SELECT upper_inf(applies_to) FROM ankt.threshold") is True


# ── selection reaches the library ─────────────────────────────────────────────

async def test_the_rule_in_force_for_a_period_is_the_one_returned(conn):
    """The R3 trap end to end: 2025 must never reach the 2026 resolution."""
    from libs.indicator.period import Period
    from libs.indicator.quantity import Quantity, Unit
    from libs.indicator.threshold import Level, Status, Threshold, assess

    indicator_id = await _indicator(conn)
    await _threshold(conn, indicator_id, "limit", "60", 2021, 2025, "NQ 23/2021/QH15")
    await _threshold(conn, indicator_id, "limit", "55", 2026, 2030, "NQ 26/2026/QH16")

    rows = await conn.fetch(
        """SELECT level, operator, value, unit, unit_multiplier, currency,
                  valid_from_year, valid_to_year, legal_basis_label
             FROM ankt.threshold WHERE indicator_id = $1""",
        indicator_id,
    )
    thresholds = [
        Threshold(
            Level(r["level"]), r["operator"],
            Quantity(Decimal(str(r["value"])), Unit(r["unit"]), r["unit_multiplier"],
                     r["currency"]),
            r["valid_from_year"], r["valid_to_year"], r["legal_basis_label"],
        )
        for r in rows
    ]

    # 57,0% clears the 2021-2025 ceiling of 60 and breaches the 2026-2030 one of
    # 55. Which answer is correct depends entirely on picking by data period.
    value = Quantity(Decimal("57.0"), Unit.PERCENT)
    in_2025 = assess(value, thresholds, Period.parse("2025"))
    in_2026 = assess(value, thresholds, Period.parse("2026"))

    assert (in_2025.status, in_2025.legal_basis) == (Status.OK, "NQ 23/2021/QH15")
    assert (in_2026.status, in_2026.legal_basis) == (Status.BREACH, "NQ 26/2026/QH16")
