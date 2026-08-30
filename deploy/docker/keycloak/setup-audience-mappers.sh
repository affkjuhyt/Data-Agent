#!/usr/bin/env bash
# Adds Audience protocol mappers to the `aeroflow-frontend` Keycloak client
# so tokens it issues contain `aeroflow-backend`, `auth-api`, and
# `release-worker` in the `aud` claim.
#
# Without these mappers, Keycloak issues tokens with `aud=["aeroflow-frontend"]`
# only. The Kong `aeroflow-jwks` plugin (configured by kong-setup.sh) rejects
# tokens whose `aud` doesn't match the per-service audience → every request
# from the frontend fails with 401 "Audience mismatch".
#
# Run after Keycloak is up + realm imported. Idempotent — re-running just
# updates the existing mappers.
#
# Usage:
#   docker compose -f docker/docker-compose.yml --profile setup run --rm \
#     keycloak-audience-setup
# OR from host (when Keycloak is exposed on :8080):
#   bash deploy/docker/keycloak/setup-audience-mappers.sh
set -euo pipefail

KC_BASE="${KEYCLOAK_URL:-http://localhost:8080}"
REALM="${KEYCLOAK_REALM:-aeroflow}"
ADMIN_USER="${KEYCLOAK_ADMIN:-admin}"
ADMIN_PASS="${KEYCLOAK_ADMIN_PASSWORD:-admin}"

# Audiences to inject — must match the `audience` config of aeroflow-jwks
# plugin in kong-setup.sh.
AUDIENCES=(aeroflow-backend auth-api release-worker)

# Client whose tokens get the audience extension.
TARGET_CLIENT="aeroflow-frontend"

echo "⏳ Waiting for Keycloak at $KC_BASE..."
for i in $(seq 1 30); do
    if curl -sf "$KC_BASE/realms/master/.well-known/openid-configuration" > /dev/null 2>&1; then
        break
    fi
    sleep 2
done

# ── 1. Get admin token ──────────────────────────────────────────────────
echo "🔑 Authenticating as $ADMIN_USER..."
TOKEN=$(curl -sf -X POST "$KC_BASE/realms/master/protocol/openid-connect/token" \
    -d "client_id=admin-cli" \
    -d "grant_type=password" \
    -d "username=$ADMIN_USER" \
    -d "password=$ADMIN_PASS" \
    | python3 -c "import sys, json; print(json.load(sys.stdin)['access_token'])")

if [ -z "$TOKEN" ]; then
    echo "❌ Failed to authenticate"
    exit 1
fi

# ── 2. Find the client ID ───────────────────────────────────────────────
CLIENT_ID=$(curl -sf -H "Authorization: Bearer $TOKEN" \
    "$KC_BASE/admin/realms/$REALM/clients?clientId=$TARGET_CLIENT" \
    | python3 -c "import sys, json; r=json.load(sys.stdin); print(r[0]['id'] if r else '')")

if [ -z "$CLIENT_ID" ]; then
    echo "❌ Client $TARGET_CLIENT not found in realm $REALM"
    exit 1
fi
echo "  → client $TARGET_CLIENT id=$CLIENT_ID"

# ── 3. List existing mappers (to detect already-added) ──────────────────
EXISTING=$(curl -sf -H "Authorization: Bearer $TOKEN" \
    "$KC_BASE/admin/realms/$REALM/clients/$CLIENT_ID/protocol-mappers/models")

# ── 4. Add audience mapper per service (skip if name already exists) ────
for aud in "${AUDIENCES[@]}"; do
    mapper_name="audience-$aud"

    already=$(echo "$EXISTING" | python3 -c "
import sys, json
existing = json.load(sys.stdin)
print('yes' if any(m['name'] == '$mapper_name' for m in existing) else 'no')
")

    if [ "$already" = "yes" ]; then
        echo "  → $mapper_name already present, skipping"
        continue
    fi

    payload=$(cat <<EOF
{
  "name": "$mapper_name",
  "protocol": "openid-connect",
  "protocolMapper": "oidc-audience-mapper",
  "consentRequired": false,
  "config": {
    "included.client.audience": "$aud",
    "id.token.claim": "false",
    "access.token.claim": "true"
  }
}
EOF
)

    curl -sf -X POST \
        -H "Authorization: Bearer $TOKEN" \
        -H "Content-Type: application/json" \
        -d "$payload" \
        "$KC_BASE/admin/realms/$REALM/clients/$CLIENT_ID/protocol-mappers/models" \
        > /dev/null
    echo "  → added $mapper_name"
done

# ── 5. Clear required actions on seeded test users ─────────────────────
# Realm-export.json seeds users with `requiredActions: ["UPDATE_PASSWORD"]`
# (or similar). For a normal UI login that's correct — the user must reset
# their password on first login. But Direct Access Grant (password flow)
# CAN'T satisfy required actions, so `curl -d 'grant_type=password'` always
# returns "Account is not fully set up". Test scripts, audience tests, and
# the tenant-isolation suite all hit this wall.
#
# This block clears required actions on the seeded test users so the
# password grant works. NOT appropriate for prod — those users should be
# forced to set a real password via the UI.
TEST_USERS=(platform-admin helios-admin ai-engineer executive-viewer)
echo
echo "🔧 Clearing required actions on seeded test users..."
for username in "${TEST_USERS[@]}"; do
    user_id=$(curl -sf -H "Authorization: Bearer $TOKEN" \
        "$KC_BASE/admin/realms/$REALM/users?username=$username&exact=true" \
        | python3 -c "import sys, json; r=json.load(sys.stdin); print(r[0]['id'] if r else '')")
    if [ -z "$user_id" ]; then
        echo "  ↷ $username not found, skipping"
        continue
    fi
    # PUT updates the user object; setting requiredActions to [] + emailVerified=true
    # + enabled=true unblocks all the actions Keycloak might raise.
    curl -sf -X PUT \
        -H "Authorization: Bearer $TOKEN" \
        -H "Content-Type: application/json" \
        -d '{"requiredActions":[],"emailVerified":true,"enabled":true}' \
        "$KC_BASE/admin/realms/$REALM/users/$user_id" \
        > /dev/null
    echo "  → cleared $username (id=$user_id)"
done

echo
echo "✅ Audience mappers configured + test users unblocked. Tokens from"
echo "   $TARGET_CLIENT will include: ${AUDIENCES[*]} in the aud claim."
echo
echo "Test with:"
echo "  TOKEN=\$(curl -s -X POST \\"
echo "    $KC_BASE/realms/$REALM/protocol/openid-connect/token \\"
echo "    -d 'client_id=$TARGET_CLIENT' \\"
echo "    -d 'grant_type=password' \\"
echo "    -d 'username=platform-admin' \\"
echo "    -d 'password=Admin@123456' | python3 -c 'import sys,json;print(json.load(sys.stdin)[\"access_token\"])')"
echo "  python3 -c \"import sys,json,base64; "
echo "    p=sys.argv[1].split('.')[1]; "
echo "    p+='='*(4-len(p)%4); "
echo "    print(json.dumps(json.loads(base64.urlsafe_b64decode(p)).get('aud'), indent=2))\" \"\$TOKEN\""
