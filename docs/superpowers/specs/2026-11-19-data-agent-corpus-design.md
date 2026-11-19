# Data agent đọc được bộ dữ liệu

Ngày 19/11/2026. Agent trên một Data Space trả lời như một data agent: thấy dữ liệu có cấu trúc và phi cấu trúc của chính space đó, lấy số từ chỉ số đã chứng nhận khi khớp, và viết SQL trên bảng của space khi không khớp. Không còn cửa “từ này không có trong tên chỉ số thì cấm”.

## Vì sao

Hôm nay mọi câu bị đọc thành “chỉ số nào đo câu này”. `explore_sql` từ chối trước khi viết SQL (RES-04) khi câu có từ không nằm trong tên chỉ số. Planner chỉ thấy danh mục chỉ số đã chứng nhận. NYC Taxi có bảng chuyến đi và 13 chỉ số nháp, không có tài liệu: “dữ liệu hiện có nói về gì” thành `search_catalog` rồi câu hỏi lại với danh sách trống.

## Corpus đưa vào planner

Một khối `<corpus>` dựng trước khi lập kế hoạch, chỉ những gì người hỏi được đọc (cùng lọc với `sqlgen/space.py` `load_space`: không bảng cá nhân của người khác, không bảng có access policy đang active, không cột `pii_label`).

| Phần | Nguồn | Ghi trong corpus |
| --- | --- | --- |
| Bảng, cột, nhãn, mô tả, quan hệ đã duyệt | `load_space` | có cấu trúc |
| Chỉ số `certified` | `analyst._visible` | dùng được để lấy số |
| Chỉ số `draft` | `context.metric` + `metric_version` mới nhất | “chưa chứng nhận”, không đưa vào `_visible` |
| Chiều | vocabulary của layer | tên và mã |
| Bộ tài liệu | `document.collection` status `active`, `deleted_at` null | tên và `media_kinds` (pdf, docx, html, email, audio, image) |
| Trang tri thức | `context.knowledge_page` status `active` | `title`, `kind`, `index_summary` |

Trần: 30 bảng, 40 cột mỗi bảng, 20 bộ tài liệu, 20 trang. Phần trống ghi `không có`. Chỉ số nháp không trở thành đầu vào của `query_metric`.

## Cách một câu đi

1. Câu không xin số (“dữ liệu hiện có nói về gì”, có những bảng nào, có tài liệu không): `mode=answer`, trả lời từ `<corpus>`. Không `search_catalog`, không `ask_user`.
2. Câu xin số và `<corpus>` có một chỉ số đã chứng nhận khớp: `query_metric`. Nhãn governed giữ nguyên. `audit_rules` vẫn buộc SQL của chỉ số đó đúng cột đã khai.
3. Câu xin số mà không có chỉ số đã chứng nhận khớp: `explore_sql` trên bảng trong `<corpus>`. Kết quả giữ nhãn exploratory. Không có bảng liên quan: nói không có cột để tính, không hỏi lại một menu trống.
4. Câu về nội dung văn bản và `<corpus>` có bộ tài liệu: `search_documents` như hiện tại. Space không có bộ tài liệu thì nói không có tài liệu, rồi kể phần bảng nếu câu cũng hỏi dữ liệu có gì.
5. Kỳ truy vấn không có dòng: vẫn không trả 0 (`present_gate.no_figure`). Số trong câu chữ vẫn chỉ là thẻ `{{F<n>}}` của một kết quả đã lấy.

Ví dụ cứng `DOANH_THU_INTERNET` trong prompt planner bỏ. Mô tả công cụ `explore_sql` bỏ câu cấm tự tính chỉ số ngoài danh mục.

## Không đổi

Router governed-first: câu resolver đọc được một chỉ số và một kỳ vẫn đi compiler. Quyền trước khi chạy SQL. Cột dữ liệu cá nhân không vào prompt. Sandbox và `sql_guard` chỉ đọc. Chứng nhận chỉ số vẫn là đường số đã kiểm, không phải cửa duy nhất.

## Ngoài phạm vi

Không chứng nhận hộ 13 chỉ số taxi. Không đọc từng ô bảng trong file (đường tài liệu có cấu trúc) trong đợt này: corpus chỉ kể bộ sưu tập, `search_documents` vẫn đọc đoạn. Không chạy lại bộ vàng AdventureWorks trên model thật; test đơn vị đổi lời từ chối, báo eval ghi rằng ngưỡng “từ chối đúng” phải chấm lại sau.
