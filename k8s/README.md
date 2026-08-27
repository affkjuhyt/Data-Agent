# Kubernetes manifests

Two tiers plus the operational jobs that keep a deployment recoverable.
Stateful stores (Postgres/Redis/Qdrant/Neo4j/Kafka/MinIO/OpenBao) are assumed to
be managed services or their own StatefulSets — **not here**. Point
[`config.yaml`](config.yaml) at wherever they run.

| File | What | Scaling |
| --- | --- | --- |
| `config.yaml` | Shared non-secret wiring for the runtime tier | — |
| **Runtime tier** | | |
| `orchestrator.yaml` | Multi-agent front door (`/api/orchestrate`) | CPU 65%, 2→6 |
| `workflow-runtime.yaml` | Agent execution (plan → retrieve → respond → judge) | CPU 65%, 2→8 |
| `sandbox-executor.yaml` | User-code execution ⚠ node prerequisites | fixed, pool-sized |
| **KB tier** | | |
| `kb-gateway.yaml` | Go edge/BFF (DDoS + per-tenant limits) | CPU 60%, 2→10 |
| `kb-backend.yaml` | Python retrieval/CRUD | CPU 60%, 3→12 |
| `model-service.yaml` | embed/rerank/LLM-proxy | CPU 70%, 2→8 |
| **Operations** | | |
| `migrate-job.yaml` | `alembic upgrade head`, once per deploy | — |
| `postgres-backup.yaml` | pg_dump CronJob + retention (every 6h) | — |
| `dr/postgres-restore-job.yaml` | Restore template — **not** part of the directory apply | — |

The KB-tier files inline their own env and predate `config.yaml`; the runtime
tier uses the shared ConfigMap because three services have to agree on the same
endpoints and a value repeated in three places eventually isn't.

## Secrets

Nothing here commits a Secret, not even a placeholder one — a manifest full of
dummy values is a manifest somebody applies by accident. Create it first; pods
stay in `CreateContainerConfigError` until you do, which is the correct failure.

```bash
kubectl create secret generic dataagent-secrets \
  --from-literal=DATABASE_URL='postgresql+asyncpg://admin:PW@postgres:5432/dataagent' \
  --from-literal=RUNTIME_DB_URL='postgresql+asyncpg://runtime_writer:PW@postgres:5432/dataagent' \
  --from-literal=PGPASSWORD='PW' \
  --from-literal=SERVICE_AUTH_SECRET='...' \
  --from-literal=GATEWAY_KEY='...' \
  --from-literal=LITELLM_API_KEY='...' \
  --from-literal=LANGFUSE_PUBLIC_KEY='...' \
  --from-literal=LANGFUSE_SECRET_KEY='...' \
  --from-literal=KAFKA_SASL_PASSWORD='...' \
  --from-literal=MINIO_ACCESS_KEY='...' \
  --from-literal=MINIO_SECRET_KEY='...' \
  --from-literal=OPENBAO_ROLE_ID='...' \
  --from-literal=OPENBAO_SECRET_ID='...'

# KB tier keeps its own (unchanged):
kubectl create secret generic kb-backend-secrets --from-literal=PG_PW=...
```

`SERVICE_AUTH_SECRET` must be **the same value** in orchestrator,
workflow-runtime and kb-backend: it signs the identity assertion each service
presents to the next. A mismatch does not fail loudly — `kb_search` simply
returns no chunks, by design, because it must never assert an identity it cannot
prove.

## Apply

Order matters once, on a fresh cluster: the schema has to exist before anything
serves traffic.

```bash
kubectl apply -f k8s/config.yaml
kubectl apply -f k8s/migrate-job.yaml
kubectl wait --for=condition=complete job/dataagent-migrate --timeout=10m
kubectl apply -f k8s/          # everything else; dr/ is NOT included (not recursive)
kubectl get hpa
```

Replace `REGISTRY/...:latest` with your images (built from each service's
Dockerfile; the migration image is built from [`../alembic/Dockerfile`](../alembic/Dockerfile)),
and create the `model-weights` PVC for `model-service`.

### Upgrades

A Job's pod template is immutable, so re-applying a completed migration Job
fails. Delete it first:

```bash
kubectl delete job dataagent-migrate --ignore-not-found
kubectl apply -f k8s/migrate-job.yaml
kubectl wait --for=condition=complete job/dataagent-migrate --timeout=10m
kubectl apply -f k8s/
```

You do not have to hold the rollout by hand: `orchestrator` and
`workflow-runtime` each run a `wait-for-migrations` initContainer that blocks
until the database itself reports the schema at head. That covers what the Job
cannot — a pod rescheduled mid-upgrade, or a scale-up during a migration.
`tests/unit/test_k8s_manifests.py` keeps that gate from being dropped when
someone copies a manifest for a new service.

**Backward-compatible migrations only.** During a rolling upgrade both the old
and new code run against the *new* schema. A revision that drops or renames a
column the old pods still read takes them down. Split it: add in one release,
backfill, drop in the next.

## Backup and restore

`postgres-backup.yaml` is the k8s port of the `postgres-backup` sidecar in
`infra/docker-compose.prod.yml` — pg_dump every 6h onto a PVC, pruned at 7 days.

What it does **not** port is WAL archiving, and that difference is the whole RPO
story. In compose, Postgres is in the stack and gets `archive_command` on its own
command line; here Postgres is out of scope for this directory, so continuous
archiving belongs to whatever runs it — the managed provider's PITR, or
`archive_command` on your StatefulSet pointing at object storage. Without one of
those, recovery means "restore the last dump" and up to 6h of committed writes
are gone. See [`../docs/disaster-recovery.md`](../docs/disaster-recovery.md).

Restore is [`dr/postgres-restore-job.yaml`](dr/postgres-restore-job.yaml). It
lives in a subdirectory because `kubectl apply -f k8s/` is not recursive and that
Job drops a database; it also ships `suspend: true`, so applying it does nothing
until you explicitly unsuspend. The full procedure is in its header.

## What is not here yet

The manifests cover the request path — gateway → orchestrator →
workflow-runtime → sandbox/KB. Not yet ported from `infra/docker-compose.app.yml`:
`auth-api`, `flow-builder`, `minio-service`, `release-worker`, `audit-bridge`,
`audit-consumer`, `memory-consumer`, `schema-crawler-worker`, `jwks-refresher`,
the frontend, and the Kong/Keycloak edge. A cluster running only what is in this
directory serves agent traffic but has no authoring UI and no audit pipeline.

Ingress is also not here: `orchestrator` and `workflow-runtime` are ClusterIP on
purpose (compose keeps their ports off the host for the same reason — the
gateway is the only way in). Wiring an Ingress/Gateway straight at them would
bypass the Kong routes that carry authentication.

## HPA needs the metrics-server

CPU-target HPAs require `metrics-server` in the cluster:

```bash
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
```

## Version prerequisites

* **1.29+** for `sandbox-executor` — its dockerd sidecar is a native sidecar
  (`initContainers[].restartPolicy: Always`), so it is up before the app
  container and the runner-image load.
* **1.30+** for `securityContext.appArmorProfile` on that same sidecar. On an
  older cluster use the `container.apparmor.security.beta.kubernetes.io/dockerd`
  pod annotation instead.
* A CNI that enforces NetworkPolicy (Calico, Cilium, …) for the
  `sandbox-executor-ingress` policy. On one that ignores them the object applies
  cleanly and protects nothing.

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

For the runtime tier CPU% is a worse proxy still: a turn blocked on a model call
is near-idle CPU but very much in use, which is why both runtime HPAs use a 300s
`scaleDown` window. Concurrent-turns-per-replica is the metric that actually
describes that tier.
