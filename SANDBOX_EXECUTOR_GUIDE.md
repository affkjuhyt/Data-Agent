# Sandbox Executor — hướng dẫn chạy & test

`sandbox-executor` (port 8500) là service duy nhất giữ Docker socket. Hai endpoint chính:

```
POST /execute/python  → AST whitelist → exec vào container "warm" trong pool → stdout
POST /execute/sql     → SQL guard (SELECT-only) → Snowflake (prod) hoặc Postgres (dev/test) → rows
```

Cả hai đều **enqueue rồi trả `202 {job_id}` ngay lập tức** (không chạy inline) — client phải poll `GET .../{job_id}` hoặc subscribe `GET .../{job_id}/stream` (SSE) tới khi có kết quả cuối.

---

## 1. Setup

| Yêu cầu | Giá trị |
|---|---|
| Python | 3.11+ |
| Docker daemon | phải chạy, có quyền truy cập socket |
| Docker image | `sandbox-runner:latest` (build trước) |

```bash
cd ~/Data-Agent

# Nếu gặp "permission denied ... docker daemon socket": chạy `newgrp docker`

# 1. Build image mà container pool dùng để exec code (pandas/numpy/plotly/matplotlib)
docker build -t sandbox-runner:latest services/sandbox-executor/sandbox_runner/

# 2. Cài dependencies
python3 -m venv .venv-sandbox
.venv-sandbox/bin/pip install -r services/sandbox-executor/requirements.txt

# 3. Chạy service
cd services/sandbox-executor
SANDBOX_SQL_BACKEND=postgres \
  ~/Data-Agent/.venv-sandbox/bin/uvicorn main:app --host 0.0.0.0 --port 8500
```

`SANDBOX_SQL_BACKEND=postgres` chuyển `/execute/sql` sang chạy thật với PostgreSQL (asyncpg) thay vì Snowflake — dùng để test end-to-end mà không cần tài khoản Snowflake (mặc định vẫn là `snowflake` khi không set — đúng hành vi production). Xem §3.

### 1.1. Chạy bằng Docker (thay vì venv)

Cách này build đúng image production dùng (`services/sandbox-executor/Dockerfile`) thay vì chạy trực tiếp bằng venv — dùng khi muốn test sát với môi trường thật, hoặc không muốn cài Python deps ra máy host.

```bash
cd ~/Data-Agent
docker network create sandbox-test-net   # network riêng để container này nói chuyện được với Postgres test (§3)

docker build -t sandbox-executor:local services/sandbox-executor

docker run -d --name sandbox-executor-c \
  --network sandbox-test-net \
  -p 8500:8500 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -e SANDBOX_SQL_BACKEND=postgres \
  sandbox-executor:local

docker logs -f sandbox-executor-c   # đợi "Application startup complete"
```

Mount thẳng `/var/run/docker.sock` của host (đơn giản, đủ dùng để test local). Sidecar Docker-in-Docker cô lập (`sandbox-dockerd`) tồn tại trong `infra/docker-compose.yml` cho production nhưng **tắt theo mặc định trên nhiều host** — cần `kernel.apparmor_restrict_unprivileged_userns=0` (Ubuntu hardening) mới chạy được rootless dind, xem §4.1 và `docs/sandbox_report.md` mục 11.

> **Cập nhật (Vòng 4):** `sandbox-executor` giờ khai báo thẳng trong `infra/docker-compose.yml` (file chính, dùng chung với Kong/Keycloak/frontend thật) — không còn cần `docker-compose.app.yml` nữa (file đó vẫn còn nhưng dùng bộ service trùng tên + kỳ vọng ghép với `docker-compose.security.yml` cho HA, không tương thích với `docker-compose.yml`). Xem §4 để chạy đúng qua `docker compose` thay vì `docker run` tay từng service.

**Biến môi trường đáng chú ý khác** (đều có default hợp lý, chỉ set khi cần đổi):
`SANDBOX_TIMEOUT_S`, `SANDBOX_MEMORY_MB`, `SANDBOX_CPUS`, `SANDBOX_POOL_SIZE` (số container warm — mặc định `3` trong `docker-compose.yml`, comment trong file có số đo latency `runsc` thật nếu cần bump), `SANDBOX_POOL_MAX_USES`/`_MAX_AGE_S` (TTL nghỉ hưu container), `SANDBOX_BREAKER_ENFORCE` (mặc định code `false` = log-only; `docker-compose.yml` set `true` — breaker thật sự từ chối `acquire()` khi `OPEN`, không chỉ log), `SANDBOX_RUNTIME` (mặc định để trống = runtime Docker mặc định `runc`; set `runsc` nếu đã cài gVisor trên đúng Docker engine mà container này đang mount socket vào — xem `docs/sandbox_report.md` mục 10.1), `MINIO_ENDPOINT`/`MINIO_ACCESS_KEY`/`MINIO_SECRET_KEY` (audit-copy code trước khi chạy, best-effort — service vẫn chạy nếu MinIO down). Danh sách đầy đủ + giải thích: `services/sandbox-executor/main.py`, `pool.py`, `docker_runner.py` docstrings.

> Khi chạy bằng venv/`docker run` tay theo hướng dẫn ở §1/§1.1 (không qua `docker compose`), các biến trên **giữ nguyên default an toàn của code** (`enforce=false`, runtime mặc định) trừ khi bạn tự set.

---

## 2. Test qua API

Xem execution trên UI: **Build → Workflows → tab Executions → sub-tab Queue** — panel "Sandbox Queue" hiện pool health + bảng job (tự poll 2.5s), click 1 job để xem `stdout`/`stderr` đầy đủ. Hiện ngay dù job submit bằng `curl` thẳng vào `sandbox-executor` (không qua FE) hay qua agent thật, vì đọc chung 1 `JobQueue`. Chi tiết đăng nhập/dựng UI: §4.

```bash
submit_and_poll() {
  local path="$1" body="$2" job_id status resp
  job_id=$(curl -s -X POST "http://localhost:8500${path}" -H "Content-Type: application/json" -d "$body" | jq -r .job_id)
  while true; do
    resp=$(curl -s "http://localhost:8500${path}/${job_id}")
    status=$(echo "$resp" | jq -r .status)
    [[ "$status" == "queued" || "$status" == "running" ]] || { echo "$resp"; break; }
    sleep 0.3
  done
}
```

#### Health + Python hợp lệ
```bash
curl http://localhost:8500/health
# {"status": "ok"}

submit_and_poll /execute/python '{"code": "import pandas as pd\nprint(pd.DataFrame({\"a\":[1,2]}).sum().to_dict())", "timeout_s": 10}'
# {"status":"done", "success": true, "stdout": "...", "exit_code": 0, ...}
```

#### Bảo mật — import bị chặn / timeout
```bash
submit_and_poll /execute/python '{"code": "import os\nos.system(\"echo hi\")"}'
# {"status":"done", "success": false, "error": "security validation failed: Import blocked: os"}

submit_and_poll /execute/python '{"code": "while True:\n    pass", "timeout_s": 3}'
# {"status":"done", "success": false, "timed_out": true}
```

#### Theo dõi qua SSE — stdout thật, từng dòng, trong lúc code đang chạy
```bash
job_id=$(curl -s -X POST http://localhost:8500/execute/python \
  -H "Content-Type: application/json" \
  -d '{"code": "for i in range(3):\n    print(i)\n    x = sum(range(15_000_000))", "timeout_s": 15}' | jq -r .job_id)
curl -N "http://localhost:8500/execute/python/${job_id}/stream"
# event: status (queued/running) → 3x event: output (mỗi dòng cách nhau ~0.3-0.4s thật) → event: done
```

#### Chạy nhiều job song song (pool dùng nhiều container cùng lúc)

Gọi `submit_and_poll` 3 lần liên tiếp không có `&` sẽ tự nhiên chạy tuần tự (hành vi bash, không liên quan pool) — cần chạy nền (`&` + `wait`):

```bash
CODE='import datetime
t0=datetime.datetime.now()
x=sum(range(20_000_000))
dt=(datetime.datetime.now()-t0).total_seconds()
print("done in %.2fs, x=%d" % (dt, x))'

submit_and_poll_timed() {
  local label="$1" job_id status resp t_start t_end body
  t_start=$(date +%s.%N)
  body=$(jq -n --arg code "$CODE" '{code: $code, timeout_s: 15}')
  job_id=$(curl -s -X POST "http://localhost:8500/execute/python" -H "Content-Type: application/json" -d "$body" | jq -r .job_id)
  echo "[$label] submitted job_id=$job_id at $(date +%T.%3N)"
  while true; do
    resp=$(curl -s "http://localhost:8500/execute/python/${job_id}")
    status=$(echo "$resp" | jq -r .status)
    [[ "$status" == "queued" || "$status" == "running" ]] || break
    sleep 0.3
  done
  t_end=$(date +%s.%N)
  echo "[$label] FINISHED at $(date +%T.%3N) — wall time: $(echo "$t_end - $t_start" | bc)s — stdout: $(echo "$resp" | jq -r .stdout)"
}

echo "=== Bắt đầu 3 job song song lúc $(date +%T.%3N) ==="
submit_and_poll_timed "job-1" &
submit_and_poll_timed "job-2" &
submit_and_poll_timed "job-3" &
wait
echo "=== Cả 3 xong lúc $(date +%T.%3N) ==="
```

Job "submitted" cách nhau vài chục ms, thời gian tính toán trong stdout ~0.4-0.5s dù wall-time tổng 4-6s (acquire/cleanup/verify overhead) — bằng chứng cả 3 container chạy đồng thời. Cần `jq`+`bc`. Nếu job phải đợi thay vì chạy song song, kiểm tra breaker:
```bash
curl -s http://localhost:8500/status | jq '.pool.circuit_breaker'
# {"state": "open", ...} <- đang chặn request mới, đợi tự đóng lại (~30s) hoặc dùng code nhẹ hơn
```

---

## 3. Test SQL với Postgres thật (không cần Snowflake)

`SANDBOX_SQL_BACKEND=postgres` (đặt lúc start service, §1) là stand-in cho Snowflake — cùng cơ chế guard SELECT-only, khác driver (`postgres_readonly.py` dùng `asyncpg`). Dựng một Postgres thật + role read-only rồi gọi `/execute/sql` như production sẽ gọi.

```bash
# 1. Postgres thật + seed data (bảng sample_orders, role sandbox_reader SELECT-only)
docker run -d --name test-postgres \
  --network sandbox-test-net \
  -e POSTGRES_USER=admin -e POSTGRES_DB=dataagent \
  -e POSTGRES_HOST_AUTH_METHOD=trust -p 5432:5432 postgres:16-alpine
until docker exec test-postgres pg_isready -U admin -d dataagent 2>/dev/null; do sleep 1; done

docker exec test-postgres psql -U admin -d dataagent -c "CREATE DATABASE sandbox_dev;"
docker exec test-postgres psql -U admin -d dataagent -c "
  DO \$\$ BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='sandbox_reader') THEN
      CREATE ROLE sandbox_reader WITH LOGIN PASSWORD 'sandbox_reader_dev_pw';
    END IF;
  END \$\$;"
docker exec test-postgres psql -U admin -d dataagent -c "GRANT CONNECT ON DATABASE sandbox_dev TO sandbox_reader;"

# -i bắt buộc (không có nó docker exec bỏ qua stdin, heredoc chạy im lặng không làm gì)
docker exec -i test-postgres psql -U admin -d sandbox_dev <<'EOF'
CREATE TABLE IF NOT EXISTS sample_orders (
  id SERIAL PRIMARY KEY, customer_name TEXT NOT NULL, amount NUMERIC(10,2) NOT NULL
);
INSERT INTO sample_orders (customer_name, amount)
SELECT * FROM (VALUES ('Acme Corp', 120.50), ('Globex', 75.00), ('Initech', 300.25)) AS v(customer_name, amount)
WHERE NOT EXISTS (SELECT 1 FROM sample_orders);
GRANT USAGE ON SCHEMA public TO sandbox_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO sandbox_reader;
EOF

# 2. sandbox-executor đã chạy với SANDBOX_SQL_BACKEND=postgres (§1) → gọi /execute/sql thật
# host="localhost" nếu sandbox-executor chạy bằng venv (§1); host="test-postgres" nếu chạy
# bằng container trên cùng `sandbox-test-net` (§1.1) — Docker DNS resolve theo tên container.
submit_and_poll /execute/sql '{
  "sql": "SELECT customer_name, amount FROM sample_orders ORDER BY id",
  "connection": {"host":"localhost","port":5432,"user":"sandbox_reader","password":"sandbox_reader_dev_pw","dbname":"sandbox_dev"}
}'
# {"status":"done", "success": true, "rows": [{"customer_name":"Acme Corp","amount":120.50}, ...], "row_count": 3}

# 3. SQL guard — DDL/DML bị từ chối trước khi chạm DB
submit_and_poll /execute/sql '{
  "sql": "DROP TABLE sample_orders",
  "connection": {"host":"localhost","port":5432,"user":"sandbox_reader","password":"sandbox_reader_dev_pw","dbname":"sandbox_dev"}
}'
# {"status":"done", "success": false, "error": "SQL rejected: ..."}
```

Trong docker-compose (`infra/docker-compose.yml`), `sandbox_dev`/`sandbox_reader` đã được auto-tạo bởi `infra/sql/create-extra-dbs.sql` trên volume postgres mới — set `SANDBOX_SQL_BACKEND=postgres` trong `.env` là dùng được ngay, không cần bước 1 ở trên.

Dọn dẹp: `docker rm -f test-postgres`.

---

## 4. Xem stream trên UI (tab Queue) — qua stack `docker compose` thật (FE + Kong + Keycloak)

`sandbox-executor` internal-only — UI không gọi thẳng, phải qua relay ở `workflow-runtime`, và cả `workflow-runtime` cũng internal-only phía sau Kong:

```
Browser → Kong :8000 /api/sandbox/*  (JWT verify, audience=workflow-runtime)
        → workflow-runtime GET /api/sandbox/status               → sandbox-executor GET /status
        → workflow-runtime GET /api/sandbox/job/{kind}/{id}      → sandbox-executor GET /execute/{kind}/{id}
        → workflow-runtime GET /api/sandbox/stream/{kind}/{id}   → sandbox-executor GET /execute/{kind}/{id}/stream
```

Component: `src/components/agent-builder/workflow/SandboxQueuePanel.tsx` — poll `/api/sandbox/status` mỗi 2.5s, click 1 job để xem chi tiết (SSE thời gian thực nếu đang chạy). Đường dẫn trong app: **Build → Workflows → tab Executions → sub-tab Queue**.

Cách dưới đây đã tự chạy thật và xác nhận thành công (kể cả xuyên qua Kong + Keycloak + đăng nhập MFA thật trên browser, xem `docs/sandbox_report.md` mục 11) — dùng đúng `infra/docker-compose.yml` (không phải `docker-compose.app.yml`, file đó dùng chung service name/HA layer không khớp với file chính, xem mục 5.5 báo cáo).

```bash
cd infra

# 1. Dựng cả stack cần thiết trong 1 lệnh (sandbox-executor giờ đã khai báo
#    thẳng trong docker-compose.yml, không còn cần docker-compose.app.yml)
docker compose -f docker-compose.yml -f docker-compose.local.yml \
  up -d --build postgres mongo redis qdrant keycloak kong-migration kong \
  minio litellm sandbox-executor workflow-runtime frontend

# sandbox-executor mount thẳng /var/run/docker.sock của host theo mặc định
# (sidecar sandbox-dockerd cô lập DinD bị TẮT theo mặc định trên host này —
# kernel.apparmor_restrict_unprivileged_userns=1 chặn rootless dind, xem
# comment trong docker-compose.yml service sandbox-dockerd + mục 11 báo cáo).

# 2. Đăng ký route Kong — QUAN TRỌNG: phải override KEYCLOAK_NETWORK_URL vì
#    docker-compose.yml (file chính) chỉ có service `keycloak` (không có
#    `aeroflow-keycloak-lb` — đó là tên dùng khi ghép thêm docker-compose.security.yml).
KONG_ADMIN_URL=http://localhost:8900 \
KEYCLOAK_URL=http://localhost:8080 \
KEYCLOAK_PUBLIC_URL=http://localhost:8080 \
KEYCLOAK_NETWORK_URL=http://keycloak:8080 \
  bash kong/kong-setup.sh

# 3. Verify qua curl trước khi mở browser — lấy JWT thật rồi gọi qua đúng Kong
TOKEN=$(curl -s -X POST http://localhost:8080/realms/aeroflow/protocol/openid-connect/token \
  -d "client_id=aeroflow-frontend" -d "grant_type=password" \
  -d "username=platform-admin" -d "password=Admin@123456" | jq -r .access_token)
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/sandbox/status | jq .
# {"pool":{"size":3,"idle":3,...},"jobs":{"python":[],"sql":[]}}

# 4. Frontend đã có sẵn trong stack ở bước 1 — http://localhost:5173
#    .env dùng cho container: VITE_KEYCLOAK_URL=http://localhost:8080,
#    VITE_API_BASE_URL=http://localhost:8000 (qua Kong, không gọi thẳng workflow-runtime)
# Đăng nhập bằng platform-admin / Admin@123456
# Vào Build → Workflows → tab Executions → sub-tab Queue
```

> Cả app bắt buộc login qua Keycloak thật (`onLoad: 'login-required'`, không có bypass cho dev) — không có cách nào khác để vào app kể cả để xem 1 tab UI.

### 4.1. 3 vấn đề thực tế đã gặp khi tự chạy lần đầu (không phải giả định — xem mục 11 báo cáo để biết chi tiết)

1. **Realm `aeroflow` đã tồn tại sẵn từ volume postgres cũ** (`Realm 'aeroflow' already exists. Import skipped`) — nếu bạn dùng lại volume `postgres_data` đã có từ trước, user/mật khẩu trong `realm-export.json` có thể không khớp với DB thật (đã có người chỉnh tay qua Admin Console trước đó). Muốn chắc chắn dùng đúng data trong file: xoá volume `infra_postgres_data` trước khi `up` lần đầu (**mất toàn bộ data Postgres khác, chỉ làm khi chắc chắn muốn reset**), hoặc tự sửa lại credential qua Keycloak Admin API/Console.
2. **`Browser MFA` là flow đăng nhập mặc định của realm** — user mới (hoặc user vừa bị xoá OTP credential) sẽ bị bắt cấu hình TOTP ngay khi đăng nhập qua UI (không xảy ra với direct grant/curl — chỉ browser flow). Đây là chính sách bảo mật có chủ đích (tên flow), không phải bug — cần quét QR/nhập mã TOTP thật lúc đăng nhập lần đầu qua UI.
3. **`aeroflow-frontend` client thiếu audience mapper** cho `workflow-runtime`/`flow-builder`/... — token lấy được có `aud` không khớp với những gì `kong-setup.sh` cấu hình cho plugin `aeroflow-jwks`, mọi route qua Kong (không chỉ `/api/sandbox`) sẽ 401 `"Audience mismatch"`. **Đã vá trong `infra/keycloak/realm-export.json`** (5 mapper `oidc-audience-mapper` mới) — chỉ áp dụng cho lần import đầu tiên của 1 volume Postgres mới; nếu tái dùng volume cũ như (1), cần tự thêm mapper qua Admin API hoặc xoá volume để import lại từ đầu.

---

## 5. Quan sát container pool khi chạy

```bash
docker ps --filter ancestor=sandbox-runner:latest   # N container "warm", luôn Up — không tạo/xoá theo request
docker stats $(docker ps -q --filter ancestor=sandbox-runner:latest)
docker top <container_id>                            # exec nào đang chạy trong container
```

---

## 6. Test suite

```bash
# Unit — không cần Docker
pytest tests/unit/test_security_visitor.py tests/unit/test_sql_guard.py \
       tests/unit/test_snowflake_readonly.py tests/unit/test_pool_circuit_breaker.py \
       tests/unit/test_pool_egress.py tests/unit/test_docker_runner_client.py \
       tests/unit/test_job_queue.py tests/unit/test_postgres_readonly.py \
       tests/unit/test_main_json_encoding.py -v

# Integration — cần Docker
pytest tests/integration/test_sandbox_executor_api.py -v      # exec thật vào sandbox-runner:latest
pytest tests/integration/test_sandbox_pool.py -v              # cleanup/TTL/label-reconciliation/egress
pytest tests/integration/test_sandbox_handler_wiring.py -v    # SSE incremental thật qua httpx (không phải TestClient)
pytest tests/integration/test_postgres_readonly_integration.py -v   # SQL backend thật (testcontainers), cột NUMERIC
pytest tests/integration/test_sandbox_sql_postgres_backend.py -v    # như trên + cột TIMESTAMPTZ (regression cho bug datetime, xem docs/sandbox_report.md mục 10.2)
```

Chạy cả 3 file "nặng Docker" (`test_sandbox_pool.py`/`test_sandbox_executor_api.py`/`test_sandbox_handler_wiring.py`) **cùng lúc** với 2 file dùng fixture `postgres_container` (`test_postgres_readonly_integration.py`/`test_sandbox_sql_postgres_backend.py`) trong 1 lệnh `pytest` từng gây flaky: Docker daemon bận tạo/xoá container pool khiến `testcontainers` health-check container Postgres bị timeout/kill (`exit 137`). Không phải bug — chạy 2 nhóm này ở 2 lệnh `pytest` riêng để tránh (xem `docs/sandbox_report.md` mục 10.5).

---

## 7. Lỗi thường gặp

| Triệu chứng | Nguyên nhân | Xử lý |
|---|---|---|
| `PoolSpawnError: could not create sandbox pool container ...` | Chưa build `sandbox-runner:latest` | `docker build -t sandbox-runner:latest services/sandbox-executor/sandbox_runner/` |
| `... per-request memory_mb/cpus overrides aren't supported ...` | Request khác `SANDBOX_MEMORY_MB`/`SANDBOX_CPUS` hiện tại | Container Pool cố định resource lúc tạo — sửa env rồi restart |
| `no idle sandbox container available within ...s` | Pool đang bận hết | Tăng `SANDBOX_POOL_SIZE` hoặc `SANDBOX_POOL_ACQUIRE_TIMEOUT_S` |
| `503 {"error": "... job queue is full ..."}` | `SANDBOX_QUEUE_MAXSIZE` đầy | Tăng maxsize hoặc pool/workers |
| `GET .../{job_id}` trả `404` | job_id sai hoặc quá `SANDBOX_JOB_TTL_S` (mặc định 600s) | Poll sớm hơn — job store chỉ ở RAM |
| `security validation failed: Import blocked: os` | Module bị cấm | Chỉ dùng: `pandas`, `numpy`, `plotly`, `matplotlib`, `json`, `math`, `datetime`, `statistics` |
| `SQL rejected: only SELECT ...` / `comments are not allowed` | SQL không phải SELECT/CTE, hoặc có `--`/`/* */` | Chỉ SELECT/WITH, không comment |
| `unknown SANDBOX_SQL_BACKEND` | Set sai giá trị | Chỉ `snowflake` hoặc `postgres` |

---

## 8. File tham chiếu

```
services/sandbox-executor/
├── main.py               # FastAPI: /health, /status, /execute/{python,sql} (submit/poll/stream)
├── security_visitor.py   # AST validator (ALLOWED_MODULES, BLOCKED_CALLS)
├── sql_guard.py          # SELECT-only guard
├── snowflake_readonly.py # Backend SQL production
├── postgres_readonly.py  # Backend SQL dev/test (SANDBOX_SQL_BACKEND=postgres)
├── pool.py               # Container Pool Manager + janitor + circuit breaker
├── docker_runner.py      # exec vào container pool + dọn dẹp/retire
├── job_queue.py          # In-memory job queue/store cho /execute/*
└── minio_store.py        # Audit-copy code lên MinIO (best-effort)

services/workflow-runtime/
├── runtime_services/sandbox_proxy.py  # relay đọc-only sang sandbox-executor
└── routers/sandbox_ops.py             # GET /api/sandbox/{status,job/*,stream/*}

src/components/agent-builder/workflow/
├── ExecutionCenter.tsx     # tab Queue nằm trong đây
└── SandboxQueuePanel.tsx   # panel thật: poll status + SSE stream
```
