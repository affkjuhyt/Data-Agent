# Kubernetes manifests

One workload per file, in three tiers.

```
deploy/k8s/
  namespace.yaml               dataagent-service + dataagent-database (restricted PSA)
  configmap.yaml               dataagent-config, one copy per namespace
  rbac.yaml                    the single RBAC exception (OpenBao)
  networkpolicy-service.yaml   deny-all + the service-tier allow matrix
  networkpolicy-database.yaml  deny-all + who may reach each store
  database/                    StatefulSets for the stores (11)
  service/                     Deployments (23)
  job/                         Jobs + the backup CronJob + the restore job (10)
```

## Namespaces

Two, both labelled `app.kubernetes.io/part-of: dataagent`:

| Namespace | Holds |
| --- | --- |
| `dataagent-database` | Postgres, Mongo, Redis, Qdrant, Neo4j, Kafka, MinIO, ClickHouse, Elasticsearch, OpenBao, PgBouncer, and the restore job |
| `dataagent-service` | the gateway, identity, application, worker and observability Deployments, and every Job except the restore |

Both carry a `name:` label as well, because the NetworkPolicies select namespaces
by it.

Splitting namespaces breaks short-name DNS: `postgres:8300` resolves only inside
the namespace that owns it. **Every in-cluster hostname is therefore an FQDN** —
`postgres.dataagent-database.svc.cluster.local:8300`, and so on for Kafka's
advertised listeners and Neo4j's advertised bolt address. A test fails the build
if a short name creeps back in.

## Network isolation

Each namespace starts from `default-deny-all` — an empty `podSelector` with
`policyTypes: [Ingress, Egress]` and no rules, so **nothing** may talk to
anything, including pods in the same namespace. Traffic is then re-opened one
flow at a time:

| Policy | Effect |
| --- | --- |
| `default-deny-all` | denies all ingress and egress, in-namespace and cross-namespace |
| `allow-dns` | egress to `kube-system` on 53 — without it no name resolves at all |
| `allow-egress-to-platform` | egress may reach only the two `dataagent-*` namespaces |
| `allow-<store>` (database ns) | ingress to one store, from the named service-namespace apps, on that store's port |
| `allow-<service>` (service ns) | ingress to one service, from the named callers only |
| `allow-kong-admin` | port 8101 only from `auth-api`, `release-worker`, `setup` |
| `allow-ingress-controller-to-kong` | the only ingress from outside the platform |
| `allow-egress-remote-llm` | `10.10.55.101` on 8002–8004, from the four workloads that use the GPU box |

Because egress is coarse (platform-only) and ingress is per-caller, a flow has to
be allowed at both ends to work. Adding a service means adding its ingress
policy — it will not talk to anything until you do.

## Identity

**Every workload runs under its own ServiceAccount**, named after it, with
`automountServiceAccountToken: false` on both the account and the pod. No
RoleBinding is created for any of them, so the account can call essentially
nothing on the API server. That is the default and it is deliberate.

`openbao` is the one exception. It calls the API server for the Kubernetes auth
method, so it mounts its token and holds exactly three verbs:

| Scope | Resource | Verb |
| --- | --- | --- |
| Role (`dataagent-database`) | `serviceaccounts` | `get` |
| Role (`dataagent-database`) | `serviceaccounts/token` | `create` |
| ClusterRole | `authentication.k8s.io/tokenreviews` | `create` |

`tokenreviews` is cluster-scoped, so that one binding has to be a
ClusterRoleBinding; there is no namespaced form. A test asserts OpenBao is the
only bound account and that the rule set never grows past those three or gains a
wildcard.

## Pod security

The namespace enforces the **restricted** Pod Security Standard, so a pod that
violates it is rejected at admission rather than degraded. Every pod in this
directory therefore sets:

| Setting | Value |
| --- | --- |
| `runAsNonRoot` | `true`, with an explicit numeric `runAsUser` |
| `seccompProfile` | `RuntimeDefault` |
| `fsGroup` / `fsGroupChangePolicy` | matching gid / `OnRootMismatch` |
| `automountServiceAccountToken` | `false` everywhere except `openbao` (see Identity) |
| `allowPrivilegeEscalation` | `false` |
| `readOnlyRootFilesystem` | `true` — writable paths are `emptyDir` or a PVC |
| `capabilities.drop` | `["ALL"]` |

No pod uses `hostNetwork`, `hostPID`, `hostIPC`, a `hostPath` volume, or
`privileged`. `tests/unit/test_k8s_manifests.py` asserts all of it, plus every
field against the API server's own OpenAPI models.

`sandbox-executor` runs user-submitted code and additionally sets
`runtimeClassName: gvisor`. The cluster needs a `RuntimeClass` named `gvisor`;
drop the field if you have not installed it, and read
`docs/Sandbox_SchemaCrawler_Implementation.md` §8.7 before you do.

## Configuration and secrets

Nothing is set inline on a container. Each workload reads exactly three sources,
in this order — later ones override earlier:

```yaml
envFrom:
  - configMapRef: { name: dataagent-config }    # shared endpoints
  - configMapRef: { name: <workload>-env }      # this workload's settings
  - secretRef:    { name: <workload>-secrets }  # only the keys it reads
```

`<workload>-env` is committed next to the workload in the same file. `dataagent-config`
lives in [`configmap.yaml`](configmap.yaml), once per namespace.

A ConfigMap change does **not** reach a running pod through `envFrom` — restart it:
`kubectl -n <ns> rollout restart deployment/<name>`.

### Secrets are scoped per workload

There is no shared Secret mounted everywhere. Each workload gets a
`<workload>-secrets` holding only the keys its code actually reads, under the
names the code expects — so `kb-backend` gets four keys and never sees
`GRAFANA_ADMIN_PASSWORD` or the Snowflake crawler password.

`GATEWAY_KEY` and `SERVICE_AUTH_SECRET` go to the five services that import
`libs.shared.auth` (which imports `service_identity`): `auth-api`,
`flow-builder`, `kb-backend`, `release-worker`, `workflow-runtime`. Nothing else
needs them, and both fail *silently* when absent — an unset `GATEWAY_KEY` makes
`X-Kong-Verified` trusted on its own, and a mismatched `SERVICE_AUTH_SECRET`
makes `kb_search` return no chunks. Check those two first if retrieval goes quiet.

Fill one env file and run the generated script; it creates all 33 Secrets in the
right namespaces with the right subsets:

```bash
cp deploy/k8s/secrets.env.example secrets.env   # fill in every value
set -a; . ./secrets.env; set +a
bash deploy/k8s/create-secrets.sh
```

[`create-secrets.sh`](create-secrets.sh) is generated from the same table the
manifests use, so it cannot drift from what the pods reference.

Kafka's JAAS file is a separate Secret — six passwords that must never sit in an
image layer:

```bash
sed -e "s/REPLACE_WITH_KAFKA_ADMIN_PASSWORD/$KAFKA_ADMIN_PASSWORD/g" \
    -e "s/REPLACE_WITH_AUDIT_CONSUMER_PASSWORD/$AUDIT_CONSUMER_KAFKA_PASSWORD/g" \
    -e "s/REPLACE_WITH_RELEASE_WORKER_PASSWORD/$RELEASE_WORKER_KAFKA_PASSWORD/g" \
    -e "s/REPLACE_WITH_WORKFLOW_RUNTIME_PASSWORD/$WORKFLOW_RUNTIME_KAFKA_PASSWORD/g" \
    -e "s/REPLACE_WITH_MEMORY_CONSUMER_PASSWORD/$MEMORY_CONSUMER_KAFKA_PASSWORD/g" \
    -e "s/REPLACE_WITH_SCHEMA_CRAWLER_WORKER_PASSWORD/$SCHEMA_CRAWLER_WORKER_KAFKA_PASSWORD/g" \
    deploy/docker/kafka/kafka-broker-jaas.config.example > /tmp/jaas.conf
kubectl -n dataagent-database create secret generic kafka-jaas \
  --from-file=kafka-broker-jaas.config=/tmp/jaas.conf
rm -f /tmp/jaas.conf
```

Pods stay in `CreateContainerConfigError` until their Secret exists, which is the
correct failure.

## File-backed ConfigMaps

Some config is a *file*, not a set of env vars — SQL, a Keycloak realm export, the
Kong setup script, Prometheus and Grafana provisioning. Those files live once, in
`deploy/docker/`, where they are also read directly by anyone running the script
by hand. Rather than copy them into YAML, they are turned into ConfigMaps and
mounted as volumes:

```bash
bash deploy/k8s/create-configmaps.sh
```

[`create-configmaps.sh`](create-configmaps.sh) is re-runnable — each ConfigMap goes
through `--dry-run=client -o yaml | kubectl apply`, so it updates rather than
fails on a second run. Three tests keep it honest against the manifests: every
mounted ConfigMap must be created by it (or declared inline), it must not create
anything nothing mounts, and each must land in the namespace of the pod that
mounts it.

Two of those are easy to get wrong:

- **`db-init-sql` belongs to `dataagent-service`**, not the database namespace —
  the SQL targets Postgres, but a ConfigMap has to live where the *pod* runs, and
  the `db-init` Job runs in the service namespace.
- **`--from-file=<dir>` is not recursive.** `monitoring/grafana/provisioning/`
  contains only subdirectories, so Grafana takes three ConfigMaps
  (`grafana-datasources`, `grafana-dashboards-provider`, `grafana-dashboards`),
  not one.

Neither `envFrom` nor a volume mount picks up a ConfigMap change on a running
pod. After editing one of these files, re-run the script and restart:
`kubectl -n <ns> rollout restart deployment/<name>`.

## Apply

Order matters once, on a fresh cluster.

```bash
kubectl apply -f deploy/k8s/namespace.yaml
kubectl apply -f deploy/k8s/configmap.yaml -f deploy/k8s/rbac.yaml
kubectl apply -f deploy/k8s/networkpolicy-service.yaml \
              -f deploy/k8s/networkpolicy-database.yaml
bash deploy/k8s/create-secrets.sh      # needs secrets.env sourced
bash deploy/k8s/create-configmaps.sh
kubectl apply -f deploy/k8s/database/
kubectl -n dataagent-database rollout status statefulset/postgres

kubectl apply -f deploy/k8s/job/kong-migration.yaml -f deploy/k8s/job/migrate.yaml
kubectl -n dataagent-service wait --for=condition=complete job/migrate --timeout=10m
kubectl apply -f deploy/k8s/job/db-init.yaml -f deploy/k8s/job/minio-init.yaml
kubectl -n dataagent-service wait --for=condition=complete job/db-init --timeout=5m

kubectl apply -f deploy/k8s/service/
kubectl apply -f deploy/k8s/job/setup.yaml          # Kong routes + Keycloak mappers
kubectl apply -f deploy/k8s/job/postgres-backup.yaml
```

`postgres-restore` drops a database, so it ships `suspend: true` with
`backoffLimit: 0` — applying it creates the Job but never starts it. Unsuspend
deliberately when you mean to restore:
`kubectl -n dataagent-database patch job postgres-restore -p '{"spec":{"suspend":false}}'`.
A test fails the build if that flag is ever flipped in the manifest. Our images
are named `dataagent/<service_name>:latest`, which resolves against a node's
local image store; prefix a registry at apply time if you push somewhere. They
are built from `backend/services/<name>/Dockerfile` (plus `backend/alembic/`,
`model/eval/`, `frontend/Dockerfile.dev`). `deploy/docker/kong/Dockerfile` is the
only image built under `deploy/` — everything else pulls a public image directly.

A Job's pod template is immutable, so re-applying a completed Job fails. Delete
it first: `kubectl -n dataagent-service delete job migrate --ignore-not-found`.

## Redeploying after a code or migration change

This is the loop for pushing a code change to an already-running cluster — not
the fresh-cluster bootstrap above. Local dev points `docker`/`kubectl` at a
single-node cluster with its own registry at `localhost:5000`; substitute your
real registry if this is remote.

**One service changed — the common case:**

```bash
# Context is the repo root for every backend service and for migrate; frontend
# is the one exception (its Dockerfile.dev expects `frontend/` as context).
docker build -f backend/services/<name>/Dockerfile -t localhost:5000/dataagent/<name>:latest .
docker push localhost:5000/dataagent/<name>:latest

kubectl -n dataagent-service rollout restart deployment/<name>
kubectl -n dataagent-service rollout status deployment/<name> --timeout=120s
```

`<name>` is the image name from `kustomization.yaml`'s `images:` list, which is
not always the directory name — `backend/services/kb_ingestion_worker/` builds
`kb-ingestion-worker` (hyphen), `backend/services/workflow_runtime/` builds
`workflow_runtime` (underscore). Check that list rather than guessing from the
path. `imagePullPolicy: Always` is set on every workload, so a `:latest` push
plus a rollout restart is enough — no need to bump a tag for local iteration.

**A new Alembic migration:**

```bash
docker build -f backend/alembic/Dockerfile -t localhost:5000/dataagent/migrate:latest .
docker push localhost:5000/dataagent/migrate:latest

kubectl -n dataagent-service delete job migrate --ignore-not-found   # immutable pod template — see above
kubectl apply -k .                                                   # NOT -f deploy/k8s/job/migrate.yaml directly:
                                                                       # the raw file still has the REGISTRY/ placeholder
kubectl -n dataagent-service wait --for=condition=complete job/migrate --timeout=5m
kubectl -n dataagent-service logs -l job-name=migrate --tail=50
```

Before writing a new revision file, verify there is exactly one head — a
hand-picked hex ID can collide with one already used elsewhere in
`backend/alembic/versions/` (this has happened; Alembic doesn't catch it until
`upgrade heads` sees two files claiming the same revision):

```bash
grep -rl "revision.*=.*['\"]<your-new-id>['\"]" backend/alembic/versions/   # expect: only your new file
```

`kubectl apply -k .` re-applies the *entire* kustomization, not just the
migrate Job — on a cluster that already has data, this routinely errors on
unrelated resources it wants to reconcile (a PVC `storage` request kubernetes
won't let shrink, a StatefulSet field that's immutable post-creation). Those
errors are expected noise, not a sign the migrate Job failed — check `job/migrate`
itself (`get pods`, `logs`) rather than the apply command's exit code.

**Frontend:**

```bash
docker build -f frontend/Dockerfile.dev -t localhost:5000/dataagent/frontend:latest frontend
docker push localhost:5000/dataagent/frontend:latest
kubectl -n dataagent-service rollout restart deployment/frontend
kubectl -n dataagent-service rollout status deployment/frontend --timeout=90s
```

Run `cd frontend && npx tsc --noEmit` first — it must run from `frontend/`
(no `tsconfig.json` at the repo root, so running it from there silently exits
0 with no output, which reads exactly like "no errors").

## Ports

Every workload listens on an `8xxx` port. For each one the **containerPort, the
Service `port` and its `targetPort` are the same number** — there is nothing to
translate when reading a manifest or a URL.

For the images we build the port appears exactly once, in the manifest's
`command`: the Dockerfiles carry no `EXPOSE` and no `CMD`.

**`8000–8099` is reserved and must stay unallocated.** It belongs to the LLM
tier: the GPU server already serves vLLM on `:8002`, TEI embed on `:8003` and
TEI rerank on `:8004`, and in-cluster LLM workloads will take the rest of the
band. Nothing in `deploy/k8s/` may use it.

| Band | Range | Tier |
| --- | --- | --- |
| LLM | `8000–8099` | **reserved — unallocated** |
| Gateway & identity | `8100–8199` | Kong, Keycloak, OpenBao |
| Application | `8200–8299` | our own services |
| Databases | `8300–8399` | stores |
| Observability | `8400–8499` | metrics, traces, logs |
| Model gateway | `8500–8599` | LiteLLM |

### Allocation

`Was` is the image's own default that the manifest overrides.

| Service | Tier | Was | Now | How it is set |
| --- | --- | ---: | ---: | --- |
| kong (proxy) | service | 8000 | **8100** | `KONG_PROXY_LISTEN` |
| kong-admin | service | 8001 | **8101** | `KONG_ADMIN_LISTEN` |
| keycloak | service | 8080 | **8110** | `KC_HTTP_PORT` |
| keycloak (health) | service | 9000 | **8111** | `KC_HTTP_MANAGEMENT_PORT` |
| openbao | database | 8200 | **8120** | `config.hcl` listener |
| auth-api | service | 8200 | **8200** | manifest `command` (unchanged) |
| release-worker | service | 8100 | **8210** | manifest `command` |
| kb-backend | service | 8000 | **8220** | manifest `command` |
| model-service | service | 8001 | **8230** | manifest `command` |
| workflow-runtime | service | 8001 | **8240** | manifest `command` |
| flow-builder | service | 8002 | **8250** | manifest `command` |
| sandbox-executor | service | 8500 | **8260** | manifest `command` |
| llm-exporter | service | 9200 | **8270** | manifest `command` |
| frontend | service | 3000 | **8280** | manifest `command` (vite `--port`) |
| postgres | database | 5432 | **8300** | `PGPORT` |
| mongo | database | 27017 | **8310** | `mongod --port` |
| redis | database | 6379 | **8320** | `redis.conf` |
| qdrant (http) | database | 6333 | **8330** | `QDRANT__SERVICE__HTTP_PORT` |
| qdrant (grpc) | database | 6334 | **8331** | `QDRANT__SERVICE__GRPC_PORT` |
| neo4j (http) | database | 7474 | **8340** | `NEO4J_server_http_listen__address` |
| neo4j (bolt) | database | 7687 | **8341** | `NEO4J_server_bolt_listen__address` |
| kafka (SASL) | database | 9092 | **8350** | `KAFKA_LISTENERS` |
| kafka (controller) | database | 9093 | **8351** | `KAFKA_LISTENERS` |
| kafka (internal) | database | 9094 | **8352** | `KAFKA_LISTENERS` |
| minio (api) | database | 9000 | **8360** | `server --address` |
| minio (console) | database | 9001 | **8361** | `--console-address` |
| clickhouse (http) | database | 8123 | **8370** | `--http_port` |
| clickhouse (native) | database | 9000 | **8371** | `--tcp_port` |
| elasticsearch | database | 9200 | **8380** | `http.port` |
| pgbouncer | database | 6432 | **8390** | `LISTEN_PORT` |
| prometheus | service | 9090 | **8400** | `--web.listen-address` |
| grafana | service | 3000 | **8410** | `GF_SERVER_HTTP_PORT` |
| langfuse | service | 3000 | **8420** | `PORT` |
| langfuse-worker | service | 3030 | **8421** | `PORT` (no Service — queue consumer) |
| jaeger (ui) | service | 16686 | **8430** | `QUERY_HTTP_SERVER_HOST_PORT` |
| jaeger (otlp) | service | 4317 | **8431** | `COLLECTOR_OTLP_GRPC_HOST_PORT` |
| kibana | service | 5601 | **8440** | `SERVER_PORT` |
| litellm | service | 4000 | **8500** | `--port` |

### Consistency

Every URL in `dataagent-config` and in the inline `env` of each manifest uses the
numbers above — `postgres:8300`, `redis://redis:8320`, `bolt://neo4j:8341`,
`kafka:8350` (SASL) / `kafka:8352` (plaintext internal), `http://minio:8360`,
`http://litellm:8500`, `http://jaeger:8431`, and so on.

Service DNS names are DNS-1123, so an underscore is not legal in a hostname:
`model-service`, `workflow-runtime`, `kb-backend`, `auth-api`, `flow-builder`,
`sandbox-executor`, `release-worker`, `llm-exporter`. `env` in the manifest wins
over whatever default the image ships, so the underscored defaults compiled into
the Python services never apply in-cluster.

`tests/unit/test_k8s_manifests.py` asserts the range, that no two Services share
a port, that `8000–8099` stays empty, and that `port == targetPort ==
containerPort` for every workload.

## Ingress

Not included — it is cluster-specific. Point one at `kong:8100`; that is the only
service meant to receive outside traffic. `kong-admin:8101` is a separate
ClusterIP Service and must never be exposed.

## Not ported from compose

| Compose service | Why |
| --- | --- |
| `sandbox-dockerd` | Rootless Docker-in-Docker needs `privileged`, unconfined seccomp/AppArmor and `/dev/fuse` — it cannot exist in a restricted namespace. `runtimeClassName: gvisor` on `sandbox-executor` is the cluster-native replacement. |
| `filebeat` | Reads `/var/log/containers` via `hostPath` as root. Node log collection is a cluster add-on, not an application workload. |
| `postgres-test` | A pytest fixture, never deployed. |
| `overlay-*.yml` | Compose-only patches; `overlay-kong-admin` is the `kong-admin` Service here. |
