# 01. Agent core và giao diện: quyết định từ nghiên cứu data agent

> Vai trò viết: BA/PO. Ngày 06/10/2026. Tiếp nối [00-product-strategy.md](00-product-strategy.md).
> Đầu vào: repo [awesome-data-agents](https://github.com/HKUSTDial/awesome-data-agents) và hai bài đi
> kèm (survey arXiv 2510.23587, tutorial SIGMOD'26 arXiv 2602.04261); đọc toàn văn các hệ Proto-L3
> (DeepEye, AgenticData, EvoDS, DeepAnalyze, SiriusBI, AOP, Data Interpreter), các benchmark 2025–2026
> (DataSpace, DAComp, FDABench, DAB, AgenticDataBench, DeepEye-SQL) và ProactiveVA. Nguồn ở mục 9.
>
> Tài liệu trả lời ba câu: agent core phải **thông minh và tự động đến mức nào** cho từng người dùng;
> **dựng nó thế nào** để có bằng chứng là đúng; **giao diện** phải cho người dùng thấy và can thiệp gì.

## 0. Kết luận

1. **Sản phẩm nhắm L3 có giám sát, không phải "tự động hoàn toàn".** Theo thang L0–L5 của survey, L3
   là "agent tự dàn pipeline cho nhiều loại việc, người giám sát kế hoạch và kết quả". Hệ hiện tại là
   **L2**: người dựng canvas, agent chạy theo; orchestrator chỉ chọn agent đã publish. L4 (tự phát hiện
   vấn đề, không cần người) chỉ làm **có giới hạn** ở Monitor. Mức tự chủ **đặt theo vai và rủi ro**
   (mục 2), không đặt một mức cho mọi người.
2. **Kế hoạch là một DAG kiểm được trước khi chạy**, không phải chuỗi suy nghĩ tự do. Ba hệ độc lập đi
   đến cùng thiết kế: AgenticData (cây operator, validate trước khi thực thi, "phần lớn lỗi bắt được
   ở bước validate"), DeepEye (Unified Node Protocol, Compiler → Validator → Optimizer → Executor),
   AOP (22 semantic operator, thực thi theo lớp, điều chỉnh sau mỗi lớp). Các agent huấn luyện tự do
   (DeepAnalyze) thắng ở việc mở nhưng sụp khi ngữ cảnh > 20K token.
3. **Kiểm tra tất định đưa chỉ thị, LLM chỉ sửa.** DeepEye-SQL: chuỗi unit test fail-fast (cú pháp →
   logic → chất lượng), "checker ra chỉ thị, LLM không tự chẩn đoán"; đồng thuận thực thi làm điểm tin
   cậy (tương quan 0,95 với đúng/sai) → ngưỡng để tự trả lời, hỏi thêm, hay đẩy cho người.
4. **Harness quan trọng ngang một bậc model.** DataSpace: cùng một model, đổi harness chênh 15,4 điểm;
   FDABench: "chọn đúng workflow có thể hơn nâng model". Vậy đầu tư vào agent core là đầu tư đúng chỗ,
   và D1 (model) không phải điều kiện tiên quyết của mọi thứ.
5. **Lỗi lớn nhất của agent giỏi không nằm ở tính toán mà ở lúc trả kết quả.** DataSpace: 44% thất
   bại của model tốt nhất là "tính đúng nhưng nộp sai cột/độ chính xác/định dạng". Vì vậy **hợp đồng
   đầu ra** (output contract) là một bước bắt buộc của mọi task, và giao diện phải hiện nó.
6. **Ngữ cảnh phải bị giới hạn theo thiết kế.** AgenticData chọn dữ liệu để ngữ cảnh < 32K; EvoDS nén
   ngữ cảnh có học, xoá hoàn toàn lỗi hết token; DeepEye cô lập ngữ cảnh từng sub-agent. Sub-agent
   nhận ngữ cảnh hẹp, planner chỉ thấy cổng vào/ra.
7. **Tri thức offline là đòn bẩy lớn nhất.** AgenticData bỏ profiling: 70% → 5% trên Real-Bank;
   SiriusBI bỏ knowledge: −23 đến −27 điểm. Khớp với Q1 của 00 (Context Layer là lõi).
8. **Giao diện phải "steerable" ở mọi giai đoạn** (DeepEye: ràng buộc ngữ cảnh bằng `@`, canvas DAG,
   inspector sửa từng node, validator chặn sửa sai, lưu thành SOP), và gợi ý chủ động phải **xem trước
   được, bỏ qua được, điều chỉnh được tần suất, nêu lý do** (ProactiveVA, user study 10 người: độ
   chính xác 0,78 → 0,95; điểm "không gây phiền" 6,1/7).

## 1. Khung tham chiếu: thang tự chủ và chỗ của chúng ta

Survey định nghĩa data agent `A : (T, D, E, M) → O` (task, dữ liệu thô, môi trường, LLM → sản phẩm dữ
liệu), khác agent chung ở chỗ: dữ liệu lớn và thô, thăm dò động, bộ công cụ chuyên (DB loader, SQL
checker, viz), **lỗi lan truyền xuôi dòng**.

| Mức | Ai dàn pipeline | Ai chạy | Vai người | Hiện trạng Data Agent | Mục tiêu |
| --- | --- | --- | --- | --- | --- |
| L1 | Người | Người, agent trả lời prompt | Tích hợp, kiểm | Ask với một agent đơn, canvas mặc định | Giữ cho câu hỏi nhanh |
| L2 | Người | Agent (có công cụ, feedback môi trường) | Dàn pipeline | **Đang ở đây**: Builder canvas, agent chạy theo; orchestrator chọn agent đã publish | Giữ cho workflow cố định do phòng ban dựng |
| L3 | **Agent** | Agent | **Giám sát** kế hoạch và kết quả | Chưa có: planner không sinh pipeline, không có validator | **Mục tiêu chính** cho Ask/Analyst/Deep analysis |
| L4 | Agent, tự phát hiện việc | Agent | Nhận kết quả | Chưa có | **Có giới hạn**: Monitor trong vùng an toàn khai báo (mục 2.3) |
| L5 | Agent, sáng tạo phương pháp | | Không | | Ngoài phạm vi |

Bốn thiếu hụt L2 → L3 mà survey nêu, và cách sản phẩm trả lời:

| Thiếu hụt (survey §V-C) | Trả lời của sản phẩm |
| --- | --- |
| Tự chủ dàn pipeline hạn chế, phụ thuộc operator định sẵn | Planner sinh DAG từ **capability đã đăng ký + operator tổng quát** (mục 3.3); capability mới đi đường skill có duyệt (mục 3.7). Chấp nhận "định sẵn" là ranh giới an toàn, không phải khuyết điểm |
| Phủ không hết vòng đời dữ liệu | Phạm vi sản phẩm: phân tích + chuẩn bị dữ liệu (profile, DQ, mapping) + quản trị; **không** làm tuning/diagnosis DBMS |
| Thiếu suy luận chiến lược, kẹt vòng lặp sửa triệu chứng | Phân loại lỗi theo **nguyên nhân gốc** và đường phục hồi riêng cho từng loại (mục 3.5); giới hạn replan; đẩy cho người khi lặp |
| Không thích nghi môi trường động | Drift schema/giá trị đi qua Catalog + DQ (V2); memory sửa lỗi; eval canary hằng ngày |

Ba rủi ro survey nêu về chữ "data agent": người dùng kỳ vọng sai (bác bỏ kết quả đúng hoặc tin kết quả
sai), trách nhiệm pháp lý mơ hồ, ngành thổi phồng. Hệ quả cho sản phẩm: **mỗi agent, mỗi câu trả lời
khai báo mức tự chủ và nhãn tin cậy** (00, bất biến 14), để kỳ vọng và quyền can thiệp khớp với mức.

## 2. Mức tự chủ theo vai và rủi ro

### 2.1 Theo vai

| Vai | Việc | Mức | Người thấy gì, quyết gì |
| --- | --- | --- | --- |
| **C-level / quản lý** | Hỏi nhanh; hỏi "vì sao"; nhận báo cáo và cảnh báo | L3 cho câu hỏi, L4 giới hạn cho monitor | Thấy **câu trả lời trước**, nhãn tin cậy, bằng chứng gấp lại; kế hoạch ẩn sau một cú bấm. Không bao giờ phải sửa SQL. Hỏi lại tối đa một câu khi thiếu metric/kỳ |
| **Data analyst** | Phân tích nhiều bước, deep analysis, báo cáo | L3 **steerable** | Thấy DAG kế hoạch khi đang chạy; sửa node (SQL, tham số), chạy lại phần xuôi dòng; thăng câu hỏi thành notebook; lưu thành SOP/verified query |
| **Data steward** | Ngữ cảnh, duyệt, sửa lỗi | L1–L2 có đề xuất | Máy đề xuất (mapping, metric, join, synonym), người quyết; không có đường tự áp |
| **Phòng ban dựng agent** | Agent cho nhóm | L2 (canvas cố định) hoặc L3 (từ mô tả + Data Space) | Chọn mức khi tạo agent; mức L3 bắt buộc có Eval Set |
| **Monitor** | Theo dõi, điều tra, cảnh báo | L4 giới hạn | Người nhận alert có điều tra kèm; không hành động ghi nào tự động |

### 2.2 Theo rủi ro của hành động

| Hành động | Tự chạy | Cần duyệt |
| --- | --- | --- |
| Đọc dữ liệu trong Data Space, trong quyền người hỏi | Có | |
| Tính toán, vẽ, viết báo cáo | Có | |
| Đọc ngoài Data Space (tìm bảng khác) | Có, nhưng ghi rõ và gắn nhãn EXPLORATORY | |
| Gửi báo cáo/alert tới kênh ngoài (Slack/Teams/email) | Theo lịch đã duyệt | Lần đầu và khi đổi người nhận |
| Ghi vào tài sản ngữ cảnh (metric, verified query, SOP, skill) | Không | Luôn (steward/checker) |
| Ghi vào dữ liệu khách, DDL, sửa pipeline | Không bao giờ | Ngoài phạm vi sản phẩm |
| Chi phí vượt ngân sách truy vấn/LLM của Data Space | Không | Người xác nhận hoặc dừng |

### 2.3 Vùng an toàn cho L4 (Monitor)

Monitor được tự chạy khi và chỉ khi: capability dùng là tất định hoặc GOVERNED; dữ liệu trong một Data
Space; không có hành động ghi; chi phí dưới ngân sách; luật significance tất định chạy trước LLM (00 bất
biến 11); alert gom nhóm, dedup, cooldown (V2 ADR-V2-18). Ngoài vùng này, Monitor chỉ được **đề xuất
điều tra**, không tự điều tra.

## 3. Thiết kế agent core

### 3.1 Vòng lặp

```text
                ┌──────────────────────────────────────────────────────────────┐
                │ 0. GROUND   Data Space ∩ quyền người hỏi; @-binding; profile │
                │             graph; glossary; verified query; SOP tương tự     │
                └──────────────────────────────┬───────────────────────────────┘
 Câu hỏi / mục tiêu / sự kiện monitor          ▼
 ──────────────► 1. UNDERSTAND  intent, metric, dimension, kỳ, output contract
                      │ thiếu metric/kỳ → hỏi lại (tất định, SiriusBI)
                      ▼
                 2. PLAN       DAG kế hoạch (Plan IR, mục 3.3): node = capability/operator,
                      │        cổng vào/ra có kiểu, evidence_requirements; chọn SOP nếu khớp
                      ▼
                 3. VALIDATE   tĩnh, trước khi chạy: chu trình, kiểu cổng, pseudo-execute
                      │        schema, quyền, chi phí ước lượng, secret có đủ → lỗi có cấu trúc
                      ▼
                 4. EXECUTE    theo lớp topo, song song trong lớp; ToolNode tất định,
                      │        AgentNode ngữ cảnh cô lập; kết quả = RESULT SET có id
                      ▼
                 5. VERIFY     tất định, fail-fast → chỉ thị: số có nguồn, đơn vị, độ tươi,
                      │        quyền, đủ bằng chứng, đồng thuận N-version (SQL)
                      │ lỗi → 6. REFLECT: phân loại nguyên nhân gốc → đường phục hồi riêng,
                      │        ≤ 2 vòng, rồi INSUFFICIENT_EVIDENCE hoặc đẩy người
                      ▼
                 7. MATERIALIZE  ép về output contract (cột, kiểu, độ chính xác, đơn vị, thứ tự)
                      ▼
                 8. PRESENT    khối từ catalog + nhãn tin cậy + bằng chứng + lineage
                      ▼
                 9. LEARN      phản hồi → correction memory; workflow thành công → SOP ứng viên
                               (chỉ có hiệu lực sau duyệt)
                 Người giám sát: xem/sửa ở 1, 2, 4 (từng node), 7; duyệt ở 9.
```

Khác với runtime hiện tại ở bốn chỗ: có bước 0 (ground trên Context Layer), 3 (validate tĩnh), 7
(materialize theo hợp đồng), và 6 phân loại nguyên nhân thay vì "judge FAIL → chạy lại planner".

### 3.2 Hai loại agent trong một runtime

Theo EvoDS (manager + sub-agent có không gian hành động **rời nhau**, chứng minh sai số chọn tool thấp
hơn cấu trúc phẳng) và DeepEye (planner chỉ thấy cổng I/O của sub-agent, không thấy ngữ cảnh trong):

| | Supervisor (Analyst Agent) | Chuyên gia (sub-agent) |
| --- | --- | --- |
| Thấy | Câu hỏi, Data Space profile, catalog capability, cổng vào/ra của các node | Chỉ node của mình: input đã ground, vài bảng/tài liệu, tool của vai mình |
| Không thấy | Ngữ cảnh trong của chuyên gia, dữ liệu thô | Kế hoạch tổng, chuyên gia khác |
| Ngữ cảnh | Context stack có nén (mục tiêu, quyết định, result set id) | ≤ 32K token, nén theo mục tiêu toàn cục khi vượt |
| Tool | Lập kế hoạch, hỏi lại, gọi chuyên gia, tổng hợp | Rời nhau theo vai (mục 6 của 00) |
| Model | Vai cần suy luận: model mạnh | Theo vai: SQL/code model, model nhỏ cho phân loại |

### 3.3 Plan IR

Kế hoạch là dữ liệu, không phải văn bản. Node theo Unified Node Protocol của DeepEye, loại node theo DAG
vàng của FDABench:

```yaml
plan_id: ...
output_contract:                 # bắt buộc, sinh ở bước 1, người sửa được
  columns: [{name: region, type: text}, {name: revenue_q3, type: number, precision: 0, unit: VND}]
  order_by: [revenue_q3 desc]
  shape: table | scalar | series | report
nodes:
  - id: n1
    kind: SQL_QUERY            # SQL_QUERY | RETRIEVE_DOC | EXTRACT_EVIDENCE | COMPUTE | VALIDATE
    capability: semantic.compile   # | SYNTHESIZE_REPORT | CLARIFY | HUMAN_APPROVAL
    inputs:  {metric: revenue, dims: [region], period: 2026Q3}
    outputs: [{key: rs_rev, type: result_set, schema: [...]}]
    evidence_requirements: [{kind: result_set, min_rows: 1}]
    trust: GOVERNED
  - id: n2
    kind: RETRIEVE_DOC
    capability: kb.search
    inputs: {query: "biên lợi nhuận miền Bắc tháng 9", space: ds_finance}
    outputs: [{key: ev_docs, type: evidence_list}]
edges:
  - {from: n1, to: n3, kind: HARD_DEP}     # HARD_DEP | SOFT_DEP | ALT_GROUP
```

Quy tắc:

- **AnalysisSpec** (từ bản v1.0, 02 §1.2) là `inputs` chuẩn của node `SQL_QUERY` đường GOVERNED:
  `{intent, metrics: ["sales.net_revenue@3"], dimensions, period{from,to_exclusive,timezone,calendar},
  compare_to, filters, outputs}`. Identity, tenant, policy, budget do server gắn; LLM không điền.
- **Operator tối thiểu** (lấy từ 22 của AOP và 6 semantic của AgenticData, chỉ giữ cái sản phẩm cần):
  quan hệ `select, project, join, group, agg, sort, union`; semantic `sem_extract, sem_filter,
  sem_classify, sem_join, summarize, compare, explain, validate, link`; phân tích tất định
  `compare_period, contribution, driver_decomposition, anomaly, forecast_baseline` (`libs/analytics`).
- Node tất định (ToolNode) idempotent, không cần ngữ cảnh; node LLM (AgentNode) có ngữ cảnh riêng.
- Cổng có kiểu; **result set là kiểu hạng nhất** (00, R0-1). Dữ liệu không đi qua prompt; chỉ id, schema,
  thống kê tóm tắt, và mẫu có giới hạn đi qua.
- Planner **không được** tạo node ngoài catalog capability; operator "LLM sinh mã" chỉ tồn tại dưới dạng
  node `COMPUTE(python)` trong sandbox, mang nhãn EXPLORATORY.
- Task phức tạp: phân rã thành sub-task "gần độc lập", mỗi sub-task chạy trọn vòng, kết quả **vật chất
  hoá** thành result set có profile, rồi kế hoạch cuối chạy trên các result set đó (AgenticData), không
  ghép các sub-plan.

### 3.4 Đường SQL (chuyên gia Semantic/SQL)

Áp dụng DeepEye-SQL, vì mỗi bước đều có ablation đo được (BIRD-dev 73,5% với model 30B mã mở, không
fine-tune; Spider 2.0-Snow 50,5% so ReFoRCE 38,0%):

| Bước | Làm gì | Ablation (điểm mất khi bỏ) | Nối vào repo |
| --- | --- | --- | --- |
| Verified / semantic trước | Khớp verified query hoặc semantic model → không cần sinh SQL | (dbt: semantic layer gần 100% trong phạm vi) | `libs/semantic`, Verified Query (00) |
| Semantic value retrieval | Chỉ mục giá trị theo cột TEXT; từ khoá trong câu hỏi → giá trị thật | −2,1 | Catalog: chỉ mục giá trị (00 §7.1) |
| Schema linking hợp nhất | Trực tiếp ∪ "reverse" (viết SQL nháp rồi rút schema) ∪ theo giá trị, rồi **đóng bao PK/FK** | −1,7; recall cột 80,9 → 95,4, ngữ cảnh nhỏ 9× | Schema crawler thu PK/FK; `handlers/data.py` |
| N-version | Ba cách sinh khác nhau (skeleton, ICL với câu hỏi che, chia-trị), nhiều mẫu | −2,5 (ICL) … −1,2; 10% câu đúng chỉ một cách sinh ra được | `text_to_sql` alias; thêm sinh song song |
| Unit test tất định | Cú pháp → logic (JOIN, ORDER/LIMIT, hàm thời gian) → chất lượng (SELECT *, NULL trong sort, rỗng); **checker ra chỉ thị, LLM chỉ sửa** | −2,1 | `sql_guard` mở rộng thành tool-chain |
| Cổng chất lượng | Chạy tất cả ứng viên, gom theo kết quả; confidence = tỷ lệ cụm; > 0,6 trả; thấp hơn → LLM so cặp; **rất thấp → hỏi lại hoặc đẩy người** | −0,8; confidence ~ đúng/sai r = 0,954 | Mới; là nguồn của nhãn tin cậy và của "khi nào nhờ người" |

Confidence thấp là tín hiệu đáng tin nhất hiện có để quyết định tự trả lời, hỏi lại hay đẩy review.

**Ngưỡng chốt 06/10/2026** (chỉ cho đường Khám phá; verified query và semantic model không bỏ phiếu). Sáu
ứng viên = 3 cách sinh × 2 mẫu, gom theo bảng kết quả:

| Đồng thuận | Hành động |
| --- | --- |
| ≥ 4/6 | Trả lời, nhãn Khám phá |
| 3/6 | So cặp rồi trả lời, kèm "nên kiểm lại" |
| ≤ 2/6 | Không đưa số; hỏi lại một câu có lựa chọn hoặc `INSUFFICIENT_EVIDENCE` |
| Mọi ứng viên lỗi | Lỗi có mã |

Hiệu chỉnh trên Eval Set: ngưỡng thấp nhất mà nhóm câu vượt ngưỡng đúng ≥ 90% và tỷ lệ hỏi lại ≤ 20%.
Ngưỡng của DeepEye-SQL đo trên BIRD với model khác; model của ta phải đo lại.
Đây là cơ chế "biết mình không biết" của sản phẩm.

### 3.5 Phản tư theo nguyên nhân gốc

Survey: hệ hiện tại "sửa triệu chứng của bước hỏng rồi kẹt vòng lặp". DataSpace: "triệu chứng không
phải chẩn đoán" (58/74 lỗi "sai số cột" bắt nguồn từ bước vật chất hoá, phần còn lại từ thượng nguồn).
Vì vậy lỗi được phân loại rồi mới chọn đường phục hồi:

| Lớp lỗi | Phát hiện | Phục hồi | Không làm |
| --- | --- | --- | --- |
| Thiếu dữ liệu trực tiếp (cột/bảng không có) | Validator schema | Nới chọn dữ liệu, tìm lại trong Catalog; vẫn thiếu → `NO_DATA` | Không sinh SQL khác |
| Thiếu dữ liệu gián tiếp (không có cầu join) | Pseudo-execute | Tìm bảng cầu qua PK/FK, quan hệ entity; không có → hỏi người | Không đoán join |
| Lỗi logic kế hoạch (top-k không sort, sai grain, sai mức tổng hợp) | Logic detector + unit test | Sửa node, chạy lại xuôi dòng | Không chạy lại từ đầu |
| Lỗi ngữ pháp/tool | EXPLAIN, Pylint, schema tool | LLM sửa theo chỉ thị | Không tự chẩn đoán |
| Sai ý định (grain, điều kiện, phạm vi) | Verifier so output contract với câu hỏi; confidence thấp | Hỏi lại người (một câu, có lựa chọn) | Không giả định |
| Sai vật chất hoá (cột, độ chính xác, đơn vị) | So với output contract | Ép lại theo hợp đồng, tất định | Không gọi LLM |
| Thiếu bằng chứng | Evidence requirements | Truy hồi sâu hơn một vòng; vẫn thiếu → `INSUFFICIENT_EVIDENCE` | Không kết luận vượt bằng chứng |

Mã lỗi hợp nhất với bản v1.0 (một RefusalCard/mã): `NEEDS_CLARIFICATION`, `NO_DATA`,
`NO_JOIN_PATH`, `FORBIDDEN`, `STALE_DATA`, `SCHEMA_CHANGED`, `QUALITY_BLOCKED`, `REVIEW_PENDING`,
`UNSUPPORTED`, `BUDGET_EXCEEDED`, `TOOL_UNAVAILABLE`, `INSUFFICIENT_EVIDENCE`, `CONTRACT_MISMATCH`,
`PLANNER_ERROR`, `AGENT_CONFIG_ERROR`. Verifier kiểm theo **EvidenceFrame** (claim, metric_version,
dimensions, period, unit, result_ref, source_ref, query_hash, watermark, coverage, policy_version), không
so chuỗi chữ số.

Giới hạn: ≤ 2 vòng replan; lỗi lặp cùng lớp → dừng và đẩy người. Ngân sách tăng không giúp
(AgenticDataBench: tăng bước/thời gian kéo dài vòng lặp vô ích).

### 3.6 Memory ba tầng có kiểm soát nạp

| Tầng | Nội dung | Nguồn mẫu | Kiểm soát |
| --- | --- | --- | --- |
| Làm việc | Context stack: mục tiêu, quyết định, result set id, lỗi gần nhất (top-3 theo lớp) | DeepEye, AgenticData | Nén theo mục tiêu khi vượt ngân sách; không giữ dữ liệu thô |
| Tạm thời (theo phiên/task group) | ⟨trạng thái, chuyển đổi, lỗi⟩, giá trị kế hoạch tốt/xấu | AgenticData (MDP, γ=0,1) | Top-k good/bad, xoá còn lại; "mỗi câu ~10 bản ghi, một giờ có thể triệu bản" |
| Dài hạn | Schema metadata, tài liệu miền, **SOP (workflow đã kiểm)**, good/bad plan, correction | DeepEye, SiriusBI | **Nạp có duyệt**: SOP tự lưu là ứng viên; chỉ SOP do người lưu/duyệt mới được planner dùng làm mẫu |

Memory dài hạn **gắn Data Space**: AgenticData đo memory dài hạn gần như vô ích giữa các DB khác nhau
(Spider 2.0-Lite), nhưng tăng 5× trên cùng miền.

### 3.7 Skill: học có cổng

EvoDS: skill = ⟨tên, mô tả, mã⟩; sinh khi việc vượt không gian skill → xác minh bằng thực thi → cache
riêng → chỉ thăng hạng sau τ = 3 lần sinh lại thành công; 279 skill, tái dùng 69%. Sản phẩm giữ cơ
chế nhưng thêm cổng người (00 bất biến 12):

```text
việc vượt capability → sinh skill ứng viên (mã trong sandbox, nhãn EXPLORATORY)
→ xác minh thực thi → cache theo Data Space → ≥ 3 lần dùng thành công
→ ứng viên trong Context Studio → eval offline → steward duyệt → Capability Registry (có version)
```

Skill chưa duyệt không bao giờ được planner chọn cho câu hỏi của người khác.

### 3.8 Nguyên tắc harness rút từ benchmark

| Nguyên tắc | Bằng chứng | Áp dụng |
| --- | --- | --- |
| Dữ liệu ở lại trong bộ nhớ giữa các bước | AgenticDataBench: harness nạp lại file mỗi bước timeout trên file ≥ 1 GB | Result set sống theo phiên, node sau tham chiếu id |
| Một chương trình lớn hơn nhiều lần thăm dò | DAB: 12,7 tool call thắng 30; song song < 10% lượt dù 22–51% là thăm dò | Chuyên gia Python nhận profile + mẫu, viết một đoạn mã; planner chạy node độc lập song song |
| Primitive trích xuất là tool, không để agent tự viết regex | DAB: mọi agent mặc định regex; lỗi "MALE khớp FEMALE", "năm từ ISBN" | `sem_extract`, parse ngày, NER, đơn vị tiền tệ (có sẵn `libs/indicator/numeric`) |
| Kiểm tra trước thực thi rẻ hơn chạy rồi sửa | AgenticData, DeepEye Validator | Bước 3 của vòng lặp |
| Thất bại tiêu tốn 1,2–3,2× token của thành công | DataSpace | Dừng sớm theo lớp lỗi lặp, không tăng ngân sách |
| Reflection/đa agent xuống cấp từ từ, kế hoạch tĩnh sụp trên câu khó; nhưng đắt 2–3× | FDABench | Chế độ: câu dễ → kế hoạch một lượt; câu khó/deep analysis → reflection; chọn theo confidence và độ sâu DAG |
| Model "suy nghĩ" + harness nặng = phạt kép | FDABench | Không bật thinking cho model trong node đã có checker ngoài |
| Hướng dẫn chung của dataset có thể gây hại | DAB: hint ±4 điểm, có khi làm sai | Instruction trong Data Space gắn phạm vi hẹp, có eval đo trước/sau |

### 3.9 Ánh xạ vào repo

| Thành phần đích | Hiện có | Việc |
| --- | --- | --- |
| Plan IR + validator | `orchestrator/planner/dpts` sinh Target/Task với `agent_id`; không có cổng, không validate | Planner xuất Plan IR; validator tĩnh mới (`application/plan/validate.py`); DPTS giữ làm chiến lược tìm kiếm, đổi không gian hành động từ "agent" sang "capability/operator" |
| Executor theo lớp | `executor/executor.py` + `task_queue.py` chạy DAG, replan ≤ 2 | Giữ; thêm result set store, chạy lại xuôi dòng theo `depends_on` (V2 07 phụ lục) |
| Node protocol | `NODE_TYPE_MAP` 12 loại, state phẳng `AgentState` | Cổng có kiểu; `db_results` → result set id (R0-1); AgentNode có ngữ cảnh riêng |
| Chuyên gia SQL | `handlers/data.py` một lượt + 3 lần tự sửa | Mục 3.4 |
| Verifier | `libs/indicator/verifier.py` 4 kiểm + judge LLM | Thêm output contract, đồng thuận, lớp lỗi; gửi frame `__verification` (R0-8) |
| Memory | `memory_middleware.py`, `workflow_memory.py` | Gắn Data Space; SOP có duyệt; top-k good/bad plan |
| Capability Registry | `TOOL_REGISTRY` tĩnh; `indicator_api /capabilities` 501 | Một bảng, mục 7.2 của 00 |
| Monitor | chưa có | `detector` V2 + vùng an toàn mục 2.3 |

## 4. Quyết định tính năng

Ký hiệu mức: L theo mục 1. "Dừng" = điểm hệ thống phải dừng chờ người.

| # | Tính năng | Cho ai | Mức | Dừng ở | Bằng chứng/lý do |
| --- | --- | --- | --- | --- | --- |
| F1 | **Quick answer**: metric/kỳ/dimension → số có nhãn tin cậy, chart, bằng chứng | C-level, mọi người | L3 | Thiếu metric/kỳ → một câu hỏi lại | SiriusBI: kiểm đủ metric+dimension +6…+21 điểm |
| F2 | **Explain change**: phân rã driver + sự kiện/tài liệu liên quan | C-level, analyst | L3 | Bằng chứng không đủ → nói rõ | UC10 V2; FDABench: câu nhiều nguồn là chuẩn khó |
| F3 | **Deep analysis**: mục tiêu → kế hoạch hiện ra → nhiều bước → báo cáo có số neo, chart, giới hạn | Analyst, C-level nhận | L3 steerable | Sau kế hoạch (tuỳ chọn), chi phí vượt ngân sách | DAComp: báo cáo chấm theo rubric có số neo, phạt số bịa |
| F4 | **Steer**: xem DAG, sửa node, chạy lại xuôi dòng, lưu SOP | Analyst | L3 | Mỗi lần sửa | DeepEye |
| F5 | **Output contract hiện ra và sửa được** trước khi chạy câu dài | Analyst | | | DataSpace 44% |
| F6 | **Clarify có lựa chọn**, tối đa một câu cho C-level, nhiều hơn cho analyst | Mọi người | | | BIRD-Interact: tương tác là năng lực lõi |
| F7 | **Thread → Notebook**: thăng câu hỏi thành notebook có mã, chạy lại | Analyst | L2 | | Hex Threads; Data Interpreter action graph |
| F8 | **Report & schedule**: báo cáo từ SOP, chạy định kỳ, gửi kênh | C-level nhận | L3 theo lịch đã duyệt | Lần đầu | |
| F9 | **Monitor & Alert có điều tra** | C-level, analyst | L4 giới hạn | Không ghi; chỉ thông báo | 00 §10 V2, mục 2.3 |
| F10 | **Proactive suggestion trong UI**: gợi ý khi người dùng dừng lâu/lặp thao tác; xem trước, bỏ qua, chỉnh tần suất | Analyst | L2 | Luôn chờ người chấp nhận | ProactiveVA |
| F11 | **Correction → đề xuất → duyệt**: sửa câu trả lời sai thành verified query/synonym/metric | Steward, analyst | L2 | Luôn | OpenAI, Wren; 00 T3 |
| F12 | **Skill có cổng** | Steward | L2 | Luôn | EvoDS + bất biến 12 |
| F13 | **Agent từ mô tả + Data Space**, kèm Eval Set tự sinh, chạy eval trước khi publish | Phòng ban | L3 | Publish | 00 T2 |
| F14 | **Nhãn mức tự chủ trên agent và câu trả lời** | Mọi người | | | Survey: rủi ro kỳ vọng sai |
| F15 | **Ngân sách**: chi phí truy vấn/LLM theo Data Space, dừng khi vượt | Quản trị | | Vượt | FDABench chênh chi phí > 2,5× giữa kiến trúc |

Không làm (và lý do): agent tự áp skill/SOP vào production (bất biến 12); agent ghi dữ liệu khách; tuning
DBMS; "tự động hoàn toàn" L4 ngoài vùng an toàn; tăng ngân sách bước để cứu câu khó.

## 5. Quyết định giao diện

### 5.1 Nguyên tắc

1. **Câu trả lời trước, quy trình sau, nhưng quy trình luôn cách một cú bấm.** C-level thấy số và
   nhãn; analyst mở DAG.
2. **Can thiệp được ở mọi giai đoạn** (DeepEye): trước khi lập kế hoạch (`@` ràng buộc nguồn), ở kế
   hoạch (canvas), ở từng node (inspector), ở kết quả (hợp đồng đầu ra), sau khi xong (lưu SOP).
3. **Hệ thống nói nó đã kiểm gì**, không chỉ "[1]": kết quả verifier từng mục, nhãn tin cậy, confidence
   (V2 09 §0.5: trích dẫn tự nó tạo niềm tin sai).
4. **Gợi ý chủ động phải khiêm tốn**: cửa sổ nổi, tự biến mất sau vài giây nếu không tương tác, xem
   trước rồi mới áp, có lý do, có nút tắt theo loại và thanh tần suất (ProactiveVA).
5. **Đánh giá tách khỏi sửa** trong mọi màn duyệt (V2 09 §0.6).
6. **Hợp đồng đầu ra hiện rõ**: cột, kiểu, đơn vị, độ chính xác, thứ tự; số không bao giờ lấy từ văn
   bản LLM (V2 09 §0.4, render theo catalog).
7. **Mọi trạng thái có URL** (series, as-of, run, node, case, alert) (V2 FE-00).

### 5.2 Bề mặt theo vai

| Bề mặt | Vai | Mức tự chủ hiện ra |
| --- | --- | --- |
| **Ask** | C-level, mọi người | Trả lời + nhãn; "Xem cách tính" mở DAG đọc-chỉ |
| **Analyst Workspace** | Analyst | Thread + Plan canvas + Inspector + Result sets + nút "Mở notebook" |
| **Reports & Dashboards** | C-level nhận, analyst dựng | Báo cáo có số neo và lineage; dashboard ghim result set có lịch |
| **Monitors & Alerts** | C-level, analyst | Alert gom nhóm, điều tra kèm, "Hỏi tiếp" mở Ask với ngữ cảnh |
| **Data Studio** | Steward, DE | Catalog, Semantic Model, Glossary, Verified Query, Connections |
| **Review Inbox** | Steward, checker | ReviewCase số liệu (V2) + đề xuất ngữ cảnh (correction, SOP, skill) |
| **Context Studio** | Steward | Trace, câu hỏi thất bại theo lớp lỗi, đề xuất sửa, eval theo Data Space |
| **Agent Studio** | Phòng ban | Tạo agent: Data Space, kỹ năng, mức tự chủ, kênh, Eval Set, điểm eval |

### 5.3 Analyst Workspace: bố cục

Lấy 7 panel của DeepEye làm gốc, gộp cho vừa màn hình:

```text
┌──────────────┬──────────────────────────────────────┬──────────────────────────┐
│ A. Ngữ cảnh  │ C. Thread (chat)                      │ E. Inspector / Evidence  │
│  Data Space  │  hỏi, @-binding (@kho_tài_chính,      │  node đang chọn: I/O,    │
│  nguồn, bảng │   @metric_doanh_thu)                  │  SQL/tham số sửa được,   │
│  tài liệu    │  câu trả lời theo khối:               │  result set preview,     │
│  verified q. │   ValueCard · bảng · chart ·          │  verifier từng mục,      │
│  SOP         │   nhãn tin cậy · hợp đồng đầu ra      │  provenance, chi phí     │
│              │  clarification card (lựa chọn)        │                          │
│ B. Lịch sử   ├──────────────────────────────────────┤  Gợi ý chủ động (nổi,    │
│  thread, run │ D. Plan canvas (DAG theo lớp thực thi)│  xem trước / bỏ qua)     │
│              │  node: trạng thái, trust, confidence  │                          │
│              │  bấm node → E; sửa → validator →      │ F. Hành động             │
│              │  "chạy lại từ đây"                    │  lưu SOP · verified query│
│              │  ẩn mặc định ở Ask, mở ở Workspace    │  mở notebook · báo cáo   │
└──────────────┴──────────────────────────────────────┴──────────────────────────┘
```

Hành vi bắt buộc: canvas vẽ **khi đang chạy** (frame `__plan`, `__step`), node sửa xong thì validator
chạy ngay và chặn nếu lệch kiểu cổng ("Schema mismatch" trước khi tốn tiền), "chạy lại từ đây" chỉ
chạy xuôi dòng.

### 5.4 Ask cho C-level

- Trả lời mở đầu bằng **ValueCard**: số, đơn vị, kỳ, so kỳ, nhãn tin cậy, "as of".
- Một dòng "Vì sao": driver top-3 có bằng chứng; bấm mở Explain change.
- Clarification: tối đa một câu, có 2–4 lựa chọn, có "Dùng mặc định" ghi rõ giả định.
- Không hiện SQL mặc định; "Xem cách tính" mở DAG đọc-chỉ và SQL.
- Gợi ý tiếp theo (đã có `__suggestions`) lọc theo capability của Data Space.
- Khi `INSUFFICIENT_EVIDENCE`/`NO_DATA`: RefusalCard nói thiếu gì và ai có thể bổ sung (link tới steward).

### 5.5 Hợp đồng stream và catalog khối

Frame theo mô hình AG-UI (V2 09 §4.2), bổ sung cho agent core mới:

| Frame | Nội dung |
| --- | --- |
| `__run` | run_id, agent, Data Space, mức tự chủ, ngân sách |
| `__plan` | Plan IR (node, edge, lớp thực thi, output contract) |
| `__step` | node_id, trạng thái, trust, confidence, chi phí, thời gian |
| `__result_set` | id, schema, số dòng, thống kê, mẫu giới hạn |
| `__verification` | từng kiểm: tên, đạt/không, chi tiết, hành động |
| `__block` | khối render có kiểu từ catalog |
| `__interrupt` | `clarify` (câu, lựa chọn) · `approve` (hành động, lý do) · `budget` |
| `__suggestion` | gợi ý chủ động: loại, lý do, xem trước, thời hạn |
| `__refusal`, `__lineage`, `__citations` | như V2 |

**Nhãn tin cậy chốt 06/10/2026:** gắn theo từng khối (mỗi con số, bảng, biểu đồ). Huy hiệu tổng của câu trả
lời = nhãn yếu nhất, kèm phân rã ("2 số Có quản trị · 1 số Khám phá"). Thang cho con số: Đã kiểm > Có
quản trị > Khám phá > Chưa đủ bằng chứng. Phần giải thích lấy từ tài liệu mang nhãn riêng "Giả thuyết từ
tài liệu" và không kéo nhãn của con số xuống. Lý do: một câu trả lời chỉ chắc bằng mắt xích yếu nhất, và
quyết định kinh doanh thường dựa đúng vào phần giải thích.

Catalog khối: ValueCard, SeriesChart, DataGrid (có hợp đồng đầu ra), DriverBar, EvidenceList (trang,
bbox), VerifierBadge, TrustLabel, ConfidenceMeter, OutputContractCard, PlanNodeCard, RefusalCard,
ClarificationCard, ApprovalCard, SuggestionToast, ReportSection, **ReconciliationNote** (STAGE, TARGET,
BASIS, GRAIN, RULES, IDENTITIES, RESIDUAL, OPEN, ASSUMPTIONS — agent sinh khi dựng view/metric; duyệt
note là duyệt metric; 02 §2.3).

Workspace (D9) thay cho Thread/Notebook/Report: panel B của bố cục §5.3 là **cây artifact**
`files / notes / plan / sql{deploy, checks, explore} / dashboards / reports`, mỗi mục có version và
trạng thái deploy (chưa triển khai · đã triển khai · đã đổi sau khi triển khai). Analyst có toggle **Lập
kế hoạch / Tự động** theo phiên, trong giới hạn mức tự chủ của agent (§2). Agent chỉ chọn khối và truyền dữ liệu
có kiểu; không sinh HTML.

### 5.6 Review và Context Studio

- Review Inbox hai bước (đánh giá → sửa), OCC 409, maker ≠ checker (V2).
- Context Studio: danh sách câu hỏi thất bại **theo lớp lỗi của mục 3.5** (thiếu dữ liệu, sai ý định,
  sai vật chất hoá…), vì mỗi lớp dẫn tới một loại sửa khác nhau (thêm bảng, thêm synonym, sửa metric,
  thêm verified query); bảng điểm eval theo Data Space và theo nhãn tin cậy; hàng đề xuất SOP/skill.

## 6. Đánh giá chấp nhận theo mức

Rút từ cách các benchmark chấm, để eval nội bộ (00 Q9) có cùng kỷ luật:

| Loại | Cách chấm | Nguồn |
| --- | --- | --- |
| Câu trả lời bảng/số | **Hợp đồng đóng băng trước**: kiểu cột, độ chính xác, đơn vị, thứ tự; bỏ qua header và thứ tự cột; so multiset có trùng; sai hình dạng = 0 | DataSpace |
| Câu trả lời tự do | Validator tất định: dung sai số, chứa chuỗi sau chuẩn hoá, Levenshtein cho tên | DAB |
| Pipeline/DAG nhiều bước | Ba mức: từng node với đầu vào vàng; nối tầng (node 0 nếu thượng nguồn sai); toàn bộ đúng | DAComp CS/CFS/SR |
| Báo cáo | Rubric phân cấp theo "đường giải", mỗi mục Accuracy có **số neo tính trước**, mục âm cho số bịa, "không có bằng chứng → 0" | DAComp (κw 0,83) |
| Dùng tool đúng | Phủ DAG vàng + đúng thứ tự phụ thuộc cứng | FDABench TOS |
| Tương tác | Số lần hỏi lại, tỷ lệ hỏi lại đúng lúc | BIRD-Interact |
| Dựng bộ case | Hai người giải mù độc lập rồi đối chiếu; ~30% "lỗi" còn lại của DeepEye-SQL là lỗi nhãn | DeepEye-SQL, DataSpace |

Mỗi Data Space có Eval Set; không mở mức tự chủ L3 cho một agent khi Eval Set của nó chưa đạt ngưỡng.

**Cổng phát hành chốt 06/10/2026:** tối thiểu 20 case đã đóng băng cho agent phòng ban, 100–150 cho Data
Space pilot; mỗi chỉ số đã duyệt có ít nhất 1 case; ít nhất 20% case là câu phải từ chối hoặc hỏi lại (để
agent "trả lời mọi thứ" không qua được); `unsupported_number_rate` = 0. Case đóng băng = câu hỏi + bảng đáp
án + hợp đồng đầu ra, hai người giải độc lập, sửa phải tạo phiên bản mới. Với 20 case, 19/20 vẫn có khoảng
tin cậy khoảng 76–99%: 20 là sàn chống phát hành với bộ test rỗng, không phải bằng chứng chất lượng.

## 7. Thay đổi so với 00 và lộ trình

Không đổi quyết định nào của 00. Bổ sung:

- Bất biến **16**: mọi task có **output contract** trước khi chạy; vật chất hoá là bước tất định.
- Bất biến **17**: kế hoạch là **DAG kiểm được tĩnh**; không chạy node chưa qua validator.
- R0 thêm: R0-10 Plan IR + validator tĩnh (thay chỗ planner trả `agent_id`); R0-11 lớp lỗi và đường
  phục hồi trong judge.
- R1 thêm: chuyên gia SQL theo mục 3.4 (value retrieval, linking hợp nhất, N-version, unit test, cổng
  confidence); Analyst Workspace tối thiểu (C + D + E).
- R2: Deep analysis dùng Plan IR; Report theo rubric có số neo; SOP có duyệt.
- R3: Monitor trong vùng an toàn; gợi ý chủ động; Context Studio theo lớp lỗi.
- R4: Skill có cổng; Agent Studio với nhãn mức tự chủ.

## 8. Câu hỏi còn mở cho anh

| ID | Câu hỏi | Khuyến nghị |
| --- | --- | --- |
| D5 | C-level có được **bật deep analysis** trực tiếp không, hay chỉ analyst? | Có, nhưng hiện kế hoạch dạng "mục lục" và hỏi xác nhận ngân sách một lần; kết quả là báo cáo, không phải thread |
| D6 | Monitor được **gửi ra kênh ngoài** (Slack/Teams/Zalo) tự động không? | Có sau khi người nhận duyệt lần đầu; mọi alert có link quay lại Ask |
| D7 | SOP do agent tự lưu có được dùng làm mẫu cho **cùng người dùng** mà chưa duyệt không? | Có cho chính người đó (nhãn "SOP cá nhân"), không cho người khác tới khi duyệt |

## 9. Nguồn

- Survey: Zhu et al., "A Survey of Data Agents: Emerging Paradigm or Overstated Hype?", arXiv 2510.23587
  <https://arxiv.org/abs/2510.23587>; repo <https://github.com/HKUSTDial/awesome-data-agents>
- Tutorial: Luo, Li, Fan, Tang, "Data Agents: Levels, State of the Art, and Open Problems", SIGMOD'26,
  arXiv 2602.04261 <https://arxiv.org/abs/2602.04261>
- DeepEye: "A Steerable Self-driving Data Agent System", SIGMOD demo 2026, arXiv 2603.28889
- DeepEye-SQL: SIGMOD 2026, arXiv 2510.17586
- AgenticData: arXiv 2508.05002v2; AOP: CIDR 2025 <https://www.vldb.org/cidrdb/papers/2025/p32-wang.pdf>
- EvoDS: KDD 2026, arXiv 2606.03841; DeepAnalyze: ICML 2026, arXiv 2510.16872; Data Interpreter: arXiv 2402.18679
- SiriusBI: VLDB 2025, arXiv 2411.06102
- ProactiveVA: VIS 2025, arXiv 2507.18165
- Benchmark: DataSpace arXiv 2608.03451 (KDD Cup 2026); DAComp ICLR 2026 arXiv 2512.04324; FDABench KDD
  2026 arXiv 2509.02473; DAB arXiv 2603.20576; AgenticDataBench arXiv 2607.01647; BIRD-Interact
  <https://bird-interact.github.io/>
- Lưu ý đọc: hình trong các PDF không xem được, chỉ có nhãn chữ; số của DeepAnalyze và ProactiveVA lấy
  qua tóm tắt tự động, chưa đối chiếu bảng gốc.
