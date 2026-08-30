"""Where do string literals, comments and statements actually end?

`validate_select_only` used to answer that with sqlparse alone, and sqlparse
gets it wrong for Postgres in a way that matters. sqlparse treats a backslash
inside an ordinary '...' literal as escaping the following quote — MySQL's rule.
Postgres, with `standard_conforming_strings = on` (the default since 9.1), treats
it as an ordinary character. So for::

    SELECT '\\'; DROP TABLE t; --'

sqlparse sees ONE statement whose literal runs to the end. Postgres sees TWO: the
literal is a single backslash, and `DROP TABLE t` follows. The guard's central
claim — "multiple statements not allowed" — was decided by a parser that does not
model the server it is protecting.

Nothing was exploitable through it today: asyncpg's extended protocol refuses
multi-command strings, the Snowflake connector requires an explicit
`num_statements`, and the DB role has no write grants. But those are three
incidental defences standing in for an invariant the guard states and could not
keep, and the wrapping in each backend
(``SELECT * FROM (\\n{sql}\\n) AS _sandboxed_query LIMIT N``) leans on that same
claim to bound result size.

So the boundaries are decided here instead, by rules written per dialect, and
sqlparse is left to do what it is good at: the structure of the one statement
that comes out.

Pure text scanning — no driver, no network, no sqlparse. Two callers must agree
with it: sql_guard (which enforces) and postgres_readonly (which pins
`standard_conforming_strings` so the assumption below is enforced rather than
assumed).
"""
from __future__ import annotations

from dataclasses import dataclass

POSTGRES = "postgres"
CLICKHOUSE = "clickhouse"
SNOWFLAKE = "snowflake"

DIALECTS: tuple[str, ...] = (POSTGRES, CLICKHOUSE, SNOWFLAKE)

# Does a backslash escape the next character inside an ordinary '...' literal?
#
# Postgres: no — `standard_conforming_strings` is on by default and
# postgres_readonly pins it. Its escape-string form `E'...'` does, and is
# handled separately below.
# ClickHouse and Snowflake: yes, by default and with no way to turn it off that
# this platform relies on.
_BACKSLASH_ESCAPES_IN_PLAIN_STRING = {
    POSTGRES: False,
    CLICKHOUSE: True,
    SNOWFLAKE: True,
}

# Only Postgres has $tag$ ... $tag$ quoting; elsewhere `$` is just a character.
_DOLLAR_QUOTING = {POSTGRES: True, CLICKHOUSE: False, SNOWFLAKE: False}

# Only Postgres nests /* /* */ */. Treating a nested comment as closed early
# would leave the tail scanned as code, which is the safe direction, but
# modelling it correctly keeps the comment span honest.
_NESTED_BLOCK_COMMENTS = {POSTGRES: True, CLICKHOUSE: False, SNOWFLAKE: False}

# ClickHouse quotes identifiers with backticks as well as double quotes.
_BACKTICK_IDENTIFIERS = {POSTGRES: False, CLICKHOUSE: True, SNOWFLAKE: False}

_IDENT_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_")


@dataclass(frozen=True)
class ScanResult:
    """What the text is, under one dialect's rules."""

    statements: int
    has_comment: bool
    max_paren_depth: int
    unbalanced_parens: bool
    unterminated: str  # "" when everything closed; else what was left open


def _dollar_tag(sql: str, i: int) -> str | None:
    """`$$` or `$tag$` starting at i, or None if this `$` opens nothing.

    A tag is letters/digits/underscore and must not start with a digit — the
    same shape Postgres accepts. `$1` is a parameter placeholder, not a tag.
    """
    j = i + 1
    while j < len(sql) and sql[j] in _IDENT_CHARS:
        j += 1
    if j >= len(sql) or sql[j] != "$":
        return None
    tag = sql[i + 1:j]
    if tag and tag[0].isdigit():
        return None
    return sql[i:j + 1]


def _is_escape_string_prefix(sql: str, quote_at: int) -> bool:
    """True for Postgres `E'...'` / `e'...'`, where backslash DOES escape.

    The `E` must be immediately before the quote and must not itself be the tail
    of a longer identifier — in `VALUE'x'` the E belongs to VALUE.
    """
    if quote_at == 0 or sql[quote_at - 1] not in "Ee":
        return False
    return quote_at - 2 < 0 or sql[quote_at - 2] not in _IDENT_CHARS


def scan(sql: str, dialect: str = POSTGRES) -> ScanResult:
    """Walk `sql` once, tracking what is code and what is not.

    Counts a statement for each run of code containing at least one
    non-whitespace character, so a single trailing `;` does not invent a second
    empty statement while `SELECT 1; SELECT 2` counts as two.
    """
    backslash_escapes = _BACKSLASH_ESCAPES_IN_PLAIN_STRING.get(dialect, False)
    dollar_quoting = _DOLLAR_QUOTING.get(dialect, False)
    nested_comments = _NESTED_BLOCK_COMMENTS.get(dialect, False)
    backticks = _BACKTICK_IDENTIFIERS.get(dialect, False)

    i, n = 0, len(sql)
    depth = 0
    max_depth = 0
    unbalanced = False
    has_comment = False
    unterminated = ""
    statements = 0
    current_has_code = False

    def end_statement() -> None:
        nonlocal statements, current_has_code
        if current_has_code:
            statements += 1
        current_has_code = False

    while i < n:
        ch = sql[i]

        # ── line comment ──────────────────────────────────────────────────
        if ch == "-" and sql.startswith("--", i):
            has_comment = True
            newline = sql.find("\n", i)
            i = n if newline == -1 else newline + 1
            continue

        # ── block comment ─────────────────────────────────────────────────
        if ch == "/" and sql.startswith("/*", i):
            has_comment = True
            i += 2
            level = 1
            while i < n and level:
                if nested_comments and sql.startswith("/*", i):
                    level += 1
                    i += 2
                elif sql.startswith("*/", i):
                    level -= 1
                    i += 2
                else:
                    i += 1
            if level:
                unterminated = unterminated or "block comment"
            continue

        # ── dollar-quoted string ──────────────────────────────────────────
        if dollar_quoting and ch == "$":
            tag = _dollar_tag(sql, i)
            if tag is not None:
                current_has_code = True
                close = sql.find(tag, i + len(tag))
                if close == -1:
                    unterminated = unterminated or f"dollar-quoted string {tag}"
                    i = n
                else:
                    i = close + len(tag)
                continue

        # ── ordinary string literal ───────────────────────────────────────
        if ch == "'":
            current_has_code = True
            escapes = backslash_escapes or (
                dialect == POSTGRES and _is_escape_string_prefix(sql, i)
            )
            i += 1
            closed = False
            while i < n:
                c = sql[i]
                if escapes and c == chr(92) and i + 1 < n:
                    i += 2
                    continue
                if c == "'":
                    # A doubled quote is an escaped quote in every dialect here.
                    if i + 1 < n and sql[i + 1] == "'":
                        i += 2
                        continue
                    i += 1
                    closed = True
                    break
                i += 1
            if not closed:
                unterminated = unterminated or "string literal"
            continue

        # ── quoted identifier ─────────────────────────────────────────────
        if ch == '"' or (backticks and ch == "`"):
            current_has_code = True
            closing = ch
            i += 1
            closed = False
            while i < n:
                if sql[i] == closing:
                    if i + 1 < n and sql[i + 1] == closing:
                        i += 2
                        continue
                    i += 1
                    closed = True
                    break
                i += 1
            if not closed:
                unterminated = unterminated or "quoted identifier"
            continue

        # ── plain code ────────────────────────────────────────────────────
        if ch == "(":
            depth += 1
            max_depth = max(max_depth, depth)
            current_has_code = True
        elif ch == ")":
            depth -= 1
            if depth < 0:
                # More closes than opens. Left alone this is what lets a
                # statement climb out of the backends' `SELECT * FROM (...)`
                # wrapper and shed its LIMIT.
                unbalanced = True
                depth = 0
            current_has_code = True
        elif ch == ";":
            end_statement()
        elif not ch.isspace():
            current_has_code = True
        i += 1

    end_statement()
    if depth != 0:
        unbalanced = True

    return ScanResult(
        statements=statements,
        has_comment=has_comment,
        max_paren_depth=max_depth,
        unbalanced_parens=unbalanced,
        unterminated=unterminated,
    )


def scan_all_dialects(sql: str) -> dict[str, ScanResult]:
    """Every dialect's reading of the same text.

    Used when the caller has not said which engine the SQL is bound for. The
    readings genuinely differ — that is the whole reason this module exists — so
    an unknown dialect must satisfy all of them rather than pick one.
    """
    return {d: scan(sql, d) for d in DIALECTS}
