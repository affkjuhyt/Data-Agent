#!/bin/sh
# OpenBao one-shot setup — runs after openbao is healthy
# Enables KV v2 + Transit engines and creates the secrets-vault policy.
# Idempotent: safe to re-run.

set -eu

VAULT_ADDR="${VAULT_ADDR:-http://openbao:8200}"
# Prefer token file written by openbao-init, fall back to env var
TOKEN_FILE="/openbao/data/.root-token"
if [ -z "${VAULT_TOKEN:-}" ] && [ -f "$TOKEN_FILE" ]; then
  VAULT_TOKEN=$(cat "$TOKEN_FILE")
fi
VAULT_TOKEN="${VAULT_TOKEN:-aeroflow-dev-token}"

echo "⏳ Waiting for OpenBao at $VAULT_ADDR ..."
for i in $(seq 1 30); do
  STATUS=$(curl -sf "$VAULT_ADDR/v1/sys/health" 2>/dev/null || true)
  if echo "$STATUS" | grep -q '"initialized":true'; then break; fi
  echo "  attempt $i/30 — retrying in 3s"
  sleep 3
done
echo "✅ OpenBao ready"

# ── Enable KV v2 at secret/ (idempotent) ──────────────────────────
MOUNTS=$(curl -sf -H "X-Vault-Token: $VAULT_TOKEN" "$VAULT_ADDR/v1/sys/mounts" 2>/dev/null || true)
if echo "$MOUNTS" | grep -q '"secret/"'; then
  echo "ℹ️  KV v2 mount 'secret/' already exists"
else
  curl -sf -X POST -H "X-Vault-Token: $VAULT_TOKEN" -H "Content-Type: application/json" \
    -d '{"type":"kv","options":{"version":"2"}}' \
    "$VAULT_ADDR/v1/sys/mounts/secret" > /dev/null
  echo "✅ KV v2 enabled at secret/"
fi

# ── Enable Transit engine at transit/ (idempotent) ────────────────
if echo "$MOUNTS" | grep -q '"transit/"'; then
  echo "ℹ️  Transit mount already exists"
else
  curl -sf -X POST -H "X-Vault-Token: $VAULT_TOKEN" -H "Content-Type: application/json" \
    -d '{"type":"transit"}' \
    "$VAULT_ADDR/v1/sys/mounts/transit" > /dev/null
  echo "✅ Transit engine enabled at transit/"
fi

# ── Create secrets-vault policy (KV v2 + Transit) ─────────────────
POLICY='path \"secret/data/tenants/*\" { capabilities = [\"create\",\"read\",\"update\",\"delete\",\"list\"] } path \"secret/metadata/tenants/*\" { capabilities = [\"read\",\"list\",\"delete\"] } path \"secret/destroy/tenants/*\" { capabilities = [\"update\"] } path \"transit/keys/*\" { capabilities = [\"create\",\"read\",\"update\",\"delete\",\"list\"] } path \"transit/sign/*\" { capabilities = [\"update\"] } path \"transit/verify/*\" { capabilities = [\"update\"] } path \"transit/encrypt/*\" { capabilities = [\"update\"] } path \"transit/decrypt/*\" { capabilities = [\"update\"] } path \"transit/rotate/*\" { capabilities = [\"update\"] }'

curl -sf -X POST -H "X-Vault-Token: $VAULT_TOKEN" -H "Content-Type: application/json" \
  -d "{\"policy\":\"$POLICY\"}" \
  "$VAULT_ADDR/v1/sys/policies/acl/secrets-vault-policy" > /dev/null
echo "✅ Policy 'secrets-vault-policy' updated (KV v2 + Transit)"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✅ OpenBao setup complete"
echo "   UI      : $VAULT_ADDR/ui"
echo "   KV v2   : secret/data/tenants/{tenant_id}/{key_name}"
echo "   Transit : transit/keys/{key_name}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
