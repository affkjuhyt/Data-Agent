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
| `auth-api` | `service/auth-api.yaml`, `dataagent/auth_api` | `api` (`org`, token/capability) | Giữ | API thay được `/api/auth`, API-key verification và mọi client cũ; e2e auth qua `/v1` |
| `workflow-runtime` | `service/workflow-runtime.yaml`, `dataagent/workflow_runtime` | `api` cho resource/control + `worker-agent` cho execution | Giữ | `worker-agent` chạy agent turn thật, resume/HITL, outbox và sandbox; Kong không còn route runtime cũ |
| `kb-backend` | `service/kb-backend.yaml`, `dataagent/kb-backend` | `api` cho catalog/query + `worker-ingest`/`crawler` | Giữ | ingest/search/catalog mới chạy trên V3 và các `/api/data`, `/api/knowledge`, `/api/pipeline` đã có replacement |
| `kb-promote-*` | bốn manifest `kb-promote-*`, image `kb-worker` | `worker-ingest` | Giữ | Kafka consumer/group mới xử lý đủ Bronze/Silver/Gold, retry và DLQ |
| `indicator-api` | đã xoá 12/10/2026 | module của `api` (mọi route của nó chỉ trả 501) | Đã xoá | không còn Kong route, lời gọi frontend, import hay topic nào; manifest, Ansible, Makefile, CI, secret xoá cùng commit |
| `platform-workers-*` | audit, memory, schema-crawler, web-crawler | `worker-ingest`, `crawler`, audit/memory worker V3 | Giữ | từng topic, group, ACL và state store có consumer thay thế; không còn CronJob/manifest tham chiếu |
| `model-service` | `service/model-service.yaml`, `dataagent/model_service` | Chưa được đặc tả rõ trong V3 | Giữ | Có quyết định riêng về LLM/model proxy; không xoá theo suy đoán |
| `sandbox-executor` | `service/sandbox-executor.yaml`, `dataagent/sandbox_executor` | Có thể nằm trong `worker-agent` | Giữ | worker-agent có boundary xác thực, policy OpenBao và test isolation tương đương |
| `llm-exporter` | `service/llm-exporter.yaml` | Observability V3 | Giữ | metric thay thế đã được Prometheus scrape và dashboard kiểm chứng |

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

## 5. Các phần không được xoá trong đợt này

PostgreSQL/audit, PgBouncer, Kafka, Redis, MongoDB, MinIO, Qdrant, Neo4j, ClickHouse, Keycloak,
Kong, OpenBao và observability là hạ tầng dùng chung. Việc API-06 hoàn tất không phải lý do để xoá
chúng. Các revision Alembic cũ cũng chỉ xoá theo kế hoạch migration riêng, sau khi backup và có baseline
V3 đã được nghiệm thu.
