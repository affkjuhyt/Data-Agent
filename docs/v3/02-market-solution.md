# 02. Phản biện và giải pháp bán ra thị trường

> Vai trò viết: BA/PO. Ngày 06/10/2026. Tiếp nối [00](00-product-strategy.md) và [01](01-agent-core-and-ui.md).
> Đầu vào vòng này: bản "Thiết kế sản phẩm và giải pháp kỹ thuật v1.0" (06/10/2026, viết độc lập, chưa
> kiểm mã), 8 ảnh (McKinsey, ai-market, Looker Conversational Analytics, Elton DataUtils ×3, Fabric ×2),
> và rà 6 nguồn: Agno Dash, file.ai, dtquocbao/ai-market, Google Data Agent Kit, Looker Studio, Elton
> DataUtils. Nguồn ở mục 8.
>
> Câu trả lời của tài liệu: bán **cái gì**, cho **ai**, **khác gì** các thứ đã có, và **làm gì trước** để
> có doanh thu. Phần kiến trúc không viết lại; chỉ ghi phần phải sửa trong 00/01.

## 0. Kết luận

1. **Thứ bán được không phải "chat với dữ liệu".** Looker Conversational Analytics, Fabric Data Agent,
   Databricks Genie đã GA, giá cỡ 10 USD/người/tháng, nằm sẵn trong nền tảng khách đang trả tiền. Khách
   có BigQuery hay Fabric sẽ dùng của hãng. Chat BI là mặt bằng tối thiểu, không phải giá trị.
2. **Thứ bán được là "Trusted Analyst Workspace" cho doanh nghiệp có dữ liệu phân tán và không được
   đưa dữ liệu ra ngoài:** ERP/POS/CSDL nghiệp vụ + Excel + Word/PDF tiếng Việt → metric được chứng
   nhận → câu trả lời có bằng chứng → theo dõi tự động, chạy **trong hạ tầng khách**. Khách mục tiêu:
   ngân hàng, bảo hiểm, cơ quan nhà nước, sản xuất, bán lẻ chuỗi tầm trung trở lên.
3. **Không cạnh tranh với Elton DataUtils ở SME thương mại điện tử trên BigQuery.** Họ đã có connector
   Shopee/TikTok Shop/KiotViet/Pancake, mobile cho chủ shop, BYO-LLM, giá theo pipeline. Học bố cục
   workspace của họ (mục 2.3), không đi vào phân khúc đó.
4. **Bản v1.0 đúng hướng và bổ sung được nhiều thứ** (AnalysisSpec, EvidenceFrame, bảng lỗi, bảng
   chính sách HITL, SLO theo route, case QC). Nhưng nó mở ba phân khúc cùng lúc (nội bộ, analyst,
   publisher), chưa kiểm mã nên thiếu R0, và thiếu ba thứ nghiên cứu cho thấy là quyết định: output
   contract, cổng confidence, thang tự chủ. Mục 1.
5. **Gói Publisher (data product, entitlement, API bán dữ liệu) hoãn** cho tới khi có khách thiết kế là
   nhà cung cấp dữ liệu. Làm sớm là mở phân khúc thứ ba khi hai phân khúc đầu chưa có hành trình nào
   hoàn chỉnh; chính v1.0 cũng cảnh báo điều này ở mục rủi ro của nó.
6. **Pilot: một khách thiết kế, domain bán hàng – biên lợi nhuận gộp – công nợ** (đồng ý với v1.0),
   15–25 metric, 100–150 câu nghiệm thu, hai hành trình demo. Dầu/vàng giữ làm Data Space tham chiếu và
   regression cho chuỗi thời gian.
7. **Không bán "15 agent chuyên biệt".** Ảnh ai-market là ví dụ phản diện: mọi tín hiệu BUY, confidence
   50%, cột "Agents: 0 agents". Thứ bán được là kết quả kiểm chứng được; confidence trên UI phải đến từ
   đồng thuận thực thi (01 §3.4), không phải trang trí.

## 1. Phản biện bản thiết kế v1.0

### 1.1 Đồng thuận với 00/01 (không cần bàn lại)

Asset + metric contract + artifact là lõi, agent là cách thực thi (ADR01 ≈ 00 Q1, Q4) · truy vấn tại
nguồn, chép theo nhu cầu (ADR02 ≈ Q2) · raw bất biến, sửa qua overlay (ADR03 ≈ bất biến 2) · AnalysisSpec
+ tool tất định là đường chính, ad-hoc SQL có phạm vi riêng (ADR04 ≈ Q3 hai đường) · đường ngắn cho KPI,
graph cho điều tra (ADR05 ≈ 01 §3.8) · agent động ghép capability đã cấp, promotion offline (ADR06 ≈ Q5,
01 §3.7) · evidence kiểm ý nghĩa, LLM judge không thay code (ADR07 ≈ bất biến 6, 14) · API-first, MCP
là adapter (ADR09) · SLO theo route, model/HA chốt sau benchmark (ADR10 ≈ D1).

### 1.2 Lấy vào 00/01

| Của v1.0 | Lấy vào đâu | Vì sao |
| --- | --- | --- |
| **AnalysisSpec** (intent, metrics@version, dimensions, period, compare_to, filters, outputs; server gắn identity/policy/budget) | 01 §3.3 Plan IR: AnalysisSpec là input của node `SQL_QUERY` đường GOVERNED | Cụ thể hơn QuerySpec của V2, có version metric |
| **EvidenceFrame** = tuple (claim, metric_version, dimensions, period, unit, result_ref, source_ref, query_hash, watermark, coverage, policy_version) | 01 §3.1 bước 5 Verify; thay "số trong câu trả lời ⊂ số trong evidence" | "Tìm một chuỗi chữ số giống nhau là không đủ" — kiểm theo ý nghĩa |
| **Bảng lỗi**: NEEDS_CLARIFICATION, NO_DATA, FORBIDDEN, STALE_DATA, SCHEMA_CHANGED, QUALITY_BLOCKED, REVIEW_PENDING, UNSUPPORTED, BUDGET_EXCEEDED, TOOL_UNAVAILABLE | Gộp với lớp lỗi 01 §3.5 thành một bảng; RefusalCard một thẻ/mã | FE hiện bước tiếp theo đúng; không gộp "không quyền" với "không dữ liệu" |
| **Bảng chính sách HITL theo tình huống** (nguồn mới → owner duyệt theo lô; cập nhật lặp → auto-publish khi hard check đạt; conflict → proposal có diff; write → preview + revalidate) | 01 §2.2 | Cụ thể hoá "dừng ở đâu" cho pipeline dữ liệu, không chỉ cho agent |
| **Top-k không đủ cho câu "bao nhiêu hợp đồng có điều khoản X"**: phải extract toàn corpus, materialize, kiểm coverage, aggregate bằng engine | 01 §3.3 operator `sem_extract` + `VALIDATE(coverage)` | Đúng với AnnoIndex và FDABench |
| **Cache key** gồm authorization fingerprint, contract version, watermark; invalidation theo dependency, không chỉ TTL | 00 §7.1 Governance | Quyền thu hồi phải hiệu lực ngay |
| **Connector phía khách** (outbound TLS/mTLS, job đã ký, credential trong vùng khách, "không phải remote shell") | 00 §7.1 Connectivity | Cần cho khách sau firewall |
| **SLO theo route** (KPI cache ≤ 1,2 s; metric mới ≤ 3 s TTFT; điều tra ≤ 30 s; batch không gán SLA chat) | 00 §10 | Thay p95 chung |
| **Case QC bắt buộc** (join 1-n nhân đôi doanh thu, trung bình tỷ lệ, cộng stock, follow-up giữ nhầm filter, quyền vừa thu hồi còn cache, cross-tenant qua metadata search/MCP scope, crash sau khi gửi action) | 01 §6 | Khớp danh sách lỗi của benchmark |
| **Onboarding bắt đầu từ 10–20 câu khách thật dùng**, đầu ra là workspace nghiệm thu, không phải "agent được tạo" | 02 §4.3 | |
| **Admission control**: CCU đăng nhập ≠ số request LLM đồng thời; fair queue giữa tenant | 00 §7.1 | Một GPU là điểm lỗi chung |
| **Pilot domain**: bán hàng, biên lợi nhuận gộp, công nợ | 02 §4 | Chứng minh được cả cho lãnh đạo lẫn analyst |
| "Không tính FE/QC/Ops là việc miễn phí do AI tự làm" | 02 §5 | |

### 1.3 Phản biện

| Điểm của v1.0 | Phản biện | Quyết định |
| --- | --- | --- |
| Ba phân khúc cùng lúc: nội bộ, analyst, **publisher** (data product, entitlement, API, white-label) | Chính v1.0 ghi rủi ro "không có hành trình khách hàng nào hoàn chỉnh". Publisher cần một loại khách khác, một hành trình demo khác, và pháp lý license dữ liệu | Hoãn Publisher (P2); chỉ giữ hai object `DataProduct`/`Entitlement` trong mô hình để sau không phải đập |
| "Chưa kiểm chứng lại bằng source code" | Mình đã kiểm: 10 lỗi gốc (00 §3.2). W1 Foundation của v1.0 không có việc nào trong số đó; W4 Analysis sẽ xây trên runtime đang trộn SQL với chunk văn bản | W1 := R0 của 00 trước |
| Thiếu **output contract** | 44% thất bại của model tốt nhất trên DataSpace là ở bước trả kết quả (01 §0.5) | Bất biến 16 giữ |
| Thiếu **cổng confidence** / N-version | DeepEye-SQL: confidence ~ đúng/sai r = 0,95; là cơ chế "biết mình không biết" duy nhất đo được | 01 §3.4 giữ |
| Thiếu **thang tự chủ** | Không có thang thì "tự động" là lời hứa mơ hồ; survey nêu rủi ro kỳ vọng sai và trách nhiệm pháp lý | 01 §2 giữ; nhãn mức tự chủ trên agent |
| "Swarm" worker trên blackboard | Tên gợi hình nhưng không thêm cơ chế gì ngoài executor DAG đã có; benchmark cho thấy đa agent đắt 2–3× và chỉ đáng trên câu khó | Dùng từ "chuyên gia theo DAG"; chế độ theo độ khó (01 §3.8) |
| "Durable job queue trên Postgres đủ cho pilot nếu Kafka chưa ổn" | Repo đã có Kafka + outbox chạy ổn (CLAUDE.md §7, RUNBOOK §10b) | Giữ Kafka + outbox |
| "Không tự xây semantic compiler mọi dialect; thêm adapter Cube" | Đồng ý một nửa: compiler của ta chỉ cần metric/dimension/filter/period/compare_to trên SQLGlot (đã có), import/export YAML chuẩn mở; adapter Cube/Looker khi khách đã có | Khớp D4 |
| "Domain pack" thay "tạo agent theo dataset" | Cùng ý với Data Space (00 Q4); giữ một tên | **Data Space**; "domain pack" = Data Space đóng gói sẵn để bán |
| 13–18 tuần với FE/QC/Ops; "hai kỹ sư core" | Đội thật hiện 7–9 người (V2 05 §1) nhưng đã thiếu 2,9 tuần-người cho riêng POC; v1.0 chưa cộng R0 và Context Layer | Lộ trình hợp nhất ở mục 5, không cam kết ngày trước discovery |
| Mobile cho chủ doanh nghiệp (không nêu, nhưng Elton làm) | Kênh của C-level là Zalo/Teams/email trước; app sau | D10 |

## 2. Học gì từ từng nguồn

### 2.1 Bảng tổng

| Nguồn | Là gì | Lấy | Không lấy |
| --- | --- | --- | --- |
| McKinsey "agentic orchestration layer" | Sơ đồ tổng quát: enterprise context → orchestration → agents → toolbox → hệ thống | Xác nhận "enterprise context" là ô riêng (= Context Layer) | Không có cơ chế nào để thiết kế |
| **ai-market** (dtquocbao) | Repo 2 commit cùng ngày 28/11/2025, không test, không license; "agent" là module sklearn/GARCH; số liệu (68% win rate, Sharpe 1,85) không phương pháp | Bài học phản diện: không bán số agent; confidence không được trang trí | Mọi thứ còn lại |
| **Looker Conversational Analytics** (ảnh) | Chat BI GA: agent nói một câu kế hoạch, chart/table toggle, nút Data, Debug info, bật Code Interpreter, disclaimer "may not be complete or accurate" | Đây là **mặt bằng tối thiểu** của trải nghiệm hỏi đáp; mọi thứ của ta phải ít nhất bằng | Không khác biệt được ở đây |
| **Fabric Data Agent vs Operations Agent** | Hãng tách hai sản phẩm: hỏi theo yêu cầu vs Observe→Analyze→Decide→Act có duyệt qua Teams. Giới hạn Fabric Data Agent: SELECT only, không tài liệu phi cấu trúc, chỉ tiếng Anh, ≤ 5 nguồn, ≤ 100 example query | Xác nhận 01 §2.3 (Monitor L4 giới hạn + duyệt); **ba giới hạn của Fabric là ba điểm bán của ta**: tài liệu tiếng Việt, nhiều nguồn, on-prem | |
| BIVIET infographic | "Dữ liệu tốt + ngữ cảnh tốt → câu trả lời tốt"; 5 mảnh: semantic model, business terms, ontology, instructions, DQ & governance | Checklist Context Layer khớp 00 Q1 | |
| **Elton DataUtils** (3 ảnh + rà) | Sản phẩm Việt, warehouse-first trên BigQuery; workspace = cây artifact; agent viết note; SQL view deploy có trạng thái; chế độ Lập kế hoạch/Tự động; skills + KB + metric hub; Metric Alerts; BYO-LLM; mobile | Mục 2.3 | Phân khúc SME e-commerce; phụ thuộc BigQuery |
| **Agno Dash** | Template mã mở (Apache-2.0, 2,3k sao, dừng từ 07/2026): 3 vai Leader/Analyst/Engineer; **6 lớp ngữ cảnh** (table usage, human annotations, query patterns, institutional docs, learnings, runtime schema); learnings tách khỏi curated, promotion thủ công; read-only ở mức DB | Cách tách **curated vs learnings** và 6 lớp làm sơ đồ nạp cho Context Layer | Postgres-only, không cổng duyệt, không verifier |
| **Google Data Agent Kit** (GA 30/09/2026) | Bộ MCP + skill cho **coding agent** (Claude Code, Codex, Gemini CLI) trên Google Data Cloud; không phải agent cho người dùng cuối | Hai ý: (a) phơi Data Space qua MCP cho coding agent của analyst là một bề mặt bán rẻ; (b) **schema ngữ cảnh của Conversational Analytics** (verified query có `@param`, glossary, synonym/aggregation theo cột, relationship, UDF) là checklist cho semantic model | |
| **file.ai** | Nền tảng xử lý tài liệu/file doanh nghiệp (OCR 200 ngôn ngữ, schema, validation, forensics), 14M USD Series A; **giá theo kết quả**: tính tiền khi Agent Flow đạt outcome, chạy hỏng không tính | Không cùng loại sản phẩm. Lấy ý **giá theo kết quả** cho phần ingestion | |

### 2.2 Đường ranh thị trường

```text
            Có semantic layer / warehouse hãng          Không có; dữ liệu phân tán
            (BigQuery, Fabric, Snowflake, Databricks)   (ERP, POS, Excel, Word/PDF, CSDL cũ)
          ┌──────────────────────────────────────────┬──────────────────────────────────────┐
Cloud OK  │ Hãng tự làm: Looker CA, Fabric, Genie,   │ Elton DataUtils (SME, e-commerce,    │
          │ Cortex. Ta chỉ bán adapter + governance   │ warehouse-first BigQuery)            │
          │ nếu khách cần tài liệu tiếng Việt         │                                      │
          ├──────────────────────────────────────────┼──────────────────────────────────────┤
Cấm egress│ Hiếm (Snowflake/Fabric không on-prem)     │ ★ CHỖ CỦA TA: ngân hàng, bảo hiểm,   │
/ on-prem │                                          │ nhà nước, sản xuất, bán lẻ chuỗi     │
          └──────────────────────────────────────────┴──────────────────────────────────────┘
```

### 2.3 Học từ Elton DataUtils (đối thủ gần nhất)

Ba ảnh cho thấy một mô hình giao diện đáng lấy:

| Thấy trong ảnh | Ý nghĩa | Lấy vào 01 |
| --- | --- | --- |
| Cây trái: `files / notes / plan / dashboards / sql{deploy, checks, explore}`; mỗi mục có số đếm; chấm trạng thái "đã triển khai / đã đổi sau khi triển khai / chưa triển khai" | **Workspace là một cây artifact có version và trạng thái deploy**, không phải một thread chat | Gộp Thread/Notebook/Report của 00 thành **Workspace** (D9): mọi thứ agent tạo là file có version trong cây; SQL view là tài sản deploy được |
| Note `mrm_record`: `STAGE S5 · TARGET · BASIS · GRAIN · MUTATION · RULES · IDENTITIES (COUNT DISTINCT → 100% match) · RESIDUAL 0 across 75 months · OPEN · ASSUMPTIONS`, "phiên bản 1 · agent · 8 phút trước" | Agent **tự viết bản ghi đối chiếu có cấu trúc** sau khi dựng view; đây chính là EvidenceFrame/reconciliation record ở dạng người đọc được | Chuẩn hoá "Reconciliation Note" là một khối trong catalog (01 §5.5); agent bắt buộc sinh khi tạo view/metric; steward duyệt note là duyệt metric |
| Panel phải: `PLAN 1/3`, mỗi bước có tool call (`search_skills`, `search_kb`, `metric_search`, `list_artifacts`, `read_artifact`, `execute_sql`, `draw_chart`, `finalize`), "43/50 công cụ khả dụng", lỗi scatter rồi tự sửa hiện ra | Minh bạch quy trình cho analyst; thứ tự tool đúng: tìm skill → tìm KB → tìm metric → đọc artifact → chạy | Khớp 01 §3.1 bước 0 (ground trước). Nhưng với C-level phải gấp lại; "I will list..." tiếng Anh trong UI tiếng Việt là lỗi |
| Toggle **Lập kế hoạch / Tự động**; nút **Triển khai** | Người dùng chọn mức tự chủ theo phiên; deploy là hành động có chủ ý | Khớp 01 §2 (mức theo vai) — thêm toggle theo phiên cho analyst |
| Dashboard dựng từ view đã deploy (xu hướng doanh thu thuần, top 10, tỷ lệ huỷ) | Dashboard = tập result set có lịch, nguồn là view có version | Khớp 00 Q8 |
| Bộ tính ở nhà: Mac Studio + Mac mini | Họ chạy model local/BYO-LLM; chi phí thấp | Xác nhận BYO-LLM (LiteLLM alias đã có) là tính năng bán, không phải chi tiết kỹ thuật |

Thiếu ở Elton (theo ảnh, chưa kiểm sản phẩm thật): nhãn tin cậy, verifier hiện ra, review/HITL, quyền
theo asset, tài liệu phi cấu trúc trong câu trả lời. Đó là phần ta hơn.

### 2.4 Học từ Agno Dash: nạp ngữ cảnh

| Lớp Dash | Tương ứng Context Layer (00 §4.1) | Ai ghi |
| --- | --- | --- |
| 1 Table usage (schema, quan hệ, caveat) | Catalog: asset, cột, PK/FK, profile | Schema crawler; steward sửa |
| 2 Human annotations (metric, luật, ngôn ngữ) | Semantic model + Glossary + Instructions | Steward |
| 3 Query patterns (SQL đã kiểm) | Verified Query | Analyst đề xuất, steward duyệt |
| 4 Institutional knowledge (docs qua MCP) | KB tài liệu (LightRAG) | Pipeline tài liệu |
| 5 Learnings (lỗi, kiểu, sửa) | **Correction memory** — tách riêng, không trộn vào curated | Agent ghi; promotion qua Review Inbox |
| 6 Runtime schema | Introspection lúc chạy | Runtime |

Điểm Dash làm đúng mà v1.0 và 00 chưa nói rõ: **learnings không bao giờ được nạp lẫn vào curated**;
promotion là thủ công. Giữ nguyên tắc này.

## 3. Phản biện các ý tưởng đang lưu hành

| Ý tưởng | Vấn đề | Thay bằng |
| --- | --- | --- |
| "Nhiều agent chuyên biệt" là giá trị | ai-market: 15 agent, mọi tín hiệu BUY 50%. Benchmark: đa agent đắt 2–3×, chỉ đáng trên câu khó | Bán **kết quả kiểm chứng được**; số chuyên gia là chi tiết triển khai |
| Dự báo giá thị trường là tính năng chính | Oil/gold 1–30 ngày gần bước ngẫu nhiên (V2 05 §8.1); ai-market không có backtest | Forecast nhãn "experimental", baseline bắt buộc, không bán riêng |
| Chat BI là sản phẩm | Hãng có sẵn, rẻ, trong nền tảng khách | Workspace + governance + tài liệu tiếng Việt + on-prem |
| Coding agent + MCP (Data Agent Kit) thay data agent | Đó là harness cho developer; Gartner: 60% dự án chỉ dựa MCP thất bại vì thiếu semantic layer | Ta **phơi** Data Space qua MCP cho analyst dùng Claude Code/Cursor, nhưng semantic layer và quyền vẫn ở ta |
| Mobile cho chủ doanh nghiệp | Elton đã có; chi phí FE lớn | Bản tin/alert qua Zalo/Teams/email trước (D10) |
| Publisher/data product ngay từ đầu | Phân khúc thứ ba, hành trình khác, license dữ liệu | Hoãn (D8) |

## 4. Giải pháp bán ra thị trường

### 4.1 Định vị

**Data Agent Workspace**: nền tảng phân tích dữ liệu có agent, chạy trong hạ tầng của doanh nghiệp,
biến dữ liệu phân tán (CSDL nghiệp vụ, kho, Excel, tài liệu Word/PDF tiếng Việt) thành chỉ số được
chứng nhận, câu trả lời có bằng chứng và theo dõi tự động; mọi câu trả lời cho biết nó tin cậy đến mức
nào và dựa vào đâu.

Ba câu khác biệt dùng khi chào hàng, mỗi câu có bằng chứng:

1. **Dữ liệu không rời doanh nghiệp.** k3s on-prem, BYO-LLM (Qwen local hoặc API theo policy). Fabric,
   Snowflake, Looker không có lựa chọn này.
2. **Tài liệu tiếng Việt là dữ liệu.** Thông cáo, hợp đồng, báo cáo Word/PDF đi vào câu trả lời cùng
   con số, có trang. Fabric Data Agent: không tài liệu, chỉ tiếng Anh.
3. **Mỗi con số có hợp đồng.** Metric có version, grain, join, test; câu trả lời có nhãn VERIFIED /
   GOVERNED / EXPLORATORY / DOCUMENT / INSUFFICIENT và bằng chứng mở được. Elton và chat BI không có.

### 4.2 Gói

| Gói | Cho ai | Có gì | Mô hình giá |
| --- | --- | --- | --- |
| **Business** | Chủ doanh nghiệp, quản lý | Business Overview (KPI, alert inbox, bản tin), Ask, Monitor, kênh Zalo/Teams/email | Nền tảng/tháng + số người dùng |
| **Analyst Workspace** | Data analyst, data team | Workspace cây artifact, plan canvas, inspector, SQL/semantic, notebook, report, Data Studio, Review Inbox, Context Studio, MCP cho coding agent | Theo seat analyst + consumption (LLM/token, truy vấn) có quota |
| **Enterprise deployment** | IT, compliance | On-prem/dedicated, SSO, RLS/CLS, audit, HA, backup, SLA | Theo hợp đồng triển khai |
| **Onboarding / Data Space setup** | Một lần | 10–20 câu → nguồn → metric contract → eval → nghiệm thu | Phí theo Data Space; cân nhắc **giá theo kết quả** cho phần nạp dữ liệu (file.ai): tính khi asset được publish đạt hard check |

Publisher là gói thứ tư, chỉ mở khi có khách thiết kế phù hợp (D8).

### 4.3 Pilot bán được

| Mục | Quyết định |
| --- | --- |
| Khách | Một khách thiết kế (D3), có kho hoặc CSDL thật, có owner metric |
| Domain | Bán hàng, biên lợi nhuận gộp, công nợ (v1.0 §1.2); dầu/vàng là Data Space tham chiếu |
| Phạm vi dữ liệu | Một DB/warehouse, XLSX/CSV, DOCX/PDF, một nguồn web theo lịch; hai tenant đối chứng |
| Ngữ cảnh | 15–25 metric duyệt; glossary; ≥ 30 verified query; Eval Set 100–150 câu, hai người giải mù (01 §6) |
| Hành trình demo 1 (business) | "Biên lợi nhuận gộp tháng trước giảm ở nhóm nào?" → metric + kỳ → doanh thu/giá vốn cùng grain → phân rã nhóm/chi nhánh → tài liệu thay đổi giá vốn có hiệu lực → driver + giả thuyết, nhãn riêng → lưu artifact → giao analyst |
| Hành trình demo 2 (analyst) | Ba XLSX + một DOCX → infer schema, so version → mapping/mâu thuẫn → steward duyệt overlay → view deploy → Reconciliation Note (kiểu `mrm_record`) → dashboard → monitor |
| Tiêu chí | 01 §6 + gate của v1.0 §12.1: 100% reconciliation trên metric certified; ≥ 95% task success trên holdout; 0 truy cập vượt quyền; 0 số không có EvidenceFrame |
| Đo giá trị | Thời gian hỏi → kết quả chấp nhận; số lần analyst sửa SQL/metric; tỷ lệ alert có ích; giờ tiết kiệm trên câu lặp (đo baseline trước, v1.0 §14.2) |

### 4.4 Bổ sung vào kiến trúc 00/01 từ vòng này

1. **Workspace thay Thread/Notebook/Report** (D9): một cây artifact có version, trạng thái deploy, quyền;
   chat là một panel của workspace. Mọi thứ agent tạo (SQL view, note, chart, report, plan) là file.
2. **Reconciliation Note** là khối chuẩn, agent sinh khi dựng view/metric; duyệt note = duyệt metric.
3. **AnalysisSpec** là input chuẩn của đường GOVERNED; **EvidenceFrame** là đơn vị kiểm của verifier.
4. **Bảng lỗi hợp nhất** (v1.0 + lớp lỗi 01 §3.5), một RefusalCard/mã.
5. **Context Layer nạp theo 6 lớp của Dash**, learnings tách curated, promotion qua Review Inbox.
6. **MCP server phơi Data Space** cho coding agent của analyst (Claude Code/Cursor), quyền và semantic
   vẫn ở ta; đây là bề mặt rẻ để analyst dùng ngay mà không cần UI đầy đủ.
7. **Toggle mức tự chủ theo phiên** (Lập kế hoạch / Tự động) cho analyst, trong giới hạn của agent.
8. **Kênh C-level**: Zalo/Teams/email cho bản tin và alert trước app di động.

## 5. Lộ trình hợp nhất

Gộp W1–W6 của v1.0 với R0–R4 của 00; thứ tự phụ thuộc giữ của v1.0, nội dung thêm R0 và Context Layer.

| Đợt | Gói của v1.0 | Nội dung | Ra khi |
| --- | --- | --- | --- |
| **R0 Nền mã** | (thiếu trong v1.0) | 10 lỗi gốc (00 §3.2), Plan IR + validator, result set channel, MCP chuẩn, checkpointer bền | SQL đúng không bị cắt; canvas chạy đúng như UI |
| **R1 Dữ liệu tin cậy + ngữ cảnh** | W1 Foundation + W2 Trusted Data + W3 Business Semantics | Tenant/SSO/policy (đã có phần lớn); connections đa engine; ingest file/DB/tài liệu; DQ, conflict, overlay, manifest; metric contract + AnalysisSpec compiler; Verified Query; Glossary; Catalog thống nhất; Eval Set | 15–25 metric qua reconciliation; user A/B đúng quyền; hard check chặn publish |
| **R2 Phân tích** | W4 Analysis | Router đường ngắn/GOVERNED/EXPLORATORY/DOCUMENT; chuyên gia SQL (01 §3.4); EvidenceFrame verifier; Workspace (cây artifact + plan canvas + inspector); Reconciliation Note; hybrid số + tài liệu; follow-up theo slot; cache theo dependency | Hai hành trình demo chạy độc lập; holdout ≥ 95%; SLO route pilot |
| **R3 Chủ động + học** | W5 Proactive + Review | Monitor vùng an toàn, alert có điều tra, kênh Zalo/Teams/email; proposal/action có duyệt; correction memory + Context Studio; SOP có duyệt; forecast baseline nhãn experimental | Dữ liệu đổi → 1 alert có bằng chứng; không lặp; approval idempotent |
| **R4 Nền tảng** | W6 Hardening (+ Publisher nếu D8) | Agent Studio (T2), skill có cổng, MCP server, danh tính agent, PII masking, HA/restore/security eval; Publisher chỉ khi có khách | Restore drill; failure test; eval canary |

Không gắn tuần. v1.0 ước 13–18 tuần với FE/QC/Ops đủ; cộng R0 và Context Layer, và trừ phần nền đã có
(auth, RLS, outbox, LightRAG, sandbox, Docling), mình không có cơ sở để ước chính xác hơn trước
discovery với khách thiết kế. Quy tắc: không chuyển ước tính thành ngày cam kết trước khi chốt nguồn,
connector và acceptance (v1.0 §13.2).

## 6. Rủi ro và quyết định cần chốt

| ID | Câu hỏi | Khuyến nghị |
| --- | --- | --- |
| D3 (nhắc lại) | Khách thiết kế đầu tiên | Chọn trong nhóm cấm egress, có dữ liệu phân tán, có owner metric; không chọn khách đã chuẩn hoá trên BigQuery/Fabric |
| D8 | Gói Publisher | Hoãn; giữ 2 object trong mô hình; mở khi có khách là nhà cung cấp dữ liệu |
| D9 | Workspace thay Thread/Notebook/Report | Đồng ý; FE1/FE2 dùng bố cục cây artifact trong [05-ui-v3.md](05-ui-v3.md) |
| D10 | Kênh C-level | Zalo/Teams/email trước; mobile sau khi có ≥ 2 khách trả tiền |
| D11 | Giá theo kết quả cho ingestion | Thử ở pilot: tính phí asset publish đạt hard check; đo tỷ lệ ngoại lệ cần người |
| D12 | Có phơi Data Space qua MCP cho coding agent ngay R2 không | Có; chi phí thấp (Capability Registry → MCP), giá trị sớm cho analyst |

Rủi ro mới so với 00: (1) khách mục tiêu (ngân hàng, nhà nước) có chu kỳ mua dài, pilot phải được trả
tiền hoặc có cam kết chuyển đổi; (2) Elton có thể đi lên phân khúc của ta bằng self-hosting (họ đã có
docs self-host) — lợi thế của ta phải là governance và tài liệu, không phải "on-prem" đơn thuần; (3)
hãng lớn thêm tiếng Việt và tài liệu vào Data Agent trong 12 tháng — lợi thế bền hơn là Data Space đã
nghiệm thu trong khách và vòng học từ sửa lỗi.

## 7. Thay đổi trên 00/01

- 00 §1 bề mặt: "Analyst Workspace (thread → notebook)" và "Report" → **Workspace** (cây artifact).
- 00 Q8: "Thread → Báo cáo → Dashboard → Monitor" → "Workspace artifact có version → Dashboard → Monitor".
- 00 §8 thêm D8–D12; 00 §9 lộ trình tham chiếu mục 5 ở đây.
- 01 §3.3: AnalysisSpec là input node GOVERNED; §3.1 bước 5 kiểm theo EvidenceFrame; §3.5 gộp bảng lỗi
  v1.0; §5.5 thêm khối ReconciliationNote; §5.3 thêm toggle mức tự chủ theo phiên.

## 8. Nguồn

- Bản "Thiết kế sản phẩm và giải pháp kỹ thuật Data Agent v1.0", 06/10/2026 (nội bộ; nguồn S1–S13 của nó).
- Agno Dash <https://github.com/agno-agi/dash>, docs <https://docs.agno.com/tutorials/dash/overview> (Apache-2.0; 2.265 sao; push cuối 10/07/2026).
- file.ai <https://www.file.ai/>, pricing <https://www.file.ai/pricing>; Series A
  <https://technode.global/2025/02/03/singapores-fileai-raises-14m-in-series-a-funding-led-by-illuminate-financial-antler-elevate-insignia-heinemann-and-others/>.
- dtquocbao/ai-market <https://github.com/dtquocbao/ai-market> (2 commit, 28/11/2025, không test, không license).
- Google Data Agent Kit GA <https://cloud.google.com/blog/topics/developers-practitioners/data-agent-kit-is-now-ga-bring-google-data-cloud-to-any-coding-agent>,
  plugin <https://github.com/GoogleCloudPlatform/data-agent-kit-plugin>; Conversational Analytics
  context <https://docs.cloud.google.com/gemini/data-agents/conversational-analytics-api/data-agent-authored-context-bq>;
  ADK BigQuery toolset <https://adk.dev/integrations/bigquery/>.
- Elton Data / DataUtils <https://eltondata.com/en>, docs <https://docs.eltondata.com/docs/>, blog
  <https://blog.eltondata.com/post/71>, <https://blog.eltondata.com/post/55>, app <https://xiz.eltondata.com/>.
  Tên agent/tool/toggle trong ảnh là quan sát sản phẩm, chưa thấy công bố công khai.
- Microsoft Fabric Data Agent limits (ảnh infographic; đối chiếu docs ở 00 §11).
- Looker Conversational Analytics (ảnh màn hình; docs ở 00 §11).
