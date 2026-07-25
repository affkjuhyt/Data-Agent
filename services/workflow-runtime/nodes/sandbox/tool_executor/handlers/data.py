from typing import Any
import asyncio
import json
import logging
import uuid

import litellm

from nodes.sandbox.tool_executor._utils import _safe_path, _strip_code_block, _get_query

logger = logging.getLogger(__name__)

_LITELLM_BASE = __import__("os").environ.get("LITELLM_BASE_URL", "http://localhost:4000")
_LITELLM_KEY  = __import__("os").environ.get("LITELLM_API_KEY",  "sk-dev")


async def _get_schema_text(tenant_id: str, conn_cfg: dict[str, Any]) -> str:
    """Tự đọc information_schema.columns của Postgres — thay hoàn toàn cho việc
    gõ tay schema_hint hay mượn hệ Schema Catalog (hệ đó viết cứng cho
    Snowflake, không hỗ trợ Postgres/DB Connector — xem plan). Cache qua Redis
    (không phải bộ nhớ tiến trình — workflow-runtime chạy nhiều replica) khoá
    theo (tenant_id, host, port, database), KHÔNG theo tên kết nối do người
    dùng tự đặt hay user/password (không ảnh hưởng cấu trúc bảng)."""
    from redis_cache import cache_get_schema, cache_set_schema

    host, port, database = conn_cfg.get("host", ""), conn_cfg.get("port", 5432), conn_cfg.get("database", "")

    cached = await cache_get_schema(tenant_id, host, port, database)
    if cached is not None:
        return cached

    db_type = (conn_cfg.get("type") or "postgres").lower()
    try:
        if db_type == "clickhouse":
            from libs.database_connector import clickhouse_connector as _ch  # noqa: PLC0415
            rows = await _ch.columns(conn_cfg)  # [{table_name, column_name, data_type}, ...]
        else:
            import asyncpg  # noqa: PLC0415
            conn = await asyncpg.connect(
                host=host, port=port, user=conn_cfg.get("user", ""),
                password=conn_cfg.get("password", ""), database=database,
            )
            try:
                rows = await conn.fetch(
                    "SELECT table_name, column_name, data_type FROM information_schema.columns "
                    "WHERE table_schema = 'public' ORDER BY table_name, ordinal_position"
                )
            finally:
                await conn.close()
    except Exception as e:
        logger.warning("text_to_sql | schema introspection failed type=%s host=%s db=%s: %s", db_type, host, database, e)
        return ""

    tables: dict[str, list[str]] = {}
    for r in rows:
        tables.setdefault(r["table_name"], []).append(f"{r['column_name']} {r['data_type']}")
    schema_text = "\n".join(f"{t}({', '.join(cols)})" for t, cols in tables.items())

    await cache_set_schema(tenant_id, host, port, database, schema_text)
    return schema_text


async def _run_one_query(
    name: str, sql: str, conn_cfg: dict[str, Any], query: str, schema_text: str, sql_mdl: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Chạy 1 câu SQL vào đúng 1 DB, tự sửa lỗi tối đa 3 lần (fold lỗi vào
    prompt sinh lại SQL, y hệt logic cũ) — độc lập với các DB khác đang chạy
    song song (asyncio.gather ở _text_to_sql_handler), lỗi ở đây không ảnh
    hưởng query của DB khác."""
    current_sql = sql
    last_err: str | None = None
    db_type = (conn_cfg.get("type") or "postgres").lower()

    for attempt in range(3):
        try:
            if db_type == "clickhouse":
                from libs.database_connector import clickhouse_connector as _ch  # noqa: PLC0415
                data = await _ch.query(conn_cfg, current_sql)
            else:
                import asyncpg  # noqa: PLC0415
                conn = await asyncpg.connect(
                    host=conn_cfg.get("host", ""), port=conn_cfg.get("port", 5432),
                    user=conn_cfg.get("user", ""), password=conn_cfg.get("password", ""),
                    database=conn_cfg.get("database", ""),
                )
                try:
                    rows = await conn.fetch(current_sql)
                    data = [dict(r) for r in rows]
                finally:
                    await conn.close()
            logger.info("text_to_sql | db=%s type=%s attempt=%d → %d rows", name, db_type, attempt + 1, len(data))
            return {
                "id":       f"sql_{uuid.uuid4().hex[:8]}",
                "content":  json.dumps(data, ensure_ascii=False, default=str),
                "source":   "tool/sql",
                "metadata": {"sql": current_sql, "row_count": len(data), "db_name": name, "db_type": db_type},
            }, None
        except Exception as e:
            last_err = str(e)
            logger.warning("text_to_sql | db=%s attempt=%d failed: %s", name, attempt + 1, e)
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
