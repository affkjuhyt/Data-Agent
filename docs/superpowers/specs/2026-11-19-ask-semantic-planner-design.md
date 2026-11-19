# Ask: context trước, planner chọn capability

Ngày 19/11/2026. Câu hỏi trên một Data Space không bắt đầu bằng việc tìm chỉ số. Router chỉ quyết định có chạy ngay đường governed hay không. Mọi câu còn lại đi universal planner. Không thêm route cho từng kiểu câu.

## Vì sao

`router.classify` (`backend/services/worker_agent/router.py`) gửi `UNCATALOGUED` và `AMBIGUOUS` vào `governed` (dòng 85–87). Đường đó đọc câu theo chỉ số (`understand/llm_reader.py`). “Các dữ liệu ở đây là về gì?” không khớp một chỉ số đã chứng nhận, nên thành menu chỉ số hoặc câu từ chối. Planner đã có `<corpus>` và `mode="answer"` cho câu không xin số (`agentic/prompts.py`), nhưng router không đưa câu tới đó.

`docs/blueprint/ASK_ENGINE_SPEC.md` §2 đã phác `explore` cho câu không có trong danh mục. Bản đã làm (AE-01, cùng mục, “Đã làm”) cố ý gộp nhánh đó vào governed. Quyết định này rút lại đúng phần đó: câu không đặt được một chỉ số đã chứng nhận với đủ slot không còn vào governed.

## Mô hình đích

Hai đường.

1. **Fast governed.** Một chỉ số đã chứng nhận, slot bắt buộc đủ (kỳ khi câu xin số theo kỳ), chạy compiler tất định. Câu “vì sao”, hai chỉ số được nối, vừa số vừa tài liệu, và lượt deep giữ nguyên như AE-01: sang agent.
2. **Universal planner.** Mọi câu còn lại. Planner chọn một capability, không phải một route mới: `answer`, `query_metric`, `explore_sql`, `search_documents`, `analyze`, `ask`, `refuse`.

`FORBIDDEN` và `GONE` ở lại governed. Đường đó đã từ chối đúng lý do quyền hoặc câu không còn. Planner không được kể bảng cho người không có quyền hỏi space.

`ASK_ENGINE=agentic` và `ASK_ENGINE=governed` giữ nguyên ý nghĩa đường lui.

## Hợp đồng

`RequestContract` là kết quả hiểu câu, không phải một metric.

| Trường | Giá trị |
| --- | --- |
| `purpose` | `orient`, `answer`, `analyze`, `explain`, `compare`, `document` |
| `numeric_intent` | `none`, `required`, `optional` |
| `missing_required_slots` | tên slot còn thiếu, rỗng khi đủ |

“Các dữ liệu ở đây là về gì?” là `purpose=orient`, `numeric_intent=none`, không slot thiếu. `needs_metric_choice()` sai. Chỉ đúng khi `numeric_intent` là `required` và `metric` nằm trong slot thiếu.

Mã lỗi, tách bạch, không thay thế cho nhau: `ACCESS_DENIED`, `NO_DATA`, `NO_READABLE_ASSET`, `AMBIGUOUS_REQUEST`, `MISSING_REQUIRED_SLOT`, `UNSUPPORTED_REQUEST`, `INSUFFICIENT_EVIDENCE`.

`DataSpaceContext` là nguồn sự thật một lần cho planner: phạm vi quyền, bảng/cột/quan hệ, chỉ số đã chứng nhận và nháp, chiều, bộ tài liệu và trang, truy vấn đã duyệt, chất lượng. Metadata, không embedding dòng. Cache theo principal và phiên bản context, không chỉ theo Data Space. Bản corpus hiện tại (`agentic/corpus.py`) là phần lõi; builder thống nhất là việc của kế hoạch sau, không của lát này.

## Thứ tự, để không mất menu đang đúng

Câu không đặt được chỉ số (`UNCATALOGUED`, gồm câu mô tả dữ liệu) sang planner ngay. Planner đã được bảo trả lời câu không xin số từ `<corpus>`.

Câu hai chỉ số đã chứng nhận cùng gốc (“Lãi gộp năm 2013”) vẫn `governed` / `AMBIGUOUS` cho đến khi planner tự hỏi đúng các ứng viên đó. Đưa câu này sang planner trước khi có luật hỏi theo slot sẽ mất menu đang có. Kiến trúc đích vẫn là planner quyết định; lát này chưa chuyển nhánh đó.

`llm_reader` không bị xoá. Nó không còn là nơi quyết định câu `UNCATALOGUED`.

## Ngoài lát đầu

Capability registry, Plan IR dùng chung, validator tĩnh cho mọi capability, chuyển `AMBIGUOUS` sang planner, cờ `ASK_PLANNER_SHADOW`, và xoá đường metric-first. Mỗi phần một kế hoạch sau, khi lát trước đã chạy.

## Xong khi

Câu không xin số không bị ép chọn chỉ số. Một chỉ số đã chứng nhận với đủ kỳ vẫn governed. Hai ứng viên “Lãi gộp” vẫn được hỏi trên governed. Không số nào thành 0 khi kỳ không có dòng. Người không có quyền không thấy tên bảng. Thêm một kiểu câu mới là thêm capability hoặc tài sản context, không thêm route.
