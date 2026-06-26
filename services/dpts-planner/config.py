import os

# ── Mongo (plan DB — plan stored with the user query) ───────────────────────
MONGODB_URL = os.environ.get("MONGODB_URL", "mongodb://localhost:27017/dataagent")
PLANS_COLLECTION = os.environ.get("DPTS_PLANS_COLLECTION", "dpts_plans")

# ── Elasticsearch (Cached Plan — search if a plan exists for a user query) ──
ES_URL = os.environ.get("DPTS_ES_URL", "")  # empty = Cached Plan search disabled
ES_INDEX = os.environ.get("DPTS_ES_INDEX", "dpts_plans")
ES_MIN_SCORE = float(os.environ.get("DPTS_ES_MIN_SCORE", "1.0"))  # BM25 is corpus-relative; tune per corpus

# ── Model service (infra/model-service /generate -> vLLM) ───────────────────
MODEL_SERVICE_URL = os.environ.get("MODEL_SERVICE_URL", "")  # empty -> MockLLM fallback

# ── Agents catalog (Postgres) — task `type` is chosen from these agents ─────
POSTGRES_DSN = os.environ.get("POSTGRES_DSN", "")            # empty -> task type stays free-form
AGENT_TENANT_ID = os.environ.get("DPTS_AGENT_TENANT_ID", "")  # empty -> agents across all tenants

# ── Cached-plan TTL (sweeper erases expired plans from Mongo + ES) ──────────
PLAN_TTL_SECONDS = int(os.environ.get("DPTS_PLAN_TTL_SECONDS", "86400"))   # 24h
SWEEP_INTERVAL_SECONDS = int(os.environ.get("DPTS_SWEEP_INTERVAL_SECONDS", "300"))

# ── Search parameters ───────────────────────────────────────────────────────
H_ES = float(os.environ.get("DPTS_H_ES", "0.5"))   # demote exploitation → pool
H_DS = float(os.environ.get("DPTS_H_DS", "0.7"))   # promote exploration → exploitation
BRANCHING = int(os.environ.get("DPTS_BRANCHING", "2"))
MAX_PLAN_DEPTH = int(os.environ.get("DPTS_MAX_PLAN_DEPTH", "4"))  # hard cap on plan length
QUEUE_SIZE = int(os.environ.get("DPTS_QUEUE_SIZE", "4"))  # static fallback |P|
EXPLOIT_RATIO = float(os.environ.get("DPTS_EXPLOIT_RATIO", "0.5"))

# no model service configured -> deterministic MockLLM (local dev / CI)
USE_MOCK_EXPANDER = os.environ.get("DPTS_USE_MOCK", "").lower() in ("1", "true", "yes") or not MODEL_SERVICE_URL
