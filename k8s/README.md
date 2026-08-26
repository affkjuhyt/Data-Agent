# Kubernetes — HPA-autoscaled KB tier

Deployment + Service + **HorizontalPodAutoscaler** for the three stateless KB
tiers (see [`../KB-SCALE-10K.md`](../KB-SCALE-10K.md) §5, #7). Stateful stores
(Postgres/Redis/Qdrant/Neo4j/Kafka) are assumed to be managed services or their
own StatefulSets — not here.

| File | Tier | HPA |
|---|---|---|
| `kb-gateway.yaml` | Go edge/BFF (DDoS + per-tenant limits) | CPU 60%, 2→10 |
| `kb-backend.yaml` | Python retrieval/CRUD | CPU 60%, 3→12 |
| `model-service.yaml` | embed/rerank/LLM-proxy | CPU 70%, 2→8 |

## Apply
```bash
kubectl create secret generic kb-backend-secrets --from-literal=PG_PW=... # etc.
kubectl apply -f k8s/
kubectl get hpa            # watch replicas scale with load
```
Replace `REGISTRY/...:latest` with your images (built from the services'
Dockerfiles), and create the `model-weights` PVC.

## HPA needs the metrics-server
CPU-target HPAs require `metrics-server` in the cluster:
```bash
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
```

## GPU vs CPU (this repo runs GPU-less today)
`model-service` **auto-detects** its device (`device.py`: CUDA > Apple MPS > CPU)
and reports it at `GET /capabilities`. The same image runs on a CPU node (here) or
a GPU node pool — you only change **scheduling** in `model-service.yaml` (uncomment
the `nodeSelector`/`tolerations` + `nvidia.com/gpu` limit). On CPU, embed is the
bottleneck → scale wider (higher `maxReplicas`). On GPU, 1-2 replicas + dynamic
batching handle far more → cap lower and scale on a GPU-utilisation custom metric.

## CCU-accurate scaling (better than CPU%)
CPU% is a proxy. For true CCU, scale on **requests-per-second** via the Prometheus
Adapter (custom/external metrics) — the commented `Pods` metric in
`kb-backend.yaml` shows the shape (target ~300 rps/replica, matching the measured
per-replica throughput in KB-SCALE-10K §3).
