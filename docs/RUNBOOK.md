# Operations Runbook — GTEL Data Agent

How to run, check and fix the Data Agent stack on the dev server. Start here.

| For | Read |
| --- | --- |
| How the deploy pipeline works, its flags, secrets, server-only state | [`deploy/ansible/README.md`](../deploy/ansible/README.md) |
| Manifest design: namespaces, ports, network policy, dev vs production | [`deploy/k8s/README.md`](../deploy/k8s/README.md) |
| Security posture | [`SECURITY.md`](./SECURITY.md) |

---

## 1. Where things are

| | |
| --- | --- |
| Dev server | `ndip`, `10.10.55.101`, single-node k3s |
| Deploy checkout | `/home/ndip/Data-Agent` — deploy from here, and only from here |
| Deploy command | `cd deploy/ansible && ansible-playbook deploy.yml` |
| Manifests applied | `deploy/k8s/dev/` |
| Image registry | `localhost:5000` (Docker container `registry` on the server) |
| Namespaces | `dataagent-database` (stores), `dataagent-service` (everything else), `dataagent-sandbox` (sandbox runner pods) |
| LLM / embed / rerank | GPU services on `10.10.55.101`: vLLM `:8002`, TEI embed `:8003`, TEI rerank `:8004` |
| Server-only secrets | `deploy/ansible/inventories/k8s-local/.secrets/`, `deploy/k8s/dev/{secrets.env,.openbao-keys.json,.openbao-env.yml}` — git-ignored, back them up |

## 2. URLs and logins

Every dev Service is a NodePort with `nodePort = port + 22000`. Open them from
any machine on the `10.10.55.x` network; no port-forwarding is needed.

| Service | URL |
| --- | --- |
| **Frontend** | http://10.10.55.101:30280 |
| Kong proxy — all API traffic | http://10.10.55.101:30100 |
| Keycloak (realm `aeroflow`) | http://10.10.55.101:30110 |
| Langfuse | http://10.10.55.101:30420 |
| Grafana / Prometheus | http://10.10.55.101:30410 / :30400 |
| Jaeger / Kibana | http://10.10.55.101:30430 / :30440 |
| LiteLLM | http://10.10.55.101:30500 |
| MinIO console | http://10.10.55.101:30361 |
| Kong admin (dev only — never expose beyond the LAN) | http://10.10.55.101:30101 |

The full port table is in [`deploy/k8s/README.md`](../deploy/k8s/README.md#ports).

**App users** (realm `aeroflow`, all in tenant `a0000000-0000-0000-0000-000000000001`):
`helios-admin`, `helios-engineer`, `helios-viewer`, `platform-admin`,
`ai-engineer`, `executive-viewer`. Their passwords are set by the `setup` Job
from the `set_password` lines in
[`deploy/docker/kong/kong-setup.sh`](../deploy/docker/kong/kong-setup.sh).

**Admin consoles**: passwords come from the vault. Read them from the cluster:

```bash
kubectl -n dataagent-service get secret keycloak-secrets \
  -o jsonpath='{.data.KEYCLOAK_ADMIN_PASSWORD}' | base64 -d; echo   # Keycloak user: admin
kubectl -n dataagent-service get secret grafana-secrets \
  -o jsonpath='{.data.GF_SECURITY_ADMIN_PASSWORD}' | base64 -d; echo # Grafana user: admin
```

## 3. Deploy a change

1. Commit on your branch. **Uncommitted edits are not deployed** (the pipeline
   diffs commits), and the run still reports success.
2. Run the pipeline from the deploy checkout:
   ```bash
   cd /home/ndip/Data-Agent/deploy/ansible
   ansible-playbook deploy.yml
   ```
3. Read the report near the top — `changed: N file(s)`, `secrets: … configmaps: …`,
   `building: …` — to confirm it picked up what you expected.

| Change | Extra flags |
| --- | --- |
| Code in `backend/services/<svc>/` or `frontend/` | none — that image is rebuilt and its workload restarted |
| `backend/libs/`, `pyproject.toml`, `uv.lock` | none, but **every** image rebuilds — check disk first (§9) |
| New Alembic revision | `-e apply_jobs=true` (the rebuilt `migrate` image triggers the Jobs) |
| A Job manifest under `deploy/k8s/dev/job/` | `-e apply_jobs=true` |
| `deploy/docker/kong/kong-setup.sh`, Keycloak mappers | `-e apply_setup=true` |
| A value in the vault | `-e secrets_changed=true` (§7) |
| Only one image, skip the rest | `-e images_only=kb-backend` |

The dev frontend runs the Vite dev server inside its pod (image from
`frontend/Dockerfile.dev`). The source is baked into the image, so a frontend
change still needs a rebuild, and `VITE_*` values are read when the pod starts.

## 4. Check the stack

```bash
kubectl get pods -A | grep -vE 'Running|Completed'     # anything not healthy
kubectl -n dataagent-service get jobs                  # every Job Complete
```

End-to-end, the way a browser goes (Keycloak → token → Kong → service):

```bash
KC=http://10.10.55.101:30110; API=http://10.10.55.101:30100
read -rsp 'platform-admin password: ' PW; echo
TOKEN=$(curl -s -X POST "$KC/realms/aeroflow/protocol/openid-connect/token" \
  -d grant_type=password -d client_id=aeroflow-frontend -d scope=openid \
  --data-urlencode username=platform-admin --data-urlencode "password=$PW" \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')
curl -s -o /dev/null -w 'frontend %{http_code}\n' http://10.10.55.101:30280/
curl -s -H "Authorization: Bearer $TOKEN" "$API/api/agents" | python3 -m json.tool | head -20
```

Expected: frontend `200`, and `/api/agents` lists `e2e-sql-agent` and
`Default Agent` when demo data is loaded.

Issuer check — both lines must print `http://10.10.55.101:30110/realms/aeroflow`:

```bash
curl -s "$KC/realms/aeroflow/.well-known/openid-configuration" \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["issuer"])'
curl -s "http://10.10.55.101:30101/plugins?size=1000" \
  | python3 -c 'import sys,json; print({p["config"]["issuer"] for p in json.load(sys.stdin)["data"] if p["name"]=="aeroflow-jwks"})'
```

`kb-backend` is **fail-closed**: calling it directly on `:30220` returns `401`.
That is expected; application traffic goes through Kong.

## 5. Demo data

With `seed_demo: true` (the dev inventory's setting) the `seed` Job loads:

| Source | What |
| --- | --- |
| `backend/services/workflow_runtime/migrations/seed.sql` | always: Dev tenant, default LLM providers, system prompt, `Default Agent`, guardrail |
| `tests/e2e/seed.sql` | demo `e2e-sql-agent` with a published workflow; semantic metrics `doanh_thu_gop` / `doanh_thu_thuan` and synonyms; `secrets_vault` row for the warehouse password; demo warehouse `sandbox_dev.fact_sales` (8 months × 4 regions × 3 products) |
| `tests/e2e/seed-governance.sql` | warehouse registration for the schema crawler, `stg_orders_raw` (deliberately stale), `dim_region`, `dim_product`, owners/lineage/freshness SLAs |
| `tests/e2e/seed-flow.js` | the demo agent's canvas in Mongo (planner → db_connector → rrf → reranker → visualization/responder → judge) |
| `k8s_openbao` role | OpenBao secret `secret/tenants/<tenant>/e2e_dw_password` = `sandbox_reader`'s password |

Everything is idempotent. Knowledge-base **documents** are not seeded; upload them through the UI.

Check it:

```bash
kubectl -n dataagent-database exec postgres-0 -- psql -U admin -d sandbox_dev \
  -Atc "select 'fact_sales', count(*) from fact_sales union all select 'stg_orders_raw', count(*) from stg_orders_raw"
```

**Reseed** without a full pipeline run (after `create-configmaps.sh` if a seed file changed):

```bash
bash deploy/k8s/create-configmaps.sh        # only if a seed file changed
kubectl -n dataagent-service delete job seed --ignore-not-found
sed "s|SEED_DEMO: '0'|SEED_DEMO: '1'|" deploy/k8s/dev/job/seed.yaml | kubectl apply -f -
kubectl -n dataagent-service wait --for=condition=complete job/seed --timeout=10m
kubectl -n dataagent-service logs job/seed --all-containers --tail=30
kubectl -n dataagent-service rollout restart deployment/workflow-runtime   # it caches graphs and metrics
```

The OpenBao demo password is written by the pipeline, not the Job: if the vault
was reset, run `ansible-playbook deploy.yml` once.

## 6. Change the public URL

Needed when the server's IP or hostname changes, or when putting an ingress in front.

1. Edit `public_api_url`, `keycloak_public_url`, `langfuse_public_url` in
   `deploy/ansible/inventories/k8s-local/group_vars/all/main.yml`.
2. Add the new frontend origin where it is hard-coded:
   - Kong CORS: the `origins` lists in `deploy/docker/kong/kong-setup.sh`.
   - Keycloak client `aeroflow-frontend` redirect URIs and web origins:
     `deploy/docker/keycloak/realm-export.json` is imported only into a fresh
     realm, so on the running stack also add them in the Keycloak admin console
     (Clients → `aeroflow-frontend`).
3. Commit, then run:
   ```bash
   ansible-playbook deploy.yml -e apply_setup=true
   ```
   The `group_vars` change reapplies the Secrets and restarts the service tier
   (new `KC_HOSTNAME_URL`, `VITE_*`, `NEXTAUTH_URL`); `apply_setup` updates Kong's
   JWT issuer and CORS.
4. Check with §4. Everyone has to log in again: existing tokens carry the old issuer.

## 7. Secrets

- Passwords live in the ansible-vault file
  `deploy/ansible/inventories/k8s-local/.secrets/vault.yml`:
  `ansible-vault edit deploy/ansible/inventories/k8s-local/.secrets/vault.yml`
  (from `deploy/ansible/`, the vault password is picked up automatically).
- The vault is outside git, so apply an edit with
  `ansible-playbook deploy.yml -e secrets_changed=true`.
- **Do not change** the Postgres admin, Keycloak admin, MinIO, Neo4j or
  ClickHouse passwords on a running stack: those stores only take the password
  when their storage is first created.
- Never run `deploy/k8s/create-secrets.sh` by hand from an env file you haven't
  compared with the cluster. It overwrites every Secret.

Details and the full flow: [`deploy/ansible/README.md` → Secrets](../deploy/ansible/README.md#secrets).

## 8. OpenBao

OpenBao holds warehouse and connector passwords. It **seals itself whenever its
pod restarts** (node reboot, eviction). While sealed, `workflow-runtime` can't
read secrets: DB-connector nodes fail to authenticate and the logs show
`failed to read secret … from vault`.

```bash
kubectl -n dataagent-database exec statefulset/openbao -- bao status   # Sealed: true?
```

Unseal by running the pipeline once (any normal run unseals):

```bash
cd /home/ndip/Data-Agent/deploy/ansible && ansible-playbook deploy.yml
```

or by hand:

```bash
KEY=$(python3 -c 'import json; d=json.load(open("deploy/k8s/dev/.openbao-keys.json")); print((d.get("unseal_keys_b64") or d["keys_base64"])[0])')
kubectl -n dataagent-database exec statefulset/openbao -- bao operator unseal "$KEY"
```

Back up `deploy/k8s/dev/.openbao-keys.json` somewhere off the server. Without
it the vault can never be unsealed again.

## 9. Disk

Since 2026-09-27 all heavy state lives on the 1 TB data disk (`/dev/sdb1` on
`/data`), bind-mounted back onto the original paths so PVs, the registry
container and scripts are unchanged (entries in `/etc/fstab`):

| Path | Backed by | Holds |
| --- | --- | --- |
| `/var/lib/rancher` | `/data/rancher` | k3s images + every local-path PV |
| `/var/lib/kubelet` | `/data/kubelet` | pod emptyDirs |
| `/var/lib/containerd` | `/data/containerd` | Docker images (Docker 29 uses the containerd image store) |
| `/var/lib/docker` | `/data/docker` | Docker container state |
| `/home/ndip/data-agent-registry` | `/data/registry` | the `localhost:5000` registry |

systemd drop-ins (`/etc/systemd/system/{k3s,docker,containerd}.service.d/data-disk.conf`)
keep those services from starting if `/data` did not mount. `/` now only holds
the OS and home directories.

PVC sizes are not enforced by `local-path-provisioner`, so `/data` is the
limit that matters. kubelet starts garbage-collecting images at **85 %**
and evicts pods at **95 %**. On 2026-09-11 image rebuilds pushed `/` to 99 %,
the whole stack was evicted twice, and the evictions deleted the images the
pods needed to come back.

```bash
df -h / /data
docker system df
du -sh /data/registry-backups
```

- Before `deploy_full=true` or a `backend/libs` change (which rebuilds every
  image), make sure usage is under ~70 %.
- Reclaim space with `docker image prune` (dangling images). Don't delete
  `localhost:5000/dataagent/*` images unless they are in the registry.
- Host cron scripts in `deploy/k8s/ops/`: `disk-alert.sh` (alerts above 80 %,
  `journalctl -t disk-alert`) and `backup-registry.sh` (daily registry backup).
  Install lines are in each script's header.

## 10. Database migrations (Alembic)

In the cluster, migrations run as the `migrate` Job: rebuild the image and run
with `-e apply_jobs=true` (§3). Locally, through asyncpg like production:

```bash
alembic -c backend/alembic/alembic.ini upgrade head
alembic -c backend/alembic/alembic.ini current
alembic -c backend/alembic/alembic.ini downgrade -1
```

Before adding a revision, check its id is unique:
`grep -rl "revision.*=.*['\"]<new-id>['\"]" backend/alembic/versions/`.

- RLS is enforced via `enable_tenant_rls` + `rls_kb_child_tables` (FK-chain
  EXISTS policies for child tables without their own `tenant_id`). Never disable
  RLS on a tenant table to "fix" a query — scope the query by tenant.
- The chain is covered by `tests/integration/test_alembic_migrations.py`.

## 11. LLM and model backend

`model-service` (`:8230`) runs no local models. It proxies embed, rerank and
generate to the GPU services on `10.10.55.101` (vLLM `:8002`, TEI embed `:8003`,
TEI rerank `:8004`) through `REMOTE_EMBED_URL` / `REMOTE_RERANK_URL` /
`REMOTE_LLM_URL` in the shared ConfigMap `dataagent-config`
(`deploy/k8s/dev/configmap.yaml`; restart the workloads after changing it).
LiteLLM's model routing is `deploy/docker/litellm/config.yaml` (ConfigMap
`litellm-config`). Embeddings are `bge-m3` (1024-dim): a knowledge base
ingested against another embedding model must be re-ingested.

If retrieval quality is poor, first check the GPU services answer, before
suspecting the pipeline.

## 12. Troubleshooting

| Symptom | Cause → fix |
| --- | --- |
| Browser stuck on "Connecting to Keycloak…", console shows `localhost:8110` / `localhost:8100` `ERR_CONNECTION_REFUSED` | The public URLs point at localhost → §6 |
| Login works, every API call returns `401` | Kong's JWT `issuer` differs from Keycloak's → rerun with `-e apply_setup=true`, check with §4 |
| CORS error in the browser | Frontend origin missing from `kong-setup.sh` → §6 step 2 |
| The UI loads but everything is empty | No demo data (`seed` Job didn't run with `SEED_DEMO=1`) → §5; or a wrong tenant in the token |
| Pipeline succeeded, change not live | Change not committed → §3 |
| Job stuck in `ImagePullBackOff` | Image no longer pullable (happened to `minio/mc`) → use a pinned image already on the node or in `localhost:5000` |
| `field is immutable` applying a Job | Delete the Job first: `kubectl -n dataagent-service delete job <name>` |
| Pod in `CreateContainerConfigError` | Its `<workload>-secrets` is missing a key → `kubectl -n <ns> describe pod <pod>` names it; rerun the pipeline with `-e secrets_changed=true` |
| DB-connector / warehouse nodes fail to authenticate | OpenBao sealed → §8 |
| Many pods `Evicted` at once | Disk pressure → §9 |
| `sandbox-executor` Pending (production set) | No `gvisor` RuntimeClass; preflight reports it. The dev set doesn't use gvisor |
| `kb_search` returns no chunks, no error | `SERVICE_AUTH_SECRET` differs between `workflow-runtime` and `kb-backend` |
| Every `/api` call returns `503` but Kong `/status` is `200` | Kong is not at fault: kb-backend's Postgres pool stayed "not connected" after Postgres restarted. `/health` now returns `503` in that state so liveness restarts the pod; to fix by hand `kubectl -n dataagent-service rollout restart deploy/kb-backend` |
| A worker's Kafka producer times out on metadata ("Failed to update metadata after 60 secs") though SASL login succeeds | Kafka's ACLs are gone (ACLs live in the broker's metadata, lost with it). Re-run the topics/ACL Job: `kubectl apply -f deploy/k8s/dev/job/kafka-topics.yaml` (delete the old Job first if it still exists) |
| litellm `429 No deployments available` for `embedder`, or `401 Incorrect API key ... platform.openai.com` | `EMBEDDING_V1_BASE_URL` missing from `litellm-env`, so the embedder calls OpenAI and goes into cooldown |
| Silver finishes but an Auto source never reaches Gold; Kafka logs `OutOfOrderSequenceException` for a producer id | The broker no longer agrees with an idempotent producer's id/sequence. `KafkaProducerClient.produce` now rebuilds the producer and retries once; on an image without that fix, restart the pod or publish `kb.promote.gold` by hand |
| A document stays at Silver and shows "Ends at Silver" | Intended: Training/Evaluation data (Data Purpose) is parsed but never promoted to Gold (`libs/ingestion/purpose.py`); change the purpose to Production to send it to the knowledge base |
| `Dockerfile cannot be empty` | A Dockerfile was truncated by a bad edit → `git checkout HEAD -- <path>` |

## 13. Pre-release checklist

- [ ] `ruff check .` and `ruff format --check .` clean
- [ ] `lint-imports` → contracts kept
- [ ] `pytest -m "unit or integration"` green; coverage ≥ `fail_under` in `pyproject.toml`
- [ ] `pytest tests/unit/test_k8s_manifests.py` green
- [ ] `alembic upgrade head` applies cleanly on a fresh DB
- [ ] `cd frontend && npx tsc --noEmit` clean (must run from `frontend/`)
- [ ] CI `security-scan` reviewed (pip-audit + trivy) — no unexplained HIGH/CRITICAL
- [ ] Changes committed, `ansible-playbook deploy.yml` green, §4 checks pass
