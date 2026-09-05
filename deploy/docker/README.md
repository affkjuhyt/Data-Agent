# deploy/docker — build contexts and service config

This directory no longer defines a stack. The Compose files were removed once
[`deploy/k8s/`](../k8s) became the only deployment definition; what remains is
what the images are built from and what the cluster mounts as ConfigMaps.

The superseded Compose stack is kept verbatim in [`.old/`](.old/) for reference.
Nothing reads it.

## What is here

| Path | Used by |
| --- | --- |
| `kong/Dockerfile`, `kong/plugins/aeroflow-jwks/` | image `dataagent/kong` — the only image built here, because the plugin is our Lua |
| `kong/kong-setup.sh` | ConfigMap `kong-scripts` → `job/setup.yaml` |
| `keycloak/setup-audience-mappers.sh`, `realm-export.json` | ConfigMaps `keycloak-scripts`, `keycloak-realm` |
| `sql/create-extra-dbs.sql`, `init.sql` | ConfigMap `postgres-initdb` |
| `sql/grant-runtime-writer.sql`, `kb-seed.sql` | ConfigMap `db-init-sql` → `job/db-init.yaml` |
| `litellm/config.yaml` | ConfigMap `litellm-config` |
| `monitoring/prometheus.yml`, `monitoring/grafana/provisioning/` | ConfigMaps `prometheus-config`, `grafana-provisioning` |
| `redis/redis.conf` | superseded — the ConfigMap in `database/redis.yaml` carries it |
| `openbao/config.hcl` | superseded — the ConfigMap in `database/openbao.yaml` carries it |
| `certs/gen-certs.sh` | dev TLS material, generated files git-ignored |
| `kafka/kafka-broker-jaas.config.example` | copy + fill → Secret `kafka-jaas` (never baked into an image) |
| `filebeat/`, `haproxy/` | unused — see "Not ported" in the k8s README |

Application images build from `backend/services/<name>/Dockerfile` with the repo
root as context. `deploy/k8s/README.md` has the exact `kubectl create configmap`
commands for every file above.

## Building

```bash
docker build -t dataagent/kb-backend:latest -f backend/services/kb_backend/Dockerfile .
docker build -t dataagent/kong:latest       -f deploy/docker/kong/Dockerfile .
```

Images are named `dataagent/<service_name>`; push them as
`REGISTRY/dataagent/<service_name>:<tag>` and set `REGISTRY` in the manifests.

## Ports

Container ports are fixed in each service's Dockerfile and matched by the
manifests: auth-api 8200, release-worker 8210, kb-backend 8220, model-service
8230, workflow-runtime 8240, flow-builder 8250, sandbox-executor 8260,
llm-exporter 8270. `8000–8099` stays reserved for the LLM tier. Third-party
images keep their native port.

## Constraints that survive the move

- **Kafka replication factors are pinned to 1.** A single broker cannot create
  the internal topics at the default factor of 3, and consumers then hang on
  `GroupCoordinatorNotAvailableError`.
- **Kafka `INTERNAL://:9094` is plaintext and its principal is a super-user.**
  `kb-backend` and `kb-ingestion-worker` have no SASL support and use it.
- **Redis has no password.** Langfuse must not send `AUTH`.
- **`SERVICE_AUTH_SECRET` must be identical** in `workflow-runtime` and
  `kb-backend` — a mismatch is silent.
- **`GATEWAY_KEY` must equal the `aeroflow-jwks` plugin's key** set by
  `kong/kong-setup.sh`.
- **`model_service` runs no local models.** `REMOTE_EMBED_URL` /
  `REMOTE_RERANK_URL` / `REMOTE_LLM_URL` are required. Embeddings are `bge-m3`
  (1024-dim) — a KB ingested against the old 384-dim backend must be re-ingested.
