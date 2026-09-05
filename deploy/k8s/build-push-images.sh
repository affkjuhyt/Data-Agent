#!/usr/bin/env bash
# Builds every image deploy/k8s/ references and pushes it to the local
# registry started for this single-node cluster (localhost:5000, see
# install-k3s.sh's registries.yaml mirror). Run from the repo root.
#
# Image name : Dockerfile : build context — kept exactly as deploy/k8s/*.yaml
# spells "dataagent/<name>" after REGISTRY/, since a couple of services use a
# hyphen (kb-backend, kb-ingestion-worker, platform-workers) while most use
# the underscored Python package name.
set -euo pipefail
cd "$(dirname "$0")/../.."

REGISTRY="${REGISTRY:-localhost:5000}"

declare -A IMAGES=(
  [auth_api]="backend/services/auth_api/Dockerfile|."
  [flow_builder]="backend/services/flow_builder/Dockerfile|."
  [kb-backend]="backend/services/kb_backend/Dockerfile|."
  [kb-ingestion-worker]="backend/services/kb_ingestion_worker/Dockerfile|."
  [llm_exporter]="backend/services/llm_exporter/Dockerfile|."
  [model_service]="backend/services/model_service/Dockerfile|backend/services/model_service"
  [platform-workers]="backend/services/platform_workers/Dockerfile|."
  [release_worker]="backend/services/release_worker/Dockerfile|."
  [sandbox_executor]="backend/services/sandbox_executor/Dockerfile|backend/services/sandbox_executor"
  [workflow_runtime]="backend/services/workflow_runtime/Dockerfile|."
  [migrate]="backend/alembic/Dockerfile|."
  [kong]="deploy/docker/kong/Dockerfile|."
  [keycloak]="deploy/docker/keycloak/Dockerfile|."
  [setup]="deploy/docker/setup/Dockerfile|."
  [sandbox-runner]="backend/services/sandbox_executor/sandbox_runner/Dockerfile|backend/services/sandbox_executor/sandbox_runner"
  [frontend]="frontend/Dockerfile.dev|frontend"
  [eval]="model/eval/Dockerfile|."
)

only="${1:-}"

for name in "${!IMAGES[@]}"; do
  if [[ -n "$only" && "$name" != "$only" ]]; then continue; fi
  entry="${IMAGES[$name]}"
  dockerfile="${entry%%|*}"
  context="${entry##*|}"
  tag="$REGISTRY/dataagent/$name:latest"
  echo "==> building $tag  (Dockerfile=$dockerfile context=$context)"
  docker build -f "$dockerfile" -t "$tag" "$context"
  echo "==> pushing $tag"
  docker push "$tag"
done

echo "==> registry now holds:"
curl -s "http://$REGISTRY/v2/_catalog"
echo
