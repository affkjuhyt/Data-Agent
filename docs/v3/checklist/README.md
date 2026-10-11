# Checklist độ phủ thiết kế: cách dùng

Nhiều agent và người cùng làm theo tài liệu thiết kế hệ thống, nên "đã làm được gì" phải đọc được từ repo chứ
không nằm trong đầu ai. [`../10-design-checklist.md`](../10-design-checklist.md) trả lời câu đó cho từng hạng mục
của tài liệu: 115 use case với 778 tiêu chí nghiệm thu (tab 8), 382 endpoint A2 (tab 4), các bảng (tab 3/3b),
195 quy tắc nghiệp vụ, 75 màn hình (tab 2), 48 ADR (tab 6).

## Ba nguồn

| Tệp | Là gì | Ai sửa |
| --- | --- | --- |
| `design-snapshot.json` | Bản chụp tài liệu thiết kế: UC kèm tiêu chí và ma trận truy vết 11.1, BR → UC (11.2), màn hình (12.1, 12.5), ADR (O2) | Người chụp lại khi tài liệu đổi; ghi số rev các tab ở `source` |
| `status.tsv` | Trạng thái khai tay: `id`, `status`, `owner`, `note`, phân cách bằng tab | Agent hoặc người làm xong một hạng mục, trong cùng commit |
| `../10-design-checklist.md` | Bảng tổng hợp và audit, sinh bởi `make design-checklist` | Không sửa tay |

`status` là một trong `todo`, `doing`, `partial`, `done`, `blocked`, `na`. Mã không khai coi như `todo`.

## Bằng chứng tự động

Công cụ đọc mã, không chạy dịch vụ:

- **Endpoint:** route trong `backend/services/api` dựng bằng `endpoint("METHOD", "/v1/...")` (đã được cổng A2 giữ khớp
  với đặc tả, xem [06-api-v1.md](../06-api-v1.md)).
- **Bảng:** `CREATE TABLE schema.table` trong `backend/alembic/sql/dataagent*.sql`.
- **Tiêu chí nghiệm thu:** một test nhắc mã AC. Cách gắn chuẩn là marker có sẵn của repo:

  ```python
  @pytest.mark.req("AC-01-01-04")
  async def test_question_outside_rights_hides_table_names(...): ...
  ```

  Test frontend hay eval ghi mã trong tên hoặc chú thích (`it('AC-01-01-01 …')`).
- **Quy tắc nghiệp vụ:** mã (Python, SQL, TS, manifest) nhắc `BR-142` ở chỗ thực thi quy tắc.
- **Màn hình:** mã frontend nhắc `SCR-01`. Đa số màn chưa ghi mã: khai ở `status.tsv` kèm ghi chú.

## Quy tắc khi làm việc

1. Viết test cho một tiêu chí thì gắn mã AC của nó. Đây là cách chính để UC lên trạng thái đủ.
2. Xong một UC, màn hình, BR hay ADR mà bằng chứng tự động không thấy được thì thêm hoặc sửa dòng ở `status.tsv`.
3. Chạy `make design-checklist` rồi commit `10-design-checklist.md` cùng thay đổi. Hai agent cùng sửa tệp này thì
   khi gộp chỉ cần chạy lại lệnh, không gộp tay.
   Công cụ đọc cây làm việc, nên khi nhiều agent dùng chung một cây, sinh tệp trong một worktree sạch ở `HEAD` có
   chép thêm đúng các tệp mình sửa; nếu không, tệp sẽ ghi nhận cả mã chưa commit của agent khác và CI (chạy trên
   cây đã commit) báo tệp cũ.
4. `make design-audit` (CI chạy) thất bại khi tệp cũ hoặc có điểm khai trái bằng chứng: UC khai `done` mà còn thiếu
   endpoint, bảng hoặc tiêu chí bắt buộc; AC khai `done` không có test cũng không ghi cách kiểm; mã không có trong
   bản chụp.

## Audit

Mục "Audit" trong `10-design-checklist.md` liệt kê điểm khai trái bằng chứng và chỗ tài liệu tự lệch (ma trận 11.1
nhắc endpoint không có trong A2 hay bảng không có trong DDL). Khi cần rà thiếu sót toàn diện, đọc theo thứ tự:
UC chưa đủ tiêu chí bắt buộc → nhóm endpoint còn thiếu → BR không có mã nhắc tới → màn và ADR chưa khai.

## Chụp lại tài liệu

Khi tài liệu thiết kế đổi (thêm UC, tiêu chí, BR, màn), chụp lại `design-snapshot.json` từ tab 2, 6, 8 và cập nhật
`source.tabs` với số rev mới; A2 chụp riêng ở `backend/services/api/catalogue/a2_endpoints.json` (ADR-48). Mã đã cấp
không đổi (tab 8 T0), nên các dòng `status.tsv` cũ vẫn đúng.
