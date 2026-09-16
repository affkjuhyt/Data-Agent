# Shell scripts: what each one is for, and what replaces it

Policy is in [`CLAUDE.md`](../CLAUDE.md) §5: no new `.sh` for orchestration. This table is
the migration plan for the 20 that exist (line counts as of 16/09/2026).

| Script | Lines | Does | Plan |
| --- | ---: | --- | --- |
| `deploy/docker/kong/kong-setup.sh` | 633 | Creates Kong services, routes, consumers, JWT/ACL plugins through the Admin API | **Replace with decK**: export the live config (`deck gump`), commit `deploy/kong/kong.yaml`, apply with `deck sync` from a Job. Declarative, diffable, idempotent — 633 lines of `curl` become one YAML |
| `tests/e2e/e2e.sh` | 329 | Brings up the compose stack, waits, seeds, runs API + UI suites | **Split**: keep a thin `make e2e`; move waits to `docker compose up --wait` (healthchecks already exist), move setup/seed/asserts into `tests/e2e/*.py` driven by pytest |
| `deploy/docker/kafka/kafka-setup.sh` | 283 | Creates topics + ACLs | **Topic spec**: a YAML list of topics/partitions/ACLs applied by a small Python Job (kafka-python is already a dependency); Strimzi `KafkaTopic` CRs if the cluster moves to an operator |
| `deploy/k8s/create-secrets.sh` | 234 | Renders `secrets.env` into k8s Secrets | **External Secrets Operator** reading OpenBao (target state in `docs/ankt/OPS-DESIGN.md`); until then an Ansible `kubernetes.core.k8s` task |
| `deploy/docker/keycloak/setup-audience-mappers.sh` | 159 | Adds audience mappers to realm clients | Fold into the realm export (`realm-export.json`) so the realm is imported already correct |
| `deploy/docker/certs/gen-certs.sh` | 128 | Dev mTLS material | Keep — local dev only, no cluster dependency |
| `tests/loadtest/ingestion_throughput.sh` | 115 | Drives ingestion load | Move into the k6 suite next to `ccu-test.js` so all load tests share one tool |
| `deploy/k8s/gen-secrets-env.sh` | 110 | Generates `secrets.env` from templates/vault | Same as `create-secrets.sh` — Ansible template + ESO |
| `backend/services/sandbox_executor/ops/setup_sandbox_egress_iptables.sh` | 104 | Host firewall for the sandbox | Keep, but call it from an Ansible role so it is applied reproducibly |
| `deploy/docker/openbao/openbao-approle-setup.sh` | 92 | AppRole bootstrap | Ansible `k8s_openbao` role already does this for the cluster; keep only the compose path |
| `deploy/k8s/bootstrap-openbao.sh` | 91 | Init/unseal OpenBao | Keep (bootstrap runs before any tooling exists), but move the unseal keys out of the file system |
| `backend/services/sandbox_executor/ops/install_gvisor.sh` | 84 | Installs gVisor on the host | Ansible role; it is host bootstrap, not app code |
| `deploy/k8s/switch-gpu-containers-to-host-network.sh` | 82 | Runs vLLM/TEI containers with `--gpus all` | **Manifests**: GPU Operator + Deployments with pinned image tags (`docs/ankt/OPS-DESIGN.md` §3.1). Today this is the only definition of how the models run — highest-value replacement |
| `deploy/k8s/build-push-images.sh` | 68 | Builds and pushes images | Superseded by `deploy/ansible/deploy.yml --tags images`; keep as the manual fallback until CI builds images |
| `deploy/k8s/fix-docker-userland-proxy.sh` | 60 | One-time host daemon fix | Ansible task + a line in `docs/RUNBOOK.md`; delete the script once applied everywhere |
| `deploy/k8s/create-configmaps.sh` | 59 | Builds ConfigMaps from `deploy/docker/**` | Keep for now (Ansible calls it); becomes a Kustomize `configMapGenerator` when GitOps lands |
| `deploy/k8s/dev/up.sh` | 51 | Dev bring-up | Fold into `make deploy` |
| `deploy/k8s/ops/disk-alert.sh` | 50 | Cron disk check that only writes to syslog | **Delete** once Prometheus + Alertmanager exist (rule `DiskFillingUp`, `docs/ankt/OPS-DESIGN.md` §4.3) |
| `deploy/k8s/fix-pod-to-host-docker-networking.sh` | 42 | One-time host networking fix | Same as the other `fix-*`: Ansible + runbook |
| `deploy/k8s/ops/backup-registry.sh` | 26 | Registry backup cron | Keep until Harbor replication; it is already declarative enough |
| `deploy/k8s/gen-kafka-jaas-secret.sh` | 23 | Renders the Kafka JAAS secret | Merge into the secrets path above |

## Order of work

1. `switch-gpu-containers-to-host-network.sh` → manifests (removes the last undeclared runtime).
2. `kong-setup.sh` → decK (largest script, highest drift risk).
3. Secrets trio (`create-secrets`, `gen-secrets-env`, `gen-kafka-jaas-secret`) → ESO.
4. `e2e.sh` → `make e2e` + pytest.
5. Delete `fix-*.sh` and `disk-alert.sh` once their replacements are in place.
