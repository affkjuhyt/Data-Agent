# 00. Kiến trúc V2 chính thức (High-Level Architecture)

> **Đây là nguồn chuẩn.** Được chốt ngày 27/09/2026 làm High-Level Architecture V2 cho POC dầu/vàng.
> Khi một file khác trong `docs/v2/` mâu thuẫn với file này, file này thắng; hãy sửa file kia thay
> vì làm theo nó. Cách hiện thực từng phần trên repo nằm ở
> [03-target-design.md](03-target-design.md), từng use case ở [08-use-cases.md](08-use-cases.md).

## 0. Mười hai bất biến

Mọi thay đổi mã cho V2 phải giữ được các điều sau. Vi phạm một điều là lỗi thiết kế, không phải
lựa chọn phong cách.

1. **Chat chỉ là một giao diện.** Data Agent phải chạy được khi không ai hỏi (luồng chủ động, mục 10).
2. **Raw bất biến.** Không có `UPDATE` trên artifact gốc. Con người chỉ *append* correction, approval
   và tạo effective version mới.
3. **Structured không đi đường chunk.** CSV/XLSX/API/DB không bao giờ đi `chunk → embedding` để agent
   phân tích số. Số đi Observation Store.
4. **Chunk chỉ phục vụ retrieval.** Chunk không phải knowledge. Knowledge là Entity/Claim/Event/Evidence.
5. **LLM đề xuất, kiểm tra tất định và con người quyết định** (mapping, schema, conflict, claim).
6. **LLM không tính số và không đoán giá.** Tính bằng capability tất định; dự báo bằng model đã backtest.
7. **Planner không tưởng tượng tool.** Chỉ chọn capability đã đăng ký trong Capability Graph.
8. **Mọi con số có provenance** (source, artifact, release, vị trí) và **thời gian đủ bốn trục**
   (event/valid, publication, available_at, system).
9. **Point-in-time là mặc định.** Câu hỏi và feature as-of ngày X không được thấy thông tin công bố sau X.
10. **Không last-write-wins.** Mọi ghi vào dữ liệu đã duyệt dùng OCC (`expected_version`, 409 khi lệch).
11. **Không gọi LLM cho mọi cập nhật dữ liệu.** Luật tất định và thống kê lọc trước, agent chỉ điều tra
    khi đáng.
12. **Agent không tự sửa production.** Skill/workflow mới đi qua offline eval và người duyệt.

## 1. Định nghĩa V2

V2 không phải `User → Chat → Router → SQL/RAG → LLM → Answer`. V2 là:

```text
                    DATA ENVIRONMENT
                           │
             Structured + Unstructured
                           │
                           ▼
                   Data Understanding
                           │
                           ▼
                 Semantic / Knowledge
                           │
                           ▼
             Governed Effective Data
                           │
                           ▼
                    DATA AGENT CORE
              Understand → Plan → Act
                    ↑           ↓
                 Learn ← Verify
                           │
          ┌────────────────┼─────────────────┐
          ▼                ▼                 ▼
       Answer           Workflow           Alert
       Report         Data Action      Investigation
```

## 2. Các lớp

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│ EXPERIENCE LAYER                                                             │
│ Ask Data │ Analyst Workspace │ Data Studio │ Review Inbox │ Alert │ Report   │
└───────────────────────────────────┬──────────────────────────────────────────┘
                             REST / SSE / MCP
┌───────────────────────────────────▼──────────────────────────────────────────┐
│ DATA AGENT CORE                                                              │
│  Query / Goal Understanding                                                  │
│  → Entity + Metric + Time + Intent Resolution                                │
│  → Capability Graph                                                          │
│  → Planner → Task Graph                                                      │
│  → Hybrid Executor (SQL │ RAG │ Graph │ API │ Python │ ML)                   │
│  → Verifier / Evidence Judge ── replan nếu thiếu bằng chứng                  │
│  → Final Action                                                              │
└───────────────────────────────────┬──────────────────────────────────────────┘
┌───────────────────────────────────▼──────────────────────────────────────────┐
│ INTELLIGENCE / DATA API                                                      │
│ Observation │ Semantic Query │ Knowledge │ Graph │ Forecast │ Alert │ Lineage│
└───────────────────────────────────┬──────────────────────────────────────────┘
┌───────────────────────────────────▼──────────────────────────────────────────┐
│ SEMANTIC & KNOWLEDGE FABRIC                                                  │
│ Structured Semantic Layer: Entity │ Metric │ Dimension │ Unit │ Grain │      │
│                            Formula │ Series                                  │
│ Unstructured Knowledge Layer: Entity │ Claim │ Event │ Evidence │ Document │ │
│                               Relationship                                   │
│ Unified Knowledge Object Model                                               │
│ Ontology │ Knowledge Graph │ Vector Index │ Compiled Knowledge               │
└───────────────────────────────────┬──────────────────────────────────────────┘
┌───────────────────────────────────▼──────────────────────────────────────────┐
│ GOVERNANCE / EFFECTIVE DATA                                                  │
│ Data Quality → Conflict → ReviewCase → HITL → Approval                       │
│                → Correction / Override → Effective Data                      │
│ Audit │ Version │ Vintage │ Lineage │ Classification │ Provenance            │
└───────────────────────────────────┬──────────────────────────────────────────┘
┌───────────────────────────────────▼──────────────────────────────────────────┐
│ INGESTION PLANE                                                              │
│ Source Registry → Connector Runtime → Immutable Raw                          │
│ Structured: Profile → Reverse Schema → Semantic Mapping                      │
│ Unstructured: Layout/OCR/VLM → Document Structure →                          │
│               Entity/Claim/Event extraction                                  │
└──────────────────────────────────────────────────────────────────────────────┘
Nền: Kafka · k3s · PostgreSQL · ClickHouse · MinIO · Qdrant · Neo4j · Redis ·
     Keycloak · OpenBao · Langfuse · OpenTelemetry · Prometheus/Grafana
```

## 3. Hai pipeline dữ liệu khác nhau

Structured:

```text
DB / API / CSV / XLSX → Connector → Immutable Raw → Schema Profiling → Reverse Schema
→ Semantic Mapping → Normalization → DQ / Conflict → HITL nếu cần → Observation Store
→ Effective Data
```

Unstructured:

```text
PDF / DOCX / HTML → Immutable Raw → Layout / OCR / Parser → Document Structure
(Heading │ Table │ Figure) → Semantic Extraction
(Entity │ Claim │ Event │ Observation Candidate) → Knowledge → Graph │ Vector │ Evidence
```

Bảng số trong PDF đi nhánh unstructured nhưng kết thúc bằng **Observation Candidate**, rồi nhập
pipeline structured từ bước DQ.

## 4. Unified Knowledge Object Model

```text
                       ENTITY
          ┌──────────────┼───────────────┐
          ▼              ▼               ▼
     OBSERVATION       CLAIM           EVENT
          │              └───────┬───────┘
          │                      ▼
          │                   EVIDENCE → DOCUMENT
          ▼
       FEATURE → FORECAST → ALERT
```

Ví dụ: Entity `BRENT`; Observation `Brent = 71,2 USD/bbl`; Event `Supply disruption X`; Claim
`Event X tạo áp lực tăng giá`; Evidence `Report Y, trang 12`; Forecast `phân phối Brent 5 ngày`;
Alert `giảm >5% trong 5 ngày`.

## 5. Raw → Human → Effective Data

```text
SOURCE → RAW ARTIFACT [immutable forever] → EXTRACTION → MACHINE RESULT → DQ / Conflict
   ├─ PASS ───────────────────────────────────────────────┐
   └─ ISSUE → REVIEW CASE → Reviewer → Correction / Override → Checker ─┤
                                                                         ▼
                                                            APPROVED → EFFECTIVE DATA
```

Không có `Human → UPDATE raw`. Chỉ có `Human → append correction → append approval → new effective
version`.

## 6. Quan hệ dữ liệu

```text
SOURCE ─produces→ ARTIFACT ─contains→ DOCUMENT
ARTIFACT → EXTRACTION → MAPPING ─proposes→ OBSERVATION
                                ─extracts→ CLAIM, EVENT
OBSERVATION ─belongs_to→ ENTITY · ─evidence→ ARTIFACT · ─may_trigger→ REVIEW_CASE
CLAIM ─subject→ ENTITY · ─relates_to→ EVENT · ─supported_by→ EVIDENCE
EVENT ─affects→ ENTITY · ─supported_by→ EVIDENCE
REVIEW_CASE → CORRECTION | OVERRIDE → EFFECTIVE OBSERVATION → FEATURE → FORECAST
                                                            → ALERT
EFFECTIVE OBSERVATION + CLAIM + EVENT + FORECAST → ANSWER → REPORT
```

## 7. Giao tiếp giữa các service

| Kênh | Dùng cho |
| --- | --- |
| REST | Truy vấn đồng bộ |
| Kafka | Thay đổi trạng thái/dữ liệu |
| MCP | Agent gọi capability |
| SSE | Backend đẩy realtime tới UI |
| DB | Chỉ service sở hữu được truy cập |

```text
FRONTEND ─REST/SSE→ Kong → Workflow Runtime ─MCP→ MCP Gateway
                                     ├→ Observation API → ClickHouse
                                     ├→ Knowledge API   → Qdrant / Neo4j
                                     └→ ML API          → MLflow

Connector ─artifact.stored→ Extraction ─extraction.completed→ Mapping ─mapping.proposed→
Data Quality ─validation.completed→ Curation ─review.approved→ Observation
─observation.published→ { Compute → Anomaly / Forecast → Alert → Notification ; Lineage }
```

## 8. Query path

Câu hỏi "Brent giảm bao nhiêu tuần này, tại sao và 5 ngày tới thế nào?":

```text
Question → Intent Understanding (entity=BRENT, period=current_week, action=explain_change,
forecast=5d) → QuerySpec → Planner → Task Graph:

  FETCH BRENT SERIES → CALCULATE RETURN ─┬→ FIND EVENT WINDOW → SEARCH CLAIMS → RANK EVIDENCE ─┐
                                         └→ BUILD FEATURES → FORECAST ─────────────────────────┤
                                                                                  VERIFY ◄─────┘
                                                               evidence đủ? có → answer
                                                                            không → retrieve sâu hơn
```

## 9. Capability Graph

```text
EXPLAIN_PRICE_MOVEMENT → FetchTS, Calculate, FindEvents → RetrieveEvidence → RankDrivers
                       → VerifyData, VerifyEvidence
```

```yaml
capability_id: COMPARE_PERIOD
requires: [series, period]
returns: [current_value, baseline_value, absolute_change, pct_change]
implementation:
  type: deterministic
  service: analytics-service
```

LLM chỉ chọn capability. Logic tính không nằm trong prompt.

## 10. Proactive Data Agent (kịch bản quan trọng nhất)

```text
NEW DATA → Ingest → Semantic Mapping → DQ → Conflict?
   ├─ không ─────────────────────────────┐
   └─ có → ReviewCase → Notify Human → Approve ─┤
                                                ▼
Effective Data → Dependency Graph → Recompute → Change / Anomaly Detection → Significant?
   → có → Autonomous Investigation (structured, documents, events, graph expansion,
          evidence verification) → Alert Candidate → Notify Analyst
```

Bỏ chat đi mà luồng này vẫn chạy thì V2 mới là Data Agent.

## 11. Mười bốn use case POC

| ID | Use case | Data | Kết quả |
| --- | --- | --- | --- |
| UC01 | Onboard structured source | API/CSV/DB | Observation |
| UC02 | Onboard unstructured source | PDF/DOCX/Web | Knowledge |
| UC03 | Reverse schema | Structured | Mapping proposal |
| UC04 | Semantic extraction | Unstructured | Entity/Claim/Event |
| UC05 | DQ | Cả hai | Quality result |
| UC06 | Conflict resolution | Đa nguồn | Reconciliation |
| UC07 | HITL | Cả hai | Effective data |
| UC08 | Structured analytics | TS/DB | Numerical answer |
| UC09 | Knowledge investigation | Document | Evidence answer |
| UC10 | Hybrid investigation (golden use case) | Cả hai | Analysis |
| UC11 | Forecast | Structured | Forecast object |
| UC12 | Proactive anomaly/alert | Cả hai | Alert |
| UC13 | Historical/as-of | Cả hai | Reproducible answer |
| UC14 | Skill improvement | Agent trace | Skill candidate |

Chi tiết, vấn đề cần xử lý và cách hiện thực từng UC: [08-use-cases.md](08-use-cases.md).

## 12. Stack

| Thành phần | Công nghệ |
| --- | --- |
| API, contract | FastAPI, OpenAPI + Pydantic |
| Event | Kafka |
| Agent tools | MCP |
| Raw object | MinIO |
| Metadata, review, registry | PostgreSQL |
| Time-series (observation) | ClickHouse |
| Vector | Qdrant |
| Graph | Neo4j |
| Cache | Redis |
| Scheduler | K8s CronJob, sau đó Dagster |
| Extraction | Docling + OCR/VLM |
| Query IR | JSON Schema/Pydantic (QuerySpec) |
| SQL compilation | SQLGlot + compiler riêng |
| Model registry | MLflow |
| Tracing | Langfuse (agent), OpenTelemetry (service) |
| Monitoring | Prometheus/Grafana |
| IAM, secret | Keycloak, OpenBao |

Không đổi stack hàng loạt: phần lớn đã chạy trong cụm (01, mục 3–4).

## 13. Mười vấn đề phải giải trước khi tăng độ phức tạp

| Vấn đề | Nếu bỏ qua |
| --- | --- |
| Semantic identity | Gold/Brent/WTI bị hiểu sai (spot vs futures, Dated vs ICE) |
| Temporal semantics | Dùng dữ liệu tương lai |
| Provenance | Không biết số từ đâu |
| Schema drift | Nạp sai mà không báo |
| Conflict semantics | Sinh xung đột giả hàng loạt |
| Effective data | Agent không biết tin bản nào |
| HITL authority | Người không đủ quyền duyệt dữ liệu |
| Cross-modal join | Structured và tài liệu không nối được |
| Evidence sufficiency | LLM kết luận vượt bằng chứng |
| Deterministic eval | Không biết V2 tốt lên hay kém đi |

## 14. Thứ tự triển khai

```text
STEP 1  Canonical Data Model (Entity / Observation / Claim / Event / Evidence)
STEP 2  Source + Immutable Raw
STEP 3  Structured pipeline        STEP 4  Unstructured pipeline
STEP 5  Semantic Fabric
STEP 6  DQ / Conflict / HITL
STEP 7  Effective Data
STEP 8  Semantic Operators
STEP 9  Planner + Capability + Task Graph
STEP 10 Verifier + Evaluation
STEP 11 Alert        STEP 12 Forecast        STEP 13 Skill
```

**Multi-agent không phải nền móng; ngữ nghĩa dữ liệu mới là nền móng.** Chưa có Observation /
Claim / Event / Evidence / Effective Data / Provenance mà đã dựng Supervisor + 10 agent thì chỉ
được một chatbot phức tạp hơn.

## 15. Kịch bản nghiệm thu POC (27 bước)

1. Một nguồn Brent mới xuất hiện.
2. Connector lấy dữ liệu.
3. Raw được lưu bất biến.
4. Schema nguồn thay đổi.
5. Reverse Schema phát hiện.
6. Mapping mới được đề xuất.
7. DQ phát hiện observation bất thường.
8. Hệ thống đối chiếu nguồn khác.
9. Không tự giải quyết được.
10. ReviewCase được tạo.
11. Người có thẩm quyền nhận notification.
12. Người xác định: spot ≠ futures.
13. Người sửa mapping, không sửa raw.
14. Checker duyệt.
15. Effective Observation mới được publish.
16. Dữ liệu dẫn xuất được tính lại.
17. Hệ thống phát hiện Brent giảm >5%.
18. Không cần ai hỏi, agent tự mở investigation.
19. Structured engine xác định mức giảm.
20. Document agent tìm event/report.
21. Graph nối Event ↔ Brent ↔ Evidence.
22. Verifier kiểm số, độ tươi, bằng chứng.
23. Alert được tạo.
24. Analyst nhận notification.
25. Analyst hỏi "So với 3 lần gần nhất?".
26. Agent chạy phân tích hybrid structured + unstructured.
27. Trả bảng + chart + evidence + lineage.

Demo trọn chuỗi này chứng minh: ingestion → understanding → governance → HITL → effective data →
reasoning → proactive intelligence → human interaction. Cách dựng từng bước bằng dữ liệu thật:
[06-evaluation-plan.md](06-evaluation-plan.md) mục 6.
