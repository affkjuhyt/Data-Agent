# Qdrant Collections

All collections are per-deployment. The default names below can be overridden via environment variables.

---

## `knowledge`
**Env var:** `QDRANT_COLLECTION` · **Default:** `knowledge`
**Created by:** `infra/kb-ingestion-worker/main.py` at startup (`strict_dim_check=True`)
**Written by:** gold worker (`infra/kb-ingestion-worker/gold.py`)
**Read by:** LightRAG retrieval pipeline (`app/pipeline/light_rag/pipeline.py`) — pass1, pass2 vector search

**Purpose:** Stores one point per committed knowledge chunk (text block). Step-2 retrieval surface: searched with a `data_id` filter built from the doc-level hits in `knowledge_documents` (plus flat fallback search).

**Vector:** Embedding of the chunk **content** (the textblock itself).

**Payload:**
| Field | Type | Description |
|---|---|---|
| `tenant_id` | `str` (UUID) | Tenant isolation key — all queries filter on this |
| `data_id` | `str` (UUID) | Parent `KBData` document ID — **payload-indexed**, backs step-2 narrowing |
| `block_id` | `str` (UUID) | 1-to-1 with `KBTextBlock` row in Postgres; used for hydration |
| `summary` | `str` | 1–2 sentence LLM summary, same language as the chunk (written by silver) |
| `intents` | `list[str]` | Snake_case intent labels (e.g. `information_retrieval`) |
| `knowledge_base` | `str` (UUID) | KB scope filter |
| `is_deleted` | `bool` | Soft-delete flag |

Entities are no longer stored per chunk — extraction is document-level (see `knowledge_documents`).

**Point ID:** `block_id` (UUID string, same as Postgres `KBTextBlock.block_id`)

---

## `knowledge_documents`
**Env var:** `QDRANT_DOC_COLLECTION` · **Default:** `knowledge_documents`
**Created by:** `infra/kb-ingestion-worker/main.py` at startup (`strict_dim_check=True`)
**Written by:** gold worker (`infra/kb-ingestion-worker/gold.py`) — written **last** during promotion
**Read by:** step-1 of 2-step retrieval (find top-m documents, then narrow `knowledge` by their `data_id`s)

**Purpose:** One point per GOLD document. The vector is the embedding of the map-reduced document summary (chunk summaries → batched LLM reduce passes → one summary). Because this point is the final write of gold promotion, a document missing here is by definition partially ingested and invisible to 2-step retrieval — the doc point is the transaction boundary.

**Vector:** Embedding of the rolled-up **document summary** (same embedding model as `knowledge`).

**Payload:**
| Field | Type | Description |
|---|---|---|
| `tenant_id` | `str` (UUID) | Tenant isolation key |
| `data_id` | `str` (UUID) | Same as the point ID |
| `name` | `str` | Document name from `KBData` |
| `summary` | `str` | Rolled-up document summary (same language as the document) |
| `entities` | `list[str]` | Canonical doc-level entity names (boost/filter in step 1) |
| `intents` | `list[str]` | Top-5 aggregated chunk intents |
| `chunk_count` | `int` | Committed chunk count |
| `knowledge_base` | `str` (UUID) | KB scope filter |
| `is_deleted` | `bool` | Soft-delete flag |

**Point ID:** `data_id` (UUID string, same as Postgres `KBData.data_id`)

---

## `entity_registry`
**Env var:** `ENTITY_COLLECTION` · **Default:** `entity_registry`
**Created by:** `infra/kb-ingestion-worker/main.py` at startup (`strict_dim_check=False`)
**Written by:** `infra/kb-ingestion-worker/entity_registry.py`
**Read by:** `entity_registry.normalize_entities()` during gold promotion for semantic dedup

**Purpose:** Deduplication registry for extracted entities. Each unique canonical entity per tenant gets one point. Embedding match is the SECOND resolution step — exact alias lookup in Postgres `KBEntityLookup` runs first (the Vietnamese synonym path: "TP.HCM" / "Sài Gòn" / "Thành phố Hồ Chí Minh" resolve deterministically once learned). Canonical forms preserve Vietnamese diacritics — tone marks are lexical and are never stripped.

**Vector:** Embedding of `"name: description"` — the description carries the semantics that joins synonyms whose surface forms share no tokens ("Mỹ" / "Hoa Kỳ").

**Payload:**
| Field | Type | Description |
|---|---|---|
| `tenant_id` | `str` (UUID) | Tenant isolation key |
| `canonical` | `str` | Canonical form: lowercase + underscores, Unicode preserved (e.g. `thành_phố_hồ_chí_minh`) |
| `display_name` | `str` | Original surface form — case and diacritics exactly as written (Neo4j display) |
| `aliases` | `list[str]` | Surface forms mapped to this canonical (LLM-proposed + learned) |
| `description` | `str` | Short description of the entity (stored on first registration, updated if later encounter has a description and existing entry does not) |
| `is_deleted` | `bool` | Soft-delete flag |

**Point ID:** Deterministic UUID v5: `uuid5(NS, "{tenant_id}:{canonical}")` — makes upsert idempotent; re-processing the same entity never creates a duplicate.

**Matching thresholds** (configurable via env):
| Variable | Default | Meaning |
|---|---|---|
| `ENTITY_SIMILARITY_THRESHOLD` | `0.88` | Hard cosine match — same entity |
| `ENTITY_SOFT_THRESHOLD` | `0.72` | Soft cosine — also check word overlap |
| `ENTITY_WORD_OVERLAP_THRESHOLD` | `0.50` | Jaccard on word tokens (soft path) |

---

## `query_cache`
**Env var:** `QDRANT_CACHE_COLLECTION` · **Default:** `query_cache`
**Created by:** Not auto-created by the worker — must be provisioned manually or by the API service at startup.
**Written by:** *(cache population not yet implemented — reserved for future write-back after successful retrieval)*
**Read by:** LightRAG retrieval pipeline (`app/pipeline/light_rag/pipeline.py`) — step 2, early-return cache lookup

**Purpose:** Semantic cache for retrieval queries. On each query the raw query is embedded and searched here first. A hit above `CACHE_THRESHOLD` returns the cached result immediately, skipping the full pipeline (Qdrant pass1/pass2, Neo4j expansion, reranking, Postgres hydration).

**Vector:** Embedding of the raw user query string.

**Payload:** Cached pipeline result (free-form dict — same shape as a single entry in the `results` list returned by the pipeline).

**Point ID:** Query UUID (assigned at cache-write time).

**Threshold:** `CACHE_THRESHOLD` env var, default `0.92` — conservative to avoid returning stale answers for slightly different questions.
