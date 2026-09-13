# Data-Agent k8s dev — bring-up report

> **Historical record (2026-09-11).** Written during the first bring-up from the
> separate checkout `~/Data-Agent-k8s`. Since PR #55 the stack is deployed from
> `~/Data-Agent` with `deploy/ansible/deploy.yml`; current procedures are in
> [`docs/RUNBOOK.md`](../../../docs/RUNBOOK.md). Statements below about `up.sh`,
> `dev/` being untracked, and "nothing is committed" no longer hold.

Server `ndip` (10.10.55.101), single-node k3s v1.36, repo `~/Data-Agent-k8s`, branch `k8s/ver.0.1`.

## Result

The dev stack is fully up, deployed from a clean slate with `bash deploy/k8s/dev/up.sh`, which runs end to end without errors and can be re-run.

| | |
| --- | --- |
| Workloads | 11 StatefulSets/Deployments in `dataagent-database`, 25 in `dataagent-service`, all ready |
| Jobs | `migrate`, `kong-migration`, `db-init`, `minio-init`, `kafka-topics`, `setup`, `seed` all complete; `postgres-backup` CronJob scheduled; `postgres-restore` suspended by design |
| Sandbox | sandbox-executor keeps a warm pool of 3 runner pods in `dataagent-sandbox` |
| HTTP | 23 endpoints return 200 through their NodePorts. Kong `/` returns 404 because no route is defined at the root. `/api/auth/health` through Kong returns 401 without a JWT, so routes and auth are in place |
| Access | `dataagent-dev` can exec, port-forward and read secrets in the 3 namespaces, and nothing outside them. A shell in our containers runs as root with a writable root filesystem |

## Workflow items

| Item | Status |
| --- | --- |
| `deploy/k8s/production` = original content | Done (split already on the branch) |
| `deploy/k8s/dev` mirrors production | Done, then relaxed as below |
| No security enforced in dev | Privileged PSA on all namespaces, no `securityContext` restrictions, one `allow-all` NetworkPolicy per namespace, gvisor removed, our images run as `runAsUser: 0` |
| Fixed NodePort range | Every Service is NodePort with `nodePort = port + 22000`, in the band **30100–30599** (36 ports in use). Headless DB Services got a `<name>-nodeport` twin |
| Service accounts | Every workload runs under its own ServiceAccount with its token mounted. Added `dataagent-dev`, bound to `admin` in all 3 namespaces, with a token Secret `dataagent-dev-token` |
| Secrets in dev only | `dev/secrets.env` and `dev/.openbao-keys.json` (mode 600) exist only on the server. Nothing secret is left in `deploy/k8s/` or the repo root |
| dev in `.gitignore` | `deploy/k8s/dev/` and `/tmp/` are ignored, and `dev/` is untracked (`git rm --cached`, staged, not committed) |

## Errors found and fixed

"Both" means the bug was also in production and is fixed there too.

| # | Symptom | Cause | Fix | Scope |
| --- | --- | --- | --- | --- |
| 1 | `kong-migration` CrashLoop: `authentication exchange unsuccessful` | `KONG_PG_USER` and `KONG_PG_DATABASE` were unset, so Kong fell back to user/db `kong` | Set `admin` / `dataagent`, matching compose | Both |
| 2 | Kong `OOMKilled` (exit 137 about 3s after start) | nginx `worker_processes auto` starts 32 workers on this 32-core host, under a 512Mi limit | `KONG_NGINX_WORKER_PROCESSES: '2'` | Both |
| 3 | model-service: `invalid literal for int(): 'tcp://10.43…:8230'` | Kubernetes service-link env `MODEL_SERVICE_PORT` overwrote the app's own port variable. The same class of collision could hit Kong and Kafka, which read `KONG_*` / `KAFKA_*` | `enableServiceLinks: false` on every pod. Everything already addresses peers by FQDN | Both |
| 4 | workflow-runtime: `Could not import module "main"` | The image puts code in `/app/services/workflow_runtime` | Command `services.workflow_runtime.main:app` | Both |
| 5 | release-worker: `No module named 'libs'` | Code imports `services.release_worker.*` / `libs.shared.*`, but the Dockerfile still used the old flat layout | Dockerfile copies `libs/` and `services/`, `PYTHONPATH=/app`; command `services.release_worker.app:create_app`; image rebuilt | Both |
| 6 | kb-promote-gold/-silver, kb-conflict-resolution, kb-structured-data: `No module named aioboto3 / neo4j` | `libs/ingestion/runtime.py` builds every store client up front, and imported docling at module level even for workers without the DOCLING component | Added the missing client libs to each `requirements.txt`; docling is imported lazily in the DOCLING branch, so gold and conflict don't need torch or docling; 4 images rebuilt | Both |
| 7 | sandbox-executor could never run code | `runtimeClassName: gvisor` doesn't exist on this cluster, and `SANDBOX_BACKEND` was unset, so it fell back to the Docker-socket backend | Dev: gvisor removed. Both: `SANDBOX_BACKEND=kubernetes`, runner namespace, image and ServiceAccount set | Both (gvisor: dev only) |
| 8 | Frontend readiness 404 | The registry image came from the production `Dockerfile` (`dist/` only), but the manifest runs `npm run dev` | Rebuilt from `Dockerfile.dev`, as `build-push-images.sh` specifies | Image |
| 9 | Frontend `EMFILE` on file watch, then `OOMKilled` | The host's inotify limit; Vite pre-bundling dependencies on the first request needs more than 512Mi (it settles at about 150Mi) | Dev: `DISABLE_HMR=true` (code is baked into the image, so watching is useless), limit 2Gi / 1 CPU | Dev |
| 10 | Kibana killed by liveness during startup | Slow boot under 500m CPU | `startupProbe` (5 min) in both; dev limits 1 CPU / 1Gi (production's halved limit is deliberate and kept) | Both |
| 11 | kb-backend / KB workers: Neo4j `Unsupported authentication token, missing key scheme` | The server's `secrets.env` had `NEO4J_AUTH=neo4j/<pw>`, but `neo4j_connector.py` connects without credentials (`gen-secrets-env.sh` documents `none` for this reason) | Dev `NEO4J_AUTH=none` | Dev |
| 12 | Images would pull from Docker Hub | Manifests say `dataagent/<name>:latest`; k3s reads from `localhost:5000` | Dev images use the `localhost:5000/` prefix | Dev |
| 13 | NodePort 30111 dead | Keycloak 24 has no separate management port (that arrived in 25); health is served on 8110 | Removed port 8111 from the dev Service | Dev |
| 14 | Scripts wrote secrets outside dev; JAAS used a `/tmp` file | Hard-coded paths | `bootstrap-openbao.sh` / `gen-secrets-env.sh` read `SECRETS_ENV` / `KEYS_FILE`; `gen-kafka-jaas-secret.sh` pipes through stdin | Scripts |
| 15 | Eviction storms on a single node | 10 Deployments at 2–3 replicas plus HPAs | Dev: `replicas: 1`, HPA `minReplicas: 1` | Dev |

## Disk pressure incident

The root filesystem was already at 82% when I started. During the image rebuilds it reached 94–99%, kubelet's 5% eviction threshold (8.7 GB) triggered, and the whole stack was evicted twice. It kept looping because kubelet image GC deletes images of evicted pods, and the next reschedule re-pulled all of them at once.

Recovered by:
- pruning dangling images;
- removing Docker's local `dataagent/*` copies, each one first verified to be in the `localhost:5000` registry;
- `docker system prune -a` (you approved it), which freed 18.5 GB.

Docker now holds only the 4 images used by its 5 running containers (vllm ×2, TEI ×2, registry). Disk is now at **81% (31 GB free)**. There's no margin for heavy rebuilds: kubelet image GC starts at 85% and eviction at 95%. The largest remaining item you control is `~/data-agent-registry-backups` (32 GB).

## Still open (not blocking dev)

- **Production Neo4j auth**: `neo4j_connector.py` needs to pass credentials before `NEO4J_AUTH` can be anything but `none`.
- **Production frontend** still has a 512Mi limit with the Vite dev server, so it will OOM on first request, as dev did.
- **Qdrant client/server version mismatch** warning (client 1.19 vs server 1.13.6) in kb-backend. It's a warning only.
- **`tests/unit/test_tenant_isolation.py::test_no_service_gains_unscoped_call_sites`** fails; it fails the same way with my changes stashed. The 134 manifest tests in `test_k8s_manifests.py` pass.
- **Sandbox runner pods** keep their restricted `securityContext`; that is set in `k8s_pool.py` code, not a manifest.
- **OpenBao needs unsealing** after any restart: re-run `up.sh` or `bootstrap-openbao.sh`.

## Using it

```bash
bash deploy/k8s/dev/up.sh
kubectl -n dataagent-service get secret dataagent-dev-token -o jsonpath='{.data.token}' | base64 -d
kubectl -n dataagent-service exec -it deploy/workflow-runtime -- sh
```

| NodePort | Service | NodePort | Service |
| --- | --- | --- | --- |
| 30100 | kong proxy | 30300 | postgres |
| 30101 | kong admin | 30310 | mongo |
| 30110 | keycloak | 30320 | redis |
| 30120 | openbao | 30330/30331 | qdrant http/grpc |
| 30200 | auth-api | 30340/30341 | neo4j http/bolt |
| 30210 | release-worker | 30350–30352 | kafka |
| 30220 | kb-backend | 30360/30361 | minio api/console |
| 30230 | model-service | 30370/30371 | clickhouse |
| 30240 | workflow-runtime | 30380 | elasticsearch |
| 30250 | flow-builder | 30390 | pgbouncer |
| 30260 | sandbox-executor | 30400 | prometheus |
| 30270 | llm-exporter | 30410 | grafana |
| 30280 | frontend | 30420 | langfuse |
| 30500 | litellm | 30430/30431 | jaeger ui/otlp |
| | | 30440 | kibana |

## Housekeeping on the server

- **OpenBao was re-initialised fresh.** `tmp/openbao-keys.previous-vault.json` and `~/openbao-keys.backup.json` belong to the old vault and are obsolete.
- **`~/Data-Agent-k8s/tmp/`** (ignored) holds the run logs (`up.log`, `build*.log`) and `secrets.env.repo-root-stale`, the stale copy that used to sit in the repo root.
- **Nothing is committed.** Tracked changes are in the working tree on both the server and the local checkout, and they match.
