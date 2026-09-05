#!/usr/bin/env bash
# Root cause (found empirically, not from a doc): connections from k3s pods
# to this host's own published Docker ports (10.10.55.101:8001-8004 — the
# vllm/TEI containers) succeed unpredictably — sometimes the very next
# request to the exact same pod/label/ServiceAccount combination that just
# worked fails with "connection refused", and vice versa. That flakiness
# pattern (not a deterministic block, not tied to any pod property tested:
# image, labels, ServiceAccount all ruled out) is the signature of Docker's
# userland-proxy (`docker-proxy`, one userspace process per published port)
# misbehaving under traffic arriving via an overlay/CNI path rather than a
# plain host NIC — a known class of issue, not specific to this cluster.
#
# Fix: tell the Docker daemon to publish ports via pure iptables DNAT
# instead (no userspace process in the path). Requires restarting the
# Docker daemon, which restarts every container it manages — including
# vllm-fast, vllm-main, bge-m3, bge-reranker (a GPU model reload, likely the
# slowest part) — run this when a short interruption to those is OK.
#
#   bash deploy/k8s/fix-docker-userland-proxy.sh
set -euo pipefail

DAEMON_JSON=/etc/docker/daemon.json
BACKUP="$DAEMON_JSON.bak.$(date +%s)"

echo "Backing up $DAEMON_JSON -> $BACKUP"
sudo cp "$DAEMON_JSON" "$BACKUP"

NEW_JSON=$(python3 - "$DAEMON_JSON" <<'PYEOF'
import json, sys
with open(sys.argv[1]) as f:
    cfg = json.load(f)
cfg["userland-proxy"] = False
print(json.dumps(cfg, indent=4))
PYEOF
)
echo "$NEW_JSON" | sudo tee "$DAEMON_JSON" > /dev/null
echo "wrote $DAEMON_JSON:"
cat "$DAEMON_JSON"

echo
echo "Restarting Docker (every container restarts, including vllm/bge/vllm-fast/vllm-main)..."
sudo systemctl restart docker

echo "Waiting for GPU containers to come back..."
for i in $(seq 1 60); do
  up=$(docker ps --filter "name=vllm-main" --filter "name=vllm-fast" --filter "name=bge-m3" --filter "name=bge-reranker" --format '{{.Names}}' | wc -l)
  [ "$up" -eq 4 ] && break
  sleep 2
done
docker ps --format 'table {{.Names}}\t{{.Status}}' | grep -E "vllm|bge"

echo
echo "Verifying from a throwaway pod in the cluster (run a few times — the whole"
echo "point of the old bug was that it was intermittent, so one success isn't proof):"
export KUBECONFIG="${KUBECONFIG:-$HOME/.kube/config}"
for i in 1 2 3 4 5; do
  kubectl -n dataagent-service run useprox-check-$i --rm -i --restart=Never --image=curlimages/curl:latest \
    --overrides='{"spec":{"securityContext":{"runAsNonRoot":true,"runAsUser":100,"runAsGroup":101,"seccompProfile":{"type":"RuntimeDefault"}},"containers":[{"name":"c","image":"curlimages/curl:latest","command":["curl","-s","-m","5","-o","/dev/null","-w","attempt '"$i"': %{http_code}\n","http://10.10.55.101:8003/health"],"securityContext":{"allowPrivilegeEscalation":false,"capabilities":{"drop":["ALL"]}}}]}}' \
    -- true 2>&1 | grep -v "recorded in container\|command prompt\|pod \""
done
