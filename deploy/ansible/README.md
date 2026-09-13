# Ansible — Data Agent on Kubernetes

Three playbooks against an existing cluster. The manifests in `deploy/k8s/` stay
the source of truth for *what* is deployed; these own the order, the waits, and
the values that cannot be committed.

```bash
cd deploy/ansible
ansible-playbook setup.yml                                          # first run
ansible-playbook config.yml                                         # secrets / config changed
ansible-playbook release.yml -e image_tag=$(git -C ../.. describe --tags --always)
```

`ansible.cfg` defaults to `inventories/k8s-local` (`ansible_connection: local` —
run it on the VM that holds the kubeconfig).

Each playbook does one thing. Nothing infers which case you are in.

## setup.yml

| Role | Does |
| --- | --- |
| `k8s_preflight` | kubectl, API server, nodes Ready, required files |
| `k8s_core` | namespaces, shared ConfigMap, RBAC, NetworkPolicies |
| `k8s_secrets` | decrypt `secrets.env`, create Secrets, ConfigMaps, kafka-jaas |
| `k8s_validate` | `--dry-run=server`, node capacity, every referenced Secret exists |
| `k8s_databases` | 10 StatefulSets + pgbouncer, rollout waits |
| `k8s_jobs` | kong-migration, migrate, db-init, minio-init, kafka-topics |
| `k8s_services` | 23 Deployments |
| `k8s_setup` | Kong routes / Keycloak mappers job, backup CronJob |
| `k8s_verify` | namespaces, netpol, RBAC, ports, unready pods |

`k8s_validate` creates nothing. If it fails, no pod has been made.

## config.yml

`k8s_preflight` → `k8s_secrets` → `k8s_verify`.

Re-renders `secrets.env`, re-applies Secrets and ConfigMaps. No image is built
and no migration runs. If `kubectl` reports anything `configured`, a handler runs
`rollout restart deployment --all`; if everything is `unchanged`, no pod is
touched.

## release.yml

`k8s_images` → `k8s_jobs` → `k8s_services` → `k8s_verify`.

Builds every image, then applies with the new tag. Docker's layer cache makes an
unchanged image cheap, so nothing tries to guess which sources moved.

`Get image ids` and `List rebuilt images` compare `docker images -q` either side
of the build. A cached rebuild keeps the same image id, so only images whose
content actually moved are saved and pushed — a re-run of the same tag imports
nothing. A fresh tag is new to every image, so everything is saved.

Narrow it by hand when you want to:

```bash
ansible-playbook release.yml -e image_tag=v9 -e images_only=workflow_runtime,frontend
```

Every image reference is built from two variables, in one place:

```
{{ image_prefix }}/<name>:{{ image_tag }}
    dataagent               latest
```

To push to a registry, put it in the prefix: `image_prefix: reg.local:5000/dataagent`.

The manifests commit the default of that shape, `dataagent/<name>:latest`, and
it is rewritten at apply time, so `image_prefix` changes what is built and what
is deployed together. A non-`latest` tag
also makes Kubernetes default `imagePullPolicy` to `IfNotPresent`, which is what
locally imported images need, and gives `kubectl rollout undo` something to roll
back to.

`context` in the `images` list is read off each Dockerfile: `model_service`,
`sandbox_executor` and `frontend` build from their own directory, the rest from
the repo root.

Load into the node without a registry:

```yaml
image_import: "sudo k3s ctr images import -"
```

## Secrets

One ansible-vault file is the source of truth. `k8s_secrets` decrypts it to
`deploy/k8s/secrets.env` (0600, git-ignored) and feeds it to
`create-secrets.sh` and `create-configmaps.sh`.

```bash
ansible-playbook setup.yml --tags bootstrap-secrets
cp ../k8s/secrets.env inventories/k8s-local/secrets.env.vault
ansible-vault encrypt inventories/k8s-local/secrets.env.vault
ansible-vault edit inventories/k8s-local/secrets.env.vault
export ANSIBLE_VAULT_PASSWORD_FILE=~/.dataagent-vault-pass
```

Postgres, Keycloak, MinIO, Neo4j and ClickHouse apply their password only when
initialising empty storage. Changing one afterwards updates the Secret but not
the store, so decide those before the first run.

## Sizing

`k8s_validate` reports requested vs allocatable. On these manifests the database
tier asks for 3.6 cores / 7.9Gi; adding the service tier takes it to 12.9 cores /
20.7Gi, plus 200Gi of PVCs. Trim `database_workloads` (elasticsearch, clickhouse,
neo4j) on a small VM.

## Conventions

Task names start with a verb: `Create namespaces`, `Wait for postgres`,
`Check store ports`. No comments in the YAML.
