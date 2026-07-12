"""Shared kb_backend constants.

Single source for values that were previously hardcoded and duplicated across
routers (`_DEFAULT_TENANT` in two routers, the role→permission-id map inline in
ingestion). These mirror rows seeded in infra/Postgres/seed.sql; overridable via
env so non-dev environments can point at their own ids.
"""
import os

# Seeded default tenant (infra/Postgres/seed.sql → "Dev Tenant").
DEFAULT_TENANT_ID = os.getenv("KB_DEFAULT_TENANT_ID", "a0000000-0000-0000-0000-000000000001")

# App role name → RolePermissions.id stamped on a new KBData row (KBData.role_id FK).
# MUST match the RolePermissions rows in infra/Postgres/seed.sql.
ROLE_PERMISSION_IDS: dict[str, str] = {
    "PLATFORM_ADMIN":    os.getenv("KB_ROLE_ID_PLATFORM_ADMIN",    "00000000-0000-0000-0000-000000000010"),
    "AI_ENGINEER":       os.getenv("KB_ROLE_ID_AI_ENGINEER",       "00000000-0000-0000-0000-000000000011"),
    "BUSINESS_OPERATOR": os.getenv("KB_ROLE_ID_BUSINESS_OPERATOR", "00000000-0000-0000-0000-000000000012"),
    "EXECUTIVE":         os.getenv("KB_ROLE_ID_EXECUTIVE",         "00000000-0000-0000-0000-000000000013"),
}
