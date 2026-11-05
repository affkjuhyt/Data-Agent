#!/usr/bin/env bash
# ConfigMaps built from files that live in deploy/docker/. Those files stay where
# they are — there is one copy in the repo —
# so these have to be created imperatively rather than committed as manifests.
#
#   bash deploy/k8s/create-configmaps.sh
#
# Re-runnable: each is applied through `--dry-run=client -o yaml | kubectl apply`,
# so an existing ConfigMap is updated rather than rejected.
#
# `envFrom`/volume mounts do not pick up a ConfigMap change on a running pod:
#   kubectl -n <ns> rollout restart deployment/<name>
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/../.."

K=deploy/docker
DB=dataagent-database
SVC=dataagent-service

cm() {  # cm <namespace> <name> <--from-file args...>
  local ns=$1 name=$2; shift 2
  kubectl -n "$ns" create configmap "$name" "$@" \
    --dry-run=client -o yaml | kubectl apply -f -
}

cm "$DB" postgres-initdb \
  --from-file=01-create-dbs.sql=$K/sql/create-extra-dbs.sql \
  --from-file=03-app-schema.sql=$K/sql/init.sql

# db-init runs in the SERVICE namespace even though the SQL targets Postgres —
# a ConfigMap has to live where the pod that mounts it runs.
cm "$SVC" db-init-sql \
  --from-file=01-grants.sql=$K/sql/grant-roles.sql

# GEN-04: demo data only where SEED_DEMO=1 (the deploy passes seed_demo): the demo
# directory, DS-AW and its agents (applied by db-init from the optional
# db-init-demo-sql), DS-AW's documents (put in da-raw-docs by minio-init) and the
# demo users of the realm. Without it they are removed, so an install that once
# had them does not keep loading them; the Jobs mount these ConfigMaps optional.
DEMO_CONFIGMAPS=(db-init-demo-sql adventureworks-business-documents adventureworks-product-documents)
REALM=(
  --from-file=aeroflow-realm.json=$K/keycloak/realm-export.json
)
if [ "${SEED_DEMO:-0}" = 1 ]; then
  cm "$SVC" db-init-demo-sql \
    --from-file=03-v3-demo-seed.sql=$K/sql/v3-demo-seed.sql \
    --from-file=04-v3-adventureworks-seed.sql=$K/sql/v3-adventureworks-seed.sql \
    --from-file=06-v3-adventureworks-context.sql=$K/sql/v3-adventureworks-context.sql \
    --from-file=06-v3-adventureworks-documents.sql=$K/sql/v3-adventureworks-documents.sql \
    --from-file=07-v3-agent-seed.sql=$K/sql/v3-agent-seed.sql \
    --from-file=08-v3-screen-audit-seed.sql=$K/sql/v3-screen-audit-seed.sql
  cm "$SVC" adventureworks-business-documents --from-file=$K/adventureworks-docs/business/
  cm "$SVC" adventureworks-product-documents --from-file=$K/adventureworks-docs/product/
  # Keycloak imports <realm>-users-<n>.json beside the realm, on a fresh realm only.
  REALM+=(
    --from-file=aeroflow-users-0.json=$K/keycloak/demo/aeroflow-users-0.json
  )
else
  kubectl -n "$SVC" delete configmap "${DEMO_CONFIGMAPS[@]}" --ignore-not-found
fi

cm "$SVC" keycloak-realm "${REALM[@]}"
cm "$SVC" keycloak-scripts --from-file=$K/keycloak/setup-audience-mappers.sh
cm "$SVC" kong-scripts     --from-file=$K/kong/kong-setup.sh
cm "$SVC" litellm-config   --from-file=config.yaml=$K/litellm/config.yaml
cm "$SVC" prometheus-config --from-file=$K/monitoring/prometheus.yml

