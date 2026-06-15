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
    "responder_model": "responder",
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
    "guardrail_input_patterns": [],
    "guardrail_llm_enabled":    False,
    "guardrail_model":           "openai/guardrail",
    "guardrail_max_tokens":      128,
    "guardrail_timeout":         10,
    "guardrail_allowed_topics":  [],
    "guardrail_id":              None,
    "agent_id": os.environ.get("DEV_AGENT_ID", "00000000-0000-0000-0000-000000000030"),
    "agent_version_id": None,
    "flow_nodes": [],
    "flow_edges": [],
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

            # 4. Guardrail — platform-wide, always from DB
            g_row = (await session.execute(text("""
                SELECT g.id, g.conditions,
                       lp.model_id AS guardrail_model
                FROM "Guardrails" g
                LEFT JOIN "LLMProviders" lp ON lp.id = g.guardrail_model_id
                ORDER BY g.priority DESC
                LIMIT 1
            """))).fetchone()

            if g_row:
                cond = dict(g_row.conditions or {})
                guardrail_cfg = {
                    "guardrail_input_patterns": cond.get("input_patterns", []),
                    "guardrail_llm_enabled":    cond.get("llm_enabled",    True),
                    "guardrail_allowed_topics": cond.get("allowed_topics", []),
                    "guardrail_model":          g_row.guardrail_model or DEV_CONFIG["guardrail_model"],
                    "guardrail_max_tokens":     cond.get("max_tokens",     128),
                    "guardrail_timeout":        cond.get("timeout",        10),
                    "guardrail_id":             str(g_row.id),
                }
            else:
                guardrail_cfg = {k: DEV_CONFIG[k] for k in (
                    "guardrail_input_patterns",
                    "guardrail_llm_enabled", "guardrail_model",
                    "guardrail_max_tokens", "guardrail_timeout",
                    "guardrail_allowed_topics", "guardrail_id",
                )}

        # 5. Load flow_nodes + flow_edges từ MongoDB
        flow_nodes = await load_flow_nodes(wv_id) if wv_id else []
        flow_edges = await load_flow_edges(wv_id) if wv_id else []
        node_cfg   = _extract_node_config(flow_nodes)

        return {
            **DEV_CONFIG,
            # identity
            "agent_id":         agent_id,
            "agent_version_id": str(row.agent_version_id),
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
            # guardrail
            **guardrail_cfg,
            # node-level config từ canvas
            **node_cfg,
            # flow_nodes + flow_edges cho dynamic graph
            "flow_nodes": flow_nodes,
            "flow_edges": flow_edges,
        }

    except Exception as e:
        logger.warning("load_agent_config DB failed: %s — fallback DEV_CONFIG", e)
        return {**DEV_CONFIG, "agent_id": agent_id}


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
    return cfg
