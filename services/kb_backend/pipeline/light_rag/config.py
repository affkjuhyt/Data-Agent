import os

# All LLM access (canonicalize/generate), embedding, and reranking go through the
# dedicated model-service gateway, which is env-routed to a local or remote backend.
# The retrieval pipeline never talks to llama / vLLM directly.
MODEL_SERVICE_URL = os.getenv("MODEL_SERVICE_URL", "http://model-service:8001")

# Databases
QDRANT_URL = os.getenv("QDRANT_URL", "http://qdrant:6333")
NEO4J_URL = os.getenv("NEO4J_URL", "bolt://neo4j:7687")
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "knowledge")
QDRANT_CACHE_COLLECTION = os.getenv("QDRANT_CACHE_COLLECTION", "query_cache")
CACHE_THRESHOLD = float(os.getenv("CACHE_THRESHOLD", "0.92"))
# Cached query results live for 24 hours; expired entries are ignored at lookup time
# (Qdrant has no native TTL) and are reclaimed by the background sweep loop.
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", str(24 * 60 * 60)))
# How often the background loop sweeps expired cache entries (default hourly).
CACHE_SWEEP_INTERVAL = int(os.getenv("CACHE_SWEEP_INTERVAL", str(60 * 60)))

# ── 2-step document narrowing (doc-summary collection → data_id filter) ───────
# When enabled, an extra retrieval pass searches the doc-summary collection for
# the top-m documents, then searches chunks restricted to those data_ids; the
# narrowed list joins the RRF fusion as a third ranked list (flat search stays —
# the doc pass is a precision path, never the only path).
DOC_NARROWING = os.getenv("DOC_NARROWING", "0") == "1"
QDRANT_DOC_COLLECTION = os.getenv("QDRANT_DOC_COLLECTION", "knowledge_documents")
DOC_PASS_TOP_M = int(os.getenv("DOC_PASS_TOP_M", "3"))          # documents from step 1
DOC_PASS_CHUNK_LIMIT = int(os.getenv("DOC_PASS_CHUNK_LIMIT", "5"))  # chunks from step 2

# ── Type-aware retrieval ───────────────────────────────────────────────────────
# Entities carry a closed-vocabulary type (person/location/...) on Neo4j nodes
# AND as per-type chunk payload fields (ent_person, ent_location, …). When the
# question implies an answer type ("ai" → person, "ở đâu" → location), graph
# expansion boosts matching-type neighbours and an extra typed-field search
# joins the RRF fusion. Soft signals only — a wrong type hint can demote but
# never exclude.
TYPE_AWARE = os.getenv("TYPE_AWARE", "1") == "1"
TYPE_BOOST = float(os.getenv("TYPE_BOOST", "0.08"))   # added to cosine in graph ranking

# ── Canonicalize/retrieval overlap ─────────────────────────────────────────────
# The canonicalize LLM call (~2-3s through the tunnel) does not gate vector
# search — pass-1 and doc narrowing need only the query embedding. When enabled,
# canonicalize runs concurrently with them, bounding total latency at roughly
# max(LLM, retrieval) instead of LLM + retrieval.
CANON_OVERLAP = os.getenv("CANON_OVERLAP", "0") == "1"

# ── Query-time alias resolution (KBEntityLookup) ───────────────────────────────
# When enabled, the query's canonical entities are resolved through the learned
# alias table before they're used as payload filters / graph seeds — a Vietnamese
# synonym in the query ("Sài Gòn") then matches the stored canonical entity
# ("thành_phố_hồ_chí_minh").
QUERY_ALIAS_RESOLVE = os.getenv("QUERY_ALIAS_RESOLVE", "0") == "1"

# Pipeline tuning
QDRANT_PASS1_LIMIT = int(os.getenv("QDRANT_PASS1_LIMIT", "3"))
QDRANT_PASS2_LIMIT = int(os.getenv("QDRANT_PASS2_LIMIT", "2"))
NEO4J_MAX_HOPS = int(os.getenv("NEO4J_MAX_HOPS", "1"))
NEO4J_MAX_NEIGHBOURS = int(os.getenv("NEO4J_MAX_NEIGHBOURS", "5"))
# Max entities (from ranked pass1 results) used as graph expansion starting points
NEO4J_TOP_ENTITIES = int(os.getenv("NEO4J_TOP_ENTITIES", "3"))
RRF_TOP_N = int(os.getenv("RRF_TOP_N", "10"))
RERANK_TOP_N = int(os.getenv("RERANK_TOP_N", "5"))
