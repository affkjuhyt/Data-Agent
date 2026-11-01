# 08. Kế hoạch chuyển khỏi deployable cũ

> Ngày 11/10/2026. Tài liệu này biến quyết định ADR-28 trong [06](06-api-v1.md) thành một danh sách
> kiểm soát cutover. V3 có năm khối đích (`api`, `worker-ingest`, `worker-agent`, `crawler`,
> `ml-forecast`), nhưng việc thay thế là theo capability. Không xoá workload cũ chỉ vì khối đích đã
> có tên; phải có replacement chạy thật và một kiểm thử qua gateway.

## 1. Nguyên tắc

- CSDL V3 không tương thích với schema cũ. Vì vậy sau khi `migrate` áp baseline V3, workload cũ có thể
  vẫn tồn tại trong repo nhưng không được coi là tương thích với database mới.
- Tắt traffic trước, xoá deployment sau. Giữ code và image cũ trong một cửa sổ rollback cho tới khi
  replacement vượt qua smoke/e2e và không còn consumer cũ.
- Mỗi lần xoá phải tìm cả tên package và tên runtime: `workflow_runtime`/`workflow-runtime`,
  `auth_api`/`auth-api`, topic Kafka, tên Secret và route Kong.
- Database role chỉ xoá sau workload cuối cùng dùng role đó đã biến mất khỏi manifest, Job, script và
  consumer. Không xoá role chỉ vì không thấy một import Python.

## 2. Inventory hiện tại

| Workload cũ | Manifest / image | Replacement V3 | Trạng thái | Điều kiện xoá |
| --- | --- | --- | --- | --- |
| `auth-api` | đã xoá 01/11/2026 | `api` (`org`, `integration`: token/capability, api client bằng client-credentials) | Đã xoá | không frontend hay dịch vụ V3 nào gọi `/api/auth`; Kong xoá service và route cũ, plugin bỏ nhánh khoá `sk-`; e2e audience chạy qua `/v1` (`tests/integration/test_jwt_audience.py`) |
| `workflow-runtime` | `service/workflow-runtime.yaml`, `dataagent/workflow_runtime` | `api` cho resource/control + `worker-agent` cho execution | Giữ | `worker-agent` chạy agent turn thật, resume/HITL, outbox và sandbox; Kong không còn route runtime cũ |
| `kb-backend` | đã xoá mã 22/10/2026 (KB-08) | `api` (document, catalog, conflict) + `worker-document`, `worker-agent`, `crawler` | Đã xoá, cổng §4 đạt 01/11/2026 | ingest/search/catalog mới chạy trên V3 và các `/api/data`, `/api/knowledge`, `/api/pipeline` đã có replacement |
| `kb-promote-*` | đã xoá mã 22/10/2026 (KB-08) | `worker-document` + `worker-agent` (`document.indexed`) | Đã xoá, cổng §4 đạt 01/11/2026 | Kafka consumer/group mới xử lý đủ Bronze/Silver/Gold, retry và DLQ |
| `indicator-api` | đã xoá 12/10/2026 | module của `api` (mọi route của nó chỉ trả 501) | Đã xoá | không còn Kong route, lời gọi frontend, import hay topic nào; manifest, Ansible, Makefile, CI, secret xoá cùng commit |
| `platform-workers-*` | đã xoá 01/11/2026 | — | Đã xoá | audit, memory nhận topic chỉ `workflow-runtime` (đã xoá) phát; schema-crawler đọc/ghi bảng KB cũ (`KBWarehouse*`, `SchemaCatalog`) không có trong CSDL V3, V3 quét schema trong `worker-agent` (`schema_scans.py`); topic, ACL, group Kafka và user SASL của chúng xoá cùng commit |
| `model-service` | đã xoá 01/11/2026 | TEI cho embedding, rerank (ADR-35); sinh văn bản qua LiteLLM (ADR-41) | Đã xoá | client duy nhất (`libs/database_connector/model_connector.py`) không còn ai import; Grafana (chỉ có dashboard `kb-ccu` vẽ số của nó và llm-exporter) xoá cùng |
| `sandbox-executor` | đã xoá 01/11/2026 | — (V3 không chạy code người dùng; sandbox, khi cần, nằm trong `worker-agent`, tab 1 §7.2) | Đã xoá | bên gọi duy nhất là `workflow-runtime` (đã xoá); không khối V3 nào gọi; manifest, namespace `dataagent-sandbox`, RBAC, Ansible, OpenBao, CI, suite và các ca eval của nó xoá cùng commit |
| `llm-exporter` | đã xoá 01/11/2026 | `model/bench` (TTFT, decode, throughput theo vai trò) | Đã xoá | chỉ dashboard Grafana `kb-ccu` của KB cũ đọc số của nó |

Frontend không phải legacy backend để xoá. Nó chỉ chuyển từng màn từ mock/`/api` sang `/v1` theo nhóm
đã vào `COMPLETE_GROUPS`.

## 3. Thứ tự cutover

### 3.1 API

- [x] Manifest API dev/production và migration gate.
- [x] Ansible `service_workloads`.
- [x] Secret URL `DATAAGENT_DB_URL__SVC_*` từ `vault_db_role_passwords`.
- [x] Kong `/v1` và audience `dataagent-api`.
- [ ] Điền tất cả role API vào vault, chạy `make deploy` với `apply_jobs=true` khi cần.
- [ ] Smoke `/healthz`, `/v1/openapi.json`, một GET có token và một POST có transaction V3.
- [ ] E2E qua NodePort Kong; chỉ sau đó mới tắt mock cho nhóm tương ứng.

### 3.2 Worker và pipeline

Không xoá `workflow-runtime`, KB workers hoặc crawler trước khi các khối `worker-agent`,
`worker-ingest`, `crawler` có image, manifest, secret, Kafka ACL và test integration riêng. Đây là
phần còn thiếu lớn nhất giữa tài liệu ADR-28 và repo hiện tại.

### 3.3 Xoá từng workload

Khi một replacement đạt gate:

1. bỏ route Kong cũ, nhưng giữ route mới và kiểm tra 401/404 đúng;
2. scale workload cũ về 0 trong một release;
3. quan sát Kafka lag, DB audit và error rate;
4. xoá Deployment/Service, Secret, `service_workloads`, image mapping và test riêng;
5. chỉ xoá code sau khi `rg` cả package name lẫn runtime name không còn consumer/docs/manifest;
6. cuối cùng mới xoá DB role, topic/group/ACL và migration/test legacy còn sót.

## 4. Checklist bằng chứng bắt buộc

- `kubectl` không còn pod/Job của workload cũ.
- Kong không còn service/route/plugin cũ.
- Không còn tham chiếu trong `deploy/`, `Makefile`, CI, test và docs.
- Kafka group không còn lag hoặc consumer cũ.
- CSDL audit không ghi lỗi permission từ replacement.
- E2E qua gateway đạt cho luồng đã thay thế.
- Rollback đã thử bằng cách bật lại workload cũ trên một database tương thích, không phải CSDL V3.

### Bằng chứng KB-08 (kb-backend, kb-promote-*, kb-structured-data, kb-conflict-resolution, web-crawler), 01/11/2026, cụm k8s-local

- `kubectl`: không còn object nào của các workload này. Sáu Secret còn sót được xoá bằng task Ansible
  "Remove the retired knowledge base and web-crawler" (mọi cụm từng chạy chúng được dọn như nhau).
- Kong: không còn service `kb-backend` và route `/api/data`, `/api/knowledge`, `/api/pipeline`,
  `/api/fleet`. Job `setup` không apply được từ 17/10 (manifest dev không phải YAML), nên route cũ còn
  đó tới khi sửa.
- Tham chiếu: chỉ còn các bước xoá đồ đã bỏ và ghi chú lịch sử; comment và docs còn gọi
  `kb-backend` như một bên đang chạy đã được sửa.
- Kafka: không còn topic `kb.*`, không còn consumer group.
- CSDL: không có `permission denied` nào trong 72 giờ log Postgres (main và audit).
- E2E qua gateway: `tests/integration/test_kb_replacement_e2e.py`, 12/12 (route cũ là "no Route
  matched" của Kong; `/v1` documents, collections, document-sources, datasets, sources, crawl-sources,
  conflicts và search/documents trả 200 cho người dùng demo).
- Rollback: image `kb-backend` dựng lại từ commit trước khi xoá (5dc31db5), schema cũ (59 bảng, có
  `KBData`) tạo từ ORM của chính commit đó trên một Postgres riêng, không phải CSDL V3; dịch vụ khởi
  động (`KB_LITE`) và `/health` trả 200.

Cụm k8s-local chạy lát V3 (`make deploy-local`); `make deploy` đầy đủ không chạy được trên node đơn
này (đã đặt trước 82% bộ nhớ) và không cần cho các bằng chứng trên.

## 5. Các phần không được xoá trong đợt này

PostgreSQL/audit, PgBouncer, Kafka, Redis, MongoDB, MinIO, Qdrant, Neo4j, ClickHouse, Keycloak,
Kong, OpenBao và observability là hạ tầng dùng chung. Việc API-06 hoàn tất không phải lý do để xoá
chúng. Các revision Alembic cũ cũng chỉ xoá theo kế hoạch migration riêng, sau khi backup và có baseline
V3 đã được nghiệm thu.
