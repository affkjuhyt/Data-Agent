#!/bin/sh
# Bootstrap AI provider keys into OpenBao KV v2.
# Runs once after openbao-setup completes — idempotent (safe to re-run).
# Replace placeholder values with real keys before deploying to production.
# NOTE: Infra credentials (PostgreSQL, Kafka, Keycloak, MinIO) are NOT stored
# here — those services read directly from docker-compose env vars.

set -eu

VAULT_ADDR="${VAULT_ADDR:-http://openbao:8200}"
TOKEN_FILE="/openbao/data/.root-token"

# Read root token from file written by openbao-init
if [ -f "$TOKEN_FILE" ]; then
  VAULT_TOKEN=$(cat "$TOKEN_FILE")
else
  VAULT_TOKEN="${VAULT_TOKEN:-aeroflow-dev-token}"
fi

AI="tenants/ai-keys"   # AI provider keys path

put() {
  PATH_=$1; shift
  curl -sf -X POST \
    -H "X-Vault-Token: $VAULT_TOKEN" \
    -H "Content-Type: application/json" \
    -d "$1" \
    "$VAULT_ADDR/v1/secret/data/$PATH_" > /dev/null
  echo "  ✅ $PATH_"
}

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "📦 Bootstrapping AI provider keys into OpenBao"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── AI Provider Keys — replace placeholders with real keys in production ──
echo "→ AI Provider keys"
put "$AI/OPENAI_API_KEY"     '{"data":{"value":"sk-placeholder-replace-me","note":"OpenAI API key — replace with real key"}}'
put "$AI/ANTHROPIC_API_KEY"  '{"data":{"value":"sk-ant-placeholder-replace-me","note":"Anthropic API key — replace with real key"}}'
put "$AI/AZURE_OPENAI_KEY"   '{"data":{"value":"placeholder-replace-me","note":"Azure OpenAI key — replace with real key"}}'

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✅ Bootstrap complete — AI keys at secret/data/$AI/*"
echo "   UI: $VAULT_ADDR/ui  → Secrets Engines → secret → data/tenants/ai-keys"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
