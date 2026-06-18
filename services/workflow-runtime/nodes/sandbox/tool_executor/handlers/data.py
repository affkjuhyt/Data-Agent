import json
import logging
import uuid

import litellm

from nodes.sandbox.tool_executor._utils import _safe_path, _strip_code_block, _get_query

logger = logging.getLogger(__name__)

_LITELLM_BASE = __import__("os").environ.get("LITELLM_BASE_URL", "http://localhost:4000")
_LITELLM_KEY  = __import__("os").environ.get("LITELLM_API_KEY",  "sk-dev")


async def _text_to_sql_handler(call: dict, cfg: dict) -> tuple[dict | None, dict | None]:
    tool_cfg = cfg.get("tool_config", {}).get("text_to_sql", {})
    db_url = tool_cfg.get("db_url", "")
    if not db_url:
        return None, {"tool_name": "text_to_sql", "error": "db_url not configured"}

    query = _get_query(call)
    if not query:
        return None, {"tool_name": "text_to_sql", "error": "no query provided"}

    schema_ctx  = f"\n\nSchema:\n{tool_cfg['schema_hint']}" if tool_cfg.get("schema_hint") else ""
    planner_mdl = cfg.get("planner_model", "planner")
    last_err: str | None = None
    sql: str | None      = None

    for attempt in range(3):
        retry_ctx = f"\n\nSQL trước lỗi: {last_err}\nSửa lại." if last_err else ""
        try:
            resp = litellm.completion(
                model=planner_mdl, api_base=_LITELLM_BASE, api_key=_LITELLM_KEY,
                messages=[{"role": "user", "content":
                    f"Chuyển câu hỏi sau thành SQL{schema_ctx}. Chỉ trả SQL thuần, không giải thích.{retry_ctx}\n\nCâu hỏi: {query}"
                }],
                temperature=0, max_tokens=512, timeout=15,
            )
            sql = _strip_code_block(resp.choices[0].message.content or "")
        except Exception as e:
            return None, {"tool_name": "text_to_sql", "error": f"SQL generation failed: {e}"}

        try:
            import asyncpg  # noqa: PLC0415
            conn = await asyncpg.connect(db_url)
            try:
                rows = await conn.fetch(sql)
                data = [dict(r) for r in rows]
                logger.info("text_to_sql | attempt=%d → %d rows", attempt + 1, len(data))
                return {
                    "id":       f"sql_{uuid.uuid4().hex[:8]}",
                    "content":  json.dumps(data, ensure_ascii=False, default=str),
                    "source":   "tool/sql",
                    "metadata": {"sql": sql, "row_count": len(data)},
                }, None
            finally:
                await conn.close()
        except Exception as e:
            last_err = str(e)
            logger.warning("text_to_sql | attempt=%d failed: %s", attempt + 1, e)

    return None, {"tool_name": "text_to_sql", "error": f"failed after 3 attempts: {last_err}", "sql": sql}


async def _text_to_pandas_handler(call: dict, cfg: dict) -> tuple[dict | None, dict | None]:
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
        ns: dict = {}
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
