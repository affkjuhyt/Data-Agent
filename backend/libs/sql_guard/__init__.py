"""The read-only guard for SQL run against a source (BR-22), shared by the api and worker-agent.

Parsed, not pattern-matched: sqlglot reads the text the way the target engine does, so a literal
or a comment cannot hide a second statement. The guard admits exactly one statement whose root is
a query (SELECT, UNION/INTERSECT/EXCEPT, or a WITH over one), with no data-changing node anywhere
in the tree (a write inside a CTE included), no SELECT … INTO, no locking clause, and no call to a
function that reaches outside the query (files, sleeps, other connections, session settings).

It is the first of two locks: the executor also runs every statement in a READ ONLY transaction
with a statement timeout, so a construct the guard does not know still cannot write. The api runs
it too, before anything is queued, to tell the author at once (read_only_problem).
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp
from sqlglot.errors import OptimizeError, ParseError, TokenError
from sqlglot.optimizer.scope import traverse_scope

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


# Pure functions sqlglot 25 does not model, so they parse as Anonymous: each computes a value from
# its arguments and nothing else. Without them a pipeline step that cleans a number written
# "1.234,5" (replace) could not run. Their arguments are still walked like any other node.
_PURE_UNMODELLED = frozenset(
    {
        "replace",
        "split_part",
        "translate",
        "btrim",
        "strpos",
        "reverse",
        "trunc",
        "make_date",
        "age",
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
        if isinstance(node, exp.Anonymous) and (
            node.name.lower() not in _PURE_UNMODELLED or isinstance(node.parent, exp.Dot)
        ):
            # A function sqlglot does not model (pg_*, dblink, an extension's, a user-defined
            # one) is refused whatever its name: a list of bad names is never complete. An
            # allowlisted name counts only unqualified, where the built-in wins the lookup:
            # some_schema.replace(...) is whatever that schema's owner wrote.
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


def tables_of(tree: exp.Expression, dialect: str) -> set[tuple[str | None, str]]:
    """The (schema, table) pairs a checked query reads; schema None when it is not written.

    A CTE's name is not a table, but only where that CTE is visible: names are resolved scope by
    scope (sqlglot's scope analysis), so a CTE inside a subquery cannot hide a real table of the
    same name elsewhere in the statement. A table node the scope walk does not reach (sqlglot
    skips a subquery in double parentheses) counts as a table whatever its name: counting too
    much only makes the caller's allowlist refuse, never admit. A table function (generate_series) is not a table. A name in another database
    (catalog.schema.table) is refused: a source is one database. PostgreSQL folds an unquoted name
    to lower case and keeps a quoted one, so the name is the one it resolves to; the other engines
    compare names without case. PostgreSQL also searches pg_catalog before the search path, so an
    unqualified pg_* name is refused rather than guessed."""
    try:
        scopes = traverse_scope(tree)
    except OptimizeError as exc:
        raise SqlRejected("the tables of the SQL cannot be resolved") from exc
    fold = dialect == "postgres"

    def name(ident: exp.Identifier) -> str:
        text = str(ident.this)
        return text if fold and ident.quoted else text.lower()

    seen: set[int] = set()
    found: set[tuple[str | None, str]] = set()

    def add(table: exp.Table) -> None:
        if table.args.get("catalog"):
            raise SqlRejected("a table in another database is not allowed")
        schema = table.args.get("db")
        if schema is None and fold and table.name.lower().startswith("pg_"):
            raise SqlRejected("write the schema of a pg_* table")
        found.add((name(schema) if isinstance(schema, exp.Identifier) else None, name(table.this)))

    for scope in scopes:
        for table in scope.tables:
            seen.add(id(table))
            if not isinstance(table.this, exp.Identifier):
                continue
            if table.args.get("db") is None and table.name in scope.cte_sources:
                continue
            add(table)
    for table in tree.find_all(exp.Table):
        if isinstance(table.this, exp.Identifier) and id(table) not in seen:
            add(table)
    return found


def read_only_problem(sql: str) -> str | None:
    """Why `sql` is not one read-only query, None when it is — for the api, which takes the SQL
    before it knows the source. It passes when it is read-only in any supported dialect: the worker
    checks it again in the source's own (check_read_only), so a dialect's syntax is not refused
    here, while a write, a second statement or a comment is refused in all of them."""
    reasons: list[str] = []
    for dialect in dict.fromkeys(DIALECTS.values()):
        try:
            check_read_only(sql, dialect)
        except SqlRejected as exc:
            reasons.append(str(exc))
            continue
        return None
    # The engine most sources speak first: its reason is the one an author can act on.
    return reasons[0]
