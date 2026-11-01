#!/usr/bin/env bash
# ─── Kong Admin API Setup ──────────────────────────────────────────────
# JWT verification is done by the custom `aeroflow-jwks` plugin
# (deploy/docker/kong/plugins/aeroflow-jwks), which fetches and caches the
# Keycloak JWKS itself — no public-key registration in Kong is needed.
#
# Runs as the `setup` Job (deploy/k8s/<env>/job/setup.yaml), which mounts this
# file from the kong-scripts ConfigMap; the deploy pipeline reruns it with
# `-e apply_setup=true`. Kong's admin API is the ClusterIP Service kong-admin
# (8101), reachable only from the setup Job (allow-kong-admin).
#
# Requires: curl, python3 (baked into the runner image — see
# deploy/docker/setup/Dockerfile).

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KONG_ADMIN="${KONG_ADMIN_URL:-http://localhost:8101}"
KC_BASE="${KEYCLOAK_URL:-http://localhost:8110}"

# Upstream hostnames below default to the backend/services/<name> directory
# names (underscores). k8s Service objects can't contain underscores at all
# (DNS-1035 label rule), so dataagent-config (deploy/k8s/{dev,production}/configmap.yaml)
# sets the active service names to match.
# Without the override, Kong's upstream DNS lookup 503s with "name error"
# the moment a route actually gets proxied (JWT/CORS checks can pass fine
# before that point, which is what made this easy to miss).
API_HOST="${API_HOST:-api}"
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

# ── 0. Set demo user passwords via Keycloak admin API ─────────────────
# The realm export ships WITHOUT credential hashes (they can't be portably
# serialised across Keycloak versions). Each demo user's password comes from
# DEMO_PASSWORD_<USER> (setup-secrets <- ansible-vault vault_demo_password_*),
# never from this file; an unset one leaves that user's password alone, so a
# deploy never overwrites a password someone changed in Keycloak.
echo "🔑 Setting demo user passwords in Keycloak realm '$REALM'..."
KC_ADMIN_TOKEN=$(curl -sf -X POST "$KC_BASE/realms/master/protocol/openid-connect/token" \
  --data-urlencode "grant_type=password" \
  --data-urlencode "client_id=admin-cli" \
  --data-urlencode "username=${KEYCLOAK_ADMIN:-admin}" \
  --data-urlencode "password=${KEYCLOAK_ADMIN_PASSWORD:-admin}" \
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
      # Password history rejects ANY recent password, not only the current
      # one: if someone changed it since, this value is old and was not set.
      # Logging in is the only way to tell the two apart.
      if curl -sf -X POST "$KC_BASE/realms/$REALM/protocol/openid-connect/token" \
          --data-urlencode "grant_type=password" --data-urlencode "client_id=aeroflow-frontend" \
          --data-urlencode "username=$USERNAME" --data-urlencode "password=$PASSWORD" >/dev/null; then
        echo "  ℹ️  $USERNAME password already set (unchanged) — ok"
      else
        echo "  ⚠️  $USERNAME: configured password is in Keycloak's password history but is not the current one — not changed"
      fi
    else
      echo "  ⚠️  $USERNAME password set failed (HTTP $status)"
    fi
  }

  for pair in \
      "platform-admin:DEMO_PASSWORD_PLATFORM_ADMIN" \
      "helios-admin:DEMO_PASSWORD_HELIOS_ADMIN" \
      "helios-engineer:DEMO_PASSWORD_HELIOS_ENGINEER" \
      "helios-viewer:DEMO_PASSWORD_HELIOS_VIEWER" \
      "ai-engineer:DEMO_PASSWORD_AI_ENGINEER" \
      "executive-viewer:DEMO_PASSWORD_EXECUTIVE_VIEWER"; do
    username="${pair%%:*}"; var="${pair#*:}"
    if [ -n "${!var:-}" ]; then
      set_password "$username" "${!var}"
    else
      echo "  ℹ️  $var not set — leaving $username's password as it is"
    fi
  done
fi

# ── 1. Python runner (used for JSON-encoding TLS cert material below) ──
# JWKS fetching / key verification is done by the aeroflow-jwks plugin (§4);
# nothing here talks to Keycloak's certs endpoint.
PYTHON=$(command -v python3 2>/dev/null || command -v python 2>/dev/null || echo "")
if [ -z "$PYTHON" ] || ! "$PYTHON" -c "import sys; assert sys.version_info >= (3,6)" 2>/dev/null; then
  echo "  → Python not found on host; using docker python:3-alpine"
  # Only reached outside the setup image (which bakes python3 in): a
  # developer's host whose Docker network is named data-agent-network.
  DOCKER_NET=$(docker network ls --filter name=data-agent-network --format '{{.Name}}' | head -1)
  PY_RUN="docker run --rm -i --network ${DOCKER_NET} python:3-alpine python3"
else
  PY_RUN="$PYTHON"
fi

# Helper: extract first occurrence of a string field from JSON (no jq/Python needed)
json_field() { grep -o "\"$1\":\"[^\"]*\"" | head -1 | sed 's/.*":"//;s/"$//'; }

# ── 2. Create the V3 upstream and routes ───────────────────────────────
# The old workflow-runtime and flow-builder routes are deliberately removed
# here. Kong is stateful, so deleting their manifests alone would leave stale
# routes active after a rollout.
echo "🧹 Removing legacy workflow-runtime routes and services..."
for route in builder-route runtime-route; do
  curl -s -o /dev/null -X DELETE "$KONG_ADMIN/services/flow_builder/routes/$route" || true
  curl -s -o /dev/null -X DELETE "$KONG_ADMIN/services/workflow_runtime/routes/$route" || true
done
curl -s -o /dev/null -X DELETE "$KONG_ADMIN/services/flow_builder" || true
curl -s -o /dev/null -X DELETE "$KONG_ADMIN/services/workflow_runtime" || true

# ── 3. Create routes ───────────────────────────────────────────────────

echo "📡 Creating api service..."
curl -sf -X PUT "$KONG_ADMIN/services/api" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "api",
    "url": "http://'"$API_HOST"':8290",
    "connect_timeout": 5000,
    "read_timeout": 60000
  }' | json_field name | xargs -I{} echo "  → service: {}"

echo "🛣️  Creating api route (/v1)..."
curl -sf -X PUT "$KONG_ADMIN/services/api/routes/api-v1-route" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "api-v1-route",
    "paths": ["/v1"],
    "strip_path": false,
    "methods": ["GET","POST","PUT","PATCH","DELETE","OPTIONS"]
  }' | json_field name | xargs -I{} echo "  → route: {}"

# ── 3b. Retired auth_api service + routes ──────────────────────────────
# /api/auth (API keys, IP allowlist, Secrets Vault) was removed; V3 serves those from /v1
# (org, integration). Same as 3c below: the routes first, then the service, whose
# aeroflow-jwks plugin and the routes' ip-restriction plugins go with them.
echo "🧹 Removing the retired auth_api service..."
for route in auth_api-route auth_api-keys-route auth_api-secrets-route; do
  curl -s -o /dev/null -X DELETE "$KONG_ADMIN/services/auth_api/routes/$route" || true
done
curl -s -o /dev/null -X DELETE "$KONG_ADMIN/services/auth_api" || true

# ── 3c. Retired release_worker service + route ─────────────────────────
# Release management (/api/release) was removed. Every step here is an
# idempotent PUT, so nothing deletes what an earlier run created: drop the
# route first (Kong refuses to delete a service a route still references), then
# the service, whose plugins go with it. A 404 on a clean gateway is fine.
echo "🧹 Removing the retired release_worker service..."
curl -s -o /dev/null -X DELETE "$KONG_ADMIN/services/release_worker/routes/release-route" || true
curl -s -o /dev/null -X DELETE "$KONG_ADMIN/services/release_worker" || true

# (3d used to create an `orchestrator` service + route, and 3d-2 a
# `minio_service` one. Both are retired with the legacy runtime.)

# ── 3e. Retired kb-backend service + route ─────────────────────────────
# The legacy knowledge base (/api/data, /api/knowledge, /api/pipeline, /api/fleet) was
# removed (KB-08); the V3 screens use /v1. Same as 3c: the route first, then the service,
# whose plugins (size limit, aeroflow-jwks, rate limiting) and the route's CORS go with them.
echo "🧹 Removing the retired kb-backend service..."
curl -s -o /dev/null -X DELETE "$KONG_ADMIN/services/kb-backend/routes/kb-backend-route" || true
curl -s -o /dev/null -X DELETE "$KONG_ADMIN/services/kb-backend" || true


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
# when KC_HOSTNAME_STRICT=false). Browser clients hit http://localhost:8110
# so tokens carry iss=http://localhost:8110/realms/<realm>. Override via
# KEYCLOAK_PUBLIC_URL if your deploy uses a different external URL.
KC_PUBLIC="${KEYCLOAK_PUBLIC_URL:-http://localhost:8110}"
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
# from the same value in .env. Empty: the plugin omits the header and upstreams
# refuse X-Kong-Verified (401) unless they run with GATEWAY_KEY_OPTIONAL=true.
KONG_GATEWAY_KEY="${GATEWAY_KEY:-${KB_GATEWAY_KEY:-}}"
if [ -z "$KONG_GATEWAY_KEY" ]; then
  echo "⚠️  GATEWAY_KEY is empty — upstreams will refuse Kong traffic unless they set GATEWAY_KEY_OPTIONAL=true."
fi
# aeroflow-jwks' `gateway_key` field, when present, must be non-empty (schema:
# len_min=1) — so omit the key entirely rather than send "" when unset.
GATEWAY_KEY_JSON_FIELD=""
if [ -n "$KONG_GATEWAY_KEY" ]; then
  GATEWAY_KEY_JSON_FIELD=",\"gateway_key\":\"$KONG_GATEWAY_KEY\""
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
  echo "🔐 Enabling aeroflow-jwks plugin on api (aud=dataagent-api)..."
  upsert_plugin "$KONG_ADMIN/services/api/plugins" \
    "{\"name\":\"aeroflow-jwks\",\"config\":{\"jwks_uri\":\"$JWKS_URI\",\"issuer\":\"$ISSUER\",\"audience\":\"dataagent-api\",\"jwks_refresh_interval\":$KONG_REFRESH,\"grace_period\":$KONG_GRACE,\"hide_credentials\":false$GATEWAY_KEY_JSON_FIELD}}" \
    "aeroflow-jwks"

fi

# ── 5. IP restriction ─────────────────────────────────────────────────
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
  '{"name":"cors","config":{"origins":["http://localhost:3000","http://localhost:8150","http://localhost:30280","http://10.10.55.101:30280"],"methods":["GET","POST","PUT","PATCH","DELETE","OPTIONS"],"headers":["Content-Type","Authorization","X-Data-Space","X-Tenant-ID","X-Conversation-Id","Accept","X-Role","Idempotency-Key","X-Upload-Filename","Last-Event-ID","If-Match"],"exposed_headers":["X-Conversation-Id","X-Kong-Request-Id"],"credentials":false,"max_age":3600,"preflight_continue":false}}' \
  "cors" "global"

# ── 9. Correlation ID (tracing) ────────────────────────────────────────
echo "📝 Enabling correlation-id..."
upsert_plugin "$KONG_ADMIN/plugins" \
  '{"name":"correlation-id","config":{"header_name":"X-Request-ID","generator":"uuid#counter","echo_downstream":true}}' \
  "correlation-id" "global"

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

  # Attach client cert to the active V3 backend services
  for svc in api; do
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
echo "  Admin :  in-network only (kong-admin:8001)"
echo ""
echo "  JWT:    RS256, issuer=$ISSUER"
echo "  Routes: /v1/* -> api:8290"
echo "  CORS:   localhost:3000, localhost:8150"
MTLS_STATUS="NOT configured (run deploy/docker/certs/gen-certs.sh first)"
[ -f "$CERT_FILE" ] && MTLS_STATUS="Configured (Kong presents client cert)"
echo "  mTLS:   $MTLS_STATUS"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
