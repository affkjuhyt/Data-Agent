# Schema Crawler Worker — hướng dẫn chạy & test

```
cron tick (CRAWL_INTERVAL_SECONDS, mặc định 24h)                Kafka DDL event (ddl.changes)
        │                                                              │
        ▼                                                              ▼
get_active_snowflake_warehouses()                          debounce (DDL_DEBOUNCE_SECONDS)
        │  KBWarehouse JOIN KBWarehouseConfig WHERE is_active=true      │
        ▼                                                              ▼
crawl_warehouse(conn_params, table_names)  ◄────────────────────────────
        │
        ├── discover_and_describe()  → INFORMATION_SCHEMA.TABLES + COLUMNS
        └── mỗi table: schema_hash(columns) so với SchemaCatalog.schema_hash
                UNCHANGED → chỉ update last_crawled_at
                CHANGED   → describe_table() [LiteLLM chat] → embed() [LiteLLM embeddings]
                            → upsert SchemaCatalog (Postgres) + qdrant.upsert (point_id xác định)
```

---

## 1. Setup

| Dịch vụ | Mặc định |
|---|---|
| PostgreSQL | `postgres:5432` (DB `dataagent`, bảng `KBWarehouse`/`KBWarehouseConfig`/`SchemaCatalog`) |
| Qdrant | `http://qdrant:6333` |
| Kafka | `kafka:9092` (tùy chọn — cron vẫn chạy nếu không có) |
| LiteLLM | `http://litellm:4000` (LLM describe + embedding) |

```bash
cd ~/Data-Agent
python3 -m venv .venv-sandbox
.venv-sandbox/bin/pip install -r services/schema-crawler-worker/requirements.txt

# Migration (bảng KBWarehouse/KBWarehouseConfig/SchemaCatalog)
DATABASE_URL=postgresql+asyncpg://admin@localhost:5432/dataagent \
  .venv-sandbox/bin/python3 -m alembic -c alembic/alembic.ini upgrade head
```

**Biến môi trường:**
```bash
DB_HOST=localhost DB_PORT=5432 DB_NAME=dataagent DB_USER=admin DB_PASSWORD=dev123
QDRANT_URL=http://localhost:6333
EMBEDDING_DIM=1024                     # BGE-M3
KAFKA_BOOTSTRAP_SERVERS=localhost:9092  # tùy chọn
DDL_DEBOUNCE_SECONDS=60
SNOWFLAKE_CRAWLER_PASSWORD=your-password   # hoặc per-warehouse: SNOWFLAKE_PASSWORD__<warehouse_id>
CRAWL_INTERVAL_SECONDS=86400            # 24h mặc định
LITELLM_BASE_URL=http://localhost:4000
LITELLM_API_KEY=sk-dev
```

```bash
cd services/schema-crawler-worker
~/Data-Agent/.venv-sandbox/bin/python3 main.py
```

Lúc start: connect Postgres (retry 20 lần, delay 5s) → connect Qdrant, tạo collection `schema_catalog` nếu chưa có → connect Kafka (best-effort — cron-only nếu fail) → vào loop chính (poll DDL events + cron).

---

## 2. Test end-to-end với LLM thật (Ollama — không cần Snowflake/cloud API)

Pipeline đầy đủ: Snowflake mock + LLM describe thật + embed thật → Postgres + Qdrant.

```bash
# 1. Ollama (một lần)
mkdir -p ~/.local/bin
curl -fsSL https://github.com/ollama/ollama/releases/download/v0.30.11/ollama-linux-amd64.tar.zst -o /tmp/ollama.tar.zst
tar --zstd -xf /tmp/ollama.tar.zst -C ~/.local/
export PATH="$PATH:~/.local/bin"

OLLAMA_HOST=0.0.0.0:11434 ollama serve &
ollama pull qwen2.5:7b          # describe (~4.7GB)
ollama pull mxbai-embed-large   # embedding 1024-dim (~670MB)

# 2. LiteLLM trỏ vào Ollama (config có sẵn: infra/litellm/config.test-ollama.yaml)
docker run -d --name test-litellm --network host \
  -v $(pwd)/infra/litellm/config.test-ollama.yaml:/app/config.yaml \
  -e LITELLM_MASTER_KEY=sk-1234 \
  ghcr.io/berriai/litellm:main-stable --config /app/config.yaml --port 4000
until curl -s http://localhost:4000/v1/models -H "Authorization: Bearer sk-1234" | grep -q "planner"; do sleep 2; done

# 3. Postgres + Qdrant + migration
docker run -d --name test-postgres -e POSTGRES_USER=admin -e POSTGRES_DB=dataagent \
  -e POSTGRES_HOST_AUTH_METHOD=trust -p 5432:5432 postgres:16-alpine
docker run -d --name test-qdrant -p 6333:6333 qdrant/qdrant:v1.13.6
until docker exec test-postgres pg_isready -U admin -d dataagent 2>/dev/null; do sleep 1; done
DATABASE_URL=postgresql+asyncpg://admin@localhost:5432/dataagent \
  .venv-sandbox/bin/python3 -m alembic -c alembic/alembic.ini upgrade head

# 4. Chạy crawler với LLM thật
LITELLM_BASE_URL=http://localhost:4000 LITELLM_API_KEY=sk-1234 DB_HOST=localhost \
  .venv-sandbox/bin/python3 scripts/test_crawler_with_llm.py
```

**Kết quả mong đợi:**
```
INFO  HTTP Request: POST http://localhost:4000/v1/chat/completions "200 OK"
INFO  HTTP Request: POST http://localhost:4000/v1/embeddings "200 OK"
INFO  HTTP Request: PUT .../schema_catalog/points "200 OK"
INFO  process_table | crawled tenant=... table=ANALYTICS.fact_stock_price (changed)

=== fact_stock_price ===
Keywords: stock price, ticker symbol, trading date, closing price, volume
Join hints: dim_company.ticker | dim_calendar.trade_date
```
Thời gian mỗi bảng: ~8s (LLM) + ~0.2s (embed) trên CPU. Nếu output ra tiếng Trung dù cột tiếng Anh, chỉnh `services/schema-crawler-worker/describe_prompt.txt` để ép ngôn ngữ đầu ra.

Nếu project docker-compose đã chạy sẵn (`dataagent-postgres` chiếm port 5432), dùng container đó thay vì `test-postgres`, chỉ cần đảm bảo role `app_user` tồn tại và stamp đúng revision alembic trước khi upgrade (xem §4 lỗi thường gặp).

Dọn dẹp: `docker rm -f test-postgres test-qdrant test-litellm; pkill -f "ollama serve"`

---

## 3. Test suite

```bash
# Unit — không cần Docker
pytest tests/unit/test_schema_hash.py tests/unit/test_schema_crawler_llm_embed.py \
       tests/unit/test_ddl_debounce.py tests/unit/test_kafka_reconnect.py \
       tests/unit/test_process_table_diff_detection.py -v

# Integration — cần Docker (testcontainers: Postgres + Qdrant, Snowflake mocked)
pytest tests/integration/test_schema_crawler_worker.py -v
```

---

## 4. Lỗi thường gặp

| Triệu chứng | Nguyên nhân | Xử lý |
|---|---|---|
| `connection_params | no password configured for warehouse=...` | Thiếu env password | Set `SNOWFLAKE_CRAWLER_PASSWORD` hoặc `SNOWFLAKE_PASSWORD__<warehouse_id>` |
| `PostgreSQL not ready` | DB chưa sẵn sàng | Worker tự retry 20 lần/5s. Kiểm tra `DB_HOST=localhost` khi chạy ngoài Docker |
| `Kafka unavailable ... running in cron-only mode` | Không có Kafka | Bình thường — worker vẫn chạy theo cron |
| `DuplicateTableError: relation "KBEntityLookup" already exists` | DB có bảng nhưng thiếu `alembic_version` | `alembic stamp ff9ae8dcd7f7` rồi upgrade tiếp |
| `UndefinedTableError: relation "AgentMemories" does not exist` | DB chỉ có KB tables, thiếu workflow tables | `alembic stamp b2c3d4e5f6a7` |
| `UndefinedObjectError: role "app_user" does not exist` | Role RLS chưa tạo | `psql -c "CREATE ROLE app_user LOGIN NOBYPASSRLS;"` |
| `Bind for 0.0.0.0:5432 failed: port is already allocated` | Container postgres khác đang chiếm port | Dùng container đang chạy thay vì tạo mới |
| `[Errno -3] Temporary failure in name resolution` | Dùng hostname Docker nội bộ (`qdrant`/`postgres`) ngoài compose network | Set `QDRANT_URL=http://localhost:6333`, `DB_HOST=localhost` |

---

## 5. File tham chiếu

```
services/schema-crawler-worker/
├── main.py                # Main loop: cron + Kafka DDL events
├── snowflake_metadata.py  # INFORMATION_SCHEMA discovery
├── llm_describe.py        # LiteLLM chat completion → table description
├── embed.py                # LiteLLM embeddings → vector
├── schema_hash.py          # MD5 fingerprint cho diff detection
└── describe_prompt.txt     # Prompt template cho LLM
```
