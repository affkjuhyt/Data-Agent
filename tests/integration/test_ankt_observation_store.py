"""Point-in-time reads over the ankt observation store (T2 in docs/ankt/TECH-DESIGN.md).

Runs the real migration against a real Postgres, because the three properties
under test are enforced by the database, not by the code that calls it: the
append-only trigger, the natural-key uniqueness, and the RLS policy that the
schema-wide loop in a1b2c3d4e5f6 does not reach.

The central scenario is the one TECH-DESIGN T2 prescribes — load three vintages
of one period, and assert an `as_of` read returns the version that was current
at each moment.
"""
from __future__ import annotations

import datetime as dt
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

from libs.indicator.formula import (  # noqa: E402
    Alignment,
    AsPercent,
    Ratio,
    StockOrFlow,
    Var,
    VariableSpec,
    evaluate,
)
from libs.indicator.observation import (  # noqa: E402
    ObservationNotFound,
    ObservationStore,
    resolve_from,
)
from libs.indicator.period import Period  # noqa: E402
from libs.indicator.quantity import Unit  # noqa: E402

pytestmark = pytest.mark.integration

TENANT = uuid.uuid4()
OTHER_TENANT = uuid.uuid4()


_APP_PASSWORD = "rls-test"


@pytest.fixture(scope="module")
def dsn() -> Iterator[str]:
    """A migrated container, returning the `app_user` DSN — not the owner's.

    Every test connects the way the services do. A superuser or BYPASSRLS role
    would make each assertion below pass with the row-level policy inert, so
    the RLS test would be green whether or not a policy existed at all.
    """
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
            capture_output=True,
            text=True,
            timeout=180,
        )
        # Surfaced rather than swallowed by check=True: a migration that fails
        # here is the thing under test, and its stderr is the whole diagnosis.
        assert result.returncode == 0, result.stderr

        # a1b2c3d4e5f6 creates app_user LOGIN NOBYPASSRLS with no password,
        # deliberately — production sets it from the vault.
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
        # DELETE, not TRUNCATE: app_user has CRUD but not the owner privilege,
        # and the policy scopes the delete to this tenant, which is the point.
        for table in ("observation", "release", "source_document", "variable"):
            await connection.execute(f"DELETE FROM ankt.{table}")
        await connection.close()


async def _seed_variable(conn, code: str, unit: str = "money",
                         stock_or_flow: str = "stock", currency: str | None = "VND") -> str:
    return await conn.fetchval(
        """INSERT INTO ankt.variable (tenant_id, code, unit, unit_multiplier,
                                      currency, stock_or_flow)
           VALUES ($1, $2, $3, 12, $4, $5) RETURNING variable_id""",
        TENANT, code, unit, currency, stock_or_flow,
    )


async def _seed_release(conn, release_date: dt.date, label: str, status: str) -> str:
    return await conn.fetchval(
        """INSERT INTO ankt.release (tenant_id, release_date, vintage_label, revision_status)
           VALUES ($1, $2, $3, $4) RETURNING release_id""",
        TENANT, release_date, label, status,
    )


async def _seed_observation(conn, variable_id, release_id, period: str, value: str,
                            status: str = "approved") -> str:
    return await conn.fetchval(
        """INSERT INTO ankt.observation (tenant_id, variable_id, release_id, period,
                                         value, unit, unit_multiplier, currency, status)
           VALUES ($1, $2, $3, $4, $5, 'money', 12, 'VND', $6) RETURNING obs_id""",
        TENANT, variable_id, release_id, period, Decimal(value), status,
    )


async def _seed_three_vintages(conn) -> str:
    """The same 2024 figure as estimate, preliminary and official."""
    variable_id = await _seed_variable(conn, "debt")
    for date, label, status, value in [
        (dt.date(2025, 3, 1), "2025-03 estimate", "estimate", "3000.0"),
        (dt.date(2025, 7, 1), "2025-07 preliminary", "preliminary", "3080.0"),
        (dt.date(2026, 1, 15), "2026-01 official", "official", "3109.2"),
    ]:
        release_id = await _seed_release(conn, date, label, status)
        await _seed_observation(conn, variable_id, release_id, "2024", value)
    return variable_id


# ── point in time ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("as_of", "expected", "status"), [
    (dt.date(2025, 4, 1), "3000.0", "estimate"),
    (dt.date(2025, 8, 1), "3080.0", "preliminary"),
    (dt.date(2026, 6, 1), "3109.2", "official"),
])
async def test_as_of_returns_the_version_current_at_that_moment(conn, as_of, expected, status):
    await _seed_three_vintages(conn)
    store = ObservationStore(conn)

    observation = await store.value_as_of("debt", Period.parse("2024"), as_of)

    assert observation.quantity.value == Decimal(expected)
    assert observation.revision_status == status


async def test_a_date_before_the_first_release_has_no_answer(conn):
    """Not zero, not the earliest figure — the number did not exist yet."""
    await _seed_three_vintages(conn)
    store = ObservationStore(conn)

    with pytest.raises(ObservationNotFound):
        await store.value_as_of("debt", Period.parse("2024"), dt.date(2025, 1, 1))


async def test_a_revision_leaves_the_superseded_figure_readable(conn):
    """What makes an earlier answer reproducible after the number changes."""
    await _seed_three_vintages(conn)
    store = ObservationStore(conn)

    triangle = await store.vintages("debt", Period.parse("2024"))

    assert [str(o.quantity.value) for o in triangle] == ["3000.000000", "3080.000000",
                                                         "3109.200000"]
    assert [o.revision_status for o in triangle] == ["estimate", "preliminary", "official"]


async def test_unapproved_figures_are_invisible(conn):
    """A candidate is a number a machine proposed and nobody checked."""
    variable_id = await _seed_variable(conn, "debt")
    release_id = await _seed_release(conn, dt.date(2025, 3, 1), "v1", "estimate")
    await _seed_observation(conn, variable_id, release_id, "2024", "9999.9", status="candidate")
    store = ObservationStore(conn)

    with pytest.raises(ObservationNotFound):
        await store.value_as_of("debt", Period.parse("2024"), dt.date(2026, 1, 1))


# ── database-enforced guarantees ──────────────────────────────────────────────

async def test_the_same_release_cannot_be_ingested_twice(conn):
    """Re-running a connector over one release must not duplicate a figure."""
    variable_id = await _seed_variable(conn, "debt")
    release_id = await _seed_release(conn, dt.date(2025, 3, 1), "v1", "estimate")
    await _seed_observation(conn, variable_id, release_id, "2024", "3000.0")

    with pytest.raises(asyncpg.UniqueViolationError):
        await _seed_observation(conn, variable_id, release_id, "2024", "3000.0")


async def test_a_published_figure_cannot_be_rewritten(conn):
    variable_id = await _seed_variable(conn, "debt")
    release_id = await _seed_release(conn, dt.date(2025, 3, 1), "v1", "estimate")
    obs_id = await _seed_observation(conn, variable_id, release_id, "2024", "3000.0")

    with pytest.raises(asyncpg.RaiseError, match="append-only"):
        await conn.execute("UPDATE ankt.observation SET value = $1 WHERE obs_id = $2",
                           Decimal("1.0"), obs_id)


async def test_review_may_still_change_status(conn):
    """Approving is not rewriting — the trigger must not block the workbench."""
    variable_id = await _seed_variable(conn, "debt")
    release_id = await _seed_release(conn, dt.date(2025, 3, 1), "v1", "estimate")
    obs_id = await _seed_observation(conn, variable_id, release_id, "2024", "3000.0",
                                     status="candidate")

    await conn.execute(
        "UPDATE ankt.observation SET status = 'approved', reviewed_by = $1 WHERE obs_id = $2",
        "steward@example.gov.vn", obs_id)

    store = ObservationStore(conn)
    assert (await store.value_as_of("debt", Period.parse("2024"),
                                    dt.date(2026, 1, 1))).quantity.value == Decimal("3000.0")


async def test_another_tenant_sees_nothing(conn, dsn):
    """RLS in the ankt schema: the policy loop in a1b2c3d4e5f6 covers only
    `public`, so these tables would otherwise have no policy at all."""
    await _seed_three_vintages(conn)

    other = await asyncpg.connect(dsn)
    try:
        await other.execute(f"SET app.tenant_id = '{OTHER_TENANT}'")
        assert await other.fetchval("SELECT count(*) FROM ankt.observation") == 0
    finally:
        await other.close()


async def test_money_without_a_currency_is_rejected(conn):
    with pytest.raises(asyncpg.CheckViolationError):
        await _seed_variable(conn, "broken", unit="money", currency=None)


# ── the seam into the formula layer ───────────────────────────────────────────

async def test_debt_to_gdp_computed_from_stored_observations(conn):
    """T1 + T3 + T2 together: two stored figures become the published ratio."""
    debt_id = await _seed_variable(conn, "debt", stock_or_flow="stock")
    gdp_id = await _seed_variable(conn, "gdp", stock_or_flow="flow")
    release_id = await _seed_release(conn, dt.date(2026, 1, 15), "2026-01", "official")
    await _seed_observation(conn, debt_id, release_id, "2024", "3109.2")
    await _seed_observation(conn, gdp_id, release_id, "2024", "8960.2")

    store = ObservationStore(conn)
    period = Period.parse("2024")
    observations = await store.prefetch(["debt", "gdp"], [period], dt.date(2026, 6, 1))

    specs = {
        "debt": VariableSpec("debt", Unit.MONEY, StockOrFlow.STOCK, 12, "VND"),
        "gdp": VariableSpec("gdp", Unit.MONEY, StockOrFlow.FLOW, 12, "VND"),
    }
    formula = AsPercent(Ratio(Var("debt"), Var("gdp"), Alignment.SAME_CALENDAR_YEAR))
    result = evaluate(formula, specs, resolve_from(observations), period).round_to(1)

    assert (result.value, result.unit) == (Decimal("34.7"), Unit.PERCENT)


async def test_an_earlier_vintage_yields_the_earlier_answer(conn):
    """The property the whole store exists for: the same question, asked as of
    two dates, gives two defensible answers rather than one unexplained one."""
    debt_id = await _seed_variable(conn, "debt", stock_or_flow="stock")
    gdp_id = await _seed_variable(conn, "gdp", stock_or_flow="flow")
    early = await _seed_release(conn, dt.date(2025, 3, 1), "2025-03", "estimate")
    late = await _seed_release(conn, dt.date(2026, 1, 15), "2026-01", "official")
    await _seed_observation(conn, debt_id, early, "2024", "3000.0")
    await _seed_observation(conn, gdp_id, early, "2024", "8310.8")
    await _seed_observation(conn, debt_id, late, "2024", "3109.2")
    await _seed_observation(conn, gdp_id, late, "2024", "8960.2")

    store = ObservationStore(conn)
    period = Period.parse("2024")
    specs = {
        "debt": VariableSpec("debt", Unit.MONEY, StockOrFlow.STOCK, 12, "VND"),
        "gdp": VariableSpec("gdp", Unit.MONEY, StockOrFlow.FLOW, 12, "VND"),
    }
    formula = AsPercent(Ratio(Var("debt"), Var("gdp"), Alignment.SAME_CALENDAR_YEAR))

    async def ratio_as_of(as_of: dt.date) -> Decimal:
        observed = await store.prefetch(["debt", "gdp"], [period], as_of)
        return evaluate(formula, specs, resolve_from(observed), period).round_to(1).value

    assert await ratio_as_of(dt.date(2025, 6, 1)) == Decimal("36.1")
    assert await ratio_as_of(dt.date(2026, 6, 1)) == Decimal("34.7")
