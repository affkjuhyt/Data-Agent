# STM/LTM Memory Architecture — Triển khai

Tài liệu này ghi lại những gì đã được triển khai khi áp dụng
`docs/STM_LTM_Memory_Architecture.docx` vào agent (`services/workflow-runtime`),
cách deploy, và checklist test case. Xem file `.docx` gốc để biết đầy đủ lý
thuyết/tham chiếu — file này chỉ tập trung vào phần thực thi.

## 1. Phạm vi

Đã triển khai toàn bộ 7 loại memory theo doc: **STM**, **Semantic LTM**,
**Episodic Past LTM**, **Episodic Future LTM**, **Procedural LTM**,
**Workflow Memory**, **Schema Memory** (doc §3.7 — xem mục 6).

## 2. Mapping kiến trúc → code

| Khái niệm (doc) | Trạng thái trước | Thay đổi |
|---|---|---|
| STM (Redis + session history) | Đã có (`redis_cache.py`, `Conversations`/`Messages`) | Không đổi |
| Semantic LTM (Qdrant + Postgres) | Có nhưng chỉ ADD đơn giản, không domain/fact_key | Nâng cấp: controlled vocab, deterministic point-id, importance score, cap, 4-decision lifecycle |
| Episodic Past LTM | Chưa có | Mới: bảng `EpisodicMemory` |
| Episodic Future LTM | Chưa có | Mới: bảng `FutureMemory` |
| Procedural LTM | Có (`SystemPrompts` + `AgentVersions.llm_config`/`retrieval_config` + `MemoryPolicy`, đã versioned) | Không đổi |
| Retrieval Score (α·recency + β·importance + γ·relevance) | Chỉ RRF rank | Mới: re-rank theo formula sau khi RRF merge candidate |
| Extraction pipeline | Đồng bộ, trong process, không retry/DLQ | Chuyển sang Kafka async + consumer riêng |
| Retention | 1 env var `MEMORY_RETENTION_DAYS` toàn cục | Bảng `MemoryRetentionConfig` per tenant+domain |
| Workflow Memory (doc §3.7) | Chưa có | Mới: bảng `WorkflowMemory` + Qdrant collection `workflow_memory` (mục 6) |
| Schema Memory (doc §3.7) | Chưa có | Mới: bảng `SchemaMemory`, Admin-managed, PG exact-match (không Qdrant) (mục 6) |

## 3. File đã thêm / sửa

**Database**
- `alembic/versions/b2c3d4e5f6a7_stm_ltm_memory_architecture.py` — thêm cột `domain`/`fact_key` + unique constraint vào `AgentMemories`; tạo bảng `EpisodicMemory`, `FutureMemory`, `MemoryRetentionConfig`; áp RLS cho 3 bảng mới; seed retention config mặc định.
- `basemodel/services_databaseconnector/postgres_orm/workflow_orm.py` — ORM cho các bảng/cột trên.
- `basemodel/services_databaseconnector/postgres_orm/auth_release_orm.py` — relationship `TenantsORM` → 3 bảng mới.

**workflow-runtime (request path — không đổi latency người dùng)**
- `memory_middleware.py` — `retrieve_memories()` re-rank theo retrieval score; thêm `retrieve_episodic_past()`, `retrieve_pending_reminders()`; `apply_memory_policy()` giờ chỉ emit Kafka event (không còn extract/write đồng bộ).
- `kafka_producer.py` — producer Kafka (fire-and-forget, fail-open nếu Kafka down).
- `app.py`, `runtime_services/conversation_runner.py` — wiring (init/close producer; nạp reminders/episodic past khi mở conversation mới; emit event sau khi lưu message).
- `jobs/memory_cleanup.py` — cleanup theo `MemoryRetentionConfig` per domain + cap eviction cho `EpisodicMemory`.

> Lưu ý: bản đầu có tạo `memory_vocab.py` + `prompts/memory_extract.txt` trong
> `workflow-runtime`, nhưng sau khi quyết định chuyển toàn bộ LLM extraction
> sang `memory-consumer`, 2 file đó hết được gọi tới (dead code) — đã xóa.
> Controlled vocabulary + prompt giờ chỉ tồn tại 1 nơi: trong `memory-consumer`.

**Service mới — `services/memory-consumer/`**
- `main.py` — Kafka consumer xử lý `memory.extraction.requested`: đọc lại turn từ Postgres, gọi LLM extraction, thực thi 4-decision lifecycle (ADD/UPDATE/DELETE/NO_CHANGE) cho Semantic LTM, ghi Episodic Past/Future, cap eviction. Retry 3 lần (backoff 1/2/4s) rồi đẩy sang `memory.extraction.dlq` và tiếp tục — không còn block partition vô hạn như `audit-consumer` hiện tại. Controlled vocabulary `fact_key` (`DOMAIN_FACT_KEYS`) định nghĩa trực tiếp trong file này.
- `extract_prompt.txt`, `requirements.txt`, `Dockerfile`.

**Infra**
- `infra/kafka/kafka-broker-jaas.config.example` — thêm user `workflow-runtime`, `memory-consumer`.
- `infra/kafka/kafka-setup.sh` — tạo topic `memory.extraction.requested` (3 partitions, 7d) + `memory.extraction.dlq` (1 partition, 30d); ACL cho 2 service trên.
- `.env.example` — `WORKFLOW_RUNTIME_KAFKA_PASSWORD`, `MEMORY_CONSUMER_KAFKA_PASSWORD`.
- `docker/docker-compose.app.yml` — thêm Kafka env cho `workflow-runtime`; thêm service `memory-consumer`.

## 4. Deploy

```bash
# 1. Set password mới trong .env (copy từ .env.example) và trong
#    infra/kafka/kafka-broker-jaas.config (copy từ .example, điền password thật).
#    WORKFLOW_RUNTIME_KAFKA_PASSWORD, MEMORY_CONSUMER_KAFKA_PASSWORD

# 2. Chạy migration
alembic upgrade head

# 3. Build + start service mới + restart workflow-runtime
docker compose -f docker/docker-compose.app.yml build workflow-runtime memory-consumer
docker compose -f docker/docker-compose.app.yml up -d workflow-runtime memory-consumer

# 4. Apply Kafka ACL cho topic/user mới (idempotent, an toàn re-run)
bash infra/kafka/kafka-setup.sh
```

Rollback: `alembic downgrade -1` (xóa 3 bảng mới + cột `domain`/`fact_key`);
`docker compose stop memory-consumer` để dừng consumer mới (workflow-runtime
vẫn chạy được vì producer fail-open khi Kafka/consumer không có).



## 5. Test case — kết quả (đã chạy trên Docker thật)

Ban đầu chỉ test static/mock (không có Docker daemon chạy). Sau khi Docker
sẵn sàng, đã chạy lại toàn bộ trên hạ tầng thật: Postgres 16 (container
throwaway riêng, không đụng vào `dataagent-postgres` hiện có — xem ghi chú
dưới), Qdrant + Kafka thật (`dataagent-qdrant`, `dataagent-kafka`, đã bật ACL
qua `kafka-setup.sh`), `memory-consumer` build + run bằng image thật. Vì
chưa có LLM provider key nào cấu hình (`OPENAI_API_KEY`/`GEMINI_API_KEY`
trống, không có service `litellm` đang chạy), phần gọi LLM được stub bằng 1
HTTP server nhỏ trả JSON cố định — đủ để test toàn bộ logic ghi/đọc, không
test được chất lượng extraction thật. Tất cả container/test data đã được
dọn sạch sau khi xong; `dataagent-postgres/qdrant/kafka` đã `stop` lại đúng
trạng thái trước khi bắt đầu (không `recreate`, không mất volume).

⚠️ Phát hiện ngoài lề: volume `docker_postgres_data` hiện tại (project
compose name "docker") có `alembic_version` ở revision `a1b2c3d4e5f6` nhưng
**thiếu bảng `AgentMemories`** — không liên quan đến migration mới, là bug
tồn tại từ trước (env.py có ghi "two source-of-truth schemas is a real bug
worth untangling separately"). Vì vậy mọi test migration ở đây dùng Postgres
container riêng, sạch từ đầu, không phải volume đó.

| # | Test case | Phương pháp | Kết quả |
|---|---|---|---|
| 1 | `alembic upgrade head` sạch trên Postgres thật; `downgrade -1` rồi `upgrade head` lại | Docker (Postgres 16 container riêng, sạch) | ✅ Cả 3 revision chạy sạch; downgrade xóa đúng 3 bảng mới + 2 cột; re-upgrade lại sạch |
| 2 | Schema thật sau migration: cột `domain`/`fact_key`, `uq_agentmemories_fact ... NULLS NOT DISTINCT`, RLS forced trên `EpisodicMemory`/`FutureMemory`/`MemoryRetentionConfig` | Docker (`\d`, `pg_constraint`, `pg_class`) | ✅ Đúng thiết kế |
| 3 | Fix #6 (`NULLS NOT DISTINCT`): 2 lần `INSERT ... ON CONFLICT DO UPDATE` cùng key với `user_ref=NULL` | Docker (Postgres thật) | ✅ Đúng 1 row, content/importance là của lần ghi sau — không tạo duplicate |
| 4 | Fix #4 (`IS NOT DISTINCT FROM`): 5 `EpisodicMemory` rows `user_ref=NULL`, cap=3 | Docker (Postgres thật, đúng câu SQL trong `jobs/memory_cleanup.py`) | ✅ Archive đúng 2 row, còn lại 3 active |
| 5 | Fix #1 (`embedding_ref`/point-id): upsert Qdrant point bằng `_qdrant_point_id()` thật, search lại, so khớp `payload["pg_id"]` | Docker (Qdrant thật) | ✅ Khớp tuyệt đối — đúng giá trị `memory_middleware.py` sẽ dùng để join Postgres |
| 6 | Build Docker image `memory-consumer` (Dockerfile + requirements.txt) | Docker build | ✅ Build sạch |
| 7 | Consumer kết nối Postgres + Kafka (SASL/PLAIN, đúng ACL) + Qdrant (tạo collection) | Docker (container thật, network `data-agent-network`) | ✅ "PostgreSQL connected", join consumer group thành công, collection tạo OK |
| 8 | Semantic ADD: turn có preference rõ → ghi `AgentMemories` (domain/fact_key/importance/embedding_ref) + 1 point Qdrant | Docker, full path qua Kafka → consumer → Postgres + Qdrant (LLM stub) | ✅ Đúng |
| 9 | Episodic Future ADD trong cùng turn: `FutureMemory` với `execution_time`/`recurrence` | Docker (cùng test #8, 2 insight trong 1 turn) | ✅ Đúng |
| 10 | NO_CHANGE: gửi lại đúng nội dung (cùng embedding) | Docker | ✅ Không gọi Qdrant upsert, chỉ `updated_at` đổi, content/importance giữ nguyên |
| 11 | DELETE (semantic): soft-delete `AgentMemories` | Docker | ✅ `deleted_at` được set (lần thử đầu bị miss do Kafka consumer-group rebalance giữa lúc đổi container liên tục trong session test — lần thử lại sau khi group ổn định thì đúng ngay) |
| 12 | Episodic Past ADD | Docker | ✅ Ghi được. Lần đầu phát hiện bug: do rebalance/redelivery (at-least-once) trong lúc test, insight bị xử lý 2 lần → 2 row giống nhau trong `EpisodicMemory` (xem mục 5, issue #7). Đã sửa bằng `_episodic_id()` (id xác định trước từ `message_id` + vị trí insight) + `ON CONFLICT (id) DO NOTHING`. |
| 13 | Retry + DLQ: LLM không reachable (không có provider thật) | Docker (lỗi thật, không phải giả lập) | ✅ 4 attempt (backoff 0/1/2/4s), publish đúng vào `memory.extraction.dlq`, consumer KHÔNG kẹt — tiếp tục nhận message kế tiếp bình thường |
| 14 | `kafka_producer._json_default`, `produce_event` fail-open, `_rrf`, `_retrieval_score`, `_qdrant_point_id`, `_normalize_fact_key`, brace-safe prompt, SAVEPOINT isolation | Mock/venv (không cần Docker) | ✅ Tất cả pass — xem chi tiết logic trong commit history |
| 15 | `py_compile` toàn bộ file đã sửa | Static | ✅ Sạch |
| 16 | Fix #7 (idempotency cho `EpisodicMemory`): publish **cùng 1 event y nguyên 2 lần** (giả lập Kafka redeliver) | Docker (Postgres + Kafka + Qdrant thật, sau khi sửa) | ✅ Đúng 1 row duy nhất — `ON CONFLICT (id) DO NOTHING` chặn duplicate thành công |

**Còn lại — chưa test** (không bắt buộc cho lần này, để lại làm sau):
- [ ] `workflow-runtime` thật chạy `/conversations/run` → tự emit event (đã test bằng cách tự publish event đúng format thay cho việc build/run workflow-runtime — chưa build image đó).
- [ ] UPDATE với ngưỡng similarity 0.85–0.95 thật — cần embedding model thật, không tái tạo được bằng stub.
- [ ] Cap eviction cho `AgentMemories` (200/user/domain) qua đúng hàm `enforce_agent_memories_cap` với data thật (chỉ test cap cho `EpisodicMemory`).
- [ ] `jobs/memory_cleanup.py` chạy như 1 script thật (chỉ test câu SQL tương đương thủ công).
- [ ] Tắt Kafka hoàn toàn khi `workflow-runtime` đang chạy thật (producer fail-open) — chỉ test bằng mock Python.

## 6. Workflow Memory + Schema Memory (doc §3.7) — 2026-06-18

Triển khai phần đã bỏ qua ở mục 1, sau khi node `text_to_sql` xuất hiện
trong `nodes/sandbox/tool_executor/handlers/data.py` — điều kiện chặn ban
đầu ("chưa có agent generate SQL") không còn đúng.

### 6.1 Thiết kế

- **Workflow Memory** — Episodic Memory đặc thù cho data execution
  context: lưu task_type, task_description, param_snapshot, outcome,
  duration_ms mỗi lần một data-tool (`text_to_sql`, `text_to_pandas`) chạy
  xong, bất kể thành công hay lỗi. Ghi đồng bộ trong `finally` của
  `conversation_runner.py` (giống `apply_memory_policy`, không chặn token
  stream). Đọc trước khi `planner_node` chạy — top-3 theo
  `relevance × recency × inverse(duration)` (doc §IV), lọc
  `task_type` + `outcome=success`, inject vào system prompt dưới tag
  `<past_successful_runs>`.
- **Schema Memory** — catalog bảng/cột mà agent được phép query
  (`column_metadata`, `business_description`, `access_roles`). Agent chỉ
  đọc — viết qua Admin CRUD router (`routers/schema_memory.py`). Đọc bằng
  exact-match `(tenant_id, data_source, table_name)`, không dùng vector
  search (đúng doc §IV — "Không dùng semantic search"). `_text_to_sql_handler`
  ưu tiên Schema Memory, fallback về `schema_hint` tĩnh nếu tenant chưa có
  record nào.

**Đơn giản hóa có chủ đích so với doc:**
- Doc yêu cầu task_type filter trước khi biết agent sẽ làm task gì — nhưng
  việc đó chỉ planner mới quyết định được, *sau* thời điểm cần retrieve.
  Giải pháp: suy ra `task_type` từ tool nào đang được agent cấu hình
  (`text_to_sql` → `sql_query`, `text_to_pandas` → `report_gen`) — xem
  `_DATA_TOOL_TASK_TYPE` trong `conversation_runner.py`. Chưa có taxonomy
  task_type đầy đủ.
- Doc mô tả bước "planning step" chọn danh sách table cần query trước khi
  gọi Schema Memory. Chưa có bước đó — hiện tại nạp toàn bộ table đã đăng
  ký cho `(tenant_id, data_source)`. Sẽ cần thu hẹp lại nếu một tenant đăng
  ký nhiều chục bảng (context window).
- Permission "BI Agent write / Gov-Finance Agent read-only" (doc §3.7) chưa
  triển khai — chưa có khái niệm agent role/type trong codebase để gán
  quyền theo đó. Hiện tại mọi agent có `text_to_sql`/`text_to_pandas` đều
  ghi Workflow Memory.

### 6.2 File đã thêm / sửa

**Database**
- `alembic/versions/c3d4e5f6a7b8_workflow_schema_memory.py` — bảng
  `WorkflowMemory`, `SchemaMemory` + RLS `tenant_isolation` (cùng pattern
  `a1b2c3d4e5f6`/`b2c3d4e5f6a7`).
- `basemodel/services_databaseconnector/postgres_orm/workflow_orm.py` —
  `WorkflowMemoryORM`, `SchemaMemoryORM` + relationship trên `AgentsORM`.
- `basemodel/services_databaseconnector/postgres_orm/auth_release_orm.py` —
  relationship `TenantsORM` → 2 bảng mới.

**workflow-runtime**
- `workflow_memory.py` (mới) — `record_workflow_outcome()`,
  `retrieve_workflow_memory()` (Qdrant collection riêng `workflow_memory`,
  client riêng — cùng pattern duplicate-client như `memory_middleware.py`
  vs `memory-consumer`), `retrieve_schema_memory()`, `format_schema_context()`.
- `routers/schema_memory.py` (mới) — Admin CRUD: `GET/PUT/DELETE
  /api/schema-memory`.
- `state.py` — thêm `workflow_memory_context`.
- `nodes/model_harness/planner.py` — inject `<past_successful_runs>`.
- `nodes/sandbox/tool_executor/handlers/data.py` — `_text_to_sql_handler`
  gọi `retrieve_schema_memory()` trước khi sinh SQL.
- `config_loader.py` — thêm `data_source` vào `tool_config.text_to_sql`.
- `runtime_services/conversation_runner.py` — retrieve trước khi build
  state; ghi `record_workflow_outcome()` trong `finally` cho mỗi data-tool
  call (`_DATA_TOOL_TASK_TYPE`).
- `app.py` — wiring `init_workflow_qdrant`/`close_workflow_qdrant` +
  đăng ký `routers.schema_memory`.

**Tests** (mới — `tests/conftest.py` thêm fixture `qdrant_container`)
- `tests/integration/test_workflow_schema_memory.py` — migration shape/RLS/
  downgrade, `workflow_memory.py` round-trip (Postgres + Qdrant
  testcontainers thật), Admin router CRUD.
- `tests/unit/test_workflow_memory_scoring.py` — công thức scoring,
  `format_schema_context`.
- `tests/integration/test_alembic_migrations.py` — sửa assertion head
  revision cũ (`a1b2c3d4e5f6`, đã stale từ trước khi `b2c3d4e5f6a7` lên
  head) thành `c3d4e5f6a7b8`.

### 6.3 Test — chỉ chạy test case KHÔNG cần LLM thật

Theo yêu cầu: bỏ qua mọi test case cần gọi LLM provider thật. Toàn bộ
24 test dưới đây chạy qua `pytest` thật (Postgres 16 + Qdrant qua
`testcontainers`, không build image `workflow-runtime`/`memory-consumer`),
`_embed()` được monkeypatch thành vector cố định — không gọi LiteLLM/OpenAI
nào.

| # | Test case | Kết quả |
|---|---|---|
| 1 | `alembic upgrade head` tạo `WorkflowMemory` + `SchemaMemory` | ✅ |
| 2 | Unique constraint `(tenant_id, data_source, table_name)` chặn duplicate | ✅ |
| 3 | RLS policy `tenant_isolation` tồn tại trên cả 2 bảng mới | ✅ |
| 4 | `downgrade -1` xóa sạch 2 bảng, `upgrade head` lại sạch | ✅ |
| 5 | `record_workflow_outcome` → `retrieve_workflow_memory` round-trip (PG+Qdrant thật) | ✅ |
| 6 | Retrieve loại `outcome=failed` | ✅ |
| 7 | Retrieve loại task_type khác | ✅ |
| 8 | Retrieve cách ly theo tenant (tenant khác không leak) | ✅ |
| 9 | `retrieve_schema_memory` exact-match + `format_schema_context` render đúng DDL | ✅ |
| 10 | Router `PUT/GET /api/schema-memory` | ✅ |
| 11 | Router `PUT` idempotent trên cùng `(data_source, table_name)` | ✅ — phát hiện + sửa bug thật (xem dưới) |
| 12 | Router `DELETE` | ✅ |
| 13 | Router `DELETE` id không tồn tại → 404 | ✅ |
| 14–19 | Unit: `_workflow_score` (recency/duration/relevance), `format_schema_context` | ✅ (6 case) |
| 20–23 | Regression: `test_alembic_migrations.py` 3/4 case cũ vẫn pass sau khi thêm migration mới | ✅ (xem ghi chú) |

**Bug thật phát hiện qua test #11 — đã sửa:** `list_schema_memory` đọc
thuộc tính ORM (`r.id`, `r.column_metadata`, ...) trong list-comprehension
*sau khi* thoát khỏi `async with client.get_client(...)` — session đã
rollback (expire toàn bộ instance) và đóng tại điểm đó, nên truy cập
thuộc tính ném `DetachedInstanceError`. Sửa bằng cách build response dict
bên trong block `async with`, trước khi session đóng.

**Quy ước môi trường test phát hiện thêm (không phải bug code):**
- `qdrant-client` cài mới nhất (1.18.0) đổi API — `AsyncQdrantClient` không
  còn `.search()`. Phải ghim đúng range trong
  `services/workflow-runtime/requirements.txt` (`>=1.13.0,<1.14.0`) khi
  tạo venv test, khớp với production.
- `AsyncQdrantClient` mặc định chạy compatibility-check ở background
  thread, warning khi client/server lệch minor version — đã thêm
  `check_compatibility=False` vào `init_workflow_qdrant()` để tránh
  warning rò ra ngoài (ảnh hưởng cả test harness có
  `filterwarnings=["error"]` và log nhiễu ở production).

**Regression với test cũ (`test_alembic_migrations.py`):**
- `test_alembic_upgrade_head_on_fresh_db` — assertion hardcode head
  revision đã **stale từ trước** (trỏ `a1b2c3d4e5f6` trong khi
  `b2c3d4e5f6a7` đã là head sau đợt triển khai ở mục 1–5, không ai cập
  nhật lại). Đã sửa trỏ sang head mới `c3d4e5f6a7b8`.
- `test_rls_policy_exists_after_upgrade` — **fail từ trước khi có thay đổi
  này**, không liên quan: bảng `api_keys` không được tạo bởi alembic chain
  thuần (chỉ tồn tại qua SQL init script riêng, đúng như ghi chú "two
  source-of-truth schemas is a real bug worth untangling separately" trong
  `alembic/env.py`). Đã xác minh bằng cách chạy lại `upgrade head` trên DB
  sạch và query `information_schema.tables` — `api_keys` không tồn tại.
  Không sửa trong lần này vì ngoài phạm vi (bug schema-duality có sẵn).

**Chưa test — do thiếu LLM provider thật** (không phải chưa cố tình bỏ
qua, mà bị chặn cứng vì cần gọi model thật):
- [ ] `_text_to_sql_handler` sinh SQL thật từ `column_metadata`/`business_description`
      của Schema Memory (cần LLM thật để đánh giá SQL sinh ra có đúng/dùng
      đúng context không — phần ghép context thì đã test qua `format_schema_context`).
- [ ] `planner_node` thực sự đọc `<past_successful_runs>` và đổi quyết định
      tool-call dựa trên đó (cần LLM thật để planner phản ứng với prompt).
- [ ] `_embed()` gọi LiteLLM `/v1/embeddings` thật (đã monkeypatch toàn bộ
      test — chỉ test logic sau khi có vector, không test chính lệnh gọi
      HTTP/endpoint/response-parsing thật).
- [ ] End-to-end `/conversations/run` thật: planner → tool_executor →
      `text_to_sql` → responder → ghi `WorkflowMemory` trong `finally` —
      cần `litellm` + toàn bộ stack chạy thật, không chỉ Postgres/Qdrant.
- [ ] Độ chính xác ranking khi có embedding thật (test hiện tại dùng vector
      giả dựa trên `hash()` — đủ để test thứ tự tương đối nhưng không phản
      ánh chất lượng semantic similarity thật).

### 6.4 Deploy

```bash
alembic upgrade head
docker compose -f docker/docker-compose.app.yml build workflow-runtime
docker compose -f docker/docker-compose.app.yml up -d workflow-runtime
```

Không cần thay đổi gì ở `memory-consumer`/Kafka — Workflow/Schema Memory
không qua Kafka (ghi đồng bộ trong request `finally`, không cần LLM
extraction bất đồng bộ).

Rollback: `alembic downgrade -1` (xóa `WorkflowMemory`, `SchemaMemory`).
