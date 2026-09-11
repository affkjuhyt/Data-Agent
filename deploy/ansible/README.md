# Ansible — Data Agent on Kubernetes

```bash
cd deploy/ansible
export ANSIBLE_VAULT_PASSWORD_FILE=~/.dataagent-vault-pass
ansible-playbook deploy.yml
```

One playbook, read top to bottom:

```
pull  ->  diff  ->  build what changed  ->  apply everything  ->  restart what changed
```

Everything is in [deploy.yml](deploy.yml). Settings are in
[roles/k8s_common/defaults/main.yml](roles/k8s_common/defaults/main.yml) — a
role default, so any inventory's `group_vars` can override it. The only roles
left are the ones that do something genuinely intricate (`k8s_openbao`,
`k8s_setup`) or purely informational (`k8s_preflight`, `k8s_verify`).

`ansible.cfg` defaults to `inventories/k8s-local` (`ansible_connection: local` —
run it on the machine with the kubeconfig and the Docker daemon).

## 1. Pull

```
git fetch --prune origin main
git merge --ff-only origin/main
```

Fast-forward only, and that is the entire safety story: git either replays the
remote commits onto an untouched tree, or it **refuses before writing a byte**.
There is no merge to resolve, no conflict state to clean up, and nothing local
is ever discarded.

A refused pull stops the run before anything is built, applied or restarted,
and writes `~/.dataagent-deploy/<env>/last-pull-failure.log` with git's own
output, the working-tree status, the commits you have that the remote does not,
and the commits waiting to come in. The two reasons it happens:

* **the tree has diverged** — local commits that are not on `origin/main`;
* **local changes would be overwritten** by an incoming commit.

Reconciling either is the operator's call, deliberately not this playbook's.
Reset, stash, commit or push by hand, then rerun.

Ansible reads `group_vars` when the play starts, before the pull, so the play
re-reads `inventories/<env>/group_vars/all` from the pulled tree right after it.
A pulled secret or setting therefore lands in the same run. A pulled change to
`deploy.yml` or a role still runs the old tasks until the next run.

`inventories/k8s-local` sets `git_sync_enabled: false` — that inventory is a
developer's own machine, where the tree is normally dirty or ahead and a
fast-forward would just refuse every run.

## 2. Diff

`git diff --name-only <checkpoint> HEAD` — one diff, read three ways:

| Bucket | Test | Effect |
| --- | --- | --- |
| code | a changed directory (or any parent of it) contains a `Dockerfile` | build that image |
| secrets | a path under `secret_paths` | rerun `create-secrets.sh` |
| config | a path under `config_paths` | rerun `create-configmaps.sh` |

The checkpoint is `~/.dataagent-deploy/<env>/last_deployed_sha.txt` — one file,
one sha, outside the repo because it is not repo content and has to survive
whatever you do to the tree to unblock a refused pull.
Written **last**, so a run that fails anywhere redeploys the same commit next
time instead of skipping past what it never applied. Delete it (or pass
`-e deploy_full=true`) to force a full deploy.

## 3. Build

Changed paths → their directories and every parent → `stat` for a `Dockerfile`
→ that is the build list. The parents matter: a change to
`backend/services/auth_api/routes/v1/users.py` has to find
`backend/services/auth_api/Dockerfile`.

**`shared_paths` is the exception that makes this correct.** `backend/libs/`,
`pyproject.toml` and `uv.lock` have no `Dockerfile` next to them, but they sit
inside the build context of every image that builds from the repo root. A pure
per-directory rule would ship a `backend/libs/` change to nobody, so a change
there rebuilds everything.

Image name = directory name, hyphenated if that is what `deploy/k8s/*.yaml`
asks for — the manifests are not consistent (`kb_backend` ships as
`kb-backend`, `workflow_runtime` as `workflow_runtime`), so both spellings are
tried and the manifests arbitrate. Only `backend/alembic` → `migrate` is
spelled out, in `image_name_overrides`. Build context is the repo root unless
the directory is listed in `image_self_context`.

Tags are always `dataagent/<name>:latest` — no registry prefix, exactly as the
manifests spell it. Where the bytes go is separate:

| Variable | Effect |
| --- | --- |
| `image_import` | pipe `docker save` into the node's own store, e.g. `sudo k3s ctr images import -` |
| `image_registry` | also tag and push `<registry>/dataagent/<name>:latest` |
| `image_push` | push the bare `dataagent/<name>:latest` |

> Every workload sets `imagePullPolicy: IfNotPresent`, so a node that already
> cached a `:latest` tag **never re-pulls it**. Pushing to a remote registry
> alone is not enough on such a node — import into the node store (what
> `k8s-local` does) or run a registry the node treats as a mirror. This is the
> most likely reason a deploy "succeeds" and the old code keeps running.

## 4. Secrets and ConfigMaps

Secrets come from the encrypted `inventories/<env>/group_vars/all/vault.yml`,
which holds raw passwords and nothing composite. `group_vars/all/secrets.yml`
derives every DSN, `MINIO_URL` and `NEO4J_AUTH` from them, so a password and
the URL embedding it cannot drift apart. Those values render into
`deploy/k8s/secrets.env`, and `create-secrets.sh` decides which key reaches
which workload — that script is unchanged.

```bash
cd inventories/k8s-local/group_vars/all
cp vault.yml.example vault.yml
$EDITOR vault.yml                     # passwords only; DSNs are derived
ansible-vault encrypt vault.yml       # then commit it
ansible-vault edit vault.yml          # from then on
```

The **encrypted** vault is meant to be committed — it is ciphertext, and
committing it is what lets `git diff` say "the secrets changed in this pull".
Only the password stays out of the repo, and the playbook refuses to run if
`vault.yml` turns out to be plaintext.

When the diff says secrets moved, all of them are reapplied. No per-Secret
comparison: `kubectl apply` on an unchanged Secret does nothing to the running
pods, and 36 applies take about a second. ConfigMaps work the same way.

`OPENBAO_TOKEN` / `ROLE_ID` / `SECRET_ID` are not in the vault — they only
exist once a cluster does. `k8s_openbao` mints them into
`~/.dataagent-deploy/<env>/openbao.yml`, and the Secrets carrying them are
reapplied on the run that mints them.

### Migrating from the old `.secrets/secrets.env`

For each `vault_*` key in `vault.yml.example`, copy the matching password from
the old file (`vault_postgres_password` from `POSTGRES_PASSWORD`, and so on).
Do **not** copy the DSNs, `MINIO_URL` or `NEO4J_AUTH` — those are rebuilt from
the passwords. `OPENBAO_*` are not copied either.

## 5. Apply, then restart

Manifests are applied **every run**, never gated on the diff. `kubectl apply`
compares server-side and only moves a pod when the spec actually differs, so it
is already the change detector — running our own on top of it just adds a way
to be wrong, and would let a manual `kubectl edit` or a hand-deleted resource
drift out of sight forever.

That handles manifest changes. It does **not** handle the other two, and this
is worth being precise about:

* a rebuilt image keeps the same `dataagent/<name>:latest` reference, so the
  Deployment spec is byte-identical and apply is a no-op — the pod keeps
  running the old image;
* a Secret or ConfigMap is only read when a pod starts.

So after the apply there is one restart step: the workloads running a rebuilt
image are found by asking the cluster which ones use that image reference, and
restarted; and the service tier is restarted if any Secret or ConfigMap moved.
Nothing else is touched.

Jobs are delete-then-apply (their pod template is immutable) and only rerun on
a full deploy, when the `migrate` image was rebuilt, or when something under
`deploy/k8s/production/job/` changed — so an Alembic revision reruns migrations and a
frontend change does not.

## Inventories

`inventories/k8s-local/` is the dev server: it applies `deploy/k8s/dev/`
(`k8s_env: dev`), pushes to the `localhost:5000` registry, and keeps
`secrets.env`, the OpenBao keys and the minted AppRole credentials inside
`deploy/k8s/dev/`, all git-ignored. The hardening checks in `k8s_verify` run
only for `production`. A deploy inventory sets
`git_sync_enabled: true`, `apply_jobs` / `apply_services` / `apply_setup: true`,
and whichever of `image_push` / `image_registry` / `image_import` matches how
its nodes get images.

## Recovering

| Symptom | Fix |
| --- | --- |
| a deploy failed — did it skip those changes? | no: the checkpoint only advances on success. Rerun. |
| `Pull refused` | read `~/.dataagent-deploy/<env>/last-pull-failure.log`; reconcile the tree by hand, then rerun |
| everything rebuilt unexpectedly | the checkpoint sha is unreachable (force-push or GC). Expected, once. |
| new code, old pods | `IfNotPresent` plus a cached `:latest` — see the note in section 3 |
| a Secret looks stale | `ansible-playbook deploy.yml -e deploy_full=true` |
| `OpenBao is initialised but openbao-keys.json is missing` | restore the key file, or `-e openbao_allow_reset=true` on a throwaway cluster |
