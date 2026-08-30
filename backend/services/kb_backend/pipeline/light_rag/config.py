import os

MODEL_SERVICE_URL = os.getenv("MODEL_SERVICE_URL", "http://model-service:8001")

QDRANT_URL = os.getenv("QDRANT_URL", "http://qdrant:6333")
NEO4J_URL = os.getenv("NEO4J_URL", "bolt://neo4j:7687")
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "knowledge")
QDRANT_CACHE_COLLECTION = os.getenv("QDRANT_CACHE_COLLECTION", "query_cache")
CACHE_THRESHOLD = float(os.getenv("CACHE_THRESHOLD", "0.92"))
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", str(24 * 60 * 60)))
CACHE_SWEEP_INTERVAL = int(os.getenv("CACHE_SWEEP_INTERVAL", str(60 * 60)))

DOC_NARROWING = os.getenv("DOC_NARROWING", "0") == "1"
QDRANT_DOC_COLLECTION = os.getenv("QDRANT_DOC_COLLECTION", "knowledge_documents")
DOC_PASS_TOP_M = int(os.getenv("DOC_PASS_TOP_M", "3"))
DOC_PASS_CHUNK_LIMIT = int(os.getenv("DOC_PASS_CHUNK_LIMIT", "5"))

DEGRADE_BUDGET_MS = int(os.getenv("DEGRADE_BUDGET_MS", "0"))

TYPE_AWARE = os.getenv("TYPE_AWARE", "1") == "1"
TYPE_BOOST = float(os.getenv("TYPE_BOOST", "0.08"))

CANON_OVERLAP = os.getenv("CANON_OVERLAP", "0") == "1"

QUERY_ALIAS_RESOLVE = os.getenv("QUERY_ALIAS_RESOLVE", "0") == "1"

HYBRID_LEXICAL = os.getenv("HYBRID_LEXICAL", "0") == "1"
LEXICAL_LIMIT = int(os.getenv("LEXICAL_LIMIT", "5"))
LEXICAL_TS_CONFIG = os.getenv("LEXICAL_TS_CONFIG", "simple")

CONTENT_INTENTS = [
    s.strip() for s in os.getenv(
        "CONTENT_INTENTS",
        "information_retrieval,regulation_compliance,technical_specification,"
        "financial_reporting,process_description,organisational_info,"
        "question_answering,data_definition,historical_record,announcement",
    ).split(",") if s.strip()
]

QDRANT_PASS1_LIMIT = int(os.getenv("QDRANT_PASS1_LIMIT", "3"))
QDRANT_PASS2_LIMIT = int(os.getenv("QDRANT_PASS2_LIMIT", "2"))
NEO4J_MAX_HOPS = int(os.getenv("NEO4J_MAX_HOPS", "1"))
NEO4J_MAX_NEIGHBOURS = int(os.getenv("NEO4J_MAX_NEIGHBOURS", "5"))
NEO4J_TOP_ENTITIES = int(os.getenv("NEO4J_TOP_ENTITIES", "3"))
RRF_TOP_N = int(os.getenv("RRF_TOP_N", "10"))
RERANK_TOP_N = int(os.getenv("RERANK_TOP_N", "5"))

RRF_K = int(os.getenv("RRF_K", "60"))
GRAPH_SEED_MARGIN = int(os.getenv("GRAPH_SEED_MARGIN", "3"))
MAX_PREFERRED_TYPES = int(os.getenv("MAX_PREFERRED_TYPES", "2"))
MAX_TYPED_GROUPS = int(os.getenv("MAX_TYPED_GROUPS", "3"))
GRAPH_PREVIEW_LIMIT = int(os.getenv("GRAPH_PREVIEW_LIMIT", "20"))
