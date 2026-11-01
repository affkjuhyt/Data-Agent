# deploy/docker — image build contexts and ConfigMap sources

[`deploy/k8s/`](../k8s) is the deployment definition; Kubernetes is the only way
the platform runs (the Docker Compose stack was deleted). This directory holds two
things:

- the build contexts of the three images built under `deploy/`: `kong` (our Lua
  JWT plugin), `keycloak` (bakes `kc.sh build --db=postgres`) and `setup` (bash +
  curl for the `setup` Job);
- the files the cluster reads as ConfigMaps or Job inputs: SQL, the Kong and
  Keycloak setup scripts, the Keycloak realm, LiteLLM routing, the Prometheus
  config — plus the dev TLS tool and the Kafka JAAS template.

## What is here

| Path | Used by |
| --- | --- |
| `kong/Dockerfile`, `kong/plugins/aeroflow-jwks/` | image `dataagent/kong`, because the plugin is our Lua |
| `keycloak/Dockerfile` | image `dataagent/keycloak`: Keycloak built for Postgres, so `--optimized` starts under a read-only root fs |
| `setup/Dockerfile` | image for `job/setup.yaml` (the Job has no Internet egress to `apk add` at runtime) |
| `kong/kong-setup.sh` | ConfigMap `kong-scripts` → `job/setup.yaml` |
| `keycloak/setup-audience-mappers.sh`, `realm-export.json` (key `aeroflow-realm.json`), `keycloak/demo/aeroflow-users-0.json` (demo users, only with `seed_demo`) | ConfigMaps `keycloak-scripts`, `keycloak-realm` |
| `sql/create-extra-dbs.sql`, `init.sql` | ConfigMap `postgres-initdb` |
| `sql/grant-roles.sql`, `v3-*.sql` | ConfigMap `db-init-sql` → `job/db-init.yaml` |
| `litellm/config.yaml` | ConfigMap `litellm-config` |
| `monitoring/prometheus.yml` | ConfigMap `prometheus-config` |
| `certs/gen-certs.sh` | dev TLS material, generated files git-ignored |
| `kafka/kafka-broker-jaas.config.example` | filled by `deploy/k8s/gen-kafka-jaas-secret.sh` → Secret `kafka-jaas` (never baked into an image) |

Application images build from `backend/services/<name>/Dockerfile` with the repo
root as context. The ConfigMaps above are created by
[`deploy/k8s/create-configmaps.sh`](../k8s/create-configmaps.sh), which reads
these files in place — there is no second copy.

## Building

```bash
docker build -t dataagent/crawler:latest    -f backend/services/crawler/Dockerfile .
docker build -t dataagent/kong:latest       -f deploy/docker/kong/Dockerfile .
```

Images are named `dataagent/<service_name>`; push them as
`REGISTRY/dataagent/<service_name>:<tag>` and set `REGISTRY` in the manifests.

## Ports

Ports are not in the Dockerfiles. Each image has no `EXPOSE` and no `CMD` — the
start command and the port live in the workload's manifest, so there is one place
to change them (the table in `deploy/k8s/README.md`). `8000–8099` stays reserved for the LLM tier. Third-party
images keep their native port.

## Constraints to keep in mind

- **Kafka replication factors are pinned to 1.** A single broker cannot create
  the internal topics at the default factor of 3, and consumers then hang on
  `GroupCoordinatorNotAvailableError`.
- **Kafka's `INTERNAL` listener (`:8352`) is plaintext and its principal is a
  super-user.** Only in-cluster admin Jobs may use it (K7 of the V2 ops plan).
- **Redis has no password.** Langfuse must not send `AUTH`.
- **`GATEWAY_KEY` must equal the `aeroflow-jwks` plugin's key** set by
  `kong/kong-setup.sh`.
- **Debian packages come from `DEBIAN_MIRROR` when it is set.** The Dockerfiles that run
  `apt-get` take it as a build arg (empty keeps `deb.debian.org`); the k8s-local inventory
  passes BizFly's mirror through `image_build_flags`, because deb.debian.org was ~0.3 MB/s
  from that host. apt verifies the archive signatures, so the mirror cannot alter a package.
- **No service runs a local model.** LLM calls go through LiteLLM, embeddings and
  reranking to TEI. Embeddings are `bge-m3` (1024-dim).
