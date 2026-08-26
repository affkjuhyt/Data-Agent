from typing import Any
import asyncio
import json
import logging
import uuid

import httpx
import litellm

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


async def _sandbox_sql(
    sql: str, conn_cfg: dict[str, Any], timeout_s: int = _QUERY_TIMEOUT_S,
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
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            result = await submit_and_poll(
                http, "/execute/sql",
                {"sql": sql, "connection": _sandbox_connection(conn_cfg)},
                overall_timeout_s=timeout_s + POLL_OVERHEAD_S,
            )
    except Exception as e:
        return None, f"sandbox-executor unreachable: {e}", False

    if result.get("_http_status") == 503:
        return None, f"sandbox-executor is overloaded, try again shortly: {result.get('error', 'rejected')}", False

    if not result.get("success"):
        return None, str(result.get("error", "query failed")), bool(result.get("rejected_by_guard"))

    rows: list[dict[str, Any]] = result.get("rows", [])
    if result.get("is_truncated"):
        logger.warning("text_to_sql | result truncated at sandbox-executor row cap")
    return rows, None, False


async def _get_schema_text(tenant_id: str, conn_cfg: dict[str, Any]) -> str:
    """Đọc information_schema.columns qua sandbox-executor — không tự mở kết nối
    ở tiến trình này nữa. Cache qua Redis (không phải bộ nhớ tiến trình —
    workflow-runtime chạy nhiều replica) khoá theo (tenant_id, host, port,
    database), KHÔNG theo tên kết nối do người dùng tự đặt hay user/password
    (không ảnh hưởng cấu trúc bảng)."""
    from redis_cache import cache_get_schema, cache_set_schema

    host, port, database = conn_cfg.get("host", ""), conn_cfg.get("port", 5432), conn_cfg.get("database", "")

    cached = await cache_get_schema(tenant_id, host, port, database)
    if cached is not None:
        return cached

    db_type = (conn_cfg.get("type") or "postgres").lower()
    rows, error, _ = await _sandbox_sql(
        _COLUMNS_SQL.get(db_type, _COLUMNS_SQL["postgres"]), conn_cfg, timeout_s=_SCHEMA_TIMEOUT_S,
    )
    if error is not None:
        # Same contract as before: introspection failure degrades to an empty
        # schema block (the LLM then works from the connection note alone)
        # rather than failing the whole turn.
        logger.warning("text_to_sql | schema introspection failed type=%s host=%s db=%s: %s", db_type, host, database, error)
        return ""

    tables: dict[str, list[str]] = {}
    for r in rows or []:
        tables.setdefault(r["table_name"], []).append(f"{r['column_name']} {r['data_type']}")
    schema_text = "\n".join(f"{t}({', '.join(cols)})" for t, cols in tables.items())

    await cache_set_schema(tenant_id, host, port, database, schema_text)
    return schema_text


async def _run_one_query(
    name: str, sql: str, conn_cfg: dict[str, Any], query: str, schema_text: str, sql_mdl: str,
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
        rows, error, rejected_by_guard = await _sandbox_sql(current_sql, conn_cfg)

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

    schema_texts = {name: await _get_schema_text(tenant_id, conn_cfg) for name, conn_cfg in db_connections.items()}

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
        _run_one_query(name, sql, db_connections[name], query, schema_texts[name], sql_mdl)
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
