#!/bin/sh
# One-shot: read-only AppRole for schema-crawler-worker to resolve Snowflake
# warehouse secrets from OpenBao
# (services/schema-crawler-worker/warehouse_secrets.py) — see
# docs/Schema_Crawler_Risk_Remediation_Plan.md #1.
#
# Deliberately a SEPARATE AppRole from workflow-runtime's
# (openbao-approle-setup.sh), even though both bind to the same read-only
# policy (secret/data/tenants/*, read-only) — a compromised
# schema-crawler-worker only ever holds its own role_id/secret_id, never
# workflow-runtime's, and vice versa. Revoking/rotating one process's
# credentials doesn't require touching the other's.
#
# Idempotent: safe to re-run. role_id/secret_id are persisted to the data
# volume on first run so re-runs print the same credentials instead of
# minting a new secret_id every time.
#
# Run from inside the openbao container (config dir is mounted there):
#   docker exec dataagent-openbao sh /openbao/config/openbao-schema-crawler-approle-setup.sh
# Then copy the printed OPENBAO_ROLE_ID/OPENBAO_SECRET_ID into .env
# (as the schema-crawler-worker service's own values, not workflow-runtime's)
# and restart schema-crawler-worker.
set -eu

VAULT_ADDR="${VAULT_ADDR:-http://openbao:8200}"
DATA_DIR="/openbao/data"
TOKEN_FILE="$DATA_DIR/.root-token"
CREDS_FILE="$DATA_DIR/.schema-crawler-worker-approle.env"

if [ -z "${VAULT_TOKEN:-}" ] && [ -f "$TOKEN_FILE" ]; then
  VAULT_TOKEN=$(cat "$TOKEN_FILE")
fi
VAULT_TOKEN="${VAULT_TOKEN:-aeroflow-dev-token}"

# Reuses workflow-runtime's read-only policy (same scope: secret/data/tenants/*,
# "read" only) rather than defining an identical one under a new name — the
# isolation this script provides is per-AppRole (separate role_id/secret_id),
# not per-policy.
POLICY_NAME="workflow-runtime-read-policy"
ROLE_NAME="schema-crawler-worker"

echo "⏳ Waiting for OpenBao at $VAULT_ADDR ..."
for i in $(seq 1 30); do
  STATUS=$(curl -sf "$VAULT_ADDR/v1/sys/health" 2>/dev/null || true)
  if echo "$STATUS" | grep -q '"initialized":true'; then break; fi
  echo "  attempt $i/30 — retrying in 3s"
  sleep 3
done
echo "✅ OpenBao ready"

# ── Read-only policy: secret/data/tenants/* only, "read" capability only ──
echo "→ Creating/updating policy '$POLICY_NAME'"
POLICY='path \"secret/data/tenants/*\" { capabilities = [\"read\"] }'
curl -sf -X POST -H "X-Vault-Token: $VAULT_TOKEN" -H "Content-Type: application/json" \
  -d "{\"policy\":\"$POLICY\"}" \
  "$VAULT_ADDR/v1/sys/policies/acl/$POLICY_NAME" > /dev/null
echo "✅ Policy '$POLICY_NAME' is read-only on secret/data/tenants/*"

# ── Enable AppRole auth method (idempotent) ───────────────────────────────
AUTH_METHODS=$(curl -sf -H "X-Vault-Token: $VAULT_TOKEN" "$VAULT_ADDR/v1/sys/auth" 2>/dev/null || true)
if echo "$AUTH_METHODS" | grep -q '"approle/"'; then
  echo "ℹ️  AppRole auth already enabled"
else
  curl -sf -X POST -H "X-Vault-Token: $VAULT_TOKEN" -H "Content-Type: application/json" \
    -d '{"type":"approle"}' \
    "$VAULT_ADDR/v1/sys/auth/approle" > /dev/null
  echo "✅ AppRole auth enabled"
fi

# ── Create/update the AppRole bound to the read-only policy ──────────────
echo "→ Creating/updating AppRole '$ROLE_NAME'"
curl -sf -X POST -H "X-Vault-Token: $VAULT_TOKEN" -H "Content-Type: application/json" \
  -d "{\"token_policies\":\"$POLICY_NAME\",\"token_ttl\":\"1h\",\"token_max_ttl\":\"4h\"}" \
  "$VAULT_ADDR/v1/auth/approle/role/$ROLE_NAME" > /dev/null
echo "✅ AppRole '$ROLE_NAME' created/updated"

if [ -f "$CREDS_FILE" ]; then
  echo "ℹ️  Credentials already generated previously — re-printing existing values"
  cat "$CREDS_FILE"
else
  ROLE_ID=$(curl -sf -H "X-Vault-Token: $VAULT_TOKEN" \
    "$VAULT_ADDR/v1/auth/approle/role/$ROLE_NAME/role-id" \
    | grep -o '"role_id":"[^"]*"' | cut -d'"' -f4)
  SECRET_ID=$(curl -sf -X POST -H "X-Vault-Token: $VAULT_TOKEN" \
    "$VAULT_ADDR/v1/auth/approle/role/$ROLE_NAME/secret-id" \
    | grep -o '"secret_id":"[^"]*"' | cut -d'"' -f4)
  {
    echo "OPENBAO_ROLE_ID=$ROLE_ID"
    echo "OPENBAO_SECRET_ID=$SECRET_ID"
  } > "$CREDS_FILE"
  chmod 600 "$CREDS_FILE"
  cat "$CREDS_FILE"
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✅ AppRole setup complete"
echo "   Copy the OPENBAO_ROLE_ID/OPENBAO_SECRET_ID lines above into .env"
echo "   as schema-crawler-worker's own values, then restart schema-crawler-worker."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
