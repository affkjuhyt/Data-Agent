# ASK\-WORKSPACE

# Ask Workspace — thiết kế không gian làm việc phân tích

> Phiên bản 16/09/2026 · Đối tượng: frontend, backend agent runtime, thiết kế sản phẩm, nhóm Data Research\.<br>Tài liệu này đi sâu vào **một màn hình duy nhất**: “Hỏi dữ liệu”\. Mục 6\.4 của [UI\\\-DESIGN\\\.md](UI-DESIGN.md) chỉ nêu 6 điểm nâng cấp — không đủ\. Đây là bản thiết kế đầy đủ\.<br>Căn cứ: khảo sát `backend/services/workflow\_runtime`, `backend/services/kb\_backend`, `backend/services/sandbox\_executor` và `frontend/src/features/ask`\.<br>Sơ đồ: [41](diagrams/41-ask-workspace-layout.puml) ba bề mặt · [42](diagrams/42-control-ladder.puml) thang điều khiển · [43](diagrams/43-context-assembly.puml) lắp ngữ cảnh · [44](diagrams/44-seq-block-manipulation.puml) thao tác trên block\.

## 0\. Ý tưởng trung tâm

Ask hiện là **một khung chat trả về văn bản kèm biểu đồ**\. Nó phải trở thành **một không gian làm việc phân tích** gồm ba bề mặt liên kết \([41](diagrams/41-ask-workspace-layout.puml)\):

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910465046&cot=14)*

| Bề mặt | Vai trò | Câu một dòng |
| --- | --- | --- |
| Ngữ cảnh \(trái\) | Tập nguồn agent được phép nhìn, người dùng kiểm soát tường minh | “Agent nhìn thấy gì” |
| Hội thoại \(giữa\) | Dòng lệnh bằng tiếng Việt để tạo và sửa block | “Tôi muốn gì” |
| Canvas \(phải\) | Nơi kết quả sống thành block có danh tính, thao tác được bằng chuột và bàn phím | “Kết quả và tôi làm gì với nó” |

Bốn ý tưởng chống đỡ toàn bộ thiết kế:

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910465053&cot=14)*

| \# | Ý tưởng | Vì sao quan trọng |
| --- | --- | --- |
| 1 | Mọi kết quả là một block có block\_id và một QuerySpec | Block tái lập được, có lịch sử phiên bản, ghim được, đưa vào báo cáo được, đặt cảnh báo được\. Chat không còn là nơi kết quả trôi mất sau 10 lượt hỏi |
| 2 | Năm tầng điều khiển trên cùng một block — lời nói → chip → kéo thả → form → SQL \(42\) | Đây là câu trả lời cho “linh hoạt với người dùng”: lãnh đạo dừng ở tầng 0, chuyên viên sống ở tầng 2–3, analyst kỹ thuật xuống tầng 4\. Cùng một artifact, không phải ba sản phẩm |
| 3 | Ngữ cảnh là danh sách tường minh, không phải phép màu | Agent không tự quét toàn bộ kho\. Người dùng gắn bảng/tài liệu/chỉ số/tệp bằng @, thấy được và sửa được trước khi gửi\. Đây vừa là tính năng, vừa là điều kiện để tin câu trả lời |
| 4 | Phân biệt chỉnh hình và chạy lại | Kéo thả trên dữ liệu đã có phải tức thì \(không gọi backend\)\. Chỉ khi spec đổi mới chạy lại, qua guard và audit\. Nhầm hai việc này là lý do các công cụ “BI có AI” chậm và không đáng tin |

## 1\. Khoảng cách: backend đã có, Ask chưa phơi bày

Phát hiện quan trọng nhất của đợt khảo sát này: **phần lớn năng lực cần cho một không gian phân tích đã nằm trong backend**, chỉ chưa có đường ra giao diện\.

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910465057&cot=14)*

| Năng lực backend đã có | Ở đâu | Ask hiện phơi bày |
| --- | --- | --- |
| 15 công cụ gồm execute\_sql, execute\_python, text\_to\_pandas, text\_to\_sql, calculator, web\_search, wikipedia, delegate\_to\_agent, file\_read/write/edit, glob, grep, bash | nodes/sandbox/tool\_executor/handlers/ | Không cái nào — người dùng không biết agent có thể chạy SQL hay Python |
| Kho kết quả có địa chỉ: mỗi câu trả lời sinh result\_id, lưu MinIO tenant/\{t\}/ask\-results/\{id\} \(payload\.json \+ answer\.xlsx với sheet “Dữ liệu”/“Nguồn”\) | answer\_exports\.py, routers/ask\_exports\.py → /api/ask/results/\{id\}/rows, /export\.xlsx | Chỉ dùng để phân trang bảng và tải xlsx\. Đây chính là nền cho pivot/kéo thả không cần chạy lại |
| Schema catalog đầy đủ: bảng, cột, row\_count, last\_altered, text\_description, join\_hints, owner, classification, data\_as\_of, freshness\_status | routers/schema\_catalog\.py → GET /api/schema\-catalog | Không dùng trong Ask\. Đây là nền cho trình duyệt nguồn kéo thả |
| Schema memory: mô tả nghiệp vụ từng cột \+ sample\_values \+ access\_roles | routers/schema\_memory\.py | Không dùng trong Ask |
| Sandbox có hàng đợi và stream theo job: kind = sql / python / shell, POST /run/\{kind\}, GET /api/sandbox/stream/\{kind\}/\{job\_id\}, GET /api/sandbox/status | sandbox\_executor/main\.py, job\_queue\.py | Không dùng trong Ask — không có cách nào xem truy vấn đang chạy |
| Semantic layer: câu hỏi khớp metric đã duyệt → SQL biên dịch tất định, không để LLM viết | semantic\_layer\.match\_question, handlers/data\.py: \_semantic\_result | Có hiển thị trong EvidencePanel, nhưng không có nút đăng ký metric mới — dù panel đã gợi ý đúng điều đó |
| KB có phạm vi: collection, knowledge\_base trong RetrieveRequest; /api/knowledge/collections; /\{doc\_id\}/tables trả bảng Docling đã tách | kb\_backend/schemas/retrieval\.py, routes kb | Không cho chọn phạm vi khi hỏi |
| Nạp tài liệu \+ tiến trình realtime: POST /bronze, /bronze/web, GET /documents/\{id\}/stream, promote/\{id\}/silver\|gold | kb\_backend | Chỉ có ở màn Tri thức, không có ở Ask |
| Hội thoại lưu phía server: /api/conversations, /api/conversations/\{id\}/messages | routers/ | Ask vẫn lưu lịch sử ở localStorage |
| Trace và chi phí: /api/traces/\{conv\_id\}, /api/orchestrate/traces/\{trace\_id\}, /api/billing/spend | orchestrator/routers/traces\.py | Chỉ có ở Playground |

**Và đây là điểm nghẽn cụ thể** — payload gửi đi từ Ask \([api\\\.ts](../../frontend/src/features/ask/api.ts)\):

```typescript
{ query, agent_id?, user_id, messages, conversation_id?, clarification_answers? }
```

Không có trường nào để nói *“hỏi trong phạm vi bảng này”*, *“dùng tài liệu này”*, *“đây là tệp tôi vừa tải lên”*, *“sửa block số 3”*\. Mọi tính năng trong tài liệu này đều bắt đầu từ việc mở rộng payload đó \(mục 11\.1\)\.

Hệ quả thiết kế: **phần lớn công việc là frontend \+ một lớp API mỏng**, không phải xây service mới\. Đây là tin tốt cho lộ trình\.

## 2\. Ba bề mặt

### 2\.1 Rail Ngữ cảnh — “agent nhìn thấy gì”

Một cột trái gập được, chia bốn nhóm\. Mỗi mục có **công tắc bật/tắt** và **nhãn phân loại**\.

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910465062&cot=14)*

| Nhóm | Nội dung | Nguồn API |
| --- | --- | --- |
| Nguồn dữ liệu | Cây warehouse → schema → bảng → cột\. Mỗi bảng hiện: số dòng, độ tươi \(badge đã có\), chủ sở hữu, phân loại, mô tả nghiệp vụ, join\_hints | GET /api/schema\-catalog, GET /api/schema\-memory |
| Tri thức | Bộ sưu tập → tài liệu\. Lọc theo hiệu lực văn bản, cơ quan ban hành, ngày | GET /api/knowledge/collections, /documents |
| Chỉ số | Cây taxonomy → chỉ số đã duyệt, kèm trạng thái ngưỡng và độ phủ dữ liệu | GET /indicators \(chưa có\) |
| Phiên làm việc | Tệp đã tải lên phiên này · block đã ghim làm đầu vào · rổ so sánh | cục bộ \+ POST /api/knowledge/bronze |

Ba chi tiết quyết định chất lượng:

1. **Tìm kiếm trong rail** \(`Ctrl\+K` cũng vào được\): với vài trăm bảng và hàng trăm chỉ số, cây không duyệt tay được\.
2. **Ngân sách ngữ cảnh** hiển thị ở chân rail: “3 bảng · 2 tài liệu · 1 tệp · ~12k token”\. Người dùng thấy mình đang bắt agent đọc bao nhiêu, và hệ thống cảnh báo khi vượt ngưỡng làm chậm hoặc làm loãng câu trả lời\.
3. **Xem trước không rời màn**: click một bảng → drawer hiện 20 dòng mẫu \+ kiểu cột; click tài liệu → trang đầu; click chỉ số → thẻ giá trị\. Quyết định “có gắn nó không” không nên phải mở tab khác\.

### 2\.2 Hội thoại — dòng lệnh

Giữ khung hội thoại hiện có, thêm bốn cơ chế:

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910465065&cot=14)*

| Cơ chế | Thiết kế |
| --- | --- |
| @\-mention | Gõ @ mở bộ chọn hợp nhất: bảng · cột · tài liệu · chỉ số · block trước · tệp\. Chọn xong chèn một chip tham chiếu vào ô nhập, không dán nội dung\. @fact\_debt\.amount gắn tới tận cột |
| Chip ngữ cảnh | Dải chip trên ô nhập hiển thị slot agent đã suy ra \(kỳ · vintage · vùng · tần suất · nguồn\), mỗi chip ghi rõ nguồn gốc: người dùng nhập · agent suy · mặc định hệ thống\. Sửa chip → chạy lại chỉ field đó |
| Nhắm mục tiêu block | “Làm lại block \#3 theo quý” — hoặc bấm Hỏi về block này trên BlockCard, hội thoại tự gắn target\_block\_id\. Không phải mô tả lại kết quả bằng lời |
| Tường thuật tiến trình | Thay con trỏ nhấp nháy bằng dòng trạng thái thật: “đang lấy schema 3 bảng” → “đang chạy SQL \(4,2s\)” → “đang kiểm số với evidence”\. Lấy từ frame \_\_progress \(mục 11\.4\)\. Với câu đa chỉ số ngân sách 30s \(PLATFORM\-ARCHITECTURE mục 4\.1\), đây là khác biệt giữa “đang chạy” và “hỏng rồi” |

### 2\.3 Canvas — nơi block sống

Vùng phải \(hoặc toàn màn khi gập hội thoại\), bố cục lưới kéo thả được\. Block ghim ở lại khi hội thoại cuộn tiếp\.

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910465070&cot=14)*

| Tính năng canvas | Mô tả |
| --- | --- |
| Lưới kéo thả | Sắp xếp, đổi kích thước, nhóm block\. Bố cục lưu theo workspace |
| So sánh cạnh nhau | Ghim 2–4 block cạnh nhau \(ví dụ: số VN và số IMF cùng chỉ số\) |
| Lọc chéo | Chọn một điểm trên block A → lọc block B cùng nguồn\. Tắt được, và luôn hiện đang có lọc chéo nào |
| Undo/redo | Trên toàn canvas: Ctrl\+Z hoàn tác cả thao tác kéo thả lẫn xoá block |
| Chế độ trình bày | Ẩn rail và hội thoại, chỉ còn canvas — dùng khi họp |

## 3\. Mô hình block

### 3\.1 Giải phẫu một block

```javascript
{
  "block_id": "blk_7f3a",
  "title": "Nợ công/GDP 2019–2024",            // sửa được
  "kind": "indicator_value | table | chart | pivot | sql | notebook | document | text",
  "spec": { /* QuerySpec — mục 11.2 */ },
  "result": {
    "result_id": "a1b2…",                       // /api/ask/results/{id}/rows
    "columns": [...], "row_count": 24, "rows_capped": false
  },
  "encoding": { "x": "period", "y": ["ratio"], "color": null, "chart_type": "line" },
  "evidence": { "runs": [...], "citations": [...] },   // cấu trúc đã có
  "verification": { "numbers_grounded": true, "tolerance_ok": true, "vintage_consistent": true },
  "provenance": { "created_by": "agent | user", "turn_id": "...", "parent_block_id": null },
  "versions": [ { "v": 1, "spec_hash": "...", "at": "...", "by": "...", "change": "thêm chiều product_line" } ],
  "pinned": true, "layout": { "x": 0, "y": 2, "w": 6, "h": 4 }
}
```

### 3\.2 Loại block và thao tác

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910465077&cot=14)*

| Loại | Sinh ra từ | Thao tác đặc thù |
| --- | --- | --- |
| indicator\_value | intent lookup/compare\_threshold | Đổi kỳ · đổi vintage · mở trang tài liệu · đặt cảnh báo |
| table | text\_to\_sql, execute\_sql, bảng trong tài liệu | Sort · lọc · pivot · thêm cột tính · xuất |
| chart | \_\_chart frame, hoặc đổi từ table | Shelves mã hoá · đổi loại · chú thích điểm |
| pivot | từ table | Hàng/cột/giá trị/phép gộp · drill\-down |
| sql | mở editor từ block khác, hoặc gõ trực tiếp | Chạy · diff với SQL của agent · đăng ký metric |
| notebook | ô Python/pandas | Chạy · xem stdout/biểu đồ · gắn kết quả thành block mới |
| document | trích dẫn, tệp tải lên | Xem trang \+ highlight · trích ô bảng ra thành table |
| text | ghi chú người dùng, đoạn agent soạn | Soạn thảo · dùng làm khối trong báo cáo |

### 3\.3 Vòng đời

`tạo` → `chỉnh hình` \(không chạy lại\) → `chạy lại` \(spec mới, cùng `block\_id`, thêm phiên bản\) → `ghim` → `chia sẻ / báo cáo / cảnh báo / metric` → `lưu trữ`\.

Mỗi lần chạy lại **giữ nguyên `block\_id`** và đẩy một mục vào `versions` với mô tả thay đổi bằng tiếng Việt\. Người dùng so được hai phiên bản và hoàn tác được — điều kiện để dám thử\.

## 4\. Thang điều khiển — trả lời “flexible với người dùng”

Sơ đồ [42\\\-control\\\-ladder](diagrams/42-control-ladder.puml)\.

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910465089&cot=14)*

| Tầng | Thao tác | Ai dùng | Chạy lại? | Độ trễ mục tiêu | Rào an toàn |
| --- | --- | --- | --- | --- | --- |
| L0 · Lời nói | Đặt câu hỏi tự do | Mọi người | Có | TTFT ≤ 2 s | Guardrail · clarification |
| L1 · Chip | Sửa kỳ · vintage · vùng · tần suất · nguồn | Mọi người | Có \(một field\) | ≤ 3 s | Compiler kiểm trước |
| L2 · Kéo thả \(shelves\) | Cột → X/Y/màu/kích thước/lọc · pivot · top\-N · đổi loại biểu đồ | Chuyên viên | Không, nếu dữ liệu đã đủ | &lt; 150 ms | Chặn gộp trên tập bị cắt \(mục 5\.2\) |
| L3 · Form spec | Thêm chiều · phép biến đổi · so sánh · ngưỡng | Chuyên viên | Có | ≤ 5 s | Xem SQL sinh ra trước khi chạy |
| L4 · SQL / Python | Sửa SQL, viết pandas | Analyst kỹ thuật | Có | tuỳ truy vấn | Read\-only guard · row cap · timeout · audit |

**Quy tắc vàng**: cả năm tầng đọc–ghi **cùng một `QuerySpec`**\. Đi xuống luôn được\. Đi lên được **khi spec còn biểu diễn được** thao tác đã làm\.

**Cửa một chiều cần nói rõ**: sau khi sửa SQL tay \(`spec\.mode = "sql\_freeform"`\), không suy ngược ra form được nữa\. Giao diện phải:

1. Cảnh báo *trước* khi mở editor;
2. Giữ bản spec trước đó để hoàn tác một bấm;
3. Đánh dấu block là “SQL tự sửa” ở mọi nơi nó xuất hiện, kể cả trong báo cáo\.

**Vòng lặp đóng**: từ L3/L4 có nút **“Đăng ký thành metric”** → tạo bản nháp metric contract → maker–checker duyệt → lần sau câu hỏi tương tự đi qua semantic layer, **không còn để LLM viết SQL**\. Chính EvidencePanel hiện tại đã gợi ý điều này bằng chữ \(“Register a metric for this question so it returns the same number next time”\) nhưng không có nút\. Đây là cách hệ thống *học* từ người dùng mà không hy sinh tính tất định\.

## 5\. Thao tác BI trên kết quả

### 5\.1 Hai chế độ, tách bạch tuyệt đối

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910465097&cot=14)*

| Column 1 | Chỉnh hình \(reshape\) | Chạy lại \(re\-query\) |
| --- | --- | --- |
| Chạm backend | Không \(trừ khi cần lấy đủ dòng\) | Có |
| Số có đổi không | Không — cùng tập dữ liệu | Có |
| Chứng cứ/trích dẫn | Giữ nguyên | Sinh lại |
| Độ trễ | &lt; 150 ms | Giây |
| Ví dụ | Đổi loại biểu đồ, pivot, sort, đổi màu, top\-N cục bộ | Thêm chiều, đổi kỳ, đổi bộ lọc nguồn, đổi vintage |

Giao diện phải **cho người dùng biết mình đang ở chế độ nào**: thao tác chạy lại có badge “sẽ chạy lại” và nút xác nhận khi truy vấn nặng\.

### 5\.2 Cạm bẫy phải xử lý: gộp trên tập bị cắt

Backend trả preview bị cắt \(`rows\_capped`, `truncation\.reason` — đã có trong `types\.ts`\)\. **Pivot hoặc top\-N trên tập đã cắt sẽ ra số sai mà trông vẫn hợp lý\.** Quy tắc bắt buộc:

- Thao tác gộp chỉ chạy khi block có đủ dòng \(`row\_count === total`\), nếu không thì **tự lấy đủ** qua `/api/ask/results/\{id\}/rows` trước;
- Nếu tổng số dòng vượt ngưỡng an toàn của trình duyệt \(đề xuất 50\.000\), **không gộp ở client** — chuyển sang re\-query và nói rõ lý do;
- Mọi bảng pivot hiển thị dòng “tính trên N/N dòng”\.

Đây đúng loại lỗi mà [CLAUDE\\\.md](../../CLAUDE.md) và `aggregation\_check` đã lo ở phía SQL; phía client cũng phải lo\.

### 5\.3 Danh mục thao tác

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910465105&cot=14)*

| Thao tác | Chế độ | Ghi chú thiết kế |
| --- | --- | --- |
| Sort, lọc cột, ẩn/hiện cột | Reshape | Đã có sort trong DataGrid\.tsx |
| Đổi loại biểu đồ, đổi mã hoá \(X/Y/màu/kích thước\) | Reshape | Shelves kéo thả; ChartRenderer đã hỗ trợ 8 loại |
| Pivot \(hàng × cột × giá trị × phép gộp\) | Reshape \(có điều kiện 5\.2\) | Kéo cột vào ba vùng |
| Top\-N / Bottom\-N, gộp phần còn lại thành “Khác” | Reshape |  |
| Cột tính thêm \(tỷ trọng, chênh lệch, luỹ kế\) | Reshape | Biểu thức có kiểu, không phải chuỗi tự do |
| So kỳ \(kỳ trước, cùng kỳ năm trước\), YoY, MoM | Re\-query | compare\_to đã có trong slots, chưa compile — README mục 1 |
| Thêm/bớt chiều, đổi tần suất, đổi kỳ | Re\-query |  |
| Đổi vintage | Re\-query | Đặc thù ANKT: đổi vintage là đổi số, không phải đổi hiển thị |
| Drill\-down \(năm → quý → tháng; toàn quốc → tỉnh\) | Re\-query | Đường drill khai báo trong catalog, không để LLM đoán |
| Drill\-through \(từ một ô → các dòng chi tiết tạo ra nó\) | Re\-query | Mở block mới, giữ block gốc |
| Lọc chéo giữa block | Reshape hoặc re\-query | Chỉ nối được block cùng nguồn; nối khác nguồn phải cảnh báo phạm vi |
| Chú thích một điểm dữ liệu | — | Ghi chú gắn \{series, period\}, hiện cho người cùng workspace |

### 5\.4 Kéo thả chỉ ở ba chỗ

Kéo thả ở mọi chỗ làm giao diện khó đoán\. Giới hạn đúng ba đích, mỗi đích có vùng thả hiện rõ khi bắt đầu kéo:

1. **Rail → composer**: gắn bảng/cột/tài liệu/chỉ số làm ngữ cảnh\.
2. **Cột → shelves của block**: đổi mã hoá, pivot, lọc\.
3. **Block → canvas / báo cáo / dashboard**: bố cục và xuất bản\.

Mọi thao tác kéo thả đều **phải có đường bàn phím tương đương** \(menu ngữ cảnh\), vì steward và người dùng dùng bàn phím không thao tác chuột được\.

## 6\. Ngữ cảnh: gắn nguồn, tải tệp, quản nguồn ngay trong Ask

Sơ đồ [43\\\-context\\\-assembly](diagrams/43-context-assembly.puml)\.

### 6\.1 Bốn loại ngữ cảnh

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910465108&cot=14)*

| Loại | Gắn bằng | Agent dùng thế nào |
| --- | --- | --- |
| Nguồn dữ liệu \(bảng, cột\) | @table, kéo từ rail | Giới hạn text\_to\_sql/execute\_sql vào tập bảng này; schema đưa vào prompt chỉ gồm bảng đã gắn |
| Tri thức \(collection, tài liệu\) | @doc, @collection | retrieve với collection/knowledge\_base đã có trong RetrieveRequest |
| Chỉ số | @indicator | IQL với indicators\[\] cố định, không cần bước tìm chỉ số |
| Kết quả trước / tệp phiên | @block, kéo tệp | Tệp thành bảng tạm cho text\_to\_pandas; block thành đầu vào cho phép so sánh |

### 6\.2 Tải tệp làm ngữ cảnh — vòng đời

Người dùng kéo `du\-lieu\-thang\.xlsx` hoặc một PDF vào ô nhập:

1. `POST /api/knowledge/bronze` với `scope = session` → MinIO \+ SHA\-256 \+ `data\_id` *\(API đã có\)*;
2. Theo dõi `GET /api/knowledge/documents/\{id\}/stream` → hiện tiến trình parse ngay trong bong bóng chat *\(SSE đã có\)*;
3. Xem trước bảng tách được \(`GET /\{doc\_id\}/tables` đã có\); với Excel nhiều sheet, **cho chọn sheet/bảng** — khắc phục hạn chế “chỉ bảng lớn nhất mỗi file” của `kb\_structured\_data`;
4. Gắn nhãn **“chưa duyệt · dữ liệu phiên”** ở mọi nơi số từ tệp này xuất hiện;
5. Ba lối ra: \(a\) chỉ dùng trong phiên rồi bỏ; \(b\) **“Đưa vào kho tri thức”** → `promote/\{id\}/silver` kèm form metadata văn bản; \(c\) **“Gửi làm nguồn chỉ số”** → hàng đợi steward \([39](diagrams/39-uf-steward.puml)\)\.

**Ranh giới cứng**: dữ liệu phiên **không** vào observation store, **không** dùng cho golden set, **không** vào báo cáo đã duyệt\. Đây là điều kiện để cho phép tải tệp tự do mà không làm hỏng tính kiểm chứng được của hệ thống\.

### 6\.3 Quản nguồn ngay trong Ask

Không bắt người dùng rời sang màn Tri thức cho những việc nhỏ:

- **Kết nối warehouse mới**: mở `WarehouseWizard` hiện có trong một drawer\. *\(Lưu ý: wizard hiện chỉ có Snowflake/Databricks trong khi backend chạy Postgres/ClickHouse/Snowflake — lệch này phải sửa, xem [README](README.md) mục 3\.1\.\)*
- **Làm mới schema**: nút “crawl lại” cho một warehouse; hiện `last\_crawled\_at` từ schema\-catalog\.
- **Sửa mô tả cột**: `PUT /api/schema\-memory` *\(đã có\)* — chuyên viên gặp cột khó hiểu thì sửa tại chỗ, lần sau agent hiểu đúng hơn\. Một vòng lặp cải thiện rẻ và hiệu quả\.
- **Xem nguồn nào đang lỗi/cũ**: badge độ tươi có sẵn; thêm lối tắt tới Data Studio khi cần xử lý sâu\.

### 6\.4 Minh bạch: “agent đã nhìn thấy gì”

Một panel mở được sau mỗi câu trả lời, liệt kê **chính xác**: bảng nào và bao nhiêu dòng đã đọc, đoạn tài liệu nào \(tới trang\), chỉ số và vintage nào, tệp phiên nào — tách rõ **đã duyệt** và **dữ liệu phiên**\. Khác với EvidencePanel hiện tại \(nói *đã dùng gì*\), panel này nói *đã được phép nhìn gì* — hai câu hỏi khác nhau, và câu thứ hai mới là thứ giải thích được vì sao agent bỏ sót\.

### 6\.5 Ngân sách và an toàn

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910465117&cot=14)*

| Vấn đề | Xử lý |
| --- | --- |
| Gắn quá nhiều nguồn → chậm, loãng | Hiện ngân sách; cảnh báo mềm ở ngưỡng; đề xuất bỏ bớt nguồn không được dùng ở lượt trước |
| Trộn dữ liệu phân loại khác nhau | Block mang mức phân loại cao nhất trong các nguồn của nó; hạn chế xuất/chia sẻ theo mức đó |
| Tệp người dùng chứa dữ liệu mật | Cảnh báo trước khi tải lên; ghi audit; tệp phiên hết hạn tự động \(đề xuất 7 ngày\) |
| Rò dữ liệu qua chia sẻ workspace | Người nhận chỉ thấy block mà quyền của họ cho phép; ô không đủ quyền hiện “bị che”, không lặng lẽ biến mất |

## 7\. SQL và code

### 7\.1 Trình soạn SQL

- **Autocomplete từ schema\-catalog** \(bảng, cột, kiểu\) và **gợi ý join từ `join\_hints`** *\(cả hai đã có trong dữ liệu, chưa dùng\)*\.
- **Chạy qua đúng đường guard đã có**: sandbox\-executor có `POST /execute/sql` \(xếp hàng theo `kind`, trả `job\_id`\) với read\-only guard, row cap, audit, `aggregation\_check`\. **Nhưng nó chỉ gọi được từ workflow\-runtime**, trình duyệt không có đường tới: `routers/sandbox\_ops\.py` chỉ proxy `status`, `job/\{kind\}/\{job\_id\}`, `stream/\{kind\}/\{job\_id\}` — không có endpoint chạy\. Cần thêm `POST /api/sandbox/execute/\{kind\}` ở gateway, dùng lại y nguyên guard, **không** mở đường tắt nào khác\.
- **Diff với SQL của agent**: hiện cạnh nhau câu agent sinh và câu người dùng sửa, để người dùng thấy mình đổi gì và để reviewer đọc được\.
- **Cảnh báo trước khi chạy** khi truy vấn không có `WHERE` trên bảng lớn hoặc join nhiều bảng: ước lượng từ `row\_count` trong catalog\.
- **Theo dõi job**: `GET /api/sandbox/stream/sql/\{job\_id\}` để hiện tiến trình và cho phép huỷ — thay vì treo giao diện\.

### 7\.2 Ô mã Python/pandas

- Dùng `execute\_python` / `text\_to\_pandas` đã có, chạy trong sandbox container\.
- Đầu vào là các block đã có \(`df = blocks\["blk\_7f3a"\]`\), không phải đường dẫn tệp tuỳ ý\.
- Kết quả \(`DataFrame`, biểu đồ\) trở thành block mới, kế thừa chứng cứ của block nguồn\.
- **Giới hạn gói**: `statsmodels`/`scipy` hiện chưa có trong sandbox \([README](README.md) mục 1\) — cần bổ sung nếu muốn phân tích chuỗi thời gian, và phải khai báo rõ gói nào có để người dùng không đoán\.

### 7\.3 Ranh giới

`bash`, `file\_write`, `file\_edit`, `glob`, `grep` **không xuất hiện** trong Ask\. [PLATFORM\\\-ARCHITECTURE](PLATFORM-ARCHITECTURE.md) mục 4\.3 đã quyết định tách nhóm tool “coding” khỏi agent phân tích; giao diện phải phản ánh quyết định đó, không phơi bày thứ không nên dùng\.

## 8\. Hội thoại nhiều lượt trên block

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910465127&cot=14)*

| Tình huống | Thiết kế |
| --- | --- |
| “Làm lại cái vừa rồi theo quý” | Hội thoại tự gắn target\_block\_id là block gần nhất; hiện rõ đang nhắm block nào |
| Sửa một block cũ sau 10 lượt | Bấm Hỏi về block này trên BlockCard → chip mục tiêu xuất hiện ở composer |
| Muốn giữ cả hai | Nút Nhân bản rồi sửa — mặc định là sửa tại chỗ \(có lịch sử\), nhân bản là lựa chọn tường minh |
| So hai phiên bản | Chọn hai mục trong versions → hiện khác biệt spec và khác biệt số |
| Lỡ tay | Ctrl\+Z hoàn tác trên canvas; mỗi phiên bản block hoàn nguyên được |

## 9\. Sản phẩm đầu ra từ workspace

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910465139&cot=14)*

| Đầu ra | Cơ chế | Trạng thái |
| --- | --- | --- |
| Xuất Excel có sheet “Nguồn” | /api/ask/results/\{id\}/export\.xlsx | Đã có |
| Ghim lên dashboard cá nhân | Lưu spec, không lưu kết quả → mở lại luôn lấy số mới, kèm cờ nếu đã điều chỉnh | Chưa có |
| Đưa vào báo cáo/bản tin | Block thành khối báo cáo, giữ nguyên trích dẫn | Chưa có |
| Lưu thành saved view | Lưu tham số \+ ngữ cảnh \+ bố cục, chia sẻ được | Chưa có |
| Đặt cảnh báo từ block | “Báo tôi khi giá trị này vượt X” → alert rule | Chưa có |
| Đăng ký metric contract | Từ L3/L4, qua maker–checker | Chưa có \(semantic layer đã sẵn sàng\) |
| Chia sẻ workspace | Deep\-link mang toàn bộ trạng thái; bình luận trên từng block | Chưa có |
| Ảnh biểu đồ cho slide | PNG từ block | Chưa có |

## 10\. Bố cục, phím tắt, trạng thái

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910465142&cot=14)*

| Chiều rộng | Bố cục |
| --- | --- |
| ≥ 1600px | Rail 260 · hội thoại 520 · canvas phần còn lại |
| 1200–1600px | Rail gập thành icon; canvas và hội thoại chia đôi |
| &lt; 1200px | Tab: Ngữ cảnh / Hội thoại / Canvas |
| Chế độ trình bày | Chỉ canvas |

**Phím tắt** \(mọi thao tác kéo thả đều có đường bàn phím\): `Ctrl\+K` tìm kiếm toàn cục · `@` gắn ngữ cảnh · `Ctrl\+Enter` gửi · `Ctrl\+/` bảng phím tắt · `Esc` huỷ truy vấn đang chạy · `Ctrl\+Z` hoàn tác canvas · `\[`/`\]` gập rail/canvas\.

**Trạng thái rỗng có ích**: màn Ask trống hiện *nguồn nào đang sẵn sàng* \(số bảng, số tài liệu, số chỉ số đã duyệt\) và 3–5 câu hỏi mẫu **lấy từ golden set**, không phải ví dụ BI tiếng Anh như hiện nay\.

## 11\. Hợp đồng kỹ thuật

### 11\.1 Payload Ask mở rộng

```javascript
{
  "query": "So dư nợ trong file với số công bố 2024",
  "user_id": "...", "conversation_id": "...",
  "agent_id": null,                        // giữ nguyên
  "messages": [...],                       // giữ nguyên
  "clarification_answers": [...],          // giữ nguyên

  "context_refs": [                        // MỚI — tập nguồn tường minh
    { "kind": "table",      "id": "wh1.public.fact_debt", "columns": ["amount","period"] },
    { "kind": "document",   "id": "doc_123" },
    { "kind": "collection", "id": "legal_vn" },
    { "kind": "indicator",  "id": "ECO-D2-S22-001" },
    { "kind": "block",      "id": "blk_7f3a" },
    { "kind": "session_file", "id": "data_456", "table": "Sheet1!B3:H40" }
  ],
  "target_block_id": "blk_7f3a",           // MỚI — sửa block đã có
  "slots": { "period": "2024", "vintage": "latest_published" },  // MỚI — chip ngữ cảnh
  "preferences": { "language": "vi", "decimal_precision": "as_published" }  // MỚI
}
```

Tương thích ngược: mọi trường mới đều tuỳ chọn; thiếu thì hành vi y như hiện nay\.

### 11\.2 `QuerySpec` — một hợp đồng cho cả ba thế giới

Ask phải phục vụ đồng thời **warehouse SQL** \(BI doanh nghiệp\) và **observation/indicator** \(ANKT\)\. Một spec chung, phân biệt bằng `source`:

```javascript
{
  "spec_version": 1,
  "mode": "semantic | iql | sql_freeform | pandas",
  "source": { "kind": "warehouse", "warehouse_id": "wh1" },   // hoặc {kind:"indicator"} / {kind:"session_file"}
  "select": [ { "field": "amount", "agg": "sum", "as": "total" } ],
  "dimensions": [ "region", "period" ],
  "filters": [ { "field": "period", "op": "between", "value": ["2019","2024"] } ],
  "period": { "from": "2019", "to": "2024", "frequency": "A" },
  "vintage": { "policy": "latest_published" },                // chỉ có nghĩa với source.kind = indicator
  "transforms": [ { "op": "yoy", "target": "total" } ],
  "compare": { "REF_AREA": ["VN","TH"] },
  "order": [ { "field": "total", "dir": "desc" } ],
  "limit": 1000,
  "encoding": { "x": "period", "y": ["total"], "color": "region", "chart_type": "line" },
  "sql": null                                                  // chỉ khi mode = sql_freeform
}
```

`QuerySpec` là **mở rộng** của `AnalyticalSlots` hiện có \(`libs/semantic/slots\.py`\) và là bản đầy đủ hơn của IQL trong [PLATFORM\\\-ARCHITECTURE](PLATFORM-ARCHITECTURE.md) mục 2\.1 — không phải ngôn ngữ thứ ba\. Compiler ở backend chọn đường: semantic layer → IQL compiler → SQL có guard\.

### 11\.3 Frame SSE cần thêm

Bổ sung cho danh sách ở [UI\\\-DESIGN](UI-DESIGN.md) mục 10\.3:

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910465148&cot=14)*

| Frame | Nội dung | Phục vụ |
| --- | --- | --- |
| \_\_block | block đầy đủ \(id, kind, spec, result\_id, columns, rows, encoding\) | Canvas |
| \_\_block\_patch | cập nhật một block đã có \(phiên bản mới\) | Sửa block, không tạo bản sao |
| \_\_progress | \{step, tool, sub\_agent, elapsed\_ms, status\} | Tường thuật tiến trình |
| \_\_context\_used | nguồn thực sự đã đọc \+ số dòng/đoạn | Panel “agent đã nhìn thấy gì” |
| \_\_job | \{kind, job\_id\} để nối /api/sandbox/stream/\{kind\}/\{job\_id\} | Theo dõi và huỷ truy vấn |

### 11\.4 API cần thêm

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910465155&cot=14)*

| Endpoint | Mục đích | Ghi chú |
| --- | --- | --- |
| POST /api/ask/blocks/\{id\}/run | Chạy lại block với spec mới | Thay vì gửi cả câu hỏi qua orchestrator |
| GET/PUT /api/ask/workspaces/\{id\} | Lưu/khôi phục workspace \(block, bố cục, ngữ cảnh\) | Thay localStorage |
| POST /api/ask/results/\{id\}/reshape | \(tuỳ chọn\) gộp phía server khi tập quá lớn cho client | Giải pháp cho mục 5\.2 |
| POST /api/sandbox/execute/\{kind\} | Chạy SQL/Python ad\-hoc từ giao diện | Bắt buộc: sandbox có /execute/sql nhưng chưa proxy ra gateway \(mục 7\.1\) |
| POST /api/semantic/metrics | Đăng ký metric contract từ block | Đóng vòng lặp học |
| GET /api/knowledge/collections \(đã có\) · GET /api/schema\-catalog \(đã có\) | Rail ngữ cảnh | Chỉ cần dùng |

### 11\.5 Tái dùng trong mã hiện có

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910465162&cot=14)*

| Sẽ dùng lại | Ở đâu |
| --- | --- |
| useAgentStream\.ts, sse\.ts | Nền streaming; thêm case cho frame mới |
| DataGrid\.tsx | Lõi block table \(đã có sort, phân trang qua result\_id, xuất\) |
| ChartRenderer\.tsx | Lõi block chart \(8 loại\); thêm shelves ở ngoài, không sửa lõi |
| EvidencePanel\.tsx, EvidenceStrip\.tsx | Tab chứng cứ của block |
| ClarificationCard\.tsx | Giữ nguyên, thêm lựa chọn “trả lời với giả định X” |
| AgentPlayground\.tsx | Nguồn tham khảo cho hiển thị trace/span theo node và tiến trình |
| WarehouseWizard\.tsx, IngestionWizard\.tsx | Drawer quản nguồn trong Ask |
| shared/DataTable, FilterPopover, DetailDrawer | Rail và các drawer |

## 12\. Lộ trình

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910465167&cot=14)*

| Đợt | Nội dung | Phụ thuộc backend | Ước lượng | Nghiệm thu |
| --- | --- | --- | --- | --- |
| A · Block hoá | Mô hình block \+ canvas tối thiểu \(ghim, bố cục, lịch sử phiên bản\) · frame \_\_block · workspace lưu server | \_\_block, /api/ask/workspaces | ~3 tuần | Câu trả lời ghim lại được, mở lại sau 1 ngày vẫn còn |
| B · Ngữ cảnh | Rail nguồn \(schema\-catalog \+ collections\) · @\-mention · context\_refs · tải tệp phiên \+ tiến trình · panel “agent đã nhìn thấy gì” | context\_refs trong payload; \_\_context\_used | ~3 tuần | Hỏi được trong phạm vi 2 bảng \+ 1 tệp vừa tải, và kiểm chứng được agent chỉ đọc đúng chừng đó |
| C · BI | Shelves kéo thả · pivot · top\-N · cột tính · lọc chéo · L3 form spec · QuerySpec | POST /api/ask/blocks/\{id\}/run | ~4 tuần | Đổi mã hoá &lt; 150 ms; thêm chiều chạy lại đúng; pivot không bao giờ gộp trên tập cắt |
| D · SQL &amp; code | Trình soạn SQL \+ autocomplete \+ diff \+ theo dõi job · ô pandas · đăng ký metric | \_\_job, POST /api/semantic/metrics | ~3 tuần | Sửa SQL chạy qua guard; đăng ký metric xong câu hỏi cũ trả cùng số |
| E · Đầu ra &amp; cộng tác | Ghim dashboard · đưa vào báo cáo · saved view · cảnh báo từ block · chia sẻ \+ bình luận | report/alert svc | ~3 tuần | Một phân tích đi thẳng từ Ask vào bản tin có trích dẫn |

Tổng ~16 tuần frontend, chồng lấn được với GĐ1–GĐ2 của [lộ trình chung](README.md)\.

**Làm được ngay, không chờ backend:** rail nguồn từ `/api/schema\-catalog` \(chỉ đọc\); trạng thái rỗng lấy câu hỏi từ golden set; tường thuật tiến trình thô từ các frame đã có; nút “xem SQL” mở `EvidenceRun\.sql` trong editor chỉ đọc; chuyển lịch sử/câu hỏi đã lưu sang `/api/conversations`\.

## 13\. Rủi ro và quyết định cần chốt

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910465171&cot=14)*

| \# | Rủi ro | Giảm thiểu |
| --- | --- | --- |
| R1 | Biến Ask thành một BI tool nửa vời — cạnh tranh với Metabase/Superset mà thua | Không làm BI đa năng\. Chỉ làm những thao tác phục vụ giải thích một con số có nguồn\. Mọi block giữ chứng cứ; đó là thứ BI tool không có |
| R2 | Gộp sai trên tập bị cắt | Quy tắc cứng ở mục 5\.2, có test |
| R3 | Người dùng gắn quá nhiều ngữ cảnh → chậm và loãng | Ngân sách hiển thị; đề xuất bỏ nguồn không dùng ở lượt trước |
| R4 | Dữ liệu phiên lẫn vào số liệu chính thức | Nhãn ở mọi bề mặt; chặn cứng ở observation store, golden set, báo cáo đã duyệt |
| R5 | Canvas phình thành nơi chứa rác | Block không ghim tự hết hạn cuối phiên; ghim là hành động tường minh |
| R6 | L4 mở quá rộng | Chỉ vai trò có quyền mới thấy editor; bash/file\_\* không bao giờ xuất hiện |

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910465175&cot=14)*

| \# | Quyết định | Phương án | Đề xuất |
| --- | --- | --- | --- |
| AW\-1 | Bố cục mặc định | Chat toàn màn · ba bề mặt | Ba bề mặt, canvas gập lại được cho người chỉ muốn hỏi |
| AW\-2 | Block mặc định khi hỏi lại | Sửa tại chỗ · tạo block mới | Sửa tại chỗ kèm lịch sử phiên bản; nhân bản là lựa chọn tường minh |
| AW\-3 | Ai thấy trình soạn SQL | Mọi người · theo vai trò | Theo vai trò \(ANALYST, AI\_ENGINEER, PLATFORM\_ADMIN\) |
| AW\-4 | Tệp tải lên trong Ask | Không cho · phiên · vào KB luôn | Phiên, có nút promote tường minh |
| AW\-5 | Gộp dữ liệu lớn | Luôn client · luôn server · lai | Lai: client tới 50k dòng, trên đó chuyển server/re\-query |
| AW\-6 | QuerySpec và IQL | Hai schema · một schema | Một schema, source\.kind phân nhánh warehouse/indicator |
| AW\-7 | Hạn dữ liệu phiên | Không xoá · 7 ngày · hết phiên | 7 ngày, có audit và nút xoá ngay |
