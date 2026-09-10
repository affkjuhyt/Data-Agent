#!/usr/bin/env bash
# Pods in the k3s cluster (10.42.0.0/16, via flannel/cni0) cannot reach the
# plain `docker run` LLM containers (vllm-fast, vllm-main, bge-m3,
# bge-reranker) at this same host's LAN IP (10.10.55.101:8001-8004) —
# connections are refused even though the same request from the host itself
# (curl http://10.10.55.101:8003/health) works fine, rp_filter is already
# loose (2, not 1), and the NetworkPolicy egress rule (allow-egress-remote-llm)
# already permits it. That points at Docker's own DOCKER-USER iptables chain,
# which is evaluated before Docker's per-container rules and defaults to
# rejecting forwarded traffic from networks it doesn't recognise — the pod
# CIDR is exactly that.
#
# model-service, litellm, llm-exporter and kb-ingestion-worker all call out
# to 10.10.55.101 directly (see deploy/k8s/configmap.yaml's *_BASE_URL /
# REMOTE_*_URL values) — this blocks all of them.
#
# Needs root (this repo has no passwordless sudo on this host) — run once:
#   bash deploy/k8s/fix-pod-to-host-docker-networking.sh
set -euo pipefail

echo "Current DOCKER-USER chain:"
sudo iptables -L DOCKER-USER -n -v --line-numbers

sudo iptables -I DOCKER-USER 1 -s 10.42.0.0/16 -j ACCEPT
echo "Inserted: ACCEPT for pod CIDR 10.42.0.0/16"

echo
echo "Verifying from a throwaway pod in the cluster..."
export KUBECONFIG="${KUBECONFIG:-$HOME/.kube/config}"
kubectl -n dataagent-service run netcheck --rm -i --restart=Never --image=curlimages/curl:latest \
  --overrides='{"spec":{"securityContext":{"runAsNonRoot":true,"runAsUser":100,"runAsGroup":101,"seccompProfile":{"type":"RuntimeDefault"}},"containers":[{"name":"netcheck","image":"curlimages/curl:latest","command":["curl","-sf","-m","5","-o","/dev/null","-w","http_code=%{http_code}\n","http://10.10.55.101:8003/health"],"securityContext":{"allowPrivilegeEscalation":false,"capabilities":{"drop":["ALL"]}}}]}}' \
  -- true

cat <<'EOF'

If http_code=200 printed above: fixed. This rule does NOT survive a reboot
or `systemctl restart docker` (Docker rewrites DOCKER-USER's default policy
on start, though it preserves existing rules within it — re-run this script
if 10.10.55.101 becomes unreachable from pods again after either).
To persist it, add the same iptables -I DOCKER-USER line to a systemd unit
or /etc/network/if-up.d/ script that runs after Docker starts.
EOF
