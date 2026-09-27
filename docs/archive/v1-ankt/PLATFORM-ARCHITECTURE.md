# PLATFORM\-ARCHITECTURE

# Kiến trúc nền tảng Data Agent cho vô số chỉ số kinh tế

> Phiên bản 15/09/2026 · Đối tượng: kiến trúc sư, nhóm kỹ thuật \(backend, data, frontend\), nhóm Data Research, quản lý dự án\.<br>Tài liệu này tổng quát hoá từ golden use case Nợ công/GDP \([README\\\.md](README.md)\): **nợ công chỉ là một trong hàng trăm chỉ số** mà Data Agent phải phân tích và trả lời được\.<br>Sơ đồ PlantUML: [07](diagrams/07-platform-architecture.puml) · [08](diagrams/08-data-ingestion-services.puml) · [09](diagrams/09-agent-core.puml) · [10](diagrams/10-multi-agent-sequence.puml) · [11](diagrams/11-data-ui-map.puml) · [12](diagrams/12-generalized-data-model.puml)\.

## 0\. Tóm tắt

1. **Kiến trúc hiện tại mở rộng theo số agent, không mở rộng theo số chỉ số\.** Mỗi bộ dữ liệu cần một agent do người dựng, orchestrator đưa danh sách agent vào prompt, semantic layer khớp từ đồng nghĩa trên một bảng\. Cách này chạy tốt với vài chục metric doanh nghiệp, không chạy với hàng trăm chỉ số có công thức, vintage và ngưỡng\.
2. **Thay đổi cốt lõi**: đưa **Indicator &amp; Concept Catalog** và **Observation store** thành trung tâm; LLM sinh **Analysis Query có kiểu** thay vì SQL/phép tính tự do; mọi năng lực dữ liệu là **Platform API**, agent dùng qua **MCP chuẩn**; một **nhóm nhỏ sub\-agent chuyên môn** dùng chung thay cho “một agent mỗi bộ dữ liệu”\.
3. **Lớp dữ liệu đầu vào** cần một “ingestion plane” riêng cho dữ liệu có cấu trúc \(khác pipeline KB hiện có vốn cắt chunk để retrieval\): source registry, connector runtime, scheduler, extraction, mapping/normalization, validation, review, publisher — nối bằng Kafka\.
4. **Giao diện dữ liệu** là một **Data Studio** gồm 9 khu chức năng; phần lớn màn hình Knowledge Ops hiện có mở rộng được\.

## 1\. Kiến trúc hiện tại nhìn từ góc “vô số chỉ số”

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683923003171&cot=14)*

| \# | Điểm không mở rộng được | Căn cứ trong mã | Hệ quả khi có hàng trăm chỉ số |
| --- | --- | --- | --- |
| B1 | Chỉ số gắn vào agent: orchestrator lấy mọi agent đã publish của tenant đưa vào ToT planner | orchestrator/agent\_catalog\.py: fetch\_published\_agents | Số agent tăng theo bộ dữ liệu; prompt planner phình; chọn sai agent |
| B2 | Semantic layer = 1 bảng · 1 cột, resolver khớp cụm từ đồng nghĩa tất định | libs/semantic/models\.py, semantic\_layer\.match\_question | Không biểu diễn công thức; không tìm được chỉ số trong catalog lớn; câu hỏi dùng tên gọi khác là trượt |
| B3 | Planner sinh tool\_calls tự do \(kb\_search / db\_query / tool\) với danh sách tool tĩnh trong prompt | prompts/planner\_system\.txt, tool\_executor/\_\_init\_\_\.py \(15 tool, gồm cả bash, file\_write\) | Không có ngôn ngữ truy vấn phân tích; LLM tự quyết định cách lấy và tính số |
| B4 | Đơn vị sự thật = câu SQL trên warehouse | handlers/data\.py, sandbox\_executor | Không có vintage, không có số đã công bố trong tài liệu, không tái lập đáp án |
| B5 | Tool registry tĩnh, “MCP” không theo giao thức MCP \(HTTP POST tự định dạng\) | nodes/sandbox/mcp\.py | Không khám phá tool động, không cắm MCP server chuẩn, không dùng lại cho client ngoài |
| B6 | Có ủy quyền agent→agent nhưng không có vai trò chuyên môn | handlers/agent\.py: delegate\_to\_agent | Không có supervisor/verifier dùng chung; mỗi agent tự lo kiểm số |
| B7 | Ingestion hướng KB \(chunk cho retrieval\), không hướng quan sát có cấu trúc | libs/ingestion, kb\_structured\_data \(1 bảng/file\) | Bảng số trong PDF không trở thành dữ liệu truy vấn được |
| B8 | Eval tĩnh, viết tay | model/eval/cases/\*\.jsonl \(15–44 case/bộ\) | Không phủ được hàng trăm chỉ số; dữ liệu điều chỉnh làm đáp án cũ sai mà không ai biết |
| B9 | UI hướng builder và KB ops | frontend/src/components/knowledge, agent\-builder | Không có nơi quản lý chỉ số, nguồn, trích xuất, vintage, lineage |

**Giữ lại được:** orchestrator \+ executor DAG \(đã có phụ thuộc giữa task\), streaming/clarification, guardrail, evidence frame, governance \(freshness, classification, audit\), sandbox SQL an toàn, KB retrieval \+ rerank, Kafka pipeline, Langfuse, release gate\.

## 2\. Những thay đổi thiết kế cốt lõi

Sơ đồ tổng thể: [07\\\-platform\\\-architecture](diagrams/07-platform-architecture.puml)\. Mô hình dữ liệu: [12\\\-generalized\\\-data\\\-model](diagrams/12-generalized-data-model.puml)\.

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683923003183&cot=14)*

| Từ \(hiện tại\) | Sang \(mục tiêu\) | Vì sao |
| --- | --- | --- |
| Agent cho mỗi bộ dữ liệu | Catalog chỉ số làm trung tâm \+ 6–7 sub\-agent chuyên môn dùng chung | Thêm chỉ số = thêm bản ghi catalog, không thêm agent |
| Metric contract 1 bảng/1 cột | Indicator → formula DAG → variable → binding \(observation\_series · warehouse\_metric · api\_series\) | Một biến lấy được từ nhiều nguồn; warehouse chỉ còn là một loại binding |
| SQL làm sự thật | Observation store point\-in\-time: series\_key \+ period \+ release/vintage \+ source\_ref | Tái lập đáp án, phân biệt ước/sơ bộ/chính thức, trích dẫn tới trang |
| Khớp từ đồng nghĩa | Indicator search index hybrid \(BM25 \+ embedding \+ taxonomy \+ alias\) → LLM chọn trong top\-k → hỏi lại khi mơ hồ | Tìm đúng chỉ số trong hàng trăm–nghìn mục |
| LLM viết SQL / tự tính | LLM sinh Analysis Query \(IQL\) có schema; compiler tất định thực thi | Số do engine tính; LLM chỉ hiểu câu hỏi và diễn giải |
| Tool tĩnh, MCP tự chế | API\-first \+ MCP chuẩn \+ tool registry động | Một nguồn sự thật cho UI, agent, client ngoài |
| Ingestion cho KB | Hai pipeline chung source registry: văn bản → KB; số liệu → observation | Cùng tài liệu vừa là căn cứ trích dẫn vừa là nguồn số |
| Eval viết tay | Eval sinh từ observation \+ mẫu người duyệt \+ regression khi có release mới | Phủ theo họ chỉ số; phát hiện đáp án lỗi thời |

### 2\.1 Analysis Query \(IQL\) — hợp đồng giữa LLM và engine

```json
{
  "intent": "trend_with_threshold",
  "indicators": ["ECO-D2-S22-001", "ECO-D2-S22-004"],
  "filters": { "REF_AREA": ["VN"] },
  "period": { "from": "2016", "to": "2020", "frequency": "A" },
  "vintage": { "policy": "latest_published", "as_of": null },
  "transforms": [{ "op": "decompose_change", "target": "ECO-D2-S22-001" }],
  "compare": { "REF_AREA": ["VN", "TH", "MY", "ID", "PH"], "perimeter_check": true },
  "thresholds": "by_period",
  "output": ["table", "chart", "citations"]
}
```

- Tập `intent` hữu hạn: `lookup`, `compare\_threshold`, `trend`, `decompose\_change`, `cross\_section`, `cross\_country`, `scenario` \(sau\), `explain\_definition`\.
- Compiler kiểm tra trước khi chạy: chỉ số tồn tại và đã duyệt, tần suất có dữ liệu, kỳ đã công bố, quyền truy cập theo phân loại → trả lỗi có cấu trúc để agent **hỏi lại hoặc từ chối đúng**, thay vì đoán\.
- `slots` hiện có của planner \(metric, dimensions, time\_range, compare\_to\) là tiền thân của IQL → nâng cấp, không làm lại\.

### 2\.2 Lưu trữ

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683923003197&cot=14)*

| Dữ liệu | Kho đề xuất | Lý do |
| --- | --- | --- |
| Catalog, registry, source, review, threshold | Postgres \(đã có, RLS theo tenant\) | Giao dịch, ràng buộc, phê duyệt |
| Observation \(dạng dài, append\-only\) | ClickHouse \(đã triển khai trong cụm\) | Chuỗi thời gian lớn, truy vấn theo chiều nhanh, nén tốt |
| Giá trị chỉ số đã tính \(cache\) | ClickHouse, invalidate theo sự kiện observation\.published | Trả lời tra cứu không cần tính lại |
| Index tìm chỉ số, chunk văn bản | Qdrant \(đã có\) | Hybrid search |
| Quan hệ khái niệm ↔ văn bản ↔ ngưỡng ↔ nguồn, lineage | Neo4j \(đã có\) | Truy vết và phân tích tác động |
| Tài liệu gốc | MinIO \(đã có\) \+ SHA\-256 | Bất biến, dẫn chứng |

## 3\. Lớp dữ liệu đầu vào: tính năng và service

Sơ đồ: [08\\\-data\\\-ingestion\\\-services](diagrams/08-data-ingestion-services.puml)\.

### 3\.1 Tính năng bắt buộc

1. **Đăng ký nguồn**: cơ quan công bố, giấy phép sử dụng, phân loại, lịch công bố, SLA độ tươi\.
2. **Thu thập theo lịch và theo sự kiện**: phát hiện bản công bố mới \(hash, ETag, diff trang\), backfill lịch sử, retry\.
3. **Đa loại nguồn**: file \(PDF scan/số, Excel nhiều sheet\), web, REST API, SDMX, cơ sở dữ liệu \(incremental/CDC\), SFTP/email, upload thủ công kèm metadata\.
4. **Trích xuất có toạ độ**: OCR \+ layout \+ bảng \+ VLM, giữ trang/bbox/ô và độ tin cậy\.
5. **Mapping &amp; chuẩn hoá**: ô/cột → biến, chiều \(codelist\), kỳ, đơn vị, bội số, tiền tệ/FX; LLM gợi ý, người duyệt, hệ thống ghi nhớ quy tắc cho lần sau\.
6. **Kiểm soát chất lượng**: rule miền giá trị/đơn vị, biến động bất thường, tổng thành phần = tổng, so bản công bố trước, so giữa các nguồn\.
7. **Duyệt có trách nhiệm**: hàng đợi, maker–checker, lý do sửa, audit\.
8. **Phát hành append\-only** gắn release/vintage, phát sự kiện cho recompute, cảnh báo, eval regression, cập nhật KB\.
9. **Lineage end\-to\-end** từ tài liệu tới câu trả lời\.
10. **Giám sát**: sức khoẻ connector, trễ so với lịch công bố, tỉ lệ ô cần sửa\.

### 3\.2 Danh mục service

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683923003217&cot=14)*

| Service | Trách nhiệm | Tái dùng từ hiện có | Gợi ý công nghệ |
| --- | --- | --- | --- |
| source\-registry\-svc | Nguồn, cơ quan, giấy phép, lịch công bố, SLA | KBWarehouse, governance catalog | FastAPI \+ Postgres |
| scheduler | Lịch, sensor theo lịch công bố, retry, backfill | — \(SchedulingCenter chỉ có UI\) | K8s CronJob giai đoạn đầu → Dagster |
| connector\-runtime | Plugin: http\-file, web\-crawl\+diff, rest\-api, sdmx, db\-incremental, sftp/email, upload | web ingest \(trafilatura\), warehouse connectors, OpenBao | Python plugin interface, chạy như Dagster op |
| artifact\-store | Lưu file gốc, SHA\-256, phiên bản, dedup | MinIO bucket knowledge\-base | MinIO \+ bảng artifact |
| document\-extraction\-svc | Layout, OCR, bảng, VLM → trang/bbox/ô | Docling silver \(đang tắt OCR\) | GPU worker theo lô; model chọn qua benchmark |
| structured\-parser\-svc | xlsx nhiều sheet/bảng, csv, json, SDMX\-ML/JSON | kb\_structured\_data \(1 bảng/file\) | openpyxl, pandas, sdmx1 |
| mapping\-svc | Ô/cột → biến · chiều · kỳ · đơn vị; gợi ý LLM; bộ quy tắc | LLM propose schema \(propose\.py\) | Postgres rules \+ LLM agent\-fast |
| normalization\-svc | Bội số, FX, kỳ, codelist | — | Thư viện nội bộ, bảng FX là một variable |
| validation\-svc | DQ rules, bất thường | aggregation\_check\.check\_subtotals, sql\_guard | Rule engine nội bộ \(hoặc Soda/Great Expectations\) |
| reconciliation\-svc | So nguồn, so release trước, tự tính vs chính thức | conflict detection \(cho văn bản\) | Nội bộ, dung sai theo decimal\_precision |
| review\-svc | Hàng đợi, maker–checker, audit | StructuredTableReview, ReviewCenter, audit | Postgres \+ API |
| publisher\-svc | Ghi observation, release, phát sự kiện | Kafka topics hiện có | Kafka \+ ClickHouse |
| catalog &amp; lineage\-svc | Metadata, lineage, tác động | SchemaCatalog\.upstream, Neo4j | Neo4j \(hoặc OpenMetadata/DataHub nếu cần catalog đầy đủ\) |

### 3\.3 Sự kiện Kafka chuẩn

`source\.release\.detected` → `artifact\.stored` → `extraction\.completed` → `mapping\.proposed` → `validation\.completed` → `review\.approved` → `observation\.published` → \(`indicator\.recomputed`, `alert\.raised`, `eval\.regression\.requested`\)\.

Mọi sự kiện mang `source\_id`, `release\_id`, `artifact\_sha256`, `trace\_id` để lineage và audit nối được\.

## 4\. Agent core: MCP, tool, API, sub\-agent, multi\-agent

Sơ đồ: [09\\\-agent\\\-core](diagrams/09-agent-core.puml) · luồng mẫu: [10\\\-multi\\\-agent\\\-sequence](diagrams/10-multi-agent-sequence.puml)\.

### 4\.1 Khi nào single\-agent, khi nào multi\-agent

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683923003230&cot=14)*

| Loại câu hỏi | Ví dụ | Đường xử lý | Ngân sách độ trễ |
| --- | --- | --- | --- |
| Tra cứu, so ngưỡng, xu hướng 1 chỉ số | “Nợ công/GDP 2024?” | Fast path: 1 Indicator Analyst, ≤ 3 lời gọi tool | TTFT ≤ 2 s |
| Định nghĩa, căn cứ pháp lý | “Nợ công gồm những gì?” | Fast path: Legal Researcher | TTFT ≤ 2 s |
| Nhiều chỉ số, phân rã, so sánh quốc tế | “Vì sao A giảm khi B tăng, so với ASEAN?” | Supervisor \+ 2–4 sub\-agent song song | TTFT ≤ 5 s, hoàn tất ≤ 30 s |
| Báo cáo, bản tin | “Bản tin rủi ro tài khoá quý này” | Supervisor \+ Report Writer, chạy nền | Phút, thông báo khi xong |
| Kịch bản, dự báo | “Nếu tỷ giá tăng 10%…” | Scenario Analyst gọi model đã kiểm định \(giai đoạn sau\) | Theo model |

Router hiện có \(`input\_refine` → `route`\) mở rộng thêm phân loại độ phức tạp\. Executor DAG của orchestrator dùng lại cho supervisor\.

### 4\.2 Sub\-agent chuyên môn \(dùng chung cho mọi chỉ số\)

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683923003247&cot=14)*

| Sub\-agent | Nhiệm vụ | Tool chính | Model |
| --- | --- | --- | --- |
| Indicator Analyst | Lấy số, biến đổi, phân rã biến động, so ngưỡng | indicator\-mcp, data\-mcp, compute\-mcp | agent\-main |
| Document &amp; Legal Researcher | Định nghĩa, văn bản còn hiệu lực, căn cứ ngưỡng | source\-mcp, kb search | agent\-main |
| Data / SQL Analyst | Câu hỏi ad\-hoc trên warehouse | warehouse\-mcp \(semantic layer trước, text\-to\-SQL có guard sau\) | agent\-main |
| International Comparator | Số liệu IMF/WB, ánh xạ phạm vi, cảnh báo khác định nghĩa | intl\-mcp, indicator\-mcp | agent\-main |
| Scenario &amp; Forecast Analyst | Chạy DSA/stress test/dự báo đã kiểm định \(giai đoạn sau\) | compute\-mcp \(model registry\) | agent\-main |
| Report Writer | Bản tin, báo cáo dài, bảng biểu | report\-mcp | agent\-main |
| Verifier / Critic | Số ⊆ evidence ± dung sai, vintage nhất quán, trích dẫn đúng trang, quyền | tất định \+ agent\-fast | agent\-fast |

**Không làm:** agent riêng cho từng chỉ số hay từng domain; để LLM thấy toàn bộ catalog; để LLM tự tính hoặc tự so ngưỡng\.

### 4\.3 Tool và MCP server

Nguyên tắc **API\-first**: mỗi năng lực là một REST API \(UI và hệ thống khác dùng trực tiếp\); MCP server là lớp mỏng bọc API cho agent; schema tool sinh từ OpenAPI\.

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683923003256&cot=14)*

| MCP server | Tool | API phía sau |
| --- | --- | --- |
| indicator\-mcp | search\_indicators, get\_definition, list\_dimensions, get\_thresholds | Indicator Catalog API |
| data\-mcp | query\(AnalysisQuery\), get\_observations, list\_vintages | Observation &amp; Compute API |
| compute\-mcp | transform, ratio, decompose\_change, compare\_threshold | Observation &amp; Compute API |
| source\-mcp | get\_source\_document, cite\(doc, page, bbox\), search\_legal\(effective\_at\) | Source &amp; Knowledge API |
| warehouse\-mcp | semantic\_query, text\_to\_sql \(guarded\) | sandbox\-executor \(có sẵn\) |
| intl\-mcp | imf\_series, worldbank\_series, map\_perimeter | Observation API \(dữ liệu quốc tế cũng được ingest thành observation\) |
| report\-mcp | render\_chart, export\_xlsx, build\_briefing | answer\_exports \(có sẵn\) \+ renderer mới |

- **Thay node `mcp` tự chế** bằng MCP client theo giao thức MCP \(Streamable HTTP, xác thực bằng service assertion hiện có\), để cắm được MCP server nội bộ lẫn bên ngoài\.
- **Tool registry động**: mỗi sub\-agent chỉ thấy tool của vai trò; khi số tool lớn, chọn tool theo embedding mô tả \+ ngữ cảnh\.
- **Tách tool “coding”** \(`bash`, `file\_write`, `file\_edit`\) khỏi agent phân tích dữ liệu — không cần cho ANKT và mở rộng bề mặt rủi ro\.

### 4\.4 Platform API \(REST\)

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683923003267&cot=14)*

| API | Endpoint tiêu biểu |
| --- | --- |
| Indicator Catalog | GET /indicators?q=&amp;domain= · GET /indicators/\{id\} · GET /indicators/\{id\}/versions · GET /taxonomy |
| Observation &amp; Compute | POST /query \(IQL\) · GET /series/\{key\}/observations?vintage= · GET /series/\{key\}/vintages · POST /compute |
| Source &amp; Knowledge | GET /sources · GET /documents/\{id\} · GET /documents/\{id\}/pages/\{n\} · POST /legal/search |
| Ingestion | POST /connectors · POST /connectors/\{id\}/runs · GET /reviews?status= · POST /reviews/\{id\}/approve |
| Quality &amp; Lineage | GET /dq/results · GET /reconciliations · GET /lineage/\{entity\} |
| Eval | POST /golden\-cases/generate · POST /eval/runs · GET /eval/runs/\{id\} |

### 4\.5 Thay đổi cụ thể trong mã hiện có

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683923003275&cot=14)*

| Thành phần | Thay đổi |
| --- | --- |
| orchestrator/nodes/route | Thêm phân loại độ phức tạp → fast path hoặc supervisor |
| orchestrator/agent\_catalog\.py | Thêm registry system sub\-agent \(vai trò, tool, model\) bên cạnh agent do người dựng |
| prompts/planner\_system\.txt \+ planner\.py | Sinh Analysis Query theo JSON schema; slots hiện có chuyển thành một phần IQL |
| nodes/sandbox/mcp\.py, tool\_executor | MCP client chuẩn \+ tool registry động theo vai trò |
| nodes/model\_harness/judge\.py | Nâng thành Verifier: kiểm số với evidence có dung sai, vintage, trích dẫn trang |
| Evidence frame \(responder\.py, stream\.py\) | Thêm indicator\_id, series\_key, release/vintage, doc\_id, page, threshold\_status |
| Session context \(slots, Redis\) | Nhớ chỉ số, vùng, kỳ, vintage giữa các lượt |

## 5\. Giao diện các chức năng về dữ liệu \(Data Studio\)

Bản đồ chức năng: [11\\\-data\\\-ui\\\-map](diagrams/11-data-ui-map.puml)\. Bản đồ điều hướng toàn giao diện: [37](diagrams/37-ui-information-architecture.puml); kiến trúc frontend và bộ primitive dùng chung: [40](diagrams/40-frontend-architecture.puml); đặc tả chi tiết: [UI\\\-DESIGN\\\.md](UI-DESIGN.md)\.

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683923003282&cot=14)*

| Khu chức năng | Chức năng chính | Người dùng | Tái dùng màn hiện có |
| --- | --- | --- | --- |
| Nguồn &amp; Connector | Danh mục nguồn; lịch công bố &amp; SLA; wizard connector \(file/web/API/SDMX/DB\); lịch chạy, lịch sử, log, backfill; sức khoẻ connector | Data engineer, data steward | WarehouseWizard, IngestionWizard, SchedulingCenter \(cần backend\) |
| Trích xuất &amp; Duyệt | Viewer ảnh trang \+ khung bbox \+ ô số; bảng trích xuất sửa được; độ tin cậy từng ô; so với bản trước; duyệt hàng loạt, maker–checker | Data steward | AssetDetailWorkspace \(Preview/Tables\), StructuredTableReview |
| Mapping &amp; Chuẩn hoá | Ô/cột → biến/chiều/kỳ/đơn vị; LLM gợi ý; codelist; quy tắc FX/bội số | Data steward | StructuredTableReview \(chỉnh schema\) |
| Indicator Studio | Cây taxonomy; form chỉ số \(ECO\-001\.5\); formula builder có kiểm tra stock/flow và đơn vị; ngưỡng theo giai đoạn \+ văn bản căn cứ; phiên bản, duyệt, phân tích tác động | Nhóm research, reviewer | Chưa có |
| Series &amp; Vintage Explorer | Duyệt chuỗi theo chiều; tam giác điều chỉnh \(revision triangle\); truy nguồn tới trang | Chuyên viên, steward | Chưa có |
| Chất lượng &amp; Đối chiếu | Kết quả DQ; bất thường; chênh lệch nguồn; issue | Steward | Conflicts workspace \(cho văn bản\) |
| Lineage &amp; Tác động | Đồ thị nguồn → quan sát → chỉ số → dashboard/câu trả lời; ai bị ảnh hưởng khi số sửa | Steward, QA | KnowledgeHub \(force graph Neo4j\) |
| Eval dữ liệu | Sinh golden case từ observation; chính sách dung sai; kết quả theo họ chỉ số | QA, research | Chưa có \(dùng Langfuse Datasets trước\) |
| Quyền &amp; Phân loại | Phân loại theo nguồn/chuỗi; yêu cầu truy cập; audit | Quản trị | Settings/IAM, AuthAuditPanel |

Ưu tiên theo giai đoạn: **\(1\)** Trích xuất &amp; Duyệt \+ Indicator Studio tối thiểu \(nhập YAML\) → **\(2\)** Nguồn &amp; Connector \+ Series/Vintage Explorer \+ Chất lượng → **\(3\)** Mapping studio đầy đủ \+ Lineage \+ Eval dữ liệu\.

Phía người dùng phân tích \(đã nêu trong [README\\\.md](README.md) mục 5\): danh mục &amp; trang chỉ số, dashboard, hộp cảnh báo, so sánh, báo cáo — tất cả đọc cùng Platform API ở mục 4\.4\.

**Một quy tắc giao diện có hệ quả kiến trúc**: `intent` của Analysis Query \(mục 2\.1\) quyết định bố cục câu trả lời, không phải LLM\. Nhờ đó thêm một chỉ số mới không cần thêm mã giao diện, và mọi câu trả lời cùng loại trông giống nhau\. Ánh xạ intent → khối hiển thị: [38](diagrams/38-answer-render-contract.puml), chi tiết [UI\\\-DESIGN\\\.md](UI-DESIGN.md) mục 4\.

## 6\. Lộ trình chuyển đổi kiến trúc

Golden use case Nợ công/GDP là **pilot** chạy qua mọi lớp mới; mỗi giai đoạn phải thêm được chỉ số mới **chỉ bằng dữ liệu và cấu hình**, không sửa code agent\.

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683923003303&cot=14)*

| Giai đoạn | Nội dung kiến trúc | Tiêu chí “mở rộng được” |
| --- | --- | --- |
| A\. Nền tảng ngữ nghĩa | Indicator catalog \+ formula DAG; observation store \(ClickHouse\) \+ release/vintage; IQL \+ compiler; Indicator/Observation API; indicator\-mcp, data\-mcp, compute\-mcp; fast path \+ Verifier | Thêm 1 chỉ số tỷ lệ mới chỉ bằng YAML \+ dữ liệu, trả lời đúng L1–L3 |
| B\. Ingestion plane | Source registry; connector runtime \(file, web, API/SDMX\); scheduler; document extraction có toạ độ; mapping/normalization; validation/reconciliation; review; publisher \+ sự kiện | Release mới của nguồn tự vào hàng đợi duyệt; sau duyệt, câu trả lời và cache cập nhật không cần can thiệp |
| C\. Multi\-agent \+ Data Studio | Supervisor \+ sub\-agent; MCP client chuẩn \+ tool registry động; source\-mcp, intl\-mcp, report\-mcp; Data Studio khu \(1\)\(2\); eval sinh từ observation \+ regression theo sự kiện | Câu hỏi đa chỉ số/đa lĩnh vực đạt chuẩn eval; ≥ 30 chỉ số không thêm agent |
| D\. Cảnh báo &amp; dự phóng | Alert engine theo sự kiện; model registry cho DSA/dự báo; Scenario Analyst; Data Studio khu \(3\) | Cảnh báo có giải thích \+ truy nguồn; backtest đạt ngưỡng nghiệp vụ |

Mốc thời gian chi tiết cho pilot nợ công giữ theo [06\\\-roadmap\\\-gantt](diagrams/06-roadmap-gantt.puml); giai đoạn A ≈ GĐ0, B ≈ GĐ1, C–D ≈ GĐ2–GĐ3\.

## 7\. Quyết định kiến trúc cần chốt \(ADR\)

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683923003317&cot=14)*

| \# | Quyết định | Phương án | Đề xuất |
| --- | --- | --- | --- |
| ADR\-1 | Kho observation | Postgres phân vùng · ClickHouse | ClickHouse \(đã có trong cụm, hợp chuỗi thời gian\); Postgres cho registry |
| ADR\-2 | Scheduler | K8s CronJob · Dagster · Airflow | CronJob cho pilot → Dagster khi &gt; ~20 nguồn |
| ADR\-3 | Ngôn ngữ truy vấn phân tích | Mở rộng slots · IQL JSON riêng · SQL trên semantic layer | IQL JSON có schema, compiler tất định |
| ADR\-4 | Giao thức tool | Giữ HTTP tự định dạng · MCP chuẩn | MCP chuẩn bọc REST API \(API\-first\) |
| ADR\-5 | Catalog/lineage | Tự xây trên Postgres\+Neo4j · OpenMetadata/DataHub | Tự xây tối thiểu cho pilot; đánh giá lại khi nhiều nguồn/đội |
| ADR\-6 | Mô hình agent | Agent theo bộ dữ liệu · sub\-agent chuyên môn | Sub\-agent chuyên môn \+ fast path |
| ADR\-7 | Hạ tầng tài liệu/VLM | Chung GPU chat · GPU riêng | GPU riêng cho extraction theo lô khi vào sản xuất |
| ADR\-8 | Chuẩn mã chiều dữ liệu | Tự đặt · theo SDMX cross\-domain concepts | Theo SDMX \(REF\_AREA, FREQ, UNIT\_MULT…\) để ghép dữ liệu IMF/WB |
