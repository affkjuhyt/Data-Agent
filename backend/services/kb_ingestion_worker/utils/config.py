import os

POSTGRES_URL = os.getenv("POSTGRES_URL", "postgresql+asyncpg://aeroflow:aeroflow_secret@postgres/aeroflow_kb")
MONGO_URL = os.getenv("MONGO_URL", "mongodb://mongo:27017/dataagent")
MINIO_URL = os.getenv("MINIO_URL", "http://minioadmin:minioadmin@minio:9000")
KAFKA_URL = os.getenv("KAFKA_URL", "kafka:9092")
QDRANT_URL = os.getenv("QDRANT_URL", "http://qdrant:6333")
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://neo4j:7687")
# The LLM/embed/rerank backend is owned entirely by model_service. The worker
# only knows this URL and never which backend (local vs Colab) serves a call.
MODEL_SERVICE_URL = os.getenv("MODEL_SERVICE_URL", "http://model_service:8230")
# Result-cache invalidation: after gold completes, bump the tenant's retrieval-cache
# version in Redis so searches reflect the new content (KB-SCALE-10K.md §2).
REDIS_URL            = os.getenv("REDIS_URL", "redis://redis:6379/0")
RESULT_CACHE_ENABLED = os.getenv("RESULT_CACHE_ENABLED", "true").lower() in {"1", "true", "yes"}

KAFKA_CONSUMER_GROUP = os.getenv("KAFKA_CONSUMER_GROUP", "kb-ingestion")
KAFKA_TOPIC_SILVER = os.getenv("KAFKA_TOPIC_SILVER", "kb.promote.silver")
KAFKA_TOPIC_GOLD = os.getenv("KAFKA_TOPIC_GOLD", "kb.promote.gold")
KAFKA_TOPIC_CONFLICT_RESOLVE = os.getenv("KAFKA_TOPIC_CONFLICT_RESOLVE", "kb.resolve.conflict")

# ── Dead-letter queue + bounded retry (silver & gold) ─────────────────────────
# Previously a silver/gold exception was logged, the document marked FAILED, and
# the Kafka offset committed anyway — a terminal, non-replayable state requiring a
# manual re-promote. Now a failure is retried in-band up to INGESTION_MAX_ATTEMPTS
# (re-published to the SAME stage topic with an incremented `attempts` counter),
# and only after the attempts are exhausted is it routed to the DLQ topic — where
# it stays inspectable and replayable instead of vanishing.
KAFKA_TOPIC_DLQ = os.getenv("KAFKA_TOPIC_DLQ", "kb.ingestion.dlq")
INGESTION_MAX_ATTEMPTS = int(os.getenv("INGESTION_MAX_ATTEMPTS", "3"))

# ── Consume concurrency ───────────────────────────────────────────────────────
# The consume loop dispatches up to CONSUME_CONCURRENCY handlers in-flight at once
# (bounded by a semaphore) instead of processing one Kafka message at a time.
# Per-stage caps then bound how many of each stage run in parallel:
#   - SILVER stays at 1: OCR is CPU/GIL-bound and runs on a single shared Docling
#     DocumentConverter (not proven thread-safe) under a 2 GB memory limit —
#     concurrency wouldn't speed it up and risks converter races / OOM.
#   - GOLD is I/O-bound (LLM extraction + embedding HTTP + pooled DB writes) and
#     overlaps cheaply, so it runs several in parallel. This is the real win:
#     a doc's gold I/O can now proceed while another doc is still OCR-ing in silver.
CONSUME_CONCURRENCY   = int(os.getenv("CONSUME_CONCURRENCY",   "8"))
SILVER_CONCURRENCY    = int(os.getenv("SILVER_CONCURRENCY",    "1"))
GOLD_CONCURRENCY      = int(os.getenv("GOLD_CONCURRENCY",      "6"))
# Chunks within ONE gold doc: 1 = strictly sequential (default; exact legacy
# behaviour + maximal cross-chunk entity dedup). > 1 overlaps the per-chunk
# LLM+embed waits (bounded by the LLM gateway's parallelism) at the cost of slightly weaker
# same-batch dedup (registry upserts keep it correct). KB-SCALE-10K.md §6.
GOLD_CHUNK_CONCURRENCY = int(os.getenv("GOLD_CHUNK_CONCURRENCY", "1"))
# Conflict resolution is I/O-bound (DB reads + LLM merge + Qdrant/Neo4j writes).
# Keep it below GOLD_CONCURRENCY to avoid starving gold tasks of LLM/embed capacity.
CONFLICT_CONCURRENCY = int(os.getenv("CONFLICT_CONCURRENCY", "3"))

MINIO_BUCKET = os.getenv("MINIO_BUCKET", "knowledge-base")
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "knowledge")

# ── Document-level collection (2-step retrieval) ──────────────────────────────
# One point per document: point_id = data_id, vector = embedding of the rolled-up
# document summary. Inserted LAST during gold promotion so a half-ingested document
# is invisible to step-1 retrieval (the doc point is the transaction boundary).
QDRANT_DOC_COLLECTION = os.getenv("QDRANT_DOC_COLLECTION", "knowledge_documents")

# Map-reduce document summary: chunk summaries are concatenated in document order
# into batches of at most DOC_SUMMARY_BATCH_CHARS characters; each batch is reduced
# by one LLM call; if the concatenated result still exceeds DOC_SUMMARY_TARGET_CHARS
# the pass repeats, at most DOC_SUMMARY_MAX_DEPTH times (then hard-truncate) — a
# small model can return summaries that don't shrink, so the loop must be bounded.
DOC_SUMMARY_BATCH_CHARS = int(os.getenv("DOC_SUMMARY_BATCH_CHARS", "4000"))
DOC_SUMMARY_TARGET_CHARS = int(os.getenv("DOC_SUMMARY_TARGET_CHARS", "4000"))
DOC_SUMMARY_MAX_DEPTH = int(os.getenv("DOC_SUMMARY_MAX_DEPTH", "4"))

# Entity/relationship extraction now happens ONCE per document (from the rolled-up
# summary) instead of per chunk, so the caps are higher than the old per-chunk 5.
MAX_DOC_ENTITIES = int(os.getenv("MAX_DOC_ENTITIES", "15"))
MAX_DOC_RELATIONSHIPS = int(os.getenv("MAX_DOC_RELATIONSHIPS", "15"))

# Language of the extraction prompts: "vi" (native Vietnamese instructions — the
# corpus is Vietnamese and English-framed instructions showed comprehension
# slippage: misaligned descriptions, hallucinated aliases) or "en". JSON keys and
# type enum values stay English either way (schema-enforced).
PROMPT_LANG = os.getenv("PROMPT_LANG", "vi")

# LLM alias verification: one batched call per document asking, pair by pair,
# whether each proposed alias really names the SAME entity. Verification is a far
# easier task than generation — the audited pollution ("Đức" on "Liên bang Nga",
# "Creative Commons" on the GFDL) came from generation conflating related with
# same, not from missing knowledge. Conservative on failure: unverified aliases
# are NOT learned (alias-table pollution is sticky; a missed alias is recoverable).
ALIAS_VERIFY = os.getenv("ALIAS_VERIFY", "1") == "1"

# Bounded parallelism for the per-chunk summary LLM calls in the silver stage.
SILVER_SUMMARY_CONCURRENCY = int(os.getenv("SILVER_SUMMARY_CONCURRENCY", "4"))

# Web portal fetch timeout (seconds) for the bronze→silver web path.
WEB_FETCH_TIMEOUT = float(os.getenv("WEB_FETCH_TIMEOUT", "30"))
# Headless-render web portals with Chromium (most are client-rendered SPAs that
# need JS to paint their content). Set to 0/false to use a static httpx fetch only.
WEB_USE_PLAYWRIGHT = os.getenv("WEB_USE_PLAYWRIGHT", "1") not in ("0", "false", "False", "")
# Max seconds to wait for the page's network to settle (template/content to finish
# painting) after DOMContentLoaded before grabbing the rendered HTML.
WEB_RENDER_WAIT = float(os.getenv("WEB_RENDER_WAIT", "12"))
# Similarity floor for a stage-2 candidate to enter the stage-3 reasoning-pivot
# comparison. No auto-resolve — every confirmed conflict is surfaced as PENDING
# for human review. Stage 2 matches the incoming chunk's vector against stored chunk
# vectors, so this is a chunk-to-chunk cosine. Measured on multilingual-e5-small over
# the Vietnamese policy corpus, against one baseline chunk:
#   contradiction (12 vs 20 days, otherwise identical wording) 0.970
#   same policy re-dated / re-versioned                        0.921
#   same facts fully reworded (a real duplicate)               0.906
#   same topic, compatible extra clauses                       0.846
#   different topic entirely (salary policy)                   0.687
# Conflicting rewrites bunch at 0.90-0.97, so a 0.90 floor sat inside that band and
# dropped duplicates/related chunks; the nearest unrelated chunk is far below at 0.69.
# 0.80 keeps that separation with margin and leaves precision to stage 3's
# irrelevant_score, which is the real semantic gate. Recall matters more here: a
# missed candidate is a conflict that silently never surfaces.

#Conflict
CONFLICT_SIMILARITY_THRESHOLD = float(os.getenv("CONFLICT_SIMILARITY_THRESHOLD", "0.80"))
# Max candidate chunks retrieved per chunk in the conflict pipeline's stage 2.
CONFLICT_TOP_K_DOCS = int(os.getenv("CONFLICT_TOP_K_DOCS", "5"))
# A stage-3 candidate whose irrelevant_score is at/above this floor is dropped before conflict-type assignment.
CONFLICT_IRRELEVANT_THRESHOLD = float(os.getenv("CONFLICT_IRRELEVANT_THRESHOLD", "0.5"))
# Stage-1 fallback source length (chars) when a chunk has no summary yet.
CONFLICT_QUESTION_SOURCE_CHAR_LIMIT = int(os.getenv("CONFLICT_QUESTION_SOURCE_CHAR_LIMIT", "2000"))
# During a batch merge, a re-check conflict is raised when the merged working
# content's strongest criterion against the next incoming clears this floor.
CONFLICT_MERGE_RECHECK_THRESHOLD = float(os.getenv("CONFLICT_MERGE_RECHECK_THRESHOLD", "0.5"))
# A chunk can answer several distinct questions (stage 1); this bounds how many
# questions run stage 2+3 concurrently.
CONFLICT_QUESTION_CONCURRENCY = int(os.getenv("CONFLICT_QUESTION_CONCURRENCY", "3"))
# Within ONE question, how many candidate documents stage 3 scores concurrently.
CONFLICT_DOCUMENT_CONCURRENCY = int(os.getenv("CONFLICT_DOCUMENT_CONCURRENCY", "3"))
# A valid-but-empty pivot list isn't a JSON error, so _llm_generate_json's own
# retry never fires for it. This caps extra attempts when that happens.
CONFLICT_SEMANTIC_RETRY_ATTEMPTS = int(os.getenv("CONFLICT_SEMANTIC_RETRY_ATTEMPTS", "2"))

# Entity registry
ENTITY_COLLECTION = os.getenv("ENTITY_COLLECTION", "entity_registry")
# Measured on multilingual-e5-small (its cosine range is compressed toward 1.0):
# same-entity pairs score 0.95-0.98 but DIFFERENT countries score up to 0.949
# (Bangladesh~Ấn Độ 0.9492 vs TP.HCM~Sài Gòn 0.9495 — overlapping!). So:
#   >= ENTITY_SIMILARITY_THRESHOLD (0.96): automatic merge (type-guarded)
#   >= ENTITY_VERIFY_BAND (0.85): merge ONLY if the LLM confirms same-entity
#   below: new entity. Cosine alone cannot decide merges with this model.
ENTITY_SIMILARITY_THRESHOLD = float(os.getenv("ENTITY_SIMILARITY_THRESHOLD", "0.96"))
ENTITY_VERIFY_BAND = float(os.getenv("ENTITY_VERIFY_BAND", "0.85"))
# Soft (cosine + word-overlap Jaccard) matching is DISABLED by default: Vietnamese
# breaks it both ways — true synonyms share zero tokens ("Sài Gòn"/"TP.HCM") so the
# soft path never fires for them, while distinct entities share long runs of generic
# syllables ("Đại học Quốc gia Hà Nội"/"Đại học Quốc gia TP.HCM", Jaccard ≈ 0.6) so
# it confirms FALSE merges. Hard cosine + the LLM-alias table cover the real cases;
# under-merging (duplicate node, joinable later) is far cheaper than over-merging
# (permanently corrupted graph node).
ENTITY_SOFT_MATCH = os.getenv("ENTITY_SOFT_MATCH", "0") == "1"
ENTITY_SOFT_THRESHOLD = float(os.getenv("ENTITY_SOFT_THRESHOLD", "0.72"))
ENTITY_WORD_OVERLAP_THRESHOLD = float(os.getenv("ENTITY_WORD_OVERLAP_THRESHOLD", "0.50"))

LLM_MODEL = os.getenv("LLM_MODEL", "agent-fast")
VLM_MODEL = os.getenv("VLM_MODEL", "agent-main")

# Fallback embed model UUID used by conflict_resolver when it has no collection
# context.  The gold pipeline resolves the real UUID per-collection at task time
# (via _resolve_collection_embed_model in gold.py) — this is only a last resort.
EMBED_MODEL_ID: str = os.getenv("EMBED_MODEL_ID", "")
VLM_FRAME_INTERVAL = int(os.getenv("VLM_FRAME_INTERVAL", "30"))
USE_VLM = os.getenv("USE_VLM", "false").lower() == "true"
