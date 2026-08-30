"""validate_select_only (sandbox_schema_docs.docx §1.5) — the second,
cheaper layer in front of the read-only Snowflake service account: rejects
anything that isn't a plain SELECT/CTE before it ever reaches the network.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SVC_DIR = str(REPO_ROOT / "backend" / "services" / "sandbox-executor")


@pytest.fixture(autouse=True)
def _sys_path():
    added = [p for p in (_SVC_DIR,) if p not in sys.path]
    sys.path[:0] = added
    yield
    for p in added:
        if p in sys.path:
            sys.path.remove(p)


@pytest.mark.unit
@pytest.mark.parametrize("sql", [
    "SELECT * FROM t",
    "select * from t",
    "SELECT a, b FROM t WHERE a > 1 ORDER BY b LIMIT 10",
    "WITH cte AS (SELECT 1 AS x) SELECT * FROM cte",
    "SELECT a, (SELECT MAX(b) FROM t2) AS m FROM t1",
])
def test_select_and_cte_pass(sql):
    from sql_guard import validate_select_only
    validate_select_only(sql)  # must not raise


@pytest.mark.unit
@pytest.mark.parametrize("sql", [
    "DROP TABLE t",
    "DELETE FROM t",
    "INSERT INTO t VALUES (1)",
    "UPDATE t SET a = 1",
    "TRUNCATE TABLE t",
    "ALTER TABLE t ADD COLUMN x INT",
    "CREATE TABLE t2 (x INT)",
    "SELECT * INTO new_table FROM t",
    "SELECT 1; DROP TABLE t",
    "",
    "   ",
])
def test_non_select_statements_are_rejected(sql):
    from sql_guard import validate_select_only, SQLGuardError
    with pytest.raises(SQLGuardError):
        validate_select_only(sql)


@pytest.mark.unit
@pytest.mark.parametrize("sql", [
    # The exact shape flagged in review: a trailing "--" comment would
    # swallow the wrapping query's closing paren + LIMIT clause in
    # snowflake_readonly.py, running the inner query unbounded.
    "SELECT * FROM t -- LIMIT bypass",
    "SELECT a, b FROM t -- comment\nWHERE a > 1",
    "SELECT /* inline */ a FROM t",
    "SELECT a FROM t /* trailing block comment */",
    "WITH cte AS (SELECT 1 AS x) -- comment\nSELECT * FROM cte",
])
def test_comments_are_rejected(sql):
    from sql_guard import validate_select_only, SQLGuardError
    with pytest.raises(SQLGuardError):
        validate_select_only(sql)
