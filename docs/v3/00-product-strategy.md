# 00. Data Agent: định vị, quyết định sản phẩm, kiến trúc đích

> Vai trò viết: BA/PO. Ngày 06/10/2026. Đầu vào: rà mã thực tế ở HEAD `33e963d` (agent core, tầng dữ
> liệu, frontend), bộ tài liệu [`docs/v2/`](../v2/README.md), và khảo sát thị trường data agent tới
> 10/2026 (nguồn ở mục 11).
>
> Tài liệu này là **đầu vào để thiết kế hệ thống**, không phải thiết kế chi tiết. Nó trả lời: sản
> phẩm là gì, cho ai, "cao cấp nhất" nghĩa là gì; hiện trạng thật ra sao; cần thêm gì, sửa gì, nối gì;
> thứ tự làm. Agent core và giao diện được quyết chi tiết ở [01-agent-core-and-ui.md](01-agent-core-and-ui.md)
> (mức tự chủ theo vai, Plan IR, đường SQL, phản tư theo nguyên nhân gốc, bố cục màn, bất biến 16–17).
> Định vị bán ra thị trường, phản biện bản thiết kế v1.0 và các đối thủ (Elton, Looker, Fabric, Dash) ở
> [02-market-solution.md](02-market-solution.md); nó đổi "Thread/Notebook/Report" thành **Workspace**
> (cây artifact có version) và thêm D8–D12.

## 0. Tóm tắt một trang

**Data Agent là một "nhà phân tích dữ liệu số" cho doanh nghiệp:** hiểu dữ liệu của tổ chức (kho dữ
liệu, data mart, CSDL nghiệp vụ, file, tài liệu, web), trả lời câu hỏi với con số kiểm chứng được, tự
làm các việc của data analyst (phân tích, giải thích biến động, báo cáo, theo dõi, cảnh báo), và
chạy được cả khi không ai hỏi.

Mười quyết định sản phẩm (giải thích ở các mục sau):

| # | Quyết định | Vì sao |
| --- | --- | --- |
| Q1 | **Lớp ngữ cảnh (Context Layer) là sản phẩm lõi**, không phải model sinh SQL | Mọi nhà dẫn đầu 2026 cạnh tranh ở ngữ cảnh: semantic model, verified query, glossary, ontology, memory sửa lỗi. Số Snowflake công bố (tự đo): 24% → 86% chỉ nhờ ngữ cảnh |
| Q2 | **Dữ liệu doanh nghiệp truy vấn tại chỗ**, không sao chép vào hệ thống | Kho/mart đã có quyền, độ tươi, chủ sở hữu. Chỉ chép khi cần vintage/as-of (chuỗi chỉ số V2) |
| Q3 | **Hai đường cho số: semantic (có quản trị) và SQL/Python khám phá**, mỗi câu trả lời mang **nhãn tin cậy** | Semantic layer gần như đúng tuyệt đối trong phạm vi nó phủ, 0% ngoài phạm vi; text-to-SQL phủ rộng nhưng sai "trông hợp lý". Cần cả hai, và người dùng phải thấy đang dùng đường nào |
| Q4 | **Data Space** là đơn vị cấu hình: tài sản dữ liệu + semantic model + glossary + verified query + chỉ dẫn + quyền + bộ eval | Một agent "biến hoá" bằng cách gắn Data Space và kỹ năng khác nhau, không viết mã |
| Q5 | **Agent = cấu hình, kỹ năng = capability đã đăng ký**. "Tự biến hoá" ở ba tầng: lập kế hoạch động mỗi câu hỏi, đề xuất agent mới từ mô tả, học từ sửa lỗi; tầng 2–3 luôn qua eval và người duyệt | Giữ bất biến 12 của V2; thị trường hội tụ supervisor + chuyên gia + skill |
| Q6 | **Quyền kiểm trước khi sinh**: agent chỉ thấy schema, tài liệu, metric mà người hỏi được phép thấy; thực thi theo danh tính người hỏi | Kiểm sau khi sinh làm "không có dữ liệu" và "không có quyền" trông như nhau; rò schema qua prompt |
| Q7 | **Chế độ phân tích sâu** (deep analysis) là tính năng hạng nhất: lập kế hoạch, chạy nhiều truy vấn, kiểm, ra báo cáo có thể tái lập | Snowflake, Databricks, Google, AWS đều đã có; đây là "việc của data analyst" |
| Q8 | **Sản phẩm của phân tích là đối tượng bền**: artifact trong Workspace (SQL view, note, chart, report, plan) có version và trạng thái deploy → Dashboard → Monitor, chia sẻ được, chạy lại được | Hiện câu trả lời mất khi đóng tab (lịch sử nằm `localStorage`); Elton cho thấy mô hình cây artifact chạy được (02 §2.3) |
| Q9 | **Eval riêng theo từng Data Space là cổng phát hành**, không dựa benchmark công khai | Nghiên cứu CIDR 2026: 52,8% nhãn BIRD và 66,1% nhãn Spider 2.0-Snow sai |
| Q10 | **Mười hai bất biến V2 giữ nguyên tinh thần**, mở rộng thêm ba (mục 5) | V2 đúng hướng thị trường; chỉ cần nâng từ "chuỗi chỉ số dầu/vàng" lên "mọi dữ liệu doanh nghiệp" |

Ba việc phải làm trước mọi tính năng mới (mục 4.3): (1) sửa đường đi của kết quả SQL trong runtime,
nó đang bị xử lý như đoạn văn bản và bị cắt; (2) sửa các lỗi khiến MCP, phê duyệt người dùng và một
nửa loại node trên canvas không chạy; (3) quyết năng lực model, vì Qwen3-14B trên một A100 dùng chung
là trần chất lượng của toàn bộ sản phẩm.

## 1. Người dùng và việc họ cần làm

| Vai | Việc cần làm (job) | Bề mặt chính |
| --- | --- | --- |
| **Người dùng nghiệp vụ / quản lý** | Hỏi nhanh một con số, hiểu vì sao nó đổi, nhận báo cáo định kỳ và cảnh báo không cần hỏi | Ask, hộp thư Alert, Báo cáo, Slack/Teams/Zalo |
| **Data analyst** | Phân tích nhiều bước, so sánh, phân rã nguyên nhân, dự báo; kiểm lại SQL; biến câu trả lời tốt thành tài sản dùng lại (verified query, báo cáo, dashboard) | **Workspace**: cây artifact có version (files, notes, plan, sql, dashboards, reports) + chat + plan canvas (D9, 02 §2.3) |
| **Data steward / chuyên gia miền** | Định nghĩa metric, thuật ngữ, quan hệ; duyệt đề xuất của máy; sửa câu trả lời sai và để hệ thống học | Data Studio (Catalog, Semantic Model, Glossary), Review Inbox, Context Studio |
| **Data engineer / quản trị** | Kết nối nguồn, phân quyền, theo dõi chi phí và độ tươi | Connections, Catalog, Settings |
| **Agent builder** (phòng ban) | Tạo agent cho một nhóm: chọn Data Space, kỹ năng, kênh, lịch | Agent Studio |
| **Hệ thống ngoài / developer** | Gọi agent hoặc metric qua API, MCP, A2A | API, MCP server |

Phạm vi "việc của data analyst" mà agent phải làm được, theo mức độ:

| Mức | Việc | Ví dụ |
| --- | --- | --- |
| Mô tả | KPI, lọc, nhóm, xếp hạng, so kỳ | "Doanh thu Q3 theo vùng, so cùng kỳ" |
| Chẩn đoán | Phân rã đóng góp, tìm driver, giải thích biến động, đối chiếu tài liệu | "Vì sao biên lợi nhuận miền Bắc giảm tháng 9?" |
| Phát hiện | Bất thường, đổi chế độ, vi phạm ngưỡng, độ tươi | Monitor tự chạy, alert có điều tra kèm theo |
| Dự đoán | Dự báo có khoảng tin cậy, kịch bản what-if | "Tồn kho cuối quý nếu nhu cầu giảm 10%" |
| Tổng hợp | Báo cáo nhiều phần, có bảng, biểu đồ, trích dẫn, tái lập được | Báo cáo tuần tự sinh và gửi |

## 2. "Cao cấp nhất" vào tháng 10/2026 nghĩa là gì

Tóm từ khảo sát (chi tiết và nguồn ở mục 11). Số của nhà cung cấp là **tự đo trên dữ liệu không công
khai**, chỉ dùng để thấy hướng.

| Năng lực | Ai đã có | Data Agent hiện có? |
| --- | --- | --- |
| Semantic model nhiều bảng (entity, metric, dimension, join), có công cụ soạn và tự đề xuất | Snowflake Semantic View + Autopilot, Looker LookML, dbt MetricFlow (Apache 2.0), Cube, Wren MDL, AWS Topics (12 dataset) | Một phần: metric một bảng, không join, không có API/UI soạn |
| Verified query / trusted asset làm few-shot và nhãn "đã kiểm" | Snowflake VQR, Databricks trusted assets, Fabric example queries, Google golden queries | Không |
| Ngữ cảnh tự dựng: lineage, query log, mã pipeline, tài liệu nội bộ, ontology | Snowflake Cortex Sense / Horizon Context, Databricks Genie Ontology, OpenAI 6 lớp ngữ cảnh | Một phần: schema crawler có mô tả do LLM viết, join do LLM đoán, không PK/FK, không giá trị mẫu |
| Memory sửa lỗi + vòng duyệt của steward | OpenAI, Wren, Hex Context Studio, Genie Code | Có memory hội thoại/người dùng; không có memory sửa lỗi có duyệt |
| Supervisor + agent chuyên gia; nhiều model theo vai | Databricks Supervisor Agent (GA), Snowflake Cortex Agents | Có orchestrator DPTS + task DAG; chỉ chọn trong agent đã publish |
| Deep analysis / research mode | Genie Agent mode, Snowflake CoWork Deep Research, AWS Quick Research | Không |
| Lập lịch, monitor, alert | Genie One, CoWork automations, Google anomaly agents | Không chạy (màn lập lịch có, không có scheduler) |
| Kết hợp tài liệu + SQL trong một câu trả lời | Cortex Agents (Analyst + Search), Genie file reasoning, Spotter 3 | Có cả hai đường nhưng kết quả SQL bị trộn vào đường văn bản (mục 3.2) |
| Danh tính agent, quyền theo agent, masking, audit từng tool call | Snowflake AI Agent Identity (GA 06/2026), Unity AI Gateway | Audit truy cập có; quyền theo agent và masking kết quả SQL không |
| MCP server + A2A ra ngoài | Mọi nhà cung cấp lớn | Không ("MCP" hiện là POST tự chế) |
| Eval theo bộ câu hỏi riêng, chạy CI và canary | OpenAI, Snowflake, Databricks, Fabric SDK | `model/eval` 117 case, không case nào chạy đường text-to-SQL thật |
| On-prem / chủ quyền dữ liệu | Hầu như chỉ Palantir; phần mở: Wren, Cube Core, MetricFlow | **Có, đây là lợi thế**: toàn bộ chạy trên k3s nội bộ |

Ba kết luận dùng cho quyết định:

1. **Đường đua là ngữ cảnh.** Gartner (Market Guide Agentic Analytics, 02/2026) dự báo đến 2028, 60%
   dự án agentic analytics chỉ dựa vào MCP sẽ thất bại vì thiếu semantic layer nhất quán.
2. **Bài toán chưa ai giải xong là tương tác và nhiều bước.** BIRD-Interact: mô hình tốt nhất ~16%;
   KramaBench: ~55% so với người ~77%. Vì vậy hỏi lại, duyệt và minh bạch quy trình là tính năng lõi.
3. **Lợi thế của chúng ta là on-prem, tiếng Việt, và kỷ luật quản trị dữ liệu của V2** (provenance,
   as-of, HITL, OCC). Cloud của các nhà lớn không vào được ngân hàng, cơ quan nhà nước, doanh nghiệp
   cần dữ liệu không rời hạ tầng.

## 3. Hiện trạng đo được (06/10/2026)

### 3.1 Cái đã có và dùng được

| Khối | Hiện thực | Nơi |
| --- | --- | --- |
| Nguồn web | Nguồn thu thập (`acquire`), tải trong miền khai báo, 1 yêu cầu/giây/miền, robots, bản gốc bất biến MinIO `da-raw-crawl` theo SHA-256, trích bản ghi theo schema đã duyệt vào hàng duyệt; lượt theo lịch chờ Temporal (ADR-33) | `crawler/` (KB-02, KB-03) |
| Pipeline tài liệu | Trang (PyMuPDF + OCR vie/eng), chunk theo cấu trúc văn bản Việt, PII theo bộ sưu tập, embedding bge-m3 vào Qdrant; claim và trường trích bằng LLM có kiểm trích dẫn (KB-05, KB-07) | `worker_document/`, `worker_agent/documents/` |
| Truy hồi tài liệu | Vector + FTS Postgres, RRF, cross-encoder TEI; không mở rộng đồ thị (Neo4j hoãn, ADR-04) | `worker_agent/documents/retrieve.py` |
| Text-to-SQL trên CSDL khách | Postgres, ClickHouse, Snowflake; xếp hạng bảng/cột, catalog, tự sửa 3 lần, `sql_guard` chỉ SELECT, sandbox read-only, audit | `workflow_runtime/.../tool_executor/handlers/data.py`, `sandbox_executor/` |
| Schema crawler | Postgres/Snowflake/ClickHouse qua `information_schema`; mô tả, từ khoá, gợi ý join do LLM viết; owner, phân loại, độ tươi | `platform_workers/workers/schema_crawler/` |
| Semantic layer | Metric một bảng, synonym, phát hiện câu hỏi thiếu (thiếu kỳ, metric mơ hồ, so sánh thiếu mốc) | `libs/semantic/`, `workflow_runtime/domain/semantic_layer.py` |
| Agent authoring | Agent + version, canvas workflow (xyflow, lưu Mongo), publish, playground có trace | `workflow_runtime/api/routers/{agents,workflows,canvas}.py`, `frontend/.../agent-builder/` |
| Đa agent | Orchestrator: input_refine → route → planner DPTS → executor task DAG → summary + judge; checkpointer Postgres; replan ≤ 2 | `workflow_runtime/application/orchestrator/` |
| Kiểm chứng | Verifier tất định 4 kiểm (`numbers_grounded`, `precision_supported`, `vintage_consistent`, `citations_resolved`) + judge LLM; guardrail vào/ra | `libs/indicator/verifier.py`, `nodes/model_harness/judge.py` |
| Memory | Hội thoại, dài hạn theo user/agent (Qdrant + Postgres, điểm recency/importance/relevance), workflow memory, retention | `workflow_runtime/infrastructure/memory_middleware.py` |
| Trả lời | SSE, Recharts 8 loại, DataGrid, evidence (SQL, độ tươi, cảnh báo fan-out), citation có trang, xuất CSV/XLSX | `frontend/src/features/ask/` |
| Nền tảng | k3s, Kong, Keycloak, OpenBao, RLS theo tenant (đã chuyển workload sang `app_user`), outbox Kafka, Langfuse, OTel | `deploy/`, `libs/shared/` |

### 3.2 Lỗi gốc đã kiểm chứng trong mã

Đây là các lỗi làm sản phẩm **không thể** đạt mức cao cấp dù thêm bao nhiêu tính năng. Mình đã đọc
lại mã để xác nhận từng dòng in đậm.

| # | Lỗi | Hệ quả |
| --- | --- | --- |
| B1 | **Kết quả SQL đi chung đường với đoạn văn bản.** `rrf_ranking.py:16` trộn `db_results` với chunk; `reranker.py` xếp lại bằng cross-encoder văn bản, `top_n=5`; `responder.py:257` chỉ đọc `reranked_chunks` | Dòng dữ liệu có thể bị loại vì "kém liên quan về chữ"; canvas không có rrf/reranker thì responder không thấy kết quả SQL. Vi phạm tinh thần bất biến 3 ngay trong runtime |
| B2 | **MCP không bao giờ khớp endpoint.** Planner trả `tool_name` (`prompts/planner_system.txt:22`), `nodes/sandbox/mcp.py:34` so `endpoint_name`; planner cũng không được cho biết MCP server nào tồn tại; giao thức là POST tự chế | Mọi tích hợp ngoài qua "MCP" đều trả "no matching endpoint" |
| B3 | **Phê duyệt người dùng trong agent đơn không bền.** `conversation_runner.py:197` dùng `MemorySaver()` trong process, `_pending_reviews` là dict | Restart pod hoặc chạy 2 replica là mất phiên chờ duyệt |
| B4 | **7 loại node canvas bị bỏ âm thầm.** Frontend có `trigger, output, guardrail, condition, db_query, send_notification, loop`; `graph.py:34 NODE_TYPE_MAP` không có | Người dùng dựng workflow có rẽ nhánh/lặp/thông báo, runtime chạy một graph khác |
| B5 | **`/api/conversations/run` tin trường do client gửi**: `memory_context`, cấu hình guardrail, cờ `orchestrated` | Client tự tiêm memory, tắt guardrail, bỏ ghi memory |
| B6 | Kết quả verifier tính xong **không gửi về UI**; judge chỉ xem `reranked_chunks` | Người dùng không thấy hệ thống đã kiểm gì |
| B7 | `delegate_to_agent` cần `target_agent` nhưng planner không có danh sách agent; không chặn đệ quy | Uỷ quyền giữa agent là đoán |
| B8 | `sandbox_executor` không xác thực HTTP, mật khẩu DB đi trong body; schema crawler đọc mật khẩu từ env | Một pod bị chiếm là đọc được mọi CSDL khách |
| B9 | Lỗi nạp cấu hình agent rơi về `DEV_CONFIG`; planner không parse được thì rơi về `kb_search` | Lỗi bị che thành câu trả lời của một agent khác |
| B10 | Lưu canvas: không validate, xoá-rồi-chèn không nguyên tử, sửa được version đã publish, cache graph theo version id | Sửa agent đang chạy không có hiệu lực hoặc làm hỏng nó |

### 3.3 Khoảng trống so với sản phẩm đích

| Khối | Thiếu |
| --- | --- |
| Kết nối | MySQL, SQL Server, Oracle, BigQuery, Databricks (UI có Databricks, không có executor), Trino; quản lý kết nối chung (hiện Snowflake-only ở UI, các engine khác là trường mật khẩu trên node) |
| Catalog | Không có catalog thống nhất: tài liệu ở `KBData`/Qdrant, bảng ở `SchemaCatalog`, bảng tải lên ở `kb_structured.*`, chuỗi chỉ số chưa có. Không PK/FK, cardinality, giá trị mẫu, chỉ mục giá trị |
| Semantic | Join, metric dẫn xuất/tỷ lệ/luỹ kế, time grain, `compare_to` (đã parse, chưa compile), soạn thảo, import/export chuẩn mở, verified query |
| Agent core | Capability registry thật (indicator-api `/capabilities` = 501), MCP chuẩn, chế độ phân tích sâu, thư viện phân tích tất định (driver, anomaly, forecast), sinh báo cáo |
| Đầu ra bền | Thread chia sẻ được, báo cáo, dashboard, monitor/lịch chạy thật |
| Quản trị | Quyền cột/dòng khi agent truy vấn, PII masking trên kết quả SQL, danh tính agent, giới hạn chi phí truy vấn |
| V2 số liệu | Hợp đồng `libs/contracts` có; `indicator_api` mọi route 501; chưa có `obs` ClickHouse, `series_extractor`, review case, DQ |
| Eval | Đường text-to-SQL thật, retrieval, judge, đa agent, theo Data Space |
| Model | Qwen3-14B-AWQ (main) + Qwen3-4B-AWQ (fast) trên một A100 40 GB dùng chung, thinking tắt |

## 4. Kiến trúc sản phẩm đích

### 4.1 Hình tổng thể

```text
┌───────────────────────────────────────────────────────────────────────────────────┐
│ EXPERIENCE                                                                        │
│ Ask │ Analyst Workspace (Thread→Notebook) │ Reports & Dashboards │ Monitors/Alerts│
│ Data Studio (Catalog, Semantic Model, Glossary) │ Review Inbox │ Context Studio   │
│ Agent Studio │ Slack/Teams/Zalo │ API · MCP server · A2A                          │
└────────────────────────────────────┬──────────────────────────────────────────────┘
                              REST / SSE (AG-UI frames) / MCP
┌────────────────────────────────────▼──────────────────────────────────────────────┐
│ AGENT CORE                                                                        │
│ Supervisor (Analyst Agent): hiểu → resolve (Data Space, entity, metric, time)     │
│   → plan (task graph từ capability đã đăng ký) → act → verify → replan / hỏi lại  │
│ Chuyên gia (sub-agent, sinh theo kế hoạch): Discovery │ Semantic/SQL │ Document   │
│   │ Python Analyst │ Viz & Report │ Critic/Verifier │ Monitor                     │
│ Capability Registry ── MCP Gateway (vào/ra) │ Memory (user, agent, sửa lỗi)       │
│ Chế độ: Quick answer │ Deep analysis │ Scheduled/Proactive                        │
└────────────────────────────────────┬──────────────────────────────────────────────┘
┌────────────────────────────────────▼──────────────────────────────────────────────┐
│ CONTEXT LAYER  (lõi sản phẩm, Q1)                                                 │
│ Unified Catalog: table │ column (profile, mẫu, khoá) │ document │ series │ API    │
│ Semantic Model: entity │ metric │ dimension │ join │ time grain  (chuẩn mở)       │
│ Glossary │ Verified Queries │ Instructions │ Ontology/Knowledge Graph              │
│ Context mining: query log, lineage, dbt/mã pipeline, tài liệu nội bộ               │
│ Correction memory → đề xuất → steward duyệt → tài sản ngữ cảnh                     │
│ Data Space = gói con của tất cả các thứ trên + quyền + bộ eval  (Q4)               │
└────────────────────────────────────┬──────────────────────────────────────────────┘
┌────────────────────────────────────▼──────────────────────────────────────────────┐
│ QUERY ENGINES                                                                     │
│ Semantic Compiler (SQLGlot*, mọi dialect)│ Governed text-to-SQL │ Python sandbox  │
│ Document Retrieval (vector + FTS)   │ Observation Engine (V2: as-of, vintage)     │
│ Graph query (Neo4j)                                                               │
└────────────────────────────────────┬──────────────────────────────────────────────┘
┌────────────────────────────────────▼──────────────────────────────────────────────┐
│ CONNECTIVITY & INGESTION                                                          │
│ Live connections (truy vấn tại chỗ, Q2): Postgres, MySQL, SQL Server, Oracle,     │
│   ClickHouse, Snowflake, BigQuery, Databricks, Trino                              │
│ Ingested sources: tài liệu/web/API (Bronze→Silver→Gold), file bảng, chuỗi chỉ số │
├───────────────────────────────────────────────────────────────────────────────────┤
│ TRUST & GOVERNANCE (xuyên các lớp)                                                │
│ Quyền trước khi sinh (Q6) │ RLS/CLS passthrough │ PII masking │ danh tính agent   │
│ Nhãn tin cậy │ Provenance │ Audit tool call │ Chi phí │ HITL/OCC │ Eval gate      │
└───────────────────────────────────────────────────────────────────────────────────┘
```

\*SQLGlot chưa là dependency của repo (06/10/2026): compiler hiện tại tự dựng SQL cho postgres/clickhouse,
sandbox kiểm bằng `sql_scan` + sqlparse; thêm SQLGlot ở R1-02/R1-06.

So với V2 (00 mục 2), thay đổi là: **Context Layer** thay vị trí "Semantic & Knowledge Fabric" và
mở rộng ra mọi dữ liệu; **Observation Engine** của V2 thành một engine chuyên trong nhiều engine;
**Live connections** là đường chính cho dữ liệu doanh nghiệp; **Trust & Governance** thành mặt cắt
dọc thay vì một tầng chỉ cho số được nạp.

### 4.2 Đường đi của một câu hỏi

```text
Câu hỏi + người hỏi + agent
 → Data Space của agent ∩ quyền của người hỏi  = ngữ cảnh được phép  (Q6)
 → Resolve: metric/entity/kỳ qua semantic model + glossary; không khớp → hỏi lại, không đoán
 → Chọn đường:
     khớp verified query            → chạy, nhãn VERIFIED
     khớp semantic model            → compile, nhãn GOVERNED
     không khớp, có bảng phù hợp    → text-to-SQL + kiểm (schema linking, chạy thử,
                                       tự sửa, so nhiều ứng viên) , nhãn EXPLORATORY
     cần tài liệu                   → retrieval, nhãn DOCUMENT (trích trang)
     không đủ                       → INSUFFICIENT_EVIDENCE + gợi ý
 → Kết quả bảng đi kênh RESULT SET (id, schema, số dòng, thống kê tính tất định),
   không bao giờ qua reranker văn bản  (sửa B1)
 → Verifier: số trong câu trả lời ⊂ số trong result set/evidence; độ tươi; quyền
 → Trả lời theo catalog khối (bảng, chart, ValueCard, evidence, nhãn tin cậy, SQL)
 → Người dùng: 👍 / sửa → correction memory → đề xuất verified query → steward duyệt
```

### 4.3 Việc phải làm trước (R0, nền)

| ID | Việc | Nơi sửa |
| --- | --- | --- |
| R0-1 | Tách **kênh result set**: `db_results`/kết quả Python thành đối tượng có `result_id`, không vào RRF/reranker; responder nhận bảng tóm tắt + thống kê tất định; verifier đối chiếu với result set | `nodes/model_harness/{rrf_ranking,reranker,responder,judge}.py`, `domain/state.py` |
| R0-2 | MCP chuẩn (SDK `mcp`, Streamable HTTP): client trong runtime, planner nhận danh sách tool thật kèm schema; bỏ POST tự chế | `nodes/sandbox/mcp.py`, `prompts/planner_system.txt`, `config_loader.py` |
| R0-3 | Checkpointer Postgres cho agent đơn (như orchestrator), thêm `tenant_id` + RLS cho bảng checkpoint | `runtime_services/conversation_runner.py` |
| R0-4 | Canvas: một nguồn sự thật cho loại node (registry backend sinh danh sách cho frontend); node không chạy được thì không hiện; validate khi lưu; version đã publish bất biến | `application/node_registry.py`, `api/routers/canvas.py`, `Builder.tsx` |
| R0-5 | `/run` không nhận `memory_context`, guardrail, `orchestrated` từ client | `conversation_runner.py`, router |
| R0-6 | Xác thực service assertion cho `sandbox_executor`; mật khẩu CSDL chỉ là `secret_ref` OpenBao (cả schema crawler) | `sandbox_executor/`, `schema_crawler/metadata.py` |
| R0-7 | Lỗi cấu hình agent và planner trả lỗi có cấu trúc, không rơi về agent khác | `config_loader.py`, `planner.py` |
| R0-8 | Frame `__verification` tới UI | `conversation_runner.py`, `features/ask` |
| R0-9 | **Quyết năng lực model** (mục 8, D1) và đo lại bằng `make bench` + eval | `deploy/docker/litellm/config.yaml` |

R0 là điều kiện để mọi đo đạc chất lượng sau đó có nghĩa: hôm nay một câu hỏi SQL đúng vẫn có thể ra
câu trả lời sai vì B1.

## 5. Bất biến: giữ, mở rộng, thêm

| V2 | Quyết định cho sản phẩm |
| --- | --- |
| 1. Chat chỉ là một giao diện | Giữ. Monitor và báo cáo định kỳ là công dân hạng nhất |
| 2. Raw bất biến | Giữ cho dữ liệu nạp vào. Dữ liệu truy vấn tại chỗ không thuộc ta: lưu **câu truy vấn + thời điểm + hash kết quả** để tái lập |
| 3. Structured không đi chunk | Giữ và mở rộng: bảng kho/mart không bao giờ embedding; **chỉ metadata** (mô tả, giá trị phân loại) được index. Áp cả trong runtime (R0-1) |
| 4. Chunk chỉ phục vụ retrieval | Giữ |
| 5. LLM đề xuất, kiểm tra tất định và người quyết | Giữ, áp cho semantic model, glossary, verified query, join |
| 6. LLM không tính số | Giữ: mọi số đến từ SQL/Python đã thực thi hoặc capability tất định; LLM chỉ viết mã và diễn giải |
| 7. Planner không tưởng tượng tool | Giữ. **Làm rõ**: viết SQL/Python bên trong tool sandbox đã đăng ký là hợp lệ, kết quả mang nhãn EXPLORATORY |
| 8. Provenance, bốn trục thời gian | Provenance bắt buộc mọi nơi. Bốn trục chỉ bắt buộc cho Observation Engine; kết quả kho mang thời điểm truy vấn + độ tươi bảng |
| 9. Point-in-time mặc định | Bắt buộc ở Observation Engine; ở kho là "tại thời điểm truy vấn" và phải ghi ra |
| 10. Không last-write-wins | Giữ cho mọi tài sản ngữ cảnh (semantic model, verified query, glossary) |
| 11. Không gọi LLM cho mọi cập nhật | Giữ; monitor lọc bằng luật/thống kê trước |
| 12. Agent không tự sửa production | Giữ; agent mới do máy đề xuất, skill mới, sửa semantic đều qua eval + duyệt |
| **13 (mới)** | **Quyền kiểm trước khi sinh**: ngữ cảnh đưa vào model đã lọc theo quyền người hỏi; truy vấn chạy bằng danh tính người hỏi hoặc principal của agent có RLS/CLS |
| **14 (mới)** | **Mỗi câu trả lời có nhãn tin cậy** (VERIFIED, GOVERNED, EXPLORATORY, DOCUMENT, INSUFFICIENT) và mở được tới SQL/tài liệu gốc |
| **15 (mới)** | **Truy vấn tại chỗ là mặc định**; sao chép dữ liệu khách chỉ khi cần vintage hoặc governance yêu cầu, và phải ghi lý do |

## 6. Agent "tự biến hoá": định nghĩa để làm được

"Tự biến hoá" dễ bị hiểu thành agent tự viết agent khác và tự chạy trên production. Như vậy vi phạm
bất biến 12 và không kiểm soát được. Định nghĩa sản phẩm chốt ở ba tầng:

| Tầng | Ai quyết | Cơ chế | Ví dụ |
| --- | --- | --- | --- |
| **T1. Thích nghi trong một câu hỏi** | Máy, trong giới hạn | Supervisor lập task graph từ capability đã đăng ký; sinh sub-agent chuyên gia với ngữ cảnh hẹp (một Data Space, vài bảng) cho từng nhánh; chạy song song; replan khi thiếu bằng chứng | "So doanh thu và khiếu nại theo vùng, giải thích chênh lệch" → nhánh SQL + nhánh tài liệu + nhánh phân rã driver |
| **T2. Đề xuất agent mới** | Máy đề xuất, người duyệt | Từ mô tả + Data Space đã chọn, hệ thống sinh cấu hình agent (chỉ dẫn, kỹ năng, câu hỏi mẫu, bộ eval khởi đầu), chạy eval, trình duyệt | "Tạo agent cho phòng tín dụng theo dõi nợ xấu" → cấu hình + 20 câu eval + báo cáo điểm |
| **T3. Học từ sử dụng** | Máy đề xuất, steward duyệt | Phản hồi và sửa lỗi → correction memory → đề xuất verified query, synonym, chỉ dẫn, skill → eval gate → duyệt | Analyst sửa "doanh thu thuần" thành cột khác 3 lần → đề xuất sửa metric |

Các thực thể cấu hình:

```text
Agent    = persona/chỉ dẫn + Data Space[] + Skill[] + kênh + lịch + chính sách (model, chi phí, HITL)
Data Space = Asset[] (bảng, tài liệu, series) + Semantic Model + Glossary + Verified Query[]
             + Instruction[] + quyền + Eval Set
Skill    = capability có schema vào/ra: tất định (operator, thư viện phân tích) | truy vấn (semantic,
           SQL, retrieval) | composite (một task graph mẫu, ví dụ EXPLAIN_METRIC_CHANGE)
           Viết theo chuẩn Agent Skills (SKILL.md) cho phần hướng dẫn, khai báo trong Capability Registry
```

Bộ chuyên gia mặc định (đều là cấu hình trên cùng runtime, không phải service riêng):

| Chuyên gia | Năng lực | Nền có sẵn |
| --- | --- | --- |
| Discovery | Tìm bảng/tài liệu/metric phù hợp trong Data Space, giải thích ý nghĩa | `schema_catalog` Qdrant, LightRAG |
| Semantic/SQL | Compile semantic, text-to-SQL có kiểm, so nhiều ứng viên | `handlers/data.py`, `libs/semantic` |
| Document | Tìm, đọc, trích dẫn, đối chiếu tài liệu | `worker_agent/documents` (truy hồi, trả lời có Verifier, claim và xung đột) |
| Python Analyst | Phân rã driver, anomaly, forecast, thống kê, what-if trên result set | `sandbox_executor` (Python), mới: `libs/analytics` |
| Viz & Report | Chọn khối hiển thị, dựng báo cáo nhiều phần | node `visualization`, mới: report builder |
| Critic/Verifier | Kiểm số, độ tươi, quyền, đủ bằng chứng | `libs/indicator/verifier.py`, judge |
| Monitor | Chạy theo lịch/sự kiện, phát hiện, mở điều tra | mới (V2 S11-01 `detector`) |

Hai ràng buộc thiết kế: chuyên gia nhận **ngữ cảnh hẹp** (Databricks báo khám phá bảng tốt hơn tới
40% nhờ tìm kiếm tri thức chuyên biệt); và mỗi vai có thể dùng **model khác nhau** qua alias LiteLLM
đã có.

## 7. Bản đồ thay đổi trên repo: thêm gì, sửa gì, nối gì

### 7.1 Theo khối

| Khối | Giữ | Sửa | Thêm | Nối |
| --- | --- | --- | --- | --- |
| **Connectivity** | Connector tài liệu, crawler, raw bất biến | Mật khẩu → OpenBao mọi engine (R0-6); Warehouses UI thành **Connections** chung | Executor MySQL, SQL Server, Oracle, BigQuery, Databricks, Trino (dialect qua SQLGlot); giới hạn chi phí truy vấn (timeout, max rows/bytes) | Connection → Catalog (crawl) → Data Space |
| **Catalog** | `SchemaCatalog`, `KBData` | Schema crawler thu PK/FK, cardinality, giá trị mẫu, null rate; join hint từ khoá thật trước, LLM sau và phải duyệt | Bảng `catalog.asset` thống nhất (table, column, document, series, api) với `owner`, `certification`, độ tươi; chỉ mục giá trị cột phân loại cho schema linking; lineage từ dbt manifest/query log khi có | Catalog ← schema crawler, KB pipeline, series registry; → Discovery, Data Studio |
| **Semantic** | `libs/semantic` (resolver, ambiguity, slots) | Compiler: join theo quan hệ entity, metric tỷ lệ/dẫn xuất/luỹ kế, time grain, `compare_to`, mọi dialect; bỏ điều kiện "chỉ khi có đúng một kết nối" | API + UI soạn semantic model; import/export YAML chuẩn mở (MetricFlow/OSI); **Verified Query** (câu hỏi, SQL tham số hoá, người duyệt, version, OCC); Glossary | Semantic ← Data Studio, đề xuất từ correction; → Semantic/SQL expert, eval |
| **Observation (V2)** | Hợp đồng `libs/contracts`, raw bất biến, kế hoạch STEP 1–11 | `indicator_api` đặt lại phạm vi: **Observation Engine + Governance** (không ôm capability registry chung) | Như 05 của V2 | Series vào Catalog; capability của nó đăng ký vào registry chung |
| **Agent core** | Orchestrator DPTS, task DAG, replan, memory, verifier | R0-1…R0-8; capability registry thay `TOOL_REGISTRY` tĩnh; supervisor chọn chuyên gia/capability thay vì chỉ chọn agent đã publish | Chế độ **Deep analysis** (kế hoạch hiện cho người dùng, chạy nhiều bước, ra báo cáo); `libs/analytics` (driver decomposition, contribution, anomaly MAD/STL, forecast baseline, cohort); report builder; MCP server ra ngoài | Runtime → Context Layer (resolve), → engines qua capability/MCP; → Artifacts (thread, report) |
| **Memory** | Memory user/agent | Orchestrator ghi memory (đang không ghi) | **Correction memory** gắn Data Space; hàng đề xuất cho steward | Phản hồi UI → correction → Review Inbox → Semantic/Verified Query |
| **Artifacts** | Xuất CSV/XLSX | Lịch sử hỏi khỏi `localStorage` | Thread (chia sẻ, chạy lại), Report (khối có kiểu, xuất PDF/DOCX), Dashboard (ghim result set có lịch làm mới), Monitor (lịch/sự kiện → điều tra → alert) | Scheduler: K8s CronJob gọi runtime; outbox Kafka cho sự kiện |
| **Governance** | RLS tenant, audit truy cập, guardrail | Phân quyền tài liệu theo vai → theo asset/Data Space | Danh tính agent (principal riêng, quyền riêng); CLS/RLS khi agent truy vấn kho (passthrough nếu engine hỗ trợ, nếu không thì viết lại truy vấn với policy); PII masking trên result set; audit mỗi tool call | Policy engine dùng chung cho Catalog, Engines, Agent |
| **Eval** | `model/eval`, gate | Thêm case chạy text-to-SQL thật, so **theo result set** | Eval Set theo Data Space; canary hằng ngày trên production; bảng điểm theo nhãn tin cậy | Eval gate chặn publish agent/skill/semantic |
| **Frontend** | Khung React, Ask, AnswerBlock, ChartRenderer, Builder | FE-00 của V2 (OpenAPI types, TanStack Query, URL state) | Data Studio (Catalog, Semantic Model, Glossary, Verified Query), Analyst Workspace, Reports/Dashboards, Monitors, Context Studio (xem trace, câu hỏi thất bại, đề xuất sửa), Agent Studio (T2) | Mọi màn dùng chung catalog khối và nhãn tin cậy |

### 7.2 Các mối nối quan trọng nhất

1. **Catalog là khoá chung.** Tài liệu, bảng, cột, series, metric đều có `asset_id`. Knowledge graph
   (Neo4j, hoãn sau G3 theo ADR-04; bỏ cùng KB cũ ngày 21/10/2026) sẽ nối entity nghiệp vụ ↔ bảng/cột ↔ tài liệu ↔ series. Đây là chỗ giải bài "cross-modal
   join" của V2 (00 mục 13) cho mọi dữ liệu.
2. **Capability Registry là hợp đồng giữa agent và engine.** Một bảng, đọc bởi planner (chọn), MCP
   Gateway (phơi ra ngoài), UI (hiện "capability đã duyệt"), eval (kiểm planner chỉ dùng cái đã
   đăng ký). Gộp `TOOL_REGISTRY` (`tool_executor/__init__.py`), node registry và `/capabilities` của
   `indicator_api` thành một.
3. **Result set là đối tượng hạng nhất** (R0-1): có id, schema, provenance, hash; được verifier, UI,
   report, dashboard, eval dùng chung.
4. **Vòng học khép kín**: phản hồi → correction → đề xuất → Review Inbox (đã có cơ chế maker/checker,
   OCC của V2) → tài sản ngữ cảnh mới → eval → hiệu lực. Review Inbox của V2 dùng chung cho cả số liệu
   lẫn ngữ cảnh.
5. **Monitor dùng lại luồng chủ động V2** (00 mục 10): `observation.published` cho series; với kho
   là lịch chạy metric → luật/thống kê → significance → chuyên gia điều tra → alert.

## 8. Quyết định cần anh chốt

Phần còn lại mình đã chốt ở vai PO. Bốn điểm dưới đây thay đổi phạm vi, ngân sách hoặc cam kết bên
ngoài, nên cần anh quyết.

| ID | Câu hỏi | Khuyến nghị | Nếu không chốt |
| --- | --- | --- | --- |
| D1 | **Năng lực model.** Main là Qwen3-14B-AWQ, fast là Qwen3-4B, trên một A100 40 GB đã dùng 33 GB | Ngân sách GPU cho một model lớn hơn ở vai planner/SQL/report; giữ model nhỏ cho vai rẻ; chọn bằng `make bench` + eval theo Data Space, không theo bảng xếp hạng | Chất lượng planner, text-to-SQL nhiều bảng và deep analysis bị chặn trần, mọi cải tiến ngữ cảnh lợi ít hơn |
| D2 | **Nghiệm thu dầu/vàng 27/10** còn giữ không? | Giữ như **Data Space tham chiếu đầu tiên** cho Observation Engine, nhưng chỉ khi R0 chen vào được; nếu không, dời và nói trước với hội đồng | Đội đang thiếu ~2,9 tuần-người cho riêng POC (05 mục 7); thêm R0 và Context Layer mà không cắt là trễ cả hai |
| D3 | **Khách hàng mục tiêu đầu tiên**: dùng nội bộ, hay bán cho doanh nghiệp (ngân hàng, nhà nước, sản xuất)? | Chọn một khách thiết kế (design partner) có kho dữ liệu thật trong R1, để Data Space và eval đầu tiên là dữ liệu thật | Semantic layer và eval xây trên dữ liệu giả, sai trọng tâm |
| D4 | **Chuẩn semantic model**: tự định nghĩa, hay theo chuẩn mở (MetricFlow/OSI) | Mô hình nội bộ của ta, import/export YAML chuẩn mở; không nhúng engine ngoài | Khó nhận semantic từ dbt/Snowflake của khách |

Đã chốt 06/10/2026 theo khuyến nghị của [02](02-market-solution.md) §6: **D8** hoãn gói Publisher; **D9**
Workspace (cây artifact) thay Thread/Notebook/Report; **D10** Zalo/Teams/email cho C-level trước, mobile
sau; **D11** thử giá theo kết quả cho ingestion ở pilot; **D12** phơi Data Space qua MCP cho coding agent
từ R2. **D2** chốt cùng ngày: dời nghiệm thu 27/10, cả đội vào R0 rồi R1 ([04-backlog.md](04-backlog.md) §0).
Còn chờ: D1, D3–D7.

## 9. Lộ trình phát hành

Kích cỡ tương đối (S < 1 tuần-người, M 1–3, L > 3). Ngày cụ thể để anh xếp theo D2 và người.

| Đợt | Mục tiêu người dùng thấy | Nội dung | Cỡ |
| --- | --- | --- | --- |
| **R0 Nền** | Câu trả lời SQL đúng không bị cắt; MCP, duyệt, canvas chạy đúng như UI nói | R0-1…R0-9 (mục 4.3) | M |
| **R1 Hỏi đúng trên kho** | Analyst kết nối kho, chọn bảng thành Data Space, hỏi và nhận câu trả lời có nhãn tin cậy, bấm xem SQL | Connections đa engine; schema crawler đủ khoá/mẫu; `catalog.asset`; semantic có join + soạn thảo; Verified Query; nhãn tin cậy; quyền trước khi sinh; Eval Set theo Data Space; Thread lưu server | L |
| **R2 Phân tích như analyst** | Hỏi "vì sao" ra phân rã driver + tài liệu liên quan; chế độ deep analysis ra báo cáo; ghim lên dashboard | Capability Registry + supervisor chọn chuyên gia; `libs/analytics`; Deep analysis; Report builder; Dashboard; hybrid SQL + tài liệu qua catalog/graph | L |
| **R3 Tự chạy và tự học** | Monitor gửi alert có điều tra; báo cáo định kỳ tự gửi Slack/Teams/email; steward duyệt đề xuất sửa | Scheduler; Monitor (dùng lõi detector V2); correction memory + Context Studio; T3 học từ sử dụng; Observation Engine nếu D2 giữ | L |
| **R4 Nền tảng** | Phòng ban tự tạo agent từ mô tả; hệ thống ngoài gọi qua MCP/A2A | Agent Studio T2 (đề xuất + eval + duyệt); MCP server; A2A; danh tính agent; PII masking kết quả; giới hạn chi phí | M–L |

Tiêu chí ra mỗi đợt nằm ở mục 10. Không mở đợt sau khi đợt trước chưa đạt eval của nó. Lộ trình hợp
nhất với bản v1.0 (W1–W6) ở [02](02-market-solution.md) §5; thiết kế kỹ thuật R0 ở
[03-r0-technical-design.md](03-r0-technical-design.md); backlog có ID và cột tiến độ ở
[04-backlog.md](04-backlog.md).

## 10. Chỉ số sản phẩm

| Nhóm | Chỉ số | Mục tiêu khởi đầu (chỉnh sau baseline) |
| --- | --- | --- |
| Đúng | Độ chính xác theo result set trên Eval Set mỗi Data Space, tách theo nhãn tin cậy | VERIFIED/GOVERNED ≥ 0,95; EXPLORATORY báo cáo, tăng dần |
| Đúng | `unsupported_number_rate` (số trong câu trả lời không có trong result set/evidence) | 0 |
| Trung thực | Từ chối/hỏi lại đúng lúc | ≥ 0,9 |
| Phủ | Tỷ lệ câu hỏi đi đường VERIFIED + GOVERNED | Tăng theo tuần (đo hiệu quả vòng học) |
| Học | Đề xuất từ correction được duyệt / tổng đề xuất | Báo cáo; thấp nghĩa là đề xuất nhiễu |
| An toàn | Truy cập vượt quyền qua agent (test isolation + red team) | 0 |
| Tốc độ | Token đầu tiên câu nhanh; deep analysis hoàn tất | p95 ≤ 3 giây; p95 ≤ 3 phút |
| Chủ động | Alert có hành động / tổng alert | Báo cáo; > 1 alert/sự kiện gốc là lỗi |
| Dùng | Người dùng hoạt động tuần, câu hỏi lặp lại thành verified query | Báo cáo |

## 11. Nguồn khảo sát

Đọc ngày 06/10/2026. Số của nhà cung cấp là tự công bố, không kiểm độc lập.

- Snowflake: Cortex Agents GA <https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-agents>;
  Summit 2026 (CoWork, Semantic View Autopilot, Horizon Context, AI Agent Identity, OSI)
  <https://atlan.com/know/snowflake/summit-2026-announcements/>; Cortex Sense 24→86%
  <https://www.snowflake.com/en/blog/enterprise-ai-agents-grounded-context/>; Verified Query Repository
  <https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-analyst-evaluations>
- Databricks: Genie One / Ontology / Agents
  <https://www.databricks.com/blog/introducing-genie-one-genie-ontology-and-genie-agents>; 32%→90%,
  parallel thinking, đa model <https://www.databricks.com/blog/pushing-frontier-data-agents-genie>; deep
  analysis + file reasoning
  <https://www.databricks.com/blog/expanding-genie-agents-deep-analysis-file-reasoning-and-more>;
  Supervisor Agent GA <https://www.databricks.com/blog/agent-bricks-supervisor-agent-now-ga-orchestrate-enterprise-agents>
- Google: Conversational Analytics Q3 2026
  <https://cloud.google.com/blog/products/data-analytics/conversational-analytics-in-google-data-cloud-in-q326>;
  Looker Next '26 <https://cloud.google.com/blog/products/business-intelligence/looker-updates-for-agentic-bi-at-next26>
- Microsoft Fabric: example queries
  <https://learn.microsoft.com/en-us/fabric/data-science/data-agent-example-queries>; lưu ý RLS
  <https://learn.microsoft.com/en-us/answers/questions/5556598/fabric-data-agent-not-respecting-row-level-securit>
- Hex Threads / Context Studio <https://hex.tech/blog/introducing-threads/>,
  <https://learn.hex.tech/changelog/2026-01-28>; ThoughtSpot Spotter 3
  <https://www.thoughtspot.com/blog/spotter-for-unstructured-data>; AWS Quick multi-dataset topics
  <https://aws.amazon.com/blogs/machine-learning/build-a-unified-semantic-layer-across-datasets-with-multi-dataset-topics-in-amazon-quick/>;
  Palantir <https://blog.palantir.com/connecting-agents-to-decisions-277dee8ddb40>
- Mã mở: Wren AI <https://www.getwren.ai/post/genbi-for-ai-agents-open-context-layer>; dbt MetricFlow
  <https://www.getdbt.com/blog/open-source-metricflow-governed-metrics>; Cube D3
  <https://cube.dev/blog/announcing-cube-d3>
- Semantic layer và text-to-SQL (dbt, 04/2026) <https://docs.getdbt.com/blog/semantic-layer-vs-text-to-sql-2026>
- OpenAI in-house data agent (bài gốc không đọc được, 403; tóm tắt thứ cấp)
  <https://openai.com/index/inside-our-in-house-data-agent/>,
  <https://vanducng.dev/2026/02/06/Data-Agents-From-OpenAI-to-Open-Source/>
- Gartner Market Guide Agentic Analytics 2026 (qua bên thứ ba)
  <https://www.unsupervised.com/resources/gartner-2026-market-guide-agentic-analytics>
- Benchmark: nhãn sai BIRD/Spider (CIDR 2026) <https://www.cidrdb.org/cidr2026/papers/p5-jin.pdf>;
  Spider 2.0 <https://spider2-sql.github.io/>; BIRD <https://bird-bench.github.io/>; BIRD-Interact
  <https://bird-interact.github.io/>; KramaBench <https://arxiv.org/html/2506.06541v3>; DABstep
  <https://huggingface.co/blog/nvidia/nemo-agent-toolkit-data-explorer-dabstep-1st-place>
- Tài liệu có cấu trúc rồi mới truy vấn (AnnoIndex) <https://arxiv.org/abs/2608.13384>
