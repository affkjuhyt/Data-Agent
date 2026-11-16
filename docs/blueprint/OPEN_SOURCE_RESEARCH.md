# OPEN_SOURCE_RESEARCH — REA: Research → Evaluate → Adapt

> Ngày nghiên cứu 12/11/2026. Mọi số liệu (sao, ngày release, giấy phép, đường dẫn file) được đọc trực tiếp từ GitHub,
> PyPI hoặc docs của dự án ngày hôm đó; URL và chỗ không truy cập được ghi trong ba báo cáo gốc ở
> [evidence/oss-agents.md](evidence/oss-agents.md), [evidence/oss-data.md](evidence/oss-data.md),
> [evidence/oss-docs.md](evidence/oss-docs.md). Nghiên cứu trước đó của repo (Airbyte, OpenMetadata, ragflow, DataAgent,
> gpt-researcher, RD-Agent, Memori, WrenAI legacy) ở `docs/research/` không lặp lại ở đây; giấy phép của chúng ở
> `docs/research/licences.md`. Không clone và chạy repo nào trong lượt này: "đã đọc source" nghĩa là đã đọc file
> qua raw.githubusercontent.com, không phải đã chạy.

## 1. Phase R — tóm tắt từng nguồn

Cột "Giải quyết việc gì của ta" trỏ tới ID vấn đề ở [00-ASSESSMENT.md](00-ASSESSMENT.md) §2.2.

### 1.1 Data agents / text-to-SQL

| Repo | Phạm vi thật | Module đáng đọc (đường dẫn đã mở) | Giải quyết việc gì của ta | Bảo trì / giấy phép | Tích hợp (1–5) | Quyết định |
| --- | --- | --- | --- | --- | --- | --- |
| **DB-GPT** v0.8.2 | App chatbot dữ liệu nguyên khối: AWEL DAG runtime, chat_db (text2sql một lượt, không few-shot), GPT-Vis | `dbgpt-serve/.../db_summary_client.py` (schema linking hai mức bảng/cột trên vector store), `dbgpt-core/.../vis/tags/vis_chart.py` (enum 8 loại chart do LLM chọn), `chat_db/auto_execute/out_parser.py` (kiểm SQL chỉ bằng `sqlparse` type) | A-2 (ý tưởng linking hai mức); không hơn cái đã có | 20,3k★, push 11/11/2026, release theo quý; **MIT**; v0.8.2 vá RCE/traversal/CORS | 4 | **Reference** |
| **WrenAI** 0.18.0 | Semantic engine Rust (DataFusion) + MDL + CLI/SDK; "GenBI Classic" đã legacy; SDK pydantic-ai | `core/wren-mdl/mdl.schema.json` (relationship có cardinality, cube, RLS/CLS trong model), `core/wren-core-py/README.md` (`transform_sql`, `dry_run`, `strict_mode`, `denied_functions`), `sdk/wren-pydantic` (`wren_dry_plan`, `wren_recall_queries`) | A-2, A-10 (dry-plan + strict mode = cổng kiểm tất định; recall verified query làm few-shot) | 18,1k★, release hàng tuần; **Apache-2.0** cho `core/sdk/skills`, CC BY 4.0 docs, AGPL giữ chỗ; RLS/CLS quản trị người dùng là commercial | 3 | **Extract** (shape MDL, cổng dry-plan); Wrap `wren-core-py` chỉ khi cần dialect ngoài Postgres |
| **vanna** 2.0.2 | Training store DDL/docs/SQL pairs + loop sinh SQL; 2.0 là agent framework riêng | `src/vanna/legacy/base/base.py` (`train`, `get_sql_prompt`, `extract_sql` regex, `is_sql_valid` không ai gọi), `core/agent/agent.py` | A-10 (xác nhận thiết kế `context.example_query`); gate `allow_llm_to_see_data` | **Archived 29/03/2026**; MIT | 2 (ý tưởng) / 4 (phụ thuộc) | **Reference** |
| **pandas-ai** 3.0.0 | Semantic layer YAML + sinh mã pandas + sandbox Docker | `pandasai/core/code_generation/code_validation.py` (chỉ 1 kiểm AST), `code_execution/environment.py` (`exec` không giới hạn builtins), `extensions/sandbox/docker/docker_sandbox.py` (SQL chạy ở host, kết quả CSV đưa vào container, `network_disabled`, **không giới hạn CPU/RAM**), `helpers/dataframe_serializer.py` | N-3 (biên sandbox: credential không vào container), N-10 (format bảng cho LLM) | Không commit từ 10/2025; MIT trừ `pandasai/ee/` proprietary | 3 | **Extract** (biên sandbox, serializer); **Reject** phụ thuộc |
| **awesome-data-agents** | Danh mục + thang L0–L5 + benchmark | README; BIRD, BIRD-Interact, Spider 2.0, DABstep, KramaBench, DataSpace | Khung đo L; chọn benchmark tham chiếu | 800★, không giấy phép | 1 | **Reference** |
| SQLBot (dataease) | ChatBI đầy đủ, workspace, thuật ngữ, calibration SQL | — | Danh sách tính năng đối thủ | 7,1k★; **GPLv3 + ràng buộc FIT2CLOUD** | 5 | **Reject** (giấy phép) |
| NVIDIA auto-ontology | Ontology (term, synonym, mapping cột, join suy luận) trên Postgres+pgvector, MCP `check_answerable` | README | A-7/RES-04: kiểm "trả lời được không" trước khi sinh | 11,7k★ sau 6 tháng; Apache-2.0; không nhận PR ngoài | 3 | **Reference / watch** |
| mnemiq | "decider" tất định: read-only, object được phép, compile theo dialect, qua EXPLAIN | README | A-2: thang kiểm sạch nhất | 81★; Apache-2.0 | 2 | **Reference** |

Số benchmark đã công bố (chỉ để chọn mục tiêu, không phải số của ta): Spider 2.0-Lite #1 76,23 %; BIRD test #1 83,40 %
(người 92,96 %); BIRD-Interact Full best ~17 % (a-Interact GPT-5 25,52 normalized); DABstep hard best 14,55 %; KramaBench
55 %; DataSpace: đổi harness chênh 15,36 điểm. Kết luận cho ta: thi ở BIRD-dev vô nghĩa (model đơn > 84 %); tương tác
nhiều lượt và câu nhiều nguồn là chỗ còn khó, và harness quan trọng ngang model.

### 1.2 Ingestion / quality / semantic / metadata / orchestration / sandbox

| Thành phần | Phạm vi thật | Giải quyết việc gì của ta | Bảo trì / giấy phép | Tích hợp | Quyết định |
| --- | --- | --- | --- | --- | --- |
| **dlt** 1.32.0 | extract/normalize/load; `sql_database` (backend pyarrow/connectorx), `rest_api` khai báo (paginator, auth, placeholder cha-con), schema contract evolve/freeze/discard, incremental `range_start/end`, load package `_dlt_loads` | D-5 (ngữ nghĩa cursor/biên/dedup/backfill chuẩn), D-4 (REST) | 6,1k★, hàng tháng, commit 11/11; **Apache-2.0**; dltHub + MSSQL change-tracking là commercial | 3 (engine sau driver) / 4 (sở hữu bảng) | **Extract** ngữ nghĩa incremental + completeness marker; **Wrap `rest_api`** khi có nguồn SaaS. Không để dlt sở hữu bảng (mất RLS, `_dlt_*` cột, đổi tên cột) |
| **Great Expectations Core** 1.24.1 | Context → Source → Asset → Batch → Expectation → Suite → Validation → Checkpoint; SQLAlchemy | D-8 (từ vựng expectation, `mostly`, `row_condition`, `row_count_to_equal_other_table`) | 11,9k★, 2 tuần/release; **Apache-2.0**; ClickHouse + MySQL ở tier "không bảo đảm" | 3 | **Extract** từ vựng + result shape; giữ luật SQL của ta |
| soda-core v4 | Data contract YAML | — | **ELv2** | — | **Reject** (giấy phép) |
| pointblank | DQ nhỏ, pushdown qua Ibis | D-8 nếu muốn engine thư viện | 507★; MIT | 2 | **Reference** |
| pandera | schema cho dataframe | N-3 (kiểm output sandbox) | MIT | 2 | **Reference** (có thể Adopt trong sandbox sau) |
| **DuckDB** 2.0.0 LTS | SQL phân tích nhúng trên Arrow/Parquet/pandas; postgres extension READ_ONLY; checklist khoá cấu hình | N-10 (tính trong sandbox), cross-source join trên snapshot, Parquet tái lập | 43k★; LTS tới 10/2027; **MIT**; Python ≥ 3.11 | 2 | **Adopt** (sandbox + snapshot reader), khoá `enable_external_access=false`, `lock_configuration=true` |
| **SQLGlot** 30.31.0 | parser/transpiler/optimizer; `lineage`, `qualify(schema, on_qualify)` | D-6 (lineage cột), A-2 (qualify theo catalog cho phép), A-9 | 9,8k★, nhiều release/tuần; **MIT**; **ta đang pin 25.24.5 (10/2024), chậm 5 major** | 1 | **Adopt** (nâng 30.x) + thêm lineage/qualify vào `sql_guard` |
| **pyarrow** 26.0.1 | Parquet `write_page_checksum`, metadata schema | D-1 (snapshot bất biến có checksum) | Apache-2.0; đã pin | 1 | **Adopt** |
| **MetricFlow ≥ 0.209 / dbt-semantic-interfaces** | Spec Pydantic SemanticModel/Entity/Dimension/Measure/Metric (simple, ratio, cumulative, derived, conversion) | A-8 (chuẩn import/export, validator) | **Apache-2.0 cả hai** (BSL kết thúc 10/2025); MetricFlow engine cần dbt project | 2 (spec) / 5 (engine) | **Adopt spec** làm schema YAML; **Reject** engine |
| Apache Ossie (ex-OSI) | JSON schema trao đổi semantic | Tương lai | Apache-2.0, chưa 1.0 trong repo | 2 sau | **Reference** |
| Cube Core / Malloy | Engine semantic thứ hai (Node/Rust, TS) | — | Apache/MIT | 4 | **Reference** |
| **DataHub** 1.6 / acryl-datahub 1.7 | GMS Java + Kafka/ZK/SR + ES + MySQL; ingestion Python 150+ nguồn | Mô hình aspect/URN; stateful ingestion | Apache-2.0 toàn bộ; Lite "không production" | 5 (server) / 3 (ingestion) | **Reference**; **Reject** server. (Khác OpenMetadata: ở đây lý do là trọng lượng, không phải giấy phép) |
| **LangGraph** 1.2.20 | checkpoint theo superstep, `interrupt()`, Postgres saver (bảng riêng, msgpack) | A-5 (ý tưởng interrupt có kiểu, retention checkpoint) | 44,8k★; MIT; LangSmith self-host chỉ Enterprise | 4 | **Reject**; Extract 2 ý; **Reference** pydantic-graph (đã có qua pydantic-ai 2.x) |
| **E2B** | Firecracker/KVM + control plane riêng (PG, Redis, ClickHouse); self-host "Embed" một máy | — | Apache-2.0; multi-node Enterprise | 5 | **Reject** |
| **gVisor** | RuntimeClass `runsc` cho containerd (k3s có sẵn), systrap chạy trong VM | N-3/P (biên syscall cho sandbox pod, giữ NetworkPolicy/RO fs) | 20k★, release hàng tuần; Apache-2.0 | 2 | **Adopt** |
| Kata / microsandbox / Piston / nsjail | VM per pod / beta / process jail | — | Apache/MIT | 3–4 | Kata **Reference** (bước nâng); còn lại **Reject**; lấy giới hạn mặc định của Piston |

### 1.3 Document intelligence

| Thành phần | Phạm vi thật | Giải quyết việc gì của ta | Bảo trì / giấy phép | Tích hợp | Quyết định |
| --- | --- | --- | --- | --- | --- |
| **Docling** 2.150.0 | `DocumentConverter` → backend (docling-parse) → layout model (DocLayNet) → **TableFormer** (`TableCell` có bbox, span, header) → OCR (RapidOCR/Tesseract/EasyOCR, `iso:` code) → reading order; `DoclingDocument` có `ProvenanceItem(page_no, bbox, charspan)`, `SectionHeaderItem.level` + `HeadingHierarchyOptions`; Hierarchical/HybridChunker; CPU ~1 trang/s (4 thread), GPU tuỳ chọn | U-1, U-2, U-3, U-6 | 70,4k★, ~10 release/3 tuần; **MIT**; LF AI & Data; +2–3 GB image (torch CPU) | 2 | **Adopt (wrap)** làm backend trích; giữ chunker Điều/Khoản của ta chạy trên `DoclingDocument` |
| docling-graph 1.9.2 | KG từ DoclingDocument qua LLM template Pydantic | — | MIT | 3 | **Reference** (sớm) |
| **Unstructured** 0.27.37 | partition hi_res (yolox) + table-transformer → `text_as_html`, không bbox ô; OSS "không cho production", không GPU, không VLM | Đối chứng bake-off; fallback DOCX/PPTX/EML | Apache-2.0; telemetry mặc định bật | 2 (gọi) / 4 (bảng có provenance) | **Reference / đối chứng**; **Reject** làm nền |
| **GraphRAG** 3.3.0 | ≥ 2 lời gọi LLM/chunk + tóm tắt entity/quan hệ + report theo community | — | MIT; **maintenance mode**; LazyGraphRAG không có trong OSS | 5 | **Reject** (chi phí GPU) |
| LightRAG / nano-graphrag | nhẹ hơn, Postgres + Qdrant + bge-m3 | Chỉ khi có câu hỏi thực thể xuyên tài liệu | MIT | 3 | **Reference** |
| **LlamaIndex** core 0.14.25 | NodeParser (SentenceWindow, Hierarchical + AutoMerging ~0,525 preference), SQLJoinQueryEngine (vi phạm bất biến 16) | U-3 (parent/child + merge theo tỷ lệ) | MIT; LlamaParse commercial | 4 | **Extract** ý; không phụ thuộc |
| **Haystack** 3.5.0 | pipeline component; `DoclingConverter`, `DocumentSplitter(split_by=function)` | U-3 (pattern splitter theo hàm) | Apache-2.0 | 3 | **Reference** |
| OCR tiếng Việt | Tesseract `vie` (CER 0,14 VieBookRead; fine-tune 0,09); RapidOCR PP-OCRv6 `vi` (Apache, onnxruntime, mặc định của Docling); PaddleOCR (nặng); EasyOCR (CER 0,25); **Surya weights OpenRail-M giới hạn doanh thu → Reject**; docTR có vocab vi nhưng chưa có checkpoint | U-1 (scan) | — | — | Bake-off **Tesseract vs RapidOCR v6** CPU; giữ Tesseract làm baseline |
| Benchmark | OmniDocBench (code Apache; data chỉ nghiên cứu), DocLayNet (CDLA-Permissive-1.0), FinTabNet (CDLA-Permissive; bảng tài chính), PubTables-1M, UIT-DODV (giấy phép chưa rõ) | E2 | — | — | Dùng **evaluator code** của OmniDocBench + FinTabNet làm sanity check; **bộ 30 tài liệu tự dựng** là chuẩn chính |

## 2. Phase E — bảng so sánh theo 10 tiêu chí (các ứng viên được Adopt/Extract)

| Tiêu chí | Docling | DuckDB | SQLGlot 30 | dbt-semantic-interfaces | GX Core (từ vựng) | dlt (ngữ nghĩa + rest_api) | gVisor | WrenAI (MDL/dry-plan) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Problem fit | U-1/2/3/6 | N-10, D-1 | D-6, A-2 | A-8 | D-8 | D-5, D-4 | N-3 | A-2, A-10 |
| Technical maturity | cao, release dày | rất cao, LTS | rất cao | cao (spec) | cao | cao | rất cao | cao (engine Rust) |
| Accuracy (bằng chứng) | TableFormer TEDS 93,6 (card); FinTabNet 0,89; **chưa có số tiếng Việt** | n/a | n/a | n/a | n/a | n/a | n/a | không công bố |
| Extensibility | pipeline option, enrichment | extension | dialect | Pydantic | expectation tuỳ biến | source/resource | RuntimeClass | SDK |
| Integration complexity | 2 | 2 | 1 | 2 | 3 (chỉ lấy từ vựng → 1) | 3 | 2 | 3 |
| Performance | ~1 trang/s CPU; GPU tuỳ chọn | in-process, cap RAM/thread | ms | ms | — | pyarrow backend | chậm syscall-heavy | Rust |
| Security | model local, offline (`DOCLING_ARTIFACTS_PATH`) | khoá config, no egress | lenient parser → cần RO role | — | — | credential qua provider riêng | +kernel boundary | strict_mode |
| License | MIT | MIT | MIT | Apache-2.0 | Apache-2.0 | Apache-2.0 (dltHub commercial) | Apache-2.0 | Apache-2.0 (path-based) |
| Maintenance | LF AI & Data, hàng tuần | hàng tháng | nhiều/tuần | theo dbt | 2 tuần | hàng tháng | hàng tuần | hàng tuần |
| Reuse strategy | Wrap | Adopt | Adopt | Adopt spec | Extract | Extract/Wrap | Adopt | Extract |

Số "published" vs "tự đo": mọi số trong bảng là published. Ta **chưa tự đo** bất kỳ ứng viên nào; đó là việc của ba PoC
ở §4.

## 3. Phase A — chiến lược thực dụng

### 3.1 Dùng trực tiếp (Adopt / Wrap)

| Thành phần | Dùng cho | Điều kiện | Việc phải tự viết |
| --- | --- | --- | --- |
| Docling (wrap) | `worker_document` trích PDF/scan/DOCX → `DoclingDocument` | PoC-1 đạt ngưỡng (§4) | Adapter `DoclingDocument → document.element/table/chunk`; regex Điều/Khoản thành `SectionHeader`; cache model trên PVC; giới hạn RAM pod 4 GB |
| DuckDB 2.0 LTS | sandbox pod (SQL trên Arrow/Parquet), reader snapshot Parquet trong worker-agent | PoC-2 | Lớp mở kết nối với checklist khoá; không cho `ATTACH` nguồn sống |
| SQLGlot 30.x | `sql_guard` (qualify theo catalog), `libs/sql_guard/lineage.py` | Nâng pin, chạy test sqlgen/guard | Map `on_qualify` → catalog cho phép; lineage → `pipeline.lineage_edge` |
| pyarrow Parquet | snapshot dataset version bất biến trên MinIO | — | Writer có `write_page_checksum`, metadata provenance, SHA-256 |
| dbt-semantic-interfaces | validator + schema cho `semantic_yaml.py` | — | Map hai chiều với `context.*` |
| gVisor RuntimeClass | `python-sandbox` pod | Node k3s cài `runsc` (Ansible) | Manifest `runtimeClassName: gvisor`; test pyarrow mmap |

### 3.2 Chỉ học cách làm (Extract / Reference)

WrenAI (MDL relationship có cardinality, RLS trong model, dry-plan + strict_mode), dlt (biên incremental đóng/mở, backfill
`end_value`, `_dlt_loads`), GX (từ vựng expectation + `mostly` + `row_condition` + result shape), LangGraph (`interrupt()`
có kiểu, retention checkpoint), pandas-ai (SQL ở host, kết quả file vào sandbox; serializer bảng), LlamaIndex (parent/
child + auto-merge), Haystack (`split_by=function`), auto-ontology (`check_answerable`), mnemiq (thang kiểm), DataHub
(aspect/URN, stateful ingestion), OpenMetadata/Airbyte (đã có ở `docs/research/`).

### 3.3 Tự xây (vì không có OSS phù hợp hoặc OSS vi phạm bất biến)

Semantic compiler (giữ; MetricFlow cần dbt), governed-first router + AnswerEnvelope, rule-audit SQL khám phá, chart
validator, insights tool tất định, DQ gate, bảng theo version + view, outbox DLQ, bộ vàng và scorer theo hợp đồng.

### 3.4 Thay thế

| Hiện tại | Thay bằng | Lý do |
| --- | --- | --- |
| pdfminer.six + pypdfium2 cho layout/bảng | Docling (giữ pypdfium2 cho ảnh trang nếu cần) | U-1/U-2 |
| JSON `list[list]` 10k dòng ship vào sandbox | Arrow/Parquet + DuckDB | N-10 |
| FTS `'simple'` | cột folded + `unaccent` (+ giữ `'simple'`) | U-4 |
| sqlglot 25.24.5 | 30.x | 5 major chậm |
| `sql_scan`/sqlparse ở chỗ còn sót | sqlglot qualify | thống nhất |

### 3.5 Thừa (xem STRATEGY §6 để quyết Remove/Defer)

Kafka, ClickHouse, Langfuse trong deploy; 4 connector enum; schema `exec`; knowledge page composer; entity-link; forecast
ledger/Monte Carlo; `.msg`/audio intake; `AGENT_PYTHON_SANDBOX=local`.

### 3.6 Chưa cần ở MVP

GraphRAG/LightRAG, docling-graph, DataHub ingestion, dlt `rest_api` (đến khi có nguồn SaaS), Kata, WrenAI engine, Ossie
exporter, MetricFlow cumulative/conversion, MCP server ra ngoài (D12 R2).

### 3.7 Agent autonomy vs pipeline tất định vs duyệt người

| Việc | Cơ chế | Lý do |
| --- | --- | --- |
| Nạp, quét, profile, DQ, hash, drift, snapshot | **Tất định** (worker, Temporal) | Có đáp án đúng; LLM không thêm gì |
| Đề xuất mô tả cột, join, metric, luật DQ, bước prep, manifest REST | **LLM đề xuất → kiểm tất định → người duyệt** | Bất biến 5/12 |
| Câu hỏi một metric/kỳ/chiều | **Governed tất định** (rule resolver → compiler) | 20/20, 536 token |
| Câu nhiều bước, lai số + tài liệu, "vì sao" | **Agent trong plan DAG kiểm tĩnh**, ngân sách, hỏi lại theo policy | Chỗ duy nhất agent thêm giá trị đo được |
| Ghi vào tài sản ngữ cảnh, gửi kênh ngoài, sửa pipeline active | **Luôn duyệt người** | 01 §2.2 |
| Monitor | Luật/thống kê tất định trước; LLM chỉ viết giải thích | Bất biến 11 |

## 4. Giả thuyết kỹ thuật và PoC bắt buộc trước khi tích hợp

| PoC | Giả thuyết | Thiết kế | Đạt khi | Thời lượng |
| --- | --- | --- | --- | --- |
| **PoC-1 Docling** | Docling trích bảng và tiêu đề tiếng Việt tốt hơn pipeline hiện tại trên PDF text, không tệ hơn trên scan | 30 tài liệu (10 pháp quy VN, 8 scan VN 300 dpi + 2 điện thoại, 6 báo cáo tài chính trong đó 3 trang FinTabNet, 3 DOCX, 3 lệch/xoay); ground truth: ô bảng theo schema FinTabNet, thứ tự đọc, OCR 5 trang/scan, cây Chương/Điều/Khoản. Hệ: A hiện tại; B Docling (docling-parse, TableFormer ACCURATE, heading hierarchy; B1 Tesseract vie+eng, B2 RapidOCR v6 vi; CPU 4 thread và CUDA); C Unstructured hi_res. Metric: TEDS/TEDS-S (evaluator OmniDocBench, Apache), cell F1, reading-order edit distance, CER/WER có dấu và bỏ dấu, heading F1, s/trang, RSS/VRAM | cell F1 ≥ 0,85 PDF text, ≥ 0,70 scan; ED ≤ 0,10; CER không tệ hơn Tesseract; ≤ 3 s/trang CPU | 1 tuần; `make docling-bakeoff` trong image worker-document; kết quả vào `model/eval/` |
| **PoC-2 DuckDB trong sandbox** | DuckDB trên Arrow giảm thời gian và RAM so với JSON 10k dòng + pandas, và khoá được cấu hình | 20 phân tích mẫu (join 2 dataset, window, share, YoY) trên DS-AW 10k dòng; đo thời gian end-to-end, RSS, kích thước payload; thử escape: `read_csv('/etc/passwd')`, `INSTALL httpfs`, `SET threads` sau lock | ≥ 2× nhanh, payload giảm ≥ 5×; mọi escape bị từ chối | 3 ngày |
| **PoC-3 Rule-audit SQL khám phá** | Hai kiểm tất định (cột đo ∈ measure của metric có trong catalog; mọi giá trị lọc trong câu hỏi xuất hiện trong SQL/kết quả) loại bỏ "sai bảng" mà không tăng từ chối sai | 61 câu DS-AW + 40 câu NYC TLC qua `answer_by_sql` có/không audit | "sai bảng" 2/20 → 0; từ chối sai tăng ≤ 5 điểm | 3 ngày |
| PoC-4 unaccent | Cột folded + `unaccent` nâng recall@1 nhóm không dấu | 10 case khong-dau + 26 tieng-anh của bộ 156 | khong-dau ≥ 0,7 recall@1, nhóm khác không giảm > 2 điểm | 1 ngày |
| PoC-5 sqlglot 30 nâng cấp | Không regression ở `sql_guard`, `sqlgen`, compiler | chạy 3 suite unit | xanh | 1 ngày |

## 5. Những gì không nên sao chép (tổng hợp)

- Kiểm SQL chỉ bằng `sqlparse` type (DB-GPT, vanna); regex `extract_sql`; `exec` không giới hạn builtins (pandas-ai).
- Để agent `ATTACH` nguồn sống trong DuckDB; để dlt sở hữu bảng đích (`_dlt_*`, đổi tên cột).
- Normalizer dẹt JSON thành bảng con (dlt) cho dữ liệu đã có cấu trúc.
- `text_as_html` làm đại diện bảng (Unstructured); Markdown làm đơn vị truy hồi (Docling export).
- Checkpoint msgpack riêng, message type LangChain (LangGraph); runtime AWEL (DB-GPT).
- Indexing GraphRAG trên GPU chung; VLM pipeline Docling (không có tiếng Việt).
- Memory LanceDB của Wren (ta có `context.example_query`).
