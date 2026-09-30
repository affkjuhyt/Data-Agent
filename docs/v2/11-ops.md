# 11. Vận hành V2: hạ tầng, GitOps, backup, Kafka, alert, MLOps

> Đối tượng: Ops (0,5 người trong POC), DevOps/SRE, trưởng nhóm backend, hai senior làm S1-02.
> Đo ngày 27/09/2026 trên d63d896 (nhánh `feat/remove-redundant-service`) và cụm dev `10.10.55.101`;
> cập nhật 28/09 sau khi gỡ release management và stack Docker Compose.
> File này **thay phần vận hành và hạ tầng của thiết kế v1**; phần nào mâu thuẫn với
> [00](00-architecture-v2.md) thì 00 thắng. Việc có ID nằm ở [05](05-implementation-plan.md) (P0-03,
> P0-07, P0-08); quy ước database ở [10](10-database-design.md).
> Sơ đồ: [30](diagrams/30-cicd.puml) CI/CD · [31](diagrams/31-gitops.puml) GitOps ·
> [32](diagrams/32-envs.puml) môi trường · [33](diagrams/33-infra.puml) hạ tầng ·
> [34](diagrams/34-network.puml) mạng · [35](diagrams/35-kafka.puml) Kafka ·
> [36](diagrams/36-observability.puml) observability · [37](diagrams/37-incident.puml) sự cố ·
> [38](diagrams/38-dataops.puml) DataOps · [39](diagrams/39-backup-dr.puml) backup/DR ·
> [40](diagrams/40-supply-chain.puml) chuỗi cung ứng · [41](diagrams/41-mlops-lifecycle.puml) vòng đời
> MLOps · [42](diagrams/42-mlops-arch.puml) kiến trúc MLOps.

## 0. Kết luận

1. Cụm đang chạy là **bộ manifest `dev`** trên một node k3s; bộ `production` (PSA restricted,
   default-deny, gVisor) chưa được inventory nào triển khai. Mọi bảo đảm độ bền (Kafka RF=1, backup cùng
   node, local-path) đều là một miền lỗi.
2. Nguồn sự thật của V2 là Postgres, MinIO `raw/` và ClickHouse `obs` (ADR-V2-34). Hôm nay **không**
   có backup nào ra khỏi node, và MinIO, ClickHouse chưa có backup gì. P0-07 phải phủ cả ba trước khi
   dữ liệu đã duyệt đầu tiên xuất hiện (C2, 16/10).
3. Kafka cần đóng ba lỗ trước khi thêm topic V2 (P0-03): `auto.create` còn `true`, ba trong bốn
   producer chưa idempotent, và listener INTERNAL không xác thực với `User:ANONYMOUS` là superuser, đang
   được cả pipeline KB dùng.
4. Không có Alertmanager, không có rule, không có log tập trung trong k8s. Cảnh báo duy nhất là
   `disk-alert.sh` ghi syslog. Alert vận hành tối thiểu (mục 7) làm trong POC vì Ops còn dư 1,2
   tuần-người (05 mục 7).
5. Crawler đã gọi ra Internet mà không có allowlist tên miền (dev allow-all); production thì không mở
   Internet chút nào nên crawler không chạy được ở đó. Egress proxy theo tên miền (P0-08) là điều kiện
   để có crawler ở production.
6. GitOps (Argo CD, External Secrets, staging, ký image) là việc **sau POC**. Trong POC chỉ làm phần
   rẻ: tag image theo SHA, ghim version image bên thứ ba, probe cho worker.

## 1. Hiện trạng vận hành

### 1.1 Cụm và môi trường

| Hạng mục | Hiện trạng | Căn cứ |
| --- | --- | --- |
| Cụm | k3s 1 node `10.10.55.101`; namespace `dataagent-service`, `dataagent-database`, `dataagent-sandbox` | `deploy/k8s/README.md` mục Namespaces, `networkpolicy-sandbox.yaml` |
| Bộ đang chạy | `dev`: inventory duy nhất `k8s-local` đặt `k8s_env: dev` | `deploy/ansible/inventories/k8s-local/group_vars/all/main.yml:4` |
| dev khác production | PSA privileged thay restricted; một `allow-all` mỗi namespace thay default-deny; NodePort (`port + 22000`) thay ClusterIP; 1 replica; không gVisor | `deploy/k8s/README.md` mục Dev set, `production/service/sandbox-executor.yaml` (`runtimeClassName`) |
| Egress production | Chỉ `allow-egress-to-platform`, `allow-egress-remote-llm` (GPU `:8001-8004`), `allow-egress-k8s-api`; không có đường ra Internet | `production/networkpolicy-service.yaml:41,586-617,629` |
| Crawler | `platform-workers-web-crawler` chỉ có ở `dev/service/`, không có ở `production/service/` | `ls deploy/k8s/*/service` |
| Probe | 8 workload production không có `livenessProbe`: `kb-promote-silver/gold`, `kb-structured-data`, `kb-conflict-resolution`, `platform-workers-{audit,memory,schema-crawler}`, `sandbox-runner`; `web-crawler` (dev) cũng không | `grep -L livenessProbe production/service/*.yaml` |
| PDB / HPA | Không PDB nào; HPA theo CPU ở `kb-backend`, `workflow-runtime`, `model-service`, `kong` | `grep PodDisruptionBudget` rỗng |
| Image bên thứ ba trôi | `minio:latest`, `pgbouncer:latest`, `clickhouse-server` không tag | `production/database/minio.yaml:45`, `pgbouncer.yaml:76`, `clickhouse.yaml:57` |
| OpenBao | 2.6.3, storage `file`; khoá unseal là file trên server; tự seal khi pod restart | `openbao.yaml:35,82`, `docs/RUNBOOK.md` mục 8 |

### 1.2 Triển khai và CI

| Hạng mục | Hiện trạng | Căn cứ |
| --- | --- | --- |
| Deploy | Ansible `deploy.yml` kéo git, so checkpoint, chỉ triển khai mã **đã commit** | `deploy/ansible/deploy.yml:30-152`, CLAUDE.md mục 1 |
| Image | Build trên server, đẩy `localhost:5000`, tag `:latest`; kiểm tag chỉ chạy khi tag khác `latest` | `roles/k8s_common/defaults/main.yml:26`, `inventories/.../main.yml:10`, `roles/k8s_verify/tasks/main.yml:78` |
| Secret | ansible-vault → `secrets.env` → `create-secrets.sh` → Secret theo workload; OpenBao giữ secret ứng dụng (mật khẩu warehouse, connector) | `deploy/k8s/README.md` mục "Secrets are scoped per workload", `docs/RUNBOOK.md` mục 7–8 |
| CI (`ci.yml`) | `python-lint` (ruff, đỏ trên `main`), `python-type` (mypy strict theo danh sách surface), `python-test` (ma trận một job mỗi suite service), `python-coverage` (gộp, `fail_under`), `python-integration` (ma trận testcontainers), `eval-gate`, `frontend-lint` (tsc), `frontend-test` (vitest), `k8s-validate` (kubeconform), `quality-gates`, `security-scan` | `.github/workflows/ci.yml` (tên job) |
| Quét bảo mật | pip-audit `\|\| true`, trivy `exit-code: "0"`: chỉ báo cáo | `ci.yml` job `security-scan` |
| Release gate | Eval offline + online, chặn; chạy khi publish release hoặc tay | `.github/workflows/release-gate.yml:16-18,35` |
| E2E | `make e2e` chạy `pytest -m e2e` với cụm dev qua NodePort LAN, chạy tay, không ở CI; test có ghi dữ liệu (lượt agent, xoá/thu hồi/xoay API key) | `Makefile` target `e2e`, `tests/README.md` |
| Chưa có | Build/quét/ký image trong CI, E2E trong CI, staging, Argo CD | |

### 1.3 Observability, backup, GPU

| Hạng mục | Hiện trạng | Căn cứ |
| --- | --- | --- |
| Metrics | Prometheus scrape 3 target (`model-service`, `llm-exporter`, chính nó), giữ 15 ngày, không `rule_files` | `deploy/docker/monitoring/prometheus.yml:5-16`, `production/service/prometheus.yaml:53` |
| Dashboard | Grafana 1 dashboard (`kb-ccu.json`). Datasource trỏ `prometheus.dataagent-service.svc.cluster.local:8400` (sửa 28/09: trước đó là `prometheus:9090` của compose nên mọi panel trống); test manifest kiểm URL datasource khớp cổng Service | `deploy/docker/monitoring/grafana/`, commit 190cda1 |
| Trace | OTLP → Jaeger all-in-one 1.55; Langfuse v3 (ClickHouse + MinIO bucket `langfuse`) cho trace agent | `production/configmap.yaml:89`, `jaeger.yaml:58`, `langfuse.yaml:22-23,79` |
| Log | **Chưa có**: Filebeat và kho log đã gỡ (0 index sau 16 ngày), stack compose cũng đã xoá; xem log bằng `kubectl logs` | `deploy/k8s/README.md` mục "Deliberately absent" |
| Cảnh báo | Không Alertmanager, không node-exporter/kube-state-metrics/kafka-exporter. Duy nhất: cron `disk-alert.sh`, `/data` > 80 % → syslog + file | `deploy/k8s/ops/disk-alert.sh:7-10,23-36` |
| Backup Postgres | CronJob `pg_dump` mỗi 6 giờ, kiểm dump hoàn chỉnh, giữ 7 ngày, vào PVC `postgres-backups` 15Gi **cùng node** (local-path); Job restore có sẵn, `suspend: true` | `production/job/postgres-backup.yaml:29,46,58,92-97,126`, `deploy/k8s/README.md` mục Apply |
| Backup khác | Registry: cron hằng ngày giữ 3 bản trên `/data` (cùng máy). MinIO (không versioning), Mongo, Neo4j, Qdrant, ClickHouse, OpenBao: **không** | `ops/backup-registry.sh:14-16`, `production/job/minio-init.yaml:21,63` |
| Ổ đĩa | Mọi state nằm trên `/data` 1 TB; kubelet dọn image ở 85 %, đuổi pod ở 95 % | `docs/RUNBOOK.md` mục 9 |
| GPU | Ngoài k8s, `docker run --gpus all` trên `10.10.55.101`: `vllm-main` Qwen3-14B-AWQ `:8001`, `vllm-fast` Qwen3-4B-AWQ `:8002` (cả hai `vllm/vllm-openai:latest`), TEI `bge-m3` `:8003`, reranker `:8004`; một A100 40 GB dùng chung | `deploy/k8s/switch-gpu-containers-to-host-network.sh:36-60`, `dev/service/litellm.yaml:23-24` |

## 2. GitOps đích (sau POC)

Sơ đồ [30](diagrams/30-cicd.puml) → [31](diagrams/31-gitops.puml), môi trường [32](diagrams/32-envs.puml).

### 2.1 Nguyên tắc

1. Git là nguồn sự thật cho trạng thái cụm; không `kubectl apply` tay trên staging/production.
2. Image bất biến: tag = git SHA (+ semver khi release), không `:latest`.
3. Promote bằng PR giữa các overlay; rollback = `git revert`.
4. Secret không nằm trong git và không đi qua file `secrets.env` (mục 2.3).

### 2.2 Repo, sync wave, môi trường

Repo cấu hình riêng (`dataagent-deploy`): `base/` lấy từ `deploy/k8s/production`, `overlays/{dev,staging,prod}`
bằng Kustomize, Argo CD app-of-apps.

| Wave | Nội dung |
| --- | --- |
| -2 | Namespace, CRD, External Secrets Operator |
| -1 | `ClusterSecretStore`/`ExternalSecret`, ConfigMap, datastore |
| 0 | Job `migrate` (PreSync), `kafka-topics`, DDL ClickHouse `obs` |
| 1 | Service backend, consumer `platform_workers` |
| 2 | frontend, CronJob, eval Job sau triển khai |

| Môi trường | Mục đích | Dữ liệu | Triển khai |
| --- | --- | --- | --- |
| dev | Tích hợp hằng ngày, demo POC | Nguồn thật + fixture | Tự động mỗi merge `main` |
| staging | E2E, eval online, migration, diễn tập khôi phục (mục 4.2) | Bản sao | PR khi tạo release candidate |
| prod | Người dùng thật | Thật | PR 2 người duyệt |

### 2.3 Secret: External Secrets Operator đọc OpenBao

| | Hôm nay | Đích |
| --- | --- | --- |
| Nguồn | ansible-vault → `secrets.env` trên server | OpenBao là nơi duy nhất giữ secret ứng dụng và hạ tầng |
| Đưa vào pod | `create-secrets.sh`, `gen-secrets-env.sh`, `gen-kafka-jaas-secret.sh` ghi đè mọi Secret | ESO: một `ClusterSecretStore` xác thực bằng Kubernetes auth của OpenBao (đã có, `deploy/k8s/README.md` mục Identity); mỗi workload một `ExternalSecret` sinh đúng `<workload>-secrets` với tập khoá như hôm nay |
| Rủi ro hôm nay | File env khác thế hệ thay âm thầm mật khẩu DB/Kafka (`deploy/k8s/README.md` mục "Secrets are scoped per workload") | Secret có version trong OpenBao, ESO refresh định kỳ |
| Bootstrap | Unseal bằng Ansible, khoá trong `.openbao-keys.json` | Ansible vẫn init/unseal (chạy trước mọi công cụ); khoá unseal tách Shamir cho nhiều người giữ |
| Điều kiện | | OpenBao chuyển storage `file` → `raft` (bản 2.7 bỏ `file`, nên đây cũng là điều kiện nâng version) |

Ba script secret bị xoá khi ESO chạy ở dev. Trước đó, bước trung gian là task `kubernetes.core.k8s` của Ansible.

### 2.4 Pipeline CI đích

| Bước | Công cụ | Chặn | Khi nào |
| --- | --- | --- | --- |
| Lint, type, test, eval offline, kubeconform | có sẵn | Có | Có |
| Secret scan | gitleaks + pre-commit | Có | POC (rẻ) |
| Dependency/fs scan | pip-audit, npm audit, trivy fs | HIGH, CRITICAL | Sau khi dọn baseline |
| Build + SBOM + quét image | buildx, syft, trivy image | CRITICAL | Sau POC |
| Ký image | cosign; Kyverno kiểm chữ ký ở staging/prod | Có | Sau POC |
| Cập nhật GitOps | PR bump tag ở `overlays/dev` | | Sau POC |

### 2.5 Chuyển từ Ansible

| Bước | Việc | Ansible còn giữ |
| --- | --- | --- |
| 1 (POC) | `image_tag` = SHA ngắn (biến đã có, `k8s_verify` tự kiểm); dọn tag cũ trong registry có kiểm soát | Toàn bộ |
| 2 | Cài Argo CD + ESO bằng Ansible; tạo repo cấu hình từ `deploy/k8s/production` | Bootstrap node, OpenBao unseal |
| 3 | Argo CD quản service ở dev | Datastore, GPU |
| 4 | Staging riêng; promote giữa môi trường bằng PR (`release_worker` đã gỡ 28/09) | Bootstrap, driver GPU |

## 3. Hạ tầng đích

Sơ đồ [33](diagrams/33-infra.puml), mạng [34](diagrams/34-network.puml), chuỗi cung ứng [40](diagrams/40-supply-chain.puml).

### 3.1 Thành phần

| Thành phần | Hôm nay | Đích | Khi nào |
| --- | --- | --- | --- |
| Control plane | 1 node | 3 node k3s HA (etcd nhúng) | Sau POC, cần phần cứng |
| Worker | chung node | Tách service / database / sandbox bằng taint/affinity | Sau HA |
| Lưu trữ | local-path, không replica | Longhorn replica 2–3; MinIO phân tán | Sau HA |
| Registry | `localhost:5000` | Harbor: quét, ký, proxy cache | Sau POC |
| Biên | NodePort; Kong ClusterIP | Ingress + cert-manager TLS, chỉ Kong public | Sau POC |
| Chính sách | PSA, NetworkPolicy | + Kyverno (image đã ký, cấm `:latest`, bắt buộc probe/resources), PDB | Sau POC |
| Ghim version | 3 image bên thứ ba + vLLM trôi (mục 1) | Tag cố định (ưu tiên digest) cho mọi image | **POC** |

### 3.2 GPU: GPU Operator, vLLM/TEI thành Deployment ghim version

Hôm nay cách mô hình chạy chỉ được định nghĩa bởi `switch-gpu-containers-to-host-network.sh`, ngoài
Ansible và ngoài k8s; `vllm-main`/`vllm-fast` dùng `:latest` nên mỗi lần pull có thể đổi engine.

| Việc | Đích | Khi nào |
| --- | --- | --- |
| Ghim version | `vllm/vllm-openai:<tag cố định>` + `--revision <HF commit>`; TEI giữ tag đã ghim (`1.9`, `cuda-1.9`) | **POC**: sửa trong script + ghi vào RUNBOOK |
| Đưa vào k8s | NVIDIA GPU Operator + device plugin; vLLM, TEI thành Deployment trong namespace riêng, Service giữ cổng `8001-8004` (dải `8000-8099` đã dành, `deploy/k8s/README.md` mục Ports) | Sau POC |
| Chia GPU | MIG trên A100 (chat cần độ trễ ổn định) hoặc time-slicing; chọn bằng `make bench` | Sau POC |
| Hàng đợi batch | OCR/VLM/embedding hàng loạt chạy ưu tiên thấp, ngoài giờ | Sau POC |
| Dự phòng | LiteLLM fallback vai trò chính → vai trò nhanh, kèm alert | Sau POC |

### 3.3 Egress cho crawler (P0-08)

NetworkPolicy chỉ hiểu CIDR, còn nguồn dữ liệu là tên miền (EIA, World Bank, IMF, Vietcombank, Bộ Công
Thương, SJC/PNJ/DOJI: [04](04-sources-oil-gold.md)). Đích:

| Việc | Chi tiết |
| --- | --- |
| Egress proxy | Một proxy HTTP(S) (Squid hoặc Envoy) trong namespace riêng; pod crawler chỉ được egress tới proxy (NetworkPolicy), proxy chỉ cho tên miền trong allowlist |
| Allowlist là cấu hình | Sinh từ tên miền của nguồn trong Source Registry (`KBSourceConfig`), không sửa tay; nguồn mới chưa được duyệt thì bị chặn (quy tắc "cấu hình, không mã") |
| Log tải về | Proxy ghi mọi lượt tải (tên miền, byte, mã trả về) → đối chiếu với `artifact.stored` |
| Chặn agent | Sandbox và `workflow_runtime` không đi qua proxy crawler; agent không có đường ra Internet |
| Xong khi | Crawler chạy được ở bộ production; tên miền ngoài allowlist bị từ chối và có log |

### 3.4 Độ tin cậy rẻ, làm trong POC

Probe cho 8 workload ở mục 1.1 và 3 consumer mới, liveness kiểm "consumer còn poll trong N giây" chứ
không chỉ tiến trình còn sống; `resources` cho `sandbox-runner` (tránh QoS BestEffort); ghim image
`minio`, `pgbouncer`, `clickhouse-server`, `vllm`.

### 3.5 Từ 1 node lên HA: thứ tự bắt buộc

| Bước | Việc | Mở khoá bởi |
| --- | --- | --- |
| 0 | Backup ra ngoài node (mục 4) | Độc lập, làm ngay |
| 1 | Thêm 2 node, control plane HA | Phần cứng |
| 2 | PDB + `topologySpreadConstraints` cho Deployment ≥ 2 replica | 1 |
| 3 | Taint/affinity tách service / database / sandbox | 1 |
| 4 | Longhorn thay local-path | 1 |
| 5 | Kafka 3 broker, RF=3, `min.insync.replicas=2` | 1 + 4 |
| 6 | Postgres replica + failover (CloudNativePG hoặc Patroni) | 4 |

## 4. Backup / DR (P0-07)

Sơ đồ [39](diagrams/39-backup-dr.puml). Chia theo ADR-V2-34: kho nguồn sự thật phải backup ra ngoài node;
bản chiếu chỉ cần dựng lại được. `/data` là ổ của cùng máy, **không** tính là ngoài node.

### 4.1 Theo kho

| Kho | Giữ gì cho V2 | Tái tạo được? | Hôm nay | Đích POC | Đích sau POC | RPO | RTO |
| --- | --- | --- | --- | --- | --- | --- | --- |
| MinIO `raw/{source_id}/{sha256}` | Raw bất biến (bất biến 2) | **Không**: nguồn đổi hoặc gỡ file (Pink Sheet, 04 bẫy T4) | Không backup, không versioning | Versioning; object lock nếu `raw/` ở bucket riêng (chỉ bật được lúc tạo bucket, chốt ở S2-02); `mc mirror` hằng ngày ra ngoài node (khoá theo hash nên chỉ chép phần mới) | Replication liên tục sang site 2 | 24 h → 1 h | 4 h |
| ClickHouse `obs` | Effective observation mọi version | **Không trọn**: dựng lại từ raw + `governance` sẽ đổi `system_time`, as-of theo trục hệ thống không còn tái lập (bất biến 9) | Chưa có DB | `BACKUP DATABASE obs TO S3(...)` hằng ngày + incremental | + mỗi giờ | 24 h → 1 h | 4 h |
| Postgres `registry`, `ingest`, `governance`, `knowledge`, `alert` (+ `public`, Keycloak, Kong) | Cấu hình đã duyệt, ReviewCase, correction/override/approval, outbox | **Không**: quyết định của người | `pg_dump` 6 h, cùng node | Chép dump ra ngoài node sau mỗi lần chạy; alert tuổi backup (mục 7) | WAL-G/pgBackRest PITR | 6 h → 15 phút | 4 h → 1 h |
| OpenBao | Secret ứng dụng | Không | Không; khoá unseal là file trên server | Bản sao mã hoá của PVC + khoá unseal ngoài server (`docs/RUNBOOK.md` mục 8) | Raft snapshot mỗi giờ, Shamir | 24 h | 2 h |
| Qdrant | Index (bản chiếu) | Có, qua outbox; nhưng embedding hôm nay chỉ nằm ở Qdrant (10 mục 1) → dựng lại = embed lại trên GPU | Không | Snapshot API hằng tuần (rẻ, tránh embed lại) | Không cần khi vector lưu ở Postgres (10, DB1) | — | thời gian dựng lại, phải đo |
| Neo4j | Bản chiếu `knowledge.*` | Có, từ Postgres (ADR-V2-34) | Không | Không | Không | — | thời gian dựng lại, phải đo |
| MongoDB | **Không có dữ liệu V2**; vẫn là nguồn sự thật của canvas agent v1, silver staging (10 mục 1) | Không | Không | `mongodump` hằng ngày cho tới khi chuyển sang Postgres | Bỏ | 24 h | 4 h |
| Kafka | Sự kiện đang chuyển | Có: phát lại từ raw/outbox; topic + ACL từ Job `kafka-topics` | Không | Không | Không | — | — |
| ClickHouse Langfuse, Redis, registry | Trace, cache, image | Chấp nhận mất / dựng từ git | Registry: cron giữ 3 bản | Không đổi | Harbor replication | — | — |

Nơi lưu "ngoài node" chưa có (máy thứ hai, NAS hay S3 ngoài): phải chốt trước W2, xem mục 9.
Phạm vi P0-07 trong 05 ghi "Backup Postgres ngoài node"; mục này mở rộng thành Postgres + MinIO `raw/`
+ ClickHouse `obs`, khớp 10 mục 5.

### 4.2 Diễn tập khôi phục

- **Một lần trước tổng duyệt 26/10**: khôi phục dump Postgres (Job `postgres-restore`) và backup `obs`
  vào namespace nháp; chạy test as-of "hai version chưa gộp" (03 mục 3.2) và một câu của golden set;
  so số dòng và SHA-256 của mẫu artifact `raw/`. Ghi thời gian thực tế làm RTO đo được.
- Sau POC: hằng tháng trên staging; kết quả ghi vào `docs/RUNBOOK.md`.

## 5. Kafka hardening (P0-03)

Sơ đồ [35](diagrams/35-kafka.puml). Thứ tự rẻ trước.

| # | Việc | Hôm nay | Đích | Khi nào |
| --- | --- | --- | --- | --- |
| K1 | Idempotent producer ở mọi nơi | Chỉ client dùng chung bật (`libs/database_connector/kafka_connector.py:49`); `platform_workers/common/kafka.py:114-119` và `workflow_runtime/infrastructure/kafka_producer.py:39-44` chỉ `acks=all` | `enable_idempotence=True` + `acks=all`; một factory producer dùng chung | POC |
| K2 | Khoá phân vùng | `platform_workers` không có `key_serializer` | Khoá theo 03 mục 13 (`source_id`, `release_id`, `series_key`, `case_id`, `entity_id`) nằm trong envelope P0-09 | POC, cho topic V2 |
| K3 | Tắt auto-create | `KAFKA_AUTO_CREATE_TOPICS_ENABLE: 'true'` (`dev/database/kafka.yaml:30`, `production/.../kafka.yaml:31`); `kb.resolve.conflict` chỉ tồn tại nhờ auto-create (`libs/ingestion/config.py:20`) | `false`; thêm `kb.resolve.conflict` vào Job **trước** khi tắt | POC |
| K4 | Topic + ACL khai báo trước khi dùng | Job `kafka-topics` (`deploy/k8s/{dev,production}/job/kafka-topics.yaml`) tạo 7 topic cũ (`kb.promote.silver/gold`, `kb.structured.propose`, `audit.data.access`, `memory.extraction.requested/dlq`, `ddl.changes`) và xoá 3 topic `release.*` đã bỏ | Thêm topic 03 mục 13 (12 topic trong POC; `forecast.*` sau) + `<topic>.dlq`; ACL theo principal cho `series_extractor`, `semantic_extractor`, `detector`, `indicator-api`. Sau POC: spec YAML thay script trong Job (phụ lục A) | POC |
| K5 | DLQ mọi consumer | Chỉ `memory.extraction.dlq` | `<topic>.dlq`, retention 30 ngày, retry có backoff trước khi đẩy DLQ; alert khi có bản ghi mới (mục 7) | POC cho consumer V2 |
| K6 | Retention tường minh | Topic `kb.promote.*`, `kb.structured.propose` không đặt `retention.ms` (mặc định 7 ngày) | Mọi topic V2 khai báo `retention.ms`, `cleanup.policy` trong Job | POC |
| K7 | Listener INTERNAL | `INTERNAL:8352` PLAINTEXT, `KAFKA_SUPER_USERS: User:admin;User:ANONYMOUS` (`kafka.yaml:25,37`): ai tới được cổng 8352 là superuser. Pipeline KB (`kb-backend`, `kb-promote-*`) dùng nó qua `KAFKA_URL` (`production/configmap.yaml:39`, `libs/ingestion/runtime.py:74-75`); Job `kafka-topics` cũng dựa vào đó | Consumer V2 **chỉ** dùng SASL `:8350` với principal riêng. Gỡ `ANONYMOUS` khi pipeline KB và Job đã chuyển sang SASL | Quy tắc: POC; gỡ: sau POC |
| K8 | RF và phân vùng trên 1 node | RF=1 mọi topic (`kafka.yaml:31-33`, `--replication-factor 1`); `acks=all` không thêm bảo đảm khi chỉ có 1 replica | Giữ RF=1 tới HA (mục 3.5 bước 5). `observation.published` 6 phân vùng, topic quy trình 3; thứ tự theo khoá vẫn giữ | POC (phân vùng), sau HA (RF=3) |

K1 và K2 phải đi cùng: idempotence chặn trùng/đảo **trong** một phân vùng, khoá quyết định phân vùng
nào. Thiếu khoá thì hai sự kiện cùng `series_key` có thể xử lý song song và recompute ghi đè bằng bản cũ.
Xong P0-03 khi: gửi tới topic chưa khai báo bị từ chối, và test producer gửi lại không sinh bản trùng.

## 6. ClickHouse cho `obs`

| Việc | Chi tiết |
| --- | --- |
| Hôm nay | Một instance, chỉ có dữ liệu Langfuse (`CLICKHOUSE_DB: default`, `clickhouse.yaml:21`; `langfuse.yaml:22-23`); limit 2 CPU / 2 Gi (`clickhouse.yaml:65-69`); PVC 10 Gi + 5 Gi; users từ ConfigMap `users.d` (`:100-101`) |
| Database riêng | `obs`, tách khỏi database của Langfuse; tách instance khi tải Langfuse và truy vấn agent tranh nhau (đo, không đoán) |
| User | `obs_writer`: chỉ `INSERT` trên `obs.*`, dùng bởi publisher của `indicator-api`. `obs_reader`: chỉ `SELECT`, dùng bởi operator/agent. Không user ứng dụng nào có DDL (10 mục 4, ADR-V2-33) |
| DDL | File SQL có version chạy trong Job `migrate` (Alembic không quản ClickHouse); CI kiểm file khớp bảng (tinh thần ADR-V2-35) |
| Row policy | Theo `tenant_id`, đồng bộ với RLS Postgres |
| Bảng | 03 mục 3.2: `ReplacingMergeTree(system_time)`, `PARTITION BY toYear(event_time_start)`, `ORDER BY (tenant_id, series_key, event_time_start, available_at, effective_version)`. Dữ liệu POC dưới 200 nghìn dòng: không chia partition mịn hơn |
| Đọc | Luôn `argMax(value, (available_at, effective_version))` với `available_at <= :as_of AND system_time <= :as_of_system`; không giả định đã gộp; test hai version chưa gộp là bắt buộc (S1-02) |
| Ghi | Chỉ ghi phần thay đổi (ADR-V2-13) nên lô nhỏ: bật `async_insert=1`, `wait_for_async_insert=1` để không sinh hàng nghìn part |
| Tài nguyên | `max_memory_usage` và `max_execution_time` theo profile của `obs_reader`; nâng limit pod khi đo thấy cần |
| Image | Ghim tag (hôm nay không tag) |
| Backup | Mục 4.1; database Langfuse không backup |

## 7. Alert vận hành

Sơ đồ [36](diagrams/36-observability.puml), [37](diagrams/37-incident.puml), [38](diagrams/38-dataops.puml).

**Alertmanager chưa tồn tại.** Tối thiểu cho POC: Alertmanager + kube-state-metrics + node-exporter +
kafka-exporter + blackbox-exporter, rule khai báo trong repo. node-exporter cần `hostPath`, namespace
restricted cấm việc này (`disk-alert.sh:7-10`), nên stack giám sát chạy trong namespace riêng có PSA
nới. Kênh nhận (email/Slack/Teams) chưa chốt (mục 9).

Alert vận hành **khác** alert nghiệp vụ: alert nghiệp vụ là `alert.raised` từ `detector` vào hộp thư
Alert (UC12); alert vận hành báo cho người vận hành qua Alertmanager. Metric nghiệp vụ (ReviewCase,
drift, SLA nguồn) do `indicator-api` phơi ở `/metrics`, tính từ bảng `governance`/`ingest`.

| Rule | Điều kiện | Mức | Hành động | Nguồn số liệu |
| --- | --- | --- | --- | --- |
| ServiceDown | `up == 0` hoặc blackbox `/health` lỗi 5 phút | P1 | Xem pod, rollout restart; mục RUNBOOK tương ứng | Prometheus, blackbox |
| OpenBaoSealed | blackbox `/v1/sys/health` khác 200 trong 2 phút | P1 | Unseal (`docs/RUNBOOK.md` mục 8) | blackbox |
| LLMBackendDown | `kb_llm_up == 0` 5 phút | P1 | Kiểm container GPU, `nvidia-smi` | `llm_exporter` (`exporter.py:169-171`), có sẵn |
| DiskFillingUp | `predict_linear(node_filesystem_avail_bytes{mountpoint="/data"}[6h], 24*3600) < 0` hoặc dùng > 80 %; > 90 % là P1 | P2 / P1 | Dọn image theo RUNBOOK mục 9, không `--delete-untagged` trên registry. **Thay `deploy/k8s/ops/disk-alert.sh`**, xoá script khi rule chạy | node-exporter |
| PodRestartsHigh | `increase(kube_pod_container_status_restarts_total[30m]) > 3` hoặc `OOMKilled` | P2 | Xem log, limit | kube-state-metrics |
| KafkaConsumerLagHigh | Lag của một group tăng liên tục 15 phút | P2 | Xem consumer treo/chậm | kafka-exporter |
| DLQNotEmpty | `increase(kafka_topic_partition_current_offset{topic=~".*\\.dlq"}[10m]) > 0` | P2 | Đọc bản ghi, sửa, phát lại | kafka-exporter |
| SourceSLABreached | Có `source.sla.breached` chưa đóng cho nguồn | P3; P2 với nguồn trong kịch bản nghiệm thu | Kiểm connector, trang landing | `indicator-api` |
| ReviewCaseBacklog | Case mở > N theo `kind`, hoặc case cũ nhất > 3 ngày | P3 | Nhắc người duyệt theo authority | `indicator-api` |
| ReviewCaseRateHigh | > 5 ReviewCase / 1.000 observation / tuần (ngưỡng rủi ro ở 07) | P3 | Rà luật DQ/conflict, `SAME_UNDERLYING`, regime | `indicator-api` |
| SchemaDriftRateHigh | > 1 case `SCHEMA_DRIFT` / nguồn / tuần | P3 | Rà profile trích xuất của nguồn | `indicator-api` |
| PostgresBackupStale | `time() - kube_cronjob_status_last_successful_time{cronjob="postgres-backup"} > 12h`, hoặc bản ngoài node cũ hơn 12 h | P2 | Chạy tay, kiểm PVC/đích ngoài node | kube-state-metrics, job chép |
| ObsOrRawBackupStale | Backup `obs` hoặc `mc mirror raw/` cũ hơn 36 h | P2 | Như trên | job backup |

Log tập trung trong k8s: **chưa có**. Đề xuất (quyết định sau POC) Loki + agent thu log trong namespace
giám sát, log JSON có `trace_id`, `tenant_id`, `service`. Trong POC dùng `kubectl logs` và trace
Jaeger/Langfuse.

## 8. MLOps / LLMOps

Sơ đồ [41](diagrams/41-mlops-lifecycle.puml), [42](diagrams/42-mlops-arch.puml).

### 8.1 Đã có

| Hạng mục | Hiện trạng | Căn cứ |
| --- | --- | --- |
| Eval gate | Offline chặn merge; offline + online chặn release; ngưỡng online còn `provisional` | `ci.yml` job `eval-gate`, `release-gate.yml:35`, `model/eval/thresholds.json:23-24` |
| Benchmark serving | `make bench` (TTFT, decode, throughput theo vai trò), `make bench-gate` (0 đạt / 1 trượt / 2 không kết luận), `make bench-e2e` | `Makefile:68,75,88`, `model/bench/thresholds.json` |
| Định tuyến LLM | LiteLLM theo vai trò: `planner`, `responder`, `judge`, `text_to_sql`, `orchestrator-planner`, … `embedder` | `deploy/docker/litellm/config.yaml:25-157` |

### 8.2 Phiên bản hoá: "release bundle"

Mỗi câu trả lời và mỗi alert phải truy ngược được cấu hình đã sinh ra nó. Bundle = {image vLLM/TEI +
HF revision, commit config LiteLLM, hash prompt, version agent/workflow, version capability registry,
version golden set, version lõi số `libs/indicator`}; ghi vào metadata trace Langfuse. Trong POC chỉ cần
ghim image (mục 3.2) và ghi commit; MLflow là sau POC (05 mục 8).

### 8.3 Quy trình thay đổi

| Thay đổi | Cổng | Trong POC |
| --- | --- | --- |
| Prompt, capability, operator | PR + eval offline (`eval-gate`) + golden set dầu/vàng (S10-02) | Có |
| Model LLM hoặc engine vLLM | `make bench` + `bench-gate` (tốc độ, VRAM) **và** release gate (chất lượng); không kém baseline | Có, thủ công |
| Embedding model | Là thay đổi dữ liệu: collection Qdrant mới, index song song, benchmark retrieval, chuyển alias; giữ collection cũ tới hết hạn rollback. Hôm nay `bge-m3` 1024 chiều, đổi model phải ingest lại (`docs/RUNBOOK.md` mục 11) | Không đổi trong POC |
| Skill/workflow mới | Offline eval + người duyệt; agent không tự sửa production (bất biến 12) | Ngoài POC (S13-01) |
| Model dự báo | Backtest rolling origin so baseline random walk/drift/ETS; lưu `forecast.model_version` (10 mục 4); kết quả là object `FORECAST`, không bao giờ `verified` (03 mục 12) | Ngoài POC (S12-01/02) |
| Canary/shadow qua trọng số LiteLLM | Điểm Langfuse ≥ baseline | Sau POC |

### 8.4 Giám sát chất lượng

Golden set chạy hằng ngày → Langfuse score, alert EvalRegression (P2) khi giảm > 2 điểm %; số không có
trong evidence (verifier `libs/indicator/verifier`) > 0 trên golden set là P2; tỉ lệ
`INSUFFICIENT_EVIDENCE` và từ chối theo dõi xu hướng.

## 9. Thứ tự làm

| Giai đoạn | Việc | ID 05 | Ai |
| --- | --- | --- | --- |
| W1 (28/09–02/10) | Kafka K1–K6 và quy tắc K7 | P0-03 | Ops |
| W1 | Database `obs`, user `obs_writer`/`obs_reader`, row policy, ghim image ClickHouse | S1-02 | SR2, Ops |
| W1–W2 | Ghim image (vLLM, MinIO, pgbouncer, ClickHouse); tag image theo SHA; probe cho worker | (vận hành) | Ops |
| W2 (trước C2 16/10) | Backup ngoài node: Postgres, MinIO `raw/` (versioning + mirror), ClickHouse `obs`; OpenBao | P0-07 | Ops |
| W2–W3 | Alertmanager + rule mục 7 (tối thiểu: ServiceDown, OpenBaoSealed, LLMBackendDown, DiskFillingUp, PodRestartsHigh, DLQNotEmpty, PostgresBackupStale) | (vận hành) | Ops |
| W3 | Egress proxy + allowlist sinh từ Source Registry | P0-08 | Ops |
| W4 | Diễn tập khôi phục trước tổng duyệt 26/10 | P0-07 | Ops, QA |
| Sau POC, 11/2026 | Repo GitOps, Argo CD ở dev, ESO, staging; CI build + SBOM + trivy image + cosign; Loki (nếu chốt); metric `indicator-api` đầy đủ | | DevOps |
| Sau POC, 12/2026 | Argo CD staging/prod; Kyverno; OpenBao raft; gỡ `ANONYMOUS`; PITR Postgres | | DevOps |
| Khi có phần cứng | HA theo mục 3.5; GPU Operator + MIG; Harbor | | DevOps |

P0-07 và P0-08 là Tier B trong 05 mục 6; Ops có 2,2 tuần-người và Tier A chỉ dùng 1,0 (05 mục 7), nên
làm được trong POC mà không lấy người của Tier A.

Câu hỏi vận hành cần chốt (chuyển sang 07 mục 1 khi nhóm duyệt file này):

| # | Câu hỏi | Đề xuất |
| --- | --- | --- |
| OPS1 | Đích backup "ngoài node" là gì: máy thứ hai, NAS hay S3 ngoài? | Một bucket S3-compatible trên máy khác trong LAN cho POC; site 2 sau POC |
| OPS2 | Kênh nhận alert vận hành | Email nhóm + một kênh chat; P1 gửi cả hai |
| OPS3 | Log tập trung | Loki sau POC (đề xuất, mục 7) |

## Phụ lục A. Kế hoạch bỏ script

Quy tắc ở `CLAUDE.md` mục 5: không thêm `.sh` mới để điều phối. Bảng dưới là kế hoạch cho từng script
còn lại (18 file `.sh` được theo dõi trong repo, số dòng đo 28/09/2026) và ba script đã xoá.

| Script | Dòng | Làm gì | Kế hoạch |
| --- | ---: | --- | --- |
| `deploy/docker/kong/kong-setup.sh` | 611 | Tạo service, route, consumer, plugin JWT/ACL của Kong qua Admin API; chạy trong Job `setup` | **Thay bằng decK**: xuất cấu hình đang chạy, commit `kong.yaml`, `deck sync` từ Job. Khai báo, diff được, idempotent |
| `deploy/k8s/create-secrets.sh` | 241 | Từ `secrets.env` tạo Secret theo workload; Ansible gọi | **External Secrets Operator** đọc OpenBao (mục 2.3); trước đó là task `kubernetes.core.k8s` của Ansible |
| `deploy/docker/keycloak/setup-audience-mappers.sh` | 158 | Thêm audience mapper cho client của realm | Gộp vào `realm-export.json` để realm nhập vào đã đúng |
| `deploy/docker/certs/gen-certs.sh` | 128 | Sinh CA và chứng chỉ mTLS cho dev | Giữ: chỉ dùng ở dev, không phụ thuộc cụm |
| `tests/loadtest/ingestion_throughput.sh` | 116 | Đo thông lượng ingestion Bronze → Silver → Gold | Chuyển vào bộ k6 cạnh `ccu-10k.js` để mọi load test dùng một công cụ |
| `deploy/k8s/gen-secrets-env.sh` | 108 | Sinh `secrets.env` cho `dev/up.sh` | Như `create-secrets.sh`: template Ansible, rồi ESO |
| `backend/services/sandbox_executor/ops/setup_sandbox_egress_iptables.sh` | 102 | Firewall host cho sandbox dạng Docker | Giữ, nhưng gọi từ một role Ansible để áp dụng lặp lại được |
| `deploy/k8s/bootstrap-openbao.sh` | 90 | Init/unseal OpenBao cho `dev/up.sh` | Role `k8s_openbao` của Ansible đã làm việc này khi deploy; giữ làm bootstrap tay, đưa khoá unseal ra khỏi file (mục 2.3) |
| `deploy/k8s/switch-gpu-containers-to-host-network.sh` | 82 | Chạy container vLLM/TEI với `--gpus all` | **Manifest**: GPU Operator + Deployment ghim tag (mục 3.2). Hôm nay là định nghĩa duy nhất của cách model chạy, nên thay trước |
| `backend/services/sandbox_executor/ops/install_gvisor.sh` | 82 | Cài gVisor lên host | Role Ansible: đây là bootstrap host, không phải mã ứng dụng |
| `deploy/k8s/build-push-images.sh` | 67 | Build và push image | `deploy.yml` đã build (song song, theo input của từng image); giữ làm đường tay tới khi CI build image |
| `deploy/k8s/fix-docker-userland-proxy.sh` | 60 | Sửa một lần daemon Docker trên host | Task Ansible + một dòng trong RUNBOOK; xoá khi đã áp dụng ở mọi host |
| `deploy/k8s/create-configmaps.sh` | 59 | Tạo ConfigMap từ `deploy/docker/**` và file seed `tests/e2e/` | Giữ (Ansible gọi); thành `configMapGenerator` của Kustomize khi có GitOps |
| `deploy/k8s/dev/up.sh` | 51 | Dựng dev lần đầu (legacy) | Gộp vào `make deploy`, rồi xoá |
| `deploy/k8s/ops/disk-alert.sh` | 51 | Cron kiểm `/data`, chỉ ghi syslog | **Xoá** khi có Prometheus + Alertmanager (rule `DiskFillingUp`, mục 7) |
| `deploy/k8s/fix-pod-to-host-docker-networking.sh` | 42 | Sửa một lần mạng pod → container Docker trên host | Như các `fix-*` khác: Ansible + RUNBOOK |
| `deploy/k8s/ops/backup-registry.sh` | 26 | Cron backup registry | Giữ tới khi có Harbor replication |
| `deploy/k8s/gen-kafka-jaas-secret.sh` | 23 | Tạo Secret JAAS của Kafka bằng tay | `deploy.yml` đã tự tạo Secret này; gộp vào đường secret ở trên |
| `tests/e2e/e2e.sh` | — | Dựng stack compose, seed, chạy test API + UI | **Đã xoá** (28/09) cùng stack compose: `make e2e` chạy `pytest -m e2e` với cụm dev; seed do Job `seed` nạp |
| `deploy/docker/kafka/kafka-setup.sh` | — | Tạo topic + ACL cho compose | **Đã xoá** (28/09): Job `kafka-topics` là nơi duy nhất; bước sau là spec YAML cho topic/ACL (K4) |
| `deploy/docker/openbao/openbao-approle-setup.sh` | — | Bootstrap AppRole cho compose | **Đã xoá** (28/09): role `k8s_openbao` của Ansible làm việc này cho cụm |

Thứ tự làm:

1. `switch-gpu-containers-to-host-network.sh` → manifest (bỏ runtime cuối cùng chưa được khai báo).
2. `kong-setup.sh` → decK (script lớn nhất, rủi ro lệch cao nhất).
3. Bộ ba secret (`create-secrets`, `gen-secrets-env`, `gen-kafka-jaas-secret`) → ESO.
4. Xoá các `fix-*.sh` và `disk-alert.sh` khi phần thay thế đã chạy.
