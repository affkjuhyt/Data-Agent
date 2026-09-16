# Thiết kế GitOps · DevOps · MLOps · Monitoring cho nền tảng Data Agent

> Phiên bản 15/09/2026 · Đối tượng: DevOps/SRE, trưởng nhóm backend/data/AI, quản lý dự án.
> Căn cứ: khảo sát repo `Data-Agent` (CI, deploy, k8s manifests, monitoring, model/eval) ngày 15/09/2026.
> Quy ước màu mọi sơ đồ: **xanh lá = đã có** · **xanh dương = có một phần** · **vàng = chưa có**.
> Sơ đồ: [25](diagrams/25-cicd-current.puml) CI/CD hiện tại · [26](diagrams/26-gitops-target.puml) GitOps mục tiêu · [27](diagrams/27-environments-promotion.puml) môi trường & promote · [28](diagrams/28-infra-topology.puml) hạ tầng · [29](diagrams/29-observability-architecture.puml) observability · [30](diagrams/30-alerting-incident-flow.puml) cảnh báo & sự cố · [31](diagrams/31-mlops-lifecycle.puml) vòng đời MLOps · [32](diagrams/32-mlops-architecture.puml) kiến trúc MLOps · [33](diagrams/33-dataops-observability.puml) DataOps · [34](diagrams/34-backup-dr.puml) backup & DR · [35](diagrams/35-security-supply-chain.puml) bảo mật chuỗi cung ứng · [36](diagrams/36-capability-status-map.puml) bản đồ trạng thái toàn nền tảng.

---

## 0. Tóm tắt

| Mảng | Trạng thái | Nhận định |
|---|---|---|
| **CI** | Có, khá tốt | Lint, mypy strict, test, eval gate offline đều chặn merge. Thiếu E2E, build/quét/ký image trong CI; quét bảo mật chỉ báo cáo |
| **CD / GitOps** | Một phần | Ansible chạy tay trên server, image luôn `:latest`, không có staging thật, không có GitOps; bước deploy của release-worker là stub |
| **Hạ tầng** | Một phần | 1 node k3s, NetworkPolicy + PodSecurity tốt; GPU chạy `docker run` ngoài k8s; không PDB; 7 worker thiếu probe |
| **Monitoring** | Một phần | Prometheus 3 target, Grafana 1 dashboard, Jaeger, Langfuse. **Không có alert rule, không Alertmanager, không log thu từ k8s, không SLO** |
| **Backup / DR** | Một phần | Chỉ Postgres (cùng node) và registry; không backup MinIO/ClickHouse/Qdrant/Neo4j/Mongo/OpenBao; không DR runbook |
| **MLOps** | Một phần | Eval gate offline/online, prompt trong git. Không model registry, dataset versioning, canary/shadow, GPU scheduling; vLLM không ghim version |
| **DataOps** | Chưa có | Chỉ có freshness watermark của schema catalog |

Bản đồ tổng: [36-capability-status-map](diagrams/36-capability-status-map.puml).

**Ưu tiên cho POC (đến 23/10):** ghim version vLLM/image, alert rule + Alertmanager tối thiểu, thu log k8s, probe cho worker, backup ngoài node cho Postgres + MinIO, Langfuse scores cho eval. **Sau POC:** Argo CD + staging + External Secrets, supply-chain security, MLflow + canary LLM, HA + GPU Operator.

---

## 1. Hiện trạng chi tiết

### 1.1 CI / chất lượng mã

| Hạng mục | TT | Căn cứ |
|---|---|---|
| ruff lint/format, mypy strict 12 package | Có | `.github/workflows/ci.yml` |
| pytest unit + integration, coverage ≥ 35% | Có | `ci.yml`, `pyproject.toml` |
| eval-gate offline (chặn) | Có | `ci.yml`, `model/eval/gate.py` |
| Frontend typecheck, lint, vitest | Có | `ci.yml` |
| kubeconform manifests, import-linter | Có | `ci.yml` |
| pip-audit, trivy fs | Một phần (chỉ báo cáo) | `ci.yml` |
| release-gate eval online (chặn) | Có | `.github/workflows/release-gate.yml` |
| E2E trong CI | Chưa có | `tests/e2e` chỉ chạy tay |
| Build / SBOM / quét / ký image trong CI | Chưa có | — |
| pre-commit, CODEOWNERS, Renovate/Dependabot, gitleaks, semgrep, npm audit | Chưa có | — |

### 1.2 Triển khai

| Hạng mục | TT | Căn cứ |
|---|---|---|
| Ansible deploy theo git diff checkpoint | Có | `deploy/ansible/deploy.yml` |
| Manifest YAML dev/production | Có | `deploy/k8s/{dev,production}` |
| Tag image | Một phần (`:latest`, không SHA) | vars Ansible |
| Registry | Có (`localhost:5000`, backup hằng ngày) | `deploy/k8s/ops/backup-registry.sh` |
| Secret | Một phần (ansible-vault → `secrets.env` → k8s Secret; OpenBao cho ứng dụng) | `roles/k8s_openbao` |
| Migration | Có (Job alembic) | `job/migrate.yaml` |
| Staging | Chưa có (chỉ là tên GitHub environment) | — |
| GitOps (Argo CD/Flux), Kustomize/Helm, canary/blue-green | Chưa có | — |
| release-worker | Một phần (bước deploy là `time.sleep(0.1)`) | `release_worker/release_core.py` |

### 1.3 Hạ tầng

| Hạng mục | TT | Căn cứ |
|---|---|---|
| k3s 1 node, 3 namespace, PodSecurity restricted, NetworkPolicy default-deny | Có | `deploy/k8s/*` |
| HPA (kb-backend, workflow-runtime, model-service, kong) | Có | manifests |
| Resource requests/limits | Có (trừ sandbox-runner) | manifests |
| Liveness/readiness | Một phần (thiếu 7 worker) | manifests |
| PDB | Chưa có | — |
| GPU | Một phần (`docker run` ngoài k8s, `:latest`, không device plugin) | `switch-gpu-containers-to-host-network.sh` |
| Cảnh báo ổ đĩa | Một phần (cron ghi log, không gửi đi) | `deploy/k8s/ops/disk-alert.sh` |

### 1.4 Monitoring

| Hạng mục | TT | Căn cứ |
|---|---|---|
| Prometheus | Một phần (scrape model-service, llm-exporter, chính nó) | `deploy/docker/monitoring/prometheus.yml` |
| Grafana | Một phần (1 dashboard KB CCU) | `grafana/dashboards/kb-ccu.json` |
| Jaeger (OTLP) | Có | `service/jaeger.yaml` |
| Elasticsearch + Kibana | Có; **Filebeat chỉ ở docker-compose** | `deploy/docker/filebeat` |
| Langfuse (trace LLM, TTFT, decode tok/s) | Có | `workflow_runtime/observability.py`, `llm_call.py` |
| llm-exporter | Có | `backend/services/llm_exporter` |
| Alert rules, Alertmanager, SLO, exporter hạ tầng/DB/GPU, blackbox, OTel Collector | Chưa có | — |

### 1.5 MLOps

| Hạng mục | TT | Căn cứ |
|---|---|---|
| Eval offline/online + thresholds | Có | `model/eval` |
| Golden case jsonl | Một phần (15–44 case/bộ, không dung sai/nguồn) | `model/eval/cases` |
| RAGAS | Một phần (thử nghiệm) | `model/ragas` |
| Prompt | Một phần (file trong git + bảng `SystemPrompts` có version) | `prompts/*.txt`, `workflow_orm.py` |
| Config LiteLLM | Có (ConfigMap) | `deploy/docker/litellm/config.yaml` |
| Model registry, experiment tracking, dataset versioning | Chưa có | — |
| Canary/shadow model, feedback người dùng, Langfuse scores/datasets | Chưa có | — |
| vLLM version pinning, GPU scheduling | Chưa có | — |

---

## 2. GitOps & CD

Sơ đồ: [25 hiện tại](diagrams/25-cicd-current.puml) → [26 mục tiêu](diagrams/26-gitops-target.puml) · [27 promote](diagrams/27-environments-promotion.puml).

### 2.1 Nguyên tắc

1. **Git là nguồn sự thật** cho trạng thái cluster; không `kubectl apply` tay trên staging/prod.
2. **Tách repo**: `Data-Agent` (mã) và `dataagent-deploy` (cấu hình triển khai).
3. **Image bất biến**: tag = git SHA (+ semver khi release), không dùng `:latest`.
4. **Promote bằng Pull Request** giữa các overlay; rollback = `git revert`.
5. **Secret không nằm trong git**: External Secrets Operator đọc OpenBao.

### 2.2 Cấu trúc repo GitOps

```
dataagent-deploy/
├── apps/                      # Argo CD Application (app-of-apps)
│   ├── platform.yaml          # cert-manager, ESO, Kyverno, kube-prometheus-stack, Fluent Bit
│   ├── data.yaml              # postgres, clickhouse, qdrant, neo4j, minio, kafka, redis, openbao
│   └── services.yaml          # service ứng dụng
├── base/                      # manifest hiện có trong deploy/k8s/dev (chuyển sang)
│   ├── services/<service>/    # deployment, service, hpa, pdb, servicemonitor
│   ├── jobs/                  # migrate (PreSync hook), seed
│   └── kustomization.yaml
└── overlays/
    ├── dev/                   # 1 replica, NodePort, tag SHA mới nhất (auto)
    ├── staging/               # giống prod, dữ liệu ẩn danh, tag release candidate
    └── prod/                  # HA, Argo Rollouts canary, tag semver đã ký
```

### 2.3 Thứ tự đồng bộ (sync wave)

| Wave | Nội dung |
|---|---|
| -2 | Namespace, CRD, cert-manager, ESO, Kyverno |
| -1 | ExternalSecret, ConfigMap, datastore |
| 0 | Job `migrate` (Argo CD PreSync hook), Kong migration |
| 1 | Service backend (Argo Rollouts cho service chịu tải người dùng) |
| 2 | frontend, CronJob, eval Job hậu triển khai |

### 2.4 Pipeline CI mục tiêu

| Bước | Công cụ | Chặn? | TT |
|---|---|---|---|
| Lint, type, unit, integration, eval offline | ruff, mypy, pytest, model/eval | Có | Có |
| Secret scan | gitleaks | Có | Chưa có |
| SAST | semgrep (+ bandit) | HIGH | Chưa có |
| Dependency audit | pip-audit, npm audit | HIGH+ | Một phần |
| E2E smoke | k3d + manifests + Playwright/`tests/e2e` | Có | Chưa có |
| Build | docker buildx + cache registry | — | Chưa có |
| SBOM | syft (SPDX) | — | Chưa có |
| Quét image | trivy image | CRITICAL | Chưa có |
| Ký image + attest SBOM | cosign (keyless hoặc key trong OpenBao) | — | Chưa có |
| Push + cập nhật GitOps | registry → PR bump `overlays/dev` | — | Chưa có |

### 2.5 Môi trường

| Môi trường | Mục đích | Dữ liệu | Triển khai | TT |
|---|---|---|---|---|
| dev | Tích hợp hằng ngày, demo nội bộ | Seed demo | Tự động mỗi merge main | Một phần (Ansible tay) |
| staging | Kiểm tra release: E2E, load, eval online, migration | Bản sao ẩn danh | PR khi tạo release candidate | Chưa có |
| prod | Người dùng thật | Thật | PR 2 người duyệt + canary | Chưa có (manifest có) |

### 2.6 Chuyển đổi từ Ansible

| Bước | Việc | Giữ Ansible cho |
|---|---|---|
| 1 | Tag image theo SHA trong Ansible (không đổi luồng) | Toàn bộ |
| 2 | Cài Argo CD + ESO bằng Ansible; tạo repo GitOps từ `deploy/k8s/dev` | Bootstrap node, OpenBao unseal |
| 3 | Argo CD quản lý `services` ở dev; Ansible chỉ còn datastore | Datastore, GPU |
| 4 | Staging trên namespace/cluster riêng; release-worker chuyển thành “mở PR promote” hoặc bỏ | Bootstrap, GPU driver |

---

## 3. DevOps: hạ tầng, bảo mật, vận hành

### 3.1 Hạ tầng mục tiêu

Sơ đồ: [28-infra-topology](diagrams/28-infra-topology.puml).

| Thành phần | Hiện tại | Mục tiêu |
|---|---|---|
| Control plane | 1 node | 3 node k3s HA (embedded etcd) hoặc RKE2 |
| Worker | chung node | 2–3 worker CPU (service/database/sandbox tách bằng taint/affinity) |
| GPU | `docker run` ngoài k8s | NVIDIA GPU Operator, vLLM/TEI thành Deployment ghim version, MIG hoặc time-slicing trên A100, hàng đợi batch cho OCR/VLM |
| Lưu trữ | local-path, ổ 81% | Longhorn (replica 2–3), MinIO phân tán, cảnh báo dung lượng |
| Registry | `localhost:5000` | Harbor: quét, ký, proxy cache Docker Hub/GHCR |
| Truy cập | NodePort | Ingress + cert-manager TLS, chỉ Kong public |
| Chính sách | PodSecurity, NetworkPolicy | + Kyverno (image đã ký, cấm `:latest`, bắt buộc probe/resources), PDB cho service ≥ 2 replica |

### 3.2 Bảo mật chuỗi cung ứng

Sơ đồ: [35-security-supply-chain](diagrams/35-security-supply-chain.puml). Tiến trình đề xuất:

1. **Ngay**: gitleaks + `.pre-commit-config.yaml`; bật chặn pip-audit/trivy ở mức CRITICAL; CODEOWNERS + branch protection.
2. **GĐ GitOps**: build trong CI, syft SBOM, trivy image, cosign ký; Kyverno verify chữ ký ở staging/prod.
3. **Liên tục**: Renovate cho phụ thuộc Python/npm/base image; ghim base image theo digest (TODO trong `docs/SECURITY.md`).

### 3.3 Backup & DR

Sơ đồ: [34-backup-dr](diagrams/34-backup-dr.puml).

| Datastore | Hiện tại | Mục tiêu | Tần suất | Lưu giữ |
|---|---|---|---|---|
| Postgres | pg_dump 6h → PVC cùng node | WAL-G/pgBackRest PITR → S3 ngoài site | liên tục + full hằng ngày | 30 ngày |
| MinIO | Không | Versioning + replication sang site 2 | liên tục | theo lifecycle |
| ClickHouse | Không | `BACKUP ... TO S3` | hằng ngày + incremental | 30 ngày |
| Qdrant | Không | Snapshot API → S3 | hằng ngày | 14 ngày |
| Neo4j | Không | `neo4j-admin database dump` → S3 | hằng ngày | 14 ngày |
| Mongo | Không | mongodump → S3 | hằng ngày | 14 ngày |
| OpenBao | Không (khoá unseal lưu file local) | Raft snapshot + ký gửi khoá unseal (Shamir, nhiều người giữ) | hằng giờ | 7 ngày |
| Kafka | Không | Không backup topic; tái tạo từ artifact gốc | — | — |
| Registry | Script hằng ngày, giữ 3 | Harbor replication | hằng ngày | 3 bản |
| K8s resource/PVC | Manifest trong git | + Velero | hằng ngày | 14 ngày |

**Mục tiêu:** RPO ≤ 1 giờ cho dữ liệu nghiệp vụ (Postgres, observation), ≤ 24 giờ cho index tái tạo được (Qdrant, Neo4j); RTO ≤ 4 giờ. **Diễn tập khôi phục hằng tháng** trên staging, ghi thời gian thực tế vào DR runbook.

### 3.4 Vận hành

| Hạng mục | Thiết kế |
|---|---|
| Runbook | Mở rộng `docs/RUNBOOK.md`: mỗi alert có link runbook (triệu chứng, kiểm tra, xử lý, escalate) |
| On-call | Lịch luân phiên, P1 phản hồi ≤ 15 phút giờ làm việc |
| Change management | Mọi thay đổi staging/prod qua PR GitOps; cửa sổ bảo trì cho GPU/driver/datastore |
| Chi phí/năng lực | Dashboard dung lượng ổ, GPU VRAM, token/ngày; review hằng tháng |

---

## 4. Monitoring & Observability

Sơ đồ: [29-observability-architecture](diagrams/29-observability-architecture.puml) · [30-alerting-incident-flow](diagrams/30-alerting-incident-flow.puml) · [33-dataops-observability](diagrams/33-dataops-observability.puml).

### 4.1 Kiến trúc

| Tầng | Hiện tại | Mục tiêu |
|---|---|---|
| Metrics hạ tầng | Không | kube-prometheus-stack: node-exporter, kube-state-metrics, cAdvisor; dcgm-exporter (GPU) |
| Metrics datastore | Không | postgres-exporter, redis-exporter, kafka-exporter (consumer lag), ClickHouse/Qdrant/MinIO metrics gốc |
| Metrics ứng dụng | 2 service | OTel SDK trong mọi FastAPI: RED (rate, errors, duration) theo route; metric nghiệp vụ (câu hỏi/phút, TTFT, tỉ lệ từ chối, tool call) |
| Traces | Service → Jaeger trực tiếp | OTel Collector (sampling, gắn thuộc tính k8s) → Jaeger; `trace_id` trong log và Langfuse |
| Logs | Filebeat chỉ compose | Fluent Bit DaemonSet → Elasticsearch/Kibana; log JSON có `trace_id`, `tenant_id`, `service` |
| LLM | Langfuse traces, llm-exporter | + Langfuse scores (eval, feedback), dashboard token/chi phí/TTFT theo model |
| Dữ liệu | Freshness watermark | data-metrics exporter: freshness theo nguồn, DQ, reconciliation, backlog duyệt, lag pipeline |
| Uptime | Không | blackbox-exporter kiểm tra Kong, UI, `/health` các service |
| Frontend | Không | Web Vitals + lỗi JS gửi OTel |
| Cảnh báo | Không | PrometheusRule → Alertmanager → on-call / Slack-Teams / email / notification-svc |
| SLO | Không | Sloth hoặc Pyrra sinh rule burn-rate từ định nghĩa SLO |

### 4.2 SLO đề xuất

| SLO | SLI | Mục tiêu (30 ngày) |
|---|---|---|
| Khả dụng API | Tỉ lệ request Kong không 5xx | ≥ 99,5% |
| Tốc độ phản hồi đầu tiên | TTFT câu tra cứu chỉ số (root span `orchestrate`) | p95 ≤ 2 s |
| Hoàn tất câu trả lời | `response_ms` câu tra cứu | p95 ≤ 8 s |
| Đúng số | `value_correct` trên golden set chạy hằng ngày | ≥ 0,95 |
| Độ tươi dữ liệu | Nguồn trong SLA công bố | ≥ 95% nguồn |
| Pipeline | Lần chạy connector thành công | ≥ 98% |
| Trang chỉ số | Thời gian tải p95 | ≤ 1,5 s |

### 4.3 Alert rule tối thiểu

| Alert | Điều kiện | Mức |
|---|---|---|
| APIErrorBudgetBurn | Burn rate SLO khả dụng > 14× trong 1h | P1 |
| ServiceDown | `up == 0` hoặc blackbox fail 5 phút | P1 |
| LLMBackendDown / TTFTHigh | llm-exporter `kb_llm_up == 0` / TTFT p95 > 5 s trong 10 phút | P1 / P2 |
| GPUMemoryHigh / GPUXid | VRAM > 95% 10 phút / lỗi Xid | P2 / P1 |
| DiskFillingUp | Dự báo đầy trong 24h (`predict_linear`) | P2 |
| PodCrashLooping / OOMKilled | kube-state-metrics | P2 |
| KafkaConsumerLagHigh | Lag tăng liên tục 15 phút | P2 |
| PostgresReplication/BackupFailed | Job backup thất bại / không có backup 12h | P2 |
| CertificateExpiring | < 14 ngày | P3 |
| DataSourceSLABreached | Nguồn quá hạn công bố | P3 (P2 với nguồn nhóm A) |
| DQBlockingFailure / ReconciliationFailed | Release có lỗi chặn / lệch số chính thức | P2 |
| ReviewBacklogHigh | Hàng đợi duyệt > N hoặc tuổi > 3 ngày | P3 |
| EvalRegression | Điểm golden set giảm > 2 điểm % so với lần trước | P2 |

### 4.4 Dashboard

| Dashboard | TT |
|---|---|
| KB — High CCU (embed cache, TTFT, tok/s) | Có |
| Platform overview (khả dụng, lỗi, độ trễ theo service) | Chưa có |
| Service RED theo route | Chưa có |
| LLM & GPU (TTFT, decode tok/s, VRAM, hàng đợi vLLM, token/chi phí) | Chưa có |
| Kafka & pipeline (lag, DLQ, throughput) | Chưa có |
| Data freshness · DQ · reconciliation · review SLA | Chưa có |
| Eval trend (điểm theo ngày, theo phiên bản model/prompt) | Chưa có |
| Kubernetes & capacity (CPU/RAM/ổ/PVC) | Chưa có |
| Security (audit truy cập dữ liệu restricted, đăng nhập lỗi) | Chưa có |

---

## 5. MLOps / LLMOps

Sơ đồ: [31-mlops-lifecycle](diagrams/31-mlops-lifecycle.puml) · [32-mlops-architecture](diagrams/32-mlops-architecture.puml).

### 5.1 Tài sản cần quản lý phiên bản

| Tài sản | Nơi lưu | Khoá phiên bản | TT |
|---|---|---|---|
| Model LLM (Qwen3-14B/4B) | HF cache/MinIO + registry | image vLLM + HF revision (commit hash) + tham số serve | Chưa có |
| Embedding / reranker | như trên | + chiều vector, collection Qdrant tương ứng | Chưa có |
| OCR / VLM | như trên | model + version pipeline trích xuất | Chưa có |
| Prompt | git + `SystemPrompts` | hash nội dung | Một phần |
| Routing LiteLLM | ConfigMap (git) | commit | Có |
| Agent / workflow | flow-builder | `AgentVersions` / `WorkflowVersions` | Có |
| Golden set | git jsonl + Langfuse Datasets | version dataset | Một phần |
| Benchmark corpus, dữ liệu SFT | DVC (remote MinIO) | DVC hash | Chưa có |
| Model định lượng (dự báo, EWS, DSA) | MLflow | run + model version + dữ liệu train (vintage) | Chưa có |

**“Release bundle”** = {model revision, prompt hash, config LiteLLM, agent version, golden set version, engine compute version}; ghi vào MLflow run và vào metadata mỗi trace Langfuse, để mọi câu trả lời truy ngược được cấu hình đã sinh ra nó.

### 5.2 Quy trình thay đổi

| Bước | Cổng | TT |
|---|---|---|
| 1. Đề xuất (PR) | Review mã/prompt | Có |
| 2. Eval offline trong CI | `model/eval` gate | Có |
| 3. Benchmark (chất lượng + tốc độ + VRAM) | Không kém baseline quá ngưỡng | Chưa có |
| 4. Đăng ký candidate | MLflow stage `candidate` | Chưa có |
| 5. Staging: release-gate online + golden set chỉ số | thresholds | Một phần |
| 6. Shadow traffic (nhân bản request thật, không trả kết quả) | Điểm Langfuse ≥ baseline | Chưa có |
| 7. Canary qua trọng số LiteLLM 10% → 50% → 100% | SLO + điểm chất lượng | Chưa có |
| 8. Promote / rollback | MLflow stage `production` | Chưa có |
| 9. Giám sát + data flywheel | Drift, feedback → golden set | Chưa có |

**Đổi embedding model** là thay đổi dữ liệu, không chỉ model: tạo collection mới, index song song, chạy benchmark retrieval, chuyển alias khi đạt, giữ collection cũ đến khi hết hạn rollback.

### 5.3 Serving và GPU

| Hạng mục | Thiết kế |
|---|---|
| Phân bổ GPU | A100 40GB: MIG (ví dụ 3g.20gb cho vLLM 14B, 2g.10gb cho vLLM 4B, 1g.5gb + 1g.5gb cho TEI) hoặc time-slicing nếu MIG không vừa KV cache — quyết định bằng benchmark |
| Batch vs online | OCR/VLM/embedding hàng loạt chạy hàng đợi riêng (Kueue) ngoài giờ cao điểm hoặc trên GPU thứ hai |
| Ghim version | `vllm/vllm-openai:<tag cố định>` + `--revision <hf commit>`; không dùng `:latest` |
| Dự phòng | LiteLLM fallback: agent-main lỗi → agent-fast với cảnh báo chất lượng; alert P1 |
| Driver | Cập nhật driver trong cửa sổ bảo trì; hiện đang lệch NVML, cần reboot trước mọi thay đổi GPU |

### 5.4 Giám sát chất lượng model

| Tín hiệu | Nguồn | Hành động |
|---|---|---|
| Điểm golden set hằng ngày | eval-svc → Langfuse scores | Alert EvalRegression |
| Phản hồi đúng/sai của người dùng | UI → Langfuse score | Đưa vào hàng đợi gán nhãn |
| Tỉ lệ từ chối, tỉ lệ `__reset` (judge/guardrail bác) | runtime metrics | Theo dõi xu hướng |
| Số không có trong evidence (verifier) | runtime metrics | P2 nếu > 0 trên golden set |
| Recall retrieval trên mẫu | eval-svc | Theo dõi khi đổi embedding |
| Phân bố chủ đề câu hỏi | Langfuse + phân cụm định kỳ | Phát hiện domain mới cần chỉ số/golden set |

---

## 6. DataOps

Sơ đồ: [33-dataops-observability](diagrams/33-dataops-observability.puml).

| Hạng mục | Thiết kế | TT |
|---|---|---|
| Kiểm thử connector | Fixture ghi sẵn phản hồi API/file mẫu; chạy trong CI | Chưa có |
| Kiểm thử hợp đồng nguồn | Schema `sources.yaml` + kiểm tra định dạng đầu ra connector | Chưa có |
| Theo dõi lần chạy | Dagster UI + metrics (thành công, thời lượng, số bản ghi) | Chưa có |
| Độ tươi | So lịch công bố với thực tế theo nguồn | Một phần (watermark warehouse) |
| Chất lượng | DQ scorecard theo release, reconciliation | Chưa có |
| Backfill | Job Dagster theo khoảng kỳ, idempotent | Chưa có |
| Lineage | OpenLineage event → metadata-lineage-svc | Chưa có |
| Môi trường dữ liệu | Seed demo (dev), bản ẩn danh (staging) | Một phần (seed demo) |

---

## 7. Lộ trình

| Giai đoạn | Thời gian | GitOps / DevOps | Monitoring | MLOps / DataOps |
|---|---|---|---|---|
| **A · POC** | đến 23/10/2026 | Tag image theo SHA trong Ansible; probe cho 7 worker; PDB service chính; gitleaks + pre-commit; sửa driver GPU | Alert rule tối thiểu (mục 4.3: ServiceDown, LLMBackendDown, DiskFillingUp, PodCrashLooping, BackupFailed) + Alertmanager → email/Slack; Fluent Bit → Elasticsearch; dashboard Platform overview + LLM | Ghim version vLLM/TEI; Langfuse scores cho golden set; backup Postgres + MinIO ra ngoài node |
| **B · Nền GitOps** | 11/2026 | Repo GitOps + Kustomize; Argo CD cho dev; External Secrets; staging; CI build + SBOM + trivy image + cosign | kube-prometheus-stack đầy đủ + exporter DB/Kafka/GPU; OTel Collector; SLO + burn rate; runbook gắn alert | MLflow (tracking + registry); benchmark harness; DVC cho benchmark corpus; data-metrics exporter |
| **C · Tin cậy** | 12/2026 | Argo CD staging/prod; Argo Rollouts canary; Kyverno; Renovate; Velero | Dashboard DataOps, Eval trend; blackbox; diễn tập DR đầu tiên | Shadow + canary LLM qua LiteLLM; eval regression theo sự kiện; feedback người dùng |
| **D · Mở rộng** | Q1/2027 | Cluster HA 3 node; Longhorn; Harbor | Frontend RUM; on-call chính thức | GPU Operator + MIG/Kueue; vLLM trong k8s; MLOps cho model dự báo/EWS; fine-tune nếu eval chứng minh cần |

---

## 8. Phân công

| Hoạt động | DevOps/SRE | Backend | Data eng | AI eng | QA | Research |
|---|---|---|---|---|---|---|
| GitOps, CI build, secret, hạ tầng | **R/A** | C | C | C | I | |
| Instrument OTel, metric ứng dụng | C | **R** | C | C | | |
| Alert rule, SLO, dashboard | **R** | C | C | C | C | |
| Backup/DR, diễn tập | **R/A** | C | C | | I | |
| Model registry, benchmark, canary LLM | C | C | | **R/A** | C | |
| Golden set, eval regression | | C | C | C | **R** | **A** |
| DataOps (connector test, DQ, freshness) | C | | **R/A** | | C | C |

(R = thực hiện · A = phê duyệt · C = tham vấn · I = được thông báo)
