# Kubernetes manifests

One workload per file, in three tiers, in two manifest sets.

Deploy with [`deploy/ansible/deploy.yml`](../ansible/README.md) — it applies
these in the right order, builds images, renders Secrets and reruns Jobs. The
manual commands further down document what it does. Day-to-day operation of the
dev server is in [`docs/RUNBOOK.md`](../../docs/RUNBOOK.md).

```
deploy/k8s/
  production/                    hardened set (the sections below describe it)
    namespace.yaml               dataagent-service, dataagent-database, dataagent-sandbox (restricted PSA)
    configmap.yaml               dataagent-config, one copy per namespace
    rbac.yaml                    the two RBAC exceptions (OpenBao, sandbox-executor)
    networkpolicy-*.yaml         deny-all + the allow matrices
    database/                    StatefulSets for the stores (10)
    service/                     Deployments
    job/                         Jobs + the backup CronJob + the restore job
  dev/                           the same stack relaxed for the single-node dev server — see "Dev set"
  create-secrets.sh              per-workload Secrets from a secrets.env
  create-configmaps.sh           file-backed ConfigMaps from deploy/docker/ and tests/e2e/
  gen-secrets-env.sh, bootstrap-openbao.sh, gen-kafka-jaas-secret.sh
                                 helpers for dev/up.sh (legacy bootstrap)
  build-push-images.sh           legacy image build; deploy.yml builds images now
  ops/                           host cron scripts: disk-alert.sh, backup-registry.sh
```

## Namespaces

Three, all labelled `app.kubernetes.io/part-of: dataagent`:

| Namespace | Holds |
| --- | --- |
| `dataagent-database` | Postgres, Mongo, Redis, Qdrant, Neo4j, Kafka, MinIO, ClickHouse, OpenBao, PgBouncer, and the restore job |
| `dataagent-service` | the gateway, identity, application, worker and observability Deployments, and every Job except the restore |
| `dataagent-sandbox` | only the sandbox runner pods `sandbox-executor` spawns; its default-deny has no allow rules at all |

All carry a `name:` label as well, because the NetworkPolicies select namespaces
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
| `allow-kong-admin` | port 8101 only from `auth-api` and `setup` |
| `allow-ingress-controller-to-kong` | the only ingress from outside the platform |
| `allow-egress-remote-llm` | `10.10.55.101` on 8001–8004, from the workloads that call the GPU box: `model-service`, `litellm`, `llm-exporter` and the four KB workers |

Because egress is coarse (platform-only) and ingress is per-caller, a flow has to
be allowed at both ends to work. Adding a service means adding its ingress
policy — it will not talk to anything until you do.

## Identity

**Every workload runs under its own ServiceAccount**, named after it, with
`automountServiceAccountToken: false` on both the account and the pod. No
RoleBinding is created for any of them, so the account can call essentially
nothing on the API server. That is the default and it is deliberate.

There are two exceptions. `sandbox-executor` holds a Role in `dataagent-sandbox`
only (create/delete/get/list/watch on pods, `pods/exec`, `pods/log`) to run its
warm pool of runner pods. `openbao` calls the API server for the Kubernetes auth
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
`privileged`. `tests/unit/workflow_runtime/test_k8s_manifests.py` asserts all of it, plus every
field against the API server's own OpenAPI models.

`sandbox-executor` runs user-submitted code and additionally sets
`runtimeClassName: gvisor`. The cluster needs a `RuntimeClass` named `gvisor`;
drop the field if you have not installed it (RUNBOOK: "sandbox-executor Pending").

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
lives in [`production/configmap.yaml`](production/configmap.yaml) (and
`dev/configmap.yaml`), once per namespace.

A ConfigMap change does **not** reach a running pod through `envFrom` — restart it:
`kubectl -n <ns> rollout restart deployment/<name>`.

### Secrets are scoped per workload

There is no shared Secret mounted everywhere. Each workload gets a
`<workload>-secrets` holding only the keys its code actually reads, under the
names the code expects — so `kb-backend` gets four keys and never sees
`GRAFANA_ADMIN_PASSWORD` or the Snowflake crawler password.

`GATEWAY_KEY` and `SERVICE_AUTH_SECRET` go to the four services that import
`libs.shared.auth` (which imports `service_identity`): `auth-api`,
`flow-builder`, `kb-backend`, `workflow-runtime` (`GATEWAY_KEY` also to the
`setup` and `eval` Jobs). Nothing else needs them, and both fail *silently* when absent — an unset `GATEWAY_KEY` makes
`X-Kong-Verified` trusted on its own, and a mismatched `SERVICE_AUTH_SECRET`
makes `kb_search` return no chunks. Check those two first if retrieval goes quiet.

[`create-secrets.sh`](create-secrets.sh) reads one env file and creates every
Secret in the right namespace with the right subset. The deploy pipeline renders
that file from the ansible vault and runs the script for you — see
[`deploy/ansible/README.md` → Secrets](../ansible/README.md#secrets). By hand:

```bash
set -a; . deploy/k8s/dev/secrets.env; set +a     # the file the pipeline rendered
bash deploy/k8s/create-secrets.sh
```

It overwrites every Secret. Only feed it a file you know matches the running
cluster — an env file from another generation silently replaces the database,
Kafka and gateway credentials the stores were initialised with.

[`create-secrets.sh`](create-secrets.sh) is generated from the same table the
manifests use, so it cannot drift from what the pods reference.

Kafka's JAAS file is a separate Secret — six passwords (admin, audit, workflow
runtime, memory, schema crawler, web crawler) that must never sit in an image
layer. The deploy pipeline fills them into
`deploy/docker/kafka/kafka-broker-jaas.config.example` and creates the
`kafka-jaas` Secret itself. By hand,
[`gen-kafka-jaas-secret.sh`](gen-kafka-jaas-secret.sh) does the same from the
sourced `secrets.env`, piping the result straight into the Secret so no rendered
file touches the disk:

```bash
set -a; . deploy/k8s/dev/secrets.env; set +a
(cd deploy/k8s && bash gen-kafka-jaas-secret.sh)
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

`deploy/ansible/deploy.yml` performs these steps. This is the manual equivalent
for the production set; order matters once, on a fresh cluster.

```bash
kubectl apply -f deploy/k8s/production/namespace.yaml
kubectl apply -f deploy/k8s/production/configmap.yaml -f deploy/k8s/production/rbac.yaml
kubectl apply -f deploy/k8s/production/networkpolicy-service.yaml \
              -f deploy/k8s/production/networkpolicy-database.yaml
bash deploy/k8s/create-secrets.sh      # needs secrets.env sourced
bash deploy/k8s/create-configmaps.sh
kubectl apply -f deploy/k8s/production/database/
kubectl -n dataagent-database rollout status statefulset/postgres

kubectl apply -f deploy/k8s/production/job/kong-migration.yaml -f deploy/k8s/production/job/migrate.yaml
kubectl -n dataagent-service wait --for=condition=complete job/migrate --timeout=10m
kubectl apply -f deploy/k8s/production/job/db-init.yaml -f deploy/k8s/production/job/minio-init.yaml
kubectl -n dataagent-service wait --for=condition=complete job/db-init --timeout=5m

kubectl apply -f deploy/k8s/production/service/
kubectl apply -f deploy/k8s/production/job/setup.yaml          # Kong routes + Keycloak mappers
kubectl apply -f deploy/k8s/production/job/postgres-backup.yaml
```

`postgres-restore` drops a database, so it ships `suspend: true` with
`backoffLimit: 0` — applying it creates the Job but never starts it. Unsuspend
deliberately when you mean to restore:
`kubectl -n dataagent-database patch job postgres-restore -p '{"spec":{"suspend":false}}'`.
A test fails the build if that flag is ever flipped in the manifest. Our images
are named `dataagent/<service_name>:latest`, which resolves against a node's
local image store; prefix a registry at apply time if you push somewhere. They
are built from `backend/services/<name>/Dockerfile` (plus `backend/alembic/`,
`model/eval/`, `frontend/Dockerfile.dev`). Three images are built under `deploy/`:
`deploy/docker/kong/`, `deploy/docker/keycloak/` and `deploy/docker/setup/` (the
Job that runs `kong-setup.sh` and the Keycloak mapper script). Everything else
pulls a public image directly.

A Job's pod template is immutable, so re-applying a completed Job fails. Delete
it first: `kubectl -n dataagent-service delete job migrate --ignore-not-found`.

## Dev set

`deploy/k8s/dev/` is the same stack with the hardening removed, for the
single-node dev server (`10.10.55.101`). Its `secrets.env`, `.openbao-keys.json`
and `.openbao-env.yml` live inside it and are git-ignored.

| | production | dev |
| --- | --- | --- |
| Pod security | restricted PSA, locked-down `securityContext` | privileged PSA; our images run as root with a writable root fs |
| Network | default-deny + allow matrix | one `allow-all` per namespace |
| Services | ClusterIP | NodePort, `nodePort = port + 22000` (30100–30599) |
| Images | `dataagent/<name>:latest` | `localhost:5000/dataagent/<name>:latest` |
| Replicas | as sized | 1, HPA `minReplicas: 1` |
| Access | per-workload ServiceAccounts only | plus `dataagent-dev`, bound to `admin` in the three namespaces |

Deploy it with the pipeline (inventory `k8s-local` sets `k8s_env: dev`):

```bash
cd deploy/ansible && ansible-playbook deploy.yml
kubectl -n dataagent-service get secret dataagent-dev-token -o jsonpath='{.data.token}' | base64 -d
```

The dev frontend runs the Vite dev server (`frontend/Dockerfile.dev`), which reads
`VITE_API_URL` / `VITE_KEYCLOAK_URL` from `frontend-secrets` when the pod starts.

`dev/up.sh` is the **legacy bootstrap** used for the first bring-up. Don't run it
against the existing dev cluster: it applies Secrets from whatever
`dev/secrets.env` is on disk, mints a new OpenBao secret-id, and runs `seed`
without demo data. It points the helper scripts at the dev files through
`SECRETS_ENV=dev/secrets.env KEYS_FILE=dev/.openbao-keys.json`.

## Ports

Every workload listens on an `8xxx` port. For each one the **containerPort, the
Service `port` and its `targetPort` are the same number** — there is nothing to
translate when reading a manifest or a URL.

For the images we build the port appears exactly once, in the manifest's
`command`: the Dockerfiles carry no `EXPOSE` and no `CMD`.

**`8000–8099` is reserved and must stay unallocated.** It belongs to the LLM
tier: the GPU server already serves vLLM main on `:8001`, vLLM fast on `:8002`,
TEI embed on `:8003` and TEI rerank on `:8004`, and in-cluster LLM workloads will take the rest of the
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
| pgbouncer | database | 6432 | **8390** | `LISTEN_PORT` |
| prometheus | service | 9090 | **8400** | `--web.listen-address` |
| grafana | service | 3000 | **8410** | `GF_SERVER_HTTP_PORT` |
| langfuse | service | 3000 | **8420** | `PORT` |
| langfuse-worker | service | 3030 | **8421** | `PORT` (no Service — queue consumer) |
| jaeger (ui) | service | 16686 | **8430** | `QUERY_HTTP_SERVER_HOST_PORT` |
| jaeger (otlp) | service | 4317 | **8431** | `COLLECTOR_OTLP_GRPC_HOST_PORT` |
| litellm | service | 4000 | **8500** | `--port` |

### Consistency

Every URL in `dataagent-config` and in the inline `env` of each manifest uses the
numbers above — `postgres:8300`, `redis://redis:8320`, `bolt://neo4j:8341`,
`kafka:8350` (SASL) / `kafka:8352` (plaintext internal), `http://minio:8360`,
`http://litellm:8500`, `http://jaeger:8431`, and so on.

Service DNS names are DNS-1123, so an underscore is not legal in a hostname:
`model-service`, `workflow-runtime`, `kb-backend`, `auth-api`, `flow-builder`,
`sandbox-executor`, `llm-exporter`. `env` in the manifest wins
over whatever default the image ships, so the underscored defaults compiled into
the Python services never apply in-cluster.

`tests/unit/workflow_runtime/test_k8s_manifests.py` asserts the range, that no two Services share
a port, that `8000–8099` stays empty, and that `port == targetPort ==
containerPort` for every workload.

## Ingress

Not included — it is cluster-specific. Point one at `kong:8100`; that is the only
service meant to receive outside traffic. The dev set has no ingress: browsers
use the NodePorts (frontend `30280`, Kong `30100`, Keycloak `30110`), and those
URLs must match `public_api_url` / `keycloak_public_url` in the Ansible inventory. `kong-admin:8101` is a separate
ClusterIP Service and must never be exposed.

## Deliberately absent

The Docker Compose stack these manifests were first ported from has been deleted;
Kubernetes is the only way the platform runs. Two things it had are intentionally
not here:

| Component | Why |
| --- | --- |
| Docker-in-Docker sandbox | Rootless Docker-in-Docker needs `privileged`, unconfined seccomp/AppArmor and `/dev/fuse` — it cannot exist in a restricted namespace. `sandbox-executor` spawns runner pods in `dataagent-sandbox` (`SANDBOX_BACKEND=kubernetes`), under `runtimeClassName: gvisor` in the production set. |
| Central log store | Node log collection needs a `hostPath` agent running as root, a cluster add-on rather than an application workload. The Elasticsearch/Kibana pair was removed after it held 0 indices for 16 days (27/09/2026). Read logs with `kubectl logs`; the target (Loki, after the POC) is in `docs/v2/11-ops.md` §7. |
