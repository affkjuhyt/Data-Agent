# deploy/docker — image build contexts and ConfigMap sources

[`deploy/k8s/`](../k8s) is the deployment definition; Kubernetes is the only way
the platform runs (the Docker Compose stack was deleted). This directory holds two
things:

- the build contexts of the three images built under `deploy/`: `kong` (our Lua
  JWT plugin), `keycloak` (bakes `kc.sh build --db=postgres`) and `setup` (bash +
  curl for the `setup` Job);
- the files the cluster reads as ConfigMaps or Job inputs: SQL, the Kong and
  Keycloak setup scripts, the Keycloak realm, LiteLLM routing, Prometheus/Grafana
  provisioning — plus the dev TLS tool and the Kafka JAAS template.

## What is here

| Path | Used by |
| --- | --- |
| `kong/Dockerfile`, `kong/plugins/aeroflow-jwks/` | image `dataagent/kong`, because the plugin is our Lua |
| `keycloak/Dockerfile` | image `dataagent/keycloak`: Keycloak built for Postgres, so `--optimized` starts under a read-only root fs |
| `setup/Dockerfile` | image for `job/setup.yaml` (the Job has no Internet egress to `apk add` at runtime) |
| `kong/kong-setup.sh` | ConfigMap `kong-scripts` → `job/setup.yaml` |
| `keycloak/setup-audience-mappers.sh`, `realm-export.json` | ConfigMaps `keycloak-scripts`, `keycloak-realm` |
| `sql/create-extra-dbs.sql`, `init.sql` | ConfigMap `postgres-initdb` |
| `sql/grant-roles.sql`, `kb-seed.sql` | ConfigMap `db-init-sql` → `job/db-init.yaml` |
| `litellm/config.yaml` | ConfigMap `litellm-config` |
| `monitoring/prometheus.yml` | ConfigMap `prometheus-config` |
| `monitoring/grafana/provisioning/{datasources,dashboards}/`, `grafana/dashboards/` | ConfigMaps `grafana-datasources`, `grafana-dashboards-provider`, `grafana-dashboards` |
| `certs/gen-certs.sh` | dev TLS material, generated files git-ignored |
| `kafka/kafka-broker-jaas.config.example` | filled by `deploy/k8s/gen-kafka-jaas-secret.sh` → Secret `kafka-jaas` (never baked into an image) |

Application images build from `backend/services/<name>/Dockerfile` with the repo
root as context. The ConfigMaps above are created by
[`deploy/k8s/create-configmaps.sh`](../k8s/create-configmaps.sh), which reads
these files in place — there is no second copy.

## Building

```bash
docker build -t dataagent/kb-backend:latest -f backend/services/kb_backend/Dockerfile .
docker build -t dataagent/kong:latest       -f deploy/docker/kong/Dockerfile .
```

Images are named `dataagent/<service_name>`; push them as
`REGISTRY/dataagent/<service_name>:<tag>` and set `REGISTRY` in the manifests.

## Ports

Ports are not in the Dockerfiles. Each image has no `EXPOSE` and no `CMD` — the
start command and the port live in the workload's manifest, so there is one place
to change them: auth-api 8200, kb-backend 8220, model-service
8230, workflow-runtime 8240 (also the agent/workflow authoring API, ex flow-builder), sandbox-executor 8260,
llm-exporter 8270. `8000–8099` stays reserved for the LLM tier. Third-party
images keep their native port.

## Constraints to keep in mind

- **Kafka replication factors are pinned to 1.** A single broker cannot create
  the internal topics at the default factor of 3, and consumers then hang on
  `GroupCoordinatorNotAvailableError`.
- **Kafka's `INTERNAL` listener (`:8352`) is plaintext and its principal is a
  super-user.** `kb-backend` and the KB workers (`kb-promote-*`,
  `kb-structured-data`, `kb-conflict-resolution`) have no SASL support and use
  it, as does the `kafka-topics` Job (`docs/v2/11-ops.md` §5, K7).
- **Redis has no password.** Langfuse must not send `AUTH`.
- **`SERVICE_AUTH_SECRET` must be identical** in `workflow-runtime` and
  `kb-backend` — a mismatch is silent.
- **`GATEWAY_KEY` must equal the `aeroflow-jwks` plugin's key** set by
  `kong/kong-setup.sh`.
- **`model_service` runs no local models.** `REMOTE_EMBED_URL` /
  `REMOTE_RERANK_URL` / `REMOTE_LLM_URL` are required. Embeddings are `bge-m3`
  (1024-dim) — a KB ingested against the old 384-dim backend must be re-ingested.
