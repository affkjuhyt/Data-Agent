#!/usr/bin/env bash
# ─── Kong Admin API Setup ──────────────────────────────────────────────
# Uses Kong built-in JWT plugin (community edition)
# JWT verification: fetch RSA public key from Keycloak JWKS, register in Kong
#
# Run after stack is healthy:
#   bash infra/kong/kong-setup.sh
#
# Requires: curl, jq

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KONG_ADMIN="${KONG_ADMIN_URL:-http://localhost:8001}"
KC_BASE="${KEYCLOAK_URL:-http://localhost:8080}"
REALM="${KEYCLOAK_REALM:-aeroflow}"

# ── Wait for Kong ──────────────────────────────────────────────────────
echo "⏳ Waiting for Kong Admin API at $KONG_ADMIN ..."
for i in $(seq 1 30); do
  if curl -sf "$KONG_ADMIN/status" > /dev/null 2>&1; then break; fi
  echo "  attempt $i/30 — retrying in 5s"
  sleep 5
done
curl -sf "$KONG_ADMIN/status" > /dev/null || { echo "❌ Kong not reachable"; exit 1; }
echo "✅ Kong ready"

# ── Wait for Keycloak ──────────────────────────────────────────────────
echo "⏳ Waiting for Keycloak at $KC_BASE ..."
for i in $(seq 1 30); do
  if curl -sf "$KC_BASE/realms/$REALM/.well-known/openid-configuration" > /dev/null 2>&1; then break; fi
  echo "  attempt $i/30 — retrying in 5s"
  sleep 5
done
echo "✅ Keycloak ready"

# ── 1. Fetch RSA public key from Keycloak JWKS ─────────────────────────
echo "🔑 Fetching JWKS from Keycloak..."
JWKS_URL="$KC_BASE/realms/$REALM/protocol/openid-connect/certs"

# Detect Python (host) or fall back to a temporary Docker container
PYTHON=$(command -v python3 2>/dev/null || command -v python 2>/dev/null || echo "")
if [ -z "$PYTHON" ] || ! "$PYTHON" -c "import sys; assert sys.version_info >= (3,6)" 2>/dev/null; then
  echo "  → Python not found on host; using docker python:3-alpine"
  # Find the compose network (prefixed with project name, e.g. data-agentt_aeroflow-net)
  DOCKER_NET=$(docker network ls --filter name=aeroflow-net --format '{{.Name}}' | head -1)
  PY_RUN="docker run --rm -i --network ${DOCKER_NET} python:3-alpine python3"
  JWKS_HOST="http://keycloak-lb:8080"   # HAProxy LB fronting keycloak-node1 + node2
else
  PY_RUN="$PYTHON"
  JWKS_HOST="$KC_BASE"
fi

# Generate PEM from JWKS — fetched directly inside the container if needed
PEM_KEY=$($PY_RUN -c "
import base64, json, sys
try:
    import urllib.request
    with urllib.request.urlopen('${JWKS_HOST}/realms/${REALM}/protocol/openid-connect/certs') as r:
        data = json.load(r)
except Exception:
    data = json.load(sys.stdin)

rsa_keys = [k for k in data['keys'] if k['kty'] == 'RSA']
sig_keys  = [k for k in rsa_keys if k.get('use') == 'sig']
rsa_key   = sig_keys[-1] if sig_keys else rsa_keys[-1]

print('kid=' + rsa_key['kid'], file=sys.stderr)

def b64url_to_int(s):
    s += '=' * (-len(s) % 4)
    return int.from_bytes(base64.urlsafe_b64decode(s), 'big')

n = b64url_to_int(rsa_key['n'])
e = b64url_to_int(rsa_key['e'])

def enc_len(l):
    return bytes([l]) if l < 128 else bytes([0x81, l]) if l < 256 else bytes([0x82, l >> 8, l & 0xFF])

def enc_int(n):
    b = n.to_bytes((n.bit_length() + 7) // 8, 'big')
    if b[0] & 0x80: b = b'\x00' + b
    return b'\x02' + enc_len(len(b)) + b

rsa_pub   = b'\x30' + enc_len(len(enc_int(n) + enc_int(e))) + enc_int(n) + enc_int(e)
oid       = b'\x30\x0d\x06\x09\x2a\x86\x48\x86\xf7\x0d\x01\x01\x01\x05\x00'
bitstring = b'\x03' + enc_len(len(rsa_pub) + 1) + b'\x00' + rsa_pub
spki      = b'\x30' + enc_len(len(oid) + len(bitstring)) + oid + bitstring

b64 = base64.b64encode(spki).decode()
pem = '-----BEGIN PUBLIC KEY-----\n' + '\n'.join(b64[i:i+64] for i in range(0, len(b64), 64)) + '\n-----END PUBLIC KEY-----'
print(pem)
" 2>&1 | grep -v "^kid=" || true)

# Re-run cleanly to get just the PEM (stderr filtered above may mix output)
PEM_KEY=$($PY_RUN -c "
import base64, json, sys
try:
    import urllib.request
    with urllib.request.urlopen('${JWKS_HOST}/realms/${REALM}/protocol/openid-connect/certs') as r:
        data = json.load(r)
except Exception:
    data = json.load(sys.stdin)
rsa_keys = [k for k in data['keys'] if k['kty'] == 'RSA']
sig_keys  = [k for k in rsa_keys if k.get('use') == 'sig']
rsa_key   = sig_keys[-1] if sig_keys else rsa_keys[-1]
def b64url_to_int(s):
    s += '=' * (-len(s) % 4); return int.from_bytes(base64.urlsafe_b64decode(s), 'big')
n = b64url_to_int(rsa_key['n']); e = b64url_to_int(rsa_key['e'])
def enc_len(l): return bytes([l]) if l<128 else bytes([0x81,l]) if l<256 else bytes([0x82,l>>8,l&0xFF])
def enc_int(n):
    b=n.to_bytes((n.bit_length()+7)//8,'big'); b=(b'\x00'+b if b[0]&0x80 else b)
    return b'\x02'+enc_len(len(b))+b
rsa=b'\x30'+enc_len(len(enc_int(n)+enc_int(e)))+enc_int(n)+enc_int(e)
oid=b'\x30\x0d\x06\x09\x2a\x86\x48\x86\xf7\x0d\x01\x01\x01\x05\x00'
bs=b'\x03'+enc_len(len(rsa)+1)+b'\x00'+rsa; spki=b'\x30'+enc_len(len(oid)+len(bs))+oid+bs
b64=base64.b64encode(spki).decode()
print('-----BEGIN PUBLIC KEY-----\n'+'\n'.join(b64[i:i+64]for i in range(0,len(b64),64))+'\n-----END PUBLIC KEY-----')
")

if [ -z "$PEM_KEY" ]; then
  echo "❌ Could not build PEM from Keycloak JWKS"
  exit 1
fi
echo "  → Public key extracted (${#PEM_KEY} chars)"

# Helper: extract first occurrence of a string field from JSON (no jq/Python needed)
json_field() { grep -o "\"$1\":\"[^\"]*\"" | head -1 | sed 's/.*":"//;s/"$//'; }

# ── 2. Create upstream service ─────────────────────────────────────────
echo "📡 Creating backend service..."
curl -sf -X PUT "$KONG_ADMIN/services/aeroflow-backend" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "aeroflow-backend",
    "url": "http://host.docker.internal:8888",
    "connect_timeout": 5000,
    "read_timeout": 30000
  }' | json_field name | xargs -I{} echo "  → service: {}"

# ── 3. Create route ────────────────────────────────────────────────────
echo "🛣️  Creating API route..."
curl -sf -X PUT "$KONG_ADMIN/services/aeroflow-backend/routes/api-route" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "api-route",
    "paths": ["/api"],
    "strip_path": false,
    "methods": ["GET","POST","PUT","PATCH","DELETE","OPTIONS"]
  }' | json_field name | xargs -I{} echo "  → route: {}"

# ── 3a. workflow-runtime + flow-builder services ───────────────────────
echo "📡 Creating workflow-runtime service..."
curl -sf -X PUT "$KONG_ADMIN/services/workflow-runtime" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "workflow-runtime",
    "url": "http://workflow-runtime:8001",
    "connect_timeout": 5000,
    "read_timeout": 60000
  }' | json_field name | xargs -I{} echo "  → service: {}"

curl -sf -X PUT "$KONG_ADMIN/services/workflow-runtime/routes/runtime-route" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "runtime-route",
    "paths": ["/api/conversations", "/api/traces", "/api/nodes", "/api/llm-providers", "/api/kb-connections", "/api/system-prompts"],
    "strip_path": false,
    "methods": ["GET","POST","PUT","PATCH","DELETE","OPTIONS"]
  }' | json_field name | xargs -I{} echo "  → route: {}"

echo "📡 Creating flow-builder service..."
curl -sf -X PUT "$KONG_ADMIN/services/flow-builder" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "flow-builder",
    "url": "http://flow-builder:8002",
    "connect_timeout": 5000,
    "read_timeout": 30000
  }' | json_field name | xargs -I{} echo "  → service: {}"

curl -sf -X PUT "$KONG_ADMIN/services/flow-builder/routes/builder-route" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "builder-route",
    "paths": ["/api/agents", "/api/workflows", "/api/workflow-versions"],
    "strip_path": false,
    "methods": ["GET","POST","PUT","PATCH","DELETE","OPTIONS"]
  }' | json_field name | xargs -I{} echo "  → route: {}"

# ── 3b. Create auth-api service + route ────────────────────────────────
echo "📡 Creating auth-api service..."
curl -sf -X PUT "$KONG_ADMIN/services/auth-api" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "auth-api",
    "url": "http://auth-api:8200",
    "connect_timeout": 5000,
    "read_timeout": 30000
  }' | json_field name | xargs -I{} echo "  → service: {}"

echo "🛣️  Creating auth-api route (ip-allowlist mgmt — no IP restriction)..."
curl -sf -X PUT "$KONG_ADMIN/services/auth-api/routes/auth-api-route" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "auth-api-route",
    "paths": ["/api/auth"],
    "strip_path": false,
    "methods": ["GET","POST","PUT","PATCH","DELETE","OPTIONS"]
  }' | json_field name | xargs -I{} echo "  → route: {}"

echo "🛣️  Creating auth-api-keys route (API key mgmt — IP restricted)..."
curl -sf -X PUT "$KONG_ADMIN/services/auth-api/routes/auth-api-keys-route" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "auth-api-keys-route",
    "paths": ["/api/auth/api-keys"],
    "strip_path": false,
    "methods": ["GET","POST","PUT","PATCH","DELETE","OPTIONS"]
  }' | json_field name | xargs -I{} echo "  → route: {}"

echo "🛣️  Creating auth-api-secrets route (Secrets Vault — IP restricted)..."
curl -sf -X PUT "$KONG_ADMIN/services/auth-api/routes/auth-api-secrets-route" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "auth-api-secrets-route",
    "paths": ["/api/auth/secrets"],
    "strip_path": false,
    "methods": ["GET","POST","PUT","PATCH","DELETE","OPTIONS"]
  }' | json_field name | xargs -I{} echo "  → route: {}"

# ── 3c. Create release-worker service + route ──────────────────────────
echo "📡 Creating release-worker service..."
curl -sf -X PUT "$KONG_ADMIN/services/release-worker" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "release-worker",
    "url": "http://release-worker:8100",
    "connect_timeout": 5000,
    "read_timeout": 60000
  }' | json_field name | xargs -I{} echo "  → service: {}"

echo "🛣️  Creating release route..."
curl -sf -X PUT "$KONG_ADMIN/services/release-worker/routes/release-route" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "release-route",
    "paths": ["/api/release"],
    "strip_path": false,
    "methods": ["GET","POST","PUT","PATCH","DELETE","OPTIONS"]
  }' | json_field name | xargs -I{} echo "  → route: {}"

# Helper: upsert a plugin scoped to a specific service/route/global endpoint.
# Fetches all plugins at the scope, finds one matching plugin_name by parsing
# JSON properly (Kong's ?name= filter is unreliable — returns all plugins).
# PATCH if found (update in-place), POST if not (create). Never deletes first.
upsert_plugin() {
  local scope_url="$1" body="$2" plugin_name="$3"
  local existing_id
  existing_id=$(curl -sf "$scope_url" | \
    python3 -c "
import sys, json
data = json.load(sys.stdin)
matches = [p['id'] for p in data.get('data', []) if p.get('name') == '$plugin_name']
print(matches[0] if matches else '')
" 2>/dev/null || true)
  if [ -n "$existing_id" ]; then
    curl -sf -X PATCH "$KONG_ADMIN/plugins/$existing_id" -H "Content-Type: application/json" -d "$body" \
      | json_field name | xargs -I{} echo "  → plugin (updated): {}"
  else
    curl -sf -X POST "$scope_url" -H "Content-Type: application/json" -d "$body" \
      | json_field name | xargs -I{} echo "  → plugin (created): {}"
  fi
}

ISSUER="$KC_BASE/realms/$REALM"
# JWKS_URI must use the Docker-internal hostname so Kong (running inside Docker) can reach Keycloak.
# KC_BASE (localhost:8080) works from the host but not from inside the Kong container.
KONG_KC_HOST="http://aeroflow-keycloak-lb:8080"
JWKS_URI="$KONG_KC_HOST/realms/$REALM/protocol/openid-connect/certs"

# ── 4. aeroflow-jwks plugin — JWKS-based RS256 verify + header inject ──
# Replaces jwt built-in plugin + pre-function + jwks-refresher service.
# Uses kong.cache with 300s TTL; invalidates and refetches on kid change.
echo "🔐 Enabling aeroflow-jwks plugin on aeroflow-backend (JWKS RS256 + 300s cache)..."
upsert_plugin "$KONG_ADMIN/services/aeroflow-backend/plugins" \
  "{\"name\":\"aeroflow-jwks\",\"config\":{\"jwks_uri\":\"$JWKS_URI\",\"issuer\":\"$ISSUER\",\"jwks_refresh_interval\":300}}" \
  "aeroflow-jwks"

echo "🔐 Enabling aeroflow-jwks plugin on workflow-runtime..."
upsert_plugin "$KONG_ADMIN/services/workflow-runtime/plugins" \
  "{\"name\":\"aeroflow-jwks\",\"config\":{\"jwks_uri\":\"$JWKS_URI\",\"issuer\":\"$ISSUER\",\"jwks_refresh_interval\":300}}" \
  "aeroflow-jwks"

echo "🔐 Enabling aeroflow-jwks plugin on flow-builder..."
upsert_plugin "$KONG_ADMIN/services/flow-builder/plugins" \
  "{\"name\":\"aeroflow-jwks\",\"config\":{\"jwks_uri\":\"$JWKS_URI\",\"issuer\":\"$ISSUER\",\"jwks_refresh_interval\":300}}" \
  "aeroflow-jwks"

# ── 4b. aeroflow-jwks on release-worker ────────────────────────────────
echo "🔐 Enabling aeroflow-jwks plugin on release-worker..."
upsert_plugin "$KONG_ADMIN/services/release-worker/plugins" \
  "{\"name\":\"aeroflow-jwks\",\"config\":{\"jwks_uri\":\"$JWKS_URI\",\"issuer\":\"$ISSUER\",\"jwks_refresh_interval\":300}}" \
  "aeroflow-jwks"

# ── 4c. aeroflow-jwks on auth-api ──────────────────────────────────────
echo "🔐 Enabling aeroflow-jwks plugin on auth-api..."
upsert_plugin "$KONG_ADMIN/services/auth-api/plugins" \
  "{\"name\":\"aeroflow-jwks\",\"config\":{\"jwks_uri\":\"$JWKS_URI\",\"issuer\":\"$ISSUER\",\"jwks_refresh_interval\":300}}" \
  "aeroflow-jwks"

# ── 5. IP restriction ─────────────────────────────────────────────────
# Applied to: aeroflow-backend (data API), release-worker, auth-api-keys-route.
# auth-api ip-allowlist routes are intentionally EXCLUDED so admin can always
# manage the allowlist regardless of the current IP policy.
# Default: allow_all — auth-api syncs Kong to the DB mode on startup.
# Switch to restricted via PUT /api/auth/ip-allowlist/config {"mode":"allowlist"}.
_IP_DEFAULT='{"name":"ip-restriction","config":{"allow":["0.0.0.0/0","::/0"]}}'

echo "🛡️  Enabling IP restriction on aeroflow-backend..."
upsert_plugin "$KONG_ADMIN/services/aeroflow-backend/plugins" "$_IP_DEFAULT" "ip-restriction"

echo "🛡️  Enabling IP restriction on workflow-runtime..."
upsert_plugin "$KONG_ADMIN/services/workflow-runtime/plugins" "$_IP_DEFAULT" "ip-restriction"

echo "🛡️  Enabling IP restriction on flow-builder..."
upsert_plugin "$KONG_ADMIN/services/flow-builder/plugins" "$_IP_DEFAULT" "ip-restriction"

echo "🛡️  Enabling IP restriction on release-worker..."
upsert_plugin "$KONG_ADMIN/services/release-worker/plugins" "$_IP_DEFAULT" "ip-restriction"

echo "🛡️  Enabling IP restriction on auth-api-keys-route..."
upsert_plugin "$KONG_ADMIN/routes/auth-api-keys-route/plugins" "$_IP_DEFAULT" "ip-restriction"

echo "🛡️  Enabling IP restriction on auth-api-secrets-route..."
upsert_plugin "$KONG_ADMIN/routes/auth-api-secrets-route/plugins" "$_IP_DEFAULT" "ip-restriction"

# Remove any leftover global ip-restriction plugin (from previous setup runs)
echo "🧹 Removing any global ip-restriction plugin..."
GLOBAL_IP_ID=$(curl -sf "$KONG_ADMIN/plugins" \
  | python3 -c "import sys,json; [print(p['id']) for p in json.load(sys.stdin).get('data',[]) if p['name']=='ip-restriction' and not p.get('service') and not p.get('route')]" 2>/dev/null || true)
if [ -n "$GLOBAL_IP_ID" ]; then
  curl -sf -X DELETE "$KONG_ADMIN/plugins/$GLOBAL_IP_ID" && echo "  → removed global plugin $GLOBAL_IP_ID"
else
  echo "  → no global ip-restriction found"
fi

# ── 8. Correlation ID (tracing) ────────────────────────────────────────
echo "📝 Enabling correlation-id..."
upsert_plugin "$KONG_ADMIN/plugins" \
  '{"name":"correlation-id","config":{"header_name":"X-Request-ID","generator":"uuid#counter","echo_downstream":true}}' \
  "correlation-id"

# ── 9. Rate limiting ───────────────────────────────────────────────────
echo "⏱️  Enabling rate-limiting on aeroflow-backend..."
upsert_plugin "$KONG_ADMIN/services/aeroflow-backend/plugins" \
  '{"name":"rate-limiting","config":{"minute":300,"policy":"local"}}' \
  "rate-limiting"

echo "⏱️  Enabling rate-limiting on workflow-runtime..."
upsert_plugin "$KONG_ADMIN/services/workflow-runtime/plugins" \
  '{"name":"rate-limiting","config":{"minute":300,"policy":"local"}}' \
  "rate-limiting"

echo "⏱️  Enabling rate-limiting on flow-builder..."
upsert_plugin "$KONG_ADMIN/services/flow-builder/plugins" \
  '{"name":"rate-limiting","config":{"minute":300,"policy":"local"}}' \
  "rate-limiting"

# ── 10. mTLS client cert (Kong → Backend) ─────────────────────────────
# If certs exist (generated by infra/certs/gen-certs.sh), register Kong
# client cert so Kong presents it on every upstream request.
CERT_FILE="$SCRIPT_DIR/../certs/kong-client.crt"
KEY_FILE="$SCRIPT_DIR/../certs/kong-client.key"
CA_FILE="$SCRIPT_DIR/../certs/ca.crt"

if [ -f "$CERT_FILE" ] && [ -f "$KEY_FILE" ] && [ -f "$CA_FILE" ]; then
  echo "🔒 Registering mTLS client cert (Kong → Backend)..."

  # Register CA cert for upstream validation
  CA_CONTENT=$(cat "$CA_FILE")
  CA_ID=$(curl -sf "$KONG_ADMIN/ca_certificates" \
    | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4 || true)
  if [ -z "$CA_ID" ]; then
    CA_ID=$(curl -sf -X POST "$KONG_ADMIN/ca_certificates" \
      -H "Content-Type: application/json" \
      -d "{\"cert\":$(echo "$CA_CONTENT" | "$PY_RUN" -c 'import json,sys;print(json.dumps(sys.stdin.read()))')}" \
      | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4)
    echo "  → CA registered: $CA_ID"
  else
    echo "  → CA already registered: $CA_ID"
  fi

  # Register Kong client cert + key
  CERT_CONTENT=$(cat "$CERT_FILE")
  KEY_CONTENT=$(cat "$KEY_FILE")
  CLIENT_CERT_ID=$(curl -sf -X POST "$KONG_ADMIN/certificates" \
    -H "Content-Type: application/json" \
    -d "{\"cert\":$(echo "$CERT_CONTENT" | "$PY_RUN" -c 'import json,sys;print(json.dumps(sys.stdin.read()))'),\"key\":$(echo "$KEY_CONTENT" | "$PY_RUN" -c 'import json,sys;print(json.dumps(sys.stdin.read()))')}" \
    | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4)

  # Attach client cert to the backend service
  curl -sf -X PATCH "$KONG_ADMIN/services/aeroflow-backend" \
    -H "Content-Type: application/json" \
    -d "{\"client_certificate\":{\"id\":\"$CLIENT_CERT_ID\"}}" > /dev/null
  echo "  → Kong client cert attached to service aeroflow-backend: $CLIENT_CERT_ID"
  echo "  → Backend must verify client cert against infra/certs/ca.crt"
else
  echo "ℹ️  mTLS certs not found — skipping client cert setup."
  echo "   Run: bash infra/certs/gen-certs.sh   to generate, then re-run this script."
fi

# ── Summary ────────────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✅ Kong configuration complete"
echo ""
echo "  Proxy :  http://localhost:8000"
echo "  Admin :  http://localhost:8001"
echo "  Konga :  http://localhost:1337"
echo ""
echo "  JWT:    RS256, issuer=$ISSUER"
echo "  Route:  /api → aeroflow-backend"
MTLS_STATUS="NOT configured (run infra/certs/gen-certs.sh first)"
[ -f "$CERT_FILE" ] && MTLS_STATUS="Configured (Kong presents client cert)"
echo "  mTLS:   $MTLS_STATUS"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
