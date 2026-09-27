# 09. Frontend cho V2: hiện trạng, nghiên cứu, thiết kế, kế hoạch

> Đối tượng: 2 frontend (FE1 hiện có, FE2 vào từ W2 theo D14), trưởng nhóm, senior làm hợp đồng dữ
> liệu. Khảo sát mã ngày 27/09/2026 trên nhánh `feat/remove-redundant-service`. Nguyên tắc UI
> ([13-ui-principles.md](13-ui-principles.md))
> là nguồn cho nguyên tắc P1–P8; file này chọn phần nào làm trong POC 1 tháng, thêm
> những gì V2 đòi hỏi mà v1 chưa có, và đối chiếu với nghiên cứu hiện nay.

## 0. Kết luận

1. **Nền tốt, không viết lại.** React 19 + Vite 6 + Tailwind 4, TypeScript, SSE tự viết, Recharts,
   xyflow, bảng dữ liệu và drawer dùng chung. POC thêm màn và primitive, không đổi khung.
2. **Ba chỗ yếu phải sửa trước khi dựng màn V2**: kiểu dữ liệu API viết tay (sẽ lệch với
   `indicator-api` mới), không có lớp cache server-state (Review Inbox cần 409 và làm mới đúng lúc),
   URL chỉ giữ module/tab (không chia sẻ được "Brent as-of 21/9"). Làm ở FE-00, tuần 1–2.
3. **Giao thức stream theo mô hình sự kiện AG-UI, không kéo thư viện của nó.** Giữ SSE hiện có, đặt
   tên và ngữ nghĩa frame theo AG-UI (run, step, text, tool, state delta, interrupt) để sau này nối
   CopilotKit/Bedrock AgentCore hay client ngoài không phải làm lại.
4. **Render theo catalog, như A2UI.** Agent chỉ được chọn khối hiển thị từ một catalog component
   đã duyệt và truyền dữ liệu có kiểu; không sinh HTML/JS; số không bao giờ lấy từ văn bản LLM.
5. **Trích dẫn không tự động tạo niềm tin đúng.** Nghiên cứu AAAI 2025 cho thấy trích dẫn, kể cả
   trích dẫn ngẫu nhiên, làm tăng niềm tin. Vì vậy mỗi số phải bấm được tới đúng trang/ô, và giao diện
   hiện kết quả verifier từng kiểm tra thay vì chỉ hiện "[1]".
6. **Màn duyệt số phải tách "đánh giá" khỏi "sửa".** Thí nghiệm 2.784 người trích số từ bảng báo cáo
   ("Bias in the Loop", 2026) cho thấy bắt người duyệt nhập số sửa ngay khi bác bỏ làm họ bác bỏ ít
   hơn và để lọt lỗi nhiều hơn.
7. **Khối lượng:** ~8 tuần-người frontend; FE1 + FE2 có ~7,3. Vừa đủ khi một senior dựng script sinh
   kiểu từ OpenAPI và áp dụng ba mục cắt nhỏ ở mục 7.

## 1. Hiện trạng đo được

| Hạng mục | Hiện có | Đánh giá cho V2 |
| --- | --- | --- |
| Khung | React 19, Vite 6, Tailwind 4, TS ~5.8, `tsconfig.strict.json` | Giữ |
| Quy mô | 143 file TS/TSX, ~35.400 dòng; file lớn nhất: `Builder.tsx` 2.072, `KnowledgeHub.tsx` 1.900, `AssetDetailWorkspace.tsx` 1.742 dòng | Màn V2 mới đặt ở `features/*`, mỗi file dưới ~400 dòng; không thêm vào các file khổng lồ |
| Điều hướng | `routes.tsx` (`ROUTE_MAP` module → tab), `AppStateContext.tsx` ghi path `/module/tab` bằng `history.replaceState` | Thiếu tham số màn (series, as_of, case, alert). Thêm `urlState` (FE-00) |
| Gọi API | `lib/apiClient.ts`, `features/ask/api.ts`; kiểu viết tay trong `features/ask/types.ts`, `types.ts` | Sinh kiểu từ OpenAPI của `indicator-api` (FE-00) |
| Server state | `useState` + fetch trong từng màn; không có thư viện cache | Thêm TanStack Query cho màn V2 (FE-00) |
| Stream | `features/ask/sse.ts` + `hooks/useAgentStream.ts`: frame `__session_id`, `__reset`, `__clarification`, `__pending_review`, `__chart`, `__citations`, `__suggestions`, `__options`, `__conv_ids`, `__evidence` | Giữ cơ chế, mở rộng frame theo mục 4.2 |
| Trả lời | `AnswerBlock`, `ChartRenderer` (Recharts, 8 loại), `DataGrid`, `EvidencePanel`, `EvidenceStrip`, `CitationPanel`, `ClarificationCard` | Dùng lại; thêm primitive số (mục 4.4) |
| Đồ thị quan hệ | `@xyflow/react`, `react-force-graph-2d` (Knowledge Hub) | Dùng xyflow cho task graph và lineage |
| Xem tài liệu | `AssetDetailWorkspace.tsx:761` dùng `<iframe>` trên blob URL | Đủ cho `#page=N` trong POC; khung bbox (pdf.js) là Tier B |
| Nguồn dữ liệu | `SourceRegistry.tsx` (843 dòng), `SourceConfigFields.tsx`, `SchedulePicker.tsx`, `IngestionWizard.tsx` | Mở rộng cho licence, entity, profile, drift |
| Duyệt | `StructuredTableReview.tsx`, `ConflictWorkspace.tsx`, `ReviewCenter.tsx` | Nền cho Review Inbox |
| Định dạng số | `lib/formatVN.ts` theo chuẩn **Mỹ** (E3) | Sửa ở P0-02 trước mọi màn số |
| i18n | Không có; nhãn trộn Việt/Anh | Màn V2 viết nhãn tiếng Việt; lớp i18n để sau POC |
| Trạng thái người dùng | `localStorage` ở `AppStateContext`, `AskDataPage`, `usePromotionQueue`, `DetailDrawer` | Không lưu gì chia sẻ được vào `localStorage` |
| Kiểm thử | vitest + Testing Library, 12 file test | Thêm test cho reducer stream, formatVN, primitive; e2e trình duyệt là Tier B |
| Chất lượng | `tsc`, prettier, knip; không có ESLint (CLAUDE.md) | Giữ |

## 2. V2 đòi hỏi gì ở frontend

Từ 00 (lớp Experience: Ask Data, Analyst Workspace, Data Studio, Review Inbox, Alert, Report) và hai
kịch bản nghiệm thu, frontend phải làm được năm việc mà giao diện hiện tại chưa làm:

| # | Năng lực | Vì sao | Kịch bản |
| --- | --- | --- | --- |
| F1 | Hiện **một con số cùng toàn bộ ngữ cảnh**: giá trị, đơn vị, kỳ, bốn trục thời gian, nguồn, release, trạng thái duyệt, nhãn licence | Bất biến 8; P1–P3 của UI v1 | 27 bước: 15, 19, 27; Việt Nam: 7–8 |
| F2 | **Duyệt có trách nhiệm**: ReviewCase theo thẩm quyền, so sánh với nguồn, correction/override, checker, OCC 409, không sửa raw | 00 mục 5; UC07 | 10–15; Việt Nam: 5 |
| F3 | **Xem agent đang làm gì và dựa vào đâu**: task graph, bước đang chạy, capability đã gọi, lineage từ nguồn tới câu trả lời | UC10; WaitGPT (mục 3.2) | 18–22, 26–27 |
| F4 | **Nhận việc không do mình hỏi**: hộp thư ReviewCase, alert, mở tiếp câu hỏi từ alert | 00 mục 10 | 11, 23–25; Việt Nam: 6 |
| F5 | **Chia sẻ đúng trạng thái**: link mở đúng series, as-of, case, alert | [13](13-ui-principles.md) mục 4 | Toàn bộ |

## 3. Nghiên cứu hiện nay (9/2026) và cách áp dụng

### 3.1 Giao thức và generative UI

| Nguồn | Nội dung | Áp dụng vào POC |
| --- | --- | --- |
| **AG-UI** (CopilotKit, mở từ 5/2025) | Giao thức sự kiện agent ↔ giao diện qua SSE/WebSocket: lifecycle (`RunStarted/Finished/Error`, `StepStarted/Finished`), text, tool call, `StateSnapshot/StateDelta` (JSON Patch RFC 6902), activity, custom; interrupt/HITL qua `RunFinished.outcome = interrupt` và resume. Có tích hợp chính thức trong Microsoft Agent Framework, Google ADK, AWS Strands, Bedrock AgentCore (từ 3/2026) | Đặt frame SSE theo mô hình này (mục 4.2). Không thêm SDK CopilotKit vào POC: runtime là LangGraph tự dựng và stream đã chạy; đổi thư viện giữa POC là rủi ro không cần |
| **A2UI** (Google, 12/2025, v0.8 preview, v1.0 dự kiến Q4/2026) | UI là dữ liệu khai báo (JSONL), agent chỉ tham chiếu component trong catalog do client kiểm soát, không chạy mã từ model | Nguyên tắc "catalog đã duyệt" chính là hợp đồng render theo intent của UI v1 mục 4. Khối trả lời V2 dùng JSON `{block, props, data_ref}` gần với A2UI để sau này chuyển được; chưa dùng renderer A2UI vì đang preview |
| **MCP Apps** (SEP-1865, phát hành 26/01/2026; OpenAI Apps SDK xây trên nó) | MCP server trả UI tương tác qua tài nguyên `ui://`, host nhúng trong hội thoại | Ngoài POC. Khi làm MCP Gateway (Tier B), capability có thể kèm `ui://` để client ngoài (ChatGPT, Claude) hiện thẻ số có nguồn |
| **Generative UI** (Google, arXiv 2604.09577) | LLM có công cụ tạo được UI tuỳ biến; người dùng thích hơn markdown rõ rệt; so với UI do chuyên gia làm thì chỉ tương đương ở ~50% trường hợp | Không cho LLM tự sinh giao diện cho số liệu ANKT: sai một chữ số trên màn đẹp nguy hiểm hơn markdown xấu. Chỉ chọn khối từ catalog |
| **Software as Content** (arXiv 2603.21334) | Ứng dụng động, có trạng thái bền, thay cho trao đổi văn bản; chat vẫn tốt hơn ở một số tác vụ | Ủng hộ Ask Workspace của v1: kết quả là block có danh tính, ghim được, sống qua nhiều lượt; hội thoại vẫn là lối vào |

### 3.2 Giao diện phân tích dữ liệu có agent

| Nguồn | Phát hiện | Áp dụng |
| --- | --- | --- |
| **WaitGPT** (UIST 2024, 12 người) | Vẽ các thao tác dữ liệu của agent thành sơ đồ nút–cạnh ngay khi chạy, mở từng nút xem tham số và kết quả; người dùng phát hiện lỗi tốt hơn và tự tin hơn | **TaskGraphView**: vẽ task graph của runtime (capability, tham số, kết quả, thời gian) trong lúc stream; mỗi nút mở được input/output. Là màn chính cho bước 18–22 |
| **Data Formulator 2** (CHI 2025) | Trộn thao tác đồ hoạ với ngôn ngữ tự nhiên; lịch sử theo "data threads" để quay lại nhánh cũ và làm tiếp | Chip ngữ cảnh sửa được (series, as-of, ngưỡng) chạy lại capability với tham số mới; lịch sử block theo nhánh là Tier B |
| **Agentic Visualization** (IEEE CG&A 2025) | Mẫu thiết kế theo vai trò agent, giao tiếp, phối hợp; giữ quyền chủ động của người | Mỗi hành động tự động (alert, investigation) hiện "ai/cái gì đã làm, dựa vào luật nào", người có thể mở, dừng, bác bỏ |

### 3.3 Niềm tin, trích dẫn, duyệt

| Nguồn | Phát hiện | Áp dụng |
| --- | --- | --- |
| **Citations and Trust in LLM Generated Responses** (AAAI 2025) | Có trích dẫn thì tin hơn, **kể cả trích dẫn ngẫu nhiên**; người kiểm tra trích dẫn thì tin ít đi | Trích dẫn phải mở được tới trang/ô trong một cú bấm; hiện kết quả verifier từng kiểm (số có trong bằng chứng, dung sai, vintage, trích dẫn tồn tại); số không kiểm được thì ẩn (P8), không chỉ tô màu |
| **Seeing the Reasoning** (CHI 2026 EA) | Lời giải thích và cách diễn đạt chắc chắn làm thay đổi niềm tin và quyết định, kể cả khi nội dung giải thích như nhau | Mức chắc chắn trên giao diện lấy từ verifier và độ tươi dữ liệu, không lấy từ giọng văn của LLM; khi thiếu bằng chứng dùng RefusalCard thay vì câu trả lời dè dặt |
| **Bias in the Loop** (HDSR 8.2, 2026; 2.784 người, trích số phát thải từ bảng báo cáo doanh nghiệp) | Bắt nhập giá trị sửa khi bác bỏ làm giảm việc bác bỏ và tăng lỗi lọt; người thiện cảm với AI tin quá mức; thưởng theo hiệu suất không cải thiện độ chính xác. Khuyến nghị tách phán đoán khỏi sửa | Review Inbox hai bước: (1) với mỗi số, chọn Đúng / Sai / Không chắc, không phải nhập gì; (2) các số "Sai" chuyển sang bước sửa riêng. Chèn ngẫu nhiên case đã biết đáp án để đo độ chính xác người duyệt. Bất đồng giữa maker và checker hiện là tín hiệu, không bị gộp |
| Tổng quan automation bias (AI & Society 2025) | Mức thiên lệch phụ thuộc tác vụ, giao diện, trách nhiệm | Không có nút "Duyệt tất cả" cho số trích từ văn bản; ảnh nguồn đặt cạnh số; case quá SLA không tự duyệt |

### 3.4 Sản phẩm production

| Sản phẩm | Mẫu giao diện | Áp dụng |
| --- | --- | --- |
| Databricks AI/BI Genie | "Trusted assets": câu trả lời đi qua hàm/truy vấn mẫu đã duyệt thì được gắn nhãn | Nhãn **"Capability đã duyệt"** khi câu trả lời đi qua capability tất định (00 mục 9); câu trả lời đi đường tự do không có nhãn |
| Snowflake Cortex Analyst | Verified Query Repository: câu hỏi mẫu + SQL đã duyệt | Golden set và composite capability đóng vai trò tương tự; hiện QuerySpec đã dùng khi người dùng mở chi tiết |

## 4. Kiến trúc đề xuất

### 4.1 Nền (FE-00, W1–W2)

| Việc | Chọn | Lý do |
| --- | --- | --- |
| Kiểu dữ liệu | `openapi-typescript` sinh `src/lib/api/indicator.d.ts` từ OpenAPI của `indicator-api`; `npm run gen:api` trong CI so với file đã commit | P0-09 chốt OpenAPI trong 3 ngày đầu; kiểu viết tay sẽ lệch |
| Gọi API | Wrapper mỏng trên `lib/apiClient.ts` hiện có (giữ auth Keycloak, header tenant) | Không đổi cơ chế xác thực |
| Server state | TanStack Query v5 cho màn V2 (Series, Review Inbox, Alert, Source) | Cache, làm mới khi có event, xử lý 409 bằng `invalidateQueries` rồi hiện diff mới |
| Trạng thái URL | `lib/urlState.ts`: `?series=&as_of=&as_of_mode=world|system&case=&alert=&run=` | F5; link trong notification mở đúng chỗ |
| Stream | Reducer thuần `features/ask/streamReducer.ts` nhận frame (mục 4.2), test riêng | Dễ test, dùng lại cho TaskGraphView và hộp thư |
| Hộp thư | Một kết nối SSE `/api/notifications/stream` cho ReviewCase, alert | F4 |

Thư viện mới: `@tanstack/react-query`, `openapi-typescript` (dev). Mỗi thư viện qua knip và có lý do ở
trên; không thêm thư viện UI kit mới.

### 4.2 Frame stream V2 theo mô hình AG-UI

| Frame V2 | Tương ứng AG-UI | Nội dung |
| --- | --- | --- |
| `__run` `{status: started|finished|error, run_id}` | `RunStarted/Finished/Error` | Vòng đời lượt hỏi |
| `__task_graph` `{nodes, edges}` | `StateSnapshot` | Task graph planner lập |
| `__step` `{node_id, status, capability, started_at, ms, output_ref}` | `StepStarted/Finished` | Cập nhật từng nút TaskGraphView |
| văn bản thường (giữ nguyên) | `TextMessageContent` | Lời diễn giải |
| `__block` `{block, props, data}` | `Custom` (khối catalog, kiểu A2UI) | ValueCard, SeriesChart, DataGrid, LineagePanel |
| `__verification` `{checks: [...], passed}` | `Custom` | VerifierBadge |
| `__lineage` `{nodes, edges}` | `Custom` | Nguồn → artifact → release → observation → operator → câu trả lời |
| `__refusal` `{code, message, actions}` | `RunFinished` (kết quả) | RefusalCard |
| `__clarification`, `__pending_review` (giữ) | `RunFinished.outcome = interrupt` | Hỏi lại, chờ duyệt |
| `__evidence`, `__citations` (mở rộng `page`, `bbox`, `release_id`, `available_at`, `licence`) | `Custom` | Trích dẫn |

Frame cũ giữ nguyên để không vỡ màn hiện có; runtime phát thêm frame mới (S9-02, S10-01).

### 4.3 Hợp đồng render

- Runtime chỉ gửi tên khối trong catalog (mục 4.4) và dữ liệu có kiểu; frontend từ chối khối lạ.
- Mọi số hiển thị đi qua `formatVN` từ giá trị có kiểu (`value`, `unit`, `decimal_precision`); không
  trích số từ văn bản LLM để hiển thị.
- Văn bản LLM chỉ là lời diễn giải đi kèm khối; verifier chặn số trong văn bản không có trong bằng
  chứng (P8).

### 4.4 Catalog khối cho POC

| Khối | Props chính | Dùng ở | Nguồn thiết kế |
| --- | --- | --- | --- |
| `ValueCard` | value, unit, precision, period, event_time, publication_time, available_at, source_ref, release_id, review_status, licence | Ask, Việt Nam, Alert | IndicatorValueCard (UI v1) |
| `SeriesChart` | series[], as_of, markers (release, revision, event), regimes, downsample | Series Explorer, Ask, Việt Nam | ThresholdTimeSeries (UI v1) |
| `SourceRefChip` + `DocumentPageViewer` | doc_id, page, bbox, issuer, issued_date | mọi nơi có số | UI v1 |
| `AsOfControl` | as_of, mode (`world` / `system`) | Series Explorer, Ask | VintageControl (UI v1), UC13 |
| `TimeAxesTooltip` | 4 trục thời gian | ValueCard, bảng observation | mới (bất biến 8) |
| `VerifierBadge` | checks[] | Ask, Alert | UI v1 |
| `RefusalCard` | code, actions | Ask | UI v1 |
| `LicenceBadge` | licence, use_rights | mọi số từ nguồn `pending_review` hoặc LBMA | mới (ADR-V2-24/25) |
| `TaskGraphView` | nodes, edges, steps | Ask, Alert investigation | mới (WaitGPT) |
| `LineagePanel` | nodes, edges | Ask, Series Explorer | mới |
| `ReviewCaseCard`, `ReviewDecision`, `CorrectionForm` | case, subject, evidence, version | Review Inbox | mới (Bias in the Loop) |
| `AlertCard` | rule, entity, window, grouped[], investigation_run | Alert inbox | UI v1 |
| `ConflictClassBadge` | class | Review Inbox, Series Explorer | mới (UC06) |

Hiệu năng: chuỗi Brent ngày từ 1987 có khoảng 9.100 điểm; SeriesChart giảm mẫu theo LTTB khi hiển thị
toàn kỳ (giữ nguyên điểm khi phóng to), bảng observation phân trang phía server.

## 5. Đặc tả màn hình POC

| Màn | Ai dùng | Nội dung | Trạng thái cần có | Bước kịch bản | ID |
| --- | --- | --- | --- | --- | --- |
| **Nguồn + Series Explorer** | Data steward | Danh sách nguồn với licence, SLA, release gần nhất; profile, fingerprint, drift (diff cột); mapping đề xuất; chuỗi với AsOfControl, marker release/điều chỉnh, bảng observation có TimeAxesTooltip và SourceRefChip | Drift chặn (đỏ, có nút mở ReviewCase), nguồn quá SLA, nguồn `blocked` | 1–6, 16 | S3-05 |
| **Entity & Series** | Research | Entity, alias, quan hệ `SAME_UNDERLYING`, `SUCCEEDS`, `NOT_EQUIVALENT`; series, đơn vị, phía giá | Quan hệ tạo vòng bị chặn | 12–13; Việt Nam 4 | S1-03 |
| **Review Inbox** | Reviewer, checker | Hàng đợi theo thẩm quyền; mỗi case: số máy trích, ảnh/đoạn nguồn cạnh bên, DQ và lớp conflict; **bước 1** Đúng / Sai / Không chắc cho từng số; **bước 2** sửa các số "Sai" hoặc sửa mapping, lý do bắt buộc; checker thấy diff maker và bất đồng | 409: hiện "đã có người duyệt trước", tải diff mới, không ghi đè; maker không thấy nút duyệt của chính mình; case quá SLA | 10–15; Việt Nam 5 | S6-03 |
| **Alert inbox** | Chuyên viên | Alert đã gom nhóm (Brent, WTI, spread cùng sự kiện), luật đã kích hoạt, TaskGraphView của investigation, nút "Hỏi tiếp" mở Ask với ngữ cảnh | Alert bị cooldown hiện số lần bị gộp | 23–25; Việt Nam 6 | S11-02 |
| **Ask: khối trả lời** | Mọi người | Lời diễn giải + khối catalog; TaskGraphView khi chạy; VerifierBadge; LineagePanel; nhãn "Capability đã duyệt" | Refusal theo mã, `INSUFFICIENT_EVIDENCE`, nguồn quá SLA | 25–27; Việt Nam 7–8 | E-01, FE-01 |
| **Việt Nam** | Lãnh đạo, chuyên viên | Giá xăng kỳ này (thế giới bình quân, giá bán, thay đổi, quyết định, hiệu lực), lịch sử kỳ điều hành (bậc thang, đánh dấu E10), vàng SJC mua/bán, LBMA PM, chênh theo hai phía tỷ giá, tỷ giá Vietcombank | Nhãn nguồn LBMA; chưa có kỳ mới | Việt Nam 6–8 | VN-05 |
| **Xem nguồn** | Mọi người | Mở tài liệu tại `#page=N` | Tài liệu không có trang | 20, 27 | S4-01 |

Nguyên tắc chung: mọi màn có trạng thái đang tải, rỗng, lỗi có mã; mọi số có nguồn; không màn nào
lưu trạng thái chia sẻ trong `localStorage`.

## 6. Kiểm thử

| Mức | Công cụ | Nội dung |
| --- | --- | --- |
| Đơn vị | vitest | `formatVN` ([13](13-ui-principles.md) mục 2), `streamReducer` (thứ tự frame, frame lạ, interrupt), `urlState` |
| Component | Testing Library | ValueCard (đủ 4 trục thời gian, nhãn licence), Review Inbox (409, maker ≠ checker, bước 1 không bắt nhập số), RefusalCard |
| Hợp đồng | fixture JSON từ OpenAPI | Màn V2 render đúng với response mẫu của `indicator-api` |
| Kịch bản | harness backend (`scenario_27.py`, `scenario_vn.py`) | Frontend không có e2e trình duyệt trong POC; Playwright cho 27 bước là Tier B |

## 7. Kế hoạch 2 frontend

FE1 (hiện có, nắm mã) làm phần chạm màn cũ; FE2 (vào 05/10) làm màn mới trong `features/*`.

| ID | Việc | Ai | TN | Tuần |
| --- | --- | --- | --- | --- |
| P0-02 | E3 `formatVN.ts` | FE1 | 0,5 | W1 |
| FE-00 | Nền: sinh kiểu OpenAPI (SR1 dựng script 0,25), TanStack Query, `urlState`, `streamReducer` + frame V2, kết nối hộp thư | FE1 (+SR1) | 0,75 | W1–W2 |
| S2-01 | Form nguồn: licence, entity, locale, follow_link | FE1 | 0,5 | W2 |
| S1-03 | Entity & Series | FE2 | 0,5 | W2 |
| S3-05 | Nguồn + Series Explorer (gộp) | FE2 | 1 | W2–W3 |
| S6-03 | Review Inbox hai bước, 409, thẩm quyền | FE1 | 1,75 | W3 |
| S4-01 | Link `#page=N` từ SourceRefChip | FE1 | 0,25 | W3 |
| FE-01 | TaskGraphView + LineagePanel (xyflow) | FE2 | 0,75 | W3–W4 |
| E-01 | Khối trả lời: ValueCard, VerifierBadge, RefusalCard, nhãn capability | FE1 | 0,5 | W4 |
| S11-02 | Alert inbox + hỏi tiếp | FE2 | 0,5 | W4 |
| VN-05 | Màn Việt Nam | FE2 | 1 | W4 |
| **Tổng** | | FE1 4,25 · FE2 3,75 | **8,0** | |

Năng lực: FE1 4,4; FE2 khoảng 2,9 (từ 05/10, tuần đầu 50%). FE2 thiếu ~0,85. Cắt theo thứ tự:
LineagePanel hiện dạng danh sách thay vì đồ thị (−0,25); màn Việt Nam dựng từ ValueCard/SeriesChart có
sẵn, không thiết kế riêng (−0,25); Entity & Series chỉ đọc, sửa qua Bulk Import (−0,25).

## 8. Sau POC

A2UI renderer khi v1.0 ra (dự kiến Q4/2026); MCP Apps cho capability; CopilotKit/AG-UI SDK nếu cần
client ngoài; khung bbox bằng pdf.js; Playwright e2e; lớp i18n; data threads kiểu Data Formulator 2;
màn forecast (dải bất định, nhãn "Dự báo"); tách các file trên 1.500 dòng.

## Nguồn

- AG-UI, tài liệu giao thức và danh sách sự kiện: https://docs.ag-ui.com/introduction, https://docs.ag-ui.com/concepts/events
- Six Agent Protocols Every AI Builder Needs to Know in 2026 (MindStudio): https://www.mindstudio.ai/blog/six-agent-protocols-ai-builders-2026
- Google, Introducing A2UI: https://developers.googleblog.com/introducing-a2ui-an-open-project-for-agent-driven-interfaces/ ; https://a2ui.org/
- MCP Apps (blog MCP, 26/01/2026): https://blog.modelcontextprotocol.io/posts/2026-01-26-mcp-apps/ ; đặc tả: https://github.com/modelcontextprotocol/ext-apps/blob/main/specification/2026-01-26/apps.mdx
- OpenAI, Add UI to your MCP server: https://developers.openai.com/plugins/build/chatgpt-ui
- Generative UI: LLMs are Effective UI Generators (arXiv 2604.09577): https://arxiv.org/abs/2604.09577
- Software as Content: Dynamic Applications as the Human-Agent Interaction Layer (arXiv 2603.21334): https://arxiv.org/abs/2603.21334
- WaitGPT (UIST 2024): https://arxiv.org/abs/2408.01703
- Data Formulator 2 (CHI 2025): https://dl.acm.org/doi/10.1145/3706598.3713296
- Agentic Visualization (IEEE CG&A 2025, arXiv 2505.19101): https://arxiv.org/abs/2505.19101
- Citations and Trust in LLM Generated Responses (AAAI 2025): https://arxiv.org/abs/2501.01303
- Seeing the Reasoning (CHI 2026 Extended Abstracts): https://dl.acm.org/doi/10.1145/3772363.3798613
- Bias in the Loop: How Humans Evaluate AI-Generated Suggestions (HDSR 8.2, 2026): https://arxiv.org/html/2509.08514 ; https://hdsr.mitpress.mit.edu/pub/nrcn4h7d/release/2
- Exploring automation bias in human–AI collaboration (AI & Society 2025): https://link.springer.com/content/pdf/10.1007/s00146-025-02422-7.pdf
- Databricks, Use trusted assets in AI/BI Genie spaces: https://docs.databricks.com/gcp/en/genie/trusted-assets
- Cortex Analyst vs. Genie (2026): https://colrows.com/blogs/cortex-analyst-vs-genie/
