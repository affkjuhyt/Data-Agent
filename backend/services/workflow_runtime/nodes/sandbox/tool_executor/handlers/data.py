from typing import Any, cast
import asyncio
import base64
import json
import logging
import pathlib
import re
import time
import uuid

import httpx
import litellm

from libs.shared import aggregation_check, audit
from services.workflow_runtime.nodes.sandbox.tool_executor._sandbox_http import POLL_OVERHEAD_S, shared_client, submit_and_poll
from services.workflow_runtime.runtime_services import llm_call
from services.workflow_runtime.nodes.sandbox.tool_executor._utils import (
    _get_query,
    _safe_path,
    _strip_code_block,
    audit_ctx,
)

logger = logging.getLogger(__name__)

_LITELLM_BASE = __import__("os").environ.get("LITELLM_BASE_URL", "http://localhost:8500")
_LITELLM_KEY  = __import__("os").environ.get("LITELLM_API_KEY",  "sk-dev")

# Wall-clock budget for one query at sandbox_executor, on top of which
# submit_and_poll adds POLL_OVERHEAD_S for queue wait. Schema introspection
# gets its own, shorter budget: it is a single information_schema scan, and a
# slow one should not hold up the whole turn.
_QUERY_TIMEOUT_S = 60
_SCHEMA_TIMEOUT_S = 20

# information_schema is spelled the same either side, only the "current
# database" predicate differs. Both are plain SELECTs, so both pass sql_guard
# on the way through sandbox_executor like any agent-written query.
# Phase 1: table NAMES only — one row per table, so a warehouse with hundreds of
# tables costs hundreds of rows rather than tens of thousands.
# `{schema}` defaults to 'public' (every connection before this field existed
# behaves exactly as before) but lets a connection point at a non-default
# schema — e.g. `kb_structured`, where the KB's structured-table feature
# creates one real table per approved spreadsheet import, alongside the
# app's own internal tables which stay in `public` and stay invisible to
# whatever role this connection authenticates as (Postgres denies by default;
# nothing here grants across schemas). ClickHouse has no such notion
# (currentDatabase() already scopes it) so `{schema}` in that template is
# inert — .format() is still safe to call on it, just a no-op substitution.
_TABLES_SQL = {
    "postgres": (
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = '{schema}' AND table_type = 'BASE TABLE' ORDER BY table_name"
    ),
    "clickhouse": (
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = currentDatabase() ORDER BY table_name"
    ),
}

# Phase 2: columns for the handful of tables the question is actually about.
# `{tables}` is filled from the phase-1 result — the database's own catalog,
# never the question — and each name is re-validated as an identifier first.
_COLUMNS_SQL = {
    "postgres": (
        "SELECT table_name, column_name, data_type FROM information_schema.columns "
        "WHERE table_schema = '{schema}' AND table_name IN ({tables}) "
        "ORDER BY table_name, ordinal_position"
    ),
    "clickhouse": (
        "SELECT table_name, column_name, data_type FROM information_schema.columns "
        "WHERE table_schema = currentDatabase() AND table_name IN ({tables}) "
        "ORDER BY table_name, ordinal_position"
    ),
}

# How many tables get their columns fetched. Wider than _MAX_SCHEMA_TABLES so
# column-level ranking still has candidates to choose between, but a fixed
# ceiling either way — the wire payload no longer scales with warehouse size.
_COLUMN_FETCH_TABLES = 40

# information_schema.tables rows are cheap, but a warehouse past this many
# tables would approach sandbox_executor's own MAX_ROWS cap and lose the tail
# silently. Detected and logged rather than trusted.
_TABLE_LIST_CEILING = 5000

# Table names are interpolated into the phase-2 IN list, so they are validated
# rather than trusted — even coming from the catalog.
_TABLE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _safe_schema(conn_cfg: dict[str, Any]) -> str:
    """The canvas connection's `schema` field, interpolated directly into
    _TABLES_SQL/_COLUMNS_SQL — validated as a plain identifier (same regex as
    table names above) rather than trusted, even though it comes from
    agent-builder config rather than a chat message. Falls back to 'public'
    (the only value this ever was before the field existed) on anything else."""
    schema = str(conn_cfg.get("schema") or "public").strip()
    return schema if _TABLE_NAME_RE.match(schema) else "public"


def _sandbox_connection(conn_cfg: dict[str, Any]) -> dict[str, Any]:
    """Map a DB Connector canvas connection onto sandbox_executor's wire shape.

    The two disagree on one key: the canvas (and every other caller here) says
    `database`, while sandbox_executor's Postgres params model says `dbname`.
    ClickHouse keeps `database`. `type` tells sandbox_executor which backend to
    dispatch to for this request.
    """
    db_type = (conn_cfg.get("type") or "postgres").lower()
    common = {
        "type":     db_type,
        "host":     conn_cfg.get("host", ""),
        "user":     conn_cfg.get("user", ""),
        "password": conn_cfg.get("password", "") or "",
    }
    if db_type == "clickhouse":
        return {**common, "port": int(conn_cfg.get("port") or 8123), "database": conn_cfg.get("database", "")}
    return {**common, "port": int(conn_cfg.get("port") or 5432), "dbname": conn_cfg.get("database", "")}


def _audit_ctx(cfg: dict[str, Any]) -> dict[str, Any]:
    """Kept as a thin alias: this module had the only copy, and the other
    handlers now need the identical shape."""
    return audit_ctx(cfg)


async def _sandbox_sql(
    sql: str,
    conn_cfg: dict[str, Any],
    timeout_s: int = _QUERY_TIMEOUT_S,
    *,
    audit_ctx: dict[str, Any] | None = None,
    target: str = "",
    action: str = audit.ACTION_SQL_QUERY,
    metric_ref: str = "",
) -> tuple[list[dict[str, Any]] | None, str | None, bool, dict[str, Any] | None]:
    """Run one SQL through sandbox_executor.

    Returns (rows, error, rejected_by_guard, truncation).

    `truncation` is None when the whole result came back, and otherwise says
    what was cut and why. It is returned rather than only logged because a
    capped result is not a smaller answer to the same question — a SUM over
    the first 8MB of rows is a different number, and one that looks exactly
    like the right one.

    This is the ONLY way this module reaches a database. It used to open an
    asyncpg connection here and hand the LLM's SQL straight to conn.fetch() —
    no parse, no SELECT-only check, no row cap, no statement timeout — while a
    tested guard (backend/services/sandbox_executor/sql_guard.py) sat unused one service
    away. Everything now goes through that guard, the read-only DB role behind
    it, and its MAX_ROWS wrap.

    `rejected_by_guard` is surfaced separately from ordinary failures because
    the two deserve opposite handling: a guard rejection means the model wrote a
    non-read query, which re-prompting will not legitimately fix, so callers
    must not spend a retry on it.
    """
    started = time.monotonic()

    def _record(decision: str, row_count: int | None, reason: str) -> None:
        """Emit one audit row. Every exit path below goes through here — a
        denied or failed access is exactly as worth recording as a successful
        one, and an audit that only logs successes cannot show an attempt."""
        if audit_ctx is None:
            return
        audit.record_sql_access(
            **audit_ctx,
            action=action,
            target=target,
            sql=sql,
            decision=decision,
            row_count=row_count,
            latency_ms=int((time.monotonic() - started) * 1000),
            reason=reason,
            metric_ref=metric_ref,
        )

    try:
        result = await submit_and_poll(
            shared_client(10), "/execute/sql",
            {"sql": sql, "connection": _sandbox_connection(conn_cfg)},
            overall_timeout_s=timeout_s + POLL_OVERHEAD_S,
        )
    except Exception as e:
        _record(audit.DECISION_ERROR, None, f"sandbox_executor unreachable: {e}")
        return None, f"sandbox_executor unreachable: {e}", False, None

    if result.get("_http_status") == 503:
        _record(audit.DECISION_ERROR, None, "sandbox_executor overloaded")
        return None, f"sandbox_executor is overloaded, try again shortly: {result.get('error', 'rejected')}", False, None

    if not result.get("success"):
        rejected = bool(result.get("rejected_by_guard"))
        error_text = str(result.get("error", "query failed"))
        # A guard rejection is a POLICY decision, not a fault: the model wrote a
        # non-read statement and it was refused. Recording it as `deny` is what
        # makes "how often does the agent try to write?" a question the trail
        # can answer.
        _record(audit.DECISION_DENY if rejected else audit.DECISION_ERROR, None, error_text)
        return None, error_text, rejected, None

    rows: list[dict[str, Any]] = result.get("rows", [])

    truncation: dict[str, Any] | None = None
    if result.get("is_truncated"):
        truncation = {
            "reason": result.get("truncation_reason", "") or "row_limit",
            "rows_returned": len(rows),
            "result_bytes": result.get("result_bytes", 0),
        }
        logger.warning(
            "text_to_sql | result truncated (%s) at %d row(s)", truncation["reason"], len(rows),
        )
    # Cells are capped independently of the result: a result can be complete in
    # rows while individual values were shortened, and an answer quoting a
    # shortened value should not present it as the whole thing.
    if result.get("cells_truncated"):
        truncation = {**(truncation or {"reason": "cell_limit", "rows_returned": len(rows)}),
                      "cells_truncated": result["cells_truncated"]}

    _record(
        audit.DECISION_ALLOW, len(rows),
        f"truncated: {truncation['reason']}" if truncation else "",
    )
    return rows, None, False, truncation


# How much schema may reach the SQL-generation prompt. Introspection used to
# render EVERY table of every column into it: on a real warehouse that is a
# context blow-up, a per-turn cost multiplier, and the whole schema handed to
# the model provider. Only the tables plausibly relevant to the question go in
# now, capped both ways.
_MAX_SCHEMA_TABLES = 12
_MAX_SCHEMA_CHARS = 6000
# Per-table column cap. Without it one wide table defeats the whole budget:
# the first rendered line is always emitted (dropping it would leave an empty
# schema block), so a 400-column fact table — ordinary in a warehouse — lands
# in the prompt whole and busts _MAX_SCHEMA_CHARS on its own.
_MAX_COLUMNS_PER_TABLE = 40
# Catalog entries carry a curated business description, so fewer are needed to
# say more than raw DDL does.
_CATALOG_TOP_K = 5

_WORD_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set[str]:
    """Lowercased alphanumeric tokens, with snake_case split apart so
    `don_hang` in a table name matches `don hang` in the question."""
    return set(_WORD_RE.findall((text or "").lower()))


def _rank_table_names(names: list[str], query: str) -> list[str]:
    """Order table names by lexical overlap with the question — names only.

    Phase 1 has nothing but names to go on, which is the whole point: deciding
    which tables matter BEFORE fetching their columns is what keeps the fetch
    bounded. Column evidence still refines the order later, in `_rank_tables`,
    once the candidates' columns are actually in hand.

    Deliberately lexical, not embedded: this is the FALLBACK path, reached
    precisely because the embedded catalog has nothing for this connection —
    putting a slow, failure-prone embedding round-trip on the degraded path is
    the wrong place for it. Ties and total misses keep their original
    (alphabetical) position, so an unrelated question gives a stable result
    rather than an arbitrary one.
    """
    q = _tokens(query)
    if not q:
        return list(names)
    order = {name: i for i, name in enumerate(names)}
    return sorted(names, key=lambda n: (-len(_tokens(n) & q), order[n]))


def _rank_tables(tables: dict[str, list[str]], query: str) -> list[str]:
    """Re-rank fetched candidates, now that their columns are known.

    Same lexical scoring as `_rank_table_names`, plus column evidence — which is
    what finds `orders` from "revenue" when the matching column is
    `revenue_amount` and the table name says nothing.
    """
    q = _tokens(query)
    if not q:
        return list(tables)

    def score(name: str) -> int:
        overlap = len(_tokens(name) & q)
        # A column hit is weaker evidence than a table-name hit.
        column_overlap = sum(1 for col in tables[name] if _tokens(col) & q)
        return overlap * 10 + min(column_overlap, 5)

    return sorted(tables, key=lambda n: (-score(n), list(tables).index(n)))


def _rank_columns(columns: list[str], query: str) -> list[str]:
    """Query-relevant columns first, original order preserved within each group.

    Only matters once a table exceeds _MAX_COLUMNS_PER_TABLE — at that point the
    cut has to keep the columns the question is actually about, not the first 40
    alphabetically.
    """
    q = _tokens(query)
    if not q:
        return columns
    hit = [c for c in columns if _tokens(c) & q]
    return hit + [c for c in columns if c not in set(hit)]


def _render_table(name: str, columns: list[str], query: str) -> str:
    shown = _rank_columns(columns, query)[:_MAX_COLUMNS_PER_TABLE]
    omitted = len(columns) - len(shown)
    suffix = f", … +{omitted} more column(s)" if omitted > 0 else ""
    return f"{name}({', '.join(shown)}{suffix})"


def _render_tables(
    tables: dict[str, list[str]], query: str, total_tables: int | None = None,
) -> str:
    """Render at most _MAX_SCHEMA_TABLES tables, within _MAX_SCHEMA_CHARS.

    `total_tables` is how many tables the database actually has, which is larger
    than `tables` whenever phase 1 narrowed the candidate set. The disclosure
    below has to quote the real figure: telling the model "3 more not shown" on
    a 900-table warehouse understates the gap badly enough to change how it
    answers.
    """
    ordered = _rank_tables(tables, query)[:_MAX_SCHEMA_TABLES]
    lines: list[str] = []
    total = 0
    for name in ordered:
        line = _render_table(name, tables[name], query)
        # `and lines` keeps the first table even if it alone exceeds the
        # budget — an empty schema block is worse than an oversized one, and
        # _MAX_COLUMNS_PER_TABLE already bounds how oversized it can get.
        if total + len(line) > _MAX_SCHEMA_CHARS and lines:
            break
        lines.append(line)
        total += len(line)

    omitted = (total_tables if total_tables is not None else len(tables)) - len(lines)
    if omitted > 0:
        # Tell the model the view is partial. Without this it cannot distinguish
        # "that table does not exist" from "that table was not shown", and will
        # confidently answer that the data is unavailable.
        lines.append(f"-- ({omitted} more table(s) not shown — ask about them by name if needed)")
    return "\n".join(lines)


async def _catalog_schema_text(tenant_id: str, conn_cfg: dict[str, Any], query: str) -> str:
    """Curated Schema Catalog for this connection, or "" when it has nothing.

    The catalog (backend/services/workflow_runtime/schema_catalog.py, filled by
    schema-crawler-worker) is embedded, tenant-filtered and carries an
    LLM-written business description plus join hints per table — strictly better
    input than raw DDL. It was built and then never read from here, which is why
    this module was re-introspecting information_schema on every cache miss.

    Requires the connection to name the crawled warehouse it corresponds to
    (`catalog_warehouse_id` on the canvas node). Without that there is no safe
    way to tell which catalog entries describe THIS database, and handing the
    model tables from a different one is worse than handing it none.
    """
    warehouse_id = conn_cfg.get("catalog_warehouse_id") or ""
    if not warehouse_id:
        return ""

    try:
        from services.workflow_runtime.schema_catalog import format_catalog_context, retrieve_schema_catalog

        hits = await retrieve_schema_catalog(
            tenant_id, query, warehouse_id=warehouse_id, limit=_CATALOG_TOP_K,
        )
    except Exception as e:
        # Qdrant/embedding hiccup must not fail the turn — fall through to
        # introspection, which needs no extra dependency.
        logger.warning("text_to_sql | schema catalog lookup failed warehouse=%s: %s", warehouse_id, e)
        return ""

    if not hits:
        return ""
    logger.info("text_to_sql | schema from catalog warehouse=%s tables=%d", warehouse_id, len(hits))
    return format_catalog_context(hits)


def _quote_table_names(names: list[str]) -> str:
    """Quoted, comma-joined literals for the phase-2 IN list.

    Names come from phase 1 — the database's own catalog, never from the
    question — but they are still validated before interpolation. A table whose
    name is not a plain identifier is skipped rather than quoted around: this is
    the only place in this module where a value reaches a statement without
    passing through the metric compiler, so it does not get an exception.
    """
    Q, ESC = chr(39), chr(39) * 2
    safe = [n for n in names if _TABLE_NAME_RE.match(n)]
    dropped = len(names) - len(safe)
    if dropped:
        logger.warning(
            "text_to_sql | %d table name(s) are not plain identifiers and were skipped "
            "during column introspection", dropped,
        )
    return ", ".join(Q + n.replace(Q, ESC) + Q for n in safe)


async def _list_tables(
    tenant_id: str, conn_cfg: dict[str, Any],
    *, audit_ctx: dict[str, Any] | None = None, target: str = "",
) -> list[str]:
    """Table names for this database, Redis-cached.

    Phase 1 of a two-phase introspection. Fetching every column of every table
    in one statement — what this used to do — hits sandbox_executor's
    MAX_ROWS=10_000 cap at roughly 500 tables and comes back as an alphabetical
    prefix with `is_truncated` set. `_sandbox_sql` logs that but does not return
    it, so the tail simply vanished: the agent concluded those tables did not
    exist and nothing in the answer said otherwise. Names alone are one row per
    table, which puts that ceiling out of reach for any realistic warehouse.

    Cached because the table list changes at DDL speed, not per question.
    """
    from services.workflow_runtime.redis_cache import cache_get_schema, cache_set_schema

    host, port, database = conn_cfg.get("host", ""), conn_cfg.get("port", 5432), conn_cfg.get("database", "")
    db_schema = _safe_schema(conn_cfg)

    cached = await cache_get_schema(tenant_id, host, port, database, db_schema)
    if cached is not None:
        try:
            parsed = json.loads(cached)
            if isinstance(parsed, dict) and isinstance(parsed.get("tables"), list):
                return cast(list[str], parsed["tables"])
        except (json.JSONDecodeError, TypeError):
            pass
        # An entry written by the previous whole-schema shape, or anything
        # unparseable. Re-fetch rather than guess at it.
        logger.info("text_to_sql | discarding stale cached schema shape for db=%s", database)

    db_type = (conn_cfg.get("type") or "postgres").lower()
    sql = _TABLES_SQL.get(db_type, _TABLES_SQL["postgres"]).format(schema=db_schema)
    rows, error, _, _ = await _sandbox_sql(
        sql, conn_cfg, timeout_s=_SCHEMA_TIMEOUT_S,
        # Its own audit action: reading information_schema discloses the shape of
        # the tenant's warehouse, but it is not a business query and mixing the
        # two would drown the trail.
        audit_ctx=audit_ctx, target=target, action=audit.ACTION_SCHEMA_INTROSPECT,
    )
    if error is not None:
        # Degrades to an empty schema block (the LLM then works from the
        # connection note alone) rather than failing the turn.
        logger.warning(
            "text_to_sql | table listing failed type=%s host=%s db=%s: %s",
            db_type, host, database, error,
        )
        return []

    names = [str(r["table_name"]) for r in (rows or []) if r.get("table_name")]
    if len(names) >= _TABLE_LIST_CEILING:
        logger.warning(
            "text_to_sql | db=%s returned %d tables — at or beyond the listing ceiling, "
            "the schema view may be incomplete", database, len(names),
        )

    await cache_set_schema(tenant_id, host, port, database, json.dumps({"tables": names}), db_schema)
    return names


async def _fetch_columns(
    conn_cfg: dict[str, Any], table_names: list[str],
    *, audit_ctx: dict[str, Any] | None = None, target: str = "",
) -> dict[str, list[str]]:
    """{table: ["col type", ...]} for a BOUNDED set of tables.

    Phase 2. Deliberately not cached: which tables are asked about varies per
    question, and caching per-subset either fragments the cache or drifts back
    toward holding the whole schema — the thing this change exists to stop. The
    cost is one small query per turn against an already-narrow candidate list.
    """
    if not table_names:
        return {}

    in_list = _quote_table_names(table_names)
    if not in_list:
        return {}

    db_type = (conn_cfg.get("type") or "postgres").lower()
    template = _COLUMNS_SQL.get(db_type, _COLUMNS_SQL["postgres"])
    rows, error, _, _ = await _sandbox_sql(
        template.format(schema=_safe_schema(conn_cfg), tables=in_list), conn_cfg, timeout_s=_SCHEMA_TIMEOUT_S,
        audit_ctx=audit_ctx, target=target, action=audit.ACTION_SCHEMA_INTROSPECT,
    )
    if error is not None:
        logger.warning("text_to_sql | column introspection failed: %s", error)
        return {}

    tables: dict[str, list[str]] = {}
    for r in rows or []:
        tables.setdefault(str(r["table_name"]), []).append(f"{r['column_name']} {r['data_type']}")
    return tables


async def _get_schema_text(
    tenant_id: str, conn_cfg: dict[str, Any], query: str = "",
    *, audit_ctx: dict[str, Any] | None = None, target: str = "",
) -> str:
    """Schema block for the SQL-generation prompt.

    Curated catalog first, bounded introspection second. Both are scoped to this
    one connection and to what the question is about; neither can put the whole
    warehouse in the prompt any more.
    """
    catalog_text = await _catalog_schema_text(tenant_id, conn_cfg, query)
    if catalog_text:
        return catalog_text

    # Rank on NAMES first, then fetch columns only for the survivors. Ranking
    # after fetching — the previous order — required pulling the whole schema in
    # order to decide which fraction of it mattered.
    names = await _list_tables(tenant_id, conn_cfg, audit_ctx=audit_ctx, target=target)
    if not names:
        return ""

    candidates = _rank_table_names(names, query)[:_COLUMN_FETCH_TABLES]
    tables = await _fetch_columns(conn_cfg, candidates, audit_ctx=audit_ctx, target=target)
    if not tables:
        return ""

    # Total table count, not just the fetched candidates, so the "N more not
    # shown" disclosure reflects the real warehouse rather than the slice pulled.
    return _render_tables(tables, query, total_tables=len(names))


async def _provenance(
    conn_cfg: dict[str, Any], sql: str, *, audit_ctx: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Governance facts for the tables this statement read, or None.

    Attached to the result so the answer can state how old the data is. Every
    failure path returns None: the query has already succeeded by the time this
    runs, and an unavailable catalog must cost the disclosure, not the answer.

    tenant_id comes from `audit_ctx` rather than a new parameter — it is the
    verified caller identity this module already threads through the query path,
    and a second copy could disagree with it.
    """
    tenant_id = (audit_ctx or {}).get("tenant_id", "") or ""
    if not tenant_id:
        return None
    try:
        from services.workflow_runtime.data_provenance import provenance_for_sql

        return await provenance_for_sql(tenant_id, conn_cfg, sql)
    except Exception as e:
        logger.warning("text_to_sql | provenance lookup failed: %s", e)
        return None


async def _run_one_query(
    name: str, sql: str, conn_cfg: dict[str, Any], query: str, schema_text: str, sql_mdl: str,
    *, audit_ctx: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Chạy 1 câu SQL vào đúng 1 DB qua sandbox_executor, tự sửa lỗi kỹ thuật
    tối đa 3 lần (fold lỗi vào prompt sinh lại SQL) — độc lập với các DB khác
    đang chạy song song (asyncio.gather ở _text_to_sql_handler), lỗi ở đây không
    ảnh hưởng query của DB khác.

    KHÔNG retry khi sql_guard từ chối: đó là câu SQL không phải read-only (DDL/
    DML/nhiều câu lệnh/comment), tức lỗi chính sách chứ không phải lỗi tạm thời.
    Sinh lại chỉ tốn token và làm nhiễu tín hiệu audit — trả lỗi ngay, có cờ
    riêng để trace/alert đếm được."""
    current_sql = sql
    last_err: str | None = None
    db_type = (conn_cfg.get("type") or "postgres").lower()

    for attempt in range(3):
        # Inside the retry loop on purpose: each attempt is its own access,
        # and a trail that only showed the last one would hide that the
        # agent ran three different statements to answer one question.
        rows, error, rejected_by_guard, truncation = await _sandbox_sql(
            current_sql, conn_cfg, audit_ctx=audit_ctx, target=name,
        )

        if error is None:
            data = rows or []
            logger.info("text_to_sql | db=%s type=%s attempt=%d → %d rows", name, db_type, attempt + 1, len(data))
            return {
                "id":       f"sql_{uuid.uuid4().hex[:8]}",
                "content":  json.dumps(data, ensure_ascii=False, default=str),
                "source":   "tool/sql",
                "metadata": {
                    "sql": current_sql, "row_count": len(data), "db_name": name,
                    "db_type": db_type, "executed_via": "sandbox_executor",
                    "truncation": truncation,
                    # Model-written SQL over joins is where a fan-out silently
                    # multiplies a total. Analysed from the statement that ran,
                    # not the one first generated — a self-corrected retry can
                    # add the join that causes it.
                    "aggregation": aggregation_check.analyse(current_sql).to_dict(),
                    # How old the data behind these rows is. Read from the
                    # statement that actually ran, not the one first generated —
                    # a self-corrected retry can end up querying other tables.
                    "provenance": await _provenance(conn_cfg, current_sql, audit_ctx=audit_ctx),
                },
            }, None

        last_err = error

        if rejected_by_guard:
            logger.warning("text_to_sql | db=%s SQL rejected by sql_guard, not retrying: %s", name, error)
            return None, {
                "tool_name": "text_to_sql", "db_name": name,
                "error": f"SQL rejected by guard: {error}",
                "sql": current_sql, "rejected_by_guard": True,
            }

        logger.warning("text_to_sql | db=%s attempt=%d failed: %s", name, attempt + 1, error)
        if attempt >= 2:
            break
        try:
            resp = await llm_call.acompletion(
                "llm:text_to_sql_repair", litellm.acompletion,
                model=sql_mdl, api_base=_LITELLM_BASE, api_key=_LITELLM_KEY,
                messages=[{"role": "user", "content":
                    f"Chuyển câu hỏi sau thành SQL.\n\nSchema:\n{schema_text}\n\n"
                    f"Chỉ trả SQL thuần, không giải thích.\n\nSQL trước lỗi: {last_err}\nSửa lại.\n\nCâu hỏi: {query}"
                }],
                temperature=0, max_tokens=512, timeout=15,
            )
            current_sql = _strip_code_block(resp.choices[0].message.content or "")
        except Exception:
            break

    return None, {"tool_name": "text_to_sql", "db_name": name, "error": f"failed after 3 attempts: {last_err}", "sql": current_sql}


async def _semantic_result(
    name: str, conn_cfg: dict[str, Any], tenant_id: str, query: str,
    *, audit_ctx: dict[str, Any] | None = None, slots: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None] | None:
    """Answer from the Semantic Layer, or None to let the LLM path handle it.

    Tried BEFORE any prompt is built. When a question resolves to an approved
    metric there is nothing for a model to decide: the aggregation, the
    expression, the always-on filters and the business date column all come from
    the contract, so the SQL is compiled deterministically and the same question
    yields the same number every time.

    Returning None is the normal case for most questions and costs one in-memory
    lookup against a cached catalog — no LLM call, no database round-trip.
    """
    try:
        from services.workflow_runtime.semantic_layer import match_question

        from libs.semantic import AnalyticalSlots

        match = await match_question(
            tenant_id, query, dialect=(conn_cfg.get("type") or "postgres").lower(),
            # The structured request, already merged with the previous turn.
            # This is what makes "chỉ lấy miền Bắc" compile to the earlier query
            # plus one filter, instead of whatever the model rewrites this time.
            slots=AnalyticalSlots.from_dict(slots) if slots else None,
        )
    except Exception as e:
        # The registry is an optimisation, never a dependency — any failure
        # falls through to the general path rather than failing the turn.
        logger.warning("text_to_sql | semantic layer unavailable: %s", e)
        return None

    if match is None:
        return None

    rows, error, rejected_by_guard, truncation = await _sandbox_sql(
        match.compiled.sql, conn_cfg, audit_ctx=audit_ctx, target=name,
        # The contract that produced this SQL, so the trail can show WHY a
        # statement had the shape it did.
        metric_ref=match.compiled.metric_ref,
    )
    if error is not None:
        # A compiled metric failing means the contract disagrees with the actual
        # database (renamed column, dropped table). That is an operator problem
        # worth surfacing, not something to paper over by silently re-asking a
        # model to guess the same query.
        logger.warning("text_to_sql | metric %s failed to execute: %s", match.compiled.metric_ref, error)
        return None, {
            "tool_name": "text_to_sql", "db_name": name,
            "error": f"metric {match.compiled.metric_ref} failed: {error}",
            "sql": match.compiled.sql,
            "metric": match.compiled.metric_ref,
            "rejected_by_guard": rejected_by_guard,
        }

    data = rows or []
    logger.info("text_to_sql | db=%s answered from metric %s → %d rows", name, match.compiled.metric_ref, len(data))
    return {
        "id":       f"sql_{uuid.uuid4().hex[:8]}",
        "content":  json.dumps(data, ensure_ascii=False, default=str),
        "source":   "tool/sql",
        "metadata": {
            "sql": match.compiled.sql, "row_count": len(data), "db_name": name,
            "db_type": (conn_cfg.get("type") or "postgres").lower(),
            "executed_via": "sandbox_executor",
            "truncation": truncation,
            # A compiled metric aggregates one source_table with no joins, so
            # nothing can duplicate its rows. This is the concrete advantage of
            # the contract over model-written SQL, and the answer can state it
            # rather than the reader inferring it.
            "aggregation": aggregation_check.analyse(match.compiled.sql).to_dict(),
            # Provenance from the contract, not from the model — this is what
            # lets the answer state which definition produced the number.
            "generated_by": "semantic_layer",
            "semantic": match.provenance,
            # Two different questions, deliberately two different keys:
            # `semantic` says how the number was DEFINED, `provenance` says how
            # current the data behind it is. An approved contract compiled
            # against a table that stopped loading last week is still exactly
            # wrong, and only this field can say so.
            "provenance": await _provenance(conn_cfg, match.compiled.sql, audit_ctx=audit_ctx),
        },
    }, None


async def _text_to_sql_handler(call: dict[str, Any], cfg: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Trả về (results, errors) — CÓ THỂ nhiều kết quả nếu câu hỏi cần
    nhiều DB cùng lúc (LLM tự quyết định, xem plan mục "Multi-DB"). 1 kết nối
    → hành vi đơn giản, không qua bước phân loại DB. ≥2 kết nối → 1 lệnh LLM
    DUY NHẤT vừa chọn (các) DB liên quan vừa sinh SQL cho từng DB — không
    phải 2 lệnh riêng biệt."""
    db_connections: dict[str, Any] = cfg.get("tool_config", {}).get("db_connections", {})
    if not db_connections:
        return [], [{"tool_name": "text_to_sql", "error": "no database connection configured"}]

    query = _get_query(call)
    if not query:
        return [], [{"tool_name": "text_to_sql", "error": "no query provided"}]

    tenant_id   = cfg.get("tenant_id", "") or ""
    sql_mdl = cfg.get("text_to_sql_model", "text_to_sql")
    audit_ctx = _audit_ctx(cfg)

    # Semantic Layer first. A question that resolves to an approved metric is
    # answered from its contract — no schema in a prompt, no model-authored
    # arithmetic, no per-turn variation in what "doanh thu" means. Only the
    # questions the registry cannot answer exactly reach the LLM path below.
    #
    # Single-connection only for now: with several connections the question of
    # WHICH database a metric lives in needs the contract to name its source
    # connection, and inferring it would reintroduce exactly the guesswork this
    # layer removes.
    if len(db_connections) == 1 and tenant_id:
        only_name = next(iter(db_connections))
        semantic = await _semantic_result(
            only_name, db_connections[only_name], tenant_id, query,
            audit_ctx=audit_ctx, slots=cfg.get("slots"),
        )
        if semantic is not None:
            result, error = semantic
            return ([result] if result else []), ([error] if error else [])

    # `query` drives which tables reach the prompt — the catalog embeds it,
    # the introspection fallback ranks against it. Passing it is what keeps
    # the schema block proportional to the question instead of the warehouse.
    # One schema lookup per connection (catalog embed + search, or introspection),
    # independent of each other.
    names = list(db_connections)
    texts = await asyncio.gather(*(
        _get_schema_text(tenant_id, db_connections[name], query, audit_ctx=audit_ctx, target=name)
        for name in names
    ))
    schema_texts = dict(zip(names, texts))

    if len(db_connections) == 1:
        name = next(iter(db_connections))
        note = db_connections[name].get("note", "")
        schema_block = f"{schema_texts[name]}\n{note}".strip()
        try:
            resp = await llm_call.acompletion(
                "llm:text_to_sql", litellm.acompletion,
                model=sql_mdl, api_base=_LITELLM_BASE, api_key=_LITELLM_KEY,
                messages=[{"role": "user", "content":
                    f"Chuyển câu hỏi sau thành SQL.\n\nSchema:\n{schema_block}\n\n"
                    f"Chỉ trả SQL thuần, không giải thích.\n\nCâu hỏi: {query}"
                }],
                temperature=0, max_tokens=512, timeout=15,
            )
            sql = _strip_code_block(resp.choices[0].message.content or "")
        except Exception as e:
            return [], [{"tool_name": "text_to_sql", "error": f"SQL generation failed: {e}"}]
        queries = [(name, sql)]
    else:
        blocks = "\n\n".join(
            f'### Database "{name}"\n{schema_texts[name]}\n{db_connections[name].get("note", "")}'.strip()
            for name in db_connections
        )
        prompt = (
            f"Có các database sau:\n{blocks}\n\n"
            'Chọn (các) database phù hợp nhất với câu hỏi, sinh SQL cho từng database được chọn. '
            'Trả về CHỈ 1 JSON object: {"queries": [{"db_name": "<tên database>", "sql": "<câu SQL>"}]}, không giải thích thêm.'
            f"\n\nCâu hỏi: {query}"
        )
        try:
            resp = await llm_call.acompletion(
                "llm:text_to_sql", litellm.acompletion,
                model=sql_mdl, api_base=_LITELLM_BASE, api_key=_LITELLM_KEY,
                messages=[{"role": "user", "content": prompt}],
                temperature=0, max_tokens=1024, timeout=20,
                response_format={"type": "json_object"},
            )
            parsed = json.loads(_strip_code_block(resp.choices[0].message.content or "{}"))
        except Exception as e:
            return [], [{"tool_name": "text_to_sql", "error": f"SQL generation failed: {e}"}]

        queries = []
        for q in parsed.get("queries", []):
            name, sql = q.get("db_name"), q.get("sql")
            if name not in db_connections or not sql:
                logger.warning("text_to_sql | LLM chose unknown/invalid db_name=%r — skipping", name)
                continue
            queries.append((name, sql))

        if not queries:
            return [], [{"tool_name": "text_to_sql", "error": "LLM did not select any valid database"}]

    outcomes = await asyncio.gather(*(
        _run_one_query(name, sql, db_connections[name], query, schema_texts[name], sql_mdl, audit_ctx=audit_ctx)
        for name, sql in queries
    ))

    results = [r for r, e in outcomes if r is not None]
    errors  = [e for r, e in outcomes if e is not None]
    return results, errors


# ── text_to_pandas ─────────────────────────────────────────────────────────
#
# The file has to travel, not the path: the sandbox cannot see this service's
# filesystem, which is the isolation working rather than a gap in it. Bounded
# here as well as at sandbox_executor (SANDBOX_MAX_INPUT_BYTES) — the bytes go
# base64-encoded inside one JSON request, and 8 MiB is already far past what a
# pandas answer needs.
_MAX_PANDAS_INPUT_BYTES = 8 * 1024 * 1024
# Where sandbox_executor stages it: the container's tmpfs, wiped after the exec.
_SANDBOX_INPUT_DIR = "/tmp"
_PANDAS_TIMEOUT_S = 30

# Appended to the model's code so the answer comes back as STDOUT — the only
# channel across the boundary. The in-process version read `result` out of the
# exec namespace; there is no shared namespace over HTTP, so the rendering that
# used to happen here happens in the sandbox instead. Written to pass
# security_visitor's AST check: no open/eval/getattr, and pandas is on its
# import allowlist.
_PANDAS_RENDER_EPILOGUE = """

import pandas as pd

if 'result' not in dir():
    print('__PANDAS_TOOL_NO_RESULT__')
elif isinstance(result, pd.DataFrame):
    if len(result) > 50:
        print(result.head(50).to_markdown(index=False))
        print('... (' + str(len(result)) + ' rows)')
    else:
        print(result.to_markdown(index=False))
else:
    print(result)
"""
_NO_RESULT_SENTINEL = "__PANDAS_TOOL_NO_RESULT__"


async def _text_to_pandas_handler(call: dict[str, Any], cfg: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Phân tích file CSV/Excel bằng pandas — code do LLM sinh, chạy TRONG
    SANDBOX chứ không phải trong tiến trình này.

    Trước đây: exec(compile(code, ...)) ngay tại workflow_runtime. Code do model
    viết chạy cùng tiến trình đang giữ credential DB của mọi tenant, key LiteLLM
    và event loop phục vụ mọi hội thoại — trong khi sandbox (AST validation +
    container read-only, không network, giới hạn tài nguyên) đã sẵn có cho
    execute_python. Giờ đi cùng đường đó: file được đọc ở đây (đường dẫn đã
    qua _safe_path) rồi gửi kèm code sang sandbox.
    """
    tool_cfg     = cfg.get("tool_config", {}).get("text_to_pandas", {})
    default_file = tool_cfg.get("default_file_path", "")
    allowed_dir  = tool_cfg.get("allowed_dir", "")
    file_path    = call.get("arguments", {}).get("file_path", "") or default_file

    if not file_path:
        return None, {"tool_name": "text_to_pandas", "error": "no file_path provided or configured"}

    resolved, err = _safe_path(file_path, allowed_dir)
    if err:
        audit.record_tool_access(
            action=audit.ACTION_FILE_READ, target="text_to_pandas",
            target_objects=[file_path], detail=file_path,
            decision=audit.DECISION_DENY, reason=err, **_audit_ctx(cfg),
        )
        return None, {"tool_name": "text_to_pandas", "error": err}

    # A tenant's CSV read into a dataframe is as much a data access as a SELECT
    # against the same rows, and this handler sits in the module where the SQL
    # trail was built — it was simply not one of the paths wired.
    audit.record_tool_access(
        action=audit.ACTION_FILE_READ, target="text_to_pandas",
        target_objects=[file_path], detail=file_path, **_audit_ctx(cfg),
    )

    query = _get_query(call)
    if not query:
        return None, {"tool_name": "text_to_pandas", "error": "no query provided"}

    # Read before generating the code: a missing or oversized file is worth
    # finding out about before spending an LLM call on it.
    try:
        raw = await asyncio.to_thread(pathlib.Path(resolved).read_bytes)
    except Exception as e:
        return None, {"tool_name": "text_to_pandas", "error": f"could not read file: {e}"}
    if len(raw) > _MAX_PANDAS_INPUT_BYTES:
        return None, {
            "tool_name": "text_to_pandas",
            "error": f"file is {len(raw)} bytes, over the {_MAX_PANDAS_INPUT_BYTES}-byte sandbox input limit",
        }

    sandbox_name = pathlib.Path(resolved).name or "input.csv"
    sandbox_path = f"{_SANDBOX_INPUT_DIR}/{sandbox_name}"

    try:
        # acompletion, not completion: this runs on the event loop serving every
        # other conversation, and the synchronous call blocked all of them for
        # the duration of the generation.
        resp = await llm_call.acompletion(
            "llm:python_codegen", litellm.acompletion,
            model=cfg.get("planner_model", "planner"), api_base=_LITELLM_BASE, api_key=_LITELLM_KEY,
            messages=[{"role": "user", "content": (
                f"Viết Python pandas để trả lời: {query}\n"
                f"File dữ liệu đã có sẵn trong sandbox tại: '{sandbox_path}'\n"
                "Yêu cầu: import pandas as pd, đọc file theo đúng đường dẫn trên, "
                "lưu kết quả vào biến 'result'.\n"
                # Spelled out because the sandbox enforces exactly this list
                # (backend/services/sandbox_executor/security_visitor.py) and rejects the
                # whole submission otherwise — a model that does not know the
                # rule fails the call instead of writing something unsafe.
                "Chỉ được import: pandas, numpy, plotly, matplotlib, json, math, datetime, statistics.\n"
                "KHÔNG dùng open(), os, sys, subprocess, eval, exec, getattr.\n"
                "Chỉ trả code Python thuần."
            )}],
            temperature=0, max_tokens=512, timeout=15,
        )
        code = _strip_code_block(resp.choices[0].message.content or "")
    except Exception as e:
        return None, {"tool_name": "text_to_pandas", "error": f"code generation failed: {e}"}

    timeout_s = int(tool_cfg.get("timeout_s", _PANDAS_TIMEOUT_S))
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            result = await submit_and_poll(
                http, "/execute/python",
                {
                    "code": code + _PANDAS_RENDER_EPILOGUE,
                    "timeout_s": timeout_s,
                    "files": {sandbox_name: base64.b64encode(raw).decode("ascii")},
                },
                overall_timeout_s=timeout_s + POLL_OVERHEAD_S,
            )
    except Exception as e:
        return None, {"tool_name": "text_to_pandas", "error": f"sandbox_executor unreachable: {e}", "code": code}

    if result.get("_http_status") == 503:
        return None, {
            "tool_name": "text_to_pandas",
            "error": f"sandbox_executor is overloaded, try again shortly: {result.get('error', 'rejected')}",
            "code": code,
        }

    if not result.get("success"):
        # An AST rejection arrives here too ("security validation failed"): the
        # model wrote something outside the sandbox's allowlist. Returned as-is
        # rather than retried — that is a policy answer, not a flaky one.
        return None, {
            "tool_name": "text_to_pandas",
            "error": result.get("error", "execution failed"),
            "stderr": result.get("stderr", ""),
            "code": code,
        }

    content = (result.get("stdout") or "").strip()
    if _NO_RESULT_SENTINEL in content:
        return None, {"tool_name": "text_to_pandas", "error": "code did not set 'result'", "code": code}
    if result.get("is_truncated"):
        content += "\n[output truncated]"

    return {
        "id": f"pandas_{uuid.uuid4().hex[:8]}",
        "content": content or "(no output)",
        "source": "tool/pandas",
        "metadata": {"file": file_path, "executed_via": "sandbox_executor", "code": code},
    }, None
