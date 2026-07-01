# Hướng dẫn chạy luồng Sandbox Executor và Schema Crawler Worker

## Tổng quan kiến trúc

```
workflow-runtime
    │
    ├─► sandbox-executor  (port 8500)
    │       ├── /execute/python   → AST validation → Docker container → stdout
    │       └── /execute/sql      → SQL guard → Snowflake read-only → rows
    │
    └─► schema-crawler-worker
            ├── Cron (CRAWL_INTERVAL_SECONDS, mặc định 24h)
            │       └── PostgreSQL KBWarehouse → Snowflake metadata → LLM describe → embed → Qdrant
            └── Kafka DDL event (debounce SECONDS) → single-table crawl
```

---

## 1. Luồng Sandbox Executor

### 1.1. Yêu cầu môi trường

| Yêu cầu | Giá trị |
|---|---|
| Python | 3.11+ |
| Docker daemon | Phải chạy và có quyền truy cập socket |
| Docker image | `sandbox-runner:latest` (phải build trước) |
| Port | 8500 |

### 1.2. Build sandbox-runner image

> **Lưu ý Docker permission:** Nếu gặp `permission denied while trying to connect to the Docker daemon socket`, chạy `newgrp docker` để kế thừa docker group trong session hiện tại (không cần logout).

> Chạy tất cả lệnh từ thư mục gốc project: `~/Data-Agent`

```bash
cd ~/Data-Agent
docker build -t sandbox-runner:latest services/sandbox-executor/sandbox_runner/
```

Image này chứa: `pandas`, `numpy`, `plotly`, `matplotlib` — đúng với `ALLOWED_MODULES` trong `security_visitor.py`.

### 1.3. Cài đặt dependencies

```bash
cd ~/Data-Agent
python3 -m venv .venv-sandbox
.venv-sandbox/bin/pip install -r services/sandbox-executor/requirements.txt
```

### 1.4. Chạy dịch vụ

```bash
cd ~/Data-Agent/services/sandbox-executor
~/Data-Agent/.venv-sandbox/bin/uvicorn main:app --host 0.0.0.0 --port 8500
```

**Biến môi trường tùy chọn:**

```bash
SANDBOX_TIMEOUT_S=10       # Timeout mặc định mỗi container (giây)
SANDBOX_MEMORY_MB=256      # Giới hạn RAM mỗi container
SANDBOX_CPUS=0.5           # Giới hạn CPU
SANDBOX_DOCKER_HOST=...    # Docker-in-Docker sidecar (nếu dùng rootless)
```

### 1.5. Kiểm tra các endpoint

#### Health check
```bash
curl http://localhost:8500/health
# Kết quả mong đợi: {"status": "ok"}
```

#### Thực thi Python hợp lệ
```bash
curl -X POST http://localhost:8500/execute/python \
  -H "Content-Type: application/json" \
  -d '{"code": "import pandas as pd\nprint(pd.DataFrame({\"a\":[1,2]}).sum().to_dict())", "timeout_s": 10}'
# Kết quả mong đợi: {"success": true, "stdout": "...", "exit_code": 0, ...}
```

#### Kiểm tra bảo mật — import bị chặn
```bash
curl -X POST http://localhost:8500/execute/python \
  -H "Content-Type: application/json" \
  -d '{"code": "import os\nos.system(\"echo hi\")"}'
# Kết quả mong đợi: {"success": false, "error": "security validation failed: Import blocked: os"}
```

#### Kiểm tra bảo mật — obfuscation bị chặn (doc §1.6)
```bash
curl -X POST http://localhost:8500/execute/python \
  -H "Content-Type: application/json" \
  -d '{"code": "getattr(__builtins__, '\''ev'\''+'\'al'\'')('\''1+1'\'')"}'
# Kết quả mong đợi: {"success": false, ...}
```

#### Kiểm tra timeout
```bash
curl -X POST http://localhost:8500/execute/python \
  -H "Content-Type: application/json" \
  -d '{"code": "while True:\n    pass", "timeout_s": 3}'
# Kết quả mong đợi: {"success": false, "timed_out": true}
```

#### Kiểm tra giới hạn bộ nhớ (OOM = exit_code 137)
```bash
curl -X POST http://localhost:8500/execute/python \
  -H "Content-Type: application/json" \
  -d '{"code": "data = bytearray(b'\''\\x01'\'' * (400 * 1024 * 1024))\nprint(len(data))", "timeout_s": 15, "memory_mb": 256}'
# Kết quả mong đợi: {"success": false, "exit_code": 137}
```

#### Kiểm tra network disabled
```bash
curl -X POST http://localhost:8500/execute/python \
  -H "Content-Type: application/json" \
  -d '{"code": "import pandas as pd\npd.read_csv(\"http://example.com/data.csv\")", "timeout_s": 15}'
# Kết quả mong đợi: {"success": false} — kết nối bị chặn ở tầng Docker
```

#### Thực thi SQL (cần Snowflake credentials)
```bash
curl -X POST http://localhost:8500/execute/sql \
  -H "Content-Type: application/json" \
  -d '{
    "sql": "SELECT * FROM my_table LIMIT 10",
    "connection": {
      "account": "your-account",
      "user": "your-user",
      "password": "your-password",
      "warehouse": "COMPUTE_WH",
      "database": "MY_DB",
      "schema": "PUBLIC"
    }
  }'
```

#### SQL guard — lệnh không phải SELECT bị từ chối
```bash
curl -X POST http://localhost:8500/execute/sql \
  -H "Content-Type: application/json" \
  -d '{
    "sql": "DROP TABLE t",
    "connection": {"account":"a","user":"u","password":"p"}
  }'
# Kết quả mong đợi: {"success": false, "error": "SQL rejected: ..."}
```

---

## 2. Luồng Schema Crawler Worker

### 2.1. Yêu cầu môi trường

| Dịch vụ | Mặc định |
|---|---|
| PostgreSQL | `postgres:5432` (DB: `dataagent`) |
| Qdrant | `http://qdrant:6333` |
| Kafka | `kafka:9092` (tùy chọn — cron vẫn chạy khi không có Kafka) |
| LiteLLM | `http://litellm:4000` (LLM describe + embedding) |

### 2.2. Yêu cầu database

Database PostgreSQL phải có các bảng:
- `KBWarehouse` — registry các warehouse theo tenant
- `KBWarehouseConfig` — cấu hình kết nối Snowflake
- `SchemaCatalog` — lưu kết quả crawl (columns, hash, embedding point)

Chạy migration:
```bash
cd ~/Data-Agent
DATABASE_URL=postgresql+asyncpg://admin@localhost:5432/dataagent \
  .venv-sandbox/bin/python3 -m alembic -c alembic/alembic.ini upgrade head
```

### 2.3. Cài đặt dependencies

```bash
cd ~/Data-Agent
.venv-sandbox/bin/pip install -r services/schema-crawler-worker/requirements.txt
```

### 2.4. Biến môi trường

```bash
# PostgreSQL
DB_HOST=postgres
DB_PORT=5432
DB_NAME=dataagent
DB_USER=admin
DB_PASSWORD=dev123

# Qdrant
QDRANT_URL=http://qdrant:6333
EMBEDDING_DIM=1024                # BGE-M3 dimension

# Kafka (tùy chọn)
KAFKA_BOOTSTRAP_SERVERS=kafka:9092
KAFKA_DDL_TOPIC=ddl.changes
KAFKA_GROUP_ID=schema-crawler-worker-group
DDL_DEBOUNCE_SECONDS=60           # Delay sau khi nhận DDL event

# Snowflake credentials (per-warehouse hoặc shared)
SNOWFLAKE_CRAWLER_PASSWORD=your-password
# Hoặc per-warehouse: SNOWFLAKE_PASSWORD__<warehouse_id_with_underscores>=...

# Crawl schedule
CRAWL_INTERVAL_SECONDS=86400      # 24h (mặc định)
POLL_INTERVAL_SECONDS=5
KAFKA_RECONNECT_INTERVAL_SECONDS=300

# LiteLLM
LITELLM_BASE_URL=http://litellm:4000
LITELLM_API_KEY=sk-dev
```

### 2.5. Chạy worker

```bash
cd ~/Data-Agent/services/schema-crawler-worker
~/Data-Agent/.venv-sandbox/bin/python3 main.py
```

Lúc khởi động worker sẽ:
1. Kết nối PostgreSQL (retry tối đa 20 lần, delay 5s)
2. Kết nối Qdrant, tạo collection `schema_catalog` nếu chưa có
3. Kết nối Kafka consumer (best-effort — nếu thất bại vẫn chạy cron-only)
4. Vào vòng lặp chính: poll Kafka DDL events + cron crawl

### 2.6. Luồng crawl đầy đủ

```
cron tick (hoặc DDL event)
    │
    ▼
get_active_snowflake_warehouses()
    │  → đọc KBWarehouse JOIN KBWarehouseConfig WHERE is_active=true
    │
    ▼
crawl_warehouse(conn_params, table_names)
    │
    ├── discover_and_describe()   → INFORMATION_SCHEMA.TABLES + COLUMNS
    │
    └── cho từng table:
            ├── schema_hash(columns)         → MD5 fingerprint
            ├── so sánh với SchemaCatalog.schema_hash
            │     UNCHANGED → chỉ update last_crawled_at, skip
            │     CHANGED   →
            │         ├── describe_table()   → LiteLLM /v1/chat/completions
            │         ├── render_text_description()
            │         ├── embed()            → LiteLLM /v1/embeddings
            │         ├── upsert SchemaCatalog (PostgreSQL)
            │         └── qdrant.upsert (deterministic point_id)
            └── commit per-table (giảm orphan nếu crash giữa chừng)
```

### 2.7. Luồng DDL event (Kafka)

```
Kafka topic: ddl.changes
    │
    └── event: {tenant_id, warehouse_id, table_name}
            │
            ├── debounce: pending_ddl[key] = {due: now + DDL_DEBOUNCE_SECONDS, event}
            │   (event mới cho cùng table sẽ overwrite due — IS the debounce)
            │
            └── khi due elapsed:
                    handle_ddl_event() → crawl_warehouse([table_name])
```

### 2.8. Chạy crawler với LLM thật (Ollama — không cần Snowflake, không cần cloud API)

Pipeline đầy đủ: Snowflake mock + LLM describe thật + embed thật → PostgreSQL + Qdrant.

#### Bước 1 — Cài Ollama (một lần)

```bash
# Download binary vào user space (không cần sudo)
mkdir -p ~/.local/bin
curl -fsSL https://github.com/ollama/ollama/releases/download/v0.30.11/ollama-linux-amd64.tar.zst \
  -o /tmp/ollama.tar.zst
tar --zstd -xf /tmp/ollama.tar.zst -C ~/.local/
export PATH="$PATH:~/.local/bin"
```

#### Bước 2 — Pull models và start Ollama

```bash
# Start Ollama server
OLLAMA_HOST=0.0.0.0:11434 ollama serve &

# Pull 2 model cần thiết (~5GB tổng):
ollama pull qwen2.5:7b          # LLM cho describe (~4.7GB)
ollama pull mxbai-embed-large   # Embedding 1024-dim (~670MB)
```

#### Bước 3 — Start LiteLLM trỏ vào Ollama

Config test đã có sẵn tại `infra/litellm/config.test-ollama.yaml`:

```bash
docker run -d --name test-litellm \
  --network host \
  -v $(pwd)/infra/litellm/config.test-ollama.yaml:/app/config.yaml \
  -e LITELLM_MASTER_KEY=sk-1234 \
  ghcr.io/berriai/litellm:main-stable \
  --config /app/config.yaml --port 4000

# Chờ sẵn sàng
until curl -s http://localhost:4000/v1/models -H "Authorization: Bearer sk-1234" | grep -q "planner"; do sleep 2; done
echo "LiteLLM ready"
```

#### Bước 4 — Start PostgreSQL + Qdrant + migration

Script test (`scripts/test_crawler_with_llm.py`) kết nối hardcode vào `localhost:5432`. Có hai tình huống:

**Tình huống A — Project Docker Compose đang chạy (port 5432 bận)**

`dataagent-postgres` đã chạy → dùng luôn, chỉ cần start Qdrant:

```bash
# Qdrant
docker rm -f test-qdrant 2>/dev/null || true
docker run -d --name test-qdrant -p 6333:6333 qdrant/qdrant:v1.13.6

# Đảm bảo role app_user tồn tại (cần cho RLS migrations)
docker exec dataagent-postgres psql -U admin -d dataagent -c \
  "DO \$\$ BEGIN
     IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='app_user') THEN
       CREATE ROLE app_user LOGIN NOBYPASSRLS;
     END IF;
   END \$\$;"

cd ~/Data-Agent
DB_URL="postgresql+asyncpg://admin@localhost:5432/dataagent"

# Nếu DB có bảng nhưng chưa có alembic_version, stamp base revision trước
docker exec dataagent-postgres psql -U admin -d dataagent -tAc \
  "SELECT table_name FROM information_schema.tables WHERE table_name='alembic_version'" \
  | grep -q alembic_version || \
  DATABASE_URL=$DB_URL .venv-sandbox/bin/python3 -m alembic -c alembic/alembic.ini stamp ff9ae8dcd7f7

# Apply từng migration riêng để tránh rollback cả batch khi một migration fail
for rev in a1b2c3d4e5f6 b2c3d4e5f6a7 c3d4e5f6a7b8 d4e5f6a7b8c9 e5f6a7b8c9d0 f6a7b8c9d0e1 a7b8c9d0e1f2 b8c9d0e1f2a3; do
  DATABASE_URL=$DB_URL \
    .venv-sandbox/bin/python3 -m alembic -c alembic/alembic.ini upgrade "$rev" 2>/dev/null || true
done

# Verify SchemaCatalog tồn tại
docker exec dataagent-postgres psql -U admin -d dataagent -tAc \
  "SELECT to_regclass('public.\"SchemaCatalog\"');" | grep -q SchemaCatalog && \
  echo "OK: SchemaCatalog ready" || echo "ERROR: SchemaCatalog missing"
```

**Tình huống B — Chạy trên máy fresh (port 5432 trống)**

```bash
# Xóa container cũ nếu còn tồn tại (stopped nhưng chưa bị rm)
docker rm -f test-postgres test-qdrant 2>/dev/null || true

docker run -d --name test-postgres \
  -e POSTGRES_USER=admin -e POSTGRES_DB=dataagent \
  -e POSTGRES_HOST_AUTH_METHOD=trust -p 5432:5432 postgres:16-alpine

docker run -d --name test-qdrant -p 6333:6333 qdrant/qdrant:v1.13.6

# Bắt buộc phải đợi postgres sẵn sàng trước khi chạy alembic
until docker exec test-postgres pg_isready -U admin -d dataagent 2>/dev/null; do sleep 1; done

DATABASE_URL=postgresql+asyncpg://admin@localhost:5432/dataagent \
  .venv-sandbox/bin/python3 -m alembic -c alembic/alembic.ini upgrade head
```

#### Bước 5 — Chạy crawler với LLM thật

```bash
cd ~/Data-Agent
LITELLM_BASE_URL=http://localhost:4000 \
LITELLM_API_KEY=sk-1234 \
DB_HOST=localhost \
  .venv-sandbox/bin/python3 scripts/test_crawler_with_llm.py
```

#### Kết quả mong đợi

```
INFO  HTTP Request: POST http://localhost:4000/v1/chat/completions "200 OK"
INFO  HTTP Request: POST http://localhost:4000/v1/embeddings "200 OK"
INFO  HTTP Request: PUT .../schema_catalog/points "200 OK"
INFO  process_table | crawled tenant=... table=ANALYTICS.fact_stock_price (changed)
INFO  crawl_warehouse | warehouse=...: 1/1 table(s) changed

=== fact_stock_price ===
Name: fact_stock_price
Keywords: stock price, ticker symbol, trading date, closing price, volume
Categories:
  ticker — Mã cổ phiếu
  trade_date — Ngày giao dịch
  close — Giá đóng cửa
  volume — Khối lượng giao dịch
Use Cases:
  - Tính giá đóng cửa trung bình trong khoảng thời gian
  - Phân hiện tích volume bất thường
Join hints: dim_company.ticker | dim_calendar.trade_date
```

> **Lưu ý:** Model `qwen2.5:7b` có thể sinh mô tả tiếng Trung nếu tên cột là tiếng Anh kỹ thuật. Chỉnh prompt tại `services/schema-crawler-worker/describe_prompt.txt` để chỉ định ngôn ngữ đầu ra nếu cần.

Thời gian xử lý mỗi bảng: ~8s (LLM) + ~0.2s (embed) trên CPU.

#### Dọn dẹp

```bash
docker rm -f test-postgres test-qdrant test-litellm 2>/dev/null || true
pkill -f "ollama serve" 2>/dev/null || true
```

---

## 3. Chạy test suite

### Unit tests (không cần Docker, không cần external services)

```bash
# Toàn bộ unit tests
pytest -m unit -v

# Sandbox — AST security validator
pytest tests/unit/test_security_visitor.py -v

# Sandbox — SQL guard (SELECT-only, comments, DDL rejection)
pytest tests/unit/test_sql_guard.py -v

# Sandbox — output truncation
pytest tests/unit/test_output_truncation.py -v

# Sandbox — Snowflake connection pooling
pytest tests/unit/test_snowflake_readonly.py -v

# Crawler — schema_hash (diff detection fingerprint)
pytest tests/unit/test_schema_hash.py -v

# Crawler — LLM describe + embed HTTP shape
pytest tests/unit/test_schema_crawler_llm_embed.py -v

# Crawler — DDL debounce algorithm
pytest tests/unit/test_ddl_debounce.py -v

# Crawler — Kafka reconnect logic
pytest tests/unit/test_kafka_reconnect.py -v

# Crawler — process_table diff detection (skip unchanged, upsert changed)
pytest tests/unit/test_process_table_diff_detection.py -v
```

### Integration tests (cần Docker)

```bash
# Crawler — PostgreSQL + Qdrant testcontainers, Snowflake mocked
pytest tests/integration/test_schema_crawler_worker.py -v

# Sandbox — real Docker container execution (cần sandbox-runner:latest)
pytest tests/integration/test_sandbox_executor_api.py -v
```

### Toàn bộ test suite
```bash
pytest -v
```

---

## 4. Kết quả chạy thực tế (kiểm tra lần cuối: 2026-06-30)

### Unit tests — 84/84 PASSED
```
tests/unit/test_security_visitor.py          26 passed
tests/unit/test_sql_guard.py                 21 passed
tests/unit/test_output_truncation.py          4 passed
tests/unit/test_snowflake_readonly.py         6 passed
tests/unit/test_schema_hash.py                5 passed
tests/unit/test_schema_crawler_llm_embed.py   5 passed
tests/unit/test_ddl_debounce.py               9 passed
tests/unit/test_kafka_reconnect.py            4 passed
tests/unit/test_process_table_diff_detection.py 4 passed
```

### Integration tests — yêu cầu Docker với quyền socket

**`test_sandbox_executor_api.py`** — 8 tests SKIPPED  
Lý do: cần `docker.sock` accessible và image `sandbox-runner:latest` đã build.  
Cách fix: thêm user vào group `docker` (`sudo usermod -aG docker $USER`) hoặc chạy với `DOCKER_HOST=...`.

**`test_schema_crawler_worker.py`** — 5 tests ERROR  
Lý do: testcontainers cần `docker.sock` để spin up PostgreSQL + Qdrant containers.  
Cách fix: tương tự, cần Docker socket permission.

> **Lưu ý**: Khi chạy `pytest -m unit` cho toàn bộ project có 3 lỗi ImportError (module `hvac`, `sqlalchemy`, `langgraph`) từ các service khác (auth-api, workflow-runtime) — **không liên quan** sandbox/crawler. Cài đúng theo `requirements.txt` của từng service khi cần chạy tests đó.

---

## 5. Các lỗi thường gặp

| Triệu chứng | Nguyên nhân | Cách xử lý |
|---|---|---|
| `SandboxRunError: sandbox image 'sandbox-runner:latest' not found` | Chưa build image | `cd services/sandbox-executor/sandbox_runner && docker build -t sandbox-runner:latest .` |
| `security validation failed: Import blocked: os` | Code dùng module bị cấm | Chỉ dùng: `pandas`, `numpy`, `plotly`, `matplotlib`, `json`, `math`, `datetime`, `statistics` |
| `SQL rejected: only SELECT ...` | SQL không phải SELECT/WITH | Chỉ SELECT hoặc CTE (WITH ... SELECT) |
| `SQL rejected: comments are not allowed` | SQL có `--` hoặc `/* */` | Xóa tất cả comment trong SQL |
| `connection_params | no password configured for warehouse=...` | Thiếu env var password | Set `SNOWFLAKE_CRAWLER_PASSWORD` hoặc `SNOWFLAKE_PASSWORD__<warehouse_id>` |
| `PostgreSQL not ready` | DB chưa sẵn sàng | Worker tự retry 20 lần, delay 5s. Kiểm tra `DB_HOST=localhost` khi chạy ngoài Docker |
| `Kafka unavailable ... running in cron-only mode` | Kafka không có | Bình thường — worker vẫn chạy theo cron |
| `exit_code: 137` trên Python | OOM kill | Giảm data size hoặc tăng `memory_mb` |
| `DuplicateTableError: relation "KBEntityLookup" already exists` | DB có bảng nhưng thiếu `alembic_version` | Stamp base revision: `alembic stamp ff9ae8dcd7f7` rồi migrate từng bước (xem Bước 4 Tình huống A) |
| `UndefinedTableError: relation "AgentMemories" does not exist` | DB chỉ có KB tables, thiếu workflow tables | Stamp qua migration đó: `alembic stamp b2c3d4e5f6a7` |
| `UndefinedObjectError: role "app_user" does not exist` | Role RLS chưa được tạo | `docker exec <postgres> psql -U admin -d dataagent -c "CREATE ROLE app_user LOGIN NOBYPASSRLS;"` |
| `DuplicateColumnError: column "tenant_id" already exists` | Column đã được thêm ngoài Alembic | Stamp qua migration đó: `alembic stamp f6a7b8c9d0e1` |
| `Bind for 0.0.0.0:5432 failed: port is already allocated` | `dataagent-postgres` đang chiếm port 5432 | Dùng Tình huống A ở Bước 4 — không tạo test-postgres |
| `[Errno -3] Temporary failure in name resolution` | Worker dùng hostname `qdrant`/`postgres` (Docker internal) | Set `QDRANT_URL=http://localhost:6333` và `DB_HOST=localhost` |

---

## 6. Cấu trúc file tham chiếu

```
services/
├── sandbox-executor/
│   ├── main.py               # FastAPI app: /health, /execute/python, /execute/sql
│   ├── security_visitor.py   # AST validator — ALLOWED_MODULES, BLOCKED_CALLS
│   ├── sql_guard.py          # SELECT-only guard, reject comments/DDL/DML
│   ├── snowflake_readonly.py # Connection pool + wrapped query execution
│   ├── docker_runner.py      # Ephemeral container execution
│   ├── output.py             # Truncate stdout (DEFAULT_MAX_OUTPUT_CHARS=10000)
│   ├── requirements.txt
│   ├── Dockerfile
│   └── sandbox_runner/
│       └── Dockerfile        # sandbox-runner:latest image (pandas/numpy/etc)
│
└── schema-crawler-worker/
    ├── main.py               # Main loop: cron + Kafka DDL events
    ├── snowflake_metadata.py # INFORMATION_SCHEMA discovery
    ├── llm_describe.py       # LiteLLM chat completion → table description
    ├── embed.py              # LiteLLM embeddings → vector
    ├── schema_hash.py        # MD5 fingerprint for diff detection
    ├── describe_prompt.txt   # Prompt template cho LLM
    └── requirements.txt

tests/
├── unit/
│   ├── test_security_visitor.py         # sandbox AST validator
│   ├── test_sql_guard.py                # SQL SELECT-only guard
│   ├── test_output_truncation.py        # stdout truncation
│   ├── test_snowflake_readonly.py       # connection pooling
│   ├── test_schema_hash.py              # hash fingerprint
│   ├── test_schema_crawler_llm_embed.py # LLM/embed HTTP shape
│   ├── test_ddl_debounce.py             # DDL debounce algorithm
│   ├── test_kafka_reconnect.py          # Kafka retry/recover
│   └── test_process_table_diff_detection.py  # skip/upsert logic
└── integration/
    ├── test_sandbox_executor_api.py     # end-to-end Docker sandbox
    └── test_schema_crawler_worker.py    # PostgreSQL + Qdrant testcontainers
```
