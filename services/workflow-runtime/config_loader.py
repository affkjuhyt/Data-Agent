import json
import os
import logging
from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv())

from sqlalchemy import text

logger = logging.getLogger(__name__)

_PROMPTS_DIR = os.path.join(os.path.dirname(__file__), "prompts")
_DEFAULT_SYSTEM_PROMPT = open(os.path.join(_PROMPTS_DIR, "default_system_prompt.txt"), encoding="utf-8").read().strip()

DEV_CONFIG = {
    "kb_endpoint": os.environ.get("KB_ENDPOINT", "http://localhost:8080"),
    "planner_model": "openai/planner",
    "planner_temperature": 0,
    "planner_max_tokens": 256,
    "planner_response_format": {"type": "json_object"},
    "planner_timeout": 60,
    "planner_num_retries": 0,
    "responder_model": "openai/responder",
    "suggestion_model": "openai/suggester",
    "rewriter_model": "openai/rewriter",
    "responder_temperature": 0.2,
    "responder_max_tokens": 1000,
    "responder_stream": True,
    "system_prompt": os.environ.get("DEV_SYSTEM_PROMPT", _DEFAULT_SYSTEM_PROMPT),
    "rrf_top_n": 20,
    "reranker_model": "openai/reranker",
    "reranker_top_n": 5,
    "context_max_chunks": 5,
    "context_max_chars": 4000,
    "memory_enabled": False,
    "mcp_endpoints": [],
    "allowed_tools": [],
    "tool_config": {},
    "judge_model": "openai/judge",
    "visualize_model": "openai/visualize",
    "text_to_sql_model": "openai/text_to_sql",
    "loop_max_iterations": 3,
    "agent_id": os.environ.get("DEV_AGENT_ID", "00000000-0000-0000-0000-000000000030"),
    "agent_version_id": None,
    "workflow_version_id": None,
    "flow_nodes": [],
    "flow_edges": [],
    "injected_metadata": {},
}


async def load_agent_config(agent_id: str, tenant_id: str | None = None) -> dict:
    from services.database_connector.postgres_connector import client
    from mongo_client import load_flow_nodes, load_flow_edges

    if not client.is_connected():
        logger.debug("DB not available — using DEV_CONFIG")
        return {**DEV_CONFIG, "agent_id": agent_id}

    try:
        async with client.get_client(tenant_id=tenant_id) as session:
            # 1. Load published agent_version với responder model + system prompt
            row = (await session.execute(text("""
                SELECT
                    av.id                          AS agent_version_id,
                    av.workflow_version_id,
                    av.memory_enabled,
                    av.llm_config,
                    av.retrieval_config,
                    lp.model_id                    AS responder_model,
                    sp.content                     AS system_prompt
                FROM "Agents" a
                JOIN "AgentVersions" av  ON av.id = a.published_version_id
                JOIN "LLMProviders"  lp  ON lp.id = av.responder_model_id
                LEFT JOIN "SystemPrompts" sp ON sp.id = av.system_prompt_id
                WHERE a.id = :agent_id
                  AND a.deleted_at IS NULL
            """), {"agent_id": agent_id})).fetchone()

            if not row:
                logger.info("no published version for agent=%s", agent_id)
                return None

            llm_config       = dict(row.llm_config or {})
            retrieval_config = dict(row.retrieval_config or {})
            wv_id            = str(row.workflow_version_id) if row.workflow_version_id else None

            # 2. KB connections
            kb_rows = (await session.execute(text("""
                SELECT kc.endpoint_url, kc.api_key_ref
                FROM "AgentKB" ak
                JOIN "KBConnections" kc ON kc.id = ak.kb_connection_id
                WHERE ak.version_id = :av_id
                LIMIT 1
            """), {"av_id": str(row.agent_version_id)})).fetchall()

            kb_endpoint = kb_rows[0].endpoint_url if kb_rows else DEV_CONFIG["kb_endpoint"]

            # 3. MCP endpoints
            mcp_rows = (await session.execute(text("""
                SELECT m.name, m.endpoint_url, m.api_key_ref, m.capabilities
                FROM "AgentMCP" am
                JOIN "MCP" m ON m.id = am.mcp_id
                WHERE am.version_id = :av_id
            """), {"av_id": str(row.agent_version_id)})).fetchall()

            mcp_endpoints = [
                {
                    "name":          r.name,
                    "url":           r.endpoint_url,
                    "auth_token":    r.api_key_ref or "",
                    "allowed_tools": list(r.capabilities or []),
                }
                for r in mcp_rows
            ]

        # 4. Load flow_nodes + flow_edges từ MongoDB
        flow_nodes = await load_flow_nodes(wv_id) if wv_id else []
        flow_edges = await load_flow_edges(wv_id) if wv_id else []
        node_cfg   = _extract_node_config(flow_nodes)
        await _resolve_warehouse_secret(node_cfg, tenant_id)
        await _resolve_db_connector_secrets(node_cfg, tenant_id)

        return {
            **DEV_CONFIG,
            # identity
            "agent_id":            agent_id,
            "agent_version_id":    str(row.agent_version_id),
            # workflow_version_id keys the graph cache — a new published version
            # gets a new id, so the runner rebuilds the graph automatically.
            "workflow_version_id": wv_id,
            # responder
            "responder_model":      row.responder_model or DEV_CONFIG["responder_model"],
            "responder_temperature": llm_config.get("temperature", DEV_CONFIG["responder_temperature"]),
            "responder_max_tokens":  llm_config.get("max_tokens",  DEV_CONFIG["responder_max_tokens"]),
            "responder_stream":      llm_config.get("stream",      True),
            # system prompt
            "system_prompt": row.system_prompt or DEV_CONFIG["system_prompt"],
            # kb
            "kb_endpoint": kb_endpoint,
            # memory
            "memory_enabled": bool(row.memory_enabled),
            # mcp
            "mcp_endpoints": mcp_endpoints,
            # node-level config từ canvas
            **node_cfg,
            # flow_nodes + flow_edges cho dynamic graph
            "flow_nodes": flow_nodes,
            "flow_edges": flow_edges,
        }

    except Exception as e:
        logger.warning("load_agent_config DB failed: %s — fallback DEV_CONFIG", e)
        return {**DEV_CONFIG, "agent_id": agent_id}


async def _resolve_warehouse_secret(node_cfg: dict, tenant_id: str | None) -> None:
    """Mutates node_cfg in place: resolves execute_sql's `secret_id` (set by
    _extract_node_config below) into a real password via OpenBao, and always
    strips `secret_id` itself so it never reaches the HTTP body sent to
    sandbox-executor. No-op if no node configured Snowflake, or no secret_id
    was set — connection.password just stays "" (handler already treats that
    as "not configured", same as before this fix)."""
    connection = node_cfg.get("tool_config", {}).get("execute_sql", {}).get("connection")
    if not connection:
        return
    secret_id = connection.pop("secret_id", "")
    if not secret_id or not tenant_id:
        return

    from warehouse_secrets import resolve_snowflake_password
    password = await resolve_snowflake_password(tenant_id, secret_id)
    if password:
        connection["password"] = password
    else:
        logger.warning("could not resolve snowflakeSecretId=%s for tenant=%s — execute_sql will fail auth", secret_id, tenant_id)


async def _resolve_db_connector_secrets(node_cfg: dict, tenant_id: str | None) -> None:
    """Mutates node_cfg in place: resolves each DB Connector connection's
    `secret_id` (set by _extract_node_config below) into a real password via
    OpenBao, and always strips `secret_id` so it never reaches the SQL
    generation/execution code. No-op if no db_connector node configured, or
    no secret_id was set for a given connection — that connection's password
    just stays "" (auth fails cleanly downstream, same pattern as
    _resolve_warehouse_secret above)."""
    db_connections = node_cfg.get("tool_config", {}).get("db_connections", {})
    if not db_connections:
        return

    from warehouse_secrets import resolve_db_connector_password
    for name, connection in db_connections.items():
        secret_id = connection.pop("secret_id", "")
        if not secret_id or not tenant_id:
            continue
        password = await resolve_db_connector_password(tenant_id, secret_id)
        if password:
            connection["password"] = password
        else:
            logger.warning("could not resolve db_connector secret_id=%s for connection=%s tenant=%s — will fail auth", secret_id, name, tenant_id)


def _extract_node_config(flow_nodes: list[dict]) -> dict:
    """Đọc config từng node canvas — override retrieval_config nếu user tùy chỉnh."""
    cfg = {}
    for node in flow_nodes:
        t = node.get("data", {}).get("nodeType") or node.get("type", "")
        c = node.get("data", {}) or node.get("config", {})
        if t == "rrf_ranking":
            if "top_n" in c: cfg["rrf_top_n"] = c["top_n"]
        elif t == "reranker":
            if "top_n"     in c: cfg["reranker_top_n"] = c["top_n"]
            if "model_id"  in c: cfg["reranker_model"]  = c["model_id"]
        elif t == "responder":
            if "temperature" in c: cfg["responder_temperature"] = c["temperature"]
            if "max_tokens"  in c: cfg["responder_max_tokens"]  = c["max_tokens"]
        elif t == "judge":
            if "max_iterations" in c: cfg["loop_max_iterations"] = int(c["max_iterations"])
            if "model_id"       in c: cfg["judge_model"]         = c["model_id"]
        elif t == "metadata_injector":
            # "key1=value1,key2=value2" — cặp thiếu dấu "=" hoặc key rỗng bị
            # bỏ qua an toàn thay vì raise, vì đây là input tự do trên canvas.
            parsed: dict = {}
            for pair in c.get("metadataKv", "").split(","):
                if "=" not in pair:
                    continue
                k, v = pair.split("=", 1)
                if k.strip():
                    parsed[k.strip()] = v.strip()
            cfg["injected_metadata"] = parsed
        elif t == "db_connector":
            try:
                conns = json.loads(c.get("dbConnections") or "[]")
            except (json.JSONDecodeError, TypeError):
                conns = []
            db_connections = {
                conn["name"]: {
                    # "postgres" (default) or "clickhouse" — selects the driver in
                    # the db_connector node (handlers/data.py). Absent/unknown =>
                    # postgres, so existing flows are unchanged.
                    "type":      (conn.get("type") or "postgres").lower(),
                    "host":      conn.get("host", ""),
                    "port":      conn.get("port", 5432),
                    "database":  conn.get("database", ""),
                    "user":      conn.get("user", ""),
                    # Resolved against OpenBao in load_agent_config() via
                    # warehouse_secrets.py — no plaintext password read here.
                    "secret_id": conn.get("secretId", ""),
                    "password":  "",
                    "note":      conn.get("note", ""),
                }
                for conn in conns if conn.get("name") and conn.get("host") and conn.get("database")
            }
            if db_connections:
                cfg["tool_config"] = {**cfg.get("tool_config", {}), "db_connections": db_connections}
        elif t == "tool_executor":
            allowed = c.get("allowedBuiltinTools", [])
            if allowed:
                cfg["allowed_tools"] = allowed
            tool_config: dict = {**cfg.get("tool_config", {})}
            if c.get("defaultFilePath") or c.get("pandasAllowedDir"):
                tool_config["text_to_pandas"] = {
                    "default_file_path": c.get("defaultFilePath", ""),
                    "allowed_dir":       c.get("pandasAllowedDir", ""),
                }
            if c.get("fileReadAllowedDir"):
                tool_config["file_read"] = {"allowed_dir": c["fileReadAllowedDir"]}
            if c.get("grepAllowedDir"):
                tool_config["grep"] = {"allowed_dir": c["grepAllowedDir"]}
            if c.get("globAllowedDir"):
                tool_config["glob"] = {"allowed_dir": c["globAllowedDir"]}
            if c.get("writeAllowedDir"):
                tool_config["file_write"] = {"allowed_dir": c["writeAllowedDir"]}
                tool_config["file_edit"]  = {"allowed_dir": c["writeAllowedDir"]}
            if "bashEnabled" in c:
                tool_config["bash"] = {
                    "enabled": bool(c["bashEnabled"]),
                    "cwd":     c.get("bashCwd", ""),
                }
            if c.get("snowflakeAccount"):
                tool_config["execute_sql"] = {
                    "connection": {
                        "account":   c["snowflakeAccount"],
                        "user":      c.get("snowflakeUser", ""),
                        "warehouse": c.get("snowflakeWarehouse", ""),
                        "database":  c.get("snowflakeDatabase", ""),
                        "schema":    c.get("snowflakeSchema", ""),
                        "role":      c.get("snowflakeRole", ""),
                        # Resolved against OpenBao in load_agent_config() via
                        # warehouse_secrets.py — deliberately no plaintext
                        # `snowflakePassword` field read here (PR review gap,
                        # see docs/Sandbox_SchemaCrawler_Implementation.md §9).
                        # "secret_id" is stripped before this dict reaches
                        # execute_sql's handler; "password" is empty until
                        # resolved.
                        "secret_id": c.get("snowflakeSecretId", ""),
                        "password":  "",
                    },
                }
            if tool_config:
                cfg["tool_config"] = tool_config
            # doc §1.9 — sandbox tool-grant preset, independent of allowedBuiltinTools
            # above. Omitted entirely (no toolGrantPreset/toolGrantTools/
            # toolGrantDisallowedTools on the canvas node) -> tool_grant.py's
            # DEFAULT_PRESET grants both execute_sql/execute_python, same as
            # before this feature existed.
            if c.get("toolGrantPreset") or c.get("toolGrantTools") or c.get("toolGrantDisallowedTools"):
                cfg["tool_grant"] = {
                    "preset":           c.get("toolGrantPreset", "full_data_agent"),
                    "tools":            c.get("toolGrantTools", []),
                    "disallowed_tools": c.get("toolGrantDisallowedTools", []),
                }
    return cfg
