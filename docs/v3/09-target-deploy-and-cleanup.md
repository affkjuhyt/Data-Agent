# 09. Kiến trúc đích: đặt tên, dọn mã cũ, triển khai theo thiết kế

> 11/10/2026. Kế hoạch đưa repo về đúng tài liệu thiết kế (tab 1 §7.2, tab 6 ADR-28, O7, tab 7 F1/F7).
> [08](08-legacy-cutover.md) giữ danh sách kiểm soát cutover từng workload; tài liệu này chốt **đích**,
> **tên** và **thứ tự** để danh sách đó có điểm đến. Chỗ thiết kế chưa quy định được đánh dấu *đề xuất*.

## 1. Đích theo thiết kế

ADR-28: modular monolith, 5 khối triển khai, ranh giới module là schema và vai trò `svc_<schema>`. Thêm
`web` (tab 7 F1, ảnh nginx-unprivileged) và Job `migrate`. Mọi thứ đứng sau gateway và Keycloak (ADR-30).

| Khối | Mã | Image | Nội dung (O7.4) | Vai trò CSDL |
| --- | --- | --- | --- | --- |
| `api` | `backend/services/api` | `dataagent/api` | Mọi module phục vụ yêu cầu đồng bộ, mỗi module một schema | `svc_` của 23 schema |
| `worker-agent` | `backend/services/worker_agent` | `dataagent/worker-agent` | Lượt chạy agent (runtime mỏng trên Pydantic, ADR-37), chốt kỳ, đánh giá, theo dõi; Temporal từ R3 | `svc_agent`, `svc_qa`, `svc_query`, `svc_period`, `svc_eval`, `svc_monitor`, `svc_inbox`, `svc_notify`, `svc_exec_worker` |
| `worker-ingest` | `backend/services/worker_ingest` | `dataagent/worker-ingest` | Nạp, parse tài liệu (Docling, ADR-34), embedding, biên soạn tri thức | `svc_ingest`, `svc_pipeline`, `svc_document`, `svc_conflict`, `svc_catalog`, `svc_context` |
| `crawler` | `backend/services/crawler` | `dataagent/crawler` | HTTP và Playwright trong sandbox, chỉ ra ngoài qua egress proxy | `svc_acquire` |
| `ml-forecast` | `backend/services/ml_forecast` | `dataagent/ml-forecast` | Backtest, huấn luyện | `svc_forecast` |
| `web` | `frontend` | `dataagent/web` | SPA build tĩnh sau gateway | — |
| `migrate` (Job) | `backend/alembic` | `dataagent/migrate` | Áp DDL sinh từ `backend/schema` | `migrator` không phải superuser (tab 1 §8.14) |

*Đề xuất về chia sẻ mã:* phần nghiệp vụ của một schema mà cả `api` lẫn một worker cần (ví dụ `qa` cho
câu trả lời, `context` cho biên soạn) chuyển từ `services/api/modules/<schema>` sang
`backend/modules/<schema>` ở lần đầu một worker cần; route HTTP vẫn ở `api`. `.importlinter` cấm module
gọi chéo ngoài giao diện công khai (ADR-28).

## 2. Quy tắc đặt tên

| Thứ | Quy tắc | Ví dụ |
| --- | --- | --- |
| Gói Python | `snake_case`, trùng tên khối | `services/worker_agent` |
| Image, Deployment, Service, ConfigMap, Secret | `kebab-case`, trùng tên khối | `worker-agent`, `worker-agent-env`, `worker-agent-secrets` |
| Biến môi trường | Tiền tố hệ con | `DATAAGENT_DB_URL__SVC_QA`, `API_OIDC_ISSUER` |
| Topic Kafka | `miền.sự_kiện`; CDC `cdc.{source}.{table}`; khai trong Job `kafka-topics` (tab 1 §8.12) | `metric.published` |
| Bucket MinIO | `da-*` cho bucket mới | `da-archive` |
| Chỉ số Prometheus | `da_<service>_<đại lượng>_<đơn vị>` (O1.1) | `da_api_request_seconds` |

Bảng tên cũ, áp khi xoá chứ không đổi tên tại chỗ (đổi tên một service cũ đang chạy là thay đổi
deploy riêng, CLAUDE.md §3):

| Cũ | Thay bằng |
| --- | --- |
| `auth_api` / `auth-api` | module `org` + `integration` trong `api` |
| `workflow_runtime` / `workflow-runtime` | `api` (agent, qa, workspace, inbox) + `worker-agent` (thực thi) |
| `sandbox_executor`, `sandbox-runner` | sandbox của `worker-agent` (tab 1 §7.2) |
| `kb_backend`, `kb-worker` (`kb-promote-*`, `kb-structured-data`, `kb-conflict-resolution`) | `api` (document, catalog, conflict) + `worker-ingest` |
| `platform_workers` (audit, memory, schema-crawler, web-crawler), `llm-exporter` | audit: Audit service ghi `dataagent_audit`; memory: `worker-agent`; schema-crawler: `worker-ingest`; web-crawler: `crawler`; llm-exporter: chỉ số vLLM qua OpenTelemetry (ADR-40) |
| `indicator_api` | đã xoá 12/10/2026: `api` phục vụ đủ A2, mọi route của nó chỉ trả 501 |
| `model_service` | TEI hoặc Infinity cho embedding, rerank (ADR-35); sinh văn bản qua vLLM sau Model gateway (ADR-41) |

## 3. Lộ trình dọn mã cũ

Nguyên tắc: một workload cũ chỉ bị xoá khi khối thay nó chạy thật và qua cổng ở [08 §4](08-legacy-cutover.md).
Xoá theo năng lực, không theo tên.

| Giai đoạn | Làm | Xoá được sau đó | Điều kiện |
| --- | --- | --- | --- |
| G0 · ngay | Dọn thứ đã chết: thư mục test rỗng `tests/integration/{auth_api,platform_workers}` và dòng CI trỏ tới chúng; mục sai trong `build-push-images.sh` (`llm_exporter` không có Dockerfile); README Ansible còn nói `migrate` chép `basemodel` | Như cột trái | Không có |
| G1 · Hỏi đáp chạy trên V3 | `worker-agent` khung: nhận câu hỏi từ outbox `qa`, Plan IR + bộ thực thi tất định, ghi `qa.answer`, đẩy tiến độ SSE qua Redis `sse:{question_id}`; màn Hỏi đáp gọi `/v1/questions` thay `/api/orchestrate` | Route Kong `/api/orchestrate*`, `/api/ask*`, `/api/catalog`; sau đó `workflow_runtime`, `sandbox_executor`, CronJob `memory-cleanup`, `libs/semantic` (phần eval chuyển theo) | E2E Hỏi đáp qua gateway; eval gate ≥ 90 % (O6.5) |
| G2 · Quản trị | Nhóm A2 Quản trị và Tích hợp: `/v1/me*`, người dùng, vai trò, khoá API, danh sách IP, bí mật | `auth_api`, route `/api/auth*`, `AppStateContext` gọi `/api/auth/me` | Đăng nhập, khoá API, MCP client qua `/v1` |
| G3 · Dữ liệu | `worker-ingest`: tài liệu, quét nguồn (`catalog.scan_run`), biên soạn tri thức; nhóm A2 Nguồn và danh mục, Tài liệu, Nạp dữ liệu, Lớp ngữ cảnh, Xung đột | `kb_backend`, `kb-worker`, `platform-workers-schema-crawler`, `libs/ingestion`, Neo4j (ADR-04) | Tài liệu mẫu đi hết Bronze → Silver → Gold trên V3; tìm kiếm Qdrant đạt |
| G4 · Thu thập, dự báo | `crawler` và `ml-forecast`; nhóm A2 Thu thập, Dự báo | `platform-workers-web-crawler` (`indicator_api` đã xoá 12/10/2026) | Lượt thu thật qua egress proxy; backtest chạy |
| G5 · Dọn cuối | Audit service và ghi `dataagent_audit`; outbox qua Debezium thay `libs/shared/outbox.relay_loop` | `platform_workers`, `model_service`, `llm-exporter`, `libs/{basemodel,database_connector,shared,contracts}`, MongoDB nếu không còn canvas; test toàn repo đang nằm trong `tests/unit/workflow_runtime` chuyển sang `tests/unit/repo` trước khi xoá suite | `rg` cả tên gói lẫn tên runtime không còn kết quả |

**Xung đột cần chốt:** backlog R0 (R0-8, R0-10, R0-11, đã commit) vẫn thêm tính năng vào
`workflow_runtime`. Mỗi việc như vậy là mã phải chuyển lại vào `worker-agent` ở G1. Đề xuất: đóng băng
tính năng mới trong dịch vụ cũ, phần còn lại của R0 làm thẳng trong `worker-agent`.

## 4. Triển khai theo thiết kế

### 4.1 Môi trường

| | Dev (hiện có) | Khách (Bảng 7.2, O5) |
| --- | --- | --- |
| Cụm | k3s một nút, 1 × A100 40 GB | Cụm ứng dụng 3–5 nút 16 vCPU / 64 GB; cụm GPU riêng; vùng DMZ |
| PostgreSQL | Một bản qua PgBouncer | Primary + standby, WAL liên tục, pgBackRest (R4) |
| Kho khác | Một bản mỗi kho | Qdrant 3, MinIO 4, Kafka 3; site DR khoảng một nửa, RPO 15 phút, RTO 4 giờ |
| Gói GPU | — | nhỏ / vừa / lớn (O5), chốt ở Cổng 0 (ADR-38) |

### 4.2 Thành phần hạ tầng

| Giữ | Thêm | Bỏ |
| --- | --- | --- |
| PostgreSQL 16 + PgBouncer (transaction pooling, ADR-31); Kafka; Redis; MinIO; Qdrant; OpenBao (thêm Transit cho P16); Keycloak; vLLM + LiteLLM; Langfuse + ClickHouse; Prometheus, Grafana | Debezium / Kafka Connect cho outbox (ADR-06), Apicurio (ADR-32); TEI hoặc Infinity (ADR-35); egress proxy cho `crawler`; Model gateway (ADR-41); Loki, Tempo, Alertmanager qua OpenTelemetry (ADR-40); Temporal từ R3 (ADR-05/33) | Neo4j (ADR-04, sau G3); MongoDB khi canvas không còn (tab 1 §8.0); `model_service`, `llm-exporter`; Kong chỉ khi APISIX qua kiểm `{id}:verb` ở Giai đoạn 0 (ADR-30) |

### 4.3 Sửa ngay trong đường deploy hiện tại

1. **CSDL audit riêng.** `backend/alembic/versions/v3_0001_baseline.py` đang áp cả
   `dataagent_audit.sql` vào CSDL `dataagent`; D1.1 và ADR-01 đặt audit ở CSDL `dataagent_audit` riêng (thời
   hạn lưu, sao lưu riêng). Baseline phải tạo CSDL đó và áp tệp audit vào đó. Chưa cụm nào áp baseline nên
   sửa tại chỗ được.
2. **Migration không dùng superuser.** Job `migrate` đang chạy bằng `admin`; tab 1 §8.14 yêu cầu vai trò
   `migrator` không phải superuser. Vai trò `da_owner`, `svc_*` tạo một lần bằng tài khoản quản trị (bước
   bootstrap của Ansible), sau đó `migrator` chạy Alembic.
3. **Lệch dev và production.** Container init của `api` dùng `migrate-secrets` ở dev nhưng `api-secrets` ở
   production. *Đề xuất:* `deploy/k8s/base` + overlay `dev`/`production` bằng Kustomize (có sẵn trong
   kubectl) thay cho hai thư mục chép tay; Ansible vẫn là đường deploy duy nhất.
4. **Bí mật.** Thiết kế đặt bí mật ứng dụng ở OpenBao (KV, AppRole); hiện `DATAAGENT_DB_URL__*` dựng từ
   ansible-vault. Giữ ansible-vault cho bootstrap, chuyển URL CSDL của từng khối sang OpenBao khi khối thứ
   hai (`worker-agent`) ra đời, để không có hai nguồn.
5. **Script.** `create-secrets.sh`, `create-configmaps.sh`, `kong-setup.sh` thay bằng task Ansible và cấu hình
   khai báo của gateway (CLAUDE.md §5).
6. **`web`.** Cụm dev đang chạy `npm run dev` (`Dockerfile.dev`); tab 7 F1 là image nginx-unprivileged từ bản
   build. Dev giữ được chế độ dev, production dùng `frontend/Dockerfile`.

### 4.4 Mỗi khối mới vào cụm thế nào

Theo mẫu `api` đã làm (06 §API-06): Dockerfile ở thư mục khối; manifest Deployment, ConfigMap
`<khối>-env`, Secret `<khối>-secrets` chỉ chứa URL của đúng các vai trò ở bảng §1; mục trong
`service_workloads`; NetworkPolicy riêng (`crawler` chỉ ra egress proxy); suite test `<khối>` trong
`Makefile`, `run_suites.py`, CI.

## 5. API: tình trạng và việc tiếp

Đã vào `COMPLETE_GROUPS`: Hỏi đáp, Phiên làm việc, Cần bạn và đánh giá, Agent (73 trên 379 endpoint).
Cổng A2 hiện chỉ so đường dẫn, nhóm và năng lực, chưa so thân yêu cầu và mã lỗi ở A3. Đối chiếu tay cho thấy
lệch, ví dụ `POST /v1/hitl-requests/{id}:resolve` nhận `status`/`response` thay vì `decision`, `option`,
`note`, `reassign_to` (A3), không trả 409 khi đã xử lý; `POST /v1/proposals/{id}:decide` trả 409 thay vì 403 khi
người duyệt là người đề xuất và không ghi sự kiện outbox khi đổi trạng thái (D1 mục 8).

Thứ tự:

1. **Cổng A3.** Sinh từ tab 4 A3 trường yêu cầu (bắt buộc `*`) và mã lỗi của từng endpoint vào catalogue; test so
   với model Pydantic của route. Rồi sửa bốn nhóm đã xong cho qua cổng.
2. **Nhóm còn lại**, theo màn người dùng chạm trước và theo G1–G4 ở §3: Quản trị và Tích hợp (G2); Nguồn và
   danh mục, Lớp ngữ cảnh, Tài liệu, Nạp dữ liệu, Xung đột (G3); Theo dõi, báo cáo, thông báo; Kỹ năng; Chính
   sách; Kiểm toán; Kỳ chốt; Biến đổi dữ liệu; Gói ngành; Thu thập, Dự báo (G4); Lớp riêng lãnh đạo (màn P16 đã
   có).

## 6. Cần chốt

| Việc | Đề xuất |
| --- | --- |
| Đóng băng tính năng mới trong `workflow_runtime` | Có, từ R0-11 |
| Kustomize base + overlay | Có |
| APISIX hay giữ Kong | Theo kiểm `{id}:verb` ở Giai đoạn 0 (ADR-30) |
| `model_service` → TEI/Infinity | Có, ở G5 |
| MongoDB | Bỏ khi canvas workflow không còn trong V3 |
| Python | 3.12 cho khối mới (ADR-29); dịch vụ cũ giữ 3.11 tới khi xoá |
