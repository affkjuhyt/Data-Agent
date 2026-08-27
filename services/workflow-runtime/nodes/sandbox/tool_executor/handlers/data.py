from typing import Any, cast
import asyncio
import json
import logging
import re
import time
import uuid

import httpx
import litellm

from libs.shared import audit
from nodes.sandbox.tool_executor._sandbox_http import POLL_OVERHEAD_S, submit_and_poll
from nodes.sandbox.tool_executor._utils import _safe_path, _strip_code_block, _get_query

logger = logging.getLogger(__name__)

_LITELLM_BASE = __import__("os").environ.get("LITELLM_BASE_URL", "http://localhost:4000")
_LITELLM_KEY  = __import__("os").environ.get("LITELLM_API_KEY",  "sk-dev")

# Wall-clock budget for one query at sandbox-executor, on top of which
# submit_and_poll adds POLL_OVERHEAD_S for queue wait. Schema introspection
# gets its own, shorter budget: it is a single information_schema scan, and a
# slow one should not hold up the whole turn.
_QUERY_TIMEOUT_S = 60
_SCHEMA_TIMEOUT_S = 20

# information_schema is spelled the same either side, only the "current
# database" predicate differs. Both are plain SELECTs, so both pass sql_guard
# on the way through sandbox-executor like any agent-written query.
_COLUMNS_SQL = {
    "postgres": (
        "SELECT table_name, column_name, data_type FROM information_schema.columns "
        "WHERE table_schema = 'public' ORDER BY table_name, ordinal_position"
    ),
    "clickhouse": (
        "SELECT table_name, column_name, data_type FROM information_schema.columns "
        "WHERE table_schema = currentDatabase() ORDER BY table_name, ordinal_position"
    ),
}


def _sandbox_connection(conn_cfg: dict[str, Any]) -> dict[str, Any]:
    """Map a DB Connector canvas connection onto sandbox-executor's wire shape.

    The two disagree on one key: the canvas (and every other caller here) says
    `database`, while sandbox-executor's Postgres params model says `dbname`.
    ClickHouse keeps `database`. `type` tells sandbox-executor which backend to
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
    """Caller identity for the audit record, taken from the verified config.

    Kept as a plain dict threaded through the query path rather than read from
    a contextvar: the SQL functions are the only place that knows the target and
    the row count, and an audit record assembled anywhere else would be missing
    half of what an investigation needs.
    """
    return {
        "tenant_id": cfg.get("tenant_id", "") or "",
        "user_id": cfg.get("user_ref", "") or "anonymous",
        "user_roles": list(cfg.get("user_roles") or []),
        "auth_source": cfg.get("auth_source", "") or "",
        "session_id": cfg.get("session_id", "") or "",
        "agent_id": str(cfg.get("agent_id", "") or ""),
    }


async def _sandbox_sql(
    sql: str,
    conn_cfg: dict[str, Any],
    timeout_s: int = _QUERY_TIMEOUT_S,
    *,
    audit_ctx: dict[str, Any] | None = None,
    target: str = "",
    action: str = audit.ACTION_SQL_QUERY,
    metric_ref: str = "",
) -> tuple[list[dict[str, Any]] | None, str | None, bool]:
    """Run one SQL through sandbox-executor. Returns (rows, error, rejected_by_guard).

    This is the ONLY way this module reaches a database. It used to open an
    asyncpg connection here and hand the LLM's SQL straight to conn.fetch() —
    no parse, no SELECT-only check, no row cap, no statement timeout — while a
    tested guard (services/sandbox-executor/sql_guard.py) sat unused one service
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
        async with httpx.AsyncClient(timeout=10) as http:
            result = await submit_and_poll(
                http, "/execute/sql",
                {"sql": sql, "connection": _sandbox_connection(conn_cfg)},
                overall_timeout_s=timeout_s + POLL_OVERHEAD_S,
            )
    except Exception as e:
        _record(audit.DECISION_ERROR, None, f"sandbox-executor unreachable: {e}")
        return None, f"sandbox-executor unreachable: {e}", False

    if result.get("_http_status") == 503:
        _record(audit.DECISION_ERROR, None, "sandbox-executor overloaded")
        return None, f"sandbox-executor is overloaded, try again shortly: {result.get('error', 'rejected')}", False

    if not result.get("success"):
        rejected = bool(result.get("rejected_by_guard"))
        error_text = str(result.get("error", "query failed"))
        # A guard rejection is a POLICY decision, not a fault: the model wrote a
        # non-read statement and it was refused. Recording it as `deny` is what
        # makes "how often does the agent try to write?" a question the trail
        # can answer.
        _record(audit.DECISION_DENY if rejected else audit.DECISION_ERROR, None, error_text)
        return None, error_text, rejected

    rows: list[dict[str, Any]] = result.get("rows", [])
    if result.get("is_truncated"):
        logger.warning("text_to_sql | result truncated at sandbox-executor row cap")
    _record(audit.DECISION_ALLOW, len(rows), "truncated at row cap" if result.get("is_truncated") else "")
    return rows, None, False


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


def _rank_tables(tables: dict[str, list[str]], query: str) -> list[str]:
    """Order table names by lexical overlap with the question.

    Deliberately lexical, not embedded: this runs on the introspection FALLBACK
    path, which exists precisely because the embedded catalog has nothing for
    this connection — adding an embedding round-trip here would put the slow,
    failure-prone dependency on the degraded path. Ties and total misses keep
    their original (alphabetical, information_schema-ordered) position, so with
    an unrelated question the result is stable rather than arbitrary.
    """
    q = _tokens(query)
    if not q:
        return list(tables)

    def score(name: str) -> int:
        overlap = len(_tokens(name) & q)
        # A column hit is weaker evidence than a table-name hit, but it is what
        # finds `orders` from "revenue" when the column is `revenue_amount`.
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


def _render_tables(tables: dict[str, list[str]], query: str) -> str:
    """Render at most _MAX_SCHEMA_TABLES tables, within _MAX_SCHEMA_CHARS."""
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

    omitted = (len(tables) - len(lines))
    if omitted > 0:
        # Tell the model the view is partial. Without this it cannot distinguish
        # "that table does not exist" from "that table was not shown", and will
        # confidently answer that the data is unavailable.
        lines.append(f"-- ({omitted} more table(s) not shown — ask about them by name if needed)")
    return "\n".join(lines)


async def _catalog_schema_text(tenant_id: str, conn_cfg: dict[str, Any], query: str) -> str:
    """Curated Schema Catalog for this connection, or "" when it has nothing.

    The catalog (services/workflow-runtime/schema_catalog.py, filled by
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
        from schema_catalog import format_catalog_context, retrieve_schema_catalog

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


async def _introspect_tables(
    tenant_id: str, conn_cfg: dict[str, Any],
    *, audit_ctx: dict[str, Any] | None = None, target: str = "",
) -> dict[str, list[str]]:
    """{table_name: ["col type", ...]} from information_schema, Redis-cached.

    Cached RAW rather than pre-rendered: which tables reach the prompt now
    depends on the question, so caching the rendered text would pin one query's
    selection for every later query against the same database. Keyed by
    (tenant, host, port, database) — deliberately NOT the user-chosen connection
    name or the credentials, neither of which changes the tables.
    """
    from redis_cache import cache_get_schema, cache_set_schema

    host, port, database = conn_cfg.get("host", ""), conn_cfg.get("port", 5432), conn_cfg.get("database", "")

    cached = await cache_get_schema(tenant_id, host, port, database)
    if cached is not None:
        try:
            return cast(dict[str, list[str]], json.loads(cached))
        except (json.JSONDecodeError, TypeError):
            logger.warning("text_to_sql | discarding unparseable cached schema for db=%s", database)

    db_type = (conn_cfg.get("type") or "postgres").lower()
    rows, error, _ = await _sandbox_sql(
        _COLUMNS_SQL.get(db_type, _COLUMNS_SQL["postgres"]), conn_cfg, timeout_s=_SCHEMA_TIMEOUT_S,
        # Recorded under its own action: reading information_schema is a real
        # access (it discloses the shape of the tenant's warehouse) but it is
        # not a business query, and mixing the two would drown the trail.
        audit_ctx=audit_ctx, target=target, action=audit.ACTION_SCHEMA_INTROSPECT,
    )
    if error is not None:
        # Introspection failure degrades to an empty schema block (the LLM then
        # works from the connection note alone) rather than failing the turn.
        logger.warning("text_to_sql | schema introspection failed type=%s host=%s db=%s: %s", db_type, host, database, error)
        return {}

    tables: dict[str, list[str]] = {}
    for r in rows or []:
        tables.setdefault(r["table_name"], []).append(f"{r['column_name']} {r['data_type']}")

    await cache_set_schema(tenant_id, host, port, database, json.dumps(tables))
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

    tables = await _introspect_tables(tenant_id, conn_cfg, audit_ctx=audit_ctx, target=target)
    if not tables:
        return ""
    return _render_tables(tables, query)


async def _run_one_query(
    name: str, sql: str, conn_cfg: dict[str, Any], query: str, schema_text: str, sql_mdl: str,
    *, audit_ctx: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Chạy 1 câu SQL vào đúng 1 DB qua sandbox-executor, tự sửa lỗi kỹ thuật
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
        rows, error, rejected_by_guard = await _sandbox_sql(
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
                    "db_type": db_type, "executed_via": "sandbox-executor",
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
            resp = await litellm.acompletion(
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
    *, audit_ctx: dict[str, Any] | None = None,
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
        from semantic_layer import match_question

        match = await match_question(
            tenant_id, query, dialect=(conn_cfg.get("type") or "postgres").lower(),
        )
    except Exception as e:
        # The registry is an optimisation, never a dependency — any failure
        # falls through to the general path rather than failing the turn.
        logger.warning("text_to_sql | semantic layer unavailable: %s", e)
        return None

    if match is None:
        return None

    rows, error, rejected_by_guard = await _sandbox_sql(
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
            "executed_via": "sandbox-executor",
            # Provenance from the contract, not from the model — this is what
            # lets the answer state which definition produced the number.
            "generated_by": "semantic_layer",
            "semantic": match.provenance,
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
            only_name, db_connections[only_name], tenant_id, query, audit_ctx=audit_ctx,
        )
        if semantic is not None:
            result, error = semantic
            return ([result] if result else []), ([error] if error else [])

    # `query` drives which tables reach the prompt — the catalog embeds it,
    # the introspection fallback ranks against it. Passing it is what keeps
    # the schema block proportional to the question instead of the warehouse.
    schema_texts = {
        name: await _get_schema_text(tenant_id, conn_cfg, query, audit_ctx=audit_ctx, target=name)
        for name, conn_cfg in db_connections.items()
    }

    if len(db_connections) == 1:
        name = next(iter(db_connections))
        note = db_connections[name].get("note", "")
        schema_block = f"{schema_texts[name]}\n{note}".strip()
        try:
            resp = await litellm.acompletion(
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
            resp = await litellm.acompletion(
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


async def _text_to_pandas_handler(call: dict[str, Any], cfg: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    tool_cfg     = cfg.get("tool_config", {}).get("text_to_pandas", {})
    default_file = tool_cfg.get("default_file_path", "")
    allowed_dir  = tool_cfg.get("allowed_dir", "")
    file_path    = call.get("arguments", {}).get("file_path", "") or default_file

    if not file_path:
        return None, {"tool_name": "text_to_pandas", "error": "no file_path provided or configured"}

    _, err = _safe_path(file_path, allowed_dir)
    if err:
        return None, {"tool_name": "text_to_pandas", "error": err}

    query = _get_query(call)
    if not query:
        return None, {"tool_name": "text_to_pandas", "error": "no query provided"}

    try:
        resp = litellm.completion(
            model=cfg.get("planner_model", "planner"), api_base=_LITELLM_BASE, api_key=_LITELLM_KEY,
            messages=[{"role": "user", "content": (
                f"Viết Python pandas để trả lời: {query}\nFile: '{file_path}'\n"
                "Yêu cầu: import pandas as pd, đọc file, lưu kết quả vào biến 'result'.\nChỉ trả code Python thuần."
            )}],
            temperature=0, max_tokens=512, timeout=15,
        )
        code = _strip_code_block(resp.choices[0].message.content or "")
    except Exception as e:
        return None, {"tool_name": "text_to_pandas", "error": f"code generation failed: {e}"}

    try:
        import pandas as pd  # noqa: PLC0415, F401
        ns: dict[str, Any] = {}
        exec(compile(code, "<pandas_tool>", "exec"), ns)  # noqa: S102
        result = ns.get("result")
        if result is None:
            return None, {"tool_name": "text_to_pandas", "error": "code did not set 'result'", "code": code}
        if isinstance(result, pd.DataFrame):
            content = (result.head(50).to_markdown(index=False) + f"\n... ({len(result)} rows)") if len(result) > 50 else result.to_markdown(index=False)
        else:
            content = str(result)
        return {"id": f"pandas_{uuid.uuid4().hex[:8]}", "content": content, "source": "tool/pandas", "metadata": {"file": file_path}}, None
    except Exception as e:
        return None, {"tool_name": "text_to_pandas", "error": f"execution failed: {e}", "code": code}
