# 07. Quyết định, ADR, rủi ro

## 1. Quyết định còn chờ

Bảng chỉ chứa câu **chưa** có câu trả lời. Câu nào được chốt thì xoá khỏi bảng trong cùng commit
làm theo nó: kết quả là quyết định kỹ thuật thì ghi thành ADR ở mục 2; là việc quản lý (người, hạn,
phạm vi) thì ghi vào [05](05-implementation-plan.md). Số hiệu D không đánh lại để các file khác
không trỏ nhầm.

| # | Câu hỏi | Đề xuất |
| --- | --- | --- |
| D3 | Nguồn giá vàng thế giới theo ngày: chọn phương án nào (04 mục 7)? | POC: mua gói API thương mại (~12–32 USD/tháng), gắn nhãn "nguồn thương mại, không phải benchmark"; song song gửi email IBA hỏi loại licence cho mục đích phân tích nội bộ, chuẩn bị ngân sách Usage Licence 8.000 USD/năm cho production |
| D14 | Thêm phạm vi Việt Nam (D2) làm thiếu ~3 tuần-người, nhiều nhất ở frontend (05 mục 7): xử lý thế nào? | Thêm 1 frontend (thuê ngắn hạn hoặc điều động) từ W2; nếu không, một senior nhận frontend từ W3 và kịch bản Việt Nam rút còn 6 bước |

Đã chốt và xoá khỏi bảng:

- 27/09/2026 (lần 1): D1, D11 (tuyển 2 senior, POC 1 tháng, nghiệm thu 27/10: 05); D5 (ClickHouse:
  00, ADR-V2-16); D8 (raw bất biến: 00, ADR-V2-21); D9 (forecast ngoài POC nên không còn áp dụng).
- 27/09/2026 (lần 2): D2 (giá trong nước **làm trong POC** để trình diễn cho người dùng Việt Nam: 05,
  06 mục 7); D4 (lấy lõi số và các sửa liên quan từ `feat/add-task` sang nhánh hiện tại: P0-01);
  D6 (tên deployable: ADR-V2-22); D7 (authority và maker/checker: ADR-V2-23); D10 (chưa cần tin
  tức: 05 mục 6); D12 (nguồn fixture cho bước 1–7: 06 mục 6); D13 (chính sách licence: ADR-V2-24).

## 2. ADR

Tiếp nối ADR-V2-01…08 của thiết kế gốc. ADR mâu thuẫn với 00 thì 00 thắng.

| ADR | Quyết định | Lý do |
| --- | --- | --- |
| ADR-V2-09 | Dữ liệu có cấu trúc không đi đường chunk/embedding để phân tích số; mọi con số trả lời đi từ effective observation qua operator | Bất biến 3 của 00. Sự cố d116062 (27/09): summary chunk hỏng do LLM lỗi ở 32% chunk làm bảng đúng tụt hạng rerank; câu trả lời số phụ thuộc chất lượng một bước tóm tắt là quá mong manh |
| ADR-V2-10 | Observation thuộc Truth plane; claim/event chỉ tham chiếu `(series_key, event_time, release_id)` | Tránh hai nơi lưu cùng số |
| ADR-V2-11 | Vertical là cấu hình (nguồn, entity, series, profile, luật, capability composite, golden set) | Mục tiêu "mở rộng bằng cấu hình" |
| ADR-V2-12 | LLM đề xuất mapping/profile khi thêm nguồn hoặc khi drift; release thường chạy tất định | Bất biến 5; tái lập được |
| ADR-V2-13 | Chỉ ghi observation khi giá trị khác effective version hiện hành | File nguồn chứa toàn bộ lịch sử |
| ADR-V2-14 | Point-in-time dùng `available_at`, không dùng `event_time` | EIA công bố spot theo tuần; forecast và as-of không được thấy trước |
| ADR-V2-15 | Postgres là nguồn sự thật cho tri thức và governance; Neo4j, Qdrant là bản chiếu/index | OCC, RLS, giao dịch |
| ADR-V2-16 | Candidate ở Postgres `ingest`; chỉ bản đã duyệt vào ClickHouse; agent chỉ đọc ClickHouse | Agent không bao giờ thấy số chưa qua DQ/HITL |
| ADR-V2-17 | Chỉ `SOURCE_CONFLICT` và `UNKNOWN` mở ReviewCase mặc định | Chặn bão ReviewCase giả (UC06) |
| ADR-V2-18 | Alert gom theo sự kiện gốc, dedup `(rule, entity, window)`, có cooldown | 32 đợt giảm ≥5%/5 phiên trong 3 năm (04) |
| ADR-V2-19 | Entity khác benchmark là entity khác (`BRENT_SPOT` ≠ `BRENT_FUTURES_M1`), nối bằng `SAME_UNDERLYING` | Spot 130,80 vs futures ~107 ngày 15/9/2026 |
| ADR-V2-20 | Series đổi định nghĩa là series mới, nối bằng `SUCCEEDS` | RON95-III → E10RON95-III từ 16/6/2026 |
| ADR-V2-21 | Re-crawl chỉ thay **bản tìm kiếm** của KB; artifact (MinIO theo SHA-256), release, observation không bao giờ bị xoá | Bất biến 2 của 00; Phase A (22/09) đang xoá vết tài liệu cũ, sửa ở S2-02 |
| ADR-V2-22 | Tên: deployable `indicator-api` (Observation/Knowledge/Governance API, sau này thêm MCP Gateway), `forecast-worker`; consumer trong `platform_workers`: `series_extractor`, `semantic_extractor`, `detector` | Chung mọi miền (ADR-V2-11); chốt D6 |
| ADR-V2-23 | Authority: người duyệt được chọn theo (entity, nguồn, loại case); research là maker, người mang vai trò `REVIEWER` là checker; hệ thống chặn tự duyệt | Chốt D7 |
| ADR-V2-24 | Nguồn chưa đọc điều khoản ở `use_rights = pending_review` (IMF, JODI, GPR, WGC, Cboe, SJC/PNJ/DOJI, API vàng thương mại): nạp và dùng trong hệ thống, không đưa vào báo cáo/tài liệu gửi ra ngoài tới khi đọc xong điều khoản | Chốt D13 |

## 3. Rủi ro

| Rủi ro | Dấu hiệu sớm | Giảm thiểu | Theo dõi |
| --- | --- | --- | --- |
| Hạn 1 tháng không có đệm; thiếu ~3 tuần-người sau khi thêm phạm vi Việt Nam | Mốc C1 (09/10) hoặc C2 (16/10) trễ quá 2 ngày | D14; thứ tự cắt 05 mục 5 | PM |
| Senior vào muộn hoặc cần thời gian làm quen dài | Sau 05/10 chưa có người, hoặc P0-09 chưa xong 02/10 | Hợp đồng dữ liệu P0-09 do BE hiện có viết trước nếu cần; cắt theo 05 mục 5 | PM |
| ClickHouse mới cho đội | S1-02 chưa xong 02/10 | Test as-of với bản ghi chưa gộp ngay tuần 1; tách database khỏi Langfuse | SR2 |
| Xung đột giả tràn hộp duyệt | > 5 ReviewCase/1.000 observation/tuần | ADR-V2-17; quan hệ `SAME_UNDERLYING`; khai báo regime | BE |
| Bão alert | > 1 alert/sự kiện gốc khi replay | ADR-V2-18; test replay release 23/09 | BE |
| Nguồn chặn bot (OPEC, SJC) | Headless cũng 403 | Tải tay có metadata; nguồn thứ hai (PNJ, DOJI); không để bước demo phụ thuộc | DE |
| Thông cáo Bộ Công Thương đổi cách trình bày | Extractor không tìm thấy giá thế giới bình quân | Proposal thiếu trường → ReviewCase, research nhập tay; theo dõi tỷ lệ bài trích đủ trường | BE1 |
| API vàng thương mại: không rõ nguồn gốc giá, không cam kết độ chính xác, quyền dùng hết khi ngừng thuê bao | Giá lệch IMF/World Bank tháng > 1% | Nhãn nguồn thương mại trên mọi màn; so với trung bình tháng IMF mỗi tháng; lưu điều khoản cùng nguồn (D3) | DE |
| Nguồn ngừng hoặc đổi địa chỉ | Không có release quá SLA | `source.sla.breached`; `follow_link_pattern` | DE |
| EIA chỉ công bố tuần | Người dùng hỏi "hôm nay" | Luôn trả kèm as-of và ngày công bố; không nội suy | BE |
| Licence chưa rõ | `pending_review` tới W6 | D13; không đưa vào tài liệu gửi hội đồng | RS |
| Lỗi summarization LLM làm hỏng retrieval (~32% khi chạy song song) | Cảnh báo fallback trong log Silver | Fallback theo bảng đã sửa (d116062); giảm `SILVER_SUMMARY_CONCURRENCY` nếu tỷ lệ vẫn cao | BE |
| Ổ đĩa 28 GB trống, kubelet đuổi pod dưới ~8,7 GB | `df` dưới 20 GB | ClickHouse và snapshot nhỏ (< 2 GB) nhưng image mới cộng dồn; prune sau build; không GC registry với `--delete-untagged` | Ops |
| GPU dùng chung 33,2/40 GB | TTFT > 2 giây khi extractor chạy | `semantic_extractor` chạy lô, đồng thời 1 | Ops |
| Cổng lint/type đỏ sẵn che lỗi mới | | So worktree baseline mỗi commit | BE |
| Nhánh sống quá lâu | Nhánh tính năng > 1 tuần chưa merge | Merge `main` hằng tuần; demo chạy từ `main` | Trưởng nhóm |
