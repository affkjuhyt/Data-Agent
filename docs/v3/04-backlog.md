# 04. Backlog V3: R0 và R1

> Ngày 06/10/2026. Cột **TT** là nơi ghi tiến độ: `todo` → `doing` → `done <commit>`; cắt ghi `cut`. Ai làm
> xong một ID thì sửa cột này trong cùng commit (cùng quy ước với `docs/v2/05`). Cỡ: S < 1 tuần-người,
> M 1–3, L > 3. Thiết kế kỹ thuật của R0 ở [03](03-r0-technical-design.md); R1 trở đi tham chiếu
> [00](00-product-strategy.md), [01](01-agent-core-and-ui.md), [02](02-market-solution.md).
>
> **Linear:** project P-NOP-3 "Data Agent V3 · Sản phẩm doanh nghiệp", milestone R0–R4; mỗi story mang mã
> ở cột ID trong tiêu đề, task con `[BE]/[FE]/[DB]/[AI]` theo lớp. Project V2 P-NOP-2 đã đóng 06/10/2026.

## 0. Người và điều kiện bắt đầu

D2 chốt 06/10/2026: **dời** nghiệm thu dầu/vàng 27/10; cả đội vào R0 rồi R1; lịch R1 xếp sau discovery
với khách thiết kế (D3). Cột "Vai" bên dưới giữ tên vai của bản đầu; người thật:

| Người | Phụ trách | Vai trong bảng |
| --- | --- | --- |
| Trần Hải Long | Agent core và giao diện: R0-1, R0-10, R0-7, R0-8, R0-11; R1-08, R1-09, R1-11, R1-13, R1-14; mọi `[FE]` | SR1, FE1, FE2 |
| Đức Long | Nền tảng và dữ liệu: R0-2, R0-3, R0-4, R0-5, R0-6, R0-12; R1-01…R1-07, R1-10; mọi `[DB]` | BE2, BE1, SR2, DE, Ops |
| Quân | Dữ liệu, đánh giá, kiểm thử: R0-13, R1-12, R1-15, R1-16; `[Data]` của R0-9 và R1-06 | RS, QA |
| Linh | Model: R0-9; mọi `[AI]` (R0-10, R0-11, R1-03, R1-05, R1-08, R1-09) | ML |

Năng lực: R0 khoảng 13 tuần-người (5 việc M, 7 việc S) trên hai người làm mã chính, tức khoảng 6 tuần
lịch nếu không thêm người. Thứ tự ưu tiên trong R0 khi phải cắt: R0-1 → R0-7 → R0-10 → R0-8 → R0-11 →
R0-6 → R0-5 → R0-3 → R0-4 → R0-2 → R0-12.

## 1. R0 Nền mã

| ID | Việc | Phụ thuộc | Vai | Cỡ | Xong khi | TT |
| --- | --- | --- | --- | --- | --- | --- |
| R0-1 | Kênh result set: contract `ResultSet`, state `result_sets`, không vào rrf/reranker, responder + judge + visualization đọc trực tiếp, frame `__result_set`, lưu MinIO | | SR1 | M | 03 §1 test 1–4 xanh; `db_results` không còn | done 2bfd519 |
| R0-10 | Plan IR + validator tĩnh; orchestrator validate trước executor; `runtime.task_run`; frame `__plan`, `__step` | | BE2 | M | 03 §9 test; plan sai không chạy node nào | todo |
| R0-7 | Mã lỗi hợp nhất `libs/contracts/v1/errors.py`; bỏ `DEV_CONFIG` fallback và planner fallback; frame `__refusal`; RefusalCard | | BE2, FE1 (0,25) | S | DB lỗi → `AGENT_CONFIG_ERROR`, không trả lời bằng agent khác | done ec8def5 |
| R0-8 | Frame `__verification`; VerifierBadge | R0-1, R0-7 | SR1, FE1 (0,25) | S | Frame phát đúng một lần/câu trả lời | todo |
| R0-11 | Lớp lỗi trong judge và bảng định tuyến tất định; dừng khi lặp lớp | R0-8 | SR1 | S | 03 §10 test | todo |
| R0-2 | MCP chuẩn: SDK `mcp`, `infrastructure/mcp_client.py`, planner thấy tool + schema, `mcp.py` gọi `call_tool`, `MCP.protocol`, từ chối legacy khi publish | R0-6 (api_key_ref) | BE2 | M | FastMCP test in-process; không còn `"/execute"` | todo |
| R0-3 | Checkpointer Postgres cho agent đơn; bảng `runtime.pending_review` có RLS; resume sau restart | | Ops + BE2 (0,25) | S | Integration resume xanh; `MemorySaver` không còn | todo |
| R0-4 | Canvas: registry là nguồn duy nhất (kèm style), xoá 7 loại node không chạy khỏi frontend, validate khi lưu (dry-run graph), một document/version, 409 khi đã publish, cache theo `canvas_hash` | | BE2, FE2 (0,5) | M | 03 §4 test; knip sạch | todo |
| R0-5 | `/run` công khai không nhận `memory_context`/`guardrail_cfg`/`orchestrated`; `InternalRunRequest` chỉ cho service `orchestrator` | | SR1 | S | 422 cho client; orchestrator qua assertion | todo |
| R0-6 | Sandbox xác thực service assertion; `connection_ref` thay mật khẩu; schema crawler đọc OpenBao; policy OpenBao qua Ansible; xoá env mật khẩu | | Ops | M | 401 không assertion; grep CI không còn `WAREHOUSE_PASSWORD__` | todo |
| R0-9 | Đo model (D1): `make bench` + eval hai cấu hình, báo cáo một trang | | Ops, QA | S | Bảng điểm nộp anh Linh | todo |
| R0-13 | Kịch bản kiểm thử và nghiệm thu đợt R0: kịch bản chạy trên cụm dev cho từng story R0, ghi lỗi theo mức, biên bản ký | R0-1…R0-12 | Quân | M | Biên bản nghiệm thu R0 ký | todo |
| R0-12 | RUNBOOK: HITL bền, MCP, OpenBao policy; `docs/v2/09-frontend.md` ghi chú Workspace (D9) | R0-2, R0-3, R0-6 | Ops, FE1 | S | Docs cùng commit | todo |

## 2. R1 Hỏi đúng trên kho + ngữ cảnh

Gộp W1–W3 của bản v1.0 và phần V2 đang làm (ingest, release, DQ, review, effective; `docs/v2/05` giữ ID
của chúng, không đánh lại). Chỉ liệt kê việc **mới** so với V2.

| ID | Việc | Phụ thuộc | Vai | Cỡ | Xong khi | TT |
| --- | --- | --- | --- | --- | --- | --- |
| R1-01 | Connections: bảng `registry.connection` (engine, host, db, `secret_ref`, owner, classification, read-only role, giới hạn rows/bytes/timeout); UI "Connections" thay WarehouseCenter Snowflake-only; connection test | R0-6 | BE1, FE2 | M | Thêm Postgres/ClickHouse/Snowflake từ UI; không mật khẩu trong Postgres | todo |
| R1-02 | Executor MySQL, SQL Server (đọc-chỉ; thêm SQLGlot làm dependency để parse/kiểm theo dialect — hiện sandbox dùng `sql_scan` + sqlparse, compiler semantic chỉ có postgres/clickhouse); BigQuery/Databricks/Oracle/Trino theo khách (cắt nếu không có khách) | R1-01 | SR2 | M | Test readonly + guard cho mỗi engine | todo |
| R1-03 | Schema crawler thu PK/FK, cardinality, null rate, giá trị mẫu; join hint từ khoá thật trước, LLM sau và gắn `proposed` | R0-6 | DE | M | Catalog có FK; join LLM không được dùng khi chưa duyệt | todo |
| R1-04 | Catalog thống nhất `catalog.asset` (table, column, document, series, api) + `asset_id` dùng chung; màn Catalog trong Data Studio | R1-03, V2 S1-03 | BE1, FE2 | L | Một màn thấy bảng, tài liệu, series; link tới nguồn | todo |
| R1-05 | Chỉ mục giá trị cột text (HNSW/FTS) cho schema linking; `sem_value_retrieval` | R1-03 | SR2 | M | Từ khoá → giá trị thật; test DeepEye-SQL §3.4 | todo |
| R1-06 | Semantic model: join theo quan hệ entity, metric tỷ lệ/dẫn xuất, time grain, `compare_to` compile; bỏ điều kiện "một kết nối"; import/export YAML (D4) | R1-04 | SR1 | L | 15–25 metric pilot compile đúng; reconciliation 100% | todo |
| R1-07 | API + UI soạn semantic model, glossary, instruction (Data Studio); OCC + maker/checker dùng chung Review Inbox V2 | R1-06, V2 S6-03 | SR1, FE1 | L | Steward tạo/sửa metric qua UI, có version | todo |
| R1-08 | Verified Query: bảng, API, UI; khớp câu hỏi → chạy SQL tham số hoá, nhãn VERIFIED; đề xuất từ câu trả lời được 👍 | R1-07 | BE2, FE1 | M | ≥ 30 verified query pilot; nhãn hiện trên UI | todo |
| R1-09 | Chuyên gia SQL: schema linking hợp nhất + đóng bao FK, N-version (3 cách sinh), unit test tất định fail-fast, cổng confidence; `generated_by` + confidence vào `ResultSet` | R0-1, R1-03, R1-05 | SR2 | L | Ablation nội bộ trên Eval Set; confidence hiện ở `__verification` | todo |
| R1-10 | Quyền trước khi sinh: Data Space ∩ quyền người hỏi lọc catalog/semantic/tài liệu trước prompt; thực thi bằng role read-only theo connection; `FORBIDDEN` tách khỏi `NO_DATA` | R1-04 | BE1 | M | Test isolation: user không quyền không thấy tên bảng trong prompt | todo |
| R1-11 | Data Space: bảng + API + UI (asset[], semantic subset, glossary, verified query, instruction, quyền, eval set); agent gắn Data Space | R1-04, R1-07 | BE2, FE2 | L | Agent chỉ thấy Data Space của nó | todo |
| R1-12 | Eval Set theo Data Space: schema case (01 §6: hợp đồng đóng băng, so multiset), harness chạy đường text-to-SQL thật, bảng điểm theo nhãn tin cậy, cổng publish agent | R1-11 | QA, RS | M | 100–150 case pilot, hai người giải mù | todo |
| R1-13 | Workspace lưu server: thread/run/artifact tối thiểu (SQL, note, chart) có version; bỏ `localStorage`; URL state (FE-00 V2) | R0-1 | FE1 | M | Mở lại link thấy đúng thread và result set | todo |
| R1-14 | Trust label trên mọi câu trả lời (TrustLabel, ConfidenceMeter) | R0-8, R1-09 | FE1 | S | Nhãn đúng theo `generated_by` | todo |
| R1-16 | Dữ liệu pilot mẫu: kho bán hàng, giá vốn, công nợ, tồn kho và tài liệu chính sách tiếng Việt có sẵn bẫy số liệu (join nhân đôi, trung bình tỷ lệ, cộng tồn kho, tài liệu hết hiệu lực, hai phiên bản cùng kỳ); nạp bằng một lệnh | | Quân | M | R1 dev và R1-12 dùng được trước khi có khách thiết kế | todo |
| R1-15 | Onboarding checklist: 10–20 câu khách → nguồn → metric → eval; màn trạng thái (nguồn chưa kết nối, metric chưa duyệt, eval chưa đạt) | R1-11, R1-12 | FE2, RS | S | Dùng cho khách thiết kế | todo |

Ra R1 khi: Eval Set pilot ≥ 0,95 trên VERIFIED/GOVERNED, `unsupported_number_rate = 0`, test isolation
0 lỗi, 15–25 metric qua reconciliation (02 §4.3).

## 3. R2–R4 (ID giữ chỗ, chi tiết khi R1 vào giữa chừng)

| Đợt | ID | Việc |
| --- | --- | --- |
| R2 | R2-01…R2-10 | Capability Registry bảng (gộp `TOOL_REGISTRY`, node registry, `/capabilities`); supervisor chọn capability/chuyên gia; `libs/analytics` (contribution, driver decomposition, anomaly MAD/STL, forecast baseline); Deep analysis; Workspace đầy đủ (cây artifact, plan canvas, inspector, chạy lại xuôi dòng); ReconciliationNote; Report builder; Dashboard; hybrid số + tài liệu qua catalog/graph; MCP server phơi Data Space (D12); cache theo dependency |
| R3 | R3-01…R3-08 | Scheduler; Monitor vùng an toàn (lõi `detector` V2); alert gom nhóm + kênh Zalo/Teams/email (D10); proposal/action có duyệt; correction memory + Context Studio theo lớp lỗi; SOP có duyệt; gợi ý chủ động; forecast baseline nhãn experimental |
| R4 | R4-01…R4-07 | Agent Studio (T2: từ mô tả + Data Space, eval trước publish); skill có cổng (T3); danh tính agent; PII masking result set; giới hạn chi phí; A2A; HA/restore/security eval; Publisher chỉ khi D8 mở lại |

## 3b. API /v1 (khối `api`, thiết kế ở [06](06-api-v1.md))

Thêm 11/10/2026 khi giao diện v3.1 đã đủ màn và bắt đầu thay mock MSW bằng API thật. Nguồn đặc tả là tab 4
của tài liệu thiết kế hệ thống; cổng A2 trong CI giữ mã khớp đặc tả.

| ID | Việc | Phụ thuộc | Vai | Cỡ | Tiêu chí | TT |
| --- | --- | --- | --- | --- | --- | --- |
| API-00 | Khung `backend/services/api`: kernel (token JWKS, `app.org_id` mỗi giao dịch, problem+json, con trỏ, SSE), bản chụp A2 và cổng so OpenAPI, suite `api`, CI, import-linter | | BE | S | `make test-unit UNIT_SUITES=api:api` xanh; route ngoài A2 lỗi khi import | done 543d7be0 |
| API-01 | Bộ giải danh tính và năng lực từ `org.*` (toàn tổ chức + Data Space qua `X-Data-Space`), `GET /v1/data-spaces`, `GET /v1/me` (thêm vào A2) | API-00, ORM `libs/dataagent_db` | BE | S | Test tích hợp trên DDL thật: năng lực theo vai trò, hết hạn, nhóm, cô lập tổ chức, không vào Data Space khi không là thành viên | done dc268b2b |
| API-02 | Hỏi đáp + Truy vấn: câu hỏi 202, luồng SSE có `answer_id`, câu trả lời, phản hồi, biên nhận | API-01 | BE, FE | L | Nhóm vào `COMPLETE_GROUPS`; màn Hỏi đáp chạy không cần mock | done afffb02d |
| API-03 | Phiên làm việc | API-01 | BE, FE | M | Như trên cho màn Phiên | done ad1168c0 |
| API-04 | Cần bạn và đánh giá | API-01 | BE, FE | M | Như trên cho màn Cần bạn | done b35f6655 |
| API-05 | Agent: lượt chạy, sự kiện SSE, tạm dừng/tiếp tục | API-02 | BE, FE | L | Như trên cho màn Agent | todo |
| API-06 | Triển khai khối `api`: manifest, Ansible, route `/v1` ở gateway, secret từ OpenBao | API-02 | Ops | S | `make deploy` đưa `api` lên cụm dev; e2e gọi `/v1` qua gateway | todo |

## 4. Quy tắc theo dõi

- Thứ Sáu hằng tuần rà cột TT; việc trễ > 2 ngày thì cắt hoặc đổi người ngay trong tuần.
- Mỗi ID một PR; PR ghi ID trong tiêu đề; test và docs trong cùng PR.
- Không mở ID của đợt sau khi đợt trước chưa đạt tiêu chí ra (00 §9).

## 5. Thứ tự thực hiện (theo phụ thuộc trên Linear P-NOP-3)

Trong mỗi story: `[DB]` → `[BE]` → `[FE]`; `[AI]` chạy song song và phải xong trước bước `[BE]` cắm prompt.
Không mở R1 khi biên bản R0-13 chưa ký; ngoại lệ là việc chuẩn bị không đụng mã sản phẩm (dữ liệu pilot,
định nghĩa chỉ số, viết kịch bản kiểm thử).

### 5.1 R0

| Bước | Hải Long | Đức Long | Linh | Quân |
| --- | --- | --- | --- | --- |
| 1 | R0-1 NOP-278 (279 → 282) | R0-6 NOP-347 | — chờ bộ câu của Quân | R0-9 [Data] NOP-363 |
| 2 | R0-7 NOP-301 (319 → 321) | R0-5 NOP-342 | R0-9 NOP-352 | R0-13 viết kịch bản NOP-365 |
| 3 | R0-10 NOP-292: 295 phần contract + validator | R0-3 NOP-300 (302 → 304) | R0-10 [AI] NOP-357 | R1-16 dữ liệu pilot NOP-364 |
| 4 | R0-10: 295 cắm prompt → 296 | R0-4 NOP-325 (332) | R0-11 [AI] NOP-358 | R1-06 [Data] NOP-362 |
| 5 | R0-8 NOP-324 (327 → 329) | R0-2 NOP-290 (294 → 297) | | |
| 6 | R0-11 NOP-338 (359) · R0-4 FE NOP-334 | R0-12 NOP-354 | | R0-13 chạy kịch bản, biên bản |

Thứ tự cắt nếu trễ (giữ từ trên xuống): R0-1 → R0-7 → R0-10 → R0-8 → R0-11 → R0-6 → R0-5 → R0-3 →
R0-4 → R0-2 → R0-12.

### 5.2 R1 (sau khi ký biên bản R0)

| Bước | Hải Long | Đức Long | Linh | Quân |
| --- | --- | --- | --- | --- |
| 1 | R1-13 NOP-328 (339 → 341, sau DB 337) | R1-01 NOP-280 (285 → 287) · R1-13 DB 337 | R1-03 [AI] NOP-360 | R1-16 hoàn tất, giải mù câu đối chứng |
| 2 | R1-01 FE NOP-289 | R1-03 NOP-305 (320 → 322) | R1-05 [AI] NOP-345 | R1-06 [Data] NOP-362 |
| 3 | R1-04 FE NOP-336 | R1-04 NOP-326 (330 → 333) | R1-08 [AI] NOP-361 | R1-12 dựng case từ dữ liệu pilot |
| 4 | R1-11 NOP-281 (DB 284 → 286 → 288) | R1-05 BE NOP-346 · R1-06 NOP-283 (291 → 293) | R1-09 [AI] NOP-355 | |
| 5 | R1-08 NOP-344 (DB 348 → 349 → 350) | R1-07 NOP-303 (331 → 335) · R1-10 NOP-351 | hiệu chỉnh ngưỡng R1-09 trên Eval Set | R1-12 NOP-299 |
| 6 | R1-09 NOP-353 (356) → R1-14 NOP-277 · R1-07 FE NOP-340 | R1-02 NOP-298 (chỉ khi khách cần MySQL/SQL Server) | | R1-15 NOP-323 |

Đường găng R1 nằm trọn ở Đức Long: R0-6 → R1-03 → R1-04 → R1-06 → R1-07 → R1-08/R1-11 → R1-12 → R1-15.
Muốn rút ngắn thì chuyển R1-05 BE, R1-10, R1-02 sang người khác; không chuyển R1-03, R1-04, R1-06.
