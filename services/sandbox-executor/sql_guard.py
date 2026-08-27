"""SELECT-only guard for agent-authored SQL (doc §1.5).

The Snowflake service account this hits is already USAGE+SELECT-only at the
warehouse level (no INSERT/UPDATE/DELETE/DDL grants) — this guard is the
second, cheaper layer: reject anything that isn't a read query before it
ever reaches the network, including the one read-shaped statement that still
mutates state: ``SELECT ... INTO``.

Two parsers, each doing what it is good at:

  * **sql_scan** decides where literals, comments and statements end. It models
    each engine's actual lexing rules, which sqlparse does not — sqlparse applies
    MySQL's backslash-escape rule inside '...' and so reads
    ``SELECT '\\'; DROP TABLE t; --'`` as one statement where Postgres reads two.
    Statement counting is the guard's central claim and cannot rest on that.
  * **sqlparse** decides the shape of the single statement that survives: does it
    start SELECT/WITH, does it contain DDL, a non-SELECT DML, or INTO.

They must also AGREE on how many statements there are. Disagreement means one of
the two is wrong about where a literal ends, and there is no way from inside here
to know which — so it fails closed. That is what makes the invariant hold rather
than merely be asserted.

`dialect` should be the backend the SQL is bound for. Omitted, every dialect's
reading must pass, because the readings differ and guessing one would be the
whole bug again.
"""
import sql_scan
import sqlparse
from sql_scan import DIALECTS, scan
from sqlparse.sql import Statement
from sqlparse.tokens import DDL, DML, Comment, Keyword


class SQLGuardError(Exception):
    pass


def _sqlparse_statements(sql: str) -> list[Statement]:
    return [s for s in sqlparse.parse(sql) if s.token_first(skip_ws=True, skip_cm=True)]


def _check_lexical(sql: str, dialect: str | None) -> None:
    """Boundaries, comments and paren balance — before any structural check.

    Runs first because every structural check below reads sqlparse's tokens, and
    those are only meaningful once the text is known to be a single statement
    with no comment hiding part of it.
    """
    targets = DIALECTS if dialect is None else (dialect,)

    for target in targets:
        result = scan(sql, target)
        label = "" if dialect is not None else f" (as {target})"

        if result.unterminated:
            raise SQLGuardError(f"unterminated {result.unterminated}{label}")
        if result.has_comment:
            # The backends wrap this SQL as `SELECT * FROM (\n{sql}\n) AS
            # _sandboxed_query LIMIT N`. A comment can swallow the closing paren
            # and the LIMIT, running the inner query unbounded. Rejected outright
            # rather than special-cased by position.
            raise SQLGuardError(f"comments are not allowed in sandboxed SQL{label}")
        if result.unbalanced_parens:
            # Balanced parens are what keep the statement inside that wrapper.
            raise SQLGuardError(f"unbalanced parentheses{label}")
        if result.statements == 0:
            raise SQLGuardError("empty statement")
        if result.statements > 1:
            raise SQLGuardError(f"multiple statements not allowed{label}")

    # Both parsers must reach the same count. If they differ, one of them is
    # wrong about where a literal ends — and the wrong one might be ours.
    parsed = _sqlparse_statements(sql)
    if len(parsed) != 1:
        raise SQLGuardError(
            "statement boundaries are ambiguous: sql_scan reads 1 statement, "
            f"sqlparse reads {len(parsed)}"
        )


def validate_select_only(sql: str, dialect: str | None = None) -> None:
    """Raise SQLGuardError unless `sql` is exactly one read-only statement.

    dialect: one of sql_scan.DIALECTS. None means "unknown" and is the strictest
    setting, not a lenient default.
    """
    if dialect is not None and dialect not in DIALECTS:
        raise SQLGuardError(f"unknown SQL dialect: {dialect!r}")

    _check_lexical(sql, dialect)

    stmt = _sqlparse_statements(sql)[0]
    first = stmt.token_first(skip_ws=True, skip_cm=True)
    first_word = (first.value or "").upper()

    # CTEs ("WITH ... SELECT") are allowed; everything else must start SELECT.
    if first_word not in ("SELECT", "WITH"):
        raise SQLGuardError(f"only SELECT (or WITH ... SELECT) statements are allowed, got: {first_word}")

    for token in stmt.flatten():
        if token.ttype is DDL:
            raise SQLGuardError(f"DDL token not allowed: {token.value}")
        if token.ttype is Keyword and token.value.upper() == "INTO":
            raise SQLGuardError("SELECT INTO is not allowed")
        if token.ttype is DML and token.value.upper() != "SELECT":
            raise SQLGuardError(f"DML token not allowed: {token.value}")
        if token.ttype in Comment:
            # _check_lexical already rejected comments using boundaries that
            # model the server. Kept as a second reading: if sqlparse sees a
            # comment our scanner placed inside a literal, the two disagree
            # about the text and the safe answer is no.
            raise SQLGuardError("comments are not allowed in sandboxed SQL")


__all__ = ["SQLGuardError", "sql_scan", "validate_select_only"]
