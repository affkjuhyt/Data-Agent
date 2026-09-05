"""Identifier validation and quoting for semi-trusted table/column names.

Shared copy of the pattern in
backend/services/platform_workers/workers/schema_crawler/identifiers.py —
same problem shows up wherever a name that isn't a bind-parameter position
(a table or column name, not a value) comes from something other than a
literal in this codebase: an LLM's proposed schema, edited by a human,
before it goes into `CREATE TABLE`/`INSERT`. Both are validated as plain
identifiers first and quoted second; anything that isn't a bare identifier
is refused rather than sanitised, so a silently-rewritten name can never
end up pointing at a different object than the caller named.
"""
import re

# Deliberately narrower than what Postgres will accept: ASCII letters,
# digits and underscore, not starting with a digit.
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class UnsafeIdentifier(ValueError):
    """A name that will not be interpolated into SQL."""


def safe_identifier(name: str, kind: str = "identifier") -> str:
    """Return `name` unchanged, or raise. Never sanitises in place — a
    silently rewritten identifier would create/query a different object
    than the caller named."""
    if not name or not _IDENTIFIER_RE.match(name):
        raise UnsafeIdentifier(f"{kind} {name!r} is not a plain identifier")
    return name


def quote_identifier(name: str) -> str:
    """SQL-standard double quoting (Postgres)."""
    return '"' + name.replace('"', '""') + '"'
