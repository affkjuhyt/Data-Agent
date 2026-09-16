# DIAGRAM\-INDEX

# Mục lục và phân loại sơ đồ kiến trúc — Data Agent ANKT

> Phiên bản 16/09/2026 · Đối tượng: **lãnh đạo, chủ nhiệm đề tài, hội đồng nghiệm thu**, và nhóm kỹ thuật khi cần tra cứu\.<br>Mục đích: một chỗ duy nhất để biết **có những sơ đồ nào, mỗi sơ đồ trả lời câu hỏi gì, và sơ đồ đó nói về hiện trạng hay mục tiêu**\.<br>Tổng: **53 sơ đồ**, mỗi sơ đồ có bản `\.puml` \(nguồn\) và `\.svg` \(render sẵn\) trong `[diagrams/](diagrams/)`\.

## 1\. Cách đọc bộ sơ đồ này

Một sơ đồ được định vị bằng **bốn thuộc tính**\. Khi báo cáo, luôn đọc đủ bốn — đặc biệt là cột **Tính chất**, vì lẫn “mục tiêu” thành “hiện trạng” là hiểu sai nghiêm trọng nhất\.

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683922632390&cot=14)*

| Thuộc tính | Các giá trị |
| --- | --- |
| Cấp độ \(mức trừu tượng, theo tinh thần C4\) | L0 Bối cảnh — hệ thống và môi trường xung quanh · L1 Tổng thể — các lớp, service, môi trường · L2 Thành phần — bên trong một lớp/service/màn hình · L3 Chi tiết — mô hình dữ liệu, hợp đồng, tương tác cụ thể |
| Góc nhìn | Nghiệp vụ · Ứng dụng · Dữ liệu · Giao diện · Hạ tầng &amp; Vận hành · Kế hoạch |
| Loại sơ đồ | Bối cảnh · Ca sử dụng · Thành phần · Triển khai · Tuần tự · Hoạt động · Máy trạng thái · Lớp/ERD · Sơ đồ tư duy · Gantt |
| Tính chất | HT hiện trạng \(as\-is\) · MT mục tiêu \(to\-be\) · HH hỗn hợp — vẽ mục tiêu nhưng tô màu theo mức đã có · KH kế hoạch |

**Quy ước màu thống nhất** \(mục 4\) cho phép đọc “thực trạng” ngay trên sơ đồ mục tiêu: xanh lá = đã có · xanh dương = có một phần · vàng = chưa có\.

## 2\. Bảng phân loại đầy đủ

### 2\.1 Nhóm A — Bối cảnh và tổng quan *\(đọc trước tiên\)*

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683922632397&cot=14)*

| \# | Sơ đồ | Cấp | Loại | Tính chất | Trả lời câu hỏi |
| --- | --- | --- | --- | --- | --- |
| 45 | Sơ đồ bối cảnh hệ thống | L0 | Bối cảnh | HH | Hệ thống nằm ở đâu? Ai dùng? Lấy dữ liệu từ những tổ chức nào? Ranh giới tới đâu? |
| 46 | Ca sử dụng theo vai trò | L0 | Ca sử dụng | HH | 6 vai trò làm được những gì? Bao nhiêu phần đã có? |
| 36 | Bản đồ trạng thái năng lực | L1 | Thành phần | HT | Toàn nền tảng: năng lực nào đã có, một phần, chưa có? |
| 01 | Kiến trúc hiện tại | L1 | Thành phần | HT | Hôm nay hệ thống gồm những gì, và thiếu gì? |
| 02 | Kiến trúc mục tiêu \(golden use case\) | L1 | Thành phần | MT | Để trả lời được bài toán Nợ công/GDP cần thêm gì? |
| 07 | Các lớp nền tảng đa chỉ số | L1 | Thành phần | MT | Kiến trúc tổng quát cho hàng trăm chỉ số gồm mấy lớp? |

### 2\.2 Nhóm B — Nghiệp vụ và miền *\(hội đồng nghiệp vụ đọc\)*

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683922632416&cot=14)*

| \# | Sơ đồ | Cấp | Loại | Tính chất | Trả lời câu hỏi |
| --- | --- | --- | --- | --- | --- |
| 19 | Vòng đời một chỉ số | L2 | Hoạt động | MT | Một chỉ số được định nghĩa, duyệt, phát hành, sửa đổi thế nào? |
| 20 | Kỳ công bố số liệu | L2 | Hoạt động | MT | Một bản tin mới của Bộ Tài chính đi vào hệ thống qua những bước nào? |
| 21 | Xử lý một cảnh báo | L2 | Hoạt động | MT | Cảnh báo chạm ngưỡng được xử lý và báo cáo ra sao? |
| 47 | Vòng đời 4 đối tượng nghiệp vụ | L3 | Máy trạng thái | MT | Chỉ số · kỳ công bố · nhiệm vụ duyệt · cảnh báo có những trạng thái nào, chuyển khi nào? |
| 03 | Luồng dữ liệu một chỉ số | L2 | Hoạt động | HH | Từ tài liệu nguồn đến đáp án chuẩn đi qua đâu? |
| 18 | Luồng dữ liệu đầu–cuối | L2 | Hoạt động | MT | Nguồn → quan sát → chỉ số → cảnh báo/eval → câu trả lời |
| 05 | Mô hình dữ liệu lõi | L3 | Lớp/ERD | MT | Indicator · Source · Observation · Golden case quan hệ thế nào? |
| 12 | Mô hình dữ liệu tổng quát | L3 | Lớp/ERD | MT | Mô hình mở rộng cho vô số chỉ số \(theo SDMX\) |

### 2\.3 Nhóm C — Ứng dụng và service *\(kỹ thuật đọc\)*

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683922632432&cot=14)*

| \# | Sơ đồ | Cấp | Loại | Tính chất | Trả lời câu hỏi |
| --- | --- | --- | --- | --- | --- |
| 15 | Bản đồ service mục tiêu | L1 | Thành phần | HH | 17 service hiện có \+ 14 service mới nằm ở đâu? |
| 16 | Kết nối giữa service | L2 | Thành phần | HH | REST · Kafka · MCP · SSE nối những gì với những gì? |
| 17 | Database theo service | L2 | Thành phần | HH | Service nào sở hữu schema nào, ở datastore nào? |
| 08 | Lớp dữ liệu đầu vào | L2 | Thành phần | MT | Ingestion plane gồm service nào, nối bằng sự kiện gì? |
| 09 | Agent core | L2 | Thành phần | MT | Sub\-agent chuyên môn, MCP, Platform API tổ chức ra sao? |
| 04 | Tuần tự: trả lời câu hỏi chỉ số | L3 | Tuần tự | HH | Một câu hỏi đi qua service nào, chọn ngưỡng lúc nào? |
| 10 | Tuần tự: câu hỏi đa bước | L3 | Tuần tự | MT | Supervisor điều phối nhiều sub\-agent thế nào? |
| 24 | Tuần tự: mở trang chỉ số | L3 | Tuần tự | HH | 4 request song song và luồng hỏi trong ngữ cảnh |
| 48 | Tuần tự: xác thực, phân quyền, ghi vết | L3 | Tuần tự | HH | Ai được xem con số nào, và để lại vết gì? |

### 2\.4 Nhóm D — Giao diện *\(sản phẩm và frontend đọc\)*

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683922632438&cot=14)*

| \# | Sơ đồ | Cấp | Loại | Tính chất | Trả lời câu hỏi |
| --- | --- | --- | --- | --- | --- |
| 37 | Bản đồ điều hướng giao diện | L1 | Sơ đồ tư duy | HH | Ứng dụng có những khu nào, vai trò nào dùng, đã có bao nhiêu? |
| 11 | Chức năng Data Studio | L2 | Sơ đồ tư duy | HH | 9 khu chức năng dữ liệu gồm gì? |
| 40 | Kiến trúc frontend | L2 | Thành phần | HH | Module, primitive dùng chung, lớp API client |
| 38 | Hợp đồng render câu trả lời | L3 | Thành phần | MT | 9 intent ánh xạ vào 10 khối hiển thị thế nào? |
| 22 | Luồng giao diện: lãnh đạo | L2 | Hoạt động | HH | Lãnh đạo đi từ dashboard tới quyết định ra sao? |
| 23 | Luồng giao diện: chuyên viên | L2 | Hoạt động | HH | Chuyên viên phân tích một chỉ số ra sao? |
| 39 | Luồng giao diện: data steward | L2 | Hoạt động | MT | Steward duyệt một kỳ công bố ra sao? |
| 41 | Ask Workspace: ba bề mặt | L2 | Thành phần | HH | Màn hỏi đáp gồm những vùng nào? |
| 42 | Thang điều khiển trên một block | L3 | Máy trạng thái | MT | 5 tầng thao tác: lời nói → chip → kéo thả → form → SQL |
| 43 | Tuần tự: lắp ngữ cảnh | L3 | Tuần tự | HH | Người dùng gắn nguồn/tệp, agent đọc đúng chừng đó |
| 44 | Tuần tự: thao tác trên block | L3 | Tuần tự | MT | Khi nào chỉnh hình tại chỗ, khi nào chạy lại |

### 2\.5 Nhóm E — Hạ tầng, vận hành, bảo mật *\(DevOps và quản trị đọc\)*

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683922632443&cot=14)*

| \# | Sơ đồ | Cấp | Loại | Tính chất | Trả lời câu hỏi |
| --- | --- | --- | --- | --- | --- |
| 28 | Hạ tầng | L1 | Triển khai | HT\+MT | Chạy trên máy gì, mục tiêu HA ra sao? |
| 27 | Môi trường và promote | L2 | Hoạt động | HH | dev → staging → prod đi thế nào? |
| 25 | CI/CD hiện tại | L2 | Hoạt động | HT | Hôm nay build và triển khai bằng gì? |
| 26 | GitOps mục tiêu | L2 | Thành phần | MT | Argo CD, ký image, repo cấu hình |
| 29 | Observability | L2 | Thành phần | HH | Metrics · logs · traces · LLM · dữ liệu |
| 30 | Cảnh báo vận hành &amp; sự cố | L2 | Hoạt động | HH | Sự cố hạ tầng được phát hiện và xử lý ra sao? |
| 33 | DataOps | L2 | Thành phần | HH | Giám sát sức khoẻ dữ liệu và pipeline |
| 34 | Backup &amp; DR | L2 | Thành phần | HH | Từng datastore sao lưu và phục hồi thế nào? |
| 35 | Bảo mật chuỗi cung ứng | L2 | Hoạt động | HH | Cổng kiểm soát từ commit tới cluster |
| 51 | Mạng và giao thức | L2 | Thành phần | HH | Ai nói với ai, giao thức gì, xác thực gì, hở ở đâu? |
| 52 | Kafka: cấu hình và bảo đảm | L3 | Thành phần | HH | Broker, topic, producer, consumer — bảo đảm nào đạt được, cái nào chỉ là danh nghĩa? |
| 31 | Vòng đời MLOps/LLMOps | L2 | Hoạt động | HH | Đổi model/prompt/routing đi qua bước nào? |
| 32 | Kiến trúc MLOps | L2 | Thành phần | HH | Model registry, canary, GPU scheduling |

### 2\.6 Nhóm F — Kỹ thuật *\(kỹ sư hiện thực đọc\)*

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683922632453&cot=14)*

| \# | Sơ đồ | Cấp | Loại | Tính chất | Trả lời câu hỏi |
| --- | --- | --- | --- | --- | --- |
| 49 | Bản đồ kỹ thuật 12 tầng | L2 | Thành phần | HH | Kỹ thuật nào đã có, cái nào đang tắt bằng cờ, cái nào chưa có? |
| 50 | Đường đi của một con số | L3 | Hoạt động | HH | Từ trang PDF tới con số hiển thị đi qua kỹ thuật nào, mắt xích nào còn vàng? |

### 2\.7 Nhóm G — Kế hoạch *\(quản lý dự án đọc\)*

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683922632462&cot=14)*

| \# | Sơ đồ | Cấp | Loại | Tính chất | Trả lời câu hỏi |
| --- | --- | --- | --- | --- | --- |
| 06 | Gantt lộ trình nền tảng | — | Gantt | KH | GĐ0–GĐ3 từ 21/09/2026 |
| 14 | Gantt POC | — | Gantt | KH | POC 16/09 → 23/10/2026 |
| 13 | Kiến trúc POC | L1 | Thành phần | KH | Phạm vi tối thiểu 5 tuần trên hạ tầng hiện có |
| 53 | Bàn kiểm thử đường warehouse | L2 | Thành phần | HH | Nguồn database/warehouse nào chứng minh thành phần nào? |

## 3\. Ma trận độ phủ

### 3\.1 Cấp độ × Góc nhìn

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683922632467&cot=14)*

| Column 1 | Nghiệp vụ | Ứng dụng | Dữ liệu | Giao diện | Hạ tầng &amp; Vận hành | Kế hoạch |
| --- | --- | --- | --- | --- | --- | --- |
| L0 Bối cảnh | 45, 46 | 45 | 45 | 46 | — | — |
| L1 Tổng thể | 36 | 01, 02, 07, 15, 36 | 08 | 37, 11 | 28, 13 | 06, 14, 13 |
| L2 Thành phần | 19, 20, 21, 03, 18 | 09, 16, 17, 49 | 08, 17, 33, 53 | 40, 22, 23, 39, 41 | 25, 26, 27, 29, 30, 31, 32, 34, 35, 51 | — |
| L3 Chi tiết | 47 | 04, 10, 24, 48 | 05, 12, 50 | 38, 42, 43, 44 | 52 | — |

**Đọc ma trận này**: không ô nào trống ở những chỗ quan trọng\. Ô trống hợp lý duy nhất còn lại là L0 về hạ tầng — không cần\.

### 3\.2 Theo loại sơ đồ

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683922632471&cot=14)*

| Loại | Số lượng | Sơ đồ |
| --- | --- | --- |
| Thành phần \(component / container\) | 22 | 01, 02, 07, 08, 09, 13, 15, 16, 17, 26, 29, 32, 33, 34, 36, 38, 40, 41, 49, 51, 52, 53 |
| Hoạt động \(activity, phần lớn có làn trách nhiệm\) | 14 | 03, 18, 19, 20, 21, 22, 23, 25, 27, 30, 31, 35, 39, 50 |
| Tuần tự \(sequence\) | 6 | 04, 10, 24, 43, 44, 48 |
| Lớp / ERD | 2 | 05, 12 |
| Máy trạng thái | 2 | 42, 47 |
| Sơ đồ tư duy \(mindmap\) | 2 | 11, 37 |
| Gantt | 2 | 06, 14 |
| Bối cảnh \(C4 mức 1\) | 1 | 45 |
| Ca sử dụng | 1 | 46 |
| Triển khai | 1 | 28 |
| Tổng | 53 |  |

Trong 14 sơ đồ hoạt động, **12 có làn trách nhiệm** \(swimlane\) nên đọc được ai làm bước nào; 22 và 23 không có làn vì chỉ mô tả một vai trò\.

### 3\.3 Theo tính chất — **phần quan trọng nhất khi báo cáo**

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683922632477&cot=14)*

| Tính chất | Số lượng | Ý nghĩa khi đọc báo cáo |
| --- | --- | --- |
| HT — hiện trạng | 3 \(01, 25, 36\) | Đây là những gì đang thật sự chạy\. Sơ đồ 36 là bức tranh trung thực nhất về mức độ hoàn thành |
| HH — hỗn hợp \(vẽ mục tiêu, tô màu theo mức đã có\) | 29 | Đọc được cả hai: hình dạng tương lai và phần đã làm được\. Phải nhìn màu, không nhìn hình |
| MT — mục tiêu | 17 | Chưa tồn tại\. Là hợp đồng thiết kế, không phải mô tả hệ thống |
| KH — kế hoạch | 3 \(06, 13, 14\) | Mốc thời gian, cần đối chiếu tiến độ thực tế |
| HT\+MT — vẽ cả hai cạnh nhau | 1 \(28\) | Hạ tầng hiện tại và mục tiêu trong cùng một hình |

> **Cảnh báo khi trình bày**: 17/53 sơ đồ mô tả thứ **chưa tồn tại**\. Nếu chiếu sơ đồ 02, 07, 09 hay 18 mà không nói rõ đó là mục tiêu, người nghe sẽ hiểu hệ thống đã có ngần ấy\. Mọi slide dùng sơ đồ MT nên có nhãn “thiết kế mục tiêu” ở tiêu đề\.

## 4\. Quy ước trình bày

### 4\.1 Thang màu chuẩn \(dùng ở 45/53 sơ đồ\)

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683922632480&cot=14)*

| Màu | Mã | Nghĩa |
| --- | --- | --- |
| Xanh lá | \#EEF7EE / viền \#4E8A4E | Đã có, dùng được ngay |
| Xanh dương | \#E6F0FF / viền \#2F6FB3 | Có một phần, cần mở rộng |
| Vàng | \#FFF4D6 / viền \#C08A00 | Chưa có, phải xây mới |

Một số sơ đồ kiến trúc dùng nhãn tương đương **reuse / extend / new**\.

### 4\.2 Ngoại lệ đã rà soát và xử lý

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683922632486&cot=14)*

| Sơ đồ | Ngoại lệ | Xử lý |
| --- | --- | --- |
| 01 | Thang hai màu \(trắng = đã có, đỏ nhạt = khoảng trống\) vì là sơ đồ hiện trạng | Đã bổ sung chú giải giải thích rõ, và ghi chú rằng các sơ đồ khác dùng thang ba màu |
| 17 | Dùng đúng thang ba màu nhưng thiếu chú giải | Đã bổ sung chú giải |
| 16 | Chú giải giải thích kiểu nét vẽ \(liền = REST, đứt = Kafka\), không phải màu | Giữ nguyên — đây là trục thông tin khác |
| 19, 20, 21 | Chú giải bằng chữ \(“toàn bộ quy trình chưa có”\) thay cho bảng màu | Giữ nguyên — cả sơ đồ cùng một trạng thái nên bảng màu thừa |
| 06, 14 | Gantt, không có trạng thái để tô | Không áp dụng |
| 11, 37 | Sơ đồ tư duy dùng caption thay legend \(giới hạn của PlantUML\) | Giữ nguyên, nội dung chú giải tương đương |

### 4\.3 Render

```javascript
make diagrams     # docker plantuml/plantuml:1.2024.8, ghim phiên bản
```

**Phải ghim 1\.2024\.8\.** Bản `latest` \(1\.2026\.8\) coi cú pháp tô màu action `\#màu:\.\.\.;` là lỗi thời — báo lỗi ở các sơ đồ hoạt động \(03, 18, 23, 25, 27, 30\) và vẽ sai ở 35\. Chi tiết trong [README](README.md) mục 10\.

Kiểm tra sau khi render — PlantUML vẽ lỗi thành ảnh chứ không dừng, nên phải soi:

```javascript
cd docs/ankt/diagrams && for f in *.svg; do grep -qo '\[From .*puml (line' "$f" && echo "LỖI: $f"; done
```

Tình trạng 16/09/2026: **53/53 render sạch**\.

## 5\. Lộ trình đọc theo đối tượng

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683922632491&cot=14)*

| Đối tượng | Thứ tự đọc | Thời lượng |
| --- | --- | --- |
| Lãnh đạo / hội đồng \(bức tranh 15 phút\) | 45 bối cảnh → 36 trạng thái năng lực → 46 ca sử dụng → 06 lộ trình | 4 sơ đồ |
| Chủ nhiệm nghiệp vụ | 45 → 46 → 19, 20, 21 ba luồng nghiệp vụ → 47 vòng đời đối tượng → 22, 23, 39 luồng người dùng | 9 sơ đồ |
| Kiến trúc sư | 01 hiện trạng → 02, 07 mục tiêu → 15, 16, 17 service &amp; dữ liệu → 05, 12 mô hình → 04, 10, 24, 48 tuần tự | 12 sơ đồ |
| Frontend / sản phẩm | 37 IA → 38 hợp đồng render → 40 kiến trúc → 41, 42, 43, 44 Ask Workspace → 11 Data Studio | 8 sơ đồ |
| DevOps / SRE | 28 hạ tầng → 51 mạng &amp; giao thức → 52 Kafka → 25, 26, 27 CI/CD &amp; môi trường → 29, 30, 33 giám sát → 34 backup → 35, 48 bảo mật → 31, 32 MLOps \(đọc cùng INFRA\-DESIGN\.md\) | 13 sơ đồ |
| Kỹ sư hiện thực | 49 bản đồ kỹ thuật → 50 đường đi của một con số → 05, 12 mô hình dữ liệu → 48 bảo mật lúc chạy \(đọc cùng TECH\-DESIGN\.md\) | 5 sơ đồ |
| Quản lý dự án | 13, 14 POC → 53 bàn kiểm thử warehouse → 06 lộ trình → 36 trạng thái để đối chiếu tiến độ | 5 sơ đồ |

## 6\. Bốn sơ đồ bổ sung trong đợt rà soát này

Đợt rà soát 16/09/2026 phát hiện bốn khoảng trống về **loại sơ đồ** — không phải về nội dung, mà về góc nhìn chưa ai vẽ\. Đã bổ sung:

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683922632507&cot=14)*

| \# | Sơ đồ mới | Khoảng trống nó lấp | Vì sao cần cho báo cáo |
| --- | --- | --- | --- |
| 45 | Bối cảnh hệ thống \(C4 mức 1\) | Không có sơ đồ nào vẽ ranh giới hệ thống và các tổ chức bên ngoài\. 01 là sơ đồ bên trong, không phải bối cảnh | Đây là sơ đồ lãnh đạo hỏi đầu tiên\. Nó cũng cho thấy 38 nguồn đã khảo sát nhưng chưa có connector tự động nào |
| 46 | Ca sử dụng theo vai trò | Có luồng hoạt động cho từng vai trò \(22, 23, 39\) nhưng không có bức tranh tổng vai trò × chức năng | Cho con số trực tiếp: 5/30 ca sử dụng đã có · 8 một phần · 17 chưa có, và nhóm chưa có tập trung ở lõi nghiệp vụ ANKT |
| 47 | Máy trạng thái 4 đối tượng nghiệp vụ | Vòng đời chỉ số, kỳ công bố, nhiệm vụ duyệt, cảnh báo chỉ nằm trong văn bản và bảng, chưa được vẽ | Đây là phần “quản trị dữ liệu” mà hội đồng nghiệm thu sẽ soi: append\-only, maker–checker, phiên bản bất biến, ngưỡng theo kỳ |
| 48 | Tuần tự xác thực – phân quyền – ghi vết | 35 là bảo mật chuỗi cung ứng \(commit → cluster\)\. Không có sơ đồ nào về bảo mật lúc chạy | Với dữ liệu ANKT, câu hỏi “ai được xem số nào và để lại vết gì” là câu hỏi bắt buộc\. Sơ đồ chỉ rõ ABAC theo phân loại chưa có |

## 7\. Những gì bộ sơ đồ này **không** có \(và không nên có\)

Ghi rõ để không ai đi tìm nhầm:

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683922632520&cot=14)*

| Không có | Vì sao | Ở đâu thay thế |
| --- | --- | --- |
| Sơ đồ lớp mã nguồn \(class diagram mức code\) | Mã thay đổi hàng ngày, sơ đồ sẽ lạc hậu ngay | Đọc mã; AGENTS\.md có hướng dẫn tra cứu |
| Sơ đồ ERD đầy đủ mọi bảng | 05 và 12 vẽ phần lõi; vẽ hết sẽ không đọc được | Migration trong backend/alembic/versions/ |
| Sơ đồ mạng chi tiết \(IP, firewall rule\) | Thuộc tài liệu vận hành có kiểm soát truy cập | OPS\-DESIGN\.md, runbook |
| Ma trận RACI dạng sơ đồ | Bảng đọc nhanh hơn sơ đồ | SERVICE\-DESIGN\.md mục 7\.1 |
| Sơ đồ rủi ro / quyết định | Là bảng quyết định, không phải quan hệ | README mục 9, các mục ADR trong từng tài liệu |

## 8\. Bảo trì

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683922632526&cot=14)*

| Khi nào | Phải làm gì |
| --- | --- |
| Thêm sơ đồ mới | Đánh số tiếp theo · thêm hàng vào mục 2 \(đúng nhóm\) · cập nhật ma trận mục 3 · thêm vào bảng sơ đồ trong README mục 10 |
| Một năng lực chuyển từ “chưa có” sang “đã có” | Đổi màu ở mọi sơ đồ liên quan, đặc biệt 36 \(bản đồ trạng thái\) và 46 \(ca sử dụng\) — hai sơ đồ này là căn cứ báo cáo tiến độ |
| Trước mỗi kỳ báo cáo | Chạy make diagrams, chạy lệnh soi lỗi ở mục 4\.3, đối chiếu cột Tính chất ở mục 3\.3 để không trình bày nhầm mục tiêu thành hiện trạng |
