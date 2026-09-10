import os

import structlog

structlog.configure(processors=[structlog.processors.JSONRenderer()])
log = structlog.get_logger()

# No default. The old one ("aeroflow:aeroflow_secret@postgres:5432/aeroflow")
# named a user, database and port that do not exist, so a missing env var
# failed at connect time with a confusing error instead of at startup.
PG_DSN = os.environ.get("DATABASE_URL", "")
KONG_ADMIN = os.environ.get("KONG_ADMIN_URL", "http://kong:8001")
OPENBAO_ADDR = os.environ.get("OPENBAO_ADDR", "http://openbao:8200")

PRIVATE_FALLBACK_CIDRS = [
    "127.0.0.1/32",
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
]

POOL_MIN = int(os.environ.get("DB_POOL_MIN", "2"))
POOL_MAX = int(os.environ.get("DB_POOL_MAX", "20"))
OWNER_POOL_MAX = POOL_MAX + 2
