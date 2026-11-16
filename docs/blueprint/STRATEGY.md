# STRATEGY — Định hướng sản phẩm và ưu tiên (Part 4)

> Ngày 12/11/2026. Kế thừa `docs/v3/00-product-strategy.md` (Q1–Q10, bất biến 1–17) và `02-market-solution.md`; tài liệu
> này **không đổi** mười quyết định Q1–Q10, nó đổi **thứ tự và phạm vi** sau khi có bằng chứng đo được
> ([00-ASSESSMENT.md](00-ASSESSMENT.md)). Chỗ nào trái với `04-backlog.md` thì tài liệu này thắng cho đến khi backlog
> được cập nhật ([IMPLEMENTATION_BACKLOG.md](IMPLEMENTATION_BACKLOG.md) §0 nói cách hợp nhất).

## 1. Mười câu hỏi chiến lược — trả lời

| # | Câu hỏi | Trả lời |
| --- | --- | --- |
| 1 | Data Agent giải bài toán kinh doanh nào? | **Một con số đúng, có nguồn, cho người không viết SQL, trên dữ liệu không rời doanh nghiệp.** Hệ quả: giảm thời gian "hỏi analyst → đợi → nhận bảng" và giảm số sai lệch giữa các báo cáo. Mọi tính năng khác (phân tích sâu, monitor, tài liệu) chỉ có giá trị khi câu một con số đã đúng |
| 2 | Khác chatbot + RAG + text-to-SQL ở đâu? | (a) **Lớp ngữ cảnh có quản trị**: metric có version/chứng nhận, Data Space, verified query, quyền kiểm trước khi sinh; (b) **mỗi số có nhãn và receipt tái lập**; (c) **bảng trong tài liệu là dữ liệu có cấu trúc**, không phải chữ; (d) **hỏi lại thay vì đoán** theo policy đo được. Text-to-SQL chỉ là đường dự phòng có nhãn Khám phá |
| 3 | Hành trình mang giá trị sớm nhất? | Steward duyệt 15–25 metric trên một kho đã có → quản lý hỏi "doanh thu tháng trước, theo vùng, so cùng kỳ" → nhận ValueCard + chart + nhãn GOVERNED + receipt → bấm "xem cách tính". Đã chạy được trên AdventureWorks (20/20); chưa chạy trên dữ liệu thật ngoài AW |
| 4 | Làm đầy đủ Data / Ask / Analytics ngay? | **Không.** Thứ tự: Data nền (version, DQ gate) → Ask governed-first → Tài liệu có bảng → Analytics tất định tối thiểu. Analytics "agent tự khám phá" và Monitor lùi sau cổng P0 |
| 5 | Tính năng nền cho tính năng khác? | Dataset có version + snapshot Parquet; AnswerEnvelope (nhãn + receipt một chỗ); plan DAG kiểm tĩnh có ngân sách; element/table của tài liệu; bộ vàng trên dữ liệu thật + gate |
| 6 | Dùng OSS cho gì? | Docling (trích tài liệu), DuckDB (tính trong sandbox + snapshot), sqlglot 30 (lineage/qualify), dbt-semantic-interfaces (schema YAML), gVisor (sandbox), pyarrow; từ vựng GX; ngữ nghĩa incremental của dlt. Chi tiết [OPEN_SOURCE_RESEARCH.md](OPEN_SOURCE_RESEARCH.md) §3 |
| 7 | Đang over-engineering ở đâu? | §6 dưới: schema `exec`, knowledge page composer, entity-link/consolidation, forecast ledger/Monte Carlo, Kafka/ClickHouse/Langfuse, 4 connector enum, 7 loại lịch Temporal, hai đường trả lời có hai bộ cơ chế |
| 8 | MVP nhắm L1, L2 hay L3? | **L2 vững, Proto-L3 có giám sát cho câu nhiều bước**, theo thang awesome-data-agents. Lý do đo được: governed (L2) 20/20 với 536 token; agentic 17/20 với 22,6k token và thua ở gate của chính nó; BIRD-Interact cho thấy tương tác là chỗ mọi hệ thống còn < 20 %. L3 đầy đủ (agent tự dàn pipeline) chỉ cho deep analysis của analyst, sau cổng P1 |
| 9 | Triển khai nhiều khách mà không tùy chỉnh từng lần? | Data Space là đơn vị cấu hình (đã có); onboarding checklist 10–20 câu → nguồn → metric → eval (R1-15 đã có màn); bộ vàng theo Data Space là cổng. Việc còn thiếu: dataset contract tối thiểu, YAML semantic chuẩn (dbt-semantic-interfaces), gói ngôn ngữ vi/en, không còn tiếng Việt cứng trong harness |
| 10 | Bằng chứng để quyết đầu tư tiếp? | Ba thí nghiệm E1–E3 ở [00-ASSESSMENT.md](00-ASSESSMENT.md) §10.6 và cổng P0 ở §5 dưới; tất cả đo trên NYC TLC (và SEC nếu không cắt), có checksum snapshot, giải mù hai người |

## 2. Định vị chốt

**Governed Analytics Agent cho doanh nghiệp on-prem**: hỏi bằng tiếng Việt/Anh trên kho dữ liệu, file bảng và tài liệu
của tổ chức; mọi con số mang nhãn tin cậy và receipt tái lập; dữ liệu và model không rời hạ tầng khách. Ba câu bán hàng
của 02 §4.1 giữ nguyên; bỏ chữ "agent tự biến hoá" khỏi thông điệp MVP (T2/T3 của 00 §6 lùi R4).

## 3. Ma trận ưu tiên

Chấm: Impact (I), Dependency (D = cái gì phải có trước), Effort (E: S < 1 tuần-người, M 1–3, L > 3), Risk (R), Reuse (U),
Đo bằng (Q). ID tính năng `F-*` dùng lại ở [IMPLEMENTATION_BACKLOG.md](IMPLEMENTATION_BACKLOG.md).

### P0 — Must have (không có thì không chứng minh được giá trị)

| ID | Tính năng | I | D | E | R | U | Q |
| --- | --- | --- | --- | --- | --- | --- | --- |
| F-01 | **Dataset có version**: bảng theo version + view tên cũ, snapshot Parquet bất biến (checksum) trên MinIO, không `DROP`; receipt trỏ tới version còn dữ liệu | cao | — | M | cao (đụng mọi reader) | pyarrow, DuckDB | replay 100 % receipt cũ; `test_versioned_ingest` |
| F-02 | **DQ gate**: `quality_state=blocked` chặn governed, báo cáo, data product; dataset contract tối thiểu (schema hash, freshness SLA, luật bắt buộc); từ vựng expectation GX | cao | F-01 | M | thấp | GX vocabulary | câu hỏi trên dataset blocked → `QUALITY_BLOCKED`, 0 số trả ra |
| F-03 | **AnswerEnvelope**: mã nhãn `trust_code` lưu trên `qa.answer`, receipt niêm phong cho **cả hai đường**, `INSUFFICIENT` đặt ở server, khối nào nhãn ấy | cao | — | M | trung bình | — | hợp đồng test cho cả hai đường; AUD-05 đóng |
| F-04 | **Governed-first router + ngân sách**: câu một metric → governed; agent chỉ nhận composite/lai; ngân sách token + thời gian; `ask_user` qua clarify_policy; `audience` từ API | cao | F-03 | M | thấp | — | tỷ lệ GOVERNED trên bộ vàng; token/câu đơn ≤ 2× governed |
| F-05 | **Rule-audit SQL khám phá** (cột đo ∈ measure metric catalog; filter câu hỏi ∈ SQL) ở cả hai đường | cao | — | S | thấp | mnemiq/Wren ý tưởng | PoC-3: sai bảng 2/20 → 0, từ chối sai ≤ +5 điểm |
| F-06 | **Zero ≠ no-data** ở `present_gate` + coverage | cao | — | S | thấp | — | 2 câu tồn kho DS-AW xanh |
| F-07 | **Bảng tài liệu có cấu trúc** (Docling wrap): `document.element`, `document.table_cell`, trích dẫn tới ô/bbox; số trong bảng đi đường SQL | cao | PoC-1 | L | cao (image, model) | Docling | cell F1 ≥ 0,85/0,70; RG-05 đóng |
| F-08 | **Chỉ mục bỏ dấu** + synonym glossary trong truy hồi | cao | — | S | thấp | unaccent | khong-dau recall@1 ≥ 0,7 |
| F-09 | **Analytics tất định tối thiểu**: tool `insights` (trend/streak/outlier/concentration) phơi cho agent; chart validator AN-03; receipt cho sandbox (code + input hashes + lib version); DuckDB trong sandbox | cao | F-03 | M | trung bình | DuckDB, pandera | `causal_claim_rate` đo trên văn bản thật = 0; chart lỗi bị chặn 100 % trên bộ AN-13 |
| F-10 | **Dữ liệu thật + bộ vàng + gate thật**: DS-TLC (snapshot TLC-2024Q1), ≥ 150 case giải mù, scorer theo hợp đồng, nightly real-model, ngưỡng online bỏ `provisional` | cao | F-01 | L | trung bình | — | report nightly; gate đọc retrieval + agent-eval |
| F-11 | **Vận hành tối thiểu**: outbox attempts + dead-letter + 3 sự kiện mồ côi xử lý hoặc trả "không hỗ trợ"; backup đủ 6 kho + restore drill; `/metrics` Prometheus + OTel span tới collector có sẵn | cao | — | M | thấp | prometheus_client, OTel SDK | restore drill xanh; dashboard p50/p95 |
| F-12 | **Sandbox hardening**: gVisor RuntimeClass; SQL không bao giờ vào container; kiểm nghĩa output (đơn vị, additivity) | cao | F-09 | S | thấp | gVisor | escape test 0/10; share trên non-additive bị chặn |

### P1 — Should have

| ID | Tính năng | D | E |
| --- | --- | --- | --- |
| F-20 | Monitor chạy theo lịch thật (threshold, change_vs_previous), alert → điều tra có ngữ cảnh (`created_from=alert`, auto ExplainChange) | F-09, F-11 | M |
| F-21 | Đa lượt thống nhất: slot kế thừa (metric/kỳ/lọc/nguồn) cho cả hai đường; viết lại câu follow-up trước truy hồi (RG-07) | F-04 | M |
| F-22 | Verified query khớp gần đúng (chuẩn hoá + trigram + tham số); reuse tile/dashboard (AN-10) | F-03 | S |
| F-23 | Lineage cột bằng sqlglot 30 (`lineage`, `qualify`); job nạp ghi edge | sqlglot 30 | M |
| F-24 | Ingest: discovery nguồn (PK, cột cursor, quyền SELECT), kiểu từ catalog nguồn mọi driver, staging + swap cho full | F-01 | M |
| F-25 | Chunk theo cấp tiêu đề cho tài liệu không pháp quy; parent/child; bỏ header/footer/TOC | F-07 | M |
| F-26 | Hybrid consistency: số từ tài liệu vs số từ SQL được đối chiếu (đơn vị, kỳ, nguồn) trước khi compose; `ReconciliationNote` | F-07, F-03 | M |
| F-27 | SEC EDGAR Data Space + 50 case hybrid/restatement | F-10 | M |
| F-28 | Semantic: chiều bắt buộc, quy tắc dấu/số dư (AGC-12), derived + ratio theo dbt-semantic-interfaces | — | M |
| F-29 | Danh tính thực thi theo người hỏi (SET ROLE / per-role credential) ở Postgres; READ ONLY cho SQL Server | — | M |
| F-30 | Trace ghi theo bước (AGC-05); OCC bắt buộc PATCH tài sản ngữ cảnh (AUD-09); prep steps thành bản nháp (AUD-04) | — | S |

### P2 — Advanced (chỉ khi P0 đạt cổng và P1 có baseline)

Deep analysis report mode (RES-08) trên plan DAG; cross-source reconciliation engine cho nhiều báo cáo có phiên bản;
Agent Studio T2; skill có cổng T3; MCP server phơi Data Space (D12); REST source qua dlt `rest_api`; CDC log; correction
memory consolidation (RES-10); kho tài liệu scan tiếng Việt fine-tune Tesseract; Analyst Workspace đầy đủ (IA-02).

## 4. Mức tự chủ MVP và vì sao

| Vai | Mức MVP | Dừng ở | Bằng chứng |
| --- | --- | --- | --- |
| Quản lý / mọi người (Ask) | **L2 governed**: resolver tất định → compiler; hỏi lại ≤ 1 câu, 3 lựa chọn; không SQL khám phá nếu metric có trong catalog | NEEDS_CLARIFICATION, NO_DATA, QUALITY_BLOCKED | 20/20, 536 token; BIRD-Interact < 20 % cho tương tác tự do |
| Analyst | **Proto-L3 có giám sát**: agent lập plan DAG ≤ 8 bước, kiểm tĩnh, ngân sách, hiện plan; sửa node; chạy lại xuôi dòng | Sau plan (tuỳ chọn), vượt ngân sách | 17/20 ở 42× chi phí → chỉ bật khi câu thật sự nhiều bước |
| Steward | L1–L2 có đề xuất | Luôn | Bất biến 5/12 |
| Monitor | L4 giới hạn, lùi P1 | Không ghi | Chưa chạy được hôm nay |

## 5. Cổng P0 (release gate của MVP) — ngưỡng đề xuất, chưa đo

Ngưỡng dưới đây là **mục tiêu**; được chốt lại từ lượt chạy thật đầu tiên trên snapshot cố định (cách rebase ở
[DATASET_AND_EVALUATION.md](DATASET_AND_EVALUATION.md) §6). Cơ sở chọn: số đã đo trên AW (governed 100 % trên câu đơn,
không dấu 0,10) và số công bố (Spider 2.0-Lite 76 %, DataSpace 66 %).

| Nhóm | Metric | Ngưỡng P0 | Cơ sở |
| --- | --- | --- | --- |
| Ask structured | giá trị đúng (multiset, hợp đồng đóng băng) trên 40 case DS-TLC | ≥ 85 % | AW governed 100 % trên câu đơn; TLC khó hơn (dòng bẩn, tỷ lệ có điều kiện, nhiều giá trị lọc) |
| Ask | `unsupported_number_rate` | 0 | tất định, đã = 0 trên AW |
| Ask | hỏi lại đúng lúc (precision/recall trên 30 case multi-turn) | ≥ 0,80 / ≥ 0,80 | 01 §10 đặt 0,9; chưa đo nên hạ |
| Ask | tỷ lệ câu đi VERIFIED + GOVERNED | ≥ 60 % | giả định §10.1 của 00-ASSESSMENT |
| SQL | truy vấn ngoài quyền | 0 | — |
| Analytics | số sai trong 25 case analytical (tolerance 0,5 %) | ≤ 10 % | ablation DS-BH 23 % sai khi đã trả lời → phải tốt hơn |
| Analytics | `causal_claim_rate` trên văn bản thật | 0 | — |
| Documents | cell F1 bảng (PDF text / scan) | ≥ 0,85 / ≥ 0,70 | TableFormer FinTabNet 0,89 |
| Retrieval | recall@10 (156 case, **full stack** có TEI) | ≥ 0,85; khong-dau ≥ 0,7 | keyword-only 0,80 |
| Retrieval | not_found_accuracy | ≥ 0,85 | keyword-only 0,74 |
| Hybrid | 25 case cross-source: kết luận đúng hoặc từ chối đúng | ≥ 70 % | KramaBench 55 % |
| Performance | p95 TTFT câu governed / p95 hoàn tất câu agentic | ≤ 3 s / ≤ 60 s | AW: 2,3 s / 16 s trung bình |
| Reliability | handler outbox lỗi làm kẹt lô | 0; dead-letter có alert | — |
| Security | injection (27 case policy) + 10 escape sandbox + cross-org sweep | 0 lọt | offline gate đã 0 |

## 6. Remove / Defer (quyết định, không phải gợi ý)

| Thành phần | Quyết định | Lý do | Cách làm |
| --- | --- | --- | --- |
| Schema `exec` (vault, thesis, publication), `features/vault`, `exec.publication.requested` | **Defer, tắt khỏi điều hướng** | Không worker, Publisher D8 hoãn, 10 bảng RLS phức tạp | Ẩn route FE; endpoint trả 501 có mã `UNSUPPORTED`; giữ bảng (xoá là migration riêng khi D8 chốt) |
| Knowledge page composer (`knowledge_run.requested`, 8 bảng, 16 route) | **Defer** | Không worker; giá trị chưa chứng minh | Trả `UNSUPPORTED` thay 202 |
| Entity-link / consolidation (`acquire.entity_link`, `reference_entity` ngân hàng VN) | **Remove khỏi UI, defer backend** | Không matcher, đặc thù ngành đã bỏ (D13) | Ẩn màn; xoá seed reference_entity ngân hàng trong commit riêng |
| Forecast ledger rescore, formula Monte Carlo, `event_bulletin.requested`, màn `Fuel*` | **Remove** (`Fuel*`) / **Defer** (ledger, Monte Carlo) | AUD-15; không handler | Xoá `features/forecast/Fuel*` + test (commit riêng) |
| Kafka, ClickHouse, Langfuse trong deploy | **Remove khỏi `make deploy-local`; giữ manifest production có cờ tắt** | Không gì gửi/đọc; tốn RAM cụm | Ansible var `enable_observability_stack=false`; nếu F-11 chọn OTel → collector + Tempo/Prometheus thay Langfuse |
| 4 connector enum (oracle, snowflake, bigquery, databricks) + kafka_cdc/object_store/sftp/sharepoint | **Ẩn khỏi picker** cho đến khi có driver + test | D-4 | `CONNECTORS` card sinh từ registry driver (F-24) |
| `.msg`, audio intake | **Trả `UNSUPPORTED` rõ** | Không transcribe | — |
| `AGENT_PYTHON_SANDBOX=local` | **Remove** | Biên bảo mật đã quyết là pod | Xoá nhánh `local` |
| Hai đường trả lời có hai cơ chế nhãn/receipt/ngữ cảnh | **Hợp nhất qua AnswerEnvelope (F-03) và slot carry-over (F-21)**; governed path **giữ** làm engine mặc định | Đo được: governed tốt hơn trên câu đơn | Không xoá governed (trái memory "delete governed sau eval": bằng chứng mới nói ngược lại) |
| Temporal 7 loại lịch | Giữ Temporal; gộp thành 1 workflow + bảng target (đã vậy) | — | — |

## 7. Điều không làm ở MVP (và lý do)

Multi-agent swarm (FDABench: đắt 2–3×, DataSpace: harness > model); fine-tuning model (chưa có bộ vàng để đo); GraphRAG
(GPU chung); auto-generated pipeline end-to-end (bất biến 12); agent ghi dữ liệu khách; "tự động hoàn toàn" L4 ngoài vùng
an toàn; mobile app (D10).
