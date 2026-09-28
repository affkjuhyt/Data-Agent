#!/usr/bin/env bash
# Init/unseal OpenBao and mint the workflow_runtime AppRole, then patch the
# three OPENBAO_* lines in deploy/k8s/secrets.env in place.
#
# The manual counterpart of deploy/ansible/roles/k8s_openbao (the deploy path)
# — same steps, addressed at the openbao Service's ClusterIP, and the result
# lands in secrets.env.
# There is no k8s Job for this on purpose: it would need RBAC to write
# Secrets, and deploy/k8s/README.md's identity model gives every workload
# (openbao excepted, for a narrower reason) zero API-server access. This is a
# control-plane step, run from the operator's machine — which is what
# deploy/ansible/k8s.yml calls it from.
#
#   bash deploy/k8s/bootstrap-openbao.sh
set -euo pipefail
cd "$(dirname "$0")"

SECRETS_ENV="${SECRETS_ENV:-secrets.env}"
KEYS_FILE="${KEYS_FILE:-.openbao-keys.json}"
NS=dataagent-database

[[ -f "$SECRETS_ENV" ]] || { echo "run gen-secrets-env.sh first" >&2; exit 1; }

kubectl -n "$NS" rollout status statefulset/openbao --timeout=180s

CLUSTER_IP=$(kubectl -n "$NS" get svc openbao -o jsonpath='{.spec.clusterIP}')
BAO_URL="http://$CLUSTER_IP:8120"

health() { curl -sf "$BAO_URL/v1/sys/health" -o /dev/null -w '%{http_code}' 2>/dev/null || echo 000; }
for i in $(seq 1 30); do
  code=$(health)
  [[ "$code" != "000" ]] && break
  echo "waiting for openbao ($i/30)"; sleep 2
done

json() { curl -sf "$@"; }

if [[ ! -f "$KEYS_FILE" ]]; then
  initialized=$(curl -s "$BAO_URL/v1/sys/init" | python3 -c 'import sys,json;print(json.load(sys.stdin)["initialized"])')
  if [[ "$initialized" == "True" ]]; then
    echo "OpenBao is already initialised but $KEYS_FILE is missing — refusing to touch it." >&2
    echo "Restore the keys file, or delete the openbao PVC to reset (non-production only)." >&2
    exit 1
  fi
  json -X POST "$BAO_URL/v1/sys/init" -d '{"secret_shares":1,"secret_threshold":1}' > "$KEYS_FILE"
  chmod 600 "$KEYS_FILE"
  echo "wrote $KEYS_FILE — back this up; losing it means losing the vault"
fi

ROOT_TOKEN=$(python3 -c 'import json;print(json.load(open("'"$KEYS_FILE"'"))["root_token"])')
UNSEAL_KEY=$(python3 -c 'import json;print(json.load(open("'"$KEYS_FILE"'"))["keys_base64"][0])')

sealed=$(curl -s "$BAO_URL/v1/sys/seal-status" | python3 -c 'import sys,json;print(json.load(sys.stdin)["sealed"])')
if [[ "$sealed" == "True" ]]; then
  json -X POST "$BAO_URL/v1/sys/unseal" -d "{\"key\":\"$UNSEAL_KEY\"}" > /dev/null
  echo "unsealed"
fi

AUTH_HDR=(-H "X-Vault-Token: $ROOT_TOKEN")

mounts=$(json "${AUTH_HDR[@]}" "$BAO_URL/v1/sys/mounts")
echo "$mounts" | python3 -c 'import sys,json; d=json.load(sys.stdin); exit(0 if "secret/" in d else 1)' \
  || json "${AUTH_HDR[@]}" -X POST "$BAO_URL/v1/sys/mounts/secret" -d '{"type":"kv","options":{"version":"2"}}' > /dev/null
echo "$mounts" | python3 -c 'import sys,json; d=json.load(sys.stdin); exit(0 if "transit/" in d else 1)' \
  || json "${AUTH_HDR[@]}" -X POST "$BAO_URL/v1/sys/mounts/transit" -d '{"type":"transit"}' > /dev/null

json "${AUTH_HDR[@]}" -X POST "$BAO_URL/v1/sys/policies/acl/workflow_runtime-read-policy" \
  -d '{"policy":"path \"secret/data/tenants/*\" { capabilities = [\"read\"] }"}' > /dev/null

auth=$(json "${AUTH_HDR[@]}" "$BAO_URL/v1/sys/auth")
echo "$auth" | python3 -c 'import sys,json; d=json.load(sys.stdin); exit(0 if "approle/" in d else 1)' \
  || json "${AUTH_HDR[@]}" -X POST "$BAO_URL/v1/sys/auth/approle" -d '{"type":"approle"}' > /dev/null

json "${AUTH_HDR[@]}" -X POST "$BAO_URL/v1/auth/approle/role/workflow_runtime" \
  -d '{"token_policies":"workflow_runtime-read-policy","token_ttl":"1h","token_max_ttl":"4h"}' > /dev/null

ROLE_ID=$(json "${AUTH_HDR[@]}" "$BAO_URL/v1/auth/approle/role/workflow_runtime/role-id" \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["data"]["role_id"])')
SECRET_ID=$(json "${AUTH_HDR[@]}" -X POST "$BAO_URL/v1/auth/approle/role/workflow_runtime/secret-id" \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["data"]["secret_id"])')

sed -i \
  -e "s#^OPENBAO_TOKEN=.*#OPENBAO_TOKEN=$ROOT_TOKEN#" \
  -e "s#^OPENBAO_ROLE_ID=.*#OPENBAO_ROLE_ID=$ROLE_ID#" \
  -e "s#^OPENBAO_SECRET_ID=.*#OPENBAO_SECRET_ID=$SECRET_ID#" \
  "$SECRETS_ENV"

echo "OpenBao ready: KV v2 (secret/), transit/, AppRole workflow_runtime."
echo "OPENBAO_* written to $SECRETS_ENV — re-run create-secrets.sh, then:"
echo "  kubectl -n dataagent-service rollout restart deployment/workflow-runtime deployment/auth-api"
