#!/usr/bin/env sh
# Idempotent post-start configuration for Keycloak.
# Runs as a one-shot container after keycloak-lb is healthy.
# Assigns realm-management client roles (view-users, view-events, …) to
# ALL users whose role_id attribute is "platform-admin" — covers every tenant.

set -e

KC="${KC_URL:-http://keycloak-lb:8080}"
REALM="${KC_REALM:-aeroflow}"
ADMIN_USER="${KC_ADMIN_USER:-admin}"
ADMIN_PASS="${KC_ADMIN_PASSWORD:-admin}"

echo "[keycloak-setup] Waiting for Keycloak..."
until curl -sf "${KC}/realms/master" > /dev/null; do sleep 3; done
echo "[keycloak-setup] Keycloak ready."

# ── Get master admin token ──────────────────────────────────────────────────
TOKEN=$(curl -sf -X POST "${KC}/realms/master/protocol/openid-connect/token" \
  -d "client_id=admin-cli" \
  -d "username=${ADMIN_USER}" \
  -d "password=${ADMIN_PASS}" \
  -d "grant_type=password" | sed 's/.*"access_token":"\([^"]*\)".*/\1/')

AUTH="Authorization: Bearer ${TOKEN}"

# ── Resolve realm-management client ID ─────────────────────────────────────
CLIENT_ID=$(curl -sf -H "${AUTH}" \
  "${KC}/admin/realms/${REALM}/clients?clientId=realm-management&search=false" \
  | sed 's/.*"id":"\([^"]*\)".*/\1/' | head -1)

echo "[keycloak-setup] realm-management client id: ${CLIENT_ID}"

ALL_ROLES=$(curl -sf -H "${AUTH}" \
  "${KC}/admin/realms/${REALM}/clients/${CLIENT_ID}/roles")

assign_roles_to_user() {
  local uid="$1"
  local uname="$2"
  local assigned
  assigned=$(curl -sf -H "${AUTH}" \
    "${KC}/admin/realms/${REALM}/users/${uid}/role-mappings/clients/${CLIENT_ID}")

  for ROLE_NAME in view-events view-users query-users query-groups; do
    if echo "${assigned}" | grep -q "\"${ROLE_NAME}\""; then
      echo "[keycloak-setup] ${uname}: ${ROLE_NAME} already assigned."
      continue
    fi
    ROLE_JSON=$(echo "${ALL_ROLES}" | grep -o "{[^}]*\"name\":\"${ROLE_NAME}\"[^}]*}")
    if [ -z "${ROLE_JSON}" ]; then
      echo "[keycloak-setup] WARNING: role ${ROLE_NAME} not found."
      continue
    fi
    curl -sf -X POST -H "${AUTH}" -H "Content-Type: application/json" \
      "${KC}/admin/realms/${REALM}/users/${uid}/role-mappings/clients/${CLIENT_ID}" \
      -d "[${ROLE_JSON}]"
    echo "[keycloak-setup] ${uname}: assigned ${ROLE_NAME}."
  done
}

# ── Assign roles to ALL users with role_id=platform-admin (all tenants) ─────
# Keycloak attribute search: q=role_id:platform-admin
ALL_ADMINS=$(curl -sf -H "${AUTH}" \
  "${KC}/admin/realms/${REALM}/users?q=role_id%3Aplatform-admin&max=100")

echo "${ALL_ADMINS}" | python3 -c "
import sys, json
users = json.load(sys.stdin)
for u in users:
    print(u['id'] + '|' + u['username'])
" | while IFS='|' read -r uid uname; do
  echo "[keycloak-setup] Processing platform-admin user: ${uname} (${uid})"
  assign_roles_to_user "${uid}" "${uname}"
done

# ── User Profile: enable unmanagedAttributePolicy so tenant_id/role_id persist ──
# Without this, Keycloak 22+ declarative UP drops unknown attributes on write.
PROFILE=$(curl -sf -H "${AUTH}" "${KC}/admin/realms/${REALM}/users/profile")
if echo "${PROFILE}" | grep -q '"unmanagedAttributePolicy":"ENABLED"'; then
  echo "[keycloak-setup] unmanagedAttributePolicy already ENABLED, skipping."
else
  UPDATED=$(echo "${PROFILE}" | sed 's/"unmanagedAttributePolicy":"[^"]*"/"unmanagedAttributePolicy":"ENABLED"/; t; s/^\({.*\)$/\1/' | \
    python3 -c "
import sys, json
profile = json.load(sys.stdin)
profile['unmanagedAttributePolicy'] = 'ENABLED'
# Ensure tenant_id and role_id are declared
existing = {a['name'] for a in profile.get('attributes', [])}
for attr in [
    {'name': 'tenant_id', 'displayName': 'Tenant ID', 'permissions': {'view': ['admin'], 'edit': ['admin']}},
    {'name': 'role_id',   'displayName': 'Role ID',   'permissions': {'view': ['admin'], 'edit': ['admin']}},
]:
    if attr['name'] not in existing:
        profile.setdefault('attributes', []).append(attr)
print(json.dumps(profile))
")
  curl -sf -X PUT -H "${AUTH}" -H "Content-Type: application/json" \
    "${KC}/admin/realms/${REALM}/users/profile" \
    -d "${UPDATED}" > /dev/null
  echo "[keycloak-setup] unmanagedAttributePolicy set to ENABLED, tenant_id/role_id declared."
fi

echo "[keycloak-setup] Done."
