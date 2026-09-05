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

Config that lives as files in `deploy/docker/` is mounted from ConfigMaps built
out of those same files, so there is one copy of each:

```bash
K=deploy/docker
DB=dataagent-database
SVC=dataagent-service

kubectl -n $DB create configmap postgres-initdb \
  --from-file=01-create-dbs.sql=$K/sql/create-extra-dbs.sql \
  --from-file=03-app-schema.sql=$K/sql/init.sql

# db-init runs in the SERVICE namespace even though it talks to Postgres
kubectl -n $SVC create configmap db-init-sql \
  --from-file=01-grants.sql=$K/sql/grant-runtime-writer.sql \
  --from-file=02-kb-seed.sql=$K/sql/kb-seed.sql

kubectl -n $SVC create configmap seed-sql \
  --from-file=seed.sql=backend/services/workflow_runtime/migrations/seed.sql
kubectl -n $SVC create configmap keycloak-realm \
  --from-file=$K/keycloak/realm-export.json
kubectl -n $SVC create configmap keycloak-scripts \
  --from-file=$K/keycloak/setup-audience-mappers.sh
kubectl -n $SVC create configmap kong-scripts \
  --from-file=$K/kong/kong-setup.sh
kubectl -n $SVC create configmap litellm-config \
  --from-file=config.yaml=$K/litellm/config.yaml
kubectl -n $SVC create configmap prometheus-config \
  --from-file=$K/monitoring/prometheus.yml

# `--from-file=<dir>` is NOT recursive, and monitoring/grafana/provisioning/
# holds only subdirectories — so Grafana needs one ConfigMap per directory.
kubectl -n $SVC create configmap grafana-datasources \
  --from-file=$K/monitoring/grafana/provisioning/datasources/
kubectl -n $SVC create configmap grafana-dashboards-provider \
  --from-file=$K/monitoring/grafana/provisioning/dashboards/
kubectl -n $SVC create configmap grafana-dashboards \
  --from-file=$K/monitoring/grafana/dashboards/
```

Each ConfigMap is namespaced into whichever namespace the pod that mounts it
runs in — `db-init-sql` follows the `db-init` Job into `dataagent-service`, not
the database namespace where the SQL is applied. A test asserts every mounted
ConfigMap is one of these or is declared inline in a manifest.

## Apply

Order matters once, on a fresh cluster.

```bash
kubectl apply -f deploy/k8s/namespace.yaml
kubectl apply -f deploy/k8s/configmap.yaml -f deploy/k8s/rbac.yaml
kubectl apply -f deploy/k8s/networkpolicy-service.yaml               -f deploy/k8s/networkpolicy-database.yaml
# ... create the Secrets and the file-backed ConfigMaps above ...
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
A test fails the build if that flag is ever flipped in the manifest. Replace
`REGISTRY/` with your registry; images are named `dataagent/<service_name>`,
built from `backend/services/<name>/Dockerfile` (plus `backend/alembic/`,
`model/eval/`, `frontend/Dockerfile.dev`). `deploy/docker/kong/Dockerfile` is the
only image built under `deploy/` — everything else pulls a public image directly.

A Job's pod template is immutable, so re-applying a completed Job fails. Delete
it first: `kubectl -n dataagent-service delete job migrate --ignore-not-found`.

## Ports

Every workload listens on an `8xxx` port. For each one the **containerPort, the
Service `port` and its `targetPort` are the same number** — there is nothing to
translate when reading a manifest or a URL.

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
| auth-api | service | 8200 | **8200** | Dockerfile `--port` (unchanged) |
| release-worker | service | 8100 | **8210** | Dockerfile `--port` |
| kb-backend | service | 8000 | **8220** | Dockerfile `--port` |
| model-service | service | 8001 | **8230** | Dockerfile `--port`, `MODEL_SERVICE_PORT` |
| workflow-runtime | service | 8001 | **8240** | Dockerfile `--port` |
| flow-builder | service | 8002 | **8250** | Dockerfile `--port` |
| sandbox-executor | service | 8500 | **8260** | Dockerfile `--port` |
| llm-exporter | service | 9200 | **8270** | `LISTEN_PORT` |
| frontend | service | 3000 | **8280** | vite `--port` |
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
