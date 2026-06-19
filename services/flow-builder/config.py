import os


DEV_TENANT_ID = os.environ.get("DEV_TENANT_ID", "00000000-0000-0000-0000-000000000001")

# Redis for broadcasting graph-cache invalidation to workflow-runtime replicas
# when a workflow version is (re)published. Empty = no broadcast (runtime still
# rebuilds on the next version bump via its version-keyed cache).
REDIS_URL = os.environ.get("REDIS_URL", "")
