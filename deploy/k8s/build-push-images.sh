#!/usr/bin/env bash
# Builds every image deploy/k8s/ references. Manual escape hatch; the normal
# path is `ansible-playbook deploy.yml --tags images`, which builds only what
# changed. Run from anywhere.
#
# Images are tagged exactly as deploy/k8s/*.yaml spells them --
# `dataagent/<name>:latest`, no registry -- so a build here is immediately
# usable by the manifests. Set REGISTRY to also tag and push a mirror copy:
#
#   REGISTRY=localhost:5000 bash deploy/k8s/build-push-images.sh
#
# With REGISTRY unset nothing is pushed: on a single-node cluster the image is
# already in the daemon's store, and `imagePullPolicy: IfNotPresent` finds it.
#
# Image name : Dockerfile : build context — kept exactly as deploy/k8s/*.yaml
# spells "dataagent/<name>" after REGISTRY/, since a couple of services use a
# hyphen (kb-backend, kb-worker, platform-workers) while most use
# the underscored Python package name.
set -euo pipefail
cd "$(dirname "$0")/../.."

REGISTRY="${REGISTRY:-}"

declare -A IMAGES=(
  [api]="backend/services/api/Dockerfile|."
  [auth_api]="backend/services/auth_api/Dockerfile|."
  [kb-backend]="backend/services/kb_backend/Dockerfile|."
  [indicator-api]="backend/services/indicator_api/Dockerfile|."
  [kb-worker]="deploy/docker/kb-worker/Dockerfile|."
  [model_service]="backend/services/model_service/Dockerfile|backend/services/model_service"
  [platform-workers]="backend/services/platform_workers/Dockerfile|."
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
  tag="dataagent/$name:latest"
  echo "==> building $tag  (Dockerfile=$dockerfile context=$context)"
  docker build -f "$dockerfile" -t "$tag" "$context"
  if [ -n "$REGISTRY" ]; then
    echo "==> pushing $REGISTRY/$tag"
    docker tag "$tag" "$REGISTRY/$tag"
    docker push "$REGISTRY/$tag"
  fi
done

if [ -n "$REGISTRY" ]; then
  echo "==> registry now holds:"
  curl -s "http://$REGISTRY/v2/_catalog"
  echo
fi
