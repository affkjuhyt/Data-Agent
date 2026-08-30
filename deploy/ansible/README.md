# Triển khai bằng Ansible — một playbook, mọi môi trường

```bash
cd deploy/ansible
ansible-playbook site.yml                                    # local (inventory mặc định)
ansible-playbook -i inventories/prod site.yml --ask-vault-pass
ansible-playbook site.yml --tags stack,gateway               # chỉ vài bước
ansible-playbook site.yml --tags verify -e run_smoke=true    # health + một lượt hỏi thật
```

Cài Ansible một lần trên máy điều khiển (không cài gì trên server đích ngoài Docker):

```bash
uv tool install ansible-core --python 3.12 --with ansible --with docker   # hoặc pipx / brew
```

## Vì sao là Ansible (chứ không phải Makefile, Argo CD, Kamal…)

| Yêu cầu của dự án | Ansible + Docker Compose | Argo CD (GitOps k8s) | Kamal 2 / Swarm |
|---|---|---|---|
| Local laptop và server on-prem của ngân hàng dùng **cùng một cách** | ✅ `-c local` và SSH, cùng playbook | ❌ cần cụm k8s ngay cả ở local | ✅/⚠ Kamal thay Kong bằng proxy riêng |
| Mạng đóng, air-gapped, không cloud | ✅ agentless qua SSH, gói image offline | ⚠ cần registry + Argo trong mạng | ✅ |
| 16 service + các bước **có trạng thái** (Kong route, Keycloak, Kafka ACL, OpenBao init/unseal/AppRole, seed) | ✅ role idempotent mô tả trạng thái | ❌ Argo chỉ sync manifest; các bước này thành Job/operator viết thêm | ❌ chỉ đẩy container |
| Đội SI/vận hành VN đã quen | ✅ phổ biến nhất mảng config-mgmt | ⚠ chỉ nơi có k8s | ⚠ hệ Rails |
| Rollback / nâng cấp có kiểm soát | ✅ `repo_version`, tag từng bước, `--check --diff` | ✅ mạnh nhất (khi có k8s) | ⚠ |
| Bí mật | ✅ ansible-vault, không có secret trong repo | ✅ (SealedSecrets/ESO) | ⚠ |

Makefile chỉ gọi lệnh shell theo thứ tự — không có inventory, không có trạng thái mong muốn, không có vault,
không chạy được từ xa; những gì `tests/e2e/e2e.sh` phải tự làm (chờ, kiểm tra, idempotent) là việc Ansible
làm sẵn. **Argo CD là bước sau, không phải thay thế**: khi khách có cụm k8s (Phần IV·B của kế hoạch — Helm
umbrella chart, Kong ingress, sandbox node pool), Helm chart + Argo CD quản lý *workload*, còn Ansible vẫn
dựng phần *nền* (node, registry offline, OpenBao, bootstrap Argo). Với appliance một máy (POC, chi nhánh,
mạng đóng) — hình thái bán được sớm nhất — Ansible + Compose là toàn bộ câu chuyện.

## Cấu trúc

```
deploy/ansible/
├── site.yml                     # thứ tự role; mỗi role có tag riêng
├── ansible.cfg
├── roles/
│   ├── common/defaults/main.yml # MỌI giá trị mặc định (ưu tiên thấp nhất → inventory đè được)
│   ├── code/                    # git clone / rsync lên host (bỏ qua khi local)
│   ├── preflight/               # docker, compose v2, .env từ .env.example, ollama + model
│   ├── app_env/                 # GATEWAY_KEY, SERVICE_AUTH_SECRET, KAFKA_ADMIN_PASSWORD, VITE_API_URL
│   ├── compose_stack/           # docker compose up (chỉ build image thiếu), chờ postgres/kong/keycloak/litellm, schema keycloak|kong
│   ├── gateway/                 # deploy/docker/kong/kong-setup.sh (route, JWT, CORS, mật khẩu user dev); Keycloak MFA off (local)
│   ├── kafka_topics/            # deploy/docker/kafka/kafka-setup.sh (topic + ACL)
│   ├── openbao/                 # init/unseal (key lưu 0600 trên host), KV v2, policy, AppRole → .env, secret kho demo
│   ├── seed/                    # tests/e2e/seed.sql + seed-flow.js (chỉ khi seed_demo)
│   └── verify/                  # 401 khi không token, token Keycloak, /api/catalog + /api/reviews qua Kong, (run_smoke) một lượt thật
└── inventories/
    ├── local/                   # hosts.yml (connection local) + group_vars/all.yml (project dataagent-e2e, build never, overlay dev, seed demo)
    └── prod/                    # *.example → copy thành hosts.yml / group_vars/all.yml / group_vars/all/vault.yml (git-ignored)
```

Bí mật: nếu `vault_*` không được định nghĩa, role `common` sinh giá trị **một lần** và lưu vào
`inventories/<env>/.secrets/` (0600, git-ignored) để lần chạy sau không đổi. Production đặt `vault_*` trong
`group_vars/all/vault.yml` và `ansible-vault encrypt`. Key OpenBao nằm trên host đích tại
`<project_dir>/.ansible-secrets/openbao-keys-<project>.json` — **sao lưu** file này; mất nó là mất vault
(playbook sẽ từ chối tiếp tục trừ khi `openbao_allow_reset=true`, chỉ dành cho stack không phải production).

## Prod khác local ở đâu (toàn bộ nằm trong `inventories/prod/group_vars/all.yml`)

* `compose_extra_files: [docker-compose.prod.yml]` — WAL archive, backup sidecar, TLS; không bind-mount source.
* `compose_build: policy|always`, `llm_backend: litellm_config` (LLM hosted / GPU pool, `deploy/docker/litellm/config.yaml`).
* `keycloak_disable_mfa: false`, `seed_demo: false`, `openbao_allow_reset: false`, `run_smoke: true`.
* `kong_public_url` / `keycloak_url` là địa chỉ công bố (issuer trong token phải khớp).

## Gói cài offline (role `bundle`)

```bash
ansible-playbook site.yml --tags bundle                      # dist/bundle/<project>-<git-describe>/
ansible-playbook site.yml --tags bundle -e '{"compose_services":["postgres","redis"],"bundle_include_ollama":false}'  # thử nhanh
```

Kết quả: `images.tar.gz` (chỉ image của `compose_services`, `docker save`), `code.tar.gz` (infra + playbook +
seed + docs — **không** có source, không có `.env`/khóa; có bước tự kiểm tra rò rỉ), `ollama/` (manifest + blob của
`ollama_model`, khi `llm_backend=host_ollama`), `install.sh`, `manifest.json`, `SHA256SUMS`. Image tarball được
cache theo phiên bản (`bundle_reuse_images`) nên đổi cấu hình không phải `docker save` lại.

Trên host mạng đóng (chỉ cần Docker + python3): `./install.sh --dir /opt/data-agent` → kiểm checksum, `docker load`,
chép model, giải nén, chạy playbook local. Hoặc từ máy điều khiển: inventory prod với `code_source: bundle`,
`bundle_path: dist/bundle/<tên>` — role `code` đẩy gói và `docker load` qua SSH.

## Bố cục repo (từ 30-08-2026) — ai giữ gì

```
backend/            services/ · libs/ · alembic/      (import root Python; image build context = repo root)
frontend/           src/ · vite/vitest/tsconfig · Dockerfile.dev   (npm workspace, root package.json chỉ forward)
deploy/
├── docker/         compose + config runtime (kong, kafka, keycloak, openbao, litellm, sql…) — đi theo gói cài; project name pin = infra
├── ansible/        playbook này
└── k8s/            manifest thô cho cụm k8s; Đợt 3 thay bằng helm/
tests/              unit/ · integration/ · e2e/ (e2e.sh, Playwright, seed) · loadtest/ (k6)
docs/  model/  scripts/ (chỉ công cụ dev)
```

Playbook tham chiếu: `compose_dir = deploy/docker`, seed = `tests/e2e/seed.sql` + `seed-flow.js`, bundle đóng gói
`deploy/docker` + `deploy/ansible` + seed + `docs/RUNBOOK.md`/`docs/SECURITY.md`. Đã dọn cùng đợt: `infra/scripts/` (kiểm Kong admin
→ task trong role `verify`, bật bằng `kong_admin_published: false`), toàn bộ `.ps1`, `infra/postgres/` (seed KB → `deploy/docker/sql/kb-seed.sql`),
`scripts/seed_dev_agent.sql`. Overlay `kb-e2e.dev` giờ tự đủ trên base (`--profile runtime`, có `build:` riêng cho model_service/kb-backend/worker).

**Ansible có "đưa k8s vào" được không?** Được, nhưng là **playbook khác** (`deploy/ansible/k8s.yml`, collection
`kubernetes.core`: `helm`/`k8s` module) vì đích khác hẳn: appliance = Docker Compose trên một máy, k8s = cụm có sẵn
của khách. Local vẫn thử được với `kind`/`k3d`, song chạy k8s trên laptop cho 16 service + GPU host là nặng và không
phản ánh cụm ngân hàng — nên mặc định local vẫn là Compose; k8s bật khi có Helm chart.

## Lộ trình

1. **Bây giờ**: appliance một máy = playbook này (local → server thật chỉ đổi inventory); gói offline = `--tags bundle`.
2. **Cụm k8s của ngân hàng**: Helm umbrella chart (kế hoạch Đợt 3) + Argo CD; Ansible bootstrap nền (node, registry
   offline, OpenBao, Argo) rồi bàn giao workload cho GitOps qua `k8s.yml`.
