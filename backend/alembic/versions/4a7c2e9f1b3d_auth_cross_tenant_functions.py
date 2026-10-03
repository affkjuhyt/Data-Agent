"""auth_cross_tenant_functions

auth-api moves from the superuser to app_user (NOBYPASSRLS). Three of its reads
must cross tenants, and row-level security would hide every row from them:

  * API key verification: Kong presents a key, and the key is what says which
    tenant it belongs to, so the lookup by prefix cannot be scoped first.
  * The Kong IP allowlist: one ip-restriction plugin serves every tenant, so it
    gets the union of the active CIDRs.
  * The auto-rotation scan for overdue secrets.

Each is a SECURITY DEFINER function owned by the migration role, returning only
the columns its caller uses, executable by app_user only. They widen nothing:
app.tenant_id is set by the session itself, so app_user could already read any
tenant's rows; RLS here is a guard against forgetting the tenant, not against a
hostile app_user.

Revision ID: 4a7c2e9f1b3d
Revises: 3f1b5d7a9c2e
Create Date: 2026-10-03
"""

from collections.abc import Sequence

from alembic import op

revision: str = "4a7c2e9f1b3d"
down_revision: str | None = "3f1b5d7a9c2e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (signature, definition, comment)
_FUNCTIONS = (
    (
        "auth_api_key_candidates(p_prefix text)",
        """
        RETURNS TABLE (id uuid, tenant_id uuid, name varchar, key_hash text, scope varchar,
                       last_used_at timestamptz)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp
        AS $$
            SELECT k.id, k.tenant_id, k.name, k.key_hash, k.scope, k.last_used_at
            FROM api_keys k
            WHERE k.key_prefix = p_prefix
              AND k.revoked_at IS NULL
              AND (k.expires_at IS NULL OR k.expires_at > now())
        $$
        """,
        "Live API keys with this prefix, any tenant: auth-api /internal/api-keys/verify "
        "compares the presented key against each hash (a prefix can be shared).",
    ),
    (
        "auth_active_cidrs()",
        """
        RETURNS SETOF varchar
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp
        AS $$
            SELECT DISTINCT cidr FROM ip_allowlists WHERE is_active ORDER BY cidr
        $$
        """,
        "Every tenant's active allowlist CIDRs, de-duplicated: the one Kong "
        "ip-restriction plugin admits their union (auth-api kong_sync).",
    ),
    (
        "auth_secrets_due_for_rotation()",
        """
        RETURNS TABLE (id uuid, tenant_id uuid)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp
        AS $$
            SELECT s.id, s.tenant_id FROM secrets_vault s
            WHERE s.is_active AND s.rotation_due_at < now()
        $$
        """,
        "Active secrets past their rotation date, any tenant: the auto-rotation scan. "
        "Each one is then rotated in a session scoped to its tenant.",
    ),
)


def upgrade() -> None:
    for signature, definition, comment in _FUNCTIONS:
        op.execute(f"CREATE FUNCTION {signature} {definition}")
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO app_user")
        quoted = comment.replace("'", "''")
        op.execute(f"COMMENT ON FUNCTION {signature} IS '{quoted}'")


def downgrade() -> None:
    for signature, _definition, _comment in reversed(_FUNCTIONS):
        op.execute(f"DROP FUNCTION {signature}")
