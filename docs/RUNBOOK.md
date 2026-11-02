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
| Namespaces | `dataagent-database` (stores), `dataagent-service` (everything else) |
| LLM / embed / rerank | GPU services on `10.10.55.101`: vLLM main `:8001` (Qwen3-14B), vLLM fast `:8002` (Qwen3-4B), TEI embed `:8003`, TEI rerank `:8004` |
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
| Prometheus | http://10.10.55.101:30400 |
| Temporal UI: schedules and their workflows (namespace `dataagent`; dev only, production is port-forward) | http://10.10.55.101:30381 |
| LiteLLM | http://10.10.55.101:30500 |
| MinIO console | http://10.10.55.101:30361 |
| Mailpit: the mail the channel gateway sends (kept, never delivered; cluster-only) | `kubectl -n dataagent-service port-forward svc/mailpit 8025:8025`, then http://localhost:8025 |
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
```

### A production install (GEN-04 to GEN-06)

The production manifests carry no demo data and no address of the pilot: an inventory with
`k8s_env: production` sets the organisation (below), `seed_demo: false`, and the addresses
`llm_main_base_url`, `llm_fast_base_url`, `embedding_base_url` (each `http://<host>:<port>/v1`),
`document_embed_url`, `document_rerank_url` (the TEI services, `http://<host>:8003`, `:8004`),
`gpu_egress_cidr` (`<host>/32`) and `frontend_origins` (a list such as `["https://app.example.vn"]`).
Preflight stops on a missing one. The frontend is the built app (`frontend/Dockerfile`); its URLs and
realm come from `frontend-env` and `frontend-secrets` at pod start (`/runtime/config.js`), so one
image serves every install. With `seed_demo: false` db-init applies the grants only, `minio-init`
uploads no document and the realm imports no demo user; turning it off on an install that had it
stops loading demo data but does not delete what is already there.

### The organisation (GEN-07)

Every deploy makes sure the install has its organisation, its default roles and a first admin
(`deploy/ansible/roles/k8s_org`). An inventory other than k8s-local sets, with no default for any of
them: `org_id` (the uuid users carry as Keycloak's `tenant_id`), `org_code`, `org_name`, `org_locale`
(e.g. `vi-VN`), `org_time_zone` (e.g. `Asia/Ho_Chi_Minh`), `org_currency` (e.g. `VND`, kept in
`org.organization.settings`), `org_admin_username`, `org_admin_email`, and in the vault
`vault_org_admin_password`, the admin's temporary password, changed at first login.

| Role | Who | Capabilities |
| --- | --- | --- |
| AC01 | Người dùng nghiệp vụ | ask (an agent is seated with it) |
| AC02 | Lãnh đạo | ask, exec_vault |
| AC03 | Người phụ trách dữ liệu | ask, view_detail, steward, approve |
| AC04 | Kỹ sư dữ liệu | ask, view_detail, upload, prepare_data, admin_data, writeback |
| AC05 | Quản trị hệ thống | admin |
| AC06 | Tuân thủ và pháp chế | compliance, audit_read |
| AC08 | Chuyên viên phân tích | ask, view_detail, upload, prepare_data, run_agent, create_skill |
| AC09 | Người dựng agent | ask, run_agent, create_agent, create_skill |
| AC10 | Kiểm toán nội bộ | audit_read |

The first admin gets AC05 only. Give AC06 to someone else (Quản trị › Người dùng): compliance is the
second approver of what an admin enables. A role an administrator changed is not reset by a later
deploy. Organisation users need `tenant_id` set to `org_id` in Keycloak; the realm keeps that
attribute, editable by admins only.

The organisation self-approval switch is at **Quản trị › Người dùng và vai trò**. Only an
organisation admin, in their own session (not an agent acting for them), can change it; turning it
on asks for confirmation. Each change is an `audit.audit_log` row with action
`organization.updated`, object `organization`, and `before`/`after` `{"allow_self_approval": …}`.

Every approval someone gives their own work while the switch is on is one row with action
`self_approval`; `after->>'step'` says which step it was:

```sql
-- in the audit database (dataagent_audit)
SELECT occurred_at, actor_id, after->>'step' AS step, object_kind, object_id
FROM audit.audit_log
WHERE org_id = :org AND action = 'self_approval'
ORDER BY seq;
```

| Step | What the person approved of their own |
| --- | --- |
| `proposal.review`, `proposal.accept_edit`, `proposal.second_step` | a proposal they made, an edit they wrote, or the second step after their own first |
| `hitl.approve`, `hitl.chain_step`, `hitl.excluded_approver` | a request in Cần bạn they raised, a second step of a chain they already decided, a request whose module named them as not to decide it |
| `dataset_version.certify` | a dataset version they created |
| `knowledge_page.certify` | a knowledge page version they wrote or reviewed |
| `data_product.owner`, `data_product.steward`, `data_product.publish` | naming themselves owner of a product they create, holding owner and steward at once, publishing a P06 product they drafted |
| `data_product.owner.proposed`, `data_product.steward.proposed` | the same, in a change of an active product that waits for review |
| `data_product.activate` | accepting the activation of a product they created, whoever submitted it |
| `example.confirm` | confirming an example they created or whose SQL they last changed |
| `policy.activate` | switching on a policy they wrote |
| `forecast_model.decide`, `formula_value.certify` | a forecast model version or a formula value they made |
| `period_number.approve.proposed` | a manual period number they filed |
| `access_request.decide` | their own access request |
| `model_provider.enable`, `model_provider.approve` | both halves of switching a model provider on |
| `crawl_source.legal_review` | the legal review of a crawl source they own |
| `session_report.approve` | a session report they wrote |

A row means the waiver was used. It is written to the audit database before the approval itself
commits, so in rare cases (the approval failed after the audit write) a row stands for an approval
that did not happen: check the object before acting on it.

## 3. Deploy a change

1. Commit on your branch. **Uncommitted edits are not deployed** (the pipeline
   diffs commits), and the run still reports success.
2. Run the pipeline from the deploy checkout:
   ```bash
   cd /home/ndip/Data-Agent/deploy/ansible
   ansible-playbook deploy.yml
   ```
3. Read the report near the top — `changed: N file(s)`, `secrets: … configmaps: …`,
   then `k8s_plan : Report the plan` (each image and Job with the path that
   triggered it) — to confirm it picked up what you expected. To see that
   before deploying: `ansible-playbook plan.yml` (builds nothing, no cluster).
   Images build 6 at a time; an image that comes out identical is not pushed
   and nothing running it restarts. Build logs: `/data/logs/dataagent-deploy/k8s-local/`.

| Change | Extra flags |
| --- | --- |
| Code in `backend/services/<svc>/` or `frontend/` | none — that image is rebuilt and its workload restarted. A `backend/services/` change also rebuilds `api`, `worker-agent` and `migrate` (their Dockerfiles copy all of `backend/services/`) |
| `backend/libs/` | none — every image that copies `backend/libs` rebuilds (11 of 19) — check disk first (§9) |
| `pyproject.toml`, `requirements-test.txt` | none — no image copies them, so nothing rebuilds |
| New Alembic revision | `-e apply_jobs=true` (reruns `migrate`, then `db-init` for the grants) |
| A Job manifest under `deploy/k8s/dev/job/`, a seed file, `deploy/docker/sql/` | `-e apply_jobs=true` (reruns only the Jobs that read it; `-e jobs_force=true` reruns all) |
| `deploy/docker/kong/kong-setup.sh`, Keycloak scripts or realm | nothing: the setup Job reruns when they change (`setup_inputs`) |
| A value in the vault | `-e secrets_changed=true` (§7) |
| Only one image, skip the rest | `-e images_only=worker-agent` |

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

**Agent turn routes.** `worker-agent` consumes `question.received` from the V3
outbox; every request goes through Kong (`$API`) with `Authorization: Bearer
$TOKEN` and `X-Data-Space`. Kong clears and re-injects identity headers from the
token, so sending your own identity headers is pointless.

| Method + path | Purpose |
| --- | --- |
| `POST /v1/questions` | records a question and returns `202` with `question_id`, `conversation_id` and `stream_url` |
| `GET /v1/questions/{id}` | polls the durable question state: `answered`, `clarifying`, `refused` or `failed` |
| `GET /v1/questions/{id}/stream` | SSE progress and terminal `done` event; reconnect with `Last-Event-ID` |
| `GET /v1/answers/{id}` | reads the answer, figures, trust label and provenance after `done` |

`make bench-e2e` breaks one real turn down per stage (`model/bench/e2e.py`).

**Query result cache.** `worker-agent` reuses a query result for `QUERY_CACHE_TTL_S` seconds
(default 600; `0` turns it off) when the Data Space, the source, the executed SQL (after row
policies and scopes), the column and mask policies applied (and none of them changed since) and the
as-of date are the same
(`backend/services/worker_agent/query/executor.py`). A reused run is still a `query.query_run` row
of its own, with `result_ref = 'query_run:<id of the run it reused>'` and its own copy of the
rows. Tile refreshes, receipt replays, period closes, pipelines and the internal workspace always
read the source. To see
how often answers are served from the cache:

```sql
SELECT count(*) FILTER (WHERE result_ref LIKE 'query_run:%') AS reused, count(*) AS runs
FROM query.query_run WHERE started_at > now() - interval '1 day';
```

**Provider boundary.** Data access behind a question is selected through the worker-agent provider
registry (`services/worker_agent/providers/`). The built-in `relational` provider is the only
provider currently enabled for AdventureWorks and delegates to the existing scoped
`query/executor.py` path. Provider results use the shared v1 envelope so future sources can be
added without changing question/SSE response shapes; the envelope keeps query-run provenance,
trust, warnings, truncation, and JSON-safe values.

## 5. Demo data

With `seed_demo: true` (the dev inventory's setting) the `db-init` Job loads:

| Source | What |
| --- | --- |
| `deploy/docker/sql/v3-demo-seed.sql` | V3 demo organisation directory: `helios-engineer` and the Data Engineer role (capabilities). It opens no Data Space: the former `DS-DEMO` and the synthetic pilot `DS-BH` are archived on a cluster that still has them (their rows stay, hidden), DS-DEMO's membership is removed and the pilot's `workspace.pilot_*` tables are dropped. AdventureWorks is the only test data |
| `deploy/docker/sql/v3-adventureworks-seed.sql` | `DS-AW` "AdventureWorks": the Data Space the product is tested on and its PostgreSQL source, the database `adventureworks_dw` read as `app_user` (OpenBao `demo-postgres`). The data itself is loaded by `make adventureworks` (below); the schema scan, the semantic model (`deploy/docker/semantic/ds-aw.yaml` through `make semantic-import`) and the reviews are left to the product |
| `deploy/docker/sql/v3-adventureworks-context.sql` | `DS-AW`: 28 verified queries (R1-08) and the eval set "Câu hỏi AdventureWorks" (R1-12, the 61 cases of `deploy/docker/eval/ds-aw-cases.yaml`, blind sheet `docs/v3/eval/ds-aw-blind-sheet.md`). Seeded before the scan: a verified query's tables resolve to the scanned datasets when it runs |
| `deploy/docker/sql/v3-adventureworks-documents.sql` | `DS-AW`'s documents, two collections: Vietnamese business documents of the fictional Adventure Works Cycles (metric definitions and the decision that replaces them, promotions with the dates of `dim_promotion`, budget policy, call-centre procedure, a final and a contradicting preliminary quarterly report, a letter carrying a prompt injection) and the eight genuine AdventureWorks product manuals with an English return policy. The files are `deploy/docker/adventureworks-docs/`, put in `da-raw-docs` by `minio-init`; pages and chunks are generated with worker-document's own extractor and chunker by `make adventureworks-documents` (a stale seed fails the worker-document unit suite). `model/eval/cases/retrieval.jsonl` is scored over them (`make eval-retrieval`) |
| `deploy/docker/sql/v3-agent-seed.sql` | `DS-AW`: four agents (biên gộp Internet, doanh thu theo kênh, tồn kho, điều phối), their runs (running, waiting for a person, failed, succeeded, one lead run with two delegated ones), a rolling deployment, one skill waiting for review and one suggestion; the figures in their narratives are AdventureWorksDW's. Re-applied, the runs move back into the last day so the operations screen stays populated. Read by `tests/integration/test_agent_screens_e2e.py` |
| `k8s_openbao` role | OpenBao secret `secret/tenants/<tenant>/demo-postgres`, the demo PostgreSQL source's login |

**AdventureWorksDW** (Microsoft's sample warehouse of a bicycle maker, MIT) is the test dataset. `make adventureworks` fetches it at the commit pinned in `tests/e2e/adventureworks/sources.json` (sha256-checked, cached in `~/.cache/dataagent/adventureworks`), converts the SQL Server script to PostgreSQL (snake_case names) and reloads the database `adventureworks_dw` of the cluster in one transaction, granting `app_user` SELECT only. Then scan the DS-AW source and import its model:

```bash
make adventureworks
make semantic-import SEMANTIC_ORG=a0000000-0000-0000-0000-000000000001 SEMANTIC_SPACE=DS-AW \
     SEMANTIC_AS=helios-engineer SEMANTIC_FILE=deploy/docker/semantic/ds-aw.yaml SEMANTIC_APPLY=1
```

Everything is idempotent. Beyond the DS-AW documents above, knowledge-base **documents** are not seeded; upload them through the UI (the files of `deploy/docker/adventureworks-docs/` are also the upload tests' inputs: html, docx, eml, pdf).

Check it:

```bash
kubectl -n dataagent-database exec postgres-0 -- psql -U admin -d dataagent \
  -Atc "select code, status from org.data_space order by code"
kubectl -n dataagent-database exec postgres-0 -- psql -U admin -d adventureworks_dw \
  -Atc "select 'fact_internet_sales', count(*) from fact_internet_sales"
```

**Reseed** without a full pipeline run (after `create-configmaps.sh` if a seed file changed):

```bash
bash deploy/k8s/create-configmaps.sh        # only if a seed file changed
kubectl -n dataagent-service delete job db-init --ignore-not-found
kubectl apply -f deploy/k8s/dev/job/db-init.yaml
kubectl -n dataagent-service wait --for=condition=complete job/db-init --timeout=10m
kubectl -n dataagent-service logs job/db-init --all-containers --tail=30
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
   The `group_vars` change reapplies the Secrets and restarts the Deployments
   that read one that changed (new `KC_HOSTNAME_URL`, `VITE_*`, `NEXTAUTH_URL`); `apply_setup` updates Kong's
   JWT issuer and CORS.
4. Check with §4. Everyone has to log in again: existing tokens carry the old issuer.

## 7. Secrets

- Passwords live in the ansible-vault file
  `deploy/ansible/inventories/k8s-local/.secrets/vault.yml`:
  `ansible-vault edit deploy/ansible/inventories/k8s-local/.secrets/vault.yml`
  (from `deploy/ansible/`, the vault password is picked up automatically).
- The vault is outside git, so apply an edit with
  `ansible-playbook deploy.yml -e secrets_changed=true`.
- **Do not change** the Postgres admin, Keycloak admin, MinIO or
  ClickHouse passwords on a running stack: those stores only take the password
  when their storage is first created.
- Never run `deploy/k8s/create-secrets.sh` by hand from an env file you haven't
  compared with the cluster. It overwrites every Secret.

Details and the full flow: [`deploy/ansible/README.md` → Secrets](../deploy/ansible/README.md#secrets).

## 8. OpenBao

OpenBao holds warehouse and connector passwords. It **seals itself whenever its
pod restarts** (node reboot, eviction). While sealed, `api`, `worker-agent` and
the crawler cannot read secrets: source calls fail to authenticate and the logs show
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

- Before `deploy_full=true` (rebuilds every image) or a `backend/libs` change
  (rebuilds the 11 images that copy it), make sure usage is under ~70 %.
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
- The chain is covered by the V3 migration suite under `tests/integration/migrate/`.

## 10a. Durable HITL reviews

Human-approval cards are recorded in `runtime.pending_review`, which is forced
through RLS using the transaction-local `app.tenant_id`. `worker-agent` resumes
by the durable conversation id, so a pod restart or a second replica does not
lose the review. If a resume returns `503`, check that the API and worker point
at the V3 database and that the pending-review migration has run; do not delete
the row manually before recovering the run.

## V3 MCP tool calls

MCP calls belong to `worker-agent`, not the retired workflow runtime. The worker
uses the official SDK over Streamable HTTP. A validated plan supplies the endpoint
id, tool name and arguments; the registry filters tools by `allowed_tools` before
discovery and execution. Legacy `POST .../execute` endpoints are refused.

Credentials are transient: `api_key_ref` names an OpenBao secret and the bearer
value must never be stored in Postgres, Redis, a plan, or logs. If discovery or a
call fails, the worker returns the structured `TOOL_UNAVAILABLE` refusal; it must
not silently fall back to another tool.

Useful checks:

```bash
kubectl -n dataagent-service logs deployment/worker-agent --tail=100 \
  | rg 'MCP|TOOL_UNAVAILABLE|OpenBao'
kubectl -n dataagent-database exec statefulset/openbao -- bao status
```

When adding an MCP endpoint, verify its OpenBao `api_key_ref`, protocol and
allow-list in the owning API/plan configuration. Never put the token itself in a
manifest, ConfigMap, plan payload or test fixture.

## 10b. Outbox (`<schema>.outbox`)

A module writes its events to its own `<schema>.outbox` in the transaction that changes the data
(ADR-06/32). The target is Debezium publishing them to Kafka; until Kafka Connect exists,
worker-agent claims them straight from the tables (`services/worker_agent/events.py`, role
`svc_outbox_relay`, `FOR UPDATE SKIP LOCKED`) and stamps `published_at` once its handler has
committed. A crash between the two delays an event instead of losing it; handlers are idempotent.
worker-agent handles `question.received` (qa), `hitl.resolved` (inbox), `writeback.submitted`
(pipeline) and `hitl.requested` from every schema's outbox, the audit database's included.

```sql
-- what is waiting in one schema (should be ~0 and drain within seconds)
SELECT event_type, count(*), min(created_at) FROM qa.outbox WHERE published_at IS NULL GROUP BY 1;
```

## 10c. Nightly eval runs

The Temporal schedule `nightly-evals` (§10e) fires at 02:00 Hà Nội time and worker-agent runs
`nightly_evals.request_all`. It requests one run (`trigger = nightly`, `subject_version =
context@nightly-<day>`) for every active context eval set of an `active` Data Space with an active
steward, requested for that steward; worker-agent answers it from `eval.outbox`, each item as the
user who last wrote it (an item whose author left fails). The one cross-organisation read is the
SECURITY DEFINER `eval.nightly_eval_sets()`. A set that already had a nightly run in the last 20
hours is skipped, so running it again by hand is safe: trigger the schedule from the Temporal UI, or

```bash
kubectl -n dataagent-service exec deploy/worker-agent -- python -m services.worker_agent.nightly_evals
```

## 10d. Crawler (crawl sources, UC-14)

`crawler` (`backend/services/crawler`) works the runs of `/v1/crawl-sources`: it claims
`acquire.run.queued` from `acquire.outbox` (as `svc_outbox_relay`), fetches the source's seed URLs
(a `web_listing` is followed in its `allowed_domains` to `max_depth`), keeps each original in MinIO
`da-raw-crawl` under `<org>/<source>/<sha256>` (object lock, 400 days) and writes `crawl_page`, then
the records of the active schema version to `extracted_record` for review, all as `svc_acquire`.
The run stops at `in_review`; publishing is a person's action. Its event is `acquire.run.completed`.

- **Scope.** One request per second per domain whatever the sources ask, robots.txt honoured,
  redirects followed only inside the declared domains, nothing posted. One replica: the pace is held
  in the process.
- **Egress.** Every crawl request goes through `egress-proxy:8130` (Squid,
  `deploy/k8s/*/service/egress-proxy.yaml`; handed to the HTTP client, so `NO_PROXY` cannot exempt a
  crawl request), on dev as in production. The proxy resolves each destination and refuses an
  internal address, allows ports 80 and 443 only, caches nothing and logs every request to stdout:
  `kubectl -n dataagent-service logs deploy/egress-proxy` (`TCP_TUNNEL/200` went out, `TCP_DENIED/403`
  was refused). In production it is the only pod with a NetworkPolicy to the internet. The crawler
  also refuses internal names (`localhost`, `.svc`, `.cluster.local`, `.internal`, `.local`) and any
  host resolving to a private, loopback, link-local or reserved address, on the first request and
  every redirect; the api refuses such domains on the source. Run without a proxy
  (`CRAWLER_EGRESS_PROXY` empty), the connection is pinned to the checked address instead.
- **API keys.** A source with `auth_mode = api_key` names a key under
  `tenants/<org>/crawl-sources/` of the `secret` mount (the api refuses any other path); the value is
  in field `api_key`. The crawler's own AppRole (`dataagent-crawler-policy`) reads only those paths.
  The key is sent over https only and only to the origin it was first sent to: a redirect to
  another host, port or to http goes without it.
- **Missing intermediate CA** (`CERTIFICATE_VERIFY_FAILED`, e.g. hnx.vn): put the intermediate's
  PEM in a file mounted into the pod and point `CRAWLER_EXTRA_CA_FILE` (`crawler-env`) at it.
- **Blocked source.** Three runs in a row answered 403/429 pause the source
  (`acquire.source.status_changed`, reason `blocked`); resume it from the source screen.
- **A run stuck in `fetching`/`extracting`** after a crawler restart is failed when its event is
  claimed again (reason "abandoned"), which frees the source for a new run.
- **Scheduled runs**: an active source's `schedule_cron` is a Temporal schedule `crawl:<org>:<source>`
  (§10e), read in Hà Nội time; a firing queues a run with trigger `schedule`, skipped while a run
  is still going or once the legal review has lapsed.

## 10e. Temporal (schedules, ADR-33)

Every schedule is a Temporal Schedule (ADR-33); Kafka stays the path of events. worker-agent
(`schedules/`) keeps them in step with the data every 30 s and runs their firings:

| Schedule id | Fires | Zone |
| --- | --- | --- |
| `job:<org>:<job>` | an active ingestion job's `schedule_cron` | UTC |
| `pipeline:<org>:<pipeline>` | an active pipeline's `cron`, skipped on its work calendar's days off | the calendar's, else UTC |
| `report:<org>:<schedule>` | an active report schedule's `cron` | Asia/Ho_Chi_Minh |
| `quality:<org>` | hourly: every active quality rule not checked in 24 h | UTC |
| `crawl:<org>:<source>` | an active crawl source's `schedule_cron`: a run (trigger `schedule`) unless one is still going or its legal review lapsed | Asia/Ho_Chi_Minh |
| `agent:<org>:<trigger>` | an enabled schedule trigger of a `running` agent: a run as the agent's account, held by `no_overlap`, `min_interval` and its work calendar | UTC |
| `nightly-evals` | 02:00: the nightly eval runs (§10c) | Asia/Ho_Chi_Minh |

A firing re-reads its row, so a schedule paused, deleted or re-timed since the last sync fires into
nothing; a minute already served is recognised by the run it made. A firing missed while Temporal or
worker-agent was down is made within 15 minutes, never later. Temporal refuses `L` and `W` in a
cron, so the api does too; a cron restricting both day fields fires on either, as cron always has.
The pieces:

- **Server** `temporal` (`temporalio/server`, one pod running the frontend, history, matching and
  worker roles), its frontend at `temporal:8380`. Its state is in two databases of the shared
  Postgres, `temporal` and `temporal_visibility`, owned by the `temporal` login; `db-init` creates
  them and sets that login's password from `vault_temporal_db_password`.
- **Schema** Job `temporal-schema` (`temporalio/admin-tools`) creates and upgrades their tables; it
  is idempotent and reruns when its manifest changes. **Upgrade the server and admin-tools images
  together, in one commit**: the server refuses a schema older than it expects.
- **UI** `temporal-ui`: http://10.10.55.101:30381 on dev. Production has no NodePort (the UI can
  cancel and reset workflows and has no login of its own):
  `kubectl -n dataagent-service port-forward svc/temporal-ui 8381`.
- **`NUM_HISTORY_SHARDS`** (4 on dev, 512 in production) is fixed when the cluster first starts.
  Changing it later means a new pair of databases; do not edit it on a running cluster.

Check it (the server image has no CLI; admin-tools does):
`kubectl -n dataagent-service run temporal-cli --rm -i --restart=Never
--image=temporalio/admin-tools:1.32.1 -- temporal operator cluster health --address temporal:8380`
answers `SERVING`.

## 10e2. Source-table ingestion (worker-ingest)

A full ingestion job copies a source table into the workspace a batch at a time (RES-16): PostgreSQL
through an asyncpg server-side cursor in a read-only transaction, MySQL through a server-side cursor,
SQL Server through `fetchmany`, ClickHouse as one JSON row per line (`libs/source_drivers.stream`).
The first batch replaces the workspace table and the others append to it, all in one transaction, so
a run that fails half way leaves the previous copy as it was. `INGEST_BATCH_ROWS` (default 5000)
sets the batch, `INGEST_MAX_ROWS` (default 2 000 000) the ceiling: a larger table fails with "more
than N rows; ingest it incrementally" and writes nothing. A driver's column types are read from the
first batch's values; a later value that does not fit (a number column turning to text) fails the run
naming the column.

An incremental job (`mode = incremental`, `cursor_column`) reads only the rows above the cursor it
reached last (RES-17): each run samples `MAX(cursor)` when it starts and reads `cursor > last AND
cursor <= that maximum`, so rows written meanwhile wait for the next run; every batch appends, and
`ingestion_job.cursor_value` (with `job_run.cursor_from/to`) is written in the same transaction as
the rows, never ahead of them. The cursor must only grow: rows updated in place under a "last
modified" cursor are appended again (there is no merge on a key yet), and rows deleted at the source
stay. CDC is not wired.

## 10f. python-sandbox (the agent's analysis code)

The dynamic agent's `analyze` tool (`worker_agent/agentic/tools/analyze.py`) has the model write
pandas over the turn's datasets. That code runs in the `python-sandbox` pod
(`services/python_sandbox`, `libs/code_sandbox`), never in worker-agent:

- `deploy/k8s/*/service/python-sandbox.yaml`: no secret or platform ConfigMap, no service account
  token, read-only root filesystem with an in-memory `/tmp`, user 10001, all capabilities dropped.
- `allow-python-sandbox` (`networkpolicy-service.yaml`): ingress from worker-agent on 8140 only, no
  egress at all (not even DNS). The dev namespace's allow-all policy excludes the pod.
- Inside, `libs/code_sandbox/local.py` still checks the code statically and runs it in a child under
  an audit hook with CPU, memory and time limits: inner layers, not the boundary.

worker-agent's `AGENT_PYTHON_SANDBOX` is `service` (with `AGENT_SANDBOX_URL`) in both manifests;
`off` removes the tool from the planner, `local` runs the code inside worker-agent (a trusted
install only). When the pod is down the tool's step fails and the planner works without it.
Check it: `kubectl -n dataagent-service exec deploy/worker-agent -- python -c "import asyncio,os;
from libs.code_sandbox import client; print(asyncio.run(client.run(os.environ['AGENT_SANDBOX_URL'],
'result = 1 + 1', {})))"`.

## 11. LLM and model backend

The GPU services on `10.10.55.101` run outside k8s: vLLM main `:8001`
(Qwen3-14B-AWQ, LiteLLM role `agent-main`), vLLM fast `:8002` (Qwen3-4B-AWQ,
`agent-fast`), TEI embed `:8003`, TEI rerank `:8004`.

No service runs a local model. The api, worker-document and worker-agent embed through
LiteLLM's `embedder` (`DOCUMENT_EMBED_URL`, else `LITELLM_BASE_URL`), which LiteLLM sends
to TEI `:8003` (`EMBEDDING_V1_BASE_URL`); worker-agent reranks at TEI `:8004` directly
when `DOCUMENT_RERANK_URL` is set (dev only: production's egress policy lets only LiteLLM
reach the GPU box). LiteLLM reaches both vLLM tiers through
`LLM_MAIN_BASE_URL` / `LLM_FAST_BASE_URL` (`litellm-env`); its model routing is
`deploy/docker/litellm/config.yaml` (ConfigMap `litellm-config`). Embeddings are `bge-m3` (1024-dim): a knowledge base
ingested against another embedding model must be re-ingested.

worker-agent (`services/worker_agent/llm.py`) calls LiteLLM through PydanticAI. Every call names the
Pydantic model its answer must fit and sends that model's JSON schema as
`response_format: json_schema`, which vLLM enforces with its structured-outputs
backend (xgrammar by default) — LiteLLM passes it through. The OpenAI SDK retries
429/5xx and dropped connections twice with backoff; an answer that still does not
validate is sent back once with its errors. Past that worker-agent raises
`ModelUnavailable` and keeps to its deterministic path (template text, extractive
answer, no model reading), so a run's trace showing `UnexpectedModelBehavior` means
the model could not produce the schema — check that the vLLM tier accepts
`response_format` (a 400 from vLLM shows as `ModelHTTPError`) before blaming the prompt.

V3 documents (UC-05-02/04) use the same TEI services directly on the local slice,
which has no LiteLLM: `DOCUMENT_EMBED_URL` (`:8003`) in `worker-document-env`,
`api-env` and `worker-agent-env`, and `DOCUMENT_RERANK_URL` (`:8004`) in
`worker-agent-env`; unset, both fall back to `LITELLM_BASE_URL` (embeddings) or to
no reranker. worker-document writes one Qdrant collection per document collection
(`collection.qdrant_collection`, point id = chunk id). When the embedding service
is down a document still becomes ready, keyword-only, with `chunk.qdrant_point_id`
NULL; the next processing of that document embeds those chunks. A scanned page
below OCR confidence 0,85 leaves the document `waiting_review` with a `low_ocr`
issue in the quality queue until someone corrects or drops the page.

If retrieval quality is poor, first check the GPU services answer, before
suspecting the pipeline.

### What a question cost (the turn trace)

Every Ask turn is written as a run of the Data Space's built-in agent "Agent Hỏi
đáp" (`agent.agent_def` code `QA`, created by the worker on the space's first
question, retired so that it can never be configured or run: turns run as the
asker; an agent a person created with code `QA` is not used, and turns are then
left untraced with a warning in the worker log):
one `agent.agent_run` with `trigger_ref = question:<id>`, whose `agent_step`
rows are the analyst steps (understand → query → narrate) and every model call
(`kind = llm_call`, `output` = model, tokens in and out, latency; never the
prompt, the answer or an error's message, only its type and HTTP status). `agent_run.tokens` and `cost` are the turn's totals.
Cost prices tokens by `LLM_PRICES` in `worker-agent-env`, JSON per model or role
alias, e.g. `{"responder": {"input_per_1k": 0.002, "output_per_1k": 0.006}}`;
unset, calls cost 0, which is what the on-prem models cost per call.

```sql
SELECT r.trigger_ref, r.status, r.tokens, r.cost, s.seq, s.kind, s.name, s.duration_ms, s.output
FROM agent.agent_run r JOIN agent.agent_step s ON s.run_id = r.id
WHERE r.trigger_ref = 'question:<id>' ORDER BY s.seq;
```

A trace that cannot be written is logged ("the trace of question … was not
written") and never fails the answer.

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
| `worker-agent` does not answer a question | Check `kubectl -n dataagent-service logs deployment/worker-agent`; then inspect the question's terminal refusal and the `qa.outbox`/Redis SSE path |
| A worker's Kafka producer times out on metadata ("Failed to update metadata after 60 secs") though SASL login succeeds | Kafka's ACLs are gone (ACLs live in the broker's metadata, lost with it). No V3 service is a Kafka client yet; when one is, its topics and ACLs need a Job that creates them (the old `kafka-topics` Job only dropped legacy topics and was removed on 01/11/2026) |
| litellm `429 No deployments available` for `embedder`, or `401 Incorrect API key ... platform.openai.com` | `EMBEDDING_V1_BASE_URL` missing from `litellm-env`, so the embedder calls OpenAI and goes into cooldown |
| `/v1/questions` returns `502` | Check `api` is Ready and not `OOMKilled`; then check Kong upstream logs and `worker-agent` logs. Do not restore the retired workflow runtime |
| The card says "Hệ thống chưa lập được cách trả lời…" (`PLANNER_ERROR`) | The planner model returned no valid plan twice (the second time at temperature 0). Check the `planner \| response is not a valid plan` lines for that conversation; usually `planner_max_tokens` too low or the model endpoint degraded |
| `Dockerfile cannot be empty` | A Dockerfile was truncated by a bad edit → `git checkout HEAD -- <path>` |

## 13. Pre-release checklist

- [ ] `make lint`: no new ruff findings in the files you touched (the tree is not clean yet, `CLAUDE.md` §1)
- [ ] `lint-imports` → contracts kept
- [ ] `make test` (unit then integration, one suite per service image) green; CI's combined coverage ≥ `fail_under` in `pyproject.toml`
- [ ] `make e2e` against the dev cluster after deploying (it writes data — see `tests/README.md`)
- [ ] `make frontend-lint` and `make frontend-test` clean
- [ ] CI `security-scan` reviewed (pip-audit + trivy) — no unexplained HIGH/CRITICAL
- [ ] Changes committed, `ansible-playbook deploy.yml` green, §4 checks pass
