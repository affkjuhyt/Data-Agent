"""SELECT-only guard for agent-authored SQL (doc §1.5).

The Snowflake service account this hits is already USAGE+SELECT-only at the
warehouse level (no INSERT/UPDATE/DELETE/DDL grants) — this guard is the
second, cheaper layer: reject anything that isn't a read query before it
ever reaches the network, including the one read-shaped statement that still
mutates state: ``SELECT ... INTO``.
"""
import sqlparse
from sqlparse.sql import Statement
from sqlparse.tokens import Comment, DDL, DML, Keyword


class SQLGuardError(Exception):
    pass


def validate_select_only(sql: str) -> None:
    statements = [s for s in sqlparse.parse(sql) if s.token_first(skip_ws=True, skip_cm=True)]
    if not statements:
        raise SQLGuardError("empty statement")
    if len(statements) > 1:
        raise SQLGuardError("multiple statements not allowed")

    stmt: Statement = statements[0]
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
        # snowflake_readonly.py wraps this SQL as a single-line f-string
        # subquery (`SELECT * FROM ({sql}) AS _sandboxed_query LIMIT N`) — a
        # trailing "--" line comment would swallow the closing paren and the
        # LIMIT clause, running the inner query unbounded. Reject comments
        # outright rather than trying to special-case "safe" placements.
        if token.ttype in Comment:
            raise SQLGuardError("comments are not allowed in sandboxed SQL")
