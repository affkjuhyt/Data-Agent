# 13. Nguyên tắc giao diện cho con số

> Đối tượng: FE (P0-02, E-01, S3-05, S6-03), người review màn hình. Ngày 27/09/2026. Tám nguyên tắc
> và quy ước số cho V2 (observation có release và as-of). Màn hình cụ thể: [03](03-target-design.md) mục 14; bố cục V3 ở `docs/v3/01` §5.
> Mọi màn hình V2 phải kiểm được theo tám điểm ở mục 1 trước khi coi là xong.

## 1. Tám nguyên tắc

| # | Nguyên tắc | Nghĩa cụ thể trên giao diện |
| --- | --- | --- |
| P1 | Con số là công dân hạng nhất | Số không là text trần trong câu. Mọi số đi qua một primitive giá trị mang đủ: giá trị · đơn vị + bội số · kỳ (`event_time`) · release/as-of · nguồn. Ví dụ `Brent 130,80 USD/thùng · 15/09/2026 · EIA, release 16/09` |
| P2 | Mỗi con số bấm được tới nguồn | Bấm số → mở đúng trang tài liệu (`#page=N`, khung bbox) hoặc đúng ô của artifact (`sheet,row,col` trong `source_ref`). Số không truy được nguồn thì không hiển thị |
| P3 | Release và as-of luôn nhìn thấy | Nhãn release/as-of không giấu trong tooltip. Số đã có version mới (`version_kind = correction \| override \| revision`) mang badge kèm giá trị bản trước |
| P4 | Ngưỡng và regime chọn theo kỳ dữ liệu, không theo ngày hỏi | Dải ngưỡng trên chart đổi theo khoảng hiệu lực; hover một điểm hiện luật/regime áp cho kỳ đó ([12](12-numeric-core.md) NC-T4) |
| P5 | Giả định luôn hiển thị và sửa được | Mọi slot agent tự điền (entity, spot hay futures, kỳ, as-of, phía giá, tần suất) hiện thành chip; sửa chip → chạy lại. Người dùng không phải đoán hệ thống đã hiểu gì |
| P6 | Từ chối có cấu trúc, luôn kèm lối ra | Mỗi mã lỗi một thẻ riêng với hành động cụ thể (mục 3). Không dùng câu xin lỗi chung |
| P7 | Một primitive, mọi bề mặt | Cùng một component hiển thị giá trị trong Ask, Series Explorer, Alert, Review Inbox, báo cáo. Đổi cách hiển thị = sửa một chỗ |
| P8 | Không hiển thị số không có trong evidence | Verifier chặn ở backend ([12](12-numeric-core.md) NC-T5); giao diện hiện VerifierBadge (frame `__verification`) và **ẩn** phần trả lời chứa số không kiểm được, không hiện kèm cảnh báo nhỏ |

Quy tắc render: LLM không chọn khối hiển thị và không sinh số. Capability/intent của task graph quyết
định bố cục (bảng, chart, evidence, lineage, task graph; 03 mục 10.3). Thêm series mới không cần thêm
dòng mã giao diện nào.

## 2. Quy ước số và ngày (sửa `lib/formatVN.ts`, NC-E3)

`frontend/src/lib/formatVN.ts:2` hiện định dạng theo chuẩn Mỹ. Đây là lỗi rủi ro cao nhất, chi phí
thấp nhất của giao diện: nguồn Việt Nam viết `25.226 đồng/lít`, hiển thị lệch dấu thập phân tạo ra
câu trả lời sai nhưng trông đáng tin (04 mục 6, bẫy T7).

| Loại | Quy ước | Ví dụ |
| --- | --- | --- |
| Nhóm nghìn / thập phân | `.` / `,` | `3.109,2` · `130,80` |
| Số chữ số thập phân | Theo `decimal_precision` của series, không làm tròn tự do | EIA công bố 2 chữ số → `114,89`, không `114,9` hay `114,8900` |
| Bội số | Bằng chữ, không số mũ | `1,2 triệu thùng/ngày` |
| Đơn vị giá | Tiền tệ trên đơn vị hàng hoá | `USD/thùng` · `USD/oz` · `triệu đồng/lượng` · `đồng/lít` |
| Ngày | `dd/MM/yyyy`, tuần bắt đầu Thứ Hai | `23/09/2026` |
| Kỳ | Theo tần suất | `15/09/2026` · `tuần kết thúc 18/09/2026` · `T8/2026` · `kỳ điều hành 24/09/2026` |
| Tỷ lệ | Điểm % khác % | `−12,16%` (return) khác `+1,2 điểm %` (chênh hai tỷ lệ) |
| Số âm | Dấu trừ, không ngoặc kiểu kế toán | `−3,4` |
| Phía giá | Luôn ghi khi nguồn có nhiều phía | `SJC mua vào` · `Vietcombank bán chuyển khoản` |

Quy ước phải có unit test riêng (mở rộng `formatVN.test.ts`).

## 3. Lỗi có cấu trúc → thẻ hiển thị

Mã lỗi từ compiler QuerySpec và verifier (03 mục 9, 10.3), frame `__refusal`:

| Mã | Thẻ | Lối ra |
| --- | --- | --- |
| `NO_DATA_FOR_PERIOD` | "Chưa có số cho kỳ {kỳ}" | Chuyển sang kỳ gần nhất có số; xem lịch công bố của nguồn (EIA: thứ Tư) |
| `FREQUENCY_NOT_AVAILABLE` | "Series này chỉ có theo {tần suất}" | Chuyển tần suất; gợi ý series khác (ví dụ vàng ngày chưa có nguồn sạch licence, 04 mục 7) |
| `SERIES_NOT_APPROVED` | "Series đang chờ duyệt" | Mở ReviewCase liên quan (nếu có quyền) |
| `STALE_SOURCE` | "Nguồn quá hạn cập nhật" | Ngày release cuối, SLA của nguồn; vẫn hiện số kèm nhãn |
| `ACCESS_DENIED` | "Không có quyền xem" | Hiện metadata, ẩn giá trị |
| `INSUFFICIENT_EVIDENCE` | "Chưa đủ bằng chứng để kết luận" | Hiện phần đã kiểm được; nêu bằng chứng còn thiếu |
| Không giải được entity | Thẻ hỏi lại | Top-k entity gần đúng (spot hay futures?) |

## 4. Trạng thái URL (deep link)

Điều hướng hiện chỉ giữ hai cấp `navigate(moduleId, subId)` (`frontend/src/AppStateContext.tsx`,
`routes.tsx`), không giữ tham số: không gửi được link "Brent spot, 2023–2026, as-of 21/09/2026" cho
người khác. Quy tắc cho màn V2: **URL là nguồn sự thật của trạng thái màn hình** (series, khoảng,
as-of, ReviewCase, alert); component đọc từ URL, không giữ bản sao trong state. Không bắt buộc đổi
router: `pathname` + `history.replaceState` là đủ, thêm một lớp `lib/urlState.ts`.
