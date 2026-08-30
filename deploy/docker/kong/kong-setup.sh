#!/usr/bin/env bash
# ─── Kong Admin API Setup ──────────────────────────────────────────────
# Uses Kong built-in JWT plugin (community edition)
# JWT verification: fetch RSA public key from Keycloak JWKS, register in Kong
#
# Run after stack is healthy. Since Kong's admin API is locked to the
# kong-admin-net (see docs/auth-runbook.md "Kong admin hardening"), this
# script CANNOT be run from the host. Use the compose-managed runner:
#
#   docker compose -f docker/docker-compose.yml --profile setup run --rm kong-setup
#
# Or, with Phase 3 key-auth enabled:
#
#   docker compose -f docker/docker-compose.yml --profile setup run --rm \
#     -e ENABLE_ADMIN_KEY_AUTH=1 kong-setup
#
# Requires: curl, python3 (installed by the compose runner — see
# docker-compose.security.yml `kong-setup` service).

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KONG_ADMIN="${KONG_ADMIN_URL:-http://localhost:8001}"
KC_BASE="${KEYCLOAK_URL:-http://localhost:8080}"
REALM="${KEYCLOAK_REALM:-aeroflow}"
# Set SKIP_JWT=1 to skip JWT plugin registration (useful for dev with VITE_MOCK_AUTH=true)
SKIP_JWT="${SKIP_JWT:-0}"

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

# ── 0. Set dev user passwords via Keycloak admin API ──────────────────
# The realm export ships WITHOUT credential hashes (they can't be portably
# serialised across Keycloak versions). This block sets well-known dev
# passwords on every first boot. Safe to re-run — PUT is idempotent.
echo "🔑 Setting dev user passwords in Keycloak realm '$REALM'..."
KC_ADMIN_TOKEN=$(curl -sf -X POST "$KC_BASE/realms/master/protocol/openid-connect/token" \
  -d "grant_type=password&client_id=admin-cli&username=admin&password=admin" \
  | python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token',''))" 2>/dev/null || true)

if [ -z "$KC_ADMIN_TOKEN" ]; then
  echo "  ⚠️  Could not obtain Keycloak admin token — skipping password init"
else
  set_password() {
    local USERNAME="$1" PASSWORD="$2"
    local USER_ID
    USER_ID=$(curl -sf -H "Authorization: Bearer $KC_ADMIN_TOKEN" \
      "$KC_BASE/admin/realms/$REALM/users?username=$USERNAME&exact=true" \
      | python3 -c "import sys,json; u=json.load(sys.stdin); print(u[0]['id'] if u else '')" 2>/dev/null || true)
    if [ -z "$USER_ID" ]; then
      echo "  ⚠️  User '$USERNAME' not found — skipping"
      return
    fi
    # Capture body+status so we can tell a real failure from "already set". On a
    # persisted Keycloak volume the user already has this password, and the realm's
    # password-history policy rejects re-setting the same value with HTTP 400
    # (invalidPasswordHistoryMessage) — that is success, not failure.
    local resp status
    resp=$(curl -s -w '\n%{http_code}' -X PUT "$KC_BASE/admin/realms/$REALM/users/$USER_ID/reset-password" \
      -H "Authorization: Bearer $KC_ADMIN_TOKEN" \
      -H "Content-Type: application/json" \
      -d "{\"type\":\"password\",\"value\":\"$PASSWORD\",\"temporary\":false}")
    status=$(printf '%s' "$resp" | tail -n1)
    if [ "$status" = "204" ] || [ "$status" = "200" ]; then
      echo "  ✅ $USERNAME password set"
    elif printf '%s' "$resp" | grep -q "invalidPasswordHistoryMessage"; then
      echo "  ℹ️  $USERNAME password already set (unchanged) — ok"
    else
      echo "  ⚠️  $USERNAME password set failed (HTTP $status)"
    fi
  }

  set_password "helios-admin"    "H3lios@Admin01!"
  set_password "helios-engineer" "H3lios@Eng001!"
  set_password "helios-viewer"   "H3lios@View001!"
  set_password "platform-admin"  "Platform@Admin01!"
  set_password "ai-engineer"     "AiEng@001!dev"
  set_password "executive-viewer" "Exec@View001!"
fi

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

# ── 2. Create upstream services ────────────────────────────────────────
echo "📡 Creating workflow-runtime service..."
curl -sf -X PUT "$KONG_ADMIN/services/workflow-runtime" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "workflow-runtime",
    "host": "workflow-runtime",
    "port": 8001,
    "protocol": "http",
    "connect_timeout": 5000,
    "read_timeout": 60000
  }' | json_field name | xargs -I{} echo "  → service: {}"

echo "📡 Creating flow-builder service..."
curl -sf -X PUT "$KONG_ADMIN/services/flow-builder" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "flow-builder",
    "host": "flow-builder",
    "port": 8002,
    "protocol": "http",
    "connect_timeout": 5000,
    "read_timeout": 30000
  }' | json_field name | xargs -I{} echo "  → service: {}"

# ── 3. Create routes ───────────────────────────────────────────────────
echo "🛣️  Creating flow-builder route (canvas + agent CRUD)..."
curl -sf -X PUT "$KONG_ADMIN/services/flow-builder/routes/builder-route" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "builder-route",
    "paths": ["/api/agents", "/api/workflows", "/api/workflow-versions"],
    "strip_path": false,
    "methods": ["GET","POST","PUT","PATCH","DELETE","OPTIONS"]
  }' | json_field name | xargs -I{} echo "  → route: {}"

echo "🛣️  Creating workflow-runtime route (conversations + catalog + nodes)..."
curl -sf -X PUT "$KONG_ADMIN/services/workflow-runtime/routes/runtime-route" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "runtime-route",
    "paths": ["/api/conversations", "/api/traces", "/api/nodes", "/api/llm-providers", "/api/kb-connections", "/api/system-prompts", "/api/catalog", "/api/sandbox", "/api/runtime-config", "/api/db-connector", "/api/reviews", "/api/platform", "/api/billing"],
    "strip_path": false,
    "methods": ["GET","POST","PUT","PATCH","DELETE","OPTIONS"]
  }' | json_field name | xargs -I{} echo "  → route: {}"

# NOTE: the kb-backend service + its route (paths /api/data, /api/knowledge,
# /api/pipeline, /api/fleet) are created together in section 3d below. Do not
# add a second /api/data route here — two routes matching the same path leave
# the CORS plugin (attached to one route) ambiguous for browser preflights.

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

# ── 3d. Create orchestrator service + route ────────────────────────────
echo "📡 Creating orchestrator service..."
curl -sf -X PUT "$KONG_ADMIN/services/orchestrator" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "orchestrator",
    "url": "http://orchestrator:8003",
    "connect_timeout": 5000,
    "read_timeout": 300000
  }' | json_field name | xargs -I{} echo "  → service: {}"

echo "🛣️  Creating orchestrator route..."
curl -sf -X PUT "$KONG_ADMIN/services/orchestrator/routes/orchestrator-route" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "orchestrator-route",
    "paths": ["/api/orchestrate"],
    "strip_path": false,
    "methods": ["GET","POST","PUT","PATCH","DELETE","OPTIONS"]
  }' | json_field name | xargs -I{} echo "  → route: {}"

# ── 3d-2. Create minio-service service + route ─────────────────────────
# Object storage and the per-tenant audit archives. Unlike flow-builder, this
# one had no Kong service at all — it was reachable only by its published host
# port 8400, with no authentication: the archive GET read any tenant's exported
# audit log and the PUT replaced it.
echo "📡 Creating minio-service service..."
curl -sf -X PUT "$KONG_ADMIN/services/minio-service" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "minio-service",
    "host": "minio-service",
    "port": 8400,
    "protocol": "http",
    "connect_timeout": 5000,
    "read_timeout": 120000
  }' | json_field name | xargs -I{} echo "  → service: {}"

echo "🛣️  Creating minio-service route (object storage + audit archives)..."
curl -sf -X PUT "$KONG_ADMIN/services/minio-service/routes/storage-route" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "storage-route",
    "paths": ["/api/storage"],
    "strip_path": false,
    "methods": ["GET","HEAD","POST","PUT","PATCH","DELETE","OPTIONS"]
  }' | json_field name | xargs -I{} echo "  → route: {}"


# ── 3e. Create kb-backend service + route ──────────────────────────────
# kb-backend is a compose service on the shared network, so Kong reaches it by
# its service DNS name (NOT host.docker.internal like aeroflow-backend).
echo "📡 Creating kb-backend service..."
curl -sf -X PUT "$KONG_ADMIN/services/kb-backend" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "kb-backend",
    "url": "http://kb-backend:8000",
    "connect_timeout": 5000,
    "read_timeout": 60000
  }' | json_field name | xargs -I{} echo "  → service: {}"

echo "🛣️  Creating kb-backend route (KB UI data/knowledge/pipeline APIs)..."
curl -sf -X PUT "$KONG_ADMIN/services/kb-backend/routes/kb-backend-route" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "kb-backend-route",
    "paths": ["/api/data","/api/knowledge","/api/pipeline","/api/fleet"],
    "strip_path": false,
    "methods": ["GET","POST","PUT","PATCH","DELETE","OPTIONS"]
  }' | json_field name | xargs -I{} echo "  → route: {}"

# Helper: upsert a plugin scoped to a specific service/route/global endpoint.
# Fetches all plugins at the scope, finds one matching plugin_name by parsing
# JSON properly (Kong's ?name= filter is unreliable — returns all plugins).
# PATCH if found (update in-place), POST if not (create). Never deletes first.
upsert_plugin() {
  local scope_url="$1" body="$2" plugin_name="$3" scope_type="${4:-scoped}"
  local existing_id
  existing_id=$(curl -sf "$scope_url" | \
    python3 -c "
import sys, json
data = json.load(sys.stdin)
is_global = '$scope_type' == 'global'
matches = [
    p['id'] for p in data.get('data', [])
    if p.get('name') == '$plugin_name'
    and (not is_global or (not p.get('service') and not p.get('route')))
]
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

# ISSUER must match the `iss` claim Keycloak actually emits in tokens.
# Keycloak derives that from `KC_HOSTNAME_URL` (or the request Host header
# when KC_HOSTNAME_STRICT=false). Browser clients hit http://localhost:8080
# so tokens carry iss=http://localhost:8080/realms/<realm>. Override via
# KEYCLOAK_PUBLIC_URL if your deploy uses a different external URL.
KC_PUBLIC="${KEYCLOAK_PUBLIC_URL:-http://localhost:8080}"
ISSUER="$KC_PUBLIC/realms/$REALM"

# JWKS_URI is a NETWORK URL — Kong fetches keys via the in-cluster hostname,
# not the public one. Two URLs by design: public for iss validation, network
# for JWKS fetch. Mixing them means either tokens reject ("iss mismatch")
# OR Kong can't reach Keycloak.
KONG_KC_HOST="${KEYCLOAK_NETWORK_URL:-http://keycloak:8080}"
JWKS_URI="$KONG_KC_HOST/realms/$REALM/protocol/openid-connect/certs"

# ── 4. aeroflow-jwks plugin — JWKS-based RS256 verify + header inject ──
# Per-service `audience` is set so a token issued for service A cannot be
# replayed against service B. The plugin checks `aud` claim against this
# value; Keycloak must be configured (audience mappers on aeroflow-frontend
# client) to include each of these in the token's `aud`. See
# docs/auth-runbook.md "JWT audience matrix".
#
# `grace_period` (10 min) keeps the previous JWKS keys valid for that long
# after rotation — without it, every token in flight gets a 401 spike the
# moment Keycloak rolls. `jwks_refresh_interval` (5 min) is how often the
# cache TTL ticks, but on a kid miss we always lazy-refetch.

KONG_GRACE=600   # seconds; matches Keycloak default access-token TTL (15m floor)
KONG_REFRESH=300

# Injected as X-Gateway-Key on every JWT-verified request so an upstream can
# distinguish "Kong verified this" from "a caller reached my port and typed
# X-Kong-Verified: true". MUST equal the upstream's KB_GATEWAY_KEY — set both
# from the same value in .env. Empty (the default) reproduces the previous
# behaviour: the plugin omits the header and upstreams keep trusting
# X-Kong-Verified alone, which is only safe if nothing but Kong can reach them.
KONG_GATEWAY_KEY="${GATEWAY_KEY:-${KB_GATEWAY_KEY:-}}"
if [ -z "$KONG_GATEWAY_KEY" ]; then
  echo "⚠️  GATEWAY_KEY is empty — upstreams will accept X-Kong-Verified without proof it came from Kong."
fi

if [ "$SKIP_JWT" = "1" ]; then
  echo "⚠️  SKIP_JWT=1 — removing aeroflow-jwks plugins if present (dev mode)"
  curl -sf "$KONG_ADMIN/plugins?size=100" | python3 -c "
import sys, json
data = json.load(sys.stdin)
for p in data.get('data', []):
    if p.get('name') == 'aeroflow-jwks':
        print(p['id'])
" | while read -r pid; do
    curl -sf -X DELETE "$KONG_ADMIN/plugins/$pid" && echo "  → removed aeroflow-jwks plugin $pid"
  done
else
  echo "🔐 Enabling aeroflow-jwks plugin on workflow-runtime..."
  upsert_plugin "$KONG_ADMIN/services/workflow-runtime/plugins" \
    "{\"name\":\"aeroflow-jwks\",\"config\":{\"jwks_uri\":\"$JWKS_URI\",\"issuer\":\"$ISSUER\",\"audience\":\"workflow-runtime\",\"jwks_refresh_interval\":$KONG_REFRESH,\"grace_period\":$KONG_GRACE,\"gateway_key\":\"$KONG_GATEWAY_KEY\"}}" \
    "aeroflow-jwks"

  echo "🔐 Enabling aeroflow-jwks plugin on flow-builder..."
  upsert_plugin "$KONG_ADMIN/services/flow-builder/plugins" \
    "{\"name\":\"aeroflow-jwks\",\"config\":{\"jwks_uri\":\"$JWKS_URI\",\"issuer\":\"$ISSUER\",\"audience\":\"flow-builder\",\"jwks_refresh_interval\":$KONG_REFRESH,\"grace_period\":$KONG_GRACE,\"gateway_key\":\"$KONG_GATEWAY_KEY\"}}" \
    "aeroflow-jwks"

  echo "🔐 Enabling aeroflow-jwks plugin on minio-service..."
  upsert_plugin "$KONG_ADMIN/services/minio-service/plugins" \
    "{\"name\":\"aeroflow-jwks\",\"config\":{\"jwks_uri\":\"$JWKS_URI\",\"issuer\":\"$ISSUER\",\"audience\":\"minio-service\",\"jwks_refresh_interval\":$KONG_REFRESH,\"grace_period\":$KONG_GRACE,\"gateway_key\":\"$KONG_GATEWAY_KEY\"}}" \
    "aeroflow-jwks"

  # ── 4b. aeroflow-jwks on release-worker ────────────────────────────────
  echo "🔐 Enabling aeroflow-jwks plugin on release-worker (aud=release-worker)..."
  upsert_plugin "$KONG_ADMIN/services/release-worker/plugins" \
    "{\"name\":\"aeroflow-jwks\",\"config\":{\"jwks_uri\":\"$JWKS_URI\",\"issuer\":\"$ISSUER\",\"audience\":\"release-worker\",\"jwks_refresh_interval\":$KONG_REFRESH,\"grace_period\":$KONG_GRACE,\"gateway_key\":\"$KONG_GATEWAY_KEY\"}}" \
    "aeroflow-jwks"

  # ── 4c. aeroflow-jwks on auth-api ──────────────────────────────────────
  echo "🔐 Enabling aeroflow-jwks plugin on auth-api (aud=auth-api)..."
  upsert_plugin "$KONG_ADMIN/services/auth-api/plugins" \
    "{\"name\":\"aeroflow-jwks\",\"config\":{\"jwks_uri\":\"$JWKS_URI\",\"issuer\":\"$ISSUER\",\"audience\":\"auth-api\",\"jwks_refresh_interval\":$KONG_REFRESH,\"grace_period\":$KONG_GRACE,\"gateway_key\":\"$KONG_GATEWAY_KEY\"}}" \
    "aeroflow-jwks"

  # ── 4d-pre. aeroflow-jwks on orchestrator ──────────────────────────────
  echo "🔐 Enabling aeroflow-jwks plugin on orchestrator (aud=aeroflow-backend)..."
  upsert_plugin "$KONG_ADMIN/services/orchestrator/plugins" \
    "{\"name\":\"aeroflow-jwks\",\"config\":{\"jwks_uri\":\"$JWKS_URI\",\"issuer\":\"$ISSUER\",\"audience\":\"aeroflow-backend\",\"jwks_refresh_interval\":$KONG_REFRESH,\"grace_period\":$KONG_GRACE,\"gateway_key\":\"$KONG_GATEWAY_KEY\"}}" \
    "aeroflow-jwks"
fi

# ── 4d. aeroflow-jwks on kb-backend ────────────────────────────────────
# Reuse aud=aeroflow-backend so the existing audience mapper on the
# aeroflow-frontend client applies — no new Keycloak mapper needed.
echo "🔐 Enabling aeroflow-jwks plugin on kb-backend (aud=aeroflow-backend)..."
upsert_plugin "$KONG_ADMIN/services/kb-backend/plugins" \
  "{\"name\":\"aeroflow-jwks\",\"config\":{\"jwks_uri\":\"$JWKS_URI\",\"issuer\":\"$ISSUER\",\"audience\":\"aeroflow-backend\",\"jwks_refresh_interval\":$KONG_REFRESH,\"grace_period\":$KONG_GRACE,\"gateway_key\":\"$KONG_GATEWAY_KEY\"}}" \
  "aeroflow-jwks"

# ── 4e. CORS on kb-backend-route ───────────────────────────────────────
# Browser preflight OPTIONS carries no Authorization header. The cors plugin
# (priority higher than aeroflow-jwks @1010) answers the preflight before JWT
# verification runs — without it every cross-origin request 401s on preflight.
echo "🌐 Enabling CORS on kb-backend-route..."
upsert_plugin "$KONG_ADMIN/routes/kb-backend-route/plugins" \
  '{"name":"cors","config":{"origins":["http://localhost:3000","http://localhost:5173"],"methods":["GET","POST","PUT","PATCH","DELETE","OPTIONS"],"headers":["Authorization","Content-Type","X-Role","X-Request-ID"],"credentials":true,"max_age":3600}}' \
  "cors"

# ── 5. IP restriction ─────────────────────────────────────────────────
# Applied to: aeroflow-backend (data API), release-worker, auth-api-keys-route.
# auth-api ip-allowlist routes are intentionally EXCLUDED so admin can always
# manage the allowlist regardless of the current IP policy.
# Default: allow_all — auth-api syncs Kong to the DB mode on startup.
# Switch to restricted via PUT /api/auth/ip-allowlist/config {"mode":"allowlist"}.
_IP_DEFAULT='{"name":"ip-restriction","config":{"allow":["0.0.0.0/0","::/0"]}}'

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

# ── 8. CORS (browser preflight) ───────────────────────────────────────
# Global plugin — applies to all routes. Priority 2000 runs before JWT (1010)
# so OPTIONS preflight returns 200 without needing a token.
echo "🌐 Enabling global CORS plugin..."
upsert_plugin "$KONG_ADMIN/plugins" \
  '{"name":"cors","config":{"origins":["http://localhost:3000","http://localhost:5173"],"methods":["GET","POST","PUT","PATCH","DELETE","OPTIONS"],"headers":["Content-Type","Authorization","X-Tenant-ID","X-Conversation-Id","Accept","X-Role"],"exposed_headers":["X-Conversation-Id","X-Kong-Request-Id"],"credentials":false,"max_age":3600,"preflight_continue":false}}' \
  "cors" "global"

# ── 9. Correlation ID (tracing) ────────────────────────────────────────
echo "📝 Enabling correlation-id..."
upsert_plugin "$KONG_ADMIN/plugins" \
  '{"name":"correlation-id","config":{"header_name":"X-Request-ID","generator":"uuid#counter","echo_downstream":true}}' \
  "correlation-id" "global"

# ── 10. Rate limiting ──────────────────────────────────────────────────
echo "⏱️  Enabling rate-limiting on workflow-runtime..."
upsert_plugin "$KONG_ADMIN/services/workflow-runtime/plugins" \
  '{"name":"rate-limiting","config":{"minute":300,"policy":"local"}}' \
  "rate-limiting"

echo "⏱️  Enabling rate-limiting on flow-builder..."
upsert_plugin "$KONG_ADMIN/services/flow-builder/plugins" \
  '{"name":"rate-limiting","config":{"minute":300,"policy":"local"}}' \
  "rate-limiting"

# ── 10. mTLS client cert (Kong → Backend) ─────────────────────────────
# If certs exist (generated by deploy/docker/certs/gen-certs.sh), register Kong
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

  # Attach client cert to backend services
  for svc in workflow-runtime flow-builder; do
    curl -sf -X PATCH "$KONG_ADMIN/services/$svc" \
      -H "Content-Type: application/json" \
      -d "{\"client_certificate\":{\"id\":\"$CLIENT_CERT_ID\"}}" > /dev/null
    echo "  → Kong client cert attached to service $svc: $CLIENT_CERT_ID"
  done
  echo "  → Backends must verify client cert against deploy/docker/certs/ca.crt"
else
  echo "ℹ️  mTLS certs not found — skipping client cert setup."
  echo "   Run: bash deploy/docker/certs/gen-certs.sh   to generate, then re-run this script."
fi

# ── 11. Admin API key-auth (Phase 3 hardening, OPT-IN) ────────────────
# OSS Kong's admin endpoint doesn't natively accept plugins. Pattern below
# uses "Kong-fronting-Kong": expose admin through a regular Kong service +
# route, then apply key-auth on that route. Services that need to manage
# Kong call http://kong:8000/_kong_admin/<path> with `apikey: <key>` instead
# of the raw http://172.30.0.10:8001/<path>.
#
# Enable by setting ENABLE_ADMIN_KEY_AUTH=1. Disabled by default because
# services still call raw admin in this PR — flipping this on without also
# updating service env vars to use the proxied URL + apikey header will
# break startup. Phased rollout:
#
#   1. Run with the flag in dev/staging
#   2. Copy generated keys to .env (KONG_ADMIN_API_KEY_AUTH_API, ...)
#   3. Update services to send `apikey` header + use proxied URL
#   4. Verify, then flip on in prod
if [ "${ENABLE_ADMIN_KEY_AUTH:-0}" = "1" ]; then
  echo "🔒 Phase 3: registering Kong-fronting-Kong admin proxy with key-auth..."

  # Kong admin is bound to KONG_ADMIN_IP:8001 on the kong-admin-net interface.
  # From Kong's OWN container, 127.0.0.1:8001 also reaches it iff KONG_ADMIN_LISTEN
  # includes a loopback binding — currently it doesn't. So upstream the service
  # to the `kong-admin` alias, which resolves to the admin-net IP from Kong's
  # data plane without hardcoding it.
  ADMIN_UPSTREAM="${KONG_ADMIN_UPSTREAM:-http://kong-admin:8001}"

  curl -sf -X PUT "$KONG_ADMIN/services/kong-admin-proxy" \
    -H "Content-Type: application/json" \
    -d "{\"name\":\"kong-admin-proxy\",\"url\":\"$ADMIN_UPSTREAM\"}" \
    > /dev/null

  curl -sf -X PUT "$KONG_ADMIN/services/kong-admin-proxy/routes/kong-admin-route" \
    -H "Content-Type: application/json" \
    -d '{
      "name": "kong-admin-route",
      "paths": ["/_kong_admin"],
      "strip_path": true,
      "methods": ["GET","POST","PUT","PATCH","DELETE","OPTIONS","HEAD"]
    }' > /dev/null

  upsert_plugin "$KONG_ADMIN/routes/kong-admin-route/plugins" \
    '{"name":"key-auth","config":{"key_names":["apikey"],"hide_credentials":true}}' \
    "key-auth"

  # Optional: also restrict by IP to defense-in-depth even if key leaks.
  # Subnet comes from KONG_ADMIN_SUBNET (.env) so it stays in lockstep with the
  # network CIDR — no silent break if the CIDR changes.
  upsert_plugin "$KONG_ADMIN/routes/kong-admin-route/plugins" \
    "{\"name\":\"ip-restriction\",\"config\":{\"allow\":[\"${KONG_ADMIN_SUBNET:-172.30.0.0/24}\"]}}" \
    "ip-restriction"

  # Helper: idempotent consumer + key creation. Writes keys to admin-keys.env
  # for ops to copy into .env. NEVER commits keys to git — file is gitignored.
  KEYS_FILE="$SCRIPT_DIR/../certs/admin-keys.env"
  : > "$KEYS_FILE"
  chmod 600 "$KEYS_FILE"

  for consumer in auth-api release-worker jwks-refresher; do
    # Create consumer if not exists (PUT is idempotent)
    curl -sf -X PUT "$KONG_ADMIN/consumers/$consumer-admin" \
      -H "Content-Type: application/json" \
      -d "{\"username\":\"$consumer-admin\"}" > /dev/null

    # Rotate key: delete existing then create fresh. Idempotent re-runs
    # generate a new key each time — operators see the rotation.
    EXISTING_KEY_ID=$(curl -sf "$KONG_ADMIN/consumers/$consumer-admin/key-auth" \
      | python3 -c "
import sys, json
data = json.load(sys.stdin).get('data', [])
print(data[0]['id'] if data else '')" 2>/dev/null || true)
    if [ -n "$EXISTING_KEY_ID" ]; then
      curl -sf -X DELETE "$KONG_ADMIN/consumers/$consumer-admin/key-auth/$EXISTING_KEY_ID" > /dev/null || true
    fi

    NEW_KEY=$(curl -sf -X POST "$KONG_ADMIN/consumers/$consumer-admin/key-auth" \
      | python3 -c "import sys, json; print(json.load(sys.stdin)['key'])")

    # Upper-snake key var name, e.g. KONG_ADMIN_API_KEY_AUTH_API
    VAR_NAME=$(echo "KONG_ADMIN_API_KEY_${consumer}" | tr '[:lower:]-' '[:upper:]_')
    echo "${VAR_NAME}=${NEW_KEY}" >> "$KEYS_FILE"
    echo "  → ${VAR_NAME} written"
  done

  echo ""
  echo "  Admin proxy: http://kong:8000/_kong_admin/<path>"
  echo "  Header:      apikey: \$${VAR_NAME%_*}_<SERVICE>"
  echo "  Keys file:   $KEYS_FILE (chmod 600)"
  echo "  Next step:   copy keys to .env, switch services to proxied URL"
fi

# ── 12. Note on Kong worker cache ──────────────────────────────────────
# Kong's pubsub propagates plugin config to workers within a few seconds.
# Custom-plugin local caches (kong.cache entries keyed off conf values)
# usually pick up automatically because the cache key changes when config
# does. If you're hot-patching `issuer` / `audience` on a running stack
# and see stale "Issuer mismatch" / "Audience mismatch" errors longer than
# ~10s, run on the host:
#
#   docker exec dataagent-kong kong reload
#
# (The setup runner can't do this for you — bash:5 has no docker CLI and
# the runner container doesn't have access to docker.sock by design.)

# ── Summary ────────────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✅ Kong configuration complete"
echo ""
echo "  Proxy :  http://localhost:8000"
echo "  Admin :  internal only (kong-admin-net 172.30.0.10:8001)"
echo ""
echo "  JWT:    RS256, issuer=$ISSUER"
echo "  Routes: /api/agents|workflows|workflow-versions  -> flow-builder:8002"
echo "          /api/conversations|traces|nodes|catalog|platform|billing -> workflow-runtime:8001"
echo "  CORS:   localhost:3000, localhost:5173"
MTLS_STATUS="NOT configured (run deploy/docker/certs/gen-certs.sh first)"
[ -f "$CERT_FILE" ] && MTLS_STATUS="Configured (Kong presents client cert)"
echo "  mTLS:   $MTLS_STATUS"
ADMIN_AUTH_STATUS="disabled (set ENABLE_ADMIN_KEY_AUTH=1 to enable)"
[ "${ENABLE_ADMIN_KEY_AUTH:-0}" = "1" ] && ADMIN_AUTH_STATUS="enabled via /_kong_admin route"
echo "  Admin auth: $ADMIN_AUTH_STATUS"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
