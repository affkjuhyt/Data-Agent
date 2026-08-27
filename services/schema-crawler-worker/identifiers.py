"""Identifier validation and quoting for the watermark queries.

Every other statement in this worker reads INFORMATION_SCHEMA with bind
parameters. The freshness watermark cannot: `SELECT MAX(<column>) FROM <table>`
puts a column and a table name in positions no driver will bind, so they are
interpolated — and a table name comes from the warehouse's own catalog while a
column name comes from an operator's configuration file.

So both are validated as plain identifiers first and quoted second. Anything
that is not a bare identifier is refused rather than escaped: this worker has no
legitimate need to reach a table whose name requires quoting to be legal, and
refusing is a decision an operator can see in the log.

Its own module, not metadata.py, so the three engine modules can use it without
importing the dispatcher that imports them.
"""
import re

# Deliberately narrower than what any of these engines will accept: ASCII
# letters, digits and underscore, not starting with a digit.
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class UnsafeIdentifier(ValueError):
    """A name that will not be interpolated into SQL."""


def safe_identifier(name: str, kind: str = "identifier") -> str:
    """Return `name` unchanged, or raise. Never sanitises in place — a silently
    rewritten identifier would query a different object than the operator
    named."""
    if not name or not _IDENTIFIER_RE.match(name):
        raise UnsafeIdentifier(f"{kind} {name!r} is not a plain identifier")
    return name


def quote_identifier(name: str) -> str:
    """SQL-standard double quoting (Postgres, Snowflake)."""
    return '"' + name.replace('"', '""') + '"'


def backtick_identifier(name: str) -> str:
    """ClickHouse backtick quoting."""
    return "`" + name.replace("`", "\\`") + "`"
