"""Statement boundaries under each engine's real lexing rules.

The bug this exists to close: `validate_select_only` decided "is this one
statement?" with sqlparse, and sqlparse applies MySQL's rule that a backslash
inside '...' escapes the following quote. Postgres, with
`standard_conforming_strings = on` (the default since 9.1, and now pinned per
session in postgres_readonly), treats it as an ordinary character. So

    SELECT '\\'; DROP TABLE t; --'

is ONE statement to sqlparse and TWO to Postgres, and the guard's central claim —
"multiple statements not allowed" — was decided by a parser that does not model
the server it protects.

The dialects genuinely disagree with each other, not just with sqlparse: the very
same text is one statement to ClickHouse (backslash does escape there) and two to
Postgres. That is why the scanner takes a dialect and why "unknown" means "every
dialect's reading must pass" rather than "pick a lenient one".
"""
from __future__ import annotations

import pathlib
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SVC_DIR = str(REPO_ROOT / "backend" / "services" / "sandbox-executor")

pytestmark = pytest.mark.unit

# Built rather than typed: a literal backslash in a test file is one shell
# escaping accident away from silently testing something else. The original
# probe for this bug did exactly that and reported the guard as safe.
BS = chr(92)


@pytest.fixture(autouse=True)
def _sys_path():
    added = [p for p in (_SVC_DIR,) if p not in sys.path]
    sys.path[:0] = added
    yield
    for p in added:
        if p in sys.path:
            sys.path.remove(p)


class TestTheOriginalDivergence:
    def test_postgres_sees_two_statements_where_sqlparse_sees_one(self):
        """The bug, stated as an executable fact."""
        import sqlparse
        from sql_scan import POSTGRES, scan

        sql = "SELECT '" + BS + "'; DROP TABLE t; --'"

        sqlparse_count = len([
            s for s in sqlparse.parse(sql) if s.token_first(skip_ws=True, skip_cm=True)
        ])
        assert sqlparse_count == 1, "if this changes, sqlparse fixed it upstream"
        assert scan(sql, POSTGRES).statements == 2

    def test_the_guard_now_rejects_it(self):
        from sql_guard import SQLGuardError, validate_select_only

        sql = "SELECT '" + BS + "'; DROP TABLE t; --'"
        with pytest.raises(SQLGuardError):
            validate_select_only(sql, "postgres")
        with pytest.raises(SQLGuardError):
            validate_select_only(sql)

    def test_the_same_text_is_one_statement_to_clickhouse(self):
        """Not a false alarm suppressed — a real difference. ClickHouse does
        treat the backslash as an escape, so the literal swallows the rest and
        there is exactly one statement. Rejecting it there would be wrong."""
        from sql_scan import CLICKHOUSE, scan

        sql = "SELECT '" + BS + "'; DROP TABLE t; --'"
        assert scan(sql, CLICKHOUSE).statements == 1

        from sql_guard import validate_select_only

        validate_select_only(sql, "clickhouse")  # must not raise


class TestStringLiterals:
    @pytest.mark.parametrize("dialect", ["postgres", "clickhouse", "snowflake"])
    def test_a_semicolon_inside_a_literal_is_not_a_boundary(self, dialect):
        from sql_scan import scan
        assert scan("SELECT 'a;b;c' FROM t", dialect).statements == 1

    @pytest.mark.parametrize("dialect", ["postgres", "clickhouse", "snowflake"])
    def test_doubled_quotes_escape_everywhere(self, dialect):
        from sql_scan import scan
        assert scan("SELECT 'it''s; fine' FROM t", dialect).statements == 1

    def test_postgres_e_string_does_use_backslash_escapes(self):
        """E'...' is Postgres' opt-in to the escape rules the plain form lacks,
        so the boundary moves depending on which form was written."""
        from sql_scan import POSTGRES, scan
        assert scan("SELECT E'" + BS + "'; DROP TABLE t", POSTGRES).statements == 1
        assert scan("SELECT '" + BS + "'; DROP TABLE t", POSTGRES).statements == 2

    def test_a_trailing_e_on_an_identifier_is_not_an_escape_prefix(self):
        """In `VALUE'x'` the E belongs to VALUE — reading it as E'...' would
        move the string's end and mis-split the statement."""
        from sql_scan import POSTGRES, scan
        assert scan("SELECT VALUE'" + BS + "'; DROP TABLE t", POSTGRES).statements == 2

    def test_an_unterminated_literal_is_reported(self):
        from sql_scan import POSTGRES, scan
        assert scan("SELECT 'abc", POSTGRES).unterminated == "string literal"


class TestDollarQuoting:
    def test_postgres_dollar_quotes_hide_semicolons(self):
        from sql_scan import POSTGRES, scan
        assert scan("SELECT $$ ; DROP TABLE t; $$", POSTGRES).statements == 1
        assert scan("SELECT $tag$ ; DROP $tag$", POSTGRES).statements == 1

    def test_other_engines_have_no_dollar_quoting(self):
        """So the same text splits there — which is exactly why an unknown
        dialect must satisfy every reading."""
        from sql_scan import CLICKHOUSE, scan
        assert scan("SELECT $$ ; DROP TABLE t; $$", CLICKHOUSE).statements > 1

    def test_a_numeric_placeholder_is_not_a_tag(self):
        """`$1` is a bind parameter. Treating it as opening a dollar-quoted
        string would swallow the rest of the query."""
        from sql_scan import POSTGRES, scan
        assert scan("SELECT * FROM t WHERE a = $1; DROP TABLE t", POSTGRES).statements == 2

    def test_an_unclosed_tag_is_reported_not_silently_consumed(self):
        from sql_scan import POSTGRES, scan
        assert "dollar-quoted" in scan("SELECT $tag$ ; DROP $other$", POSTGRES).unterminated


class TestComments:
    @pytest.mark.parametrize("sql", [
        "SELECT 1 -- tail",
        "SELECT /* mid */ 1",
        "SELECT 1 /* unterminated",
    ])
    def test_comments_are_seen(self, sql):
        from sql_scan import POSTGRES, scan
        assert scan(sql, POSTGRES).has_comment is True

    def test_a_comment_marker_inside_a_literal_is_not_a_comment(self):
        """The distinction the old guard could not make reliably: whether `--`
        is code depends on where the literal ends."""
        from sql_scan import POSTGRES, scan
        assert scan("SELECT '-- not a comment' FROM t", POSTGRES).has_comment is False

    def test_postgres_nests_block_comments(self):
        from sql_scan import POSTGRES, scan
        result = scan("SELECT /* a /* b */ c */ 1", POSTGRES)
        assert result.has_comment is True
        assert result.unterminated == ""


class TestParenBalance:
    def test_balanced_is_fine(self):
        from sql_scan import POSTGRES, scan
        assert scan("SELECT (1 + (2 * 3)) FROM t", POSTGRES).unbalanced_parens is False

    @pytest.mark.parametrize("sql", ["SELECT 1) AS x", "SELECT (1", "SELECT ((1)"])
    def test_unbalanced_is_flagged(self, sql):
        """Balance is what keeps the statement inside the backends' wrapper —
        `SELECT * FROM (\\n{sql}\\n) AS _sandboxed_query LIMIT N`. An extra
        closing paren ends the subquery early and sheds the LIMIT."""
        from sql_scan import POSTGRES, scan
        assert scan(sql, POSTGRES).unbalanced_parens is True

    def test_parens_inside_literals_do_not_count(self):
        from sql_scan import POSTGRES, scan
        assert scan("SELECT '((((' FROM t", POSTGRES).unbalanced_parens is False


class TestStatementCounting:
    @pytest.mark.parametrize("sql,expected", [
        ("SELECT 1", 1),
        ("SELECT 1;", 1),
        ("SELECT 1;;;", 1),
        ("  ;  ", 0),
        ("", 0),
        ("SELECT 1; SELECT 2", 2),
        ("SELECT 1; SELECT 2;", 2),
    ])
    def test_counts(self, sql, expected):
        from sql_scan import POSTGRES, scan
        assert scan(sql, POSTGRES).statements == expected


class TestGuardIntegration:
    """The guard's own contract, now that boundaries come from sql_scan."""

    @pytest.mark.parametrize("sql", [
        "SELECT * FROM t",
        "select a, b from t where a > 1 order by b limit 10",
        "WITH cte AS (SELECT 1 AS x) SELECT * FROM cte",
        "SELECT a, (SELECT MAX(b) FROM t2) AS m FROM t1",
        "SELECT 'a;b' FROM t",
        "SELECT 'it''s' FROM t",
        'SELECT "odd name" FROM "My Table"',
        "SELECT 1;",
    ])
    def test_ordinary_reads_still_pass_with_no_dialect_given(self, sql):
        from sql_guard import validate_select_only
        validate_select_only(sql)

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
        "WITH d AS (DELETE FROM t RETURNING *) SELECT * FROM d",
        "WITH i AS (INSERT INTO t VALUES (1) RETURNING *) SELECT * FROM i",
        "COPY t TO PROGRAM 'sh -c id'",
        "MERGE INTO t USING s ON t.id = s.id WHEN MATCHED THEN DELETE",
        "",
        "   ",
        "-- only a comment",
    ])
    def test_writes_and_non_reads_are_rejected(self, sql):
        from sql_guard import SQLGuardError, validate_select_only
        with pytest.raises(SQLGuardError):
            validate_select_only(sql)

    @pytest.mark.parametrize("sql", [
        "SELECT * FROM t -- LIMIT bypass",
        "SELECT a, b FROM t -- comment\nWHERE a > 1",
        "SELECT /* inline */ a FROM t",
        "SELECT a FROM t /* trailing block comment */",
        "WITH cte AS (SELECT 1 AS x) -- comment\nSELECT * FROM cte",
    ])
    def test_comments_are_rejected(self, sql):
        from sql_guard import SQLGuardError, validate_select_only
        with pytest.raises(SQLGuardError):
            validate_select_only(sql)

    def test_a_wrapper_escape_attempt_is_rejected(self):
        """Shedding the LIMIT rather than writing anything: end the subquery
        early, then comment out the wrapper's own tail."""
        from sql_guard import SQLGuardError, validate_select_only
        sql = "SELECT '" + BS + "') AS x LIMIT 999999999 /*'"
        with pytest.raises(SQLGuardError):
            validate_select_only(sql, "postgres")

    def test_an_unknown_dialect_is_refused_rather_than_defaulted(self):
        """Silently falling back to one engine's rules is the original bug."""
        from sql_guard import SQLGuardError, validate_select_only
        with pytest.raises(SQLGuardError, match="unknown SQL dialect"):
            validate_select_only("SELECT 1", "mysql")

    def test_no_dialect_is_the_strictest_setting_not_a_lenient_one(self):
        """Postgres-only syntax passes when Postgres is named, and is refused
        when the engine is unknown — because it does not parse the same way
        everywhere."""
        from sql_guard import SQLGuardError, validate_select_only
        sql = "SELECT $$ ; $$"
        validate_select_only(sql, "postgres")
        with pytest.raises(SQLGuardError):
            validate_select_only(sql)

    def test_the_two_parsers_must_agree_on_the_count(self):
        """A backstop for the scanner itself: if sqlparse and sql_scan disagree,
        one of them is wrong about where a literal ends and it might be ours, so
        the guard fails closed instead of picking a winner."""
        import sql_guard

        original = sql_guard._sqlparse_statements
        try:
            sql_guard._sqlparse_statements = lambda sql: [object(), object()]
            with pytest.raises(sql_guard.SQLGuardError, match="ambiguous"):
                sql_guard.validate_select_only("SELECT 1", "postgres")
        finally:
            sql_guard._sqlparse_statements = original


class TestDialectsAreWiredToBackends:
    def test_every_backend_names_its_own_dialect(self):
        """A backend passing the wrong dialect — or none — reintroduces the bug
        for that engine only, which is the hardest version to notice."""
        import ast

        expected = {
            "postgres_readonly.py": "postgres",
            "clickhouse_readonly.py": "clickhouse",
            "snowflake_readonly.py": "snowflake",
        }
        for filename, dialect in expected.items():
            tree = ast.parse((pathlib.Path(_SVC_DIR) / filename).read_text(encoding="utf-8"))
            calls = [
                node for node in ast.walk(tree)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "validate_select_only"
            ]
            assert calls, f"{filename} no longer calls the guard"
            for call in calls:
                assert len(call.args) == 2, f"{filename} calls the guard without a dialect"
                assert isinstance(call.args[1], ast.Constant)
                assert call.args[1].value == dialect, f"{filename} names the wrong dialect"

    def test_the_dispatcher_passes_the_request_backend(self):
        import ast

        tree = ast.parse((pathlib.Path(_SVC_DIR) / "main.py").read_text(encoding="utf-8"))
        calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "validate_select_only"
        ]
        assert len(calls) == 1
        assert len(calls[0].args) == 2
        assert isinstance(calls[0].args[1], ast.Name)
        assert calls[0].args[1].id == "backend"

    def test_backend_names_and_dialect_names_are_the_same_set(self):
        """main.py dispatches on these strings and hands the same value to the
        guard; if the two vocabularies drift, every query fails closed on an
        unknown dialect."""
        import ast

        from sql_scan import DIALECTS

        tree = ast.parse((pathlib.Path(_SVC_DIR) / "main.py").read_text(encoding="utf-8"))
        load_backend = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "_load_backend"
        )
        names = {
            node.value for node in ast.walk(load_backend)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and node.value in set(DIALECTS)
        }
        assert names == set(DIALECTS)


class TestPostgresPinsItsAssumption:
    def test_standard_conforming_strings_is_set_on_the_session(self):
        """sql_scan's Postgres rule is only correct while that GUC is on. It is
        the default, but a tenant's database can turn it off — and then the
        guard would be reasoning about a different language than the server
        speaks."""
        import ast

        tree = ast.parse((pathlib.Path(_SVC_DIR) / "postgres_readonly.py").read_text(encoding="utf-8"))
        connect = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == "connect"
        )
        settings = next(k for k in connect.keywords if k.arg == "server_settings")
        keys = {k.value for k in settings.value.keys if isinstance(k, ast.Constant)}
        assert "standard_conforming_strings" in keys
        assert "statement_timeout" in keys
