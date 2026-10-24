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
  --from-file=01-grants.sql=$K/sql/grant-roles.sql \
  --from-file=02-platform-seed.sql=$K/sql/platform-seed.sql \
  --from-file=03-v3-demo-seed.sql=$K/sql/v3-demo-seed.sql \
  --from-file=04-v3-credit-demo.sql=$K/sql/v3-credit-demo.sql \
  --from-file=05-v3-pilot-seed.sql=$K/sql/v3-pilot-seed.sql \
  --from-file=06-v3-banking-demo.sql=$K/sql/v3-banking-demo.sql \
  --from-file=07-v3-agent-seed.sql=$K/sql/v3-agent-seed.sql

# The pilot's policy documents (05-v3-pilot-seed.sql), put in da-raw-docs by minio-init.
cm "$SVC" pilot-documents --from-file=$K/pilot-docs/

cm "$SVC" keycloak-realm   --from-file=$K/keycloak/realm-export.json
cm "$SVC" keycloak-scripts --from-file=$K/keycloak/setup-audience-mappers.sh
cm "$SVC" kong-scripts     --from-file=$K/kong/kong-setup.sh
cm "$SVC" litellm-config   --from-file=config.yaml=$K/litellm/config.yaml
cm "$SVC" prometheus-config --from-file=$K/monitoring/prometheus.yml

# `--from-file=<dir>` is NOT recursive and monitoring/grafana/provisioning/ holds
# only subdirectories, so Grafana needs one ConfigMap per directory.
cm "$SVC" grafana-datasources \
  --from-file=$K/monitoring/grafana/provisioning/datasources/
cm "$SVC" grafana-dashboards-provider \
  --from-file=$K/monitoring/grafana/provisioning/dashboards/
cm "$SVC" grafana-dashboards \
  --from-file=$K/monitoring/grafana/dashboards/
