import os

# ── LiteLLM gateway ─────────────────────────────────────────────────────────
LITELLM_BASE_URL = os.environ.get("LITELLM_BASE_URL", "http://localhost:4000")
LITELLM_API_KEY = os.environ.get("LITELLM_API_KEY", "sk-dev")

# Model aliases — phải khớp với model_name trong infra/litellm/config.yaml
# Tách riêng khỏi agent-level "planner"/"responder" để swap model to hơn độc lập
PLANNER_MODEL = os.environ.get("ORCHESTRATOR_PLANNER_MODEL", "openai/orchestrator-planner")
SUMMARY_MODEL = os.environ.get("ORCHESTRATOR_SUMMARY_MODEL", "openai/orchestrator-summary")
# Judge cho bước tổng hợp multi-agent (synthesis_judge_check trong summary.py)
SYNTHESIS_JUDGE_MODEL = os.environ.get("ORCHESTRATOR_JUDGE_MODEL", "openai/orchestrator-judge")
MAX_SYNTHESIS_RETRY = int(os.environ.get("ORCHESTRATOR_MAX_SYNTHESIS_RETRY", "2"))

# ── Database ─────────────────────────────────────────────────────────────────
DATABASE_URL = os.environ.get("DATABASE_URL", "")
DEV_TENANT_ID = os.environ.get("DEV_TENANT_ID", "00000000-0000-0000-0000-000000000001")

# ── Redis (planning-state checkpoint cache) ──────────────────────────────────
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")

# ── Workflow runtime (agent workflows) ──────────────────────────────────────
WORKFLOW_RUNTIME_URL = os.environ.get("WORKFLOW_RUNTIME_URL", "http://localhost:8001")


# ── Planner ToT parameters ───────────────────────────────────────────────────
H_ES = float(os.environ.get("DPTS_H_ES", "0.5"))
H_DS = float(os.environ.get("DPTS_H_DS", "0.7"))
BRANCHING = int(os.environ.get("DPTS_BRANCHING", "2"))
MAX_PLAN_DEPTH = int(os.environ.get("DPTS_MAX_PLAN_DEPTH", "4"))
QUEUE_SIZE = int(os.environ.get("DPTS_QUEUE_SIZE", "4"))
EXPLOIT_RATIO = float(os.environ.get("DPTS_EXPLOIT_RATIO", "0.5"))

# MockLLM fallback khi không có LITELLM_URL (dev/test)
USE_MOCK_PLANNER = os.environ.get("USE_MOCK_PLANNER", "").lower() in ("1", "true", "yes")

# ── Executor ─────────────────────────────────────────────────────────────────
AGENT_CALL_TIMEOUT = int(os.environ.get("AGENT_CALL_TIMEOUT", "120"))   # seconds
