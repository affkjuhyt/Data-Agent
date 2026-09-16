# POC\-PLAN

# Kế hoạch POC ANKT — dựng khung chạy được, test bằng chỉ số Nợ công/GDP

> Phiên bản 15/09/2026 · Đối tượng: quản lý dự án, nhóm kỹ thuật, nhóm Data Research, người nghe báo cáo\.<br>Thời gian: **16/09 → 23/10/2026** \(tuần chuẩn bị \+ 3 sprint\)\. Gantt: [14\\\-poc\\\-gantt](diagrams/14-poc-gantt.puml)\. Kiến trúc POC: [13\\\-poc\\\-architecture](diagrams/13-poc-architecture.puml)\.<br>Nguồn dữ liệu: [DATA\\\-SOURCES\\\.md](DATA-SOURCES.md) · bản máy đọc [data/sources\\\.yaml](data/sources.yaml)\. Kiến trúc đích: [PLATFORM\\\-ARCHITECTURE\\\.md](PLATFORM-ARCHITECTURE.md)\.

## 1\. Mục tiêu

Chứng minh bằng một hệ thống **chạy thật** \(không phải slide\) rằng kiến trúc đa chỉ số khả thi:

1. **Nạp được mọi dạng dữ liệu** vào cùng một mô hình: API/SDMX, PDF có bảng, PDF scan, DOCX, XLSX/XLS, XML, HTML, RSS, văn bản pháp luật\.
2. **Trả lời đúng, có kiểm chứng** câu hỏi về Nợ công/GDP: con số \+ kỳ \+ vintage \+ trạng thái ngưỡng \+ trích dẫn tới trang tài liệu\.
3. **Thêm chỉ số bằng cấu hình**: 6 chỉ tiêu nhóm A khai báo bằng YAML, không sửa code agent\.
4. **Đo được**: điểm eval có dung sai, độ trễ, thông lượng nạp dữ liệu dưới tải\.

### Trong phạm vi

- Chỉ số test đầy đủ dữ liệu: **Nợ công/GDP \(A01\)**\. A02–A06 khai báo trong registry, có dữ liệu ở mức sẵn có\.
- Nguồn Tier S \+ một phần Tier M \(xem mục 5\)\.
- Câu hỏi mức L1–L3 \(tra cứu, so ngưỡng, giải thích biến động\) \+ L4 đối chiếu IMF/WB có cảnh báo phạm vi \+ câu phải từ chối\.
- Fast path single\-agent; một MCP server mẫu \(`indicator\-mcp`\)\.

### Ngoài phạm vi POC

Multi\-agent đầy đủ, dự báo/DSA/EWS, alert engine, Dagster, ClickHouse cho observation \(POC dùng Postgres\), Data Studio đầy đủ, RBAC theo phân loại, tầng tải L \(100 GB\+, cần thêm ổ đĩa\)\.

## 2\. Kịch bản demo báo cáo \(15 phút\)

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683923126055&cot=14)*

| \# | Cảnh | Người xem thấy gì | Chứng minh |
| --- | --- | --- | --- |
| 1 | Trang Nguồn dữ liệu | ~20 nguồn, 10 định dạng, lần chạy gần nhất, số bản ghi, trạng thái | Đấu nối đa dạng dữ liệu |
| 2 | Nạp bản tin nợ công PDF | Upload → bảng trích xuất cạnh ảnh trang \(khung bbox\) → sửa 1 ô → duyệt → observation mới gắn vintage | Trích xuất có toạ độ \+ người duyệt |
| 3 | Trang chỉ số Nợ công/GDP | Định nghĩa, công thức, chuỗi năm, dải ngưỡng theo giai đoạn 2021–2025 / 2026–2030, bảng quan sát kèm nguồn | Registry \+ ngưỡng theo kỳ |
| 4 | Hỏi tra cứu “Nợ công/GDP năm 2024 bao nhiêu, so với ngưỡng thế nào?” | Chữ đầu tiên ~1–2 s, số \+ vintage \+ trạng thái ngưỡng, bấm trích dẫn mở đúng trang PDF | Đúng số \+ truy nguồn |
| 5 | Hỏi giải thích “Vì sao tỷ lệ giảm giai đoạn 2017–2020?” | Phân rã: phần do dư nợ, phần do GDP tăng/đánh giá lại | Tính toán tất định, không để LLM đoán |
| 6 | Hỏi đối chiếu “IMF ghi bao nhiêu, vì sao khác MOF?” | IMF ≈ 34% vs MOF ≈ 36–37% năm 2024, giải thích phạm vi general government vs nợ công theo luật | Đa nguồn \+ hiểu phạm vi |
| 7 | Hỏi bẫy “Nợ công/GDP quý 2/2026?” | Từ chối có lý do: chỉ tiêu chỉ công bố theo năm, kỳ chưa có số | Không bịa số |
| 8 | Cùng tool qua MCP | Gọi query\_indicator từ MCP Inspector ra đúng kết quả như trong chat | API\-first, tái dùng |
| 9 | Kết quả eval \+ tải | Bảng điểm golden set \(đúng số/nguồn/vintage/ngưỡng\), p95 độ trễ ở 20–50 CCU, thông lượng nạp | Đo được, không cảm tính |

## 3\. Kiến trúc POC

Xây trên hạ tầng Data\-Agent đang chạy \(k3s, workflow\-runtime, kb\-backend, Postgres, MinIO, Qdrant, Kafka, Langfuse, vLLM\)\. Phần mới gói gọn:

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683923126060&cot=14)*

| Thành phần | Cách làm POC | Nâng cấp sau POC |
| --- | --- | --- |
| Registry \+ observation | Schema Postgres ankt: indicator, indicator\_version, variable, formula\_input, threshold, source\_document, release, observation \(theo sơ đồ 05\) | Observation sang ClickHouse |
| Connector | Package Python ankt\-connectors, chạy bằng K8s CronJob, đọc sources\.yaml | Dagster |
| Trích xuất PDF | Docling hiện có \+ lưu page\_no/bbox \+ OCR baseline | OCR/VLM service theo benchmark |
| Duyệt trích xuất | Màn hình đơn giản: ảnh trang \+ bảng sửa được \+ nút duyệt | Workbench Data Studio |
| Tính toán | Module Python trong workflow\-runtime: ratio, căn kỳ, làm tròn, ngưỡng theo kỳ, phân rã biến động | Computation engine riêng \+ IQL |
| Agent | Tool nội bộ \+ route câu hỏi chỉ số vào fast path; evidence frame thêm vintage/nguồn/trang; verifier kiểm số | Supervisor \+ sub\-agent |
| MCP | indicator\-mcp \(SDK MCP Python, Streamable HTTP\) bọc cùng các tool | Bộ 7 MCP server |
| Eval | Case schema v2 \+ scorer dung sai trong model/eval, đẩy kết quả lên Langfuse | Sinh case từ observation |

## 4\. Gói công việc

Vai trò: **BE** backend · **DE** data engineer · **FE** frontend · **RS** Data Research · **QA** kiểm thử/eval · **OPS** hạ tầng\.

### Tuần 0 — Chuẩn bị \(16/09–18/09\)

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683923126083&cot=14)*

| ID | Việc | Đầu ra | Vai trò | Xong khi |
| --- | --- | --- | --- | --- |
| P0 | Chốt phạm vi, kịch bản demo, tiêu chí nghiệm thu | Tài liệu này được duyệt | PM, RS | Sếp/PM đồng ý 9 cảnh demo |
| P1 | Reboot sửa lỗi driver NVML; dọn ổ đĩa \(đang 81%\); kiểm tra ~50 GB trống cho Tier S\+M | Server sẵn sàng | OPS | nvidia\-smi chạy; ≥ 50 GB trống |
| P2 | Tải gói Tier S về MinIO bucket ankt\-sources, ghi SHA\-256; chốt sources\.yaml | Bộ file gốc \+ manifest | DE | Đủ file Tier S, manifest khớp hash |

### Sprint 1 — Dữ liệu nền \(21/09–02/10\)

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683923126100&cot=14)*

| ID | Việc | Đầu ra | Vai trò | Xong khi |
| --- | --- | --- | --- | --- |
| S1A | Migration schema ankt; loader YAML chỉ số từ ECO\-001\.5 \(A01–A06, ngưỡng 2021–2025 và 2026–2030\) | Bảng \+ seed | BE, RS | 6 chỉ số, ngưỡng có valid\_from/valid\_to \+ văn bản căn cứ |
| S1B | Connector API/SDMX: IMF WEO/GDD/IL, WB WDI/IDS/JEDH, BIS XRU/DEBT\_SEC | Observation quốc tế có perimeter | DE | Chạy lại không trùng; rate limit/retry hoạt động |
| S1C | Connector file NSO: báo cáo tháng DOCX\+XLSX, XNK \.xls, niên giám PDF | Observation GDP, thu NSNN, XK | DE | GDP giá hiện hành các năm có trong store |
| S1D | Docling: lưu trang/bbox cho chunk và bảng; bật OCR baseline; sinh 30 trang scan bằng Augraphy | Trích xuất có toạ độ | BE | Citation trả page; OCR đọc được trang scan |
| S1E | Connector XML/HTML/RSS: Vietcombank, SBV tỷ giá, baochinhphu | Luồng dữ liệu ngày/15 phút | DE | CronJob chạy đều 3 ngày liên tục |
| S1F | Golden set v1 \(40–60 case\) theo 6 nhóm: L1, L2, L3, L4 \(phạm vi\), bẫy vintage, phải từ chối; scorer dung sai \+ nguồn \+ vintage \+ ngưỡng | model/eval/cases/public\_debt\.jsonl \+ scorer | RS, QA, BE | Mỗi case có tài liệu nguồn \(tên/số/ngày/trang\) và dung sai |

### Sprint 2 — Agent \+ giao diện \(05/10–16/10\)

S2A và S2B khởi động ngay khi S1D/S1A xong \(khoảng 25–28/09\), gối đầu cuối Sprint 1, để dành thời gian cho S2C\.

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683923126122&cot=14)*

| ID | Việc | Đầu ra | Vai trò | Xong khi |
| --- | --- | --- | --- | --- |
| S2A | Bản tin nợ công PDF \(số 15–17\) → bảng trích xuất → màn duyệt → observation có source\_ref | Chuỗi Nợ công/GDP chính thức | BE, FE, RS | Số khớp bản PDF, sửa được và có lịch sử |
| S2B | Module tính: ratio, căn kỳ stock/flow, làm tròn theo decimal\_precision, chọn ngưỡng theo kỳ, phân rã biến động; tool search\_indicators, get\_indicator, query\_indicator, compare\_threshold, decompose\_change, get\_source | Tool nội bộ | BE | Unit test: tái tính khớp số MOF trong dung sai |
| S2C | Route câu hỏi chỉ số → fast path; evidence frame thêm indicator\_id, vintage, doc\_id, page, threshold\_status; verifier số trong câu trả lời ⊆ evidence ± dung sai | Luồng hỏi đáp | BE | Cảnh 4–7 chạy được |
| S2D | indicator\-mcp bọc các tool S2B | MCP server | BE | Gọi được từ MCP Inspector |
| S2E | UI: trang chỉ số \(chuỗi \+ dải ngưỡng \+ bảng quan sát \+ nguồn\), thẻ chỉ số trong Ask, mở PDF \#page=N, trang Nguồn dữ liệu | 3 màn hình | FE | Cảnh 1, 3, 4 chạy được |
| S2F | Nạp KB pháp lý: VBHN 59/2026, NQ 26/2026 \(upload có metadata\), corpus vbpl 4,37 GB \(Tier M\) | KB pháp lý | DE | Tra được căn cứ ngưỡng kèm trang |

### Sprint 3 — Đo và báo cáo \(19/10–23/10\)

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683923126135&cot=14)*

| ID | Việc | Đầu ra | Vai trò | Xong khi |
| --- | --- | --- | --- | --- |
| S3A | Chạy golden set, sửa lỗi, chạy lại | Báo cáo eval | QA, BE | Đạt tiêu chí mục 6 |
| S3B | Load test: k6 hỏi đáp \(tái dùng tests/loadtest/ccu\-test\.js, agent\-e2e\.js\) ở 10/20/50 CCU; ingestion\_throughput\.sh với Tier M | Báo cáo tải | QA, OPS | Có số p50/p95, lỗi, thông lượng |
| S3C | Benchmark OCR nhỏ: Docling không OCR vs OCR baseline vs 1 VLM ứng viên trên 30 trang \(text \+ scan Augraphy\) | Bảng CER/độ đúng token số | BE | Có số liệu để chọn model giai đoạn sau |
| S3D | Kịch bản demo, slide, video dự phòng | Bộ báo cáo | PM, RS | Tổng duyệt 1 lần trơn tru |

## 5\. Kế hoạch dữ liệu

### 5\.1 Checklist độ phủ định dạng \(phải đạt trong POC\)

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683923126213&cot=14)*

| Định dạng | Nguồn dùng trong POC | Sprint |
| --- | --- | --- |
| SDMX\-CSV / SDMX\-JSON | IMF WEO, GDD, IL; BIS | S1B |
| JSON REST | World Bank WDI, IDS, JEDH | S1B |
| XLSX | NSO báo cáo tháng | S1C |
| XLS \(cũ\) | NSO XNK theo tháng | S1C |
| DOCX | NSO báo cáo tháng \(lời văn\) | S1C |
| PDF có bảng | Bản tin nợ công 15–17, niên giám NSO 2025, ADB/AMRO/IMF | S1C, S2A |
| PDF scan | Niên giám cũ SEADELT \+ 30 trang Augraphy | S1D |
| XML | Tỷ giá Vietcombank | S1E |
| HTML | SBV tỷ giá, cán cân thanh toán | S1E |
| RSS | baochinhphu\.vn kinh tế | S1E |
| Văn bản pháp luật | VBHN 59/2026 \(PDF\), NQ 26/2026 \(upload\), corpus vbpl \(Parquet\) | S2F |

### 5\.2 Dữ liệu cho câu hỏi test

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683923126228&cot=14)*

| Biến | Nguồn chính | Nguồn đối chiếu |
| --- | --- | --- |
| Dư nợ công, nợ CP, nợ nước ngoài quốc gia | Bản tin nợ công MOF | IMF GDD/WEO \(phạm vi khác\), WB IDS |
| GDP giá hiện hành | NSO \(niên giám, báo cáo tháng\) | WB WDI NY\.GDP\.MKTP\.CN, IMF WEO |
| Thu NSNN | NSO báo cáo tháng, MOF công khai NSNN | — |
| Kim ngạch xuất khẩu | NSO XNK | UN Comtrade |
| Nghĩa vụ trả nợ nước ngoài | Bản tin nợ công | WB IDS DT\.TDS\.DECT\.CD |
| Ngưỡng | NQ 23/2021/QH15, NQ 26/2026/QH16 | — |

### 5\.3 Rủi ro nguồn và cách xử lý

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683923126241&cot=14)*

| Rủi ro | Xử lý trong POC |
| --- | --- |
| Trang MOF lỗi DNS / SPA | Dùng bản mirror \+ upload thủ công có metadata; đề nghị nhận file chính thức |
| NQ 26/2026 chưa có bản toàn văn chính thức online | Upload bản do nhóm research cung cấp, ghi rõ nguồn |
| vbpl\.vn chặn crawler | Dùng corpus Hugging Face \(CC BY 4\.0\) |
| IMF DataMapper chặn curl | Dùng SDMX 3\.0 |
| Không có PDF scan thật đã xác minh | Sinh bằng Augraphy từ PDF có text \(có sẵn ground truth\) |
| Ổ đĩa ~31 GB trống | Chỉ Tier S \+ M có chọn lọc; Tier L chờ bổ sung ≥ 300 GB |

## 6\. Tiêu chí nghiệm thu POC

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683923126250&cot=14)*

| Nhóm | Chỉ số | Mục tiêu |
| --- | --- | --- |
| Dữ liệu | Số định dạng nạp thành công | ≥ 10 |
| Dữ liệu | Số nguồn có connector chạy tự động | ≥ 12 |
| Dữ liệu | Observation Nợ công/GDP có source\_ref tới trang | 100% |
| Eval | value\_correct \(trong dung sai, mục 6\.3 README\) | ≥ 0,95 |
| Eval | source\_correct \(đúng tài liệu \+ trang\) | ≥ 0,95 |
| Eval | threshold\_status\_correct | 1,0 |
| Eval | refusal\_correct \(câu phải từ chối\) | ≥ 0,9 |
| Eval | unsupported\_number\_rate | 0 |
| Hiệu năng | TTFT câu tra cứu \(1 người dùng\) | ≤ 2 s |
| Hiệu năng | p95 thời gian trả lời xong ở 20 CCU | ≤ 8 s \(đo và báo cáo cả 50 CCU\) |
| Hiệu năng | Thông lượng nạp PDF có OCR | Đo và báo cáo \(trang/phút\) |
| Mở rộng | Thêm 1 chỉ số tỷ lệ mới \(A02\) chỉ bằng YAML \+ dữ liệu | Trả lời được L1–L2, không sửa code |

## 7\. Kế hoạch test tải

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683923126261&cot=14)*

| Kịch bản | Công cụ | Dữ liệu | Đo |
| --- | --- | --- | --- |
| Hỏi đáp đồng thời | k6 \(tests/loadtest/ccu\-test\.js, agent\-e2e\.js\) | Câu hỏi golden set \+ biến thể | TTFT, p50/p95, tỉ lệ lỗi, tok/s theo Langfuse |
| Nạp PDF \+ OCR | tests/loadtest/ingestion\_throughput\.sh | Niên giám \+ 1\.000 trang Augraphy | Trang/phút, GPU/CPU, hàng đợi Kafka |
| Nạp KB pháp lý lớn | Pipeline KB hiện có | vbpl 171\.556 văn bản \(4,37 GB\) | Văn bản/phút, thời gian embedding, dung lượng Qdrant |
| Nạp chuỗi thời gian bulk | Connector WDI bulk / IMF WEO toàn cầu | ~1\.600 chỉ số × 217 nền kinh tế | Dòng/giây, thời gian truy vấn chỉ số |
| Luồng liên tục | CronJob RSS 15 phút \+ tỷ giá | baochinhphu, Vietcombank | Độ trễ nạp, trùng lặp |

Lưu ý GPU: một A100 dùng chung cho chat và embedding; chạy test nạp dữ liệu nặng ngoài giờ demo để không làm sai số độ trễ hỏi đáp\.

## 8\. Nguồn lực

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683923126272&cot=14)*

| Vai trò | Số người \(đề xuất\) | Trọng tâm |
| --- | --- | --- |
| Backend | 2 | Schema, tool, route, verifier, MCP, Docling |
| Data engineer | 1 | Connector, CronJob, Tier S/M |
| Frontend | 1 | Trang chỉ số, thẻ chỉ số, trang nguồn, màn duyệt |
| Data Research | 2 | YAML chỉ số, golden set, đáp án chuẩn, kiểm tra số trích xuất |
| QA | 0,5 | Eval, load test |
| OPS | 0,5 | GPU, ổ đĩa, CronJob |

## 9\. Rủi ro dự án

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683923126279&cot=14)*

| Rủi ro | Ảnh hưởng | Giảm thiểu |
| --- | --- | --- |
| Driver GPU cần reboot | Dừng toàn bộ model trong lúc sửa | Làm ngay tuần 0, ngoài giờ |
| Trích bảng PDF sai chữ số | Sai đáp án chuẩn | Bắt buộc duyệt; so với số tổng/tỷ lệ in sẵn trong bản tin |
| Số MOF khác IMF/WB | Người nghe nghĩ hệ thống sai | Đưa chính chênh lệch này thành cảnh demo 6 |
| Ghi nhầm vintage GDP | Tỷ lệ sai mà trông hợp lý | Mọi observation bắt buộc có release; eval có case bẫy vintage |
| Dồn việc FE cuối sprint 2 | Thiếu màn hình demo | Làm wireframe tuần 0; trang Nguồn dữ liệu dùng lại bảng có sẵn |
| Chậm research final \(19–20/09\) | Trễ seed YAML | S1A dùng bản nháp ECO\-001\.5/ECO\-004, cập nhật khi final |

## 10\. Dàn ý báo cáo

1. Bài toán: vì sao Data Agent cho ANKT khác chatbot hỏi đáp \(số đúng, nguồn, vintage, ngưỡng theo kỳ\)\.
2. Kiến trúc đích đa chỉ số \(sơ đồ 07\) và phần đã dựng trong POC \(sơ đồ 13\)\.
3. Demo trực tiếp 9 cảnh\.
4. Kết quả đo: eval, độ trễ, thông lượng, OCR benchmark\.
5. Nguồn dữ liệu đã đấu nối và độ phủ định dạng\.
6. Bài học: chênh lệch phạm vi MOF–IMF, vintage GDP, giới hạn nguồn chính thức online\.
7. Đề xuất giai đoạn tiếp: 6 chỉ tiêu nhóm A đầy đủ → ~30 chỉ số → cảnh báo; nhu cầu hạ tầng \(GPU cho tài liệu, ≥ 300 GB lưu trữ\)\.
