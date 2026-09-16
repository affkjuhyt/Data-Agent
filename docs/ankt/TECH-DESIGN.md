# TECH\-DESIGN

# Thiết kế kỹ thuật — kỹ thuật đã có và còn thiếu

> Phiên bản 16/09/2026 · Đối tượng: kiến trúc sư, trưởng nhóm backend/data/ML, kỹ sư hiện thực\.<br>Mục đích: với **mọi tính năng đã định nghĩa** trong [README](README.md) · [PLATFORM\\\-ARCHITECTURE](PLATFORM-ARCHITECTURE.md) · [SERVICE\\\-DESIGN](SERVICE-DESIGN.md) · [UI\\\-DESIGN](UI-DESIGN.md) · [ASK\\\-WORKSPACE](ASK-WORKSPACE.md), trả lời ba câu: **kỹ thuật nào đã có trong mã** · **kỹ thuật nào còn thiếu** · **hiện thực bằng cách nào**\.<br>Căn cứ: đọc mã `backend/libs`, `backend/services`, `model/eval`, `frontend/src` ngày 16/09/2026\.<br>Sơ đồ: [49](diagrams/49-technique-stack.puml) bản đồ kỹ thuật 12 tầng · [50](diagrams/50-number-provenance-pipeline.puml) đường đi của một con số\.

## 0\. Tóm tắt

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910108881&cot=14)*

| \# | Kết luận |
| --- | --- |
| 1 | Nền kỹ thuật tốt hơn nhiều so với vẻ ngoài\. Những phần khó và dễ làm sai đã được làm cẩn thận: SQL guard hai bộ phân tích độc lập, AST visitor cho Python, phát hiện fan\-out do join, đánh dấu nội dung không tin cậy trong prompt, service assertion có chữ ký, RLS, LangGraph có checkpointer Postgres để resume đa replica\. Đây không phải mã prototype\. |
| 2 | Chỗ thiếu nằm đúng ở trục ANKT: toàn bộ chuỗi số có nguồn gốc — OCR có toạ độ → ánh xạ → đơn vị → Decimal → observation có vintage → formula DAG → ngưỡng theo kỳ → verifier có dung sai — chưa có mắt xích nào \(50\)\. |
| 3 | Ba lỗi kỹ thuật cụ thể phải sửa, không phải xây mới \(rẻ, rủi ro cao nếu để\): do\_ocr=False trong docling\_parser · scorer \_numbers\(\) xoá dấu phân cách nên 128,4 = 1284 · formatVN\.ts định dạng theo chuẩn Mỹ\. Cả ba đều tạo ra số sai trông đáng tin\. |
| 4 | Có kỹ thuật đã viết nhưng đang tắt: lexical/BM25 trong LightRAG \(HYBRID\_LEXICAL=0\), doc\-narrowing \(DOC\_NARROWING=0\), alias resolve \(QUERY\_ALIAS\_RESOLVE=0\), degrade budget \(DEGRADE\_BUDGET\_MS=0\)\. Với văn bản pháp luật Việt Nam — nơi phải khớp đúng “59/2026/QH15” — dense\-only là lựa chọn sai\. Bật và đo trước khi xây mới\. |
| 5 | Số học là chỗ nguy hiểm nhất còn lại: hệ thống chưa dùng Decimal cho tính toán, chưa có đại số đơn vị, chưa có bội số/FX\. Với số liệu tài khoá tính bằng nghìn tỷ đồng, float64 và unit dạng text tự do là công thức tạo sai số âm thầm\. |
| 6 | Điều phối và lập kế hoạch đã đủ mạnh để tái dùng: DPTS \(tree search có candidate pool, expanding queue, planner gate, memory estimator\) cộng executor DAG có phụ thuộc — không cần viết lại cho supervisor multi\-agent, chỉ cần thêm router theo độ phức tạp\. |
| 7 | Tổng kết lượng hoá: 34 kỹ thuật đã có · 12 có một phần · 31 chưa có \(mục 4\)\. Trong 31 cái chưa có, 9 cái chặn Demo 1\. |
| 8 | Ước lượng: ~26 tuần\-người backend/data cho toàn bộ phần còn thiếu, trong đó ~5 tuần cho 9 kỹ thuật chặn Demo 1 \(mục 7\)\. |

## 1\. Kiểm kê kỹ thuật đã có

Sơ đồ [49\\\-technique\\\-stack](diagrams/49-technique-stack.puml)\. Cột cuối đánh giá **dùng được cho ANKT tới đâu**, không phải chất lượng mã\.

### 1\.1 Điều phối và lập kế hoạch

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910108888&cot=14)*

| Kỹ thuật | Hiện thực | Đủ cho ANKT? |
| --- | --- | --- |
| LangGraph StateGraph với 8 node: input\_refine → route → \{single\_agent \| fetch\_agents → tot\_planner → execute → summary\} → suggestion | orchestrator/graph\.py | ✅ Dùng lại\. Thêm nhánh fast\-path là sửa route, không phải viết lại |
| Checkpointer Postgres \(AsyncPostgresSaver, psycopg3\) thay MemorySaver | graph\.py: \_open\_checkpointer | ✅ Quan trọng: /resume chạy được trên replica khác replica đã bắt đầu |
| interrupt\(\) để dừng giữa graph và hỏi lại | graph\.py:318 | ✅ Nền cho clarification và cho “hỏi lại thay vì đoán” của IQL compiler |
| DPTS — tree search có CandidatePool, ExpandingQueue, PlannerGate \(xử lý node hỏng do executor báo\), MemoryEstimator | orchestrator/planner/dpts/ | ✅ Mạnh hơn mức cần cho tra cứu 1 chỉ số; đúng cho câu đa bước |
| Executor DAG có phụ thuộc giữa task \+ collect\_context truyền ngữ cảnh | orchestrator/executor/ | ✅ Dùng lại nguyên cho supervisor |
| Uỷ quyền agent→agent \(delegate\_to\_agent\) | handlers/agent\.py | ⚠️ Có cơ chế, thiếu vai trò chuyên môn dùng chung |

### 1\.2 Ngữ nghĩa và sinh truy vấn

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910108891&cot=14)*

| Kỹ thuật | Hiện thực | Đủ cho ANKT? |
| --- | --- | --- |
| Metric contract → SQL tất định: aggregation, expression, filter mặc định, cột thời gian nghiệp vụ đều từ hợp đồng đã duyệt; mọi thứ nội suy là identifier đã kiểm hoặc literal đã escape theo kiểu | libs/semantic/compiler\.py | ⚠️ Đúng triết lý, sai phạm vi: 1 bảng · 1 cột · sum/count/avg/min/max\. Không biểu diễn được tỷ lệ Nợ/GDP |
| Truncate theo grain đa dialect \(DATE\_TRUNC cho Postgres, toStartOf\* cho ClickHouse\) | compiler\.py | ✅ Mẫu tốt để mở rộng cho tần suất kỳ ANKT |
| Resolver đồng nghĩa “dài nhất thắng”, chuẩn hoá bỏ dấu, tất định và giải thích được \(“khớp đồng nghĩa X”\) | libs/semantic/resolver\.py | ⚠️ Chạy với vài chục metric; không tìm được trong catalog hàng trăm chỉ số |
| AnalyticalSlots \(metric, dimensions, time\_range, relative\_period, grain, compare\_to\) \+ merge giữa lượt | libs/semantic/slots\.py | ⚠️ Tiền thân của IQL\. compare\_to parse được nhưng không compile |
| Phát hiện câu hỏi mơ hồ có mã lý do \(missing\_time\_range, ambiguous\_metric, comparison\_without\_baseline\) | libs/semantic/ambiguity\.py | ✅ Mẫu đúng cho “từ chối có cấu trúc” của UI |

### 1\.3 An toàn thực thi

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910108895&cot=14)*

| Kỹ thuật | Hiện thực | Đánh giá |
| --- | --- | --- |
| SQL guard hai bộ phân tích bắt buộc đồng thuận: sql\_scan \(lexer viết riêng, mô hình đúng luật lexing từng engine\) quyết định biên câu lệnh; sqlparse quyết định hình dạng câu lệnh sống sót\. Bất đồng về số câu lệnh = từ chối | sandbox\_executor/sql\_guard\.py, sql\_scan\.py | ✅ Kỹ thuật tốt\. Chặn được SELECT '\\\\'; DROP TABLE t; \-\-' mà sqlparse một mình đọc nhầm |
| Chặn dấu ngoặc lệch vì comment có thể nuốt \) và LIMIT của wrapper | sql\_guard\.py | ✅ Chi tiết hiếm ai nghĩ tới |
| AST visitor cho Python: whitelist module \(pandas, numpy, plotly, matplotlib, json, math, datetime, statistics\) \+ denylist call \(eval, exec, open, getattr, setattr, vars, globals, locals\) | security\_visitor\.py | ✅ Chặn được getattr\(\_\_builtins\_\_, 'ev'\+'al'\)\. ⚠️ Thiếu scipy, statsmodels |
| Pool container Docker hoặc k8s, hàng đợi riêng theo kind \(sql / python\+shell chung pool\) | pool\.py, k8s\_pool\.py, job\_queue\.py | ✅ Có cách ly và có hàng đợi ưu tiên |
| Giới hạn kết quả row/byte/cell \+ báo truncation\.reason ra tận UI | result\_limits\.py, types\.ts | ✅ |
| Role read\-only riêng cho từng engine | postgres\_readonly\.py, clickhouse\_readonly\.py, snowflake\_readonly\.py | ✅ |

### 1\.4 Chất lượng số học \(phần đã có\)

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910108900&cot=14)*

| Kỹ thuật | Hiện thực | Đánh giá |
| --- | --- | --- |
| Phát hiện fan\-out do join: SUM\(o\.amount\) FROM orders o JOIN order\_items i nhân tổng lên theo số dòng con — sai theo hệ số, không phải sai làm tròn, và không có gì trong câu trả lời để người đọc nhận ra | libs/shared/aggregation\_check\.py | ✅ Kỹ thuật đúng, và tự giới hạn đúng: báo rủi ro chứ không báo sai, vì catalog không lưu khoá chính/duy nhất nên không chứng minh được cardinality\. COUNT\(DISTINCT\) không bị gắn cờ |
| check\_units — phát hiện trộn đơn vị | aggregation\_check\.py:232 | ⚠️ Có hàm, chưa nối vào pipeline dữ liệu |
| check\_subtotals — tổng thành phần vs tổng, có dung sai | aggregation\_check\.py:252 | ⚠️ Tái dùng được ngay cho DQ rule của ANKT |

### 1\.5 Truy xuất tri thức

Pipeline LightRAG trong `kb\_backend/pipeline/light\_rag/pipeline\.py` — nhiều tầng hơn một RAG thông thường:

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910108904&cot=14)*

| Giai đoạn | Kỹ thuật | Tham số mặc định |
| --- | --- | --- |
| Chuẩn hoá truy vấn | LLM trích intent \+ entities \+ relationships có JSON schema | canonicalize\(\) |
| Cache L0 | Redis khớp chính xác, trước cả bước embedding | libs/shared/kb\_result\_cache\.py |
| Cache ngữ nghĩa | Qdrant cosine, ngưỡng 0\.92, TTL 24h, có vòng quét dọn hạn | CACHE\_THRESHOLD, CACHE\_SWEEP\_INTERVAL |
| Thu hẹp tài liệu | 2 lượt: chọn top\-M tài liệu rồi mới lấy chunk | DOC\_NARROWING=0 — đang tắt |
| Vector search | Lọc theo loại thực thể ưu tiên, có TYPE\_BOOST=0\.08 | QDRANT\_PASS1\_LIMIT=3, PASS2=2 |
| Lexical | Postgres tsvector \(LEXICAL\_TS\_CONFIG=simple\) | HYBRID\_LEXICAL=0 — đang tắt |
| Mở rộng đồ thị | Neo4j, tối đa 1 hop, 5 láng giềng, 3 thực thể gốc | NEO4J\_MAX\_HOPS=1 |
| Hợp nhất | RRF \(Reciprocal Rank Fusion\), k=60, top\-N 10 | rrf\_merge\(\) |
| Rerank | Cross\-encoder, logit → xác suất qua sigmoid, top 5 | RERANK\_TOP\_N=5 |
| Suy giảm có kiểm soát | Bỏ giai đoạn khi vượt ngân sách thời gian, báo degraded\[\] | DEGRADE\_BUDGET\_MS=0 — đang tắt |

**Đánh giá**: kiến trúc truy xuất đã ở mức tốt\. Điểm yếu cho ANKT không nằm ở thuật toán mà ở **dữ liệu vào** \(không có OCR, không có trang/bbox, không có metadata hiệu lực văn bản\) và ở **các cờ đang tắt**\.

### 1\.6 Xử lý tài liệu

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910108908&cot=14)*

| Kỹ thuật | Hiện thực | Đánh giá |
| --- | --- | --- |
| Docling: layout \+ TableFormer | libs/ingestion/docling\_parser\.py | ⚠️ PdfPipelineOptions\(do\_ocr=False\) — PDF scan không ra chữ |
| Chunking có ngưỡng trên/dưới: MAX\_CHUNK\_CHARS=4000, MIN\_CHUNK\_CHARS=200 \(chặn header liên tiếp sinh chunk rỗng\) | docling\_parser\.py | ✅ Thiết kế chunk chu đáo |
| Bảng để nguyên một chunk tới TABLE\_MAX\_CHUNK\_CHARS=30000, vì cắt bảng làm hỏng ngữ cảnh khi tổng hợp | docling\_parser\.py | ✅ Quyết định đúng cho tài liệu số liệu |
| Trích nội dung chính từ web \(trafilatura\) \+ Playwright cho trang render client | kb\_promote\_silver | ✅ |
| Entity/relationship theo chunk \+ hợp nhất ở tầng tài liệu \(map\-reduce\) | libs/ingestion/llm\_schemas\.py | ✅ |

### 1\.7 Phục vụ model

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910108923&cot=14)*

| Kỹ thuật | Hiện thực |
| --- | --- |
| LiteLLM 1\.59\.9 làm cổng trước vLLM \(chat\) và TEI \(embed/rerank\) | \.env\.example, model\_service/ |
| Cache embedding \+ dynamic batching \(gom request trong EMBED\_BATCH\_WAIT\_MS, trần EMBED\_BATCH\_MAX\) | model\_service/backends/embed\_service\.py |
| Probe LLM → Prometheus | llm\_exporter/ |

### 1\.8 Bảo mật và quản trị

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910108925&cot=14)*

| Kỹ thuật | Hiện thực | Đánh giá |
| --- | --- | --- |
| Kong plugin JWKS xác thực Keycloak JWT và xoá header X\-\* do client gửi trước khi chuyển tiếp | deploy/docker/kong/plugins/aeroflow\-jwks/handler\.lua | ✅ |
| Service assertion có chữ ký HMAC, có hạn — bịt lỗ “gọi thẳng cổng service và tự xưng admin” | libs/shared/service\_identity\.py | ✅ Kỹ thuật đúng, hiếm gặp ở mức này |
| RLS Postgres qua set\_config\('app\.tenant\_id', …, true\) theo transaction, có cảnh báo khi role BYPASSRLS | libs/database\_connector/postgres\_connector\.py | ✅ |
| Đánh dấu nội dung không tin cậy trong prompt: KB chunk, kết quả tool, MCP payload, memory, câu trả lời agent khác đều được bọc để model biết đâu là dữ liệu, đâu là chỉ thị | libs/shared/untrusted\.py | ✅ Phòng prompt injection ở đúng chỗ |
| PII detector thuần regex, không phụ thuộc Presidio/spaCy | libs/shared/pii\_detector\.py | ✅ |
| Audit record\_sql\_access / record\_tool\_access có decision allow/deny \+ lý do | libs/shared/audit\.py | ✅ |
| Secret trong OpenBao qua AppRole chỉ đọc \(hvac\) | warehouse\_secrets\.py | ✅ |

### 1\.9 Sự kiện và nhất quán

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910108928&cot=14)*

| Kỹ thuật | Hiện thực |
| --- | --- |
| Kafka enable\_auto\_commit=False — commit sau khi xử lý xong | platform\_workers/common/kafka\.py |
| Consumer idempotent theo source\_event\_id \(Kafka at\-least\-once nên redelivery phải vô hại\) | workers/audit/data\_access\.py |
| Optimistic locking bằng row\_version | libs/shared/row\_version\.py |
| Xoá cache đa replica qua Redis pub/sub — bịt lỗ “republish cùng version, replica giữ graph cũ tới khi restart” | libs/shared/cache\_invalidation\.py |
| Khoá tài liệu khi ingest \(document\_lock\), ghi file nguyên tử \(atomic\_io\) | libs/shared/ |

### 1\.10 Quan sát và đánh giá

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910294932&cot=14)*

| Kỹ thuật | Hiện thực | Đánh giá |
| --- | --- | --- |
| OTEL đầy đủ: FastAPI, SQLAlchemy, httpx, redis, pymongo, logging → Jaeger; structlog định dạng ECS | requirements\.txt, observability\.py | ✅ |
| Langfuse v3 \(nền OTEL\), thay v2 vì callback \+ StatefulSpanClient không ổn định | workflow\_runtime/observability\.py | ✅ |
| Scorer offline tất định: policy \(guard SQL/Python/path/input\-name\), sql \(compile \+ guard verdict\) | model/eval/scorers/ | ✅ Ngưỡng tuyệt đối \(0 vi phạm, 100% SQL\) là hợp lý vì không có model, không sampling |
| Release gate: metric thiếu = trượt, vì “không đo được” khác “không sao” | model/eval/gate\.py, thresholds\.json | ✅ Triết lý đúng |
| Ngưỡng online tự đánh dấu "provisional": true và ghi rõ chưa có baseline | thresholds\.json | ✅ Trung thực; nhưng nghĩa là gate online hiện chưa chứng nhận điều gì |

## 2\. Ba lỗi kỹ thuật phải sửa trước

Ba lỗi này khác với “chưa có”: chúng **đang chạy và đang tạo ra kết quả sai trông đáng tin**\. Chi phí sửa thấp, hậu quả nếu để cao nhất trong toàn bộ danh sách\.

### E1 · OCR đang tắt

```python
# backend/libs/ingestion/docling_parser.py:63
pipeline_options = PdfPipelineOptions(do_ocr=False)
```

Văn bản nhà nước Việt Nam có tỷ lệ PDF scan cao\. Với `do\_ocr=False`, những tài liệu đó vào hệ thống như **tài liệu rỗng** — không lỗi, không cảnh báo, chỉ là không có chữ\. Agent sau đó trả lời “không tìm thấy” cho một câu hỏi mà tài liệu đã có sẵn câu trả lời\.

**Sửa**: bật OCR có chọn model \(benchmark ở mục 3, nhóm T7\), chạy theo lô trên GPU riêng, lưu `model\_version` vào mọi kết quả\.

### E2 · Scorer số xoá dấu phân cách

```python
# model/eval/scorers/grounding.py:_numbers()
cleaned = raw.strip(".,").replace(".", "").replace(",", "")
```

Docstring nói rõ chủ đích: “128,4 và 128\.4 so sánh bằng nhau”\. Với BI doanh nghiệp, chấp nhận được\. Với ANKT thì **`128,4` \(= 128\.4\) và `1284` trở thành cùng một số**, nên một câu trả lời ghi `1284` khi nguồn ghi `128,4` vẫn được chấm là có nguồn\.

**Sửa**: parser nhận biết locale → giá trị số thật; so sánh theo **dung sai phân loại theo loại case** \([README](README.md) mục 6\.3\), không so chuỗi\.

### E3 · `formatVN\.ts` định dạng theo chuẩn Mỹ

Docstring của chính file thừa nhận: `1,284,560`, `MM/dd/yyyy`, tuần bắt đầu Chủ nhật\. Người dùng là cơ quan nhà nước Việt Nam đọc số dạng `1\.284\.560` và `34,7`\. Hiển thị lệch dấu thập phân là cùng một họ lỗi với E2, nhưng ở đầu ra\.

**Sửa**: đổi quy ước theo [UI\\\-DESIGN](UI-DESIGN.md) mục 3, mở rộng `formatVN\.test\.ts`\.

> Ba lỗi này cùng một họ: **hệ thống đang coi con số là chuỗi ký tự**\. Mọi kỹ thuật ở mục 3, nhóm T1, tồn tại để sửa gốc vấn đề đó\.

## 3\. Kỹ thuật còn thiếu

Mỗi nhóm nêu: **tính năng phụ thuộc · vì sao cái đang có không đủ · kỹ thuật đề xuất · cách kiểm thử**\.

### T1 · Số học có kiểu: Decimal, đơn vị, bội số, tiền tệ

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910294937&cot=14)*

| Column 1 | Column 2 |
| --- | --- |
| Phục vụ | Mọi tính năng có số: trang chỉ số, cảnh báo, báo cáo, eval |
| Hiện có không đủ | Decimal chỉ xuất hiện khi tuần tự hoá \(api/responses\.py, db\.py\), không dùng khi tính\. unit trong metric contract là text tự do\. Không có bội số, không có FX |
| Đề xuất | Một Quantity bất biến: \{value: Decimal, unit: str, multiplier: int, currency: str \| None\}\. Đại số đơn vị tối thiểu: cộng/trừ đòi cùng đơn vị\+bội số; nhân/chia sinh đơn vị dẫn xuất; tỷ lệ cùng đơn vị → không thứ nguyên\. Làm tròn chỉ một lần, ở cuối, theo decimal\_precision của chỉ số\. FX là một variable trong observation store \(tỷ giá có kỳ và nguồn\), không phải hằng số |
| Không dùng pint | Đơn vị ANKT là đơn vị nghiệp vụ \(“nghìn tỷ đồng”, “% GDP”, “điểm %”\), không phải đơn vị vật lý\. Một bảng đơn vị nội bộ 30 dòng đúng hơn và kiểm thử được |
| Kiểm thử | Golden calculation ECO\-009 chạy như unit test mỗi build \(SERVICE\-DESIGN mục 3\.3\); property test: a\+b\-b == a với Decimal, sai với float |

### T2 · Lưu trữ point\-in\-time \(vintage\)

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910294945&cot=14)*

| Column 1 | Column 2 |
| --- | --- |
| Phục vụ | Trang chỉ số \(chuyển vintage\), tái lập đáp án, eval vintage\_correct, tam giác điều chỉnh |
| Hiện có không đủ | Không có khái niệm vintage ở bất kỳ đâu\. Freshness \(data\_as\_of\) trả lời “dữ liệu cũ bao lâu”, không trả lời “bản công bố nào” |
| Đề xuất | ClickHouse ReplacingMergeTree, khoá tự nhiên \(series\_key, period, release\_id\), truy vấn point\-in\-time bằng argMax\(value, release\_date\) với release\_date &lt;= as\_of \(SERVICE\-DESIGN mục 3\.2\)\. Append\-only tuyệt đối: số điều chỉnh là bản ghi mới |
| Bẫy | ReplacingMergeTree gộp bất đồng bộ, nên truy vấn phải luôn có argMax/FINAL — không được giả định đã gộp\. Phải có test cho trường hợp hai bản ghi cùng khoá chưa gộp |
| Kiểm thử | Nạp 3 vintage của cùng một kỳ, khẳng định truy vấn as\_of trả đúng bản của từng mốc; khẳng định ghi lại cùng khoá không tạo bản mới |

### T3 · Formula DAG evaluator

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910294949&cot=14)*

| Column 1 | Column 2 |
| --- | --- |
| Phục vụ | Mọi chỉ số dẫn xuất \(Nợ/GDP, nghĩa vụ trả nợ/thu NSNN\), phân rã biến động, compare\_to |
| Hiện có không đủ | compile\_metric làm sum/count/avg/min/max trên một bảng, một cột\. validate\_identifier chỉ nhận tên cột, nên không biểu diễn được biểu thức |
| Đề xuất | DAG có kiểu: nút lá = variable \(bind tới series hoặc warehouse metric\), nút trong = phép toán \(ratio, sum, growth, share, fx\_convert, lag\)\. Đánh giá theo thứ tự tô\-pô, mỗi cạnh mang Quantity của T1\. Căn kỳ stock/flow: stock lấy giá trị cuối kỳ, flow lấy tổng trong kỳ — sai chỗ này là sai âm thầm\. Phân rã biến động: đóng góp tử số / mẫu số / đánh giá lại, kiểm tra tổng phần = tổng biến động trong dung sai |
| Kiểm thử | Tính lại toàn bộ chuỗi lịch sử và reconcile với số chính thức đã công bố; lệch quá dung sai = chặn phát hành phiên bản chỉ số \(19\) |

### T4 · Ngưỡng theo khoảng hiệu lực

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910294999&cot=14)*

| Column 1 | Column 2 |
| --- | --- |
| Phục vụ | Trạng thái ngưỡng ở mọi bề mặt, alert engine, eval threshold\_status\_correct |
| Hiện có không đủ | Không có |
| Đề xuất | Bảng threshold\(indicator\_version\_id, level, value, valid\_from, valid\_to, legal\_basis\_doc\_id\); chọn theo kỳ dữ liệu, không theo ngày hỏi\. Ràng buộc DB: hai ngưỡng cùng level không được chồng khoảng — dùng EXCLUDE USING gist \(… WITH &amp;&amp;\) của Postgres thay vì kiểm ở tầng ứng dụng |
| Bẫy | Kỳ 2025 không được áp ngưỡng NQ 26/2026 \(hiệu lực 2026–2030\)\. Đây là lỗi mà LLM chắc chắn mắc nếu để nó tự so |
| Kiểm thử | Case eval chuyên cho ranh giới kỳ chuyển ngưỡng |

### T5 · Verifier số có dung sai

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910295009&cot=14)*

| Column 1 | Column 2 |
| --- | --- |
| Phục vụ | Nguyên tắc “không hiển thị số không có trong evidence” \(UI\-DESIGN P8\) |
| Hiện có không đủ | nodes/model\_harness/judge\.py có, nhưng so khớp chuỗi \(cùng lỗi E2\) |
| Đề xuất | Nâng judge thành verifier bốn kiểm tra độc lập: numbers\_grounded \(mọi số trong câu trả lời parse được và nằm trong evidence ± dung sai theo loại case\) · tolerance\_ok · vintage\_consistent \(mọi số trong một câu trả lời cùng chính sách vintage\) · citations\_resolved \(mọi \[n\] giải được tới doc\_id \+ trang tồn tại\)\. Trả kết quả có cấu trúc để UI hiện VerifierBadge |
| Bẫy cần tránh | Không chấm bằng LLM\. Bốn kiểm tra này đều tất định; dùng LLM ở đây là đưa tính bất định vào đúng chỗ cần chắc chắn |

### T6 · Tìm chỉ số hybrid

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910295018&cot=14)*

| Column 1 | Column 2 |
| --- | --- |
| Phục vụ | Danh mục chỉ số, @indicator trong Ask, chọn chỉ số khi hỏi tự do |
| Hiện có không đủ | Resolver “đồng nghĩa dài nhất thắng” là tất định và giải thích được — giữ ưu điểm đó — nhưng không tìm được khi người dùng gọi tên khác |
| Đề xuất | Ba tầng, theo thứ tự: \(1\) khớp đồng nghĩa tất định như hiện nay — nếu trúng thì dừng, không gọi model; \(2\) hybrid BM25 \+ dense trên indicator\_index Qdrant, hợp nhất bằng RRF sẵn có \(rrf\_merge, k=60\); \(3\) LLM chỉ chọn trong top\-k, không tự do sinh\. Mơ hồ → clarification card, không đoán |
| Tái dùng | rrf\_merge, rerank của LightRAG dùng lại nguyên |

### T7 · Trích xuất tài liệu có toạ độ

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910295027&cot=14)*

| Column 1 | Column 2 |
| --- | --- |
| Phục vụ | ECO\-008\.14 \(trích dẫn tới trang/ô\), workbench duyệt, DocumentPageViewer |
| Hiện có không đủ | E1 \(OCR tắt\) \+ Docling trả prov \(page\_no, bbox\) nhưng ParsedChunk không lưu |
| Đề xuất | \(a\) Bật OCR, chọn model qua benchmark — ứng viên: Tesseract 5 vie làm baseline CPU, PaddleOCR PP\-OCRv5, VietOCR, Surya; \(b\) lưu page\_no/bbox/confidence vào ParsedChunk và payload Qdrant; \(c\) render ảnh trang PNG vào MinIO ankt\-derived để UI overlay SVG; \(d\) bảng → dữ liệu từng ô, không chỉ chunk markdown |
| Chỉ số đo | Độ chính xác token số \(từng chữ số, và dấu phân cách ,/\.\), không phải CER/WER — CER thấp vẫn có thể sai đúng chữ số quan trọng\. Bảng: Cell F1, TEDS, exact match các ô mục tiêu |
| Ngân sách | A100 40GB đã cấp ~32 GB; VLM 7B không chạy thường trực cùng chat được → chạy theo lô ngoài giờ \(README mục 7\.3\) |

### T8 · IQL / QuerySpec compiler

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910295042&cot=14)*

| Column 1 | Column 2 |
| --- | --- |
| Phục vụ | Explorer, chip ngữ cảnh, thang điều khiển L1–L3 \(ASK\-WORKSPACE mục 4\), hợp đồng render theo intent |
| Hiện có không đủ | AnalyticalSlots là tiền thân đúng hướng nhưng thiếu: vintage, transform, compare đa chiều, ngưỡng, và compare\_to chưa compile |
| Đề xuất | Mở rộng AnalyticalSlots thành QuerySpec \(ASK\-WORKSPACE mục 11\.2\) — một schema, source\.kind phân nhánh warehouse/indicator/session\_file, không tạo ngôn ngữ thứ ba\. Compiler kiểm trước khi chạy: chỉ số tồn tại và đã duyệt · tần suất có dữ liệu · kỳ đã công bố · quyền theo phân loại → trả lỗi có cấu trúc để agent hỏi lại hoặc từ chối đúng, thay vì đoán |
| Tái dùng | Cơ chế interrupt\(\) của LangGraph đã sẵn cho “hỏi lại giữa chừng” |

### T9 · MCP đúng giao thức \+ tool registry động

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910295049&cot=14)*

| Column 1 | Column 2 |
| --- | --- |
| Phục vụ | Agent core mở rộng, cắm MCP server ngoài, client ngoài \(IDE/BI\) |
| Hiện có không đủ | Node mcp gọi HTTP POST tự định dạng, không theo giao thức MCP → không khám phá tool động, không cắm được server chuẩn |
| Đề xuất | MCP client Streamable HTTP thật, xác thực bằng service assertion sẵn có\. Tool registry động: mỗi sub\-agent chỉ thấy tool của vai trò; khi số tool lớn, chọn tool theo embedding mô tả \+ ngữ cảnh\. Tách nhóm tool coding \(bash, file\_write, file\_edit, glob, grep\) khỏi agent phân tích |

### T10 · Thu thập dữ liệu: lịch, phát hiện thay đổi, connector

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910295052&cot=14)*

| Column 1 | Column 2 |
| --- | --- |
| Phục vụ | 38 nguồn trong sources\.yaml; SLA độ tươi; lịch công bố trên UI |
| Hiện có không đủ | SchedulingCenter lưu cron vào platform settings nhưng không có scheduler chạy\. croniter đã có trong requirements nhưng không có vòng lặp thực thi |
| Đề xuất | K8s CronJob cho giai đoạn đầu → Dagster khi &gt; ~20 nguồn \(ADR\-2\)\. Phát hiện bản mới theo ba mức, rẻ trước: ETag/Last\-Modified → hash nội dung SHA\-256 → diff cấu trúc trang\. Connector plugin: http\-file \(10 nguồn\), rest\-api \(9\), sdmx \(5\), web\-crawl \(5\+1 headless\), bulk\-parquet \(3\), rss \(1\), manual\-upload \(1\) — đúng phân bố trong sources\.yaml |
| SDMX | Dùng sdmx1 \(pandaSDMX kế nhiệm\) cho IMF/WB/OECD/BIS; ánh xạ codelist SDMX \(REF\_AREA, FREQ, UNIT\_MULT\) làm chuẩn chiều dữ liệu nội bộ \(ADR\-8\) |
| Idempotency | Cùng hash → không tạo release mới; khác hash → release mới, giữ bản cũ |

### T11 · Chất lượng dữ liệu và đối chiếu

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910295058&cot=14)*

| Column 1 | Column 2 |
| --- | --- |
| Phục vụ | Workbench duyệt \(tô ô đáng ngờ\), reconciliation, cảnh báo lệch nguồn |
| Hiện có không đủ | check\_subtotals, check\_units đã có nhưng chưa nối vào pipeline dữ liệu; chỉ đang dùng cho SQL |
| Đề xuất | Rule engine nội bộ \(không cần Great Expectations cho quy mô này\): miền giá trị · đơn vị hợp lệ · tổng thành phần = tổng \(tái dùng check\_subtotals\) · biến động bất thường so kỳ trước \(z\-score trên log\-diff, hoặc MAD cho chuỗi ngắn\) · so release trước · so nguồn đối chiếu\. Ba mức: chặn / cảnh báo / thông tin |
| Bẫy | Chuỗi kinh tế Việt Nam ngắn \(10–30 điểm/năm\) và có điểm gãy \(đánh giá lại GDP 2020\)\. Z\-score thường sẽ báo động giả ở điểm gãy → phải cho phép khai báo structural break trong catalog |

### T12 · Alert engine

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910295060&cot=14)*

| Column 1 | Column 2 |
| --- | --- |
| Phục vụ | Hộp cảnh báo, thông báo, luồng 21 |
| Hiện có không đủ | Chỉ có node send\_notification trong workflow |
| Đề xuất | Consumer indicator\.recomputed \+ source\.sla\.breached \+ reconciliation\.failed\. Dedup theo \(rule\_id, indicator\_id, period\) với cửa sổ thời gian — nếu không, một release chạm ngưỡng sẽ sinh cảnh báo cho mọi chỉ số dẫn xuất\. Vòng đời open → acknowledged → resolved \| false\_positive \(47\) |

### T13 · Lineage và phân tích tác động

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910295077&cot=14)*

| Column 1 | Column 2 |
| --- | --- |
| Phục vụ | “Ai bị ảnh hưởng khi số liệu sửa”, chọn golden case cần chạy lại |
| Hiện có không đủ | Chỉ có cột upstream trong schema catalog; Neo4j đã có nhưng dùng cho entity graph của KB |
| Đề xuất | Database Neo4j riêng lineage, consumer ghi cạnh từ envelope sự kiện đã có \(source\_id, release\_id, artifact\_sha256, trace\_id\)\. Truy vấn tác động = duyệt xuôi từ release tới Answer/Report/Alert\. Không cần OpenMetadata/DataHub ở quy mô này \(ADR\-5\) |

### T14 · Eval cho số liệu

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910295087&cot=14)*

| Column 1 | Column 2 |
| --- | --- |
| Phục vụ | Release gate thật, regression khi dữ liệu đổi |
| Hiện có không đủ | Scorer sql và policy tất định và tốt; scorer grounding mắc E2; ngưỡng online provisional, chưa có baseline |
| Đề xuất | \(a\) Case schema v2 có expected, tolerance, source, vintage, expected\_threshold\_status \(README mục 6\.2\); \(b\) bốn scorer độc lập value / source / vintage / threshold\_status \+ refusal\_correct; \(c\) sinh case từ observation \+ template câu hỏi, người duyệt xác nhận; \(d\) regression theo lineage: observation\.published → chọn case bị ảnh hưởng qua T13 → chạy lại; \(e\) chạy baseline thật và bỏ cờ provisional |

### T15 · Kết xuất báo cáo

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910295098&cot=14)*

| Column 1 | Column 2 |
| --- | --- |
| Phục vụ | Bản tin PDF/DOCX có phụ lục nguồn |
| Hiện có không đủ | Chỉ có build\_xlsx \(openpyxl, sheet “Dữ liệu”/“Nguồn”\) — mẫu tốt để mở rộng |
| Đề xuất | DOCX bằng python\-docx từ template có sẵn của cơ quan; PDF bằng WeasyPrint \(HTML→PDF, kiểm soát được kiểu chữ Việt\) thay vì LaTeX\. Mọi khối số giữ nguyên trích dẫn; phụ lục nguồn sinh tự động từ source\_ref\. Đây là yêu cầu văn bản hành chính, không phải tuỳ chọn |

### T16 · Thông báo realtime

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910295206&cot=14)*

| Column 1 | Column 2 |
| --- | --- |
| Phục vụ | Hộp thư, giao việc, cảnh báo |
| Hiện có không đủ | Không có |
| Đề xuất | SSE \(đã dùng cho câu trả lời, tái dùng hạ tầng\) \+ Redis pub/sub để fan\-out giữa replica — cùng kỹ thuật cache\_invalidation\.py đang dùng, chỉ khác kênh\. Tuỳ chọn nhận theo người dùng lưu Postgres |

### T17 · Gói phân tích chuỗi thời gian trong sandbox

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910295221&cot=14)*

| Column 1 | Column 2 |
| --- | --- |
| Phục vụ | Phân rã biến động nâng cao, ô pandas trong Ask, dự phóng/DSA \(GĐ3\) |
| Hiện có không đủ | ALLOWED\_MODULES chỉ có pandas, numpy, plotly, matplotlib, json, math, datetime, statistics |
| Đề xuất | Thêm scipy, statsmodels vào whitelist và vào image sandbox\. Khai báo rõ danh sách gói ra UI để người dùng không đoán\. Giữ nguyên denylist call — đây là mở rộng whitelist, không nới lỏng cơ chế |

### T18 · Bật và đo các kỹ thuật đang tắt

Rẻ nhất trong toàn bộ danh sách, làm trước T6:

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910295237&cot=14)*

| Cờ | Mặc định | Vì sao nên bật cho ANKT |
| --- | --- | --- |
| HYBRID\_LEXICAL | 0 | Văn bản pháp luật phải khớp đúng số hiệu \(“59/2026/QH15”, “NQ 26/2026”\)\. Dense embedding không giữ chuỗi định danh; lexical giữ |
| DOC\_NARROWING | 0 | Corpus pháp luật lớn \(171k văn bản theo SERVICE\-DESIGN mục 5\.3\); thu hẹp tài liệu trước khi lấy chunk giảm nhiễu |
| QUERY\_ALIAS\_RESOLVE | 0 | Tên cơ quan và chỉ tiêu có nhiều biến thể \(“Cục Thống kê” / “Tổng cục Thống kê” / “NSO”\) |
| DEGRADE\_BUDGET\_MS | 0 | Có ngân sách độ trễ \(TTFT ≤ 2s\) thì phải có suy giảm có kiểm soát, nếu không sẽ treo |

**Cách làm đúng**: bật từng cờ, đo trên bộ retrieval của golden set \(Recall@5/10, MRR@10, nDCG@10, p95 latency\), giữ cờ nào cải thiện\. Đây là công việc **đo đạc**, không phải công việc code\.

## 4\. Ma trận tính năng → kỹ thuật

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910295251&cot=14)*

| Tính năng đã define | Kỹ thuật cần | Đã có | Còn thiếu |
| --- | --- | --- | --- |
| Hỏi đáp có trích dẫn tới trang | LightRAG · chunking · Docling | truy xuất, rerank, RRF, cache | T7 \(OCR \+ bbox\) |
| Thẻ giá trị chỉ số | — | — | T1, T2, T3, T4 |
| Trang chỉ số có dải ngưỡng | — | — | T2, T3, T4 |
| Chuyển vintage · tam giác điều chỉnh | — | — | T2 |
| Phân rã biến động | — | — | T3, T17 |
| So sánh quốc tế \+ cảnh báo phạm vi | — | — | T10 \(SDMX\), T1 \(đơn vị\) |
| Danh mục &amp; tìm chỉ số | resolver tất định | resolver, RRF, rerank | T6 |
| Ask: SQL ad\-hoc | SQL guard, sandbox, result store | guard 2 lớp, pool, row cap | endpoint gateway \(ASK\-WORKSPACE 11\.4\) |
| Ask: thao tác BI, pivot | result store result\_id | kho kết quả MinIO \+ /rows | T8 \(QuerySpec\), reshape server |
| Ask: gắn ngữ cảnh, tải tệp | upload bronze \+ SSE tiến trình | có ở KB, chưa nối Ask | payload context\_refs |
| Từ chối có cấu trúc | ambiguity reasons | mã lý do đã có | T8 \(lỗi compiler có cấu trúc\) |
| Workbench duyệt số liệu | maker–checker | mô hình duyệt schema bảng | T7, T11, value\_edit |
| Cấu hình nguồn &amp; lịch | croniter | thư viện có, không có runner | T10 |
| Cảnh báo theo ngưỡng | Kafka consumer | hạ tầng sự kiện | T4, T12 |
| Hộp thư realtime | SSE \+ Redis pub/sub | cả hai đã dùng ở chỗ khác | T16 |
| Bản tin PDF/DOCX | openpyxl | xlsx có sheet Nguồn | T15 |
| Golden set có dung sai | scorer \+ gate | khung tất định, gate | T14 \(và sửa E2\) |
| Lineage &amp; tác động | Neo4j, envelope sự kiện | cả hai đã có | T13 |
| Quyền theo phân loại | RLS, audit, service assertion | RLS \+ audit | ABAC \(auth\-api\), row policy ClickHouse |
| MCP server chuẩn | — | node mcp tự chế | T9 |

**Tổng hợp trạng thái kỹ thuật**: 34 đã có · 12 có một phần · 31 chưa có\.

## 5\. Quyết định kỹ thuật cần chốt

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910295255&cot=14)*

| \# | Quyết định | Phương án | Đề xuất |
| --- | --- | --- | --- |
| ADR\-T1 | Kiểu số trong tính toán | float64 · Decimal · int theo bội số nhỏ nhất | Decimal với Quantity bao ngoài; float chỉ dùng cho thống kê/đồ thị |
| ADR\-T2 | Thư viện đơn vị | pint · bảng nội bộ | Bảng nội bộ — đơn vị nghiệp vụ, không phải đơn vị vật lý |
| ADR\-T3 | Kho observation | Postgres phân vùng · ClickHouse | ClickHouse ReplacingMergeTree, luôn truy vấn bằng argMax |
| ADR\-T4 | Ràng buộc ngưỡng không chồng kỳ | kiểm ở ứng dụng · EXCLUDE USING gist | Ràng buộc DB — dữ liệu sai không vào được, không phụ thuộc đường ghi |
| ADR\-T5 | Verifier số | LLM judge · kiểm tra tất định | Tất định bốn kiểm tra; LLM không tham gia chấm số |
| ADR\-T6 | Ngôn ngữ truy vấn | mở rộng slots · IQL riêng · SQL trên semantic layer | Mở rộng AnalyticalSlots thành QuerySpec — một schema, không phải ba |
| ADR\-T7 | Tìm chỉ số | tất định · dense · ba tầng | Ba tầng: đồng nghĩa → hybrid RRF → LLM chọn trong top\-k |
| ADR\-T8 | Model OCR/VLM | chọn ngay · chọn qua benchmark | Benchmark theo độ chính xác token số; lưu model\_version vào mọi kết quả |
| ADR\-T9 | Rule DQ | tự xây · Great Expectations/Soda | Tự xây — quy mô rule nhỏ, cần tích hợp chặt với dung sai và check\_subtotals sẵn có |
| ADR\-T10 | Render PDF | LaTeX · WeasyPrint · headless Chrome | WeasyPrint — kiểm soát kiểu chữ Việt, không cần trình duyệt |
| ADR\-T11 | Scheduler | CronJob · Dagster · Airflow | CronJob → Dagster khi &gt; ~20 nguồn |
| ADR\-T12 | Bật cờ LightRAG | giữ tắt · bật hết · bật và đo từng cái | Bật và đo từng cái trên bộ retrieval của golden set |

## 6\. Rủi ro kỹ thuật, xếp theo mức nguy hiểm

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910295260&cot=14)*

| \# | Rủi ro | Vì sao nguy hiểm | Giảm thiểu |
| --- | --- | --- | --- |
| R1 | Sai một chữ số hoặc dấu thập phân trong chuỗi OCR → mapping → tính → hiển thị | Sinh ra câu trả lời sai nhưng trông đáng tin, không có gì trong câu trả lời để người đọc nhận ra | Đo độ chính xác token số; Decimal; verifier tất định; human\-in\-the\-loop bắt buộc cho số trích từ PDF; sửa E1/E2/E3 |
| R2 | Sai căn kỳ stock/flow \(dư nợ cuối kỳ chia GDP cả năm vs GDP quý\) | Sai theo hệ số, không phải sai làm tròn — cùng họ với fan\-out mà aggregation\_check đã lo ở phía SQL | Khai báo stock\_or\_flow bắt buộc trong catalog; kiểm tra kiểu ở compile time của formula DAG; golden calculation |
| R3 | Áp nhầm ngưỡng giai đoạn | Kết luận “vượt ngưỡng” sai làm hỏng giá trị nghiệp vụ của cả hệ thống | Chọn ngưỡng theo kỳ dữ liệu; ràng buộc DB không chồng khoảng; eval case ở ranh giới kỳ |
| R4 | ReplacingMergeTree chưa gộp → đọc ra bản cũ | Im lặng, không lỗi | Luôn argMax; test với hai bản ghi cùng khoá chưa gộp |
| R5 | Dense\-only retrieval trượt số hiệu văn bản | Trả lời “không tìm thấy” cho tài liệu đã có | Bật HYBRID\_LEXICAL và đo |
| R6 | GPU dùng chung giữa chat và trích xuất | VLM 7B không chạy thường trực cùng chat trong ~8 GB còn lại | Trích xuất chạy batch ngoài giờ; nếu lên production thì bổ sung GPU riêng |
| R7 | Gate online chưa chứng nhận gì \(provisional, chưa có baseline\) | Tưởng có gate mà thực ra không | Chạy baseline thật, bỏ cờ provisional |
| R8 | Báo động giả của DQ ở điểm gãy chuỗi \(đánh giá lại GDP\) | Steward mất niềm tin vào cảnh báo rồi bỏ qua cả cảnh báo thật | Khai báo structural break trong catalog; dùng MAD thay z\-score cho chuỗi ngắn |

## 7\. Lộ trình kỹ thuật

Ước lượng theo **tuần\-người** backend/data \(không gồm frontend — xem [UI\\\-DESIGN](UI-DESIGN.md) mục 11 và [ASK\\\-WORKSPACE](ASK-WORKSPACE.md) mục 12\)\.

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910295263&cot=14)*

| Đợt | Kỹ thuật | Tuần\-người | Mở khoá được gì |
| --- | --- | --- | --- |
| 0 · Sửa lỗi đang chạy | E1 bật OCR · E2 scorer số · E3 formatVN · T18 bật và đo cờ LightRAG | ~2 | Ngừng tạo ra số sai trông đáng tin; retrieval tốt hơn mà không viết mã mới |
| 1 · Lõi số \(chặn Demo 1\) | T1 Quantity/Decimal · T2 observation \+ point\-in\-time · T3 formula DAG · T4 ngưỡng theo kỳ · T5 verifier · T7 bbox \+ ảnh trang | ~5 | Demo 1: trả lời Nợ công/GDP có giá trị, vintage, ngưỡng, trích dẫn tới trang |
| 2 · Truy vấn &amp; công cụ | T6 tìm chỉ số hybrid · T8 QuerySpec compiler · T9 MCP chuẩn \+ tool registry · T17 scipy/statsmodels | ~5 | Hàng trăm chỉ số; Explorer; Ask Workspace tầng L1–L3 |
| 3 · Dữ liệu vào | T10 scheduler \+ connector \(file/web/REST/SDMX\) · T11 DQ \+ reconciliation · maker–checker có value\_edit | ~7 | Demo 2: 6 chỉ tiêu nhóm A tự cập nhật, có đối chiếu |
| 4 · Vận hành nghiệp vụ | T12 alert engine · T13 lineage · T15 báo cáo · T16 thông báo | ~5 | Demo 3: cảnh báo → xử lý → bản tin, khép kín |
| 5 · Đánh giá | T14 eval số \+ sinh case \+ regression \+ baseline thật | ~2 | Release gate thật sự chặn được |

**Tổng ~26 tuần\-người**, trong đó **~7 tuần đầu \(đợt 0\+1\) là đường găng của Demo 1**\.

**Làm được ngay tuần này, không phụ thuộc gì:**

1. Bật `do\_ocr` và chạy thử trên 20 trang bản tin nợ công để có con số CER/độ chính xác token số làm baseline \(E1\)\.
2. Viết parser số nhận biết locale \+ scorer dung sai, chạy trên dữ liệu giả lập \(E2 → T5, T14\)\.
3. Sửa `formatVN\.ts` \+ mở rộng test \(E3\)\.
4. Bật `HYBRID\_LEXICAL=1` trên môi trường dev, đo Recall@10 trên 20 câu hỏi có số hiệu văn bản \(T18\)\.
5. Lưu `page\_no`/`bbox` từ `prov` của Docling vào `ParsedChunk` — thay đổi nhỏ, mở khoá toàn bộ chuỗi trích dẫn \(T7\)\.
