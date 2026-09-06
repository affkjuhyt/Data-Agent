#!/usr/bin/env bash
# Root cause (confirmed via tcpdump): a pod's SYN to 10.10.55.101:800X
# correctly passes NetworkPolicy, DOCKER-USER and the FORWARD chain (visible
# on the host-side veth), but no SYN-ACK/RST ever comes back — the packet
# is being lost between the veth and the actual container after Docker's
# bridge/DNAT rewrite (most likely: the DNAT target's internal IP went
# stale across a container restart — each of the 4 GPU containers got a
# NEW 172.17.0.x address when `docker restart` from the userland-proxy fix
# recreated them, and something in the nat/bridge path did not fully
# recover). Both earlier fixes (DOCKER-USER accept rule, disabling
# userland-proxy) were real and worth keeping, but neither is sufficient —
# the mismatch is inside Docker's own bridge+publish plumbing.
#
# Fix: run these 4 containers with --network host instead of the default
# bridge + `-p` port-publish. Host networking binds the process directly to
# the real host network stack — no DNAT, no docker0 bridge, no
# DOCKER-USER/DOCKER/isolation chains in the path at all. A k8s pod's packet
# to 10.10.55.101:800X is then ordinary local delivery to whatever is
# listening there, the same path that already reliably works for the k8s
# API server, postgres, etc.
#
# This restarts all 4 GPU containers (fresh model load — vllm-main
# especially, an ~14B model, may take a couple of minutes). Old configs are
# printed below and this script's own header preserves them; to roll back,
# remove the --network host versions and re-run each container with the
# original `-p HOST:CONTAINER` flag shown in the comments and no --gpus/
# --network-host changes.
set -euo pipefail

MODELS_DIR=/data/models/huggingface
TEI_DIR=/home/ndip/models/tei

echo "=== stopping + removing the 4 bridge-network containers ==="
docker rm -f vllm-main vllm-fast bge-m3 bge-reranker 2>&1 || true

echo "=== starting vllm-main (was -p 8001:8000) on host network, port 8001 ==="
docker run -d --name vllm-main --network host --gpus all --restart unless-stopped \
  -v "$MODELS_DIR:/root/.cache/huggingface" \
  vllm/vllm-openai:latest \
  --model Qwen/Qwen3-14B-AWQ --served-model-name agent-main \
  --max-model-len 16384 --gpu-memory-utilization 0.55 --port 8001

echo "=== starting vllm-fast (was -p 8002:8000) on host network, port 8002 ==="
docker run -d --name vllm-fast --network host --gpus all --restart unless-stopped \
  -v "$MODELS_DIR:/root/.cache/huggingface" \
  vllm/vllm-openai:latest \
  --model Qwen/Qwen3-4B-AWQ --served-model-name agent-fast \
  --max-model-len 8192 --gpu-memory-utilization 0.15 --port 8002

echo "=== starting bge-m3 (was -p 8003:80) on host network, port 8003 ==="
docker run -d --name bge-m3 --network host --gpus all --restart unless-stopped \
  -v "$TEI_DIR:/data" -e PORT=8003 \
  ghcr.io/huggingface/text-embeddings-inference:1.9 \
  --model-id BAAI/bge-m3 --dtype float16

echo "=== starting bge-reranker (was -p 8004:80) on host network, port 8004 ==="
docker run -d --name bge-reranker --network host --gpus all --restart unless-stopped \
  -v "$TEI_DIR:/data" -e PORT=8004 \
  ghcr.io/huggingface/text-embeddings-inference:cuda-1.9 \
  --model-id BAAI/bge-reranker-v2-m3 --dtype float16

echo
echo "=== waiting for all 4 to answer /health on localhost (models loading — can take minutes) ==="
for port in 8003 8004 8001 8002; do
  echo -n "port $port: "
  for i in $(seq 1 90); do
    code=$(curl -s -m 3 -o /dev/null -w "%{http_code}" "http://localhost:$port/health" 2>/dev/null || echo 000)
    if [ "$code" = "200" ]; then echo "OK (${i}0s)"; break; fi
    sleep 10
  done
done

docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' | grep -E "vllm|bge|NAMES"

echo
echo "=== verifying from a pod in the cluster ==="
export KUBECONFIG="${KUBECONFIG:-$HOME/.kube/config}"
for port in 8001 8002 8003 8004; do
  kubectl -n dataagent-service run hostnet-check-$port --rm -i --restart=Never --image=curlimages/curl:latest \
    --overrides='{"metadata":{"labels":{"app":"model-service"}},"spec":{"securityContext":{"runAsNonRoot":true,"runAsUser":100,"runAsGroup":101,"seccompProfile":{"type":"RuntimeDefault"}},"containers":[{"name":"c","image":"curlimages/curl:latest","command":["curl","-s","-m","5","-o","/dev/null","-w","port '"$port"': %{http_code}\n","http://10.10.55.101:'"$port"'/health"],"securityContext":{"allowPrivilegeEscalation":false,"capabilities":{"drop":["ALL"]}}}]}}' \
    -- true 2>&1 | grep -v "recorded in container\|command prompt\|^pod "
done
