# P0-04 · Ghi chú việc phải làm thêm

## Việc P0-09 để lại cho P0-04 (ghi ngày 29/09/2026)

P0-09 khai báo `Series.frequency: str` tạm thời, để không phải chờ P0-04
(xem [../P0-09/P0-09-plan.md](../P0-09/P0-09-plan.md), PR-A).

Khi P0-04 thêm `D`, `W`, `I`, `P` vào `Frequency` trong `backend/libs/indicator/period.py`, BE1 phải,
**trong cùng commit**:

1. Đổi `Series.frequency` trong `backend/services/indicator_api/domains/registry/` từ `str` sang
   `libs.indicator.period.Frequency`. Không tạo enum thứ hai (BR-1 của P0-09: một nơi định nghĩa).
2. Tăng phiên bản định nghĩa của `Series` (1 → 2, `x-schema-version`).
3. Xuất lại `openapi.json` và commit. Contract test của P0-09 sẽ đỏ nếu quên bước 2 hoặc 3.
4. Báo FE1 chạy lại `npm run gen:api`, vì kiểu của `frequency` đổi từ `string` sang enum.

Chỉ áp dụng khi P0-09 đã merge. Nếu P0-04 xong trước P0-09, thì P0-09 dùng thẳng `Frequency`
và bỏ qua ghi chú này.
