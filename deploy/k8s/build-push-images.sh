#!/usr/bin/env bash
# Builds the V3 image slice. Manual escape hatch; the normal
# path is `ansible-playbook deploy.yml --tags images`, which builds only what
# changed. Run from anywhere.
#
# Images are tagged exactly as deploy/k8s/*.yaml spells them --
# `dataagent/<name>:latest`, no registry -- so a build here is immediately
# usable by the manifests. Set REGISTRY to also tag and push a mirror copy:
#
#   REGISTRY=localhost:5001 bash deploy/k8s/build-push-images.sh
#
# With REGISTRY unset nothing is pushed: on a single-node cluster the image is
# already in the daemon's store, and `imagePullPolicy: IfNotPresent` finds it.
#
# Legacy service images are intentionally not listed here. Their manifests and
# source remain during the gated cutover, but they must not be pulled into a
# V3 deploy just because a shared path changed.
set -euo pipefail
cd "$(dirname "$0")/../.."

REGISTRY="${REGISTRY:-}"

declare -A IMAGES=(
  [api]="backend/services/api/Dockerfile|."
  [worker-agent]="backend/services/worker_agent/Dockerfile|."
  [worker-document]="backend/services/worker_document/Dockerfile|."
  [migrate]="backend/alembic/Dockerfile|."
  [kong]="deploy/docker/kong/Dockerfile|."
  [keycloak]="deploy/docker/keycloak/Dockerfile|."
  [frontend]="frontend/Dockerfile.dev|frontend"
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
