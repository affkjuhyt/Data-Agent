# 10. Thiết kế database V2: hiện trạng, quy ước, bố cục

> Đối tượng: hai senior viết hợp đồng dữ liệu (P0-09), BE làm S1-01/S1-02, trưởng nhóm. Đo ngày
> 27/09/2026 trên nhánh `feat/remove-redundant-service` và Postgres của cụm dev (truy vấn chỉ đọc).
> Bảng và cột cụ thể của từng object nằm ở [03](03-target-design.md) mục 2–3; file này chốt **quy
> ước chung** mà mọi bảng V2 phải theo và **những gì của hệ hiện tại không được mang sang**. Bảng
> chi tiết của luồng đầu vào (nguồn → release → candidate → `obs`): [14](14-input-data-db.md).

## 0. Kết luận

1. Không bảng hiện có nào chứa được dữ liệu V2: thiếu bốn trục thời gian, raw bị xoá/ghi đè, không có
   bảng append-only thật, OCC khác dạng. V2 dựng **schema mới** cạnh `public`, không sửa dần bảng cũ.
2. Bảng cũ (`KB*`, agent, auth) giữ nguyên cho POC, **không xây thêm tính năng V2 lên chúng**. Bảng
   release management đã xoá cùng `release_worker` (migration `e4a6c8b0d2f1`, 28/09).
3. Ba lỗi tách tenant phải sửa trước STEP 1 vì V2 dựa vào RLS: dịch vụ chạy bằng superuser, bảng có
   `tenant_id` mà không bật RLS, `KBData` unique `name` trên toàn bảng (mục 2.2).
4. Nguồn sự thật chỉ gồm Postgres, MinIO (raw theo SHA-256) và ClickHouse `obs`. Qdrant, Neo4j là bản
   chiếu dựng lại được, ghi qua outbox. MongoDB không nhận dữ liệu V2.

## 1. Hiện trạng

| Kho | Đang giữ | Vai trò thực tế |
| --- | --- | --- |
| Postgres `dataagent` | 84 bảng trong `public` (47 có RLS), `langgraph` (checkpointer), `kb_structured` (bảng tạo lúc chạy), `keycloak`, `kong` | Nguồn sự thật chính, không có ranh giới schema |
| MongoDB | Canvas agent (`flow_nodes/edges`), silver staging, conflict payload (có embedding), log ingest, pipeline events | **Nguồn sự thật** cho canvas, silver, conflict; không backup; canvas/log/event không có `tenant_id` |
| Qdrant | `knowledge`, `knowledge_documents`, `entity_registry`, `query_cache`, memory, `schema_catalog` | Index; embedding **chỉ** nằm ở đây, dựng lại phải embed lại |
| Neo4j | `(:Entity)-[:RELATES]-(:Entity)` theo tenant + KB | Bản chiếu của `KBNeo4jNode/Relationship` |
| MinIO | `knowledge-base/{tenant}/bronze/{data_id}/{filename}` | Raw file; **bị xoá và ghi đè** khi re-crawl, trash, rollback |
| ClickHouse | Chỉ Langfuse | Chưa có `obs` |
| Redis | Cache hội thoại, schema, kết quả KB; `plan_session`, `analytical_slots` | Có key mang trạng thái dưới `allkeys-lru` |
| Backup | `pg_dump` 6 giờ/lần vào PVC cùng node | MinIO, Mongo, Neo4j, Qdrant không có backup |

Số đo trên cụm dev: 76 bảng tên PascalCase có ngoặc kép, 8 bảng snake_case; 77/84 bảng gần như rỗng
(dữ liệu dev ít, không phải bằng chứng bảng chết). Sau đó 13 bảng chết đã xoá (mục 5). Ngày 28/09:
31 migration, một head `e4a6c8b0d2f1`.

## 2. Vấn đề

### 2.1 Vi phạm bất biến của 00

| Vấn đề | Bằng chứng | Bất biến |
| --- | --- | --- |
| Không có `event_time`, `publication_time`, `available_at`, `system_time` ở bảng nào | `information_schema.columns` | 8, 9 |
| Re-crawl xoá `KBData`, `KBLifecycleHistory` và object MinIO cũ; nội dung bị ghi đè tại chỗ | `platform_workers/workers/web_crawler/bronze.py:69-157` | 2 |
| Bảng "lịch sử" ghi đè được: `DataAccessLog` tự ghi "append-only NOT ENFORCED", `app_user` có UPDATE/DELETE trên mọi bảng | migration `d1a2t3a4a5c6`, `deploy/docker/sql/grant-roles.sql:43-58` | 2, 10 |
| Một luồng Gold ghi Postgres + Qdrant + Neo4j + Mongo, chỉ có bù trừ khi lỗi; `_cleanup_previous_promotion` xoá Qdrant/Neo4j trước khi commit Postgres | `libs/ingestion/gold/persistence.py:123-293` | ADR-V2-15 |
| OCC là `row_version uuid` trên 6 bảng KB; worker tăng mà không kiểm tra | migration `c7d8e9f0a1b2`, `bronze.py:176` | 10 |

### 2.2 Tách tenant và bảo mật (sửa trước STEP 1)

| Vấn đề | Bằng chứng |
| --- | --- |
| Superuser `admin` (`rolsuper`, `rolbypassrls`) là tài khoản của auth_api, bốn platform_workers (kể cả web_crawler ghi `KBData`), model-service, CronJob memory-cleanup → RLS không có tác dụng | `pg_stat_activity`; `deploy/k8s/create-secrets.sh`; `platform_workers/common/settings.py:52` |
| Có `tenant_id` mà không bật RLS: `KBKnowledgeBase`, `KBEntityLookup`, `KBNeo4jNode/Relationship`; không RLS: `KBSourceConfig/Run`, `KBStructuredImportJob`, `Messages`, `AgentTraces`, `Workflows*`; `kb_structured.*` mặc định tắt | `pg_class.relrowsecurity` |
| `KBData` có `UNIQUE(name)` toàn bảng: hai tenant không thể có tài liệu trùng tên. ORM khai báo unique theo tenant nhưng không migration nào tạo | constraint `KBData_name_key`; `knowledge_base_orm.py:104` |
| `POST /api/knowledge/neo4j/query` chạy Cypher tuỳ ý; kiểm tra chỉ theo tiền tố nên `MATCH … DETACH DELETE` lọt; query bỏ qua `$tenant_id` thì đọc được tenant khác | `kb_backend/operations/neo4j_ops.py:146` |
| SSE `/documents/{data_id}/stream` không kiểm tra tài liệu thuộc tenant (đã xoá route: frontend dùng `/progress`) | `kb_backend` router ingestion, so với `/logs` |

### 2.3 Hygiene

- Bốn họ bảng trùng: `SecretsVault/secrets_vault`, `APIKeys/api_keys`, `IPAllowlists/ip_allowlists`,
  `KeyRotations/key_rotations` (bản PascalCase là bản chết); `PlatformSettings` và `platform_settings`.
- `created_at` và `inserted_at` cùng có trên hầu hết bảng; hai kiểu xoá mềm (`is_deleted`, `deleted_at`);
  status là chuỗi tự do, không CHECK, không enum.
- ORM và migration lệch: 6 migration chỉ để vá bảng ORM đã khai báo; DDL lúc chạy (auth_api tự tạo
  `platform_settings`, `compliance_settings`, `key_rotations`; `release_worker` từng tạo partition cho
  bảng `release_history` không tồn tại, nay đã gỡ cùng các bảng release); CI không có `alembic check`.
- Xoá cạnh Neo4j theo `data_id` không xoá gì (cạnh `RELATES` không có thuộc tính `data_id`), để lại node
  và cạnh mồ côi; `KBTextBlockVersion` không unique `(block_id, version_number)`.

## 3. Quy ước bắt buộc cho mọi bảng V2

Các ADR dưới đây bổ sung vào [07](07-decisions-and-risks.md) mục 2 khi nhóm duyệt file này.

| ADR | Quy ước | Lý do |
| --- | --- | --- |
| ADR-V2-26 | Mỗi bounded context một schema: `registry`, `ingest`, `governance`, `knowledge`, `forecast`, `alert` (Postgres) và `obs` (ClickHouse). Không bảng V2 nào ở `public` | Ranh giới sở hữu (00 mục 7: "DB chỉ service sở hữu truy cập"); quyền cấp theo schema |
| ADR-V2-27 | Tên `snake_case` không ngoặc kép; PK `uuid` tên `<object>_id`; FK có index | Bỏ tình trạng lẫn PascalCase/snake_case |
| ADR-V2-28 | Mọi bảng có `tenant_id uuid NOT NULL`, `ENABLE` + `FORCE ROW LEVEL SECURITY`, policy theo `app.tenant_id`. Bảng không thuộc tenant (danh mục đơn vị, lịch giao dịch) nằm ở `registry` và ghi rõ là dùng chung | Bất biến 8 và cách ly tenant; tránh lỗi mục 2.2 |
| ADR-V2-29 | Bảng có thể sửa có `version int NOT NULL DEFAULT 1`; mọi UPDATE mang `WHERE version = :expected_version`, 0 dòng → `409 VERSION_CONFLICT` | Bất biến 10; thay `row_version uuid` |
| ADR-V2-30 | Bảng append-only (`ingest.release`, `governance.correction/override/approval`, audit, lineage) không có quyền `UPDATE/DELETE` cho role ứng dụng **và** có trigger chặn. Sửa là thêm dòng mới | Bất biến 2; "append-only NOT ENFORCED" hiện tại |
| ADR-V2-31 | Cột thời gian theo 03 mục 3.1, tất cả `timestamptz`; chỉ `created_at`, `created_by` cho metadata, không `inserted_at` | Một nghĩa cho mỗi cột |
| ADR-V2-32 | Status có `CHECK (status IN (...))`; máy trạng thái (review_case, alert) chặn chuyển trạng thái sai bằng trigger hoặc ở một hàm duy nhất trong service sở hữu | Không còn chuỗi tự do |
| ADR-V2-33 | Mỗi service một role `NOBYPASSRLS`, chỉ được cấp trên schema mình sở hữu. `admin` chỉ dùng cho migrate. Job chạy mọi tenant dùng role bảo trì riêng, chỉ được cấp đúng bảng cần | Mục 2.2: RLS hiện vô hiệu |
| ADR-V2-34 | Nguồn sự thật: Postgres, MinIO `raw/{source_id}/{sha256}`, ClickHouse `obs`. Qdrant và Neo4j chỉ ghi qua bảng outbox (cùng transaction với dữ liệu) + Kafka, dựng lại được từ Postgres. Lưu `embed_model` và vector ở Postgres để re-index không cần gọi model | ADR-V2-15; mục 2.1 |
| ADR-V2-35 | Mọi DDL qua Alembic; CI chạy `alembic check`; không tạo bảng lúc chạy (ngoại lệ duy nhất: `kb_structured`, ghi rõ) | Mục 2.3 |

## 4. Bố cục

```text
Postgres dataagent
  registry     entity, entity_relation, series, capability, operator, dq_rule, authority_rule
  ingest       release, profile, mapping_version, observation_candidate, outbox
  governance   dq_result, observation_conflict, review_case, correction, override, approval
  knowledge    claim, event, evidence, relation, document_region
  forecast     model_version, forecast_run            (ngoài POC)
  alert        alert, alert_member, notification
  public       bảng cũ: KB*, agent, auth — giữ, không mở rộng
ClickHouse obs observation (mọi effective version, chỉ ghi thêm)
MinIO          raw/{source_id}/{sha256}                (không ghi đè, không xoá)
Qdrant, Neo4j  bản chiếu từ knowledge.* và chunk KB, dựng lại qua outbox
```

Role: `registry_svc`, `ingest_svc`, `governance_svc`, `knowledge_svc`, `alert_svc` (có thể gộp trong
`indicator-api` cho POC nhưng vẫn tách role), `crawler_svc` (web_crawler, ADR-V2-46), `outbox_relay`,
`maintenance`; `obs_writer` (ClickHouse, chỉ INSERT), `obs_reader` (agent, chỉ SELECT effective).
Schema và role Postgres tạo ở migration `5b7d9f1a3c6e` (NOP-147): role chủ schema có quyền ghi mặc định
trên bảng của schema mình; role khác chỉ có `USAGE`, quyền đọc từng bảng do migration tạo bảng cấp. Mật
khẩu đặt ở Job `db-init` từ vault, chưa có cho các role V2 (làm cùng NOP-214).

Nối với bảng cũ: `ingest.release.artifact_sha256` trỏ object MinIO; `KBData` giữ vai trò bản tìm kiếm
của KB (ADR-V2-21) và mang thêm `artifact_sha256` để nối về raw. Không FK chéo từ schema V2 vào `public`.

## 5. Dữ liệu và kho cũ: giữ gì, dọn gì, khi nào

| Việc | Khi nào / trạng thái | Ghi chú |
| --- | --- | --- |
| Xoá 4 bảng PascalCase chết (`SecretsVault`, `APIKeys`, `IPAllowlists`, `KeyRotations`) | **Xong** (migration `2e2377872373`) | Đều 0 dòng; auth_api dùng bản snake_case; downgrade tạo lại cả RLS |
| `KBData`: unique `(tenant_id, name)` thay `UNIQUE(name)` | **Xong** (migration `b3d9ac7ba0a4`) | Kèm hai index keyset ORM đã khai báo |
| Xoá 13 bảng chết: release management (`Pipelines`, `PipelineSteps`, `ReleasePackages`, `ReleaseApprovals`, `EnvironmentConfigs`, `ReleaseHistory`, `RollbackOperations`, `DriftEvents`), `Webhooks`, `PIIAccessLogs`, `KBPublishAPI`, `KBNeo4jConnection`, `Plans`; cùng cột `Tenants.plan_id`, `KBNeo4jNode.connection_id` | **Xong** (migration `e4a6c8b0d2f1`, 28/09) | 0 dòng trên cụm dev trừ dòng seed; chỉ `release_worker` (gỡ cùng lúc) đọc/ghi bảng release. Cùng migration: `AuditLogs.id` có default `gen_random_uuid()` để audit worker ghi lại được |
| `/neo4j/query` nhận nhãn (đã kiểm tra), server tự dựng Cypher có lọc tenant, chạy session READ; route SSE `/stream` không ai gọi nên đã xoá | **Xong** | Không còn nhận Cypher tuỳ ý từ client |
| Xoá cạnh Neo4j theo tài liệu dựa vào mirror `KBNeo4jRelationship` | **Xong** cho trash flush; re-crawl của web_crawler còn no-op, sửa cùng S2-02 | web_crawler là mã sync, `GraphStore` là async |
| Bật RLS cho bảng mục 2.2 và chuyển service khỏi `admin` | Song song W1–W2 | Mỗi bảng phải kiểm service đọc nó đã đặt `app.tenant_id` chưa, nếu không sẽ trả rỗng |
| MinIO raw theo SHA-256, không xoá | S2-02 | ADR-V2-21 |
| Canvas, silver staging, conflict payload chuyển từ Mongo sang Postgres JSONB | Sau POC | Không thêm dữ liệu mới vào Mongo |
| Key trạng thái Redis (`plan_session`, `analytical_slots`) chuyển sang Postgres hoặc Redis không LRU | Sau POC | |
| Backup ngoài node cho Postgres và MinIO raw | P0-07 | Dữ liệu đã duyệt không tái tạo được từ nguồn |
| Network policy production cho `postgres-backup` vào Postgres | **Xong** | Trước đó backup production bị chặn bởi default-deny |

## 6. Câu hỏi cần chốt

| # | Câu hỏi | Đề xuất |
| --- | --- | --- |
| DB1 | Vector lưu ở Postgres bằng `pgvector` (phải đổi image `postgres:16-alpine`) hay chỉ lưu `embed_model` + hash để biết khi nào cần embed lại? | POC: chỉ `embed_model` + hash; pgvector sau POC nếu re-index quá chậm |
| DB2 | Có tách `indicator-api` thành nhiều role ngay trong POC không? | Có: role rẻ, sửa quyền về sau mới đắt |
