import asyncio
import json
import logging
import os

import litellm

from state import AgentState

logger = logging.getLogger(__name__)

_LITELLM_BASE = os.environ.get("LITELLM_BASE_URL", "http://localhost:4000")
_LITELLM_KEY = os.environ.get("LITELLM_API_KEY", "sk-dev")

_MAX_POINTS = 200

_SOURCE_FIELD = {
    "db_connector":  "db_results",
    "mcp":           "mcp_results",
    "kb_search":     "kb_chunks",
    "tool_executor": "tool_results",
}

_CHART_TYPES = {"bar", "line", "area", "pie", "scatter", "radar", "radialBar", "composed"}


def _incoming_source_types(cfg: dict) -> set[str]:
    """Đọc cfg["flow_nodes"]/cfg["flow_edges"] (đã có sẵn trong config mỗi lần
    chạy — xem config_loader.py:load_agent_config) để biết chính node
    "visualization" đang được nối cạnh từ (các) node nào trên canvas — CHỈ lấy
    dữ liệu từ đúng nguồn đó, không tự ý đọc field mà không có cạnh nối tới."""
    id_to_type = {
        n.get("id"): n.get("data", {}).get("nodeType")
        for n in cfg.get("flow_nodes", [])
    }
    viz_ids = {nid for nid, t in id_to_type.items() if t == "visualization"}
    return {
        id_to_type.get(e.get("source"))
        for e in cfg.get("flow_edges", [])
        if e.get("target") in viz_ids and id_to_type.get(e.get("source")) in _SOURCE_FIELD
    }


def _parse_tabular_rows(items: list[dict]) -> list[dict]:
    """Chỉ nhận item mà content parse được thành JSON list[dict]/dict — bỏ
    qua an toàn (không raise) nếu là prose/text thường. CỐ Ý không nhờ LLM
    "đọc hiểu" văn bản rồi tự bịa số liệu ra chart — tránh hallucination."""
    rows: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        content = item.get("content", "")
        if not isinstance(content, str):
            continue
        try:
            parsed = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, list) and parsed and all(isinstance(r, dict) for r in parsed):
            rows.extend(parsed)
        elif isinstance(parsed, dict):
            rows.append(parsed)
    return rows


async def _pick_chart_schema(query: str, columns: list[str], sample_rows: list[dict], cfg: dict) -> dict | None:
    """1 lệnh LLM DUY NHẤT — CHỈ chọn chart_type/title/x_key/y_keys (và
    "series" nếu chart_type="composed") từ tên cột + vài dòng mẫu. KHÔNG sinh
    "data" — visualization_node tự map dữ liệu THẬT từ rows vào đúng field đã
    chọn, tránh LLM bịa số liệu. LLM cũng KHÔNG sinh code vẽ — chỉ chọn 1
    "công thức" nhỏ, phần vẽ thật do recharts (frontend) đảm nhiệm."""
    model = cfg.get("visualize_model", "visualize")
    prompt = (
        "Chọn cách vẽ biểu đồ phù hợp cho yêu cầu sau, dựa trên các cột dữ liệu có sẵn.\n\n"
        f"Yêu cầu: {query}\n\n"
        f"Các cột: {columns}\n\n"
        f"Vài dòng mẫu: {json.dumps(sample_rows, ensure_ascii=False, default=str)}\n\n"
        "Các loại chart_type hợp lệ và khi nào dùng:\n"
        "- \"bar\": so sánh giá trị theo danh mục\n"
        "- \"line\": xu hướng theo thời gian/chuỗi liên tục\n"
        "- \"area\": giống line nhưng nhấn mạnh khối lượng/tích luỹ\n"
        "- \"pie\": tỷ trọng/phần trăm đóng góp (chỉ 1 cột giá trị)\n"
        "- \"scatter\": mối quan hệ giữa 2 cột SỐ liên tục (không phải danh mục)\n"
        "- \"radar\": so sánh nhiều tiêu chí cùng lúc theo 1 cột danh mục (nhiều cột số)\n"
        "- \"radialBar\": tỷ lệ/tiến độ theo danh mục, dạng vòng tròn\n"
        "- \"composed\": kết hợp nhiều loại (vd vừa bar vừa line) trong 1 chart — BẮT BUỘC kèm \"series\"\n\n"
        "Trả về JSON đúng schema sau, KHÔNG kèm dữ liệu thật, chỉ chọn tên cột:\n"
        '{"chart_type": "bar" | "line" | "area" | "pie" | "scatter" | "radar" | "radialBar" | "composed", '
        '"title": "<tiêu đề ngắn gọn>", '
        '"x_key": "<tên cột trục X/danh mục, phải nằm trong danh sách cột>", '
        '"y_keys": ["<tên cột trục Y, phải nằm trong danh sách cột>"], '
        '"series": [{"key": "<tên cột, chỉ khi chart_type=composed>", "type": "bar" | "line" | "area"}]}'
    )
    try:
        resp = await litellm.acompletion(
            model=model, api_base=_LITELLM_BASE, api_key=_LITELLM_KEY,
            messages=[{"role": "user", "content": prompt}],
            temperature=0, max_tokens=400, timeout=15,
            response_format={"type": "json_object"},
        )
        if not resp.choices:
            return None
        schema = json.loads(resp.choices[0].message.content or "{}")
        if (
            not schema.get("x_key")
            or not isinstance(schema.get("y_keys"), list)
            or not schema["y_keys"]
            or schema.get("chart_type") not in _CHART_TYPES
        ):
            return None
        return schema
    except Exception as e:
        logger.warning("visualization | failed to pick chart schema: %s", e)
        return None


def _build_chart_spec(schema: dict, columns: list[str], rows: list[dict]) -> dict | None:
    if schema["x_key"] not in columns:
        return None
    y_keys = [k for k in schema["y_keys"] if k in columns]
    if not y_keys:
        return None

    fields = {schema["x_key"], *y_keys}
    series = None
    if schema.get("chart_type") == "composed":
        raw_series = schema.get("series")
        if not isinstance(raw_series, list):
            return None
        series = [
            s for s in raw_series
            if isinstance(s, dict) and s.get("key") in columns and s.get("type") in {"bar", "line", "area"}
        ]
        if not series:
            return None
        fields |= {s["key"] for s in series}

    data = [{k: r.get(k) for k in fields} for r in rows[:_MAX_POINTS]]
    spec = {
        "chart_type": schema.get("chart_type", "bar"),
        "title": schema.get("title", ""),
        "x_key": schema["x_key"],
        "y_keys": y_keys,
        "data": data,
    }
    if series:
        spec["series"] = series
    return spec


async def _build_spec_for_source(query: str, rows: list[dict], cfg: dict) -> dict | None:
    if not rows:
        return None
    columns = list(rows[0].keys())
    schema = await _pick_chart_schema(query, columns, rows[:5], cfg)
    if schema is None:
        return None
    return _build_chart_spec(schema, columns, rows)


async def visualization_node(state: AgentState) -> dict:
    tool_calls = state.get("tool_calls", [])
    viz_call = next((tc for tc in tool_calls if tc.get("type") == "visualize"), None)
    if not viz_call:
        return {}

    cfg = state.get("config", {})
    connected = _incoming_source_types(cfg)
    if not connected:
        # Node có mặt trên canvas nhưng chưa nối cạnh nào vào — im lặng bỏ qua.
        return {}

    query = viz_call.get("query") or state["query"]
    # Mỗi nguồn nối vào → 1 chart RIÊNG (không gộp chung rows từ các nguồn có
    # schema khác nhau vào 1 chart, vd DB Connector {month,revenue} và MCP
    # {region,sales} không cùng cấu trúc cột) — chạy song song, độc lập.
    per_source_rows = [_parse_tabular_rows(state.get(_SOURCE_FIELD[t]) or []) for t in sorted(connected)]
    specs = await asyncio.gather(*(_build_spec_for_source(query, rows, cfg) for rows in per_source_rows))
    chart_specs = [s for s in specs if s is not None]

    if not chart_specs:
        return {"chart_specs": [], "chart_error": "Dữ liệu từ nguồn đã nối không ở dạng bảng hoặc không xác định được cách vẽ phù hợp"}

    return {"chart_specs": chart_specs, "chart_error": None}
