# Ansible — deploying Data Agent to Kubernetes

One playbook, [`deploy.yml`](deploy.yml), deploys the whole stack from **this
checkout** to the cluster in your kubeconfig. It is incremental: it diffs git
against the last successful deploy and only rebuilds, reapplies and restarts
what that change touches.

- Operating the dev server (URLs, logins, demo data, OpenBao, disk):
  [`docs/RUNBOOK.md`](../../docs/RUNBOOK.md)
- Manifest design (namespaces, ports, network policy, the dev/production split):
  [`deploy/k8s/README.md`](../k8s/README.md)

```bash
cd deploy/ansible
ansible-playbook deploy.yml                                         # normal run
ansible-playbook deploy.yml -e apply_jobs=true -e apply_setup=true  # also allow Jobs + gateway setup

# macOS/OrbStack local standard
make deploy-local
```

Run it on the machine that holds the kubeconfig and the Docker daemon — the
inventory uses `ansible_connection: local`. [`ansible.cfg`](ansible.cfg) selects
`inventories/k8s-local` and reads the vault password from
`inventories/k8s-local/.secrets/vault_pass`. Run it with stdin redirected
(`</dev/null`) when calling it from a non-interactive shell; Ansible refuses
non-blocking stdio.

> **Only committed changes are deployed.** Change detection is
> `git diff <last deployed sha> HEAD`. An uncommitted edit is invisible: no image
> is rebuilt, no Secret or ConfigMap is reapplied, no Job reruns — and the run
> still reports success. Commit first (a local commit is enough). See
> [Uncommitted changes](#uncommitted-changes) for the escape hatches.

## What a run does

Everything is in `deploy.yml`, top to bottom. Settings are role defaults in
[`roles/k8s_common/defaults/main.yml`](roles/k8s_common/defaults/main.yml),
overridden by the inventory's `group_vars/all/`.

| # | Step | Runs when |
| --- | --- | --- |
| 1 | **Preflight** (`k8s_preflight`): kubectl, API server, nodes Ready, required files, reports whether RuntimeClass `gvisor` exists | always |
| 2 | **Git sync**: `git switch <git_branch>` then `git pull <git_remote> <git_branch>`. On failure it aborts the merge, writes `~/.dataagent-deploy/<env>/last-pull-failure.log`, and stops before touching anything | `git_sync_enabled` |
| 3 | **Diff** against the checkpoint `~/.dataagent-deploy/<env>/last_deployed_sha.txt`. No checkpoint, or a sha no longer in history, means a full deploy | always |
| 4 | **Plan** (`k8s_plan`): an image is rebuilt when a changed path falls under one of its inputs — its Dockerfile, every `COPY`/`ADD` source resolved against its build context, and that context's `.dockerignore`. So a `backend/libs` change rebuilds the images that copy `backend/libs` (not keycloak, kong, setup, frontend or the sandbox images; model_service copies only `backend/libs/basemodel`), and an edit under `backend/services/<service>/` rebuilds only that service's image (each copies its own tree; `migrate` copies only `backend/alembic`, the V3 baseline and its generated SQL). Images in `images_on_demand` (`eval`, the hand-applied online-eval Job) are skipped unless `images_only` names them. Also picks the Jobs step 10 would rerun. Prints both with the path that triggered each | always |
| 4b | **Build** (`k8s_build`): `build_parallelism` images at a time (default 6), one log per image in `build_log_dir`; a failure prints the tail of that image's log. Tagged `dataagent/<name>:latest`, re-tagged for `image_registry` and pushed **only if the registry does not already serve that digest** | something to build |
| 5 | **Secrets**: render `secrets.env` from the vault (every run), then reapply every Secret and `kafka-jaas` | reapply only when a path in `secret_paths` changed |
| 6 | **Base manifests**: namespaces, RBAC, NetworkPolicies, `configmap.yaml` (every run); rerun `create-configmaps.sh` | file-backed ConfigMaps only when a path in `config_paths` changed |
| 7 | **Database tier**: apply `database/`, wait for every store and pgbouncer | `apply_databases` **and** a file under `database/` or `deploy/ansible/` changed (or `deploy_full`) |
| 8 | **OpenBao** (`k8s_openbao`): unseal, KV v2 + transit, read policy, demo warehouse password (if `seed_demo`), AppRole. A secret-id is minted only on the first run or with `openbao_rotate_approle` | `apply_databases` |
| 9 | **Service tier**: apply `service/`, wait for Keycloak and Kong | `apply_services` **and** a file under `service/` or `deploy/ansible/` changed (or `deploy_full`); a new image is restarted by digest in step 11 either way |
| 10 | **Jobs**: delete and rerun, migrations (`kong-migration`, `migrate`) first, then the init Jobs (`db-init`, `minio-init`, `kafka-topics`, `seed`) — **only the ones whose inputs changed**: the Job's own manifest or an entry in `job_inputs` (e.g. `migrate` ← `backend/alembic`, `seed` ← the seed files, `kong-migration` ← the kong image, `db-init` ← its SQL and a `migrate` rerun). Each wait ends at `Complete` **or** `Failed` (polled every `job_poll` s), so a failing Job shows its logs within seconds instead of after `wait_job` | `apply_jobs` **and** (a Job's inputs changed, a full deploy, or `jobs_force`) |
| 11 | **Restart** every Deployment/StatefulSet running a rebuilt image **whose running pods are on a different digest** — a rebuild that came out identical restarts nothing | an image was built |
| 12 | **Restart for config**: the service tier's Secrets and ConfigMaps are hashed before step 5 and after step 9; only the Deployments that read one whose content changed (`envFrom`, `env.valueFrom`, volumes) are restarted | `apply_services` and some content changed |
| 12b | **Wait** for every restarted workload, all at once; the gateway is waited on only when Keycloak/Kong changed, so an API/worker deploy does not block on the gateway | something restarted |
| 13 | **Gateway setup** (`k8s_setup`): rerun the `setup` Job — Kong services/routes/plugins/CORS, the JWT issuer, Keycloak audience mappers, dev user passwords — and apply the backup CronJob | `apply_setup` |
| 14 | **Verify** (`k8s_verify`), then write the checkpoint | always; the checkpoint only advances if every step above succeeded |

Why restarts are explicit: a rebuilt image keeps the same `:latest` reference,
so `kubectl apply` sees an identical spec and changes nothing; and a Secret or
ConfigMap is only read when a pod starts.

Why the push and the restart compare digests rather than "was it rebuilt":
images are built with `--provenance=false --sbom=false` (`image_build_flags`).
With the default provenance attestation every build — even a fully cached one —
got a new digest, so every rebuild was pushed and restarted everything running
it. Now identical inputs give an identical digest; the registry's digest decides
the push and the running pods' digest decides the restart, which also means a
run that failed after pushing still restarts on the retry. The first run after
this change restarts every workload once (the digests move from the attested
index to the plain manifest).

Every Python Dockerfile installs with `RUN --mount=type=cache,target=/root/.cache/pip`
(and so without `--no-cache-dir`/`PIP_NO_CACHE_DIR`): a changed `requirements.txt`
re-installs from BuildKit's cache instead of downloading torch and docling again.
The cache is not part of the image.

When `image_registry` is set, manifests are piped through a `sed` that prefixes
`image: dataagent/<name>:latest` with the registry and turns
`imagePullPolicy: IfNotPresent` into `Always`, so a restart pulls the new
`:latest`. The dev manifests already spell `localhost:5000/dataagent/<name>:latest`.

## Inventory `k8s-local`

The only inventory. It deploys the single-node dev server from
[`inventories/k8s-local/group_vars/all/main.yml`](inventories/k8s-local/group_vars/all/main.yml):

| Setting | Value | Why |
| --- | --- | --- |
| `k8s_env` | `dev` | applies `deploy/k8s/dev/` |
| `secrets_env_file`, `openbao_keys_file`, `openbao_env_file` | under `deploy/k8s/dev/` | server-only state, git-ignored |
| `image_registry` / `image_push` | `localhost:5001` / `true` | OrbStack pulls from the local registry |
| `git_sync_enabled` | `false` | people edit code in this same checkout; a sync would `git switch main` and pull into their working tree |
| `git_branch` | `main` | used only if sync is turned on |
| `apply_databases` / `apply_services` | `true` | |
| `service_manifests` | `[]` | Empty applies every service manifest; set names such as `api,worker-agent,keycloak,kong,frontend` for the V3 local slice |
| `deploy_image_allowlist` | `[]` | Empty uses change detection for every image; set `api,worker-agent,migrate,frontend,kong,keycloak` to make a staged V3 deploy unable to build legacy images |
| `database_manifests` | `[]` | Empty applies every database manifest; staged V3 can use `postgres,pgbouncer,openbao` |
| `apply_jobs` / `apply_setup` | `false` | Jobs rerun migrations, reset role passwords and rebuild demo tables; setup rewrites Kong config. Opt in per run with `-e` |
| `seed_demo` | `true` | the `seed` Job loads demo data (see RUNBOOK) |
| `wait_rollout` | `180` | only changed/restarted workloads are waited on; gateway waits happen only for a gateway change |
| `build_log_dir` | `$TMPDIR/dataagent-deploy/k8s-local` | per-image build/push logs on macOS |
| `public_api_url`, `keycloak_public_url`, `langfuse_public_url` | `http://10.10.55.101:30100`, `:30110`, `:30420` | the URLs **browsers** use — see below |

The three public URLs must be reachable from a user's browser. They end up in
`VITE_API_URL` / `VITE_KEYCLOAK_URL` (frontend), `KC_HOSTNAME_URL` (the `iss`
claim Keycloak puts in every token), Kong's JWT `issuer`, and Langfuse's
`NEXTAUTH_URL`. Setting them to `localhost:8100` / `:8110` makes every browser
outside the server fail with "Connecting to Keycloak…" and
`ERR_CONNECTION_REFUSED`. To change them, follow
[RUNBOOK → Change the public URL](../../docs/RUNBOOK.md#6-change-the-public-url).

## Settings you pass with `-e`

| Flag | Effect |
| --- | --- |
| `apply_jobs=true` | allow step 10. Reruns only the Jobs whose inputs changed (see `job_inputs` in the role defaults) |
| `jobs_force=true` | with `apply_jobs=true`, rerun all six Jobs whatever changed — e.g. after a vault change to the app-role passwords that `db-init` sets |
| `build_parallelism=N` | how many images build (and push) at once; default 6 |
| `apply_setup=true` | run step 13. Needed after changing the public URLs, `kong-setup.sh` or the Keycloak mappers |
| `images_only=kb-backend,frontend` | narrow the build list to these image names |
| `images_only=eval` | build an on-demand image (`images_on_demand`) — before applying the online-eval Job |
| `deploy_full=true` | treat everything as changed: rebuild **all** images, reapply all Secrets and ConfigMaps, rerun Jobs (with `apply_jobs`). Images whose digest comes out unchanged are neither pushed nor restarted. Needs tens of GB of free disk — check `df -h /data` first |
| `seed_demo=false` | run the `seed` Job without demo data |
| `openbao_rotate_approle=true` | mint a new AppRole secret-id and reapply the Secrets that carry it; restart `workflow-runtime` and `auth-api` afterwards |
| `openbao_allow_reset=true` | allow re-initialising OpenBao when the keys file is missing. **Destroys every secret in the vault** — throwaway clusters only |
| `git_sync_enabled=true` | pull before deploying (see step 2) |

## Secrets

```
.secrets/vault.yml                 raw passwords (vault_*), ansible-vault encrypted
   └─ group_vars/all/secrets.yml   derives every DSN, MINIO_URL, NEO4J_AUTH, the public URLs → secret_env
        └─ templates/secrets.env.j2 + OpenBao AppRole creds → deploy/k8s/dev/secrets.env (every run)
             └─ deploy/k8s/create-secrets.sh → one <workload>-secrets per workload, only the keys it reads
```

- The vault lives in `inventories/k8s-local/.secrets/` and is **not in git**
  (`.gitignore`: `deploy/ansible/inventories/*/.secrets/`). The `.gitignore`
  comment about committing an encrypted `vault.yml` describes an older layout.
- Keys are listed in [`group_vars/all/vault.yml.example`](inventories/k8s-local/group_vars/all/vault.yml.example).
  Edit with `ansible-vault edit inventories/k8s-local/.secrets/vault.yml`.
- Because the vault is outside git, **editing it does not trigger a reapply.**
  Run with `-e secrets_changed=true` (Secrets are reapplied and the service tier
  restarted in the same run).
- Postgres admin, Keycloak admin, MinIO, Neo4j and ClickHouse read their password
  only when their storage is first initialised. Changing one afterwards updates
  the Secret but not the store, and the services lose access. The Postgres app
  roles (`app_user`, `runtime_writer`, `sandbox_reader`, `kb_backend`,
  `pgbouncer_auth`, `kb_ingestion_worker`) are different: the `db-init` Job sets
  their passwords on every run.
- `OPENBAO_TOKEN` / `OPENBAO_ROLE_ID` / `OPENBAO_SECRET_ID` are not in the vault;
  `k8s_openbao` writes them to `deploy/k8s/dev/.openbao-env.yml`.
- `.secrets/secrets.env.vault` is a leftover from the previous pipeline. Nothing reads it.
- Do not run `create-secrets.sh` by hand from some other env file. Every Secret
  is overwritten from that file; if it came from a different generation, the
  services lose their database, Kafka and gateway credentials at the next restart.

## Server-only state

None of this is in git. Losing the first three means losing the stack's secrets.

| Path | Holds | If lost |
| --- | --- | --- |
| `deploy/ansible/inventories/k8s-local/.secrets/vault.yml` | every password | unrecoverable — back it up |
| `deploy/ansible/inventories/k8s-local/.secrets/vault_pass` | the vault password | the vault can't be opened |
| `deploy/k8s/dev/.openbao-keys.json` | OpenBao unseal key + root token | **the vault can never be unsealed again**; the only way out is wiping the OpenBao PVC and every secret in it |
| `deploy/k8s/dev/.openbao-env.yml` | minted AppRole role-id / secret-id | regenerate with `-e openbao_rotate_approle=true` |
| `deploy/k8s/dev/secrets.env` | rendered Secrets source | regenerated on every run |
| `~/.dataagent-deploy/k8s-local/last_deployed_sha.txt` | checkpoint | next run is a full deploy |
| `~/.dataagent-deploy/k8s-local/last-pull-failure.log` | last failed git sync | — |
| `/data/logs/dataagent-deploy/k8s-local/<image>.log`, `<image>.push.log`, `results.txt` | the last build/push of each image (`build_log_dir`) | — |
| `/data/logs/ansible-deploy.log` | every run's output with timestamps (`log_path` in `ansible.cfg`) | — |

The checkpoint lives in `$HOME`, keyed by `env_name` — **not** by checkout. Two
checkouts using this inventory share it, and share the cluster.

## Uncommitted changes

Commit, then run. If you cannot commit yet:

| Option | Cost |
| --- | --- |
| `-e deploy_full=true` | rebuilds every image — slow and disk-heavy |
| `-e secrets_changed=true -e config_changed=true` | reapplies every Secret and file-backed ConfigMap, restarts the Deployments whose Secret/ConfigMap content changed; builds nothing |
| add `-e '{"changed_paths": ["deploy/k8s/dev/job/seed.yaml"]}'` (with `apply_jobs=true`) | also lets the Jobs rerun |

These overrides work because `-e` outranks the diff result. The checkpoint
still advances to `HEAD`, so the next run will not see those files as changed
either: commit them before relying on the pipeline again.

## Checking a plan

[`plan.yml`](plan.yml) runs only the decision step: which images a diff would
rebuild and which Jobs it would rerun, each with the path that triggered it. It
builds nothing and never talks to the cluster.

```bash
ansible-playbook plan.yml                                    # checkpoint..HEAD, as deploy.yml would
ansible-playbook plan.yml -e plan_from=HEAD~3
ansible-playbook plan.yml -e '{"changed_paths": ["backend/alembic/versions/x.py"]}'
```

The logic lives in [`filter_plugins/deploy_plan.py`](filter_plugins/deploy_plan.py)
and is covered by the deploy-plan unit tests.

## Logs and timings

`ansible.cfg` writes every run, timestamped, to `/data/logs/ansible-deploy.log`
(Ansible warns and skips logging if `/data/logs` is missing) and enables the
`ansible.posix.profile_tasks` callback, which prints how long each task took.
That callback is not part of ansible-core; install it once per user with
`ansible-galaxy collection install -r requirements.yml` (goes to
`~/.ansible/collections`). Until then Ansible prints "Skipping callback plugin
'ansible.posix.profile_tasks'" and runs as before.

## Moving the deploy to another checkout

Done once, when the stack moved from `~/Data-Agent-k8s` to `~/Data-Agent`:

1. Make sure the new checkout contains the checkpoint commit
   (`git cat-file -e $(cat ~/.dataagent-deploy/k8s-local/last_deployed_sha.txt)`),
   otherwise the first run is a full deploy.
2. Copy the server-only state — `inventories/k8s-local/.secrets/` and
   `deploy/k8s/dev/{secrets.env,.openbao-keys.json,.openbao-env.yml}` — with
   `cp -p`, then check that `git status --ignored` lists each as ignored (`!!`).
3. Run `ansible-playbook deploy.yml` with no overrides. Expect
   `changed: 0 file(s)` and `building: []` when both checkouts are at the
   same commit. Compare the freshly rendered `deploy/k8s/dev/secrets.env` with
   the old one (`cmp`); they must be identical.
4. Stop deploying from the old checkout.

## Recovering

| Symptom | Fix |
| --- | --- |
| A run failed — were those changes skipped? | No. The checkpoint only advances on success; fix the cause and rerun |
| Run succeeded but nothing changed in the cluster | The change isn't committed — see [Uncommitted changes](#uncommitted-changes) |
| `Wait for the … Jobs` fails | The Job reached `Failed` (after `backoffLimit: 2` retries); its logs are printed right after. If it times out instead, the pod never ran: `kubectl -n dataagent-service get pods -l job-name=<job>`; `ImagePullBackOff` means the image is gone (e.g. `minio/mc`, now replaced by the pinned MinIO server image) |
| `Build the images` fails | The task prints the last 40 lines of each failed image's log; the full log is `<build_log_dir>/<image>.log`. The other images finished building, so the rerun is mostly cache hits |
| A Job did not rerun though you expected it to | It reruns only when its inputs changed (`ansible-playbook plan.yml` shows why). Force all with `-e apply_jobs=true -e jobs_force=true` |
| `field is immutable` on a Job | A Job's pod template can't change; the pipeline deletes Jobs before applying. By hand: `kubectl -n dataagent-service delete job <name>` first |
| `Pull failed` | Read `~/.dataagent-deploy/k8s-local/last-pull-failure.log`, reconcile the tree by hand, rerun |
| Everything rebuilt unexpectedly | The checkpoint sha isn't in this checkout's history (new checkout, force-push). Expected once |
| `OpenBao is initialised but … openbao-keys.json is missing` | Restore the keys file from backup. `openbao_allow_reset=true` wipes the vault |
| Tokens rejected with 401 after changing the public URL | Kong still has the old `issuer`: rerun with `-e apply_setup=true` |
