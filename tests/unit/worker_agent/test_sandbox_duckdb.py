"""AL-05: the analysis code's SQL runs in DuckDB with its configuration locked. Ten escapes, none
through: at the boundary (the locked connection itself, the word check bypassed) and through the
code as the model writes it."""

from __future__ import annotations

import os
import tempfile

import pandas as pd
import pytest

from libs.code_sandbox import duck, local

pytestmark = [pytest.mark.unit, pytest.mark.req("AL-05")]

SALES = {
    "d1": {
        "columns": ["vung", "thang", "doanh_thu"],
        "rows": [["Bắc", "2013-01", 10.0], ["Bắc", "2013-02", 30.0], ["Nam", "2013-01", 5.0]],
    },
    "d2": {"columns": ["vung", "ten"], "rows": [["Bắc", "Miền Bắc"], ["Nam", "Miền Nam"]]},
}


async def test_sql_joins_and_windows_over_the_datasets() -> None:
    done = await local.run(
        'result = sql("SELECT d2.ten, sum(d1.doanh_thu) AS tong,'
        " rank() OVER (ORDER BY sum(d1.doanh_thu) DESC) AS hang"
        ' FROM d1 JOIN d2 USING (vung) GROUP BY d2.ten ORDER BY hang")',
        SALES,
    )
    assert done.ok, done.error
    assert done.columns == ["ten", "tong", "hang"]
    assert done.rows == [["Miền Bắc", 40.0, 1], ["Miền Nam", 5.0, 2]]


# The boundary: the locked connection refuses each of these even when the word check is skipped.
@pytest.mark.parametrize(
    "statement",
    [
        "SET enable_external_access=true",
        "SELECT * FROM read_csv('/etc/passwd')",
        "SELECT * FROM '/etc/passwd'",
        "ATTACH '{tmp}/x.db' AS x",
        "COPY (SELECT 1) TO '{tmp}/out.csv'",
        "INSTALL httpfs",
        "LOAD httpfs",
        "SELECT * FROM glob('/etc/*')",
    ],
)
def test_the_locked_connection_refuses_what_reaches_outside(statement: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        con = duck.open_locked({"d1": pd.DataFrame({"a": [1]})})
        with pytest.raises(Exception):  # noqa: B017 - duckdb raises its own error types
            con.execute(statement.format(tmp=tmp)).fetchall()
        assert os.listdir(tmp) == []
    assert con.execute("SELECT current_setting('enable_external_access')").fetchone() == (False,)


@pytest.mark.parametrize(
    "code",
    [
        "result = sql(\"ATTACH '/tmp/x.db' AS x\")",
        "result = sql(\"SELECT * FROM read_csv('/etc/passwd')\")",
        "result = sql(\"COPY (SELECT 1) TO 'out.csv'\")",
        "result = sql('INSTALL httpfs')",
        "result = sql(\"SELECT * FROM '/etc/passwd'\")",
        "result = sql('SELECT 1; SET enable_external_access=true')",
        "result = sql(\"SELECT * FROM glob('/*')\")",
        "result = sql(\"SELECT getenv('HOME') AS h\")",
    ],
)
async def test_the_code_cannot_reach_outside_through_sql(code: str) -> None:
    done = await local.run(code, SALES)
    assert not done.ok
    # Refused by the word check or by DuckDB, with its reason: not a child that died (which would
    # pass this test for the wrong reason; DuckDB aborts under too small an address space).
    assert done.error and done.error != "lỗi không rõ", done.error
    assert "passwd" not in str(done.rows) and "root:" not in done.stdout


@pytest.mark.parametrize(
    "code",
    ["import duckdb\nresult = duckdb.connect()", "result = sql.__closure__"],
)
def test_the_code_never_holds_duckdb_or_its_connection(code: str) -> None:
    with pytest.raises(local.CodeRejected):
        local.check(code)


def test_the_word_check_names_what_it_refused() -> None:
    assert duck.forbidden("SELECT * FROM d1") is None
    assert duck.forbidden("select load_kw from d1") is None
    assert duck.forbidden('SELECT "set" FROM d1') is None
    assert duck.forbidden("attach 'x.db'") == "attach"
    assert duck.forbidden("SELECT 1;\n set threads=8") is not None


def test_only_code_that_calls_sql_gets_duckdb_and_its_address_space() -> None:
    assert local.uses_sql("result = sql('SELECT 1 AS a')")
    assert not local.uses_sql("result = d1.groupby('vung').sum()")
    assert not local.uses_sql("result = d1['sql'].sum()")


async def test_naming_sql_buys_no_memory() -> None:
    """The address space DuckDB reserves is no room for the code: RLIMIT_DATA holds what it
    allocates under the pod's memory (security review of AL-05)."""
    done = await local.run(
        "import numpy as np\nunused = sql\nbig = np.ones(3 * 1024 ** 3 // 8)\nresult = 1", SALES
    )
    assert not done.ok and done.error and "MemoryError" in done.error, done.error
    fits = await local.run(
        "import numpy as np\nunused = sql\nok = np.ones(200 * 1024 ** 2 // 8)\nresult = 1", SALES
    )
    assert fits.ok, fits.error
