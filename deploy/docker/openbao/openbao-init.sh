#!/bin/sh
# One-shot: init (if needed) + unseal OpenBao, save root token to volume file.
# Uses the bao CLI (available in openbao/openbao image — no curl/wget needed).
# NOTE: bao status exits 0=unsealed, 1=error, 2=sealed — set -e is NOT used
# because exit 2 (sealed) is a normal state, not a failure.
set -u

BAO_ADDR="${BAO_ADDR:-http://openbao:8200}"
DATA_DIR="/openbao/data"
KEYS_FILE="$DATA_DIR/.init-keys.txt"
TOKEN_FILE="$DATA_DIR/.root-token"

export BAO_ADDR

# ── Wait for server to accept connections ────────────────────────────
echo "⏳ Waiting for OpenBao at $BAO_ADDR ..."
for i in $(seq 1 30); do
  if bao status > /dev/null 2>&1; then
    break  # exit 0 = unsealed + active
  elif [ $? -eq 2 ]; then
    break  # exit 2 = sealed — server is up, just needs unseal
  fi
  echo "  attempt $i/30 — retrying in 2s"; sleep 2
done
echo "✅ OpenBao responding"

# ── Initialize if needed ─────────────────────────────────────────────
INITIALIZED=$(bao status 2>/dev/null | grep '^Initialized' | awk '{print $2}')

if [ "$INITIALIZED" = "false" ]; then
  echo "🔑 Initializing (1 key share / threshold 1)..."
  bao operator init -key-shares=1 -key-threshold=1 > "$KEYS_FILE"
  chmod 600 "$KEYS_FILE"

  ROOT_TOKEN=$(grep 'Initial Root Token:' "$KEYS_FILE" | awk '{print $NF}')
  echo "$ROOT_TOKEN" > "$TOKEN_FILE"
  chmod 600 "$TOKEN_FILE"
  echo "✅ Initialized — root token saved to $TOKEN_FILE"
else
  echo "ℹ️  Already initialized"
fi

# ── Unseal if sealed ─────────────────────────────────────────────────
SEALED=$(bao status 2>/dev/null | grep '^Sealed' | awk '{print $2}')

if [ "$SEALED" = "true" ]; then
  if [ -f "$KEYS_FILE" ]; then
    UNSEAL_KEY=$(grep 'Unseal Key 1:' "$KEYS_FILE" | awk '{print $NF}')
  elif [ -f "$DATA_DIR/.init-keys.json" ]; then
    UNSEAL_KEY=$(grep -o '"keys_base64":\["[^"]*"' "$DATA_DIR/.init-keys.json" | grep -o '"[^"]*"]' | tr -d '"[]')
  else
    echo "❌ No unseal key file found — cannot unseal"; exit 1
  fi
  bao operator unseal "$UNSEAL_KEY" > /dev/null 2>&1
  echo "✅ Unsealed successfully"
else
  echo "ℹ️  Already unsealed"
fi

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✅ OpenBao init complete"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
