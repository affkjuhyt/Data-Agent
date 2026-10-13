"""The read-only guard for SQL the worker runs against a source (BR-22).

Parsed, not pattern-matched: sqlglot reads the text the way the target engine does, so a literal
or a comment cannot hide a second statement. The guard admits exactly one statement whose root is
a query (SELECT, UNION/INTERSECT/EXCEPT, or a WITH over one), with no data-changing node anywhere
in the tree (a write inside a CTE included), no SELECT … INTO, no locking clause, and no call to a
function that reaches outside the query (files, sleeps, other connections, session settings).

It is the first of two locks: the executor also runs every statement in a READ ONLY transaction
with a statement timeout, so a construct the guard does not know still cannot write.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, TokenError

# sqlglot's dialect names for catalog.source.kind.
DIALECTS = {
    "postgresql": "postgres",
    "clickhouse": "clickhouse",
    "snowflake": "snowflake",
    "mysql": "mysql",
    "sqlserver": "tsql",
    "oracle": "oracle",
    "bigquery": "bigquery",
    "databricks": "databricks",
}

_WRITES: tuple[type[exp.Expression], ...] = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.TruncateTable,
    exp.Command,
    exp.Into,
    exp.Lock,
    exp.Set,
    exp.Transaction,
    exp.Commit,
    exp.Rollback,
)

# Only functions sqlglot models are admitted (aggregates, arithmetic, strings, dates); an unknown
# one is refused outright. These are refused even when modelled: an effect outside the result
# (files, sleeps, other connections, settings, large objects, locks). Lower-case names.
_DENIED_FUNCTIONS = frozenset(
    {
        "pg_sleep",
        "pg_sleep_for",
        "pg_sleep_until",
        "pg_read_file",
        "pg_read_binary_file",
        "pg_ls_dir",
        "pg_stat_file",
        "lo_import",
        "lo_export",
        "lo_get",
        "lo_put",
        "dblink",
        "dblink_exec",
        "dblink_connect",
        "set_config",
        "pg_terminate_backend",
        "pg_cancel_backend",
        "pg_advisory_lock",
        "pg_advisory_xact_lock",
        "pg_reload_conf",
        "pg_rotate_logfile",
        "query_to_xml",
        "copy",
        "sleep",
        "url",
        "file",
        "remote",
        "remotesecure",
        "s3",
        "system",
    }
)


class SqlRejected(ValueError):
    """The SQL is not a single read-only query; the message names why, never the data."""


def dialect_of(source_kind: str) -> str:
    try:
        return DIALECTS[source_kind]
    except KeyError as exc:
        raise SqlRejected(f"no SQL dialect for a {source_kind} source") from exc


def check_read_only(sql: str, dialect: str) -> exp.Expression:
    """The parsed statement when it is one read-only query; SqlRejected otherwise."""
    try:
        statements = [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
    # TokenError is not a ParseError: an unterminated literal or quoted identifier fails in the
    # tokenizer and would otherwise escape the guard instead of becoming a refusal.
    except (ParseError, TokenError) as exc:
        raise SqlRejected("the SQL does not parse") from exc
    if len(statements) != 1:
        raise SqlRejected("exactly one statement is allowed")
    # No comments at all: the compiler never writes one, and a comment is where text smuggled
    # into SQL hides from a reader (the release gate's comment-smuggling cases).
    if any(token.comments for token in sqlglot.tokenize(sql, read=dialect)):
        raise SqlRejected("comments are not allowed")
    tree = statements[0]
    if not isinstance(tree, exp.Query):
        raise SqlRejected("only a query is allowed")
    for node in tree.walk():
        if isinstance(node, _WRITES):
            raise SqlRejected(f"{node.key.upper()} is not allowed in a read-only query")
        if isinstance(node, exp.Anonymous):
            # A function sqlglot does not model (pg_*, dblink, an extension's, a user-defined
            # one) is refused whatever its name: a list of bad names is never complete.
            raise SqlRejected(f"function {node.name.lower()} is not allowed")
        if isinstance(node, exp.Func) and node.sql_name().lower() in _DENIED_FUNCTIONS:
            raise SqlRejected(f"function {node.sql_name().lower()} is not allowed")
        if isinstance(node, exp.Select) and node.args.get("locks"):
            raise SqlRejected("FOR UPDATE/SHARE is not allowed")
        if isinstance(node, exp.WithTableHint):
            # SQL Server table hints can request update/exclusive locks even though the statement
            # itself is a SELECT. Read-only callers do not get to alter source locking semantics.
            raise SqlRejected("table locking hints are not allowed")
    return tree


def capped(tree: exp.Expression, dialect: str, limit: int) -> str:
    """The query wrapped so the source returns at most `limit` rows: one more than shown tells the
    executor the result was truncated.

    What runs is this regenerated text, not the text that was checked, so it is checked again:
    it must pass the guard itself and regenerate to exactly itself. A construct sqlglot reads one
    way and writes another (a literal whose escaping it misreads) fails here instead of reaching
    the source in a form nobody inspected."""
    wrapped = exp.select("*").from_(exp.Subquery(this=tree, alias="_q")).limit(limit)
    sql = wrapped.sql(dialect=dialect)
    again = check_read_only(sql, dialect)
    if again.sql(dialect=dialect) != sql:
        raise SqlRejected("the SQL does not read back as itself")
    return sql
