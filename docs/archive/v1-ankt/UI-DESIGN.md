# UI\-DESIGN

# Thiết kế giao diện Data Agent cho phân tích đa chỉ số ANKT

> Phiên bản 16/09/2026 · Đối tượng: frontend, thiết kế sản phẩm, nhóm Data Research, chủ nhiệm nghiệp vụ\.<br>Căn cứ: khảo sát `frontend/src` \(134 file TS/TSX\) và các hợp đồng backend trong [SERVICE\\\-DESIGN\\\.md](SERVICE-DESIGN.md)\.<br>Tiếp nối mục 5 của [README\\\.md](README.md) và mục 5 của [PLATFORM\\\-ARCHITECTURE\\\.md](PLATFORM-ARCHITECTURE.md) — hai mục đó nêu *cần màn hình gì*; tài liệu này trả lời *màn hình đó gồm gì, xử lý được trường hợp nào, và dựng bằng component nào*\.<br>Màn hình “Hỏi dữ liệu” có bản thiết kế riêng, chi tiết hơn mục 6\.4 rất nhiều: [ASK\\\-WORKSPACE\\\.md](ASK-WORKSPACE.md)\.<br>Sơ đồ: [37](diagrams/37-ui-information-architecture.puml) bản đồ điều hướng · [38](diagrams/38-answer-render-contract.puml) hợp đồng render câu trả lời · [39](diagrams/39-uf-steward.puml) luồng steward · [40](diagrams/40-frontend-architecture.puml) kiến trúc frontend · [22](diagrams/22-uf-executive.puml)–[24](diagrams/24-seq-indicator-page.puml) luồng lãnh đạo/chuyên viên\.

## 0\. Tóm tắt

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910108495&cot=14)*

| \# | Kết luận |
| --- | --- |
| 1 | Giao diện hiện tại là một khung BI hội thoại tổng quát cộng một console quản trị nền tảng\. Nó trả lời tốt “metric trên warehouse”, nhưng không có chỗ nào hiển thị được một quan sát đã công bố, tại một vintage, trong một tài liệu cụ thể — tức đơn vị sự thật của ANKT\. |
| 2 | Cái đặc thù của Data Agent không nằm ở màn hình mới, mà ở một primitive mới: IndicatorValueCard — giá trị \+ đơn vị/bội số \+ kỳ \+ vintage \+ trạng thái ngưỡng \+ nguồn tới trang\. Primitive này phải xuất hiện y hệt nhau ở hội thoại, trang chỉ số, dashboard, cảnh báo và báo cáo \(mục 3\)\. |
| 3 | Cách xử lý “vô số trường hợp” không phải vẽ thêm màn hình mà là hợp đồng render theo intent: 9 intent của IQL ánh xạ vào 10 khối hiển thị dùng lại \(mục 4, sơ đồ 38\)\. LLM sinh IQL và lời diễn giải; intent quyết định bố cục, không phải LLM\. |
| 4 | Tính linh hoạt đến từ 6 điểm mở cho người dùng: chip ngữ cảnh sửa được, Explorer dựng IQL không viết JSON, rổ so sánh tự định nghĩa, saved view/dashboard cá nhân, đăng ký cảnh báo, nguồn dữ liệu cá nhân có nhãn “chưa duyệt” \(mục 7\)\. |
| 5 | Mọi trạng thái “không trả lời được” phải là một thẻ có lối ra, ánh xạ 1–1 từ mã lỗi có cấu trúc của compute\-svc — không có “xin lỗi, tôi không biết” \(mục 8\)\. |
| 6 | Ba lỗi giao diện cần sửa trước khi làm tính năng mới: formatVN\.ts đang định dạng số/ngày theo chuẩn Mỹ dù tên là VN; nhãn trộn Việt–Anh không có lớp i18n; trạng thái người dùng nằm ở localStorage nên mất khi đổi máy \(mục 1\.2\)\. |
| 6b | Riêng màn Hỏi dữ liệu phải là một không gian làm việc phân tích \(ngữ cảnh · hội thoại · canvas\), không phải khung chat: block có danh tính, năm tầng điều khiển từ lời nói tới SQL, gắn nguồn bằng @, tải tệp làm ngữ cảnh, thao tác BI kéo thả\. Phần lớn năng lực backend cho việc này đã có nhưng chưa phơi bày — bản thiết kế đầy đủ ở ASK\-WORKSPACE\.md\. |
| 7 | 12 nhóm tính năng ngoài phân tích chỉ số cần có để hệ thống dùng được trong vận hành thật: hộp thư realtime, giao việc, lịch công bố, issue dữ liệu, chú thích &amp; chia sẻ deep\-link, xuất bản tin, command palette, nhật ký truy cập số liệu mật, chế độ in/trình chiếu, mobile cho lãnh đạo, phím tắt cho steward, telemetry trải nghiệm \(mục 9\)\. |
| 8 | Khối lượng frontend ước tính: ~18 tuần công cho một người frontend qua 4 giai đoạn, bám lộ trình GĐ0–GĐ3 \(mục 11\)\. Demo 1 chỉ cần 5 primitive và 3 màn hình\. |

## 1\. Hiện trạng giao diện

### 1\.1 Đã có và dùng lại được

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910108500&cot=14)*

| Khu vực | Màn hình | Căn cứ |
| --- | --- | --- |
| Hỏi đáp | Hội thoại stream SSE, thẻ làm rõ, gợi ý tiếp theo, dừng giữa chừng, nhập bằng giọng nói | features/ask/AskDataPage\.tsx, hooks/useAgentStream\.ts, sse\.ts |
| Chứng cứ | Dải chứng cứ rút gọn \+ panel “số này ở đâu ra”: metric contract, SQL đã chạy, bảng nguồn, độ tươi, cảnh báo gộp/cắt | components/EvidencePanel\.tsx, EvidenceStrip\.tsx, types\.ts |
| Hiển thị dữ liệu | Biểu đồ 8 loại \(recharts\), bảng có phân trang, xuất CSV/XLSX | ChartRenderer\.tsx, DataGrid\.tsx, exportCsv\.ts |
| Tri thức | Upload, wizard ingest, kho tài sản, xem trước tài liệu, duyệt bảng xlsx, phát hiện mâu thuẫn, đồ thị tri thức, chính sách, thùng rác, warehouse | components/knowledge/\*\* \(9 khu\) |
| Builder | Agent CRUD/wizard/runtime/trace, workflow builder, release, playground | components/agent\-builder/\*\* |
| Vận hành | Hội thoại, trace, chờ duyệt, dashboard vận hành | Conversations\.tsx, AgentTraces\.tsx, ReviewCenter\.tsx, ExecutiveDashboard\.tsx |
| Quản trị | 12 mục Settings: tổ chức, IAM, SSO, secrets, compliance, hạ tầng, billing, API | components/Settings/\*\* |
| Nền | Design system nội bộ \(components/ui/index\.tsx, shared/\), quyền \(lib/permissions\.ts\), Keycloak, điều hướng theo URL path \+ hash cũ | AppStateContext\.tsx, routes\.tsx, constants\.ts |

Đây là một nền tốt: có streaming, có chứng cứ, có design system, có quyền\. **Không cần viết lại\.**

### 1\.2 Vì sao chưa dùng được cho ANKT

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910108502&cot=14)*

| \# | Vấn đề | Căn cứ trong mã | Hệ quả nghiệp vụ |
| --- | --- | --- | --- |
| U1 | Đơn vị trả lời là bảng SQL, không phải quan sát đã công bố\. Ví dụ mặc định vẫn là “Net revenue this month by region” | AskDataPage\.tsx: EXAMPLES, types\.ts: EvidenceRun\.sql | Không có chỗ nào hiển thị được “Nợ công/GDP 2024 = 34,7% · ước thực hiện · Bản tin nợ công số 17 trang 12” |
| U2 | Không có primitive “giá trị chỉ số” | không tìm thấy trong components/ | Mỗi màn hình mới sẽ tự vẽ lại cách hiển thị số → 5 chỗ hiển thị vintage khác nhau |
| U3 | Trích dẫn dừng ở mức đoạn/tài liệu, không có trang, không có bbox | types\.ts: Citation \(không có page\), CitationPanel\.tsx | Không đáp ứng ECO\-008\.14: người dùng không kiểm chứng được số |
| U4 | Không có khái niệm vintage trong bất kỳ kiểu dữ liệu nào | features/ask/types\.ts | Không phân biệt ước thực hiện / quyết toán; đúng số nhưng sai bản công bố |
| U5 | Không có trạng thái ngưỡng; biểu đồ không vẽ được dải ngưỡng đổi theo giai đoạn | ChartRenderer\.tsx \(chỉ x\_key/y\_keys\) | Không thể hiện được “NQ 26/2026 áp cho 2026–2030, kỳ 2025 dùng ngưỡng cũ” |
| U6 | Ngữ cảnh không đi theo người dùng và không deep\-link được theo tham số\. Điều hướng chỉ có module/subTab | AppStateContext\.tsx: navigate\(moduleId, subId\), routes\.tsx | Không gửi được link “trang chỉ số X, kỳ 2016–2024, vintage quyết toán” trong email cho lãnh đạo |
| U7 | Chỉ có một dạng thất bại: chuỗi lỗi chung | stream\.py: "Đã xảy ra lỗi, vui lòng thử lại\." | Người dùng không biết là chưa có dữ liệu, chưa công bố, sai tần suất hay không đủ quyền |
| U8 | formatVN\.ts định dạng theo chuẩn Mỹ dù tên gợi ý ngược lại: nhóm nghìn bằng ,, thập phân bằng \., ngày MM/dd/yyyy, tuần bắt đầu Chủ nhật | lib/formatVN\.ts \(docstring “US number / currency / date formatting”\) | Rủi ro cao nhất của dự án: bản tin nợ công viết “128,4” nghĩa là 128\.4; hiển thị lệch dấu thập phân tạo ra câu trả lời sai nhưng trông đáng tin, đúng loại lỗi README mục 7\.2 cảnh báo |
| U9 | Nhãn trộn Việt–Anh, không có lớp i18n: nav tiếng Việt, màn Ask tiếng Anh \(“Ask your data”, “Where is the number”\), Settings tiếng Anh | constants\.ts, AskDataPage\.tsx, features/ask/labels\.ts | Người dùng cuối là cơ quan nhà nước; không chuyển được ngôn ngữ, không thống nhất thuật ngữ |
| U10 | Trạng thái người dùng nằm ở localStorage: câu hỏi đã lưu, lịch sử | AskDataPage\.tsx: SAVED\_KEY, HISTORY\_KEY | Mất khi đổi máy/trình duyệt; không chia sẻ được; không audit được |
| U11 | Không có hộp thư, thông báo, giao việc, issue dữ liệu | không tìm thấy | Luồng cảnh báo \(21\) và luồng duyệt \(39\) không khép kín được |
| U12 | UI có, backend không có: SchedulingCenter lưu cron vào platform settings nhưng không có scheduler chạy | workflow/SchedulingCenter\.tsx \(banner tự thừa nhận\) | Người dùng tin là đã đặt lịch trong khi không có gì chạy |
| U13 | Không có tiến trình cho câu hỏi chạy lâu\. Câu đa chỉ số có ngân sách ≤ 30 s \(PLATFORM\-ARCHITECTURE mục 4\.1\) | useAgentStream\.ts chỉ có sending | 30 giây nhìn con trỏ nhấp nháy là hỏng trải nghiệm và người dùng bấm lại |

## 2\. Nguyên tắc thiết kế đặc thù

Tám nguyên tắc dưới đây là cái phân biệt Data Agent với một khung chat BI bất kỳ\. Mọi màn hình trong mục 6 đều phải kiểm được theo tám điểm này\.

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910108507&cot=14)*

| \# | Nguyên tắc | Nghĩa cụ thể trên giao diện |
| --- | --- | --- |
| P1 | Con số là công dân hạng nhất | Số không bao giờ là text trần trong câu\. Mọi số hiển thị qua IndicatorValueCard hoặc &lt;Value&gt; inline, mang đủ: giá trị · đơn vị \+ bội số · kỳ · vintage · trạng thái ngưỡng · nguồn |
| P2 | Mỗi con số bấm được tới trang tài liệu | Click số → mở DocumentPageViewer đúng trang, highlight bbox ô\. Không có số “mồ côi”; số không truy được nguồn thì không hiển thị |
| P3 | Vintage hiện diện ở mọi nơi có số | Nhãn vintage luôn nhìn thấy \(không giấu trong tooltip\)\. Số đã điều chỉnh mang cờ RevisionBadge kèm giá trị bản trước |
| P4 | Ngưỡng chọn theo kỳ dữ liệu, không theo ngày hỏi | Dải ngưỡng trên biểu đồ đổi theo giai đoạn hiệu lực; hover một điểm hiện văn bản căn cứ áp cho kỳ đó |
| P5 | Giả định luôn hiển thị và sửa được | Mọi slot agent tự điền \(kỳ, vintage, vùng, tần suất\) hiện thành chip; sửa chip → chạy lại truy vấn\. Người dùng không phải đoán hệ thống đã hiểu gì |
| P6 | Từ chối có cấu trúc, luôn kèm lối ra | Mỗi mã lỗi một thẻ riêng với hành động cụ thể \(mục 8\)\. Không dùng câu xin lỗi chung |
| P7 | Một primitive, mọi bề mặt | Cùng một component hiển thị giá trị trong hội thoại, trang chỉ số, dashboard, cảnh báo, báo cáo PDF\. Đổi cách hiển thị = sửa một chỗ |
| P8 | Không hiển thị số không có trong evidence | Verifier chặn ở backend; giao diện hiển thị VerifierBadge để người dùng thấy điều đó đã được kiểm, và ẩn phần trả lời chứa số không kiểm được thay vì hiện kèm cảnh báo nhỏ |

## 3\. Bộ primitive miền ANKT

Đây là phần “đặc thù” mà không design system chung nào có sẵn\. Mười hai primitive dưới đây nằm ở `frontend/src/components/indicator/`, dùng chung cho mọi màn hình \(sơ đồ [40](diagrams/40-frontend-architecture.puml)\)\.

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910108515&cot=14)*

| Primitive | Hợp đồng dữ liệu \(rút gọn\) | Xuất hiện ở | Ưu tiên |
| --- | --- | --- | --- |
| IndicatorValueCard | \{indicator\_id, label, value, unit, unit\_multiplier, decimal\_precision, period, frequency, vintage\{label, release\_date, obs\_status\}, threshold\{status, level, legal\_basis\_doc\}, source\_ref\{doc\_id, page, table, bbox\}, revision?\{previous\_value, released\_at\}\} | Hội thoại · trang chỉ số · dashboard · cảnh báo · báo cáo | GĐ0 |
| ThresholdTimeSeries | chuỗi \{period, value, vintage, revised\} \+ bands\[\]\{level, from, to, value, legal\_basis\} | Trang chỉ số · dashboard · báo cáo | GĐ0 |
| SourceRefChip → DocumentPageViewer | \{doc\_id, doc\_number, issuer, issued\_date, effective\_from, page, bbox\[\]\}; viewer render ảnh trang \+ khung highlight | Mọi nơi có số · workbench duyệt | GĐ0 |
| VintageControl | \{policy: latest\_published \| as\_of \| release, as\_of?, release\_id?\} \+ danh sách vintage có sẵn | Trang chỉ số · Explorer · chip ngữ cảnh | GĐ0 |
| RefusalCard | \{code, message, context, actions\[\]\} — mã lỗi có cấu trúc từ compute\-svc | Hội thoại · trang chỉ số · Explorer | GĐ0 |
| ContextChips | danh sách slot \{key, label, value, source: user \| inferred \| default, editable\} | Hội thoại · Explorer | GĐ1 |
| UnitDisplay | \{unit, multiplier, currency?, fx?\{rate, date, source\}\} — hiển thị “nghìn tỷ đồng”, không phải 1e12 VND | Mọi nơi có số | GĐ1 |
| RevisionBadge \+ RevisionTriangle | badge: \{previous\_value, delta, release\}; triangle: ma trận kỳ × ngày công bố | Trang chỉ số · Vintage Explorer | GĐ1 |
| DecompositionWaterfall | \{base, contributions\[\]\{variable, amount, share\}, total\} — đóng góp tử số/mẫu số/đánh giá lại | Trang chỉ số · hội thoại \(intent decompose\_change\) | GĐ1 |
| ComparisonMatrix \+ PerimeterWarning | \{rows\[\], columns\[\], cells\[\]\} \+ \{our\_definition, their\_definition, difference\_reason\} | So sánh nguồn · so sánh quốc tế | GĐ1 |
| VerifierBadge | \{checks: \{numbers\_grounded, tolerance\_ok, vintage\_consistent, citations\_resolved\}, model\_version\} | Hội thoại · báo cáo | GĐ1 |
| ThresholdHeatmapCell | \{domain, sub\_domain, indicator\_count, worst\_status, changed\_since\} | Dashboard ANKT | GĐ2 |

**Quy ước số học đi kèm** \(sửa `lib/formatVN\.ts`, xem U8\):

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910108536&cot=14)*

| Loại | Quy ước Việt Nam | Ví dụ |
| --- | --- | --- |
| Nhóm nghìn / thập phân | \. / , | 1\.284\.560 · 34,7 |
| Số chữ số thập phân | luôn theo decimal\_precision của chỉ số, không làm tròn tự do | công bố 1 chữ số → hiện 34,7, không hiện 34,70 hay 34,7123 |
| Bội số | hiện bằng chữ, không hiện số mũ | 1\.284,5 nghìn tỷ đồng |
| Ngày | dd/MM/yyyy, tuần bắt đầu Thứ Hai | 05/10/2026 |
| Kỳ | theo tần suất, không đổi định dạng | 2024 · Quý II/2024 · T6/2024 |
| Tỷ lệ | luôn kèm đơn vị so sánh khi là điểm phần trăm | \+1,2 điểm % khác \+1,2% |
| Số âm | dấu trừ, không dùng ngoặc đơn kiểu kế toán | \-3,4 |

Quy ước này phải có unit test riêng \(đã có `formatVN\.test\.ts` để mở rộng\), vì đây là chỗ sai gây hậu quả nặng nhất\.

## 4\. Hợp đồng render: cách xử lý “vô số trường hợp” bằng số ít component

Sơ đồ [38\\\-answer\\\-render\\\-contract](diagrams/38-answer-render-contract.puml)\.

**Quy tắc:** `intent` trong Analysis Query \(IQL, PLATFORM\-ARCHITECTURE mục 2\.1\) quyết định bố cục\. LLM sinh IQL và lời diễn giải; LLM **không** chọn khối hiển thị và **không** sinh số\. Nhờ vậy thêm một chỉ số mới không cần thêm dòng code giao diện nào, và mọi câu trả lời cùng loại trông giống nhau — điều kiện để người dùng học một lần dùng mãi\.

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910108546&cot=14)*

| intent | Khối chính | Khối phụ | Tương tác đặc thù | Suy giảm khi thiếu dữ liệu |
| --- | --- | --- | --- | --- |
| lookup | IndicatorValueCard | SourceRefChip | Click số → trang tài liệu | NO\_DATA\_FOR\_PERIOD → gợi ý kỳ gần nhất có số |
| compare\_threshold | IndicatorValueCard \+ ThresholdTimeSeries | văn bản căn cứ ngưỡng | Hover điểm → ngưỡng áp cho kỳ đó | Thiếu ngưỡng cho kỳ → hiện giá trị, ghi rõ “chưa có ngưỡng cho kỳ này” |
| trend | ThresholdTimeSeries | bảng quan sát | Chọn kỳ · đổi vintage · tải PNG | Chuỗi đứt → vẽ đứt, không nội suy; chú thích kỳ thiếu |
| decompose\_change | DecompositionWaterfall | ThresholdTimeSeries \(2 kỳ\) | Click cột → chỉ số/biến nguồn | Không đủ biến → hạ xuống trend \+ nêu lý do |
| cross\_section | RankingTable | biểu đồ cột | Đổi chiều · đổi kỳ | Chiều không có codelist → liệt kê mã thô kèm cảnh báo |
| cross\_country | ComparisonMatrix \+ PerimeterWarning | nguồn từng nước | Bật/tắt “chuẩn hoá theo định nghĩa IMF” | Khác phạm vi → bắt buộc hiện PerimeterWarning trước bảng |
| explain\_definition | DefinitionCard | công thức · biến · văn bản căn cứ | Click biến → trang biến | Chỉ số chưa duyệt → nêu trạng thái draft |
| scenario \(GĐ3\) | ScenarioTable | biểu đồ dải | Sửa giả định → chạy lại | Model chưa kiểm định → chặn, nêu lý do |
| ad\_hoc\_sql \(warehouse\) | DataGrid \+ ChartRenderer \(đã có\) | EvidencePanel \(đã có\) | Xuất CSV/XLSX | Đã có xử lý cắt dòng/ô |

**Ba khối bắt buộc kèm mọi câu trả lời** \(không phụ thuộc intent\): `ContextChips` \(giả định đã áp\), `VerifierBadge` \(đã kiểm gì\), `ActionBar` \(mở Explorer · thêm vào báo cáo · lưu · xuất · báo số sai\)\.

## 5\. Bản đồ điều hướng

Sơ đồ [37\\\-ui\\\-information\\\-architecture](diagrams/37-ui-information-architecture.puml)\.

### 5\.1 Nav hiện tại và nav mục tiêu

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910108554&cot=14)*

| Hiện tại \(constants\.ts: NAV\_ITEMS\) | Mục tiêu | Lý do đổi |
| --- | --- | --- |
| Hỏi dữ liệu | Tổng quan \(mới\) | Lãnh đạo cần bức tranh trước câu hỏi |
| Tri thức | Hỏi dữ liệu \(giữ, nâng cấp\) |  |
| Xây dựng | Chỉ số \(mới\) | Khu vực làm việc chính của chuyên viên |
| Vận hành | Cảnh báo \(mới\) | Nghiệp vụ ANKT là cảnh báo, không chỉ tra cứu |
| Cài đặt | Báo cáo \(mới\) | Sản phẩm đầu ra chính thức |
|  | Dữ liệu \(gộp “Tri thức” \+ Data Studio\) | Tri thức hiện tại là một khu của Data Studio, không phải module ngang hàng |
|  | Đánh giá \(mới, chỉ QA\) |  |
|  | Xây dựng · Vận hành · Cài đặt \(giữ\) | Console kỹ thuật, ẩn với lãnh đạo |

**Landing theo vai trò**: lãnh đạo → Tổng quan · chuyên viên → Chỉ số \(hoặc Cảnh báo nếu có cảnh báo mở được giao\) · steward → Dữ liệu/hàng đợi duyệt · QA → Đánh giá · kỹ thuật → Xây dựng \(hành vi hiện có trong `AppStateContext`, mở rộng thêm vai trò\)\.

**Vai trò cần bổ sung** vào `types\.ts: Role` \(hiện chỉ có `PLATFORM\_ADMIN | AI\_ENGINEER | EXECUTIVE`\): `ANALYST`, `DATA\_STEWARD`, `REVIEWER`, `QA`\. Nav và quyền màn hình đọc từ đây; quyền **dữ liệu** \(theo phân loại\) vẫn do backend quyết định, giao diện chỉ hiển thị kết quả \(mục 8\)\.

### 5\.2 Deep link — trạng thái nằm trong URL

Yêu cầu nghiệp vụ: gửi link trong email/văn bản để người nhận **thấy đúng cái người gửi thấy**\. Hiện `navigate\(moduleId, subId\)` chỉ giữ hai cấp, không giữ tham số\.

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910108573&cot=14)*

| Màn hình | URL |
| --- | --- |
| Trang chỉ số | /indicators/\{indicator\_id\}?from=2010&amp;to=2025&amp;vintage=as\_of:2026\-03\-01&amp;compare=TH,MY&amp;tab=series |
| Explorer | /indicators/explore?q=\{iql\_base64url\} |
| Cảnh báo | /alerts/\{alert\_id\} |
| Tài liệu nguồn | /documents/\{doc\_id\}/pages/\{n\}?bbox=…&amp;highlight=cell |
| Hội thoại | /ask/\{conversation\_id\}\#turn=\{n\} |
| Hàng đợi duyệt | /studio/reviews/\{review\_id\}?cell=\{row\},\{col\} |

Nguyên tắc: **URL là nguồn sự thật cho trạng thái màn hình**; component đọc từ URL, không giữ bản sao trong state\. Việc này cần một lớp `lib/urlState\.ts` và mở rộng router hiện có \(không bắt buộc đổi sang react\-router: cơ chế pathname \+ `history\.replaceState` sẵn có đủ, chỉ cần thêm cấp tham số\)\.

## 6\. Đặc tả màn hình

Mỗi mục nêu: người dùng · việc làm được · API phía sau \(SERVICE\-DESIGN mục 4\.4\) · trạng thái rỗng/lỗi · phần tái dùng được\.

### 6\.1 Tổng quan \(Dashboard ANKT\)

- **Người dùng**: lãnh đạo, chuyên viên\.
- **Nội dung**: heatmap domain × sub\-domain tô theo trạng thái ngưỡng xấu nhất; hàng “thay đổi từ lần xem trước”; hộp cảnh báo mới; việc được giao; lịch công bố 14 ngày tới; bản tin gần nhất; **dashboard cá nhân** \(người dùng tự ghim chỉ số, sắp xếp bằng kéo thả\)\.
- **API**: `compute /indicators/values` · `alert /alerts?status=open` · `report /reports?latest` · `source\-registry /release\-calendar` · `notification /notifications`\.
- **Trạng thái rỗng**: chưa có chỉ số nào được duyệt → hướng dẫn tới Indicator Studio, không hiện heatmap trống\.
- **Tái dùng**: `ExecutiveDashboard\.tsx` \(bố cục StatTile \+ issue list \+ OperationalHeader\) — giữ khung, đổi nội dung từ số liệu vận hành sang chỉ số ANKT\. *Lưu ý: màn vận hành hiện tại vẫn cần, chuyển về module Vận hành\.*

### 6\.2 Danh mục chỉ số

- **Người dùng**: chuyên viên, research\.
- **Nội dung**: cây taxonomy domain → sub\-domain; tìm theo tên/bí danh \(hybrid search\); lọc theo trạng thái ngưỡng, độ tươi dữ liệu, trạng thái duyệt, có/không có dữ liệu kỳ mới nhất; mỗi hàng là một `IndicatorValueCard` rút gọn; chọn nhiều chỉ số → “so sánh” hoặc “tạo rổ”\.
- **API**: `catalog /indicators?q=&amp;domain=&amp;status=` · `compute /indicators/values`\.
- **Trạng thái rỗng**: tìm không ra → gợi ý bí danh gần đúng và “hỏi agent câu này”\.
- **Tái dùng**: `shared/DataTable\.tsx`, `FilterPopover\.tsx`, `Pagination\.tsx`\.

### 6\.3 Trang chỉ số

Màn hình trung tâm\. Sequence kỹ thuật: [24\\\-seq\\\-indicator\\\-page](diagrams/24-seq-indicator-page.puml)\.

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910108584&cot=14)*

| Tab | Nội dung | Primitive |
| --- | --- | --- |
| Tổng quan | Giá trị kỳ mới nhất, xu hướng, trạng thái ngưỡng, “vì sao thay đổi” \(agent tóm tắt có trích dẫn\), cảnh báo đang mở | IndicatorValueCard · ThresholdTimeSeries · VerifierBadge |
| Chuỗi | Biểu đồ có dải ngưỡng theo giai đoạn; bảng quan sát kèm nguồn từng dòng; chọn kỳ, tần suất, vintage | ThresholdTimeSeries · VintageControl · SourceRefChip |
| Định nghĩa | Công thức \(DAG hiển thị được\), biến nguyên tử, đơn vị, stock/flow, polarity, ngưỡng theo giai đoạn \+ văn bản căn cứ, phiên bản chỉ số | DefinitionCard |
| Vintage | Tam giác điều chỉnh; so hai vintage; lý do điều chỉnh | RevisionTriangle · RevisionBadge |
| Phân rã | Đóng góp của từng biến vào biến động giữa hai kỳ | DecompositionWaterfall |
| So sánh | Nguồn khác · nước khác · chỉ số liên quan, kèm cảnh báo phạm vi | ComparisonMatrix · PerimeterWarning |
| Nguồn | Danh sách tài liệu nguồn kèm số hiệu, cơ quan, ngày ban hành, hiệu lực | SourceRefChip · DocumentPageViewer |

- **Panel hỏi agent theo ngữ cảnh** \(bên phải, thu gọn được\): câu hỏi gửi kèm `\{indicator\_id, period, vintage, filters\}`; các chip ngữ cảnh hiện ở đầu ô nhập và **sửa được**\.
- **API**: 4 request song song \(catalog · compute · kb · alert\), xem [24](diagrams/24-seq-indicator-page.puml)\. Chỉ tiêu p95 &lt; 1,5 s → skeleton từng khối, không chặn cả trang\.
- **Tái dùng**: `DetailDrawer\.tsx` \(bố cục chi tiết\), `AnswerTheme\.tsx` \(chủ đề\), `ChartRenderer\.tsx` \(nền recharts, cần thêm lớp dải ngưỡng `ReferenceArea`\)\.

### 6\.4 Hỏi dữ liệu \(nâng cấp\)

> **Bản thiết kế đầy đủ: [ASK\\\-WORKSPACE\\\.md](ASK-WORKSPACE.md)** — Ask không dừng ở “khung chat có thêm vài<br>thứ”, mà là một không gian làm việc phân tích ba bề mặt với block có danh tính, thang năm tầng điều khiển,<br>gắn ngữ cảnh bằng `@`, tải tệp, thao tác BI kéo thả, SQL và pandas\.<br>Sơ đồ [41](diagrams/41-ask-workspace-layout.puml)–[44](diagrams/44-seq-block-manipulation.puml)\.

Sáu thay đổi tối thiểu \(mức bắt buộc, nằm trong đợt A–B của lộ trình Ask\):

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910108604&cot=14)*

| Thay đổi | Chi tiết |
| --- | --- |
| Khối trả lời theo intent | AnswerBlock\.tsx chuyển thành bộ chuyển mạch theo intent \(mục 4\), thay vì luôn là markdown \+ chart \+ grid |
| Chip ngữ cảnh | Dải chip trên ô nhập hiển thị slot đã suy ra; sửa chip → gửi lại câu hỏi với slot mới \(không phải gõ lại\) |
| Tường thuật tiến trình | Câu đa chỉ số chạy 30 s: hiện các bước supervisor/sub\-agent đang chạy \(“đang lấy quan sát 2016–2024”, “đang đối chiếu IMF”\), lấy từ frame mới \_\_progress — giải quyết U13 |
| Lịch sử phía server | Chuyển localStorage → API hội thoại đã có \(Conversations\.tsx dùng cùng nguồn\); câu hỏi đã lưu thành “phân tích đã lưu”, chia sẻ được |
| Thẻ từ chối | RefusalCard thay chuỗi lỗi chung \(mục 8\) |
| Ví dụ mở đầu | Thay 3 ví dụ BI tiếng Anh bằng ví dụ ANKT tiếng Việt lấy từ golden set |

### 6\.5 Xem tài liệu nguồn \(DocumentPageViewer\)

- **Nội dung**: ảnh trang render sẵn \+ khung bbox của ô/đoạn được trích dẫn; thanh metadata văn bản \(tên, số hiệu, cơ quan, ngày ban hành, hiệu lực từ/đến, SHA\-256\); chuyển trang; tải file gốc; “báo trích xuất sai” → issue dữ liệu\.
- **API**: `kb /documents/\{id\}` · `extraction /artifacts/\{sha\}/pages/\{n\}\.png`\.
- **Vì sao không dùng iframe PDF như hiện nay**: iframe không highlight được ô, không chạy với PDF scan đã OCR, và không hiển thị được bbox do extraction trả về\. Ảnh trang \+ overlay SVG giải quyết cả ba\.
- **Tái dùng**: `AssetDetailWorkspace\.tsx` \(tab Preview/Tables\) làm khung\.

### 6\.6 Hộp cảnh báo

- **Nội dung**: danh sách theo mức độ/domain/trạng thái; chi tiết cảnh báo gồm chỉ số, ngưỡng bị chạm, giá trị trước/sau, nguyên nhân đề xuất \(agent\), link trang chỉ số; hành động: xác nhận · giao việc · đóng kèm lý do \(bao gồm `false\_positive` khi do lỗi dữ liệu\) · tạo issue dữ liệu\.
- **API**: `alert /alerts`, `/alerts/\{id\}:ack|resolve` · `notification` \(SSE\)\.
- **Luồng**: [21\\\-bf\\\-alert\\\-handling](diagrams/21-bf-alert-handling.puml)\.
- **Tái dùng**: `ReviewCenter\.tsx` \(mô hình hàng đợi \+ hành động\), `StatusBadge\.tsx`\.

### 6\.7 Báo cáo / bản tin

- **Nội dung**: chọn template → thêm khối \(chỉ số · biểu đồ · bảng · đoạn agent soạn có trích dẫn · ghi chú người dùng\) → xem trước bản in → gửi duyệt → phát hành PDF/DOCX; lịch phát hành định kỳ; lịch sử phiên bản\.
- **Điểm đặc thù**: mọi khối số trong báo cáo **giữ nguyên trích dẫn**; bản PDF có phụ lục nguồn \(tên, số hiệu, trang\) — đây là yêu cầu văn bản hành chính, không phải tuỳ chọn\.
- **API**: `report /reports`, `:render|submit|approve` · `compute` · `runtime` \(đoạn văn\) · `kb` \(trích dẫn\)\.
- **Tái dùng**: `exportCsv\.ts` \+ `answer\_exports` backend; `ReleaseManagementCenter\.tsx` cho mô hình duyệt/phát hành\.

### 6\.8 Dữ liệu \(Data Studio\)

Chín khu chức năng đã liệt kê ở [PLATFORM\\\-ARCHITECTURE mục 5](PLATFORM-ARCHITECTURE.md) và [11\\\-data\\\-ui\\\-map](diagrams/11-data-ui-map.puml)\. Ở đây bổ sung đặc tả cho khu quan trọng nhất về khối lượng công việc:

**Workbench duyệt trích xuất** \(luồng đầy đủ: [39\\\-uf\\\-steward](diagrams/39-uf-steward.puml)\)

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910108615&cot=14)*

| Yêu cầu | Thiết kế |
| --- | --- |
| Đối chiếu nhanh | Hai cột: ảnh trang có bbox ↔ bảng ứng viên sửa được; click ô → cuộn tới bbox và ngược lại |
| Tập trung vào chỗ đáng ngờ | Hệ thống tô sẵn ô độ tin cậy thấp, ô vi phạm DQ, ô lệch bất thường so kỳ trước; bộ lọc “chỉ hiện ô có cờ” |
| Năng suất | Một kỳ công bố có thể sinh hàng trăm ô\. Bắt buộc thao tác bằng bàn phím: j/k ô kế · Enter sửa · a duyệt · r từ chối · ? bảng phím tắt\. Duyệt theo cột/bảng khi không có cờ |
| Không mất vết | Mọi sửa ghi value\_edit kèm giá trị gốc và lý do \(OCR sai · sai đơn vị · sai kỳ · sai bội số\) |
| Học được | Sửa mapping lưu thành quy tắc cho nguồn đó, lần sau tự áp |
| Maker–checker | Người duyệt 2 chỉ xem chênh lệch so với đề xuất máy và các ô đã sửa, không duyệt lại từ đầu; hệ thống chặn tự duyệt |
| Tái dùng | StructuredTableReview\.tsx \(sửa schema bảng\) làm điểm xuất phát; IngestionProgressOverlay\.tsx, usePromotionQueue\.ts cho tiến trình |

Ngoài ra: **Indicator Studio** \(form theo schema ECO\-001\.5, formula builder kiểm đơn vị/stock\-flow, ngưỡng theo giai đoạn \+ văn bản căn cứ, duyệt phiên bản, phân tích tác động trước khi đổi\) — luồng nghiệp vụ [19](diagrams/19-bf-indicator-lifecycle.puml)\.

### 6\.9 Đánh giá \(Eval console\)

- **Nội dung**: golden set theo họ chỉ số; sinh case từ observation → hàng đợi người duyệt; chính sách dung sai theo loại case \(README mục 6\.3\); chạy eval theo phiên bản model/cấu hình; so sánh hai lần chạy; scorecard 4 điểm \(`value` · `source` · `vintage` · `threshold\_status`\) \+ `refusal\_correct`; link trace Langfuse từng case\.
- **API**: `eval /golden\-cases`, `/eval\-runs`, `/eval\-runs:compare`\.
- **Tái dùng**: `AgentTraces\.tsx` \(TraceExplorer\), `AgentPlayground\.tsx` \(chạy thử\), Langfuse Datasets UI cho GĐ0\.

### 6\.10 Hộp thư, giao việc, issue dữ liệu

- **Hộp thư**: chuông \+ danh sách, đẩy realtime qua SSE `notification /notifications/stream`; tuỳ chọn nhận theo loại \(cảnh báo, việc được giao, báo cáo xong, eval xong, kỳ công bố mới\)\.
- **Giao việc**: từ cảnh báo, từ câu trả lời, từ trang chỉ số → tạo nhiệm vụ kèm deep\-link ngữ cảnh \+ hạn xử lý\.
- **Issue dữ liệu**: nút “báo số sai” ở mọi nơi có số → issue gắn `\{indicator, series\_key, period, release, source\_ref\}` → hàng đợi steward; trạng thái quay lại người báo\.

### 6\.11 Quản trị: quyền theo phân loại dữ liệu

- Màn cấu hình ABAC \(vai trò × phân loại × domain\), xem trước “người này thấy gì”\.
- **Nhật ký truy cập số liệu mật**: ai xem chỉ số `restricted`, khi nào, qua màn hình nào — yêu cầu bắt buộc với dữ liệu ANKT\.
- **Tái dùng**: `Settings/IAMSection\.tsx`, `Auth/AuthAuditPanel\.tsx`\.

## 7\. Tính linh hoạt: người dùng tự dựng, không cần lập trình

Nền tảng ship **catalog**; người dùng **compose**\. Sáu điểm mở dưới đây là cái làm hệ thống “flexible” mà không phá tính kiểm chứng được\.

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910108624&cot=14)*

| \# | Năng lực | Cách hoạt động | Ranh giới an toàn |
| --- | --- | --- | --- |
| F1 | Chip ngữ cảnh sửa được | Mọi slot agent suy ra hiện thành chip có nhãn nguồn gốc \(người dùng nhập · agent suy · mặc định hệ thống\); sửa → chạy lại | Không cho sửa indicator\_version đã duyệt |
| F2 | Explorer | Dựng Analysis Query bằng form: chọn chỉ số → kỳ → vintage → chiều → phép biến đổi → so sánh\. Sinh ra IQL, xem được JSON, chia sẻ bằng URL | Compiler tất định kiểm trước khi chạy; lỗi trả về dạng RefusalCard |
| F3 | Rổ so sánh | Người dùng tự tạo nhóm \(“ASEAN\-5”, “nhóm chỉ tiêu A”\), đặt tên, chia sẻ cho nhóm | Cảnh báo phạm vi vẫn bắt buộc khi trộn nguồn khác định nghĩa |
| F4 | Saved view &amp; dashboard cá nhân | Lưu bộ lọc/kỳ/vintage/ghi chú thành “phân tích đã lưu”; ghim chỉ số lên dashboard cá nhân, kéo thả sắp xếp | Lưu tham số, không lưu kết quả → mở lại luôn lấy số mới nhất, kèm cờ nếu số đã điều chỉnh |
| F5 | Đăng ký cảnh báo &amp; lịch công bố | Theo dõi một chỉ số/domain: nhận thông báo khi đổi trạng thái ngưỡng, khi có release mới, khi tới kỳ công bố dự kiến | Không cho người dùng tự đặt ngưỡng pháp lý; ngưỡng cá nhân đánh dấu rõ “ngưỡng theo dõi cá nhân” |
| F6 | Nguồn dữ liệu cá nhân | Upload Excel/PDF riêng → mapping nhanh → dùng để so sánh trong phiên làm việc | Gắn nhãn “chưa duyệt · dữ liệu cá nhân” ở mọi hiển thị; không vào observation store, không dùng cho golden set, không đưa vào báo cáo đã duyệt |

Hai đòn bẩy linh hoạt thêm:

- **Chế độ đọc / chế độ làm việc**: cùng trang chỉ số, hai mật độ\. Lãnh đạo thấy 1 số \+ 1 biểu đồ \+ 1 câu giải thích; chuyên viên thấy đủ 7 tab\. Không phải hai màn hình\.
- **Sở thích cá nhân**: ngôn ngữ, đơn vị hiển thị mặc định, chính sách vintage mặc định \(`latest\_published` hay `official`\), mật độ, định dạng ngày\. Lưu phía server theo người dùng\.

## 8\. Trạng thái, lỗi và suy giảm

Đây là phần quyết định “xử lý được nhiều trường hợp”\. Mã lỗi có cấu trúc của compute\-svc \(SERVICE\-DESIGN mục 3\.3\) ánh xạ 1–1 sang giao diện\.

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910108637&cot=14)*

| Mã | Thẻ hiển thị | Lối ra cụ thể |
| --- | --- | --- |
| NO\_DATA\_FOR\_PERIOD | “Chưa có số cho kỳ \{kỳ\}” | Nút chuyển sang kỳ gần nhất có số · “nhận thông báo khi công bố” · xem lịch công bố dự kiến |
| FREQUENCY\_NOT\_AVAILABLE | “Chỉ số này chỉ có theo năm” | Chuyển sang tần suất có sẵn · gợi ý chỉ số thay thế có tần suất quý |
| VINTAGE\_NOT\_FOUND | “Không có bản công bố tại \{ngày\}” | Danh sách vintage có sẵn để chọn |
| INDICATOR\_NOT\_APPROVED | “Chỉ số đang ở bản nháp” | Xem định nghĩa nháp \(nếu có quyền\) · liên hệ chủ sở hữu · theo dõi để nhận thông báo khi duyệt |
| ACCESS\_DENIED | “Số liệu này thuộc mức \{phân loại\}” | Nút yêu cầu quyền truy cập \(tạo yêu cầu, không phải email tay\) · vẫn hiện metadata \(chỉ số tồn tại, ai sở hữu\) nhưng không hiện giá trị |
| PERIMETER\_MISMATCH | Cảnh báo phạm vi | Bảng so định nghĩa hai bên · nút “so theo định nghĩa IMF” |
| RELEASE\_RETRACTED | “Bản công bố này đã bị rút lại” | Lý do rút · chuyển sang release hiện hành |
| TOLERANCE\_EXCEEDED \(verifier\) | Chặn hiển thị số | Nêu rõ số tự tính lệch số công bố bao nhiêu · link issue dữ liệu |
| Không khớp chỉ số nào | ClarificationCard | Top\-k chỉ số gần đúng để chọn · “hỏi theo cách khác” · “đề xuất chỉ số mới” |

**Ma trận trạng thái màn hình** \(mọi màn hình phải có đủ\):

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910108641&cot=14)*

| Trạng thái | Thiết kế |
| --- | --- |
| Đang tải | Skeleton theo khối, không spinner toàn trang \(trang chỉ số có 4 request song song, khối nào xong hiện trước\) |
| Đang stream nhiều bước | Tường thuật bước đang chạy \+ nút dừng \(U13\) |
| Kết quả một phần | Banner “thiếu kỳ 2021–2022 do nguồn chưa công bố”, phần có số vẫn dùng được |
| Dữ liệu cũ \(stale\) | Badge độ tươi \(đã có FRESHNESS\_LABELS\) \+ ngày dữ liệu |
| Số đã điều chỉnh | RevisionBadge \+ giá trị bản trước, không âm thầm thay số |
| Không có quyền | Hiện metadata, ẩn giá trị, nút yêu cầu quyền |
| Rỗng thật sự | Nêu bước tiếp theo cụ thể, không dùng câu “Không có dữ liệu” |
| Mất kết nối SSE | Tự nối lại, giữ phần đã nhận, báo rõ đã nối lại |

## 9\. Tính năng khác ngoài phân tích chỉ số

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910108646&cot=14)*

| \# | Tính năng | Vì sao cần | Trạng thái |
| --- | --- | --- | --- |
| 1 | Hộp thư &amp; thông báo realtime | Cảnh báo, việc được giao, kỳ công bố mới — không có thì luồng 21 và 39 không khép kín | Chưa có \(notification\-svc đã thiết kế\) |
| 2 | Giao việc | Lãnh đạo giao chuyên viên phân tích sâu; cảnh báo gán người phụ trách | Chưa có |
| 3 | Lịch công bố | “GDP quý III dự kiến công bố 06/10” — người dùng biết khi nào có số thay vì hỏi lại mỗi tuần | Chưa có \(sources\.yaml đã có trường schedule\) |
| 4 | Issue dữ liệu | Nút “báo số sai” ở mọi nơi có số, nối thẳng vào hàng đợi steward và eval | Chưa có |
| 5 | Chú thích &amp; chia sẻ | Ghi chú trên một điểm dữ liệu, @nhắc đồng nghiệp, chia sẻ deep\-link đầy đủ trạng thái | Chưa có |
| 6 | Xuất bản | Excel \+ sheet “Nguồn” \(đã có\), DOCX/PDF có phụ lục nguồn, PNG biểu đồ cho slide, copy trích dẫn theo kiểu văn bản hành chính | Một phần |
| 7 | Lịch sử hội thoại phía server | Hiện ở localStorage; cần để chia sẻ, audit, và tiếp tục trên máy khác | Một phần \(Conversations\.tsx đã đọc từ server\) |
| 8 | Tìm kiếm toàn cục &amp; command palette | Ctrl\+K: nhảy tới chỉ số, tài liệu, cảnh báo, màn hình — thiết yếu khi có hàng trăm chỉ số | Chưa có |
| 9 | Nhật ký truy cập số liệu mật | Bắt buộc với dữ liệu phân loại | Một phần \(AuthAuditPanel\) |
| 10 | Chế độ in / trình chiếu | Lãnh đạo họp bằng bản in; báo cáo phải in đẹp trên A4 | Chưa có |
| 11 | Mobile / tablet cho lãnh đạo | Dashboard, cảnh báo, bản tin — ba màn hình này phải dùng được trên điện thoại | Chưa có |
| 12 | Phím tắt &amp; a11y | Steward duyệt hàng trăm ô bằng bàn phím; tương phản và focus đúng chuẩn cho toàn ứng dụng | Chưa có |
| 13 | Telemetry trải nghiệm | Đo: tỉ lệ câu hỏi bị làm rõ, tỉ lệ từ chối theo mã lỗi, tỉ lệ bấm vào trích dẫn, thời gian tới câu trả lời đầu tiên\. Đây là dữ liệu để biết chỗ nào cần sửa | Chưa có |
| 14 | Trợ giúp theo ngữ cảnh | “Vintage là gì?”, “Vì sao số này khác IMF?” — giải thích tại chỗ, không bắt đọc tài liệu riêng | Chưa có |

## 10\. Kiến trúc frontend

Sơ đồ [40\\\-frontend\\\-architecture](diagrams/40-frontend-architecture.puml)\.

### 10\.1 Cấu trúc thư mục đề xuất

```javascript
frontend/src/
  features/
    ask/            (đã có — nâng cấp theo mục 6.4)
    overview/       dashboard ANKT + dashboard cá nhân
    indicators/     catalog · IndicatorPage · Explorer
    alerts/         hộp cảnh báo · đăng ký
    reports/        soạn bản tin · duyệt · lịch
    studio/         nguồn · duyệt trích xuất · mapping · vintage · DQ · lineage · indicator studio
    eval/           golden set · lần chạy · scorecard
  components/
    indicator/      12 primitive miền ANKT (mục 3)
    ui/ shared/     (đã có)
  lib/
    api/            catalog · observation · compute · alert · report · eval · studio
    iql.ts          dựng Analysis Query có kiểu
    urlState.ts     trạng thái màn hình trong URL
    notifications.ts SSE hộp thư
    i18n/           vi · en
    formatVN.ts     (sửa theo quy ước VN — mục 3)
```

Quy ước đặt tên giữ theo `CLAUDE\.md` mục 3: component `PascalCase\.tsx`, hook `useThing\.ts`, module khác `camelCase\.ts`\.

### 10\.2 Lớp API và kiểu dữ liệu

- Mỗi service một module trong `lib/api/`, kiểu sinh từ OpenAPI của service đó \(API\-first, PLATFORM\-ARCHITECTURE mục 4\.3\) — **không viết kiểu bằng tay** như `features/ask/types\.ts` hiện nay, để tránh lệch hợp đồng\.
- `lib/iql\.ts` giữ kiểu `AnalysisQuery` dùng chung cho Explorer, chip ngữ cảnh và deep\-link — một nguồn sự thật cho “truy vấn phân tích” ở phía client\.

### 10\.3 Mở rộng frame SSE

Frame hiện có \(`stream\.py`\): `\_\_session\_id`, `\_\_chart`, `\_\_citations`, `\_\_options`, `\_\_evidence`, `\_\_clarification`, `\_\_suggestions`, `\_\_pending\_review`, `\_\_reset`, `\_\_conv\_ids`\. Cần thêm:

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910108653&cot=14)*

| Frame mới | Nội dung | Phục vụ |
| --- | --- | --- |
| \_\_intent | \{intent, iql\} | Chọn khối hiển thị \(mục 4\) |
| \_\_indicator\_value | payload IndicatorValueCard | P1 · P7 |
| \_\_context | slot đã suy ra \+ nguồn gốc từng slot | ContextChips \(F1\) |
| \_\_progress | \{step, sub\_agent, status\} | Tường thuật tiến trình \(U13\) |
| \_\_refusal | \{code, context, actions\} | RefusalCard \(mục 8\) |
| \_\_verification | kết quả verifier | VerifierBadge \(P8\) |

`Citation` cần thêm `page`, `bbox`, `doc\_number`, `issuer`, `issued\_date`, `vintage` \(U3, U4\)\.

Các frame cho không gian làm việc \(`\_\_block`, `\_\_block\_patch`, `\_\_context\_used`, `\_\_job`\) và payload Ask mở rộng<br>\(`context\_refs`, `target\_block\_id`, `slots`\): [ASK\\\-WORKSPACE\\\.md](ASK-WORKSPACE.md) mục 11\.

### 10\.4 Hiệu năng, kiểm thử, i18n

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910108656&cot=14)*

| Mục | Yêu cầu |
| --- | --- |
| Hiệu năng | Trang chỉ số p95 &lt; 1,5 s: 4 request song song, skeleton theo khối, cache catalog 5 phút\. Bảng quan sát dài dùng ảo hoá\. Ảnh trang tải theo nhu cầu |
| i18n | vi là ngôn ngữ mặc định, en là bản phụ\. Không nhúng chuỗi thẳng trong JSX\. Thuật ngữ miền \(vintage, phạm vi, quyết toán\) có bảng thuật ngữ thống nhất |
| Kiểm thử | Mỗi primitive có test render \+ test định dạng số/ngày VN; formatVN mở rộng test cho dấu thập phân; luồng chính có test tương tác \(vitest \+ testing\-library, đã có test\-utils/render\.tsx\) |
| Dọn dẹp | knip phải sạch với mã mới; không để component chết trong components/ |

## 11\. Lộ trình giao diện

Bám lộ trình GĐ0–GĐ3 \([06\\\-roadmap\\\-gantt](diagrams/06-roadmap-gantt.puml)\)\. Ước lượng cho **một người frontend**; công việc backend tương ứng ở [SERVICE\\\-DESIGN mục 9](SERVICE-DESIGN.md)\.

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910108658&cot=14)*

| Giai đoạn | Giao diện | Primitive | Phụ thuộc backend | Ước lượng | Nghiệm thu |
| --- | --- | --- | --- | --- | --- |
| GĐ0 · Demo 1 | Thẻ kết quả chỉ số trong Ask · trang chỉ số \(tab Tổng quan \+ Chuỗi \+ Nguồn\) · DocumentPageViewer · sửa formatVN theo chuẩn VN | IndicatorValueCard · ThresholdTimeSeries · SourceRefChip · VintageControl · RefusalCard | catalog \+ compute \+ observation tối thiểu; page/bbox trong citation | ~4 tuần | Hỏi “Nợ công/GDP 2024” → thẻ có giá trị, vintage, trạng thái ngưỡng, bấm được tới trang bản tin |
| GĐ1 · Demo 2 | Danh mục chỉ số · đủ 7 tab trang chỉ số · workbench duyệt trích xuất · chip ngữ cảnh · tường thuật tiến trình · i18n | \+ ContextChips · UnitDisplay · RevisionBadge/Triangle · DecompositionWaterfall · ComparisonMatrix · VerifierBadge | extraction có bbox · curation · mapping · frame SSE mới | ~5 tuần | Steward duyệt xong một kỳ công bố hoàn toàn trong giao diện; chuyên viên phân rã được biến động |
| GĐ2 · Demo 3 | Dashboard ANKT · hộp cảnh báo · hộp thư \+ giao việc · báo cáo/bản tin · Explorer · saved view · issue dữ liệu | \+ ThresholdHeatmapCell | alert · notification · report · lineage | ~6 tuần | Cảnh báo chạm ngưỡng → thông báo → chuyên viên xử lý → bản tin có trích dẫn, tất cả trong giao diện |
| GĐ3 | Eval console · lineage viewer · kịch bản what\-if · mobile lãnh đạo · chế độ in | \+ ScenarioTable | eval\-svc · model registry | ~3 tuần | QA quản lý golden set và so sánh lần chạy không cần Langfuse UI |

**Việc làm được ngay, không chờ backend mới:**

1. Sửa `lib/formatVN\.ts` theo quy ước VN \+ mở rộng test \(U8 — rủi ro cao nhất, chi phí thấp nhất\)\.
2. Dựng lớp i18n và rút chuỗi ra khỏi JSX \(U9\)\.
3. Thay ví dụ mở đầu của Ask bằng câu hỏi ANKT tiếng Việt\.
4. Bổ sung vai trò `ANALYST`, `DATA\_STEWARD`, `REVIEWER`, `QA` vào `types\.ts` và `permissions\.ts`\.
5. Dựng `lib/urlState\.ts` và thêm cấp tham số cho điều hướng \(U6\)\.
6. Chuyển câu hỏi đã lưu/lịch sử từ `localStorage` sang API hội thoại đã có \(U10\)\.

## 12\. Quyết định giao diện cần chốt

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910108659&cot=14)*

| \# | Quyết định | Phương án | Đề xuất |
| --- | --- | --- | --- |
| UI\-1 | Ai chọn bố cục câu trả lời | LLM tự chọn · intent quyết định | intent quyết định — điều kiện để nhất quán và kiểm thử được |
| UI\-2 | Số không truy được nguồn | Hiện kèm cảnh báo · không hiện | Không hiện \(P8\); hiện cảnh báo là mời người dùng tin một số không kiểm chứng được |
| UI\-3 | Vintage mặc định trên giao diện | latest\_published · official · theo sở thích người dùng | latest\_published, cho phép đổi mặc định theo người dùng — chốt cùng quyết định 9\.2 của README |
| UI\-4 | Nav: “Tri thức” là module ngang hàng hay một khu của Data Studio | giữ ngang hàng · gộp vào Dữ liệu | Gộp vào “Dữ liệu”; tri thức và số liệu dùng chung source registry |
| UI\-5 | Dữ liệu cá nhân người dùng tải lên | không cho · cho, có nhãn · cho, vào observation store | Cho, có nhãn “chưa duyệt”, không vào observation store \(F6\) |
| UI\-6 | Ngôn ngữ | chỉ tiếng Việt · vi \+ en | vi mặc định \+ en; thuật ngữ miền có bảng thống nhất |
| UI\-7 | Router | giữ cơ chế pathname hiện tại \+ tham số · chuyển react\-router | Giữ cơ chế hiện tại, thêm lớp urlState; chuyển router là thay đổi riêng, không ghép vào tính năng |
| UI\-8 | Dashboard vận hành nền tảng \(ExecutiveDashboard hiện tại\) | thay bằng dashboard ANKT · giữ cả hai | Giữ cả hai: ANKT ở “Tổng quan”, vận hành ở “Vận hành” — hai đối tượng người dùng khác nhau |

