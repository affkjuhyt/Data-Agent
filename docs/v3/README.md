# Data Agent V3: sản phẩm doanh nghiệp

> Thư mục này là định hướng sản phẩm và thiết kế đang có hiệu lực từ 06/10/2026, khi Data Agent không còn
> là POC dầu/vàng mà là sản phẩm cho doanh nghiệp (quyết định của anh Linh sau khi họp chuyên gia).
> `docs/v2/` vẫn có hiệu lực cho **Observation Engine** (chuỗi chỉ số, as-of, DQ, HITL số liệu) và là
> Data Space tham chiếu đầu tiên; mười hai bất biến của `docs/v2/00` giữ nguyên, V3 thêm 13–17.

## Đọc theo thứ tự

1. [00-product-strategy.md](00-product-strategy.md): sản phẩm là gì, cho ai, 10 quyết định Q1–Q10, hiện
   trạng mã (10 lỗi gốc), kiến trúc đích, bất biến 13–15, lộ trình R0–R4, quyết định chờ D1–D12.
2. [01-agent-core-and-ui.md](01-agent-core-and-ui.md): thang tự chủ L0–L5 và mức theo vai, vòng lặp agent
   core, Plan IR, đường SQL, lớp lỗi, memory, skill, quyết định tính năng F1–F15, giao diện, eval, bất
   biến 16–17.
3. [02-market-solution.md](02-market-solution.md): định vị bán ra thị trường, phản biện bản thiết kế
   v1.0 và các đối thủ (Elton DataUtils, Looker, Fabric, Agno Dash), gói, pilot, Workspace (D9).
4. [03-r0-technical-design.md](03-r0-technical-design.md): thiết kế kỹ thuật R0 bám mã: hợp đồng, chỗ
   sửa, test, DoD.
5. [04-backlog.md](04-backlog.md): backlog R0, R1 có ID, phụ thuộc, vai, cột TT; R2–R4 giữ chỗ.
6. [05-ui-v3.md](05-ui-v3.md): giao diện v3.1 từ Figma: hệ thiết kế (token, component), khung trang,
   điều hướng sáu khu vực, cách lấy thiết kế xuống mã, trạng thái làm lại các màn.
7. [06-api-v1.md](06-api-v1.md): API `/v1` của khối `api` (modular monolith, ADR-28): kernel, cổng so
   OpenAPI với A2 (ADR-48), cách viết module, thứ tự các nhóm endpoint.
8. [07-database.md](07-database.md): CSDL V3 sinh từ tab 3/3b của tài liệu thiết kế: bộ sinh DDL và
   ORM, routines viết tay, biến phiên RLS, `scoped_session`, kiểm thử.
9. [08-legacy-cutover.md](08-legacy-cutover.md): bảng kiểm kê deployable cũ, replacement V3 và điều kiện
   xoá từng workload; không xoá theo một đợt khi replacement chưa có.

Người mới và phiên Claude mới: đọc 00 §0 và §3.2, rồi 04; lấy ID đầu tiên `todo` mà phụ thuộc đã `done`.
Quy tắc repo (test trong container, so baseline lint, outbox, naming) ở `CLAUDE.md` gốc.

## Quyết định

| ID | Câu hỏi | Trạng thái |
| --- | --- | --- |
| D1 | Năng lực model / GPU | Chờ; R0-9 đo và nộp bảng điểm |
| D2 | Giữ nghiệm thu dầu/vàng 27/10? | **Chốt 06/10: dời**; cả đội vào R0 → R1; Linear P-NOP-2 đóng, backlog mới ở P-NOP-3 |
| D3 | Khách thiết kế đầu tiên | Chờ; tiêu chí ở 02 §6 |
| D4 | Chuẩn semantic model | Khuyến nghị: nội bộ + import/export YAML chuẩn mở |
| D5 | C-level bật deep analysis? | Khuyến nghị: có, dạng mục lục + xác nhận ngân sách |
| D6 | Monitor tự gửi kênh ngoài? | Khuyến nghị: sau duyệt lần đầu |
| D7 | SOP tự lưu dùng cho chính người đó trước duyệt? | Khuyến nghị: có, nhãn "cá nhân" |
| D8 | Gói Publisher | **Chốt 06/10: hoãn** |
| D9 | Workspace thay Thread/Notebook/Report | **Chốt 06/10: đồng ý** |
| D10 | Kênh C-level | **Chốt 06/10: Zalo/Teams/email trước** |
| D11 | Giá theo kết quả cho ingestion | **Chốt 06/10: thử ở pilot** |
| D12 | MCP phơi Data Space cho coding agent | **Chốt 06/10: từ R2** |

Câu hỏi mới ghi vào bảng này; chốt xong thì cập nhật trạng thái và file liên quan trong cùng commit.

## Lịch sử

| Ngày | Thay đổi |
| --- | --- |
| 06/10/2026 | Tạo thư mục: 00 (chiến lược), 01 (agent core + UI từ awesome-data-agents), 02 (thị trường, phản biện v1.0), 03 (R0), 04 (backlog). Chốt D8–D12 |
| 06/10/2026 | Chốt D2 (dời 27/10). Linear: đóng P-NOP-2, tạo P-NOP-3 với milestone R0–R4 và story R0/R1; 04 §0 phân công theo người thật |
| 10/10/2026 | Thêm 05 (giao diện v3.1 theo Figma): hệ thiết kế, khung trang, điều hướng sáu khu vực, Trang chủ |
| 10/10/2026 | 05: màn chính của sáu khu vực trên API `/v1` của tài liệu thiết kế, mock MSW khi dev (`VITE_MOCK_V1`), danh sách trường đề xuất cho backend |
