import os


CORS_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("CORS_ORIGINS", "http://localhost:3000,http://localhost:8150").split(",")
    if origin.strip()
]

# Reciprocal Rank Fusion constant used by the retrieval pipeline. Surfaced to the
# UI via GET /api/runtime-config so the flow builder shows the real value instead
# of a hardcoded one.
RRF_K = int(os.environ.get("RRF_K", "60"))

# Redis used for cross-replica graph-cache invalidation (pub/sub). Empty = the
# listener is disabled and the cache relies purely on version-keyed rebuilds.
REDIS_URL = os.environ.get("REDIS_URL", "")
