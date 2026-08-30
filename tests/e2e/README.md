# E2E — một lượt hỏi thật, từ trình duyệt tới kho dữ liệu

Bộ chạy này dựng **đúng chuỗi sản phẩm**: Keycloak → Kong (JWT, tiêm danh tính)
→ orchestrator → workflow-runtime → semantic layer → sandbox-executor (SQL chỉ-đọc)
→ kho demo, với LLM là **ollama trên máy** qua litellm. Không mock ở bất kỳ tầng nào.

```bash
tests/e2e/e2e.sh all        # lần đầu ~5 phút (không build lại image — xem "Chế độ chạy"), sau đó vài phút
```

## Chế độ chạy & cách ly

* **`E2E_MODE=dev` (mặc định)** — dùng image đã có (`infra-*`) và **bind-mount source** hiện tại vào
  `/app` (overlay `deploy/docker/docker-compose.e2e-dev.yml`); chỉ build image nào thiếu. Đổi code Python →
  `docker restart dataagent-workflow-runtime` là đủ, không cần rebuild (pip install qua mạng chậm mất 30–40 phút).
  `E2E_MODE=build` rebuild toàn bộ như CI.
* **Project compose riêng `dataagent-e2e`** (`E2E_PROJECT`) — volume Postgres/Keycloak/OpenBao **mới** mỗi lần
  `down -v`; stack dev hằng ngày (project `infra`) giữ nguyên dữ liệu. Vì `container_name` cố định, `up` sẽ
  gỡ container `dataagent-*` của project khác (volume của chúng không bị đụng).
* Keycloak của stack E2E được chuyển `browserFlow` → `browser` (realm gốc dùng "Browser MFA" bắt cài OTP,
  Playwright không thể qua). Production không đổi.
* Sandbox chạy với `SANDBOX_POOL_SIZE=0` (đường SQL không cần container Python warm); `/execute/python`
  cần `docker build -t sandbox-runner:latest backend/services/sandbox-executor/sandbox_runner/`.

Từng bước, chạy lại được độc lập:

| Bước | Làm gì | Lặp lại được? |
|---|---|---|
| `preflight` | docker, ollama (+ pull `qwen2.5:3b`), sinh `GATEWAY_KEY` / `SERVICE_AUTH_SECRET` vào `.env` nếu trống, giữ đúng **một** model trên GPU | ✅ |
| `up` | `docker compose up` 16 service cần cho một lượt agent (không có KB tier); chạy alembic + seed tenant | ✅ |
| `setup` | `deploy/docker/kong/kong-setup.sh` (route, plugin JWT, mật khẩu user dev, CORS `:3000`); OpenBao init/unseal, KV v2 `secret/`, AppRole chỉ-đọc → `.env`, ghi mật khẩu kho demo; tạo lại workflow-runtime | ✅ |
| `seed` | Postgres: agent `e2e-sql-agent`, 2 chỉ số đã duyệt (`doanh_thu_gop`, `doanh_thu_thuan`) + từ đồng nghĩa, hàng `secrets_vault`, bảng `fact_sales` 8 tháng trong `sandbox_dev`; Mongo: canvas planner → db_connector → rrf → reranker → responder/visualization → judge | ✅ (rebuild bảng demo) |
| `smoke "câu hỏi"` | In từng frame SSE, tô riêng `__evidence` (SQL, metric, số dòng) | ✅ |
| `test` | `pytest -m e2e tests/integration/test_agent_answer_e2e.py` | ✅ |
| `ui` | Playwright: đăng nhập Keycloak thật → Hỏi dữ liệu → hỏi → kiểm tra bảng/bằng chứng; ảnh ở `tests/e2e/out/` | ✅ |
| `status` / `logs svc` / `token` / `down [-v]` | tiện ích | |

## Hai loại kiểm tra — vì sao tách

* **Giao thức** (luôn bắt buộc): 401 khi không token; `__session_id` → text → `[DONE]`;
  frame `__evidence` đúng shape; `/api/reviews` và `/api/catalog` đi qua Kong;
  câu lệnh ghi (`DELETE …`) không bao giờ chạy và bảng còn nguyên.
* **Hành vi** (bắt buộc khi `E2E_STRICT=1`, mặc định báo **xfail**): câu hỏi khớp chỉ số
  được trả lời từ hợp đồng (`generated_by = semantic_layer`), câu nhập nhằng bị hỏi lại
  trước khi chạy, trả lời câu hỏi lại → có truy vấn. Các ca này phụ thuộc planner trích
  slot; với model 3B local đúng phần lớn nhưng không phải luôn — xfail giữ tín hiệu mà
  không làm bộ test bập bênh. Chạy strict khi trỏ litellm sang model production.

Mọi lượt được ghi ra `tests/e2e/out/turn-*.json` (toàn bộ frame) để soi khi đỏ.

## Biến môi trường

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `E2E_USER` / `E2E_PASS` | `helios-engineer` / `H3lios@Eng001!` | user Keycloak (bảng user dev trong `deploy/docker/README.md`) |
| `E2E_AGENT_ID` | `0000000e-2e00-4000-8000-000000000001` | agent đã seed |
| `E2E_STRICT` | `0` | `1` → ca hành vi thành hard fail |
| `E2E_TURN_TIMEOUT` | `240` | giây chờ một lượt (GPU dùng chung: 1 lượt ≈ 10–40 s) |
| `E2E_PYTHON` | `python3` | interpreter có `pytest`, `httpx` (venv của bạn) |
| `OLLAMA_MODEL` | `qwen2.5:3b` | phải trùng `deploy/docker/litellm/config.agent-local.yaml` |
| `E2E_HEADED` | `0` | `1` → xem Playwright chạy |

## Kết quả lần chạy 30-08-2026 (ollama `qwen2.5:3b`, M-series)

* API: **8 pass, 2 xfail** (model-dependent: chọn đúng contract trong 2 chỉ số, follow-up sau hỏi lại).
* UI: **pass** — login Keycloak thật → hỏi → 18,5 s → bảng dữ liệu, SQL trong panel, badge "đã duyệt".
* Lỗi sản phẩm mà bộ E2E này phát hiện và đã sửa (unit test đi kèm):
  1. Route sau planner rơi về node retrieval đầu tiên khi có clarification → canvas `db_connector`
     **vừa hỏi lại vừa chạy SQL** (`graph.py`).
  2. Judge lặp lại lượt đang hỏi lại (`graph.py`).
  3. `_ToolCall` bắt buộc `confidence` → model bỏ trường này là mất toàn bộ slots (`planner.py`).
  4. Planner không biết ngày hôm nay → "tháng này" thành năm 2023 (`planner.py`, `BUSINESS_TIMEZONE`).
  5. "theo tháng/quý/năm" bị coi là dimension lạ → rơi khỏi contract sang LLM-SQL (`semantic_layer.py`).
  6. Dấu `;` cuối SQL của model làm sandbox (`SELECT * FROM (…) LIMIT`) lỗi 3/3 lần (`_utils.py`).
  7. Kong thiếu route `/api/reviews` (Review Center 404 qua gateway) (`kong-setup.sh`).
  8. Compose: mount `./postgres/init.sql` không tồn tại → init Postgres dừng, schema `keycloak` không được
     tạo trên volume mới; OpenBao nạp `config.hcl` hai lần + `config.prod.hcl` → trùng cổng 8200.
  9. Kafka chưa có topic/ACL → producer audit chờ 60 s **mỗi truy vấn** (giờ `setup` tạo topic; vẫn nên
     bỏ chờ đồng bộ trong `kafka_producer.py`).
* Đường "Tự động" (đa-agent ToT) với model 3B: lập kế hoạch >2 phút và giao việc cho "Default Agent" chưa
  publish → thất bại/lập lại. Chưa sửa — UI E2E chọn agent cụ thể.

## Khi đỏ, nhìn vào đâu

* **401 ở stream** → `setup` chưa chạy sau khi đổi `GATEWAY_KEY`, hoặc token hết hạn (15 phút).
* **`__evidence` không có `runs`, text nói "không kết nối được DB"** → OpenBao: xem
  `logs workflow-runtime` có `could not resolve db_connector secret_id`; chạy lại `setup`
  (AppRole mới được ghi vào `.env` và runtime được tạo lại).
* **Lượt treo > 60 s** → hai model đang nằm trên GPU; `preflight` unload model lạ, hoặc
  `docker stop llm-exporter kb-model-service` nếu stack KB đang chạy (docs/AGENT-E2E.md §5).
* **`migrate` lỗi** → `tests/e2e/e2e.sh logs migrate`; volume Postgres cũ có thể lệch head.
* **Keycloak login form không hiện** → `.env` `VITE_KEYCLOAK_URL` phải là `http://localhost:8080`
  (issuer trong token phải khớp `KEYCLOAK_PUBLIC_URL` mà `kong-setup.sh` cấu hình).
