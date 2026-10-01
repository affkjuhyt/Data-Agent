import asyncio
import json
import logging
import os
from typing import Any

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv())

from sqlalchemy import select

from libs.basemodel.services_databaseconnector.postgres_orm.workflow_orm import (
    AgentKBORM,
    AgentMCPORM,
    AgentVersionsORM,
    AgentsORM,
    KBConnectionsORM,
    LLMProvidersORM,
    MCPORM,
    SystemPromptsORM,
)

from services.workflow_runtime.prompts import PROMPTS_DIR

logger = logging.getLogger(__name__)

_DEFAULT_SYSTEM_PROMPT = open(os.path.join(PROMPTS_DIR, "default_system_prompt.txt"), encoding="utf-8").read().strip()

DEV_CONFIG = {
    "kb_endpoint": os.environ.get("KB_ENDPOINT", "http://kb-backend:8220"),
    "planner_model": "openai/planner",
    "planner_temperature": 0,
    # 256 truncated mid-JSON on plans with 2+ tool_calls (each carries its own
    # "reason" text) — the cut response fails validation and falls back to
    # analysis_type="other", which is how a real forecast/anomaly question
    # silently misses unsupported_recall instead of being genuinely misclassified.
    "planner_max_tokens": 768,
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


async def load_agent_config(agent_id: str, tenant_id: str | None = None) -> dict[str, Any] | None:
    from libs.database_connector.postgres_connector import client
    from services.workflow_runtime.infrastructure.mongo_client import (
        load_flow_edges,
        load_flow_nodes,
    )

    if not client.is_connected():
        logger.debug("DB not available — using DEV_CONFIG")
        return {**DEV_CONFIG, "agent_id": agent_id}

    try:
        async with client.get_client(tenant_id=tenant_id) as session:
            # 1. Load published agent_version với responder model + system prompt
            row = (
                await session.execute(
                    select(
                        AgentVersionsORM.id.label("agent_version_id"),
                        AgentVersionsORM.workflow_version_id,
                        AgentVersionsORM.memory_enabled,
                        AgentVersionsORM.llm_config,
                        AgentVersionsORM.retrieval_config,
                        LLMProvidersORM.model_id.label("responder_model"),
                        SystemPromptsORM.content.label("system_prompt"),
                    )
                    .join(AgentVersionsORM, AgentVersionsORM.id == AgentsORM.published_version_id)
                    .join(LLMProvidersORM, LLMProvidersORM.id == AgentVersionsORM.responder_model_id)
                    .outerjoin(SystemPromptsORM, SystemPromptsORM.id == AgentVersionsORM.system_prompt_id)
                    .where(AgentsORM.id == agent_id, AgentsORM.deleted_at.is_(None))
                )
            ).fetchone()

            if not row:
                logger.info("no published version for agent=%s", agent_id)
                return None

            llm_config       = dict(row.llm_config or {})
            wv_id            = str(row.workflow_version_id) if row.workflow_version_id else None

            # 2. KB connections
            kb_rows = (
                await session.execute(
                    select(KBConnectionsORM.endpoint_url, KBConnectionsORM.api_key_ref)
                    .join(AgentKBORM, AgentKBORM.kb_connection_id == KBConnectionsORM.id)
                    .where(AgentKBORM.version_id == row.agent_version_id)
                    .limit(1)
                )
            ).fetchall()

            kb_endpoint = kb_rows[0].endpoint_url if kb_rows else DEV_CONFIG["kb_endpoint"]

            # 3. MCP endpoints
            mcp_rows = (
                await session.execute(
                    select(MCPORM.name, MCPORM.endpoint_url, MCPORM.api_key_ref, MCPORM.capabilities)
                    .join(AgentMCPORM, AgentMCPORM.mcp_id == MCPORM.id)
                    .where(AgentMCPORM.version_id == row.agent_version_id)
                )
            ).fetchall()

            mcp_endpoints = [
                {
                    "name":          r.name,
                    "url":           r.endpoint_url,
                    "auth_token":    r.api_key_ref or "",
                    "allowed_tools": list(r.capabilities or []),
                }
                for r in mcp_rows
            ]

        # 4. Load flow_nodes + flow_edges từ MongoDB — hai query độc lập
        if wv_id:
            flow_nodes, flow_edges = await asyncio.gather(load_flow_nodes(wv_id), load_flow_edges(wv_id))
        else:
            flow_nodes, flow_edges = [], []
        node_cfg   = _extract_node_config(flow_nodes)
        # Each resolves a different part of node_cfg (execute_sql vs db_connections).
        await asyncio.gather(
            _resolve_warehouse_secret(node_cfg, tenant_id),
            _resolve_db_connector_secrets(node_cfg, tenant_id),
        )

        # A connected KB makes kb_search available to the planner even when the
        # agent has no explicit flow canvas — so answers get grounded in documents
        # (RAG) instead of the LLM's parametric memory.
        allowed_tools = list(node_cfg.get("allowed_tools") or DEV_CONFIG["allowed_tools"])
        if kb_rows and "kb_search" not in allowed_tools:
            allowed_tools.append("kb_search")

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
            # kb_search available whenever a KB is connected (RAG grounding)
            "allowed_tools": allowed_tools,
            # flow_nodes + flow_edges cho dynamic graph
            "flow_nodes": flow_nodes,
            "flow_edges": flow_edges,
        }

    except Exception as e:
        logger.warning("load_agent_config DB failed: %s — fallback DEV_CONFIG", e)
        return {**DEV_CONFIG, "agent_id": agent_id}


async def _resolve_warehouse_secret(node_cfg: dict[str, Any], tenant_id: str | None) -> None:
    """Mutates node_cfg in place: resolves execute_sql's `secret_id` (set by
    _extract_node_config below) into a real password via OpenBao, and always
    strips `secret_id` itself so it never reaches the HTTP body sent to
    sandbox_executor. No-op if no node configured Snowflake, or no secret_id
    was set — connection.password just stays "" (handler already treats that
    as "not configured", same as before this fix)."""
    connection = node_cfg.get("tool_config", {}).get("execute_sql", {}).get("connection")
    if not connection:
        return
    secret_id = connection.pop("secret_id", "")
    if not secret_id or not tenant_id:
        return

    from services.workflow_runtime.infrastructure.warehouse_secrets import (
        resolve_snowflake_password,
    )
    password = await resolve_snowflake_password(tenant_id, secret_id)
    if password:
        connection["password"] = password
    else:
        logger.warning("could not resolve snowflakeSecretId=%s for tenant=%s — execute_sql will fail auth", secret_id, tenant_id)


async def _resolve_db_connector_secrets(node_cfg: dict[str, Any], tenant_id: str | None) -> None:
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

    from services.workflow_runtime.infrastructure.warehouse_secrets import (
        resolve_db_connector_password,
    )

    async def _resolve(name: str, connection: dict[str, Any]) -> None:
        secret_id = connection.pop("secret_id", "")
        if not secret_id or not tenant_id:
            return
        password = await resolve_db_connector_password(tenant_id, secret_id)
        if password:
            connection["password"] = password
        else:
            logger.warning("could not resolve db_connector secret_id=%s for connection=%s tenant=%s — will fail auth", secret_id, name, tenant_id)

    # One secrets_vault lookup + one vault read per connection, independent of each other.
    await asyncio.gather(*(_resolve(name, connection) for name, connection in db_connections.items()))


def _extract_node_config(flow_nodes: list[dict[str, Any]]) -> dict[str, Any]:
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
            parsed: dict[str, Any] = {}
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
                    # "postgres" (default) or "clickhouse" — travels to
                    # sandbox_executor as connection.type and selects which
                    # read-only backend runs the query there (handlers/data.py
                    # maps it onto the wire shape). Absent/unknown => postgres,
                    # so existing flows are unchanged.
                    "type":      (conn.get("type") or "postgres").lower(),
                    "host":      conn.get("host", ""),
                    "port":      conn.get("port", 5432),
                    "database":  conn.get("database", ""),
                    # Postgres-only; defaults to 'public' — the only schema
                    # this ever queried before the field existed. Lets a
                    # connection point at a non-default schema, e.g.
                    # `kb_structured` (the KB's structured-table feature's
                    # tables), without exposing anything else in that
                    # database — handlers/data.py's introspection is scoped
                    # to exactly this schema, and nothing grants the
                    # connecting role visibility into any other one.
                    "schema":    conn.get("schema") or "public",
                    "user":      conn.get("user", ""),
                    # Resolved against OpenBao in load_agent_config() via
                    # warehouse_secrets.py — no plaintext password read here.
                    "secret_id": conn.get("secretId", ""),
                    "password":  "",
                    "note":      conn.get("note", ""),
                    # Opt-in link to a crawled Schema Catalog warehouse
                    # (SchemaCatalog.warehouse_id). Set => SQL generation uses the
                    # curated, embedded catalog for this connection instead of
                    # re-introspecting information_schema. Unset => introspection
                    # fallback, which is correct but coarser. Not defaulted: guessing
                    # which warehouse a connection maps to would feed the model
                    # tables from a different database.
                    "catalog_warehouse_id": conn.get("catalogWarehouseId", ""),
                }
                for conn in conns if conn.get("name") and conn.get("host") and conn.get("database")
            }
            if db_connections:
                cfg["tool_config"] = {**cfg.get("tool_config", {}), "db_connections": db_connections}
        elif t == "tool_executor":
            allowed = c.get("allowedBuiltinTools", [])
            if allowed:
                cfg["allowed_tools"] = allowed
            tool_config: dict[str, Any] = {**cfg.get("tool_config", {})}
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
