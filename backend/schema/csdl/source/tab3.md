# Thiết kế cơ sở dữ liệu

Tab này chứa thiết kế cơ sở dữ liệu dựng lại từ đầu theo đặc tả use case và thiết kế giao diện v3.2: nguyên tắc (D1), cấu trúc schema (D2), danh mục thực thể (D3), chi tiết từng bảng đến từng cột (D4, từ 10/10/2026 nằm ở tab 3b · Chi tiết bảng), DDL đã chạy thử (D5) và sơ đồ (D6). Trạng thái (09/10/2026): đã xong D1 đến D6; DDL đã chạy thử trên PostgreSQL 16. Bổ sung (10/10/2026): schema acquire (P14) và forecast (P15) với 18 bảng ở D2, D3, D4; DDL tăng thêm (D5.1, dataagent\_p14\_p15.sql) đã chạy thử trên PostgreSQL 16 và sơ đồ D6.1, D6.3, D6.8 đã cập nhật. Cùng ngày thêm catalog.dataset\_version\_certification (D4.4, lịch sử chứng nhận dataset hợp nhất, BR-156). Tổng 171 bảng: 169 bảng ở 22 schema của dataagent và 2 bảng audit, chưa kể outbox. Trạng thái 10/10/2026: DDL đầy đủ (dataagent.sql, dataagent\_audit.sql) sinh lại từ D4 qua csdl/definitions.json bằng gen\_ddl.py, đã gồm P14, P15 và lớp tri thức; chạy trên PostgreSQL 16 không stub, 198 phép thử đúng (D5.4); sơ đồ D6.1 đến D6.9 sinh từ cùng định nghĩa. Trạng thái 11/10/2026: tổng 196 bảng (194 bảng ở 23 schema của dataagent và 2 bảng audit, chưa kể 23 outbox); D4 ở tab 3b; DDL sinh lại từ tab 3 và 3b đã gồm 25 bảng lượt 4 và schema exec, chạy trên PostgreSQL 16 trong CSDL mới dataagent\_full2, 301 phép thử 0 lỗi (D5.4); sơ đồ D6.1 đến D6.11.

Bổ sung 10/10/2026 (lớp tri thức biên soạn, UC-07-10 đến UC-07-12): 8 bảng knowledge\_ trong schema context ở D3 và D4.5; DDL tăng thêm dataagent\_knowledge.sql (D5.1) đã chạy thử trên PostgreSQL 16 (D5.4); 8 bảng này vẽ ở Hình D6.9; từ 10/10/2026 DDL nằm trong dataagent.sql.

Bổ sung 10/10/2026 (data product, biên nhận, Error Book, định tuyến mô hình): 8 bảng mới context.data\_product, context.data\_product\_member (D4.5), qa.answer\_receipt, qa.answer\_receipt\_figure, qa.receipt\_replay (D4.6), eval.error\_book\_entry (D4.9), governance.model\_provider, governance.model\_route\_decision (D4.2); cột mới qa.answer.data\_product\_id, product\_scope, governance.model\_config.provider\_id, route\_role, governance.model\_usage.provider\_id, route\_decision\_id; giá trị mới query.query\_run.caller\_kind receipt\_replay, eval.eval\_item.origin error\_book, inbox.proposal.kind data\_product, khoá ai\_setting frontier\_routing; cột mới context.data\_product\_member.classification, classification\_confirmed\_by. Đặc tả D4 đã đủ; ngày 11/10/2026 đã sinh lại DDL, phép thử và số bảng ở D2 (D5.1, D5.4), cùng 10 bảng schema exec (D4.24).

## D1. Nguyên tắc thiết kế

Thiết kế này dựng lại cơ sở dữ liệu từ 88 đặc tả use case (tab 1, mục 5), 148 quy tắc nghiệp vụ và thiết kế giao diện v3.2. Nó thay cho mục 8 của tab 1 khi hoàn tất. Sản phẩm phục vụ nhiều tổ chức thuộc nhiều ngành; ngân hàng chỉ là ví dụ minh hoạ.

1. Một cụm PostgreSQL 16 với hai CSDL: `dataagent` chứa 23 schema nghiệp vụ, mỗi dịch vụ một schema; `dataagent_audit` chứa schema `audit` (chỉ ghi thêm, thời hạn lưu và sao lưu riêng).
2. Ranh giới dịch vụ: khoá ngoại chỉ dùng trong cùng một schema. Giữa hai schema là tham chiếu logic: cột `*_id` kiểu uuid, ghi “logic → schema.bảng” trong từ điển. Mỗi dịch vụ có một vai trò CSDL chỉ ghi được schema của mình; dịch vụ khác đọc qua view chỉ đọc do dịch vụ chủ cấp.
3. Đa tổ chức: mọi bảng nghiệp vụ có `org_id uuid NOT NULL` và Row-Level Security theo `current_setting('app.org_id')`. Bảng thuộc một Data Space có thêm `data_space_id`; quyền trong Data Space do Policy engine kiểm ở tầng ứng dụng. Một bản cài cho một khách thì chỉ có một dòng trong `org.organization`.
4. Không gắn với ngành: bảng và cột lõi không mang khái niệm riêng của một ngành. Ngữ nghĩa ngành (thực thể chuẩn, chỉ số, bảng mã, luật kiểm tra, lịch báo cáo) là dữ liệu trong schema `pack`.
5. Khoá: `id uuid` sinh ở ứng dụng theo UUIDv7 (có thứ tự thời gian, tốt cho chỉ mục). Mã nghiệp vụ hiển thị cho người dùng (`code`) là duy nhất trong phạm vi `org_id` hoặc `data_space_id`.
6. Cột chung: `org_id`, `created_at`, `created_by`, `updated_at`, `updated_by`. Đối tượng người dùng tạo và có thể xoá dùng xoá mềm `deleted_at`. Bản ghi bất biến (phiên bản đã phát hành, số đã công bố, nhật ký) không có `updated_*` và bị chặn UPDATE bằng quyền.
7. Phiên bản hoá: đối tượng có lịch sử dùng cặp bảng `X` và `X_version` (`version`, `status`, `valid_from`, `valid_to`). Phiên bản đã phát hành không sửa; sửa là tạo phiên bản mới.
8. Trạng thái: cột `status text` kèm ràng buộc CHECK theo máy trạng thái ở mục 7.5 và mục 5. Mỗi lần chuyển trạng thái ghi một sự kiện vào outbox trong cùng giao dịch.
9. Thời gian: mốc thời gian dùng `timestamptz` (UTC); ngày nghiệp vụ dùng `date`; khoảng hiệu lực dùng `daterange` hoặc `tstzrange`, kèm ràng buộc loại trừ để không chồng nhau.
10. Bảng lớn phân vùng theo tháng: `query_run`, `agent_step`, `model_usage`, `usage_counter`, `webhook_delivery`, `notification`, `audit_log`. Phân vùng quá hạn theo `retention_policy` được xuất sang MinIO (`da-archive`) rồi gỡ.
11. Dữ liệu cá nhân: cột có thể chứa dữ liệu cá nhân được đánh dấu trong từ điển và mã hoá ở tầng ứng dụng; định danh chủ thể dữ liệu chỉ lưu bản băm.
12. Ngoài PostgreSQL: Qdrant giữ vector (đoạn tài liệu, danh mục lược đồ, câu hỏi mẫu), mỗi điểm có payload `org_id`, `data_space_id` để lọc; PostgreSQL chỉ giữ mã điểm. MinIO giữ tệp, Redis giữ bộ nhớ đệm và đếm hạn mức, OpenBao giữ bí mật, Kafka nhận sự kiện từ outbox.
13. Outbox: mỗi schema có bảng `outbox` (`id`, `aggregate_type`, `aggregate_id`, `event_type`, `payload jsonb`, `created_at`, `published_at`); Debezium đọc và đẩy sang Kafka theo mục 9.3. Từ 12/10/2026 kể cả schema audit trong CSDL dataagent\_audit (audit.outbox), để Audit service phát sự kiện, ví dụ mở yêu cầu duyệt bản xuất quá 100.000 dòng (BR-138).
14. Đặt tên: snake\_case, số ít; khoá ngoại `<bảng>_id`; chỉ mục `<bảng>_<cột>_idx`; ràng buộc `<bảng>_<ý nghĩa>_check`.

## D2. Cấu trúc schema

| Schema | Dịch vụ sở hữu | Nội dung | Phân hệ | Số bảng |
| --- | --- | --- | --- | --- |
| org | Identity & tenancy (Admin service) | Tổ chức, người dùng, nhóm, vai trò, Data Space, thành viên, lịch làm việc, yêu cầu quyền | P11 | 12 |
| governance | Policy engine, Admin service | Chính sách dữ liệu và hành vi, thời hạn lưu, chủ thể dữ liệu, cấu hình và sử dụng mô hình AI | P11 | 11 |
| integration | Gateway, MCP server | Client API và MCP, webhook, hạn mức, chống gửi trùng | P12 | 5 |
| catalog | Connector service | Nguồn, bảng, cột, hồ sơ dữ liệu, lịch sử schema, lượt quét, lịch sử chứng nhận dataset hợp nhất | P04 | 7 |
| context | Context service | Mô hình ngữ nghĩa, chỉ số và phiên bản, từ đồng nghĩa, ví dụ, thuật ngữ, trang tri thức và phiên bản, khẳng định, ràng buộc số, liên kết, lượt và phát hiện rà soát | P07 | 22 |
| qa | Agent runtime (hỏi đáp) | Hội thoại, câu hỏi, kế hoạch, câu trả lời, phản hồi, xuất tệp | P01 | 9 |
| query | Query engine | Lượt chạy SQL và kết quả rút gọn | P01, P02 | 2 |
| agent | Agent runtime (quản lý agent) | Định nghĩa, cấu hình, công cụ, trigger, triển khai, lượt chạy, bước, bộ nhớ | P08, P13 | 10 |
| eval | Eval service | Bộ đánh giá dùng chung cho lớp ngữ cảnh, kỹ năng, agent, gói ngành | P03, P07, P13 | 5 |
| inbox | Inbox service | Đề xuất, quyết định duyệt, yêu cầu HITL, chuỗi duyệt | P07 | 4 |
| workspace | Workspace service | Phiên, ô, kết quả lưu, chia sẻ, bình luận, báo cáo phiên | P02 | 8 |
| skill | Skill service | Kỹ năng, phiên bản, lượt chạy, đề xuất, dữ liệu bên ngoài | P03 | 7 |
| monitor | Monitor & report service | Theo dõi chỉ số, cảnh báo, báo cáo định kỳ, lịch sự kiện ngoài và bản tin theo kỳ | P08 | 9 |
| notify | Notification & channel gateway | Thông báo, cài đặt nhận, liên kết kênh, bản sao trên kênh | P08 | 4 |
| document | Document service | Bộ sưu tập, tài liệu, trang, đoạn, chất lượng, trích xuất, ghi âm, email | P05 | 10 |
| conflict | Conflict service | Khẳng định, xung đột, quy tắc giải quyết, thẩm quyền nguồn | P07 | 5 |
| pack | Pack service | Gói ngành, phiên bản, mô hình chuẩn, cài đặt, ánh xạ, hiệu chỉnh, đối chiếu, nâng cấp | P09 | 15 |
| period | Period service | Lịch chốt, kỳ chốt, tệp kỳ, đối soát, số đã công bố, hồi tố | P10 | 6 |
| pipeline | Pipeline service | Pipeline, lượt chạy, dòng dữ liệu, data mart, sản phẩm dữ liệu, ghi ngược | P06 | 6 |
| ingest | Ingestion service | Job nạp, lượt chạy, bản ghi lỗi, tải tệp, quy tắc và kết quả chất lượng | P04, P06 | 6 |
| audit | Audit service (CSDL riêng) | Nhật ký kiểm toán chuỗi băm, sự kiện bảo mật, bản xuất có chữ ký | P11 | 3 |
| acquire | Acquisition service | Nguồn thu thập, phiên bản schema thu thập, lượt thu, trang gốc, bản ghi trích, quyết định duyệt, thay đổi cấu trúc, liên kết thực thể, danh mục thực thể chuẩn, đặc tả và lượt hợp nhất | P14 | 11 |
| forecast | Forecast service | Việc dự báo, mô hình và phiên bản, backtest, kết quả dự báo, kịch bản, nhật ký độ chính xác, mô hình và tham số công thức, sổ dự báo | P15 | 10 |
| exec | Exec vault service | Kho riêng lãnh đạo: kho và khoá bọc, luận điểm mã hoá, bằng chứng và điểm chấm niêm phong, bộ nhớ riêng, sổ truy cập khoá, bàn giao, bản công bố | P16 | 10 |
| Tổng |  |  |  | 196 bảng, cộng một bảng outbox trong mỗi schema của dataagent (23 outbox) |

## D3. Danh mục thực thể

Mỗi bảng ghi mục đích, các use case đọc hoặc ghi nó (theo dòng “Bảng dữ liệu” của đặc tả), và thay đổi so với bản v3.1 ở mục 8. Cả 141 bảng của v3.1 đều đã được xếp chỗ; có 59 bảng mới, trong đó 18 bảng của P14, P15 (schema acquire và forecast), 8 bảng lớp tri thức (context.knowledge\_\*) và 25 bảng lượt 4 ngày 10/10/2026 (15 bảng data product, biên nhận, Error Book, provider, dự báo theo công thức, lịch sự kiện ngoài và 10 bảng schema exec).

#### org · Identity & tenancy (Admin service)

| Bảng | Mục đích | Use case dùng | So với v3.1 |
| --- | --- | --- | --- |
| organization | Tổ chức (khách hàng) dùng hệ thống; gốc của org\_id | 11-08 | Mới |
| app\_user | Người dùng, đồng bộ từ nguồn danh tính của tổ chức | 11-01, 13-01 | Giữ |
| user\_group | Nhóm người dùng đồng bộ từ nguồn danh tính | 11-01 | Mới |
| user\_group\_member | Thành viên của nhóm theo lần đồng bộ gần nhất | 11-01 | Mới |
| role | Vai trò | 11-01 | Giữ |
| user\_role | Gán vai trò | 11-01 | Giữ |
| group\_mapping | Ánh xạ nhóm từ nguồn danh tính sang vai trò và Data Space | 11-01, 11-08 | Giữ; nguồn danh tính chung (AD, LDAP, OIDC) |
| data\_space | Không gian dữ liệu của một nhóm nghiệp vụ | 04-04, 11-08 | Chuyển từ catalog sang org: là ranh giới quyền |
| data\_space\_member | Người dùng, nhóm hoặc agent thuộc Data Space | 11-01, 11-07, 11-08, 13-01 | Chuyển từ catalog sang org |
| access\_request | Yêu cầu cấp quyền | 11-07 | Chuyển từ admin |
| work\_calendar | Lịch làm việc của tổ chức | 02-04, 04-05, 06-03, 07-13, 13-03 | Đổi tên từ business\_calendar, dùng chung mọi ngành |
| work\_calendar\_day | Ngày nghỉ, ngày làm việc bù của từng lịch | 02-04, 04-05, 06-03 | Mới |

#### governance · Policy engine, Admin service

| Bảng | Mục đích | Use case dùng | So với v3.1 |
| --- | --- | --- | --- |
| access\_policy | Chính sách dữ liệu theo dòng, cột, che | 11-02 | Giữ |
| behavior\_policy | Chính sách hành vi: chặn, cần duyệt, tự xử lý, ngân sách; nguồn gốc, hạn xem lại | 07-04, 07-08, 11-02, 13-02 | Đổi tên từ policy cho rõ nghĩa |
| policy\_backtest | Kết quả chạy thử chính sách trên lịch sử | 07-08 | Giữ |
| retention\_policy | Thời hạn lưu theo loại dữ liệu | — | Giữ |
| dsr\_request | Yêu cầu của chủ thể dữ liệu | 11-06 | Giữ |
| dsr\_item | Một mục dữ liệu tìm thấy cho yêu cầu chủ thể | 11-06 | Giữ |
| model\_config | Cấu hình mô hình theo tác vụ | 11-05 | Giữ |
| ai\_setting | Quy định AI dạng khoá–giá trị (chi phí tối đa mỗi lượt, bộ lọc, thời hạn lưu prompt) | 11-05 | Mới |
| model\_usage | Số token và thời gian theo lượt gọi mô hình | 11-04, 11-05 | Giữ; phân vùng theo tháng |
| model\_provider | Provider mô hình tại chỗ hoặc frontier, khoá ở OpenBao, cờ bật theo tổ chức | 11-05, 01-07 | Mới (10/10/2026, ADR-41) |
| model\_route\_decision | Quyết định định tuyến theo độ nhạy và kiểm toán lời gọi frontier | 11-05, 01-07 | Mới (10/10/2026, ADR-41) |

#### integration · Gateway, MCP server

| Bảng | Mục đích | Use case dùng | So với v3.1 |
| --- | --- | --- | --- |
| api\_client | Client MCP hoặc API | 12-01, 12-02, 12-04 | Giữ |
| webhook\_subscription | Đăng ký nhận sự kiện | 12-03 | Giữ |
| webhook\_delivery | Một lần gửi webhook | 12-03 | Giữ; phân vùng theo tháng |
| usage\_counter | Đếm sử dụng theo client, Data Space | 12-02, 12-04 | Giữ |
| idempotency\_key | Khoá chống gửi trùng ở gateway | — | Giữ; tự xoá sau 24 giờ |

#### catalog · Connector service

| Bảng | Mục đích | Use case dùng | So với v3.1 |
| --- | --- | --- | --- |
| source | Kết nối tới một nguồn dữ liệu của tổ chức | 04-01, 04-04, 04-05, 07-05 | Giữ |
| dataset | Bảng hoặc view ở nguồn | 04-02, 04-04, 04-06, 04-08, 06-01, 06-02, 06-05, 07-13 | Giữ |
| dataset\_version | Phiên bản schema của một bảng | 01-07, 04-02, 04-03, 04-06, 04-07, 07-13, 10-01 | Giữ |
| dataset\_version\_certification | Lịch sử chứng nhận, thu hồi chứng nhận phiên bản dataset hợp nhất; chỉ ghi thêm | 14-05 | Mới (10/10/2026, BR-156) |
| column\_meta | Cột của bảng | 04-02, 04-03, 04-04, 04-08 | Giữ; nhãn dữ liệu cá nhân là cột pii\_label |
| column\_profile | Thống kê giá trị của cột | 04-02 | Giữ |
| scan\_run | Một lần quét schema và thống kê nguồn | 04-01, 04-02, 04-05 | Giữ |

#### context · Context service

| Bảng | Mục đích | Use case dùng | So với v3.1 |
| --- | --- | --- | --- |
| semantic\_entity | Thực thể trong mô hình ngữ nghĩa của Data Space (ví dụ khách hàng, hợp đồng, giao dịch) | 07-01, 07-02 | Giữ |
| semantic\_dimension | Chiều phân tích (ví dụ vùng, đơn vị, sản phẩm, kênh) | 07-01 | Giữ |
| semantic\_measure | Độ đo gốc để ghép thành chỉ số (ví dụ tổng giá trị, số lượng) | 07-01 | Giữ |
| metric | Chỉ số nghiệp vụ | 07-01, 07-13 | Giữ |
| metric\_version | Định nghĩa của chỉ số theo phiên bản | 01-02, 01-07, 02-02, 04-03, 06-05, 07-01, 07-13, 09-03, 10-03, 15-07 | Giữ |
| relationship | Quan hệ giữa hai bảng | 04-02, 07-02 | Giữ |
| synonym | Cách gọi khác của chỉ số, cột, giá trị | 07-01, 07-02, 07-09 | Giữ |
| value\_index | Giá trị phân loại để khớp tên gọi trong câu hỏi (ví dụ “Hải Phòng” khớp mã HP01) | 07-02 | Giữ |
| example\_query | Câu hỏi mẫu kèm SQL đúng | 07-02 | Giữ |
| glossary\_term | Thuật ngữ nghiệp vụ và giải thích | 07-09 | Giữ |
| unmapped\_phrase | Cụm từ hay hỏi chưa ánh xạ | 01-03, 07-09 | Giữ |
| user\_term\_pref | Cách hiểu riêng của một người với thuật ngữ | 07-09 | Giữ |
| knowledge\_page | Trang tri thức (chỉ số, thực thể, nguồn, chủ đề): dòng mục lục, phiên bản đang phục vụ, chính sách tính từ nguồn | 07-10, 07-11, 07-12, 16-05 | Mới (10/10/2026, lớp tri thức biên soạn) |
| knowledge\_page\_version | Phiên bản trang bất biến: mẫu thân trang, trạng thái duyệt, người duyệt, người chứng nhận | 07-10, 07-11, 07-12, 16-05 | Mới (10/10/2026, lớp tri thức biên soạn) |
| knowledge\_source\_link | Nguồn của một phiên bản (sources\[\]) kèm mã băm và thuộc tính quyền | 07-10, 07-11, 07-12 | Mới (10/10/2026, lớp tri thức biên soạn) |
| knowledge\_claim | Khẳng định của trang, mỗi khẳng định một nguồn và hiệu lực từ, đến | 07-10, 07-11, 07-12, 16-05 | Mới (10/10/2026, lớp tri thức biên soạn) |
| knowledge\_figure | Ràng buộc chỗ giữ số tới chỉ số semantic hoặc bảng tổng hợp; số điền khi hiển thị | 07-10, 07-12, 16-05 | Mới (10/10/2026, lớp tri thức biên soạn) |
| knowledge\_link | Liên kết giữa trang | 07-10, 07-11 | Mới (10/10/2026, lớp tri thức biên soạn) |
| knowledge\_run | Lượt biên soạn hoặc rà soát của Agent Tri thức; giới hạn số trang sửa | 07-10, 07-11 | Mới (10/10/2026, lớp tri thức biên soạn) |
| knowledge\_lint\_finding | Phát hiện rà soát và hành động cố định đã chọn | 07-11, 07-14 | Mới (10/10/2026, lớp tri thức biên soạn) |
| data\_product | Data product: phạm vi trả lời có cam kết, owner, steward, cam kết độ tươi | 07-13, 07-14, 01-07, 01-01 | Mới (10/10/2026, data product) |
| data\_product\_member | Thành phần của data product và trạng thái độ tươi theo lịch | 07-13, 01-07, 01-01 | Mới (10/10/2026, data product) |

#### qa · Agent runtime (hỏi đáp)

| Bảng | Mục đích | Use case dùng | So với v3.1 |
| --- | --- | --- | --- |
| conversation | Một phiên hội thoại | 01-01, 01-04 | Tách từ agent |
| question | Một lượt hỏi | 01-01, 01-03, 01-04, 10-01, 12-02 | Tách từ agent |
| plan | Kế hoạch đã hiểu từ câu hỏi | 01-01, 01-02, 01-03, 01-04, 01-07, 07-09, 10-01, 13-06 | Tách từ agent |
| answer | Câu trả lời | 01-01, 01-02, 01-03, 01-04, 01-05, 01-07, 05-04, 07-14 | Tách từ agent |
| feedback | Đánh giá của người dùng | 01-05, 07-06, 07-14 | Tách từ agent |
| export\_job | Việc xuất kết quả ra tệp | 01-06 | Tách từ agent |
| answer\_receipt | Biên nhận đã khoá của câu trả lời trong phạm vi data product | 01-07, 01-01, 07-14 | Mới (10/10/2026, biên nhận) |
| answer\_receipt\_figure | Một số trong biên nhận: chỉ số, người ký, dataset\_version, mã băm truy vấn, độ tươi | 01-07, 01-02 | Mới (10/10/2026, biên nhận) |
| receipt\_replay | Một lần chạy lại biên nhận tại thời điểm cũ và kết quả từng số | 01-07, 07-14 | Mới (10/10/2026, biên nhận) |

#### query · Query engine

| Bảng | Mục đích | Use case dùng | So với v3.1 |
| --- | --- | --- | --- |
| query\_run | Một lần chạy SQL | 01-01, 01-02, 01-03, 01-04, 01-06, 01-07, 02-02, 03-01, 03-02, 10-01, 10-05, 12-02, 16-02, 16-04 | Giữ; phân vùng theo tháng |
| query\_result | Kết quả rút gọn của query\_run để hiển thị lại | 01-02, 02-02 | Giữ; giữ 30 ngày, bản đầy đủ ở MinIO |

#### agent · Agent runtime (quản lý agent)

| Bảng | Mục đích | Use case dùng | So với v3.1 |
| --- | --- | --- | --- |
| agent\_def | Agent: tên, chủ sở hữu, trạng thái | 08-01, 13-01 | Giữ |
| agent\_config | Cấu hình agent theo phiên bản: mục tiêu, phạm vi dữ liệu, kỹ năng, công cụ, mức tự chủ, checkpoint, giới hạn, mô hình | 03-06, 13-01, 13-02 | Giữ |
| agent\_tool | Công cụ agent dùng được và mức quyền | 13-01, 13-02 | Giữ |
| agent\_trigger | Trigger của agent | 08-01, 13-03 | Giữ |
| agent\_deployment | Phiên bản đang chạy, tỷ lệ lượt, cổng chất lượng, quay lui | 13-05 | Giữ |
| agent\_run | Một lượt chạy của agent: trạng thái, phiên bản cấu hình, chi phí | 02-05, 02-06, 07-07, 08-05, 11-03, 13-04, 13-05, 13-06, 13-07, 13-08, 16-03 | Đổi tên từ agent\_session, khớp giao diện “Lượt chạy” |
| agent\_step | Một bước trong lượt chạy: gọi mô hình, công cụ, agent khác | 08-05, 11-03, 13-06, 13-08 | Giữ |
| agent\_checkpoint\_hit | Lần lượt chạy dừng ở checkpoint | 07-07, 13-06 | Giữ |
| agent\_memory | Ghi nhớ của agent | 13-07 | Giữ |
| background\_task | Việc giao cho agent nền | 08-05 | Giữ |

#### eval · Eval service

| Bảng | Mục đích | Use case dùng | So với v3.1 |
| --- | --- | --- | --- |
| eval\_set | Bộ đánh giá cho lớp ngữ cảnh, kỹ năng, agent hoặc gói ngành | 07-03, 07-06, 07-13 | Tách từ inbox, dùng chung |
| eval\_item | Một mục đánh giá: câu hỏi chuẩn, ca kiểm thử kỹ năng hoặc tình huống thử agent, kèm đáp án | 01-05, 03-03, 03-04, 07-06, 07-13, 07-14, 13-04 | Gộp eval\_item (inbox), agent\_eval\_case (agent), skill\_test\_case (skill) |
| eval\_run | Một lần chạy bộ đánh giá trên một phiên bản đối tượng | 07-03, 07-06, 07-13, 07-14, 11-05, 13-04 | Gộp eval\_run (inbox) và agent\_eval\_run (agent) |
| eval\_result | Kết quả từng mục trong một lần đánh giá | 07-03, 07-06, 07-14 | Tách từ inbox |
| error\_book\_entry | Mục Error Book: lỗi đã xác nhận, nguyên nhân gốc, cách sửa, ca hồi quy | 07-14, 01-05, 01-07, 15-06 | Mới (10/10/2026, Error Book) |

#### inbox · Inbox service

| Bảng | Mục đích | Use case dùng | So với v3.1 |
| --- | --- | --- | --- |
| proposal | Đề xuất thay đổi chờ duyệt | 01-05, 03-05, 04-03, 05-05, 06-04, 07-01, 07-02, 07-03, 07-05, 07-06, 07-13, 07-14, 09-02, 09-03, 09-04, 16-05 | Giữ |
| proposal\_review | Quyết định duyệt | 07-03 | Giữ |
| hitl\_request | Yêu cầu người quyết định trong Cần bạn | 01-05, 01-06, 02-07, 03-05, 04-07, 06-04, 07-03, 07-04, 07-07, 07-08, 08-04, 08-06, 10-02, 10-04, 11-07, 13-05, 13-07, 13-08, 15-07 | Giữ |
| hitl\_approval\_step | Một bước trong chuỗi duyệt nhiều bước | 07-07 | Giữ |

#### workspace · Workspace service

| Bảng | Mục đích | Use case dùng | So với v3.1 |
| --- | --- | --- | --- |
| workspace | Phiên làm việc (notebook) của người dùng | 02-01, 02-06, 03-03, 03-07 | Giữ |
| tile | Một ô trong phiên | 02-01, 02-02, 02-06, 03-03, 03-07, 06-05 | Giữ |
| tile\_snapshot | Kết quả lưu của một lần làm mới | 02-01, 02-02 | Giữ |
| workspace\_share | Chia sẻ phiên | 02-03 | Giữ |
| ws\_comment | Bình luận và việc được giao trên phiên hoặc ô | 02-05 | Giữ |
| ws\_participant | Người và agent tham gia phiên | 02-01, 02-03, 02-06 | Giữ |
| artifact | Kết quả có phiên bản trong phiên: bảng, biểu đồ, đề xuất, bộ dữ liệu | 02-06, 02-07, 08-05 | Giữ |
| session\_report | Báo cáo của phiên và kết quả kiểm tra trước phát hành | 02-04, 02-07, 06-05, 08-03 | Giữ |

#### skill · Skill service

| Bảng | Mục đích | Use case dùng | So với v3.1 |
| --- | --- | --- | --- |
| skill | Kỹ năng phân tích | 03-01, 03-03, 03-05, 03-06, 03-07 | Giữ |
| skill\_version | Phiên bản của kỹ năng | 03-01, 03-03, 03-04, 03-05, 03-06, 03-07 | Giữ |
| skill\_run | Một lần chạy kỹ năng | 03-01, 03-02, 03-04, 08-02 | Giữ |
| skill\_run\_step | Kết quả từng bước của một lần chạy kỹ năng | 03-02 | Giữ |
| skill\_suggestion | Kỹ năng agent đề xuất từ chuỗi bước lặp lại | 03-07 | Giữ |
| external\_series | Chuỗi dữ liệu bên ngoài (ví dụ CPI, tỷ giá, lãi suất thị trường) | 03-02 | Giữ |
| external\_observation | Giá trị của chuỗi ngoài, có ngày công bố để tránh nhìn trước | 03-02 | Giữ |

#### monitor · Monitor & report service

| Bảng | Mục đích | Use case dùng | So với v3.1 |
| --- | --- | --- | --- |
| monitor | Theo dõi chỉ số | 08-01, 08-02 | Giữ |
| alert | Cảnh báo phát sinh | 08-01, 08-02, 08-04 | Giữ |
| report\_schedule | Báo cáo định kỳ | 02-04, 03-06, 08-03, 08-04 | Giữ |
| report\_recipient | Người nhận báo cáo và nhóm quyền dùng để sinh bản | 02-07, 08-03 | Giữ |
| report\_run | Một lần chạy báo cáo định kỳ hoặc bản tin | 08-03, 08-04 | Giữ |
| external\_calendar | Lịch sự kiện ngoài (kỳ điều hành giá xăng dầu), giờ bản tin, nguồn phát hiện | 08-08 | Mới |
| external\_event | Một kỳ dự kiến, đã xác nhận, dời hoặc đã diễn ra | 08-08, 15-08 | Mới |
| event\_subscription | Người theo dõi lịch, loại bản tin và kênh nhận | 08-08 | Mới |
| event\_bulletin | Bản tin trước kỳ và sau kỳ, kết quả cổng, trạng thái gửi | 08-08 | Mới |

#### notify · Notification & channel gateway

| Bảng | Mục đích | Use case dùng | So với v3.1 |
| --- | --- | --- | --- |
| notification | Thông báo gửi cho người dùng | 02-03, 02-04, 02-05, 08-02, 08-03, 08-04, 08-06, 08-07, 08-08 | Tách từ monitor |
| notification\_pref | Cài đặt nhận thông báo | 08-06, 08-07 | Tách từ monitor |
| channel\_binding | Liên kết tài khoản người dùng với Teams, Zalo, email, ứng dụng di động | 08-06, 08-07 | Mới |
| channel\_delivery | Bản sao thẻ trên một kênh và trạng thái | 07-07, 08-02, 08-06, 08-08 | Tách từ monitor |

#### document · Document service

| Bảng | Mục đích | Use case dùng | So với v3.1 |
| --- | --- | --- | --- |
| collection | Bộ sưu tập tài liệu | 05-01, 05-06 | Giữ |
| document\_source | Thư mục, hộp thư, nguồn ghi âm đồng bộ vào bộ sưu tập | 05-01 | Giữ |
| document | Tài liệu đã nạp và trạng thái xử lý | 05-01, 05-04, 05-05 | Giữ |
| document\_page | Trang của tài liệu | 05-01, 05-02, 05-03, 05-04 | Giữ |
| chunk | Đoạn đã chia, dùng để tìm kiếm | 05-02, 05-03, 05-04, 05-05 | Giữ; vector ở Qdrant, bảng giữ qdrant\_point\_id |
| quality\_issue | Vấn đề chất lượng chờ duyệt | 05-02, 05-03, 05-06 | Giữ |
| extraction\_schema | Lược đồ trích xuất theo phiên bản | 05-06 | Giữ |
| extracted\_record | Bản ghi trích từ một tài liệu | 05-02, 05-03, 05-06 | Giữ |
| transcript\_segment | Đoạn chép lời của ghi âm | 05-02, 05-06 | Giữ |
| email\_message | Thư trong một luồng email | 05-06 | Giữ |

#### conflict · Conflict service

| Bảng | Mục đích | Use case dùng | So với v3.1 |
| --- | --- | --- | --- |
| claim | Khẳng định trích từ nguồn | 05-02, 07-04 | Giữ |
| conflict | Một vụ xung đột giữa các nguồn | 07-04 | Giữ |
| conflict\_claim | Khẳng định tham gia vụ xung đột | 07-04 | Giữ |
| resolution\_rule | Quy tắc giải quyết đã duyệt | 07-04 | Giữ |
| source\_authority | Thứ tự ưu tiên nguồn theo loại dữ liệu | 07-04, 07-05 | Giữ |

#### pack · Pack service

| Bảng | Mục đích | Use case dùng | So với v3.1 |
| --- | --- | --- | --- |
| industry\_pack | Gói ngành | 09-01 | Giữ |
| pack\_version | Phiên bản gói ngành | 09-01, 09-05 | Giữ |
| canonical\_entity | Thực thể trong mô hình chuẩn của ngành | 09-01, 09-05 | Giữ |
| canonical\_attribute | Thuộc tính của thực thể chuẩn | 09-01, 09-05 | Giữ |
| pack\_metric | Chỉ số chuẩn của gói | 09-01, 09-03, 09-05 | Giữ |
| validation\_rule | Luật kiểm tra của gói (ví dụ tổng khớp sổ cái) | 09-01, 09-05 | Giữ |
| pack\_skill | Kỹ năng và agent mẫu đi kèm gói ngành | 09-01, 13-01 | Mới |
| pack\_install | Gói đã cài ở một Data Space | 09-02, 09-04 | Giữ |
| entity\_mapping | Ánh xạ thực thể chuẩn sang bảng, cột của tổ chức | 04-03, 09-02, 09-04, 10-01 | Giữ |
| code\_mapping | Bảng mã của tổ chức sang mã chuẩn (ví dụ phân loại, loại tiền) | 09-02 | Giữ |
| metric\_override | Hiệu chỉnh chỉ số ở lớp tổ chức | 09-02, 09-03, 09-04 | Giữ |
| calibration\_run | Một lần đối chiếu số khi đưa tổ chức vào ngành | 09-02, 09-03, 09-04 | Giữ |
| calibration\_item | Kết quả đối chiếu từng chỉ số hoặc luật | 09-02 | Giữ |
| pack\_upgrade | Một lần nâng cấp gói | 09-04 | Giữ |
| pack\_check\_run | Kết quả kiểm tra một phiên bản gói trước khi phát hành | 09-05 | Giữ |

#### period · Period service

| Bảng | Mục đích | Use case dùng | So với v3.1 |
| --- | --- | --- | --- |
| period\_calendar | Lịch chốt kỳ theo Data Space | 10-02 | Giữ |
| period\_close | Kỳ chốt và trạng thái | 10-01, 10-02, 10-03, 10-04, 10-05 | Giữ |
| period\_snapshot\_file | Tệp dữ liệu của kỳ chốt | 10-01, 10-02, 10-05 | Giữ |
| period\_reconciliation | Kết quả đối soát từng chỉ tiêu khi chốt kỳ | 10-02 | Giữ |
| published\_result | Con số đã công bố, đóng băng | 01-02, 10-01, 10-03, 10-04, 10-05 | Giữ |
| retro\_change | Thay đổi hồi tố phát hiện sau chốt | 01-07, 10-04 | Giữ |

#### pipeline · Pipeline service

| Bảng | Mục đích | Use case dùng | So với v3.1 |
| --- | --- | --- | --- |
| pipeline | Pipeline biến đổi | 06-01, 06-02, 06-03 | Giữ |
| pipeline\_run | Lần chạy pipeline | 06-01, 06-02, 06-03, 06-07 | Giữ |
| lineage\_edge | Cạnh dòng dữ liệu giữa hai đối tượng | 01-02, 02-07, 06-02, 06-05, 06-06 | Giữ |
| data\_mart | Data mart mô hình sao | 06-06 | Giữ |
| data\_product | Sản phẩm dữ liệu có hợp đồng và phiên bản | 06-06 | Giữ |
| writeback\_request | Yêu cầu ghi ngược về nguồn | 06-04 | Giữ |

#### ingest · Ingestion service

| Bảng | Mục đích | Use case dùng | So với v3.1 |
| --- | --- | --- | --- |
| ingestion\_job | Job nạp dữ liệu từ một nguồn | 04-06, 04-07, 04-09 | Giữ |
| job\_run | Một lần chạy job nạp | 04-06, 04-07, 04-09, 06-07 | Giữ |
| dlq\_record | Bản ghi lỗi chờ xử lý | 04-07, 04-09 | Giữ |
| file\_upload | Tệp CSV, Excel tải lên và ánh xạ cột | 04-08 | Giữ |
| quality\_rule | Quy tắc chất lượng trên bảng, cột | 04-08, 06-06, 06-07 | Giữ |
| quality\_result | Kết quả kiểm tra quy tắc | 04-09, 06-07 | Giữ |

#### audit · Audit service (CSDL riêng)

| Bảng | Mục đích | Use case dùng | So với v3.1 |
| --- | --- | --- | --- |
| audit\_log | Nhật ký kiểm toán, chỉ ghi thêm | 01-01, 01-02, 01-06, 02-03, 04-01, 06-04, 07-07, 08-06, 10-02, 10-03, 11-01, 11-02, 11-03, 11-06, 11-08, 12-01, 12-02, 12-04 | Giữ; CSDL riêng, chuỗi băm, phân vùng theo tháng |
| security\_event | Sự kiện bảo mật của bộ lọc AI (chèn lệnh, lộ dữ liệu) | 05-02, 11-03 | Đổi tên từ guard\_event (agent), chuyển sang CSDL audit |
| export\_job | Bản xuất nhật ký kiểm toán có chữ ký số, duyệt người thứ hai khi quá 100.000 dòng (BR-138) | 11-03 | Thêm 12/10/2026; cùng CSDL audit với các dòng nó xuất |

#### acquire · Acquisition service

| Bảng | Mục đích | Use case dùng | So với v3.1 |
| --- | --- | --- | --- |
| crawl\_source | Nguồn thu thập: mục tiêu, URL hạt giống, miền cho phép, lịch, cổng duyệt, mức tin cậy nguồn | 14-01, 14-06 | Mới; thay KBSource của code hiện tại |
| crawl\_schema\_version | Phiên bản schema thu thập: trường, kiểu, đơn vị, khoá, vị trí trích | 14-02, 14-03 | Mới; thay "schema crawler" trong Connector service |
| crawl\_run | Một lượt thu: số trang, số bản ghi, độ chính xác trên mẫu, điểm lệch cấu trúc | 14-04, 14-06 | Mới; thay KBSourceRun |
| crawl\_page | Trang, tệp, phản hồi API đã tải; bản gốc ở MinIO da-raw-crawl | 14-02, 14-06 | Mới; thay KBSourceCrawlQueue |
| extracted\_record | Bản ghi trích từ bản gốc theo một phiên bản schema | 14-03, 14-04 | Mới |
| review\_decision | Quyết định duyệt một bản ghi trích: xác nhận, sửa, bỏ | 14-04 | Mới |
| schema\_drift | Thay đổi cấu trúc nguồn phát hiện được và cách xử lý | 14-03 | Mới |
| entity\_link | Liên kết khoá thực thể của một nguồn với khoá chuẩn | 14-05 | Mới |
| reference\_entity | Danh mục thực thể chuẩn của tổ chức (ngân hàng, tổ chức tín dụng, cơ quan), khoá theo mã NHNN, SWIFT/BIC, mã số thuế (BR-169) | 14-05 | Mới (10/10/2026, lần 4) |
| consolidation\_spec | Đặc tả hợp nhất cho một loại thực thể | 14-05 | Mới |
| consolidation\_run | Một lượt hợp nhất; phát hành dataset hợp nhất phiên bản mới | 14-05 | Mới |

#### forecast · Forecast service

| Bảng | Mục đích | Use case dùng | So với v3.1 |
| --- | --- | --- | --- |
| forecast\_job | Việc dự báo bất đồng bộ từ câu hỏi hoặc lịch | 15-01, 15-02, 15-07 | Mới |
| forecast\_model | Mô hình dự báo theo chỉ số, phạm vi, tần suất | 15-01, 15-04, 15-05, 15-06, 15-08 | Mới |
| model\_version | Phiên bản mô hình: thuật toán, biến, giả định, thẻ mô hình | 15-02, 15-03, 15-04, 15-06, 15-07 | Mới |
| backtest\_run | Kết quả backtest cuốn chiếu so với mô hình chuẩn | 15-03, 15-04 | Mới |
| prediction | Giá trị dự báo theo kỳ kèm khoảng 80% và 95% | 15-01, 15-05, 15-07, 15-08 | Mới |
| scenario | Kịch bản giả định giá trị tương lai của biến ngoại sinh | 15-05 | Mới |
| accuracy\_log | Sai số dự báo so với số thực tế theo kỳ | 07-14, 15-06, 15-08 | Mới |
| formula\_model | Mô hình dự báo theo công thức (giá cơ sở, nowcast, mô hình Quỹ, Monte Carlo) cho một mặt hàng | 15-07, 15-08 | Mới |
| formula\_component | Tham số công thức theo ngày hiệu lực, kèm văn bản gốc và người chứng nhận | 15-07 | Mới |
| ledger\_entry | Sổ dự báo: dự báo đã phát hành, kết quả, chấm điểm, cả kỳ sai | 15-07, 15-08, 08-08 | Mới |

#### exec · Exec vault service

| Bảng | Mục đích | Use case dùng | So với v3.1 |
| --- | --- | --- | --- |
| vault | Kho riêng của một lãnh đạo: khoá công khai niêm phong, khoá riêng đã bọc, tên khoá Transit, chính sách rời vị trí | 16-01, 16-02, 16-06 | Mới |
| vault\_key\_wrap | Khoá kho bọc bằng passkey PRF, khoá phần cứng hoặc mã khôi phục giấy | 16-01, 16-06 | Mới |
| thesis | Luận điểm: nội dung bản mã, điều kiện chấm bản mã Transit, ngày chấm, trạng thái | 16-01, 16-03, 16-04, 16-05 | Mới |
| thesis\_evidence | Lô bằng chứng hai phía do Agent Bằng chứng tìm mù, niêm phong cho chủ kho | 16-03 | Mới |
| thesis\_score | Điểm chấm, chỉ ghi thêm | 16-04 | Mới |
| memory\_item | Ưu tiên, lý do quyết định, câu hỏi đã hỏi, bản chụp câu trả lời, ghi chú | 16-02, 16-06 | Mới |
| key\_access\_log | Sổ truy cập khoá, chỉ ghi thêm | 16-01, 16-03, 16-04, 16-06 | Mới |
| handover | Niêm phong, bàn giao có chọn lọc, huỷ khoá | 16-06 | Mới |
| handover\_item | Mục bàn giao đang trung chuyển | 16-06 | Mới |
| publication | Bản công bố bản rõ, nguồn của trang tri thức loại giả thuyết | 16-05 | Mới |

## D4. Chi tiết bảng

D4 chuyển sang tab 3b, xem 3b · Chi tiết bảng (D4) (từ 10/10/2026). Tab 3b chứa chi tiết cột, ràng buộc, chỉ mục và trigger của từng bảng theo schema (D4.1–D4.24), giữ nguyên số mục.

## D5. DDL triển khai và kiểm chứng

### D5.1 Tệp DDL

Từ 10/10/2026 hai tệp DDL sinh tự động từ csdl/definitions.json bằng csdl/gen\_ddl.py; definitions.json dựng từ D2, D3, D4, D5.2 bằng csdl/parse\_d4.py (thay bộ sinh cũ d5.py đã mất). Không sửa tay tệp SQL: sửa D4 hoặc định nghĩa rồi sinh lại. Logic trigger lấy từ hai migration đã kiểm, đặt ở csdl/source/routines.

| Tệp | CSDL | Nội dung |
| --- | --- | --- |
| dataagent.sql | dataagent | Sinh bằng csdl/gen\_ddl.py từ csdl/definitions.json (10/10/2026), đã gồm P14, P15 và lớp tri thức: 23 schema nghiệp vụ, 194 bảng, 23 bảng outbox, 2.999 cột, 203 khoá ngoại, 663 CHECK, 106 UNIQUE, 198 chỉ mục, 7 bảng phân vùng tháng có phân vùng mặc định, 64 trigger, 79 hàm, RLS trên 217 bảng (239 chính sách; 11 bảng exec FORCE RLS với chính sách hạn chế chỉ chủ kho), vai trò svc\_\<schema>, \<schema>\_read, svc\_exec\_worker và quyền đọc chéo D5.2 (sinh lại 11/10/2026 từ tab 3 và tab 3b, gồm lượt 4 và schema exec) |
| dataagent\_audit.sql | dataagent\_audit | Sinh cùng lúc bằng gen\_ddl.py: schema audit (2 bảng, audit\_log phân vùng tháng), trigger chuỗi băm audit.chain\_hash, hàm audit.verify\_chain, RLS, quyền chỉ ghi thêm (svc\_audit chỉ SELECT, INSERT trên audit\_log) |
| dataagent\_p14\_p15.sql | dataagent | Migration tăng thêm 10/10/2026: schema acquire (10 bảng) và forecast (7 bảng), 2 bảng outbox, ràng buộc, chỉ mục, 12 trigger quy tắc nghiệp vụ, RLS, vai trò và quyền; sửa ràng buộc catalog.dataset, catalog.dataset\_version (thêm created\_by, certified\_at, certified\_by), thêm bảng catalog.dataset\_version\_certification (chỉ ghi thêm), qa.answer (thêm forecast\_job\_id), inbox.hitl\_request. Viết tay; từ 10/10/2026 đã nhập vào definitions.json và dataagent.sql. Giữ làm lịch sử và để nâng cấp bản cài theo DDL ngày 09/10/2026; bản cài mới không chạy tệp này. Lần 4 (10/10/2026): thêm acquire.reference\_entity; cột duyệt pháp lý ở crawl\_source, value\_rules, drift\_kind, reference\_entity\_id, n\_points, n\_exog, pooled\_series, in\_sample\_mase, exog\_uncertainty, qa.answer.number\_check; trigger crawl\_source\_legal\_guard, crawl\_run\_legal\_guard, entity\_link\_reference\_sync, prediction\_exog\_guard; hitl kind legal\_review |
| dataagent\_knowledge.sql | dataagent | Migration tăng thêm 10/10/2026 (lớp tri thức biên soạn): 8 bảng knowledge\_ trong schema context, ràng buộc, chỉ mục, 10 trigger và 4 hàm quy tắc nghiệp vụ (BR-161 đến BR-167), RLS theo tổ chức và chính sách hạn chế theo quyền người đọc, sự kiện vào context.outbox, quyền chỉ ghi thêm; thêm kind knowledge\_page cho inbox.proposal và knowledge\_finding cho inbox.hitl\_request (nối vào danh sách đang có). Chạy sau dataagent\_p14\_p15.sql. Viết tay; từ 10/10/2026 đã nhập vào definitions.json và dataagent.sql. Giữ làm lịch sử và để nâng cấp bản cài cũ (chạy sau dataagent\_p14\_p15.sql); bản cài mới không chạy tệp này |
| data\_dictionary.md | — | Từ điển dữ liệu sinh cùng lúc với hai tệp DDL từ definitions.json (ADR-12), để đối chiếu với D4: 24 schema, 196 bảng, mỗi bảng có cột, kiểu, bắt buộc, mặc định, khoá, tham chiếu logic, CHECK, chỉ mục, trigger, quyền |
| csdl/definitions.json | — | Tệp định nghĩa duy nhất cho DDL, từ điển và sơ đồ D6: 196 bảng, 3.029 cột (kể cả cột chung), hàm và trigger. Dựng bằng parse\_d4.py; parse\_report.md ghi đối chiếu số bảng D2, D3, D4 (khớp cả 24 schema; từ 11/10/2026 đọc D4 ở tab 3b, D2, D3, D5.2 ở tab 3 qua bản xuất markdown và source/md2blocks.py) và các chỗ D4 viết bằng lời đã diễn giải |

Thứ tự cài: tạo hai CSDL, chạy hai tệp bằng vai trò sở hữu (da\_owner), tạo tài khoản đăng nhập cho từng dịch vụ và gán vai trò svc\_ tương ứng. Hằng tháng, job lưu trữ gọi public.da\_ensure\_month\_partitions(3) để tạo trước phân vùng ba tháng tới; phân vùng quá hạn theo governance.retention\_policy được xuất sang MinIO rồi gỡ.

Bản cài mới chạy dataagent.sql và dataagent\_audit.sql bằng tài khoản quản trị cụm, mỗi tệp một giao dịch: tệp tạo vai trò còn thiếu, phần bảng chạy dưới da\_owner, cuối tệp tạo phân vùng tháng hiện tại và 3 tháng tới. Bản cài theo DDL 09/10/2026 nâng cấp bằng dataagent\_p14\_p15.sql rồi dataagent\_knowledge.sql; hai migration này không gồm trigger agent\_run\_depth\_guard và cột agent mới (stop\_reason, stop\_detail, call\_fingerprint, result\_hash), cần một migration nhỏ riêng khi nâng cấp.

### D5.2 Vai trò CSDL

Mỗi schema có hai vai trò. svc\_\<schema> là vai trò dịch vụ sở hữu dùng để kết nối: đọc, ghi schema của mình. \<schema>\_read chỉ có quyền đọc các bảng của schema đó (trừ outbox) và được cấp cho dịch vụ khác cần đọc. Không vai trò dịch vụ nào là chủ bảng, nên RLS luôn áp; mỗi kết nối phải đặt app.org\_id trước khi truy vấn.

Ngoại lệ chỉ ghi thêm: svc\_period không có UPDATE, DELETE trên period.published\_result (BR-42); svc\_audit chỉ có SELECT, INSERT trên audit.audit\_log (BR-47); svc\_acquire không có UPDATE, DELETE trên acquire.crawl\_page (BR-150); svc\_forecast không có UPDATE, DELETE trên forecast.prediction; svc\_catalog chỉ có SELECT, INSERT trên catalog.dataset\_version\_certification (BR-156); svc\_context không có DELETE trên 8 bảng knowledge\_, chỉ có SELECT, INSERT trên knowledge\_claim, knowledge\_figure, knowledge\_link và knowledge\_source\_link (trừ UPDATE bốn cột quyền) (BR-164). Lượt 4 (10/10/2026): svc\_qa chỉ có SELECT, INSERT, UPDATE (sealed\_at) trên qa.answer\_receipt và SELECT, INSERT trên qa.answer\_receipt\_figure, DELETE chỉ khi quá retain\_until (BR-176); svc\_forecast không có DELETE trên forecast.ledger\_entry (BR-185); svc\_governance chỉ UPDATE bốn cột kết quả của governance.model\_route\_decision, không có DELETE (BR-181). Schema exec (P16) theo D4.24: mọi bảng FORCE ROW LEVEL SECURITY và chính sách hạn chế chỉ chủ kho; svc\_exec\_worker là vai trò thứ ba của schema, chỉ có quyền cột và INSERT trên thesis\_evidence, thesis\_score, key\_access\_log; exec\_read chỉ đọc exec.publication; thesis\_evidence, thesis\_score, key\_access\_log không vai trò nào có UPDATE, DELETE; không vai trò quản trị nào đọc được nội dung (BR-189).

| Vai trò dịch vụ | Được đọc thêm các schema |
| --- | --- |
| svc\_qa | context, catalog, governance, org, period, document |
| svc\_query | catalog, context, governance, org, period |
| svc\_agent | context, catalog, skill, governance, org, qa |
| svc\_context | catalog, pack, org, document, conflict, qa, governance (ba schema sau cho lớp tri thức biên soạn, dataagent\_knowledge.sql), exec (chỉ exec.publication qua exec\_read, nguồn trang hypothesis, BR-194) |
| svc\_conflict | catalog, document, period |
| svc\_period | catalog, context, org |
| svc\_pack | catalog, context, org |
| svc\_monitor | context, org, workspace |
| svc\_notify | org, inbox |
| svc\_inbox | org, eval |
| svc\_eval | context, agent, skill, org |
| svc\_governance | org, catalog |
| svc\_integration | org, governance |
| svc\_workspace | org, query, context |
| svc\_skill | context, org, query |
| svc\_document | org, catalog |
| svc\_pipeline | catalog, org |
| svc\_ingest | catalog, org |
| svc\_acquire | catalog, context, governance, org, pack |
| svc\_forecast | acquire, catalog, context, org, query, skill |
| svc\_catalog | org |
| svc\_org | — |
| svc\_exec | — (chỉ schema exec; chính sách chủ kho, D4.24) |
| svc\_exec\_worker | — (quyền cột trên exec.vault, exec.thesis và INSERT ba bảng chỉ ghi thêm khi app.exec\_workflow = 'on', D4.24) |

Lý do đọc chéo của hai dịch vụ mới. svc\_acquire: catalog cho dataset đích origin = consolidated; context và pack cho thuộc tính chuẩn mà fields.canonical\_attr trỏ tới; governance cho retention\_policy (retain\_until) và chính sách dữ liệu nguồn ngoài; org cho Data Space và người phụ trách. svc\_forecast: acquire cho mức tin cậy và nhãn nguồn ngoài của chuỗi hợp nhất làm biến ngoại sinh (BR-156); skill cho external\_observation; catalog và query cho data\_snapshot\_refs; context cho metric\_code; org. Chưa dịch vụ cũ nào được cấp acquire\_read, forecast\_read.

Ghi sang schema khác luôn qua API hoặc sự kiện Kafka của dịch vụ sở hữu, không qua CSDL.

Khối triển khai (tab 6 ADR-28, O7.4) dùng tập vai trò của các module nó chứa; mỗi module mở pool kết nối riêng bằng svc\_\<schema> của mình, kể cả khi nằm chung khối. api dùng svc\_ của cả 23 schema; worker-ingest dùng svc\_ingest, svc\_pipeline, svc\_document, svc\_conflict, svc\_catalog, svc\_context; worker-agent dùng svc\_agent, svc\_qa, svc\_query, svc\_period, svc\_eval, svc\_monitor, svc\_inbox, svc\_notify, svc\_exec\_worker (workflow chấm luận điểm trên Temporal); crawler dùng svc\_acquire; ml-forecast dùng svc\_forecast. Một vai trò có thể dùng ở cả api và worker của cùng module. Tách module thành dịch vụ riêng chỉ chuyển vai trò sang dịch vụ đó, không đổi quyền.

### D5.3 Outbox, phân vùng, chuỗi băm

Mỗi schema có bảng outbox (id, org\_id, aggregate\_type, aggregate\_id, event\_type, payload, created\_at, published\_at) ghi trong cùng giao dịch với thay đổi nghiệp vụ; Debezium đọc và đẩy sang Kafka theo mục 9.3.

Bảng phân vùng theo tháng: governance.model\_usage, integration.webhook\_delivery, integration.usage\_counter, query.query\_run, agent.agent\_step, notify.notification, governance.model\_route\_decision (thêm 11/10/2026), audit.audit\_log. Mỗi bảng có phân vùng mặc định để không mất dòng khi chưa kịp tạo phân vùng tháng. acquire và forecast không có bảng phân vùng: crawl\_page và extracted\_record là đích khoá ngoại nên giữ bảng thường, dọn theo retain\_until.

Trigger audit.chain\_hash tự gán seq tăng dần theo tổ chức, prev\_hash là hash dòng trước và hash là SHA-256 của nội dung dòng; khoá tư vấn theo tổ chức để hai lượt ghi không chèn song song. Hàm audit.verify\_chain(org, từ seq) trả về seq đầu tiên bị đứt, phục vụ nút Kiểm toàn vẹn ở UC-11-03.

### D5.4 Kết quả kiểm chứng

Chạy trên PostgreSQL 16 ngày 09/10/2026: cả hai tệp chạy không lỗi. Các phép thử dưới đây đều cho kết quả đúng. Ngày 10/10/2026, dataagent\_p14\_p15.sql chạy không lỗi trên PostgreSQL 16.15, sau tệp giả lập phần phụ thuộc của 20 schema cũ (stub\_dependencies.sql); 41 phép thử P14, P15 đều đúng. Chạy lại cùng ngày sau khi thêm backtest\_run.frequency, prediction.interval\_method và chứng nhận catalog.dataset\_version: 50 phép thử đều đúng. Chạy lại lần cuối sau khi thêm người sửa đặc tả gần nhất (last\_edited\_by, spec\_editor\_id) và bảng lịch sử chứng nhận: 59 phép thử (13 hàng cuối bảng) đều đúng. Chạy lại sau bổ sung điểm yếu kỹ thuật (lần 4: duyệt pháp lý nguồn, lệch ngầm, danh mục thực thể chuẩn, dự báo ít dữ liệu, sai số biến ngoại sinh, kiểm số câu trả lời): 100 phép thử đều đúng, gồm 41 phép thử mới ở 5 hàng cuối bảng; dữ liệu mẫu của phép thử cũ thêm duyệt pháp lý cho nguồn, n\_points cho phiên bản và in\_sample\_mase cho backtest (test\_p14\_p15\_result.txt).

Lớp tri thức biên soạn (10/10/2026): dataagent\_knowledge.sql chạy không lỗi trên PostgreSQL 16.15 trong CSDL thử riêng dataagent\_kn, theo thứ tự stub\_dependencies.sql, dataagent\_p14\_p15.sql (bản lần 4), stub\_knowledge.sql, dataagent\_knowledge.sql; 46 phép thử của test\_knowledge.py (5 hàng cuối bảng) đều đúng (test\_knowledge\_result.txt). Cột ở D4.5 đã đối chiếu với information\_schema: 139 cột, không thiếu, không thừa, bắt buộc khớp NOT NULL.

Chạy đầy đủ ngày 10/10/2026 (không stub): dataagent.sql và dataagent\_audit.sql sinh bằng gen\_ddl.py chạy không lỗi trên PostgreSQL 16.15 trong hai CSDL mới dataagent\_full, dataagent\_audit\_full. test\_full.py chạy 198 phép thử, 0 lỗi (csdl/test\_full\_result.txt): 100 phép thử P14, P15 và 46 phép thử lớp tri thức chuyển nguyên văn sang DDL đầy đủ (câu INSERT vào bảng cũ viết theo stub được tự điền cột bắt buộc); 43 phép thử D5.4 cho 20 schema cũ và cột agent mới; 5 phép thử audit; 4 phép đếm. Đếm ở dataagent\_full: 22 schema, 169 bảng, 22 outbox, 2.492 cột, 173 khoá ngoại, 454 CHECK, 146 chỉ mục, 203 chính sách RLS, 27 trigger; dataagent\_audit\_full: 2 bảng, 30 cột, 5 CHECK, 4 chỉ mục, 2 chính sách, 1 trigger. Đối chiếu với CSDL dựng từ hai migration: 470 cột, 25 trigger, 53 chính sách và quyền trùng khớp; khác 7 chỉ mục bảng knowledge\_ (D4 ghi chỉ mục toàn phần, migration dùng chỉ mục có WHERE cột IS NOT NULL).

Lượt 4 (11/10/2026, BR-175 đến BR-195): parse\_d4.py đọc D4 từ tab 3b, gen\_ddl.py sinh DDL đủ 196 bảng và schema exec; dataagent.sql, dataagent\_audit.sql chạy không lỗi trên PostgreSQL 16 trong hai CSDL mới dataagent\_full2, dataagent\_audit\_full2. test\_full.py chạy 301 phép thử, 0 lỗi (chạy lại 11/10/2026 sau khi sửa trigger Error Book theo D4: fixed không về open): giữ 198 phép thử cũ (phép đếm đổi thành 23 schema, 194 bảng, 23 outbox) và thêm 103 phép thử ở test\_round4.py (5 hàng cuối bảng). Đếm ở dataagent\_full2: 23 schema, 194 bảng, 23 outbox, 2.999 cột, 203 khoá ngoại, 663 CHECK, 198 chỉ mục, 239 chính sách RLS, 64 trigger, 79 hàm. Trigger mới ở source/routines/44\_round4\_triggers.sql, 45\_exec\_triggers.sql, chính sách exec ở 81\_exec\_policies.sql; cách diễn giải ghi ở parse\_report.md.

| Nhóm | Phép thử | Kết quả |
| --- | --- | --- |
| Quy tắc nghiệp vụ | Agent thiếu chủ sở hữu (BR-147), tài khoản khẩn cấp thiếu hạn (BR-44), bật chính sách chưa mô phỏng | Bị chặn |
| Quy tắc nghiệp vụ | Client hạn quá 12 tháng (BR-144), hai lượt quét chạy chồng (BR-103), bảng dẫn xuất gắn nguồn | Bị chặn |
| Quy tắc nghiệp vụ | Hai phiên bản chỉ số cùng chứng nhận, câu hỏi chế độ at thiếu mốc, kế hoạch không thuộc câu hỏi hay lượt chạy | Bị chặn |
| Quy tắc nghiệp vụ | Lượt chạy con sâu quá 3 tầng (BR-77), công cụ gửi ra ngoài không checkpoint (BR-70), từ chối đề xuất không lý do | Bị chặn |
| Quy tắc nghiệp vụ | Đóng cảnh báo lặp thiếu ghi chú (BR-124), tắt kênh trong ứng dụng cho yêu cầu duyệt (BR-127), phát hành báo cáo chưa duyệt | Bị chặn |
| Quy tắc nghiệp vụ | Trang OCR dưới 0,85 dùng được (BR-19), đối soát “khớp” khi vượt dung sai, số nhập tay thiếu duyệt (BR-133) | Bị chặn |
| Quy tắc nghiệp vụ | Ghi ngược có dòng xoá (BR-113), người duyệt trùng người yêu cầu (BR-24), chỉ số gói thiếu căn cứ, áp nâng cấp còn xung đột | Bị chặn |
| Cô lập tổ chức | Đặt app.org\_id sang tổ chức khác rồi đọc | Không thấy dòng nào |
| Quyền dịch vụ | svc\_qa đọc context.metric / ghi context.metric | Đọc được / bị từ chối |
| Chỉ ghi thêm | svc\_period sửa published\_result; svc\_audit sửa audit\_log | Bị từ chối |
| Chuỗi băm | Ghi 3 dòng, kiểm, sửa dòng 2, kiểm lại | Seq 1, 2, 3; đạt; báo đứt tại seq 2 |
| Quy tắc nghiệp vụ P14 | Schema active khi chạy thử 93% hoặc thiếu người duyệt (BR-151), phát hành lượt trích bằng schema draft, thay đổi nặng mà tự ánh xạ lại (BR-152) | Bị chặn |
| Quy tắc nghiệp vụ P14 | Phát hành lượt còn bản ghi flagged, hợp nhất nhận lượt chưa phát hành, bỏ bản ghi không lý do (BR-153), bật cổng tự động khi mới có 3 lượt mẫu ≥ 98% | Bị chặn |
| Quy tắc nghiệp vụ P15 | Phát hành phiên bản có MASE 1,05, người tạo tự duyệt, phát hành thiếu người duyệt, current\_version trỏ phiên bản chưa phát hành, dự báo từ phiên bản chưa duyệt không gắn is\_experimental (BR-158) | Bị chặn |
| Quy tắc nghiệp vụ P15 | Chuyển pending\_approval sang degraded, phiên bản degraded khi mô hình còn active (BR-160), khoảng 80% rộng hơn khoảng 95% | Bị chặn |
| Quy tắc nghiệp vụ P15 | Backtest chuỗi tháng 5 điểm cắt, chuỗi tuần 6 điểm cắt, backtest ghi tần suất khác mô hình, dự báo có interval\_method ngoài danh sách (ADR-15) | Bị chặn |
| Quy tắc nghiệp vụ P14 | Người tạo tự chứng nhận phiên bản dataset hợp nhất; người sửa đặc tả gần nhất chứng nhận phiên bản do lượt hợp nhất phát hành; ghi thẳng certified\_at, certified\_by không qua bảng lịch sử; chứng nhận phiên bản dataset origin source; phiên bản hợp nhất thiếu created\_by; chứng nhận lần hai khi đang chứng nhận; thu hồi thiếu lý do hoặc lý do chỉ có khoảng trắng (BR-156) | Bị chặn |
| Quy tắc nghiệp vụ P14, P15 | Đường hợp lệ: schema 97% có người duyệt, phát hành lượt sau khi duyệt hết, cổng tự động sau 4 lượt ≥ 98%, phát hành MASE 0,82 do người khác người tạo duyệt, phiên bản và mô hình cùng chuyển degraded, backtest chuỗi tuần 20 điểm cắt (frequency tự điền), dự báo gắn conformal\_pooled và parametric, người phụ trách khác người tạo chứng nhận phiên bản hợp nhất (có sự kiện outbox); người kích hoạt đặc tả (updated\_by cuối) chứng nhận phiên bản có created\_by là người sửa đặc tả gần nhất; thu hồi có lý do ghi lịch sử, phát sự kiện thu hồi, rồi chứng nhận lại | Chạy được |
| Cô lập tổ chức | svc\_acquire, svc\_forecast đặt app.org\_id sang tổ chức khác rồi đọc / ghi dòng mang org\_id của tổ chức khác | Không thấy dòng nào / bị chặn |
| Quyền dịch vụ | svc\_acquire đọc context.metric / ghi context.metric, đọc forecast.prediction; svc\_forecast đọc acquire.crawl\_source, skill.external\_observation / ghi acquire.crawl\_source, đọc acquire.outbox | Đọc được / bị từ chối |
| Chỉ ghi thêm | svc\_acquire sửa, xoá crawl\_page; svc\_forecast sửa prediction; svc\_catalog sửa, xoá dataset\_version\_certification | Bị từ chối |
| Bảng sẵn có | qa.answer kind forecast thiếu forecast\_job\_id / catalog.dataset origin consolidated, inbox.hitl\_request kind data\_review | Bị chặn / chạy được |
| Quy tắc nghiệp vụ P14 (lệch ngầm) | Lệch ngầm với action paused, thiếu bằng chứng, mức nhẹ; phát hành lượt (và lượt sau) khi nguồn còn lệch ngầm mở; đóng lệch ngầm không ghi người; false\_alarm cho thay đổi cấu trúc; schema active có trường phần trăm chưa khai miền, min lớn hơn max (BR-168). Đúng khi: người xác nhận báo nhầm rồi phát hành; schema có miền 0–15 %/năm | Bị chặn / chạy được |
| Quy tắc nghiệp vụ P14 (thực thể chuẩn) | Thực thể không có mã NHNN, SWIFT/BIC, mã số thuế; BIC sai định dạng; nhập tay chưa xác minh; khớp mờ 0,91 tự xác nhận; liên kết xác nhận không trỏ danh mục; khác loại thực thể (BR-169). Đúng khi: khớp theo mã chép canonical\_key; người xác nhận khớp mờ 0,82 | Bị chặn / chạy được |
| Quy tắc nghiệp vụ P14 (pháp lý nguồn) | Bật nguồn chưa duyệt; người phụ trách tự duyệt; duyệt không bằng chứng; hạn xem lại 200 ngày; tải khi duyệt hết hiệu lực; bật lại khi quá hạn (BR-174). Đúng khi: điều khoản đổi thì duyệt hết hiệu lực và nguồn tự tạm ngưng; tải khi duyệt còn hiệu lực; hitl kind legal\_review | Bị chặn / chạy được |
| Quy tắc nghiệp vụ P15 (ít dữ liệu) | LightGBM một chuỗi 40 điểm; SARIMAX 30 điểm với 3 biến; chuỗi 20 điểm; phát hành khi MASE backtest gấp 3 lần trong mẫu; phát hành thiếu n\_points (BR-172); dự báo gốc có biến ngoại sinh không mô phỏng sai số biến; kịch bản không ghi scenario\_fixed (BR-173). Đúng khi: LightGBM 40 điểm gộp 5 chuỗi; dự báo gốc simulated; kịch bản scenario\_fixed | Bị chặn / chạy được |
| Kiểm số câu trả lời | Lưu qa.answer còn 1 số không truy được (BR-171). Đúng khi: đã bỏ câu chứa số không truy được | Bị chặn / chạy được |
| Lớp tri thức (nguồn, số) | Thân trang viết tay “4,85%”; khẳng định viết “tăng 0,12 điểm”; gửi duyệt khi chỗ giữ số chưa ràng buộc; chỗ giữ số sai mẫu (BR-162); khẳng định trỏ nguồn của phiên bản khác; gửi duyệt chưa có khẳng định; {{claim:c2}} không có khẳng định; hiệu lực đến trước hiệu lực từ (BR-161) | Bị chặn |
| Lớp tri thức (phiên bản, chứng nhận) | Tạo phiên bản reviewed; sửa thân phiên bản; người tạo tự duyệt; draft sang thẳng reviewed; hai bản nháp mở; thêm khẳng định vào bản đã duyệt; xoá phiên bản, sửa khẳng định; người duyệt tự chứng nhận (BR-164); chứng nhận khi dataset ngoài chưa chứng nhận (BR-165) | Bị chặn / bị từ chối |
| Lớp tri thức (quyền người đọc) | svc\_qa chỉ có Data Space Nguồn vốn đọc trang có nguồn Khối Treasury; không đặt quyền người đọc; đọc bản nháp; nguồn chuyển confidential với clearance 1; trang đang chờ tính lại quyền; svc\_qa ghi trang (BR-163) | Không thấy dòng nào / bị từ chối |
| Lớp tri thức (lượt, rà soát, lưu ngược) | Lượt giới hạn 1 trang sửa trang thứ hai; ghi vào lượt đã kết thúc; bỏ qua phát hiện không lý do; báo lại phát hiện đã bỏ qua (BR-166); hành động ngoài tập cố định; trang về cá nhân khách hàng; lưu ngược thiếu câu trả lời hoặc không trích câu trả lời làm nguồn (BR-167); liên kết tới trang chưa có; tạo phiên bản cho trang đã ngừng | Bị chặn |
| Lớp tri thức (đường hợp lệ) | Nháp, gửi duyệt, duyệt: trang phục vụ phiên bản, tính chính sách, ghi outbox; chứng nhận khi dataset ngoài đã chứng nhận rồi thu hồi làm trang tạm mất chứng nhận; thêm nguồn Khối Treasury làm trang hẹp quyền, phiên bản cũ superseded vẫn đọc được khi đủ quyền; nguồn đổi phân loại thì tính lại; mark\_stale; lưu ngược hợp lệ; liên kết có dòng knowledge\_link; kind mới và kind P14 (data\_review, legal\_review) vẫn nhận; cô lập tổ chức | Chạy được |
| DDL đầy đủ (10/10/2026) | Lượt con thứ tư dưới chuỗi 3 tầng; lượt con khai depth 0 thì trigger tính lại thành depth lượt cha + 1 (BR-77); lượt succeeded thiếu stop\_reason; stop\_reason ngoài danh sách; ghi 3 bước cùng call\_fingerprint, result\_hash rồi dừng loop\_detected kèm stop\_detail (BR-170); bước tháng này vào phân vùng tháng, mốc 2020 vào phân vùng mặc định | Bị chặn / chạy được |
| DDL đầy đủ (10/10/2026) | Đủ 22 phép chặn ở hàng Quy tắc nghiệp vụ trên (BR-147, BR-44, BR-144, BR-103, BR-70, BR-124, BR-127, BR-19, BR-133, BR-113, BR-24…) chạy lại trên schema thật; svc\_qa đọc period, không đọc pipeline, không đọc context.outbox, knowledge\_run; svc\_audit sửa, xoá audit\_log; người quản trị sửa dòng seq 2 thì verify\_chain trả 2 | Bị chặn / đúng |
| Lượt 4 · provider, định tuyến (BR-180, BR-181) | Bật provider frontier khi chưa cam kết không lưu dữ liệu, người bật tự duyệt, frontier nhận mức internal; frontier\_allowed khi ngữ cảnh có nhãn internal trong phạm vi, còn dữ liệu cá nhân, gọi provider tại chỗ, tác vụ chưa bật; on\_prem\_default gọi frontier; frontier\_blocked không lý do; mã băm prompt sai; điền kết quả lần hai, sửa quyết định, xoá dòng. Đúng khi: câu ngoài phạm vi nhãn public gọi frontier đã bật, bị chặn sang tại chỗ cũng ghi; đổi endpoint thì provider tự tắt và phát sự kiện | Bị chặn / chạy được |
| Lượt 4 · data product, biên nhận (BR-175 đến BR-178) | Kích hoạt khi chỉ số chưa certified, người tạo tự kích hoạt, dưới 10 câu mẫu; dataset primary không cam kết độ tươi; thành phần public chưa xác nhận; gỡ thành phần primary, đổi code của bản active; biên nhận cho câu ngoài phạm vi, khoá ngay khi tạo, số hiển thị khi quá hạn, số che thiếu ghi chú; thêm số sau khi khoá, khoá lần hai, sửa biên nhận hoặc số (kể cả quản trị CSDL), xoá khi còn hạn; chạy lại identical mà có số khác. Đúng khi: kích hoạt hợp lệ, đổi cam kết thì version tăng, phân loại tính lại, chỉ số mất chứng nhận thì suspended; biên nhận BN-20260702-000001 khoá có SHA-256; chạy lại differs phát qa.receipt\_replay.mismatch | Bị chặn / chạy được |
| Lượt 4 · Error Book, lịch sự kiện (BR-179, BR-187) | Mục forecast\_miss thiếu accuracy\_log, cùng accuracy\_log tạo mục thứ hai, open sang thẳng regression\_added, fixed thiếu nguyên nhân; kỳ đã diễn ra về confirmed, theo dõi không có kênh trong ứng dụng, bản tin sent không có dòng sổ. Đúng khi: mục từ sự kiện forecast.ledger.missed cấp EB-0001 và phát eval.error\_book.opened; kỳ 12/11/2026 15:00 cho giờ bản tin 11/11 20:00, dời kỳ thì ghi shifted\_from và tính lại | Bị chặn / chạy được |
| Lượt 4 · dự báo theo công thức, sổ dự báo (BR-182 đến BR-186) | formula\_model gắn phiên bản ets, phiên bản formula\_mc gửi duyệt thiếu formula\_model; tham số ghi certified ngay, người nhập tự chứng nhận, thuế suất 120%, sửa giá trị đã chứng nhận, bản mới có valid\_from trước bản đang hiệu lực; dòng sổ ghi sau khi kỳ bắt đầu, sửa trung vị, xoá dòng, ngoài 95% mà miss = false, đổi error\_book\_entry\_id đã ghi, đưa dòng đã chấm về open. Đúng khi: tham số chưa chứng nhận không có hiệu lực, người khác chứng nhận thì có hiệu lực từ valid\_from (2,15% tại 02/7/2026), bản mới 16/7/2026 đặt valid\_to cho bản cũ; chấm kỳ ngoài 95% phát forecast.ledger.scored và forecast.ledger.missed | Bị chặn / chạy được |
| Lượt 4 · lớp riêng lãnh đạo (BR-188 đến BR-195) | Luận điểm khi kho chỉ có 1 khoá bọc, ghi vào kho người khác, ngày chấm không tròn giờ hoặc dưới 1 ngày, sửa bản mã; người khác chủ kho, vai trò quản trị (app.exec\_admin), chủ bảng da\_owner, svc\_context (exec\_read), svc\_exec\_worker chưa bật cờ đọc luận điểm, bộ nhớ, khoá bọc; worker đọc bản mã nội dung; chấm final khi chưa scoring hoặc lần hai; sửa, xoá điểm chấm và sổ truy cập khoá (kể cả quản trị CSDL); lô bằng chứng một phía hoặc nhảy số; giải mã điều kiện lần 4 trong 24 giờ; thu hồi khoá bọc còn 1; sửa mục bộ nhớ; công bố có số viết tay; mở lại kho đã huỷ khoá; huỷ khoá thiếu AC06 duyệt; cô lập tổ chức. Đúng khi: worker đọc điều kiện, chấm final thì luận điểm scored; lô hai phía thì two\_sided; tóm tắt kho cho quản trị chỉ 5 cột; huỷ mục bộ nhớ; svc\_context đọc bản công bố chờ duyệt; hoàn tất huỷ khoá xoá khoá bọc và phát exec.vault.shred\_requested | Bị chặn, không thấy dòng / chạy được |

## D6. Sơ đồ

Các sơ đồ D6.1 đến D6.11 sinh tự động từ cùng tệp định nghĩa với DDL (csdl/definitions.json, chạy csdl/erd/gen\_erd.py, Graphviz), nên luôn khớp D4 và DDL; sinh lại ngày 11/10/2026 (thêm D6.10, D6.11). Trong ERD, mỗi hộp bảng chỉ ghi khoá, cột tham chiếu và cột trạng thái; nét liền là khoá ngoại trong cùng schema, nét đứt là tham chiếu logic (sang schema khác, hoặc tới bảng phân vùng không đặt được khoá ngoại). Khoá ngoại khép vòng (DEFERRABLE) và tự tham chiếu vẽ không ràng buộc thứ bậc. Ba script vẽ tay cũ (erd\_d6\_1\_schemas.py, erd\_d6\_3\_nhom2.py, erd\_nhom7\_acquire\_forecast.dot) không còn dùng.

### D6.1 Tham chiếu giữa các schema

&#91;image: Hình D6.1 Tham chiếu logic giữa 24 schema; không vẽ tham chiếu tới org vì mọi schema đều dùng\]

inbox và catalog là hai điểm được nhiều schema tham chiếu nhất: mọi dịch vụ tạo yêu cầu cho người qua inbox, và mọi dữ liệu nghiệp vụ trỏ về bảng trong catalog. Hai dịch vụ này cần ổn định giao diện đọc (vai trò inbox\_read, catalog\_read) trước các dịch vụ khác.

Hình D6.1 đã vẽ lại cho 23 schema (10/10/2026): nét xanh là cột tham chiếu, số trên mũi tên là số cột; nét cam là tham chiếu không qua cột. Tham chiếu logic của hai schema mới: acquire → catalog (dataset, dataset\_version), agent (agent\_run), conflict (conflict, claim, ghi qua API); forecast → qa (question), context (metric), agent (agent\_run), catalog và query (data\_snapshot\_refs), acquire và skill (biến ngoại sinh); qa.answer → forecast (forecast\_job\_id); inbox → acquire, forecast (yêu cầu Cần bạn). Hình D6.3 (nhóm 2) đã gồm catalog.dataset\_version\_certification với khoá ngoại tới catalog.dataset\_version; 8 bảng context.knowledge\_\* tách sang Hình D6.9. Hình D6.1 thêm nét cam context → document, conflict (nguồn trang tri thức) và nét context → qa, agent (source\_answer\_id, agent\_run\_id); 58 cặp schema, 80 cột tham chiếu. Ngày 11/10/2026 Hình D6.1 vẽ lại cho 24 schema (thêm exec): 77 cặp schema, 115 cột tham chiếu; nét cam thêm context → exec (nguồn trang hypothesis), eval → forecast và eval → qa (sự kiện tạo mục Error Book); mọi cạnh tham gia xếp tầng để hình đọc từ trên xuống. Bảng lượt 4 của monitor vẽ ở Hình D6.5, của forecast ở Hình D6.8; của context, qa, eval, governance ở Hình D6.10.

### D6.2 ERD theo nhóm

&#91;image: Hình D6.2 Nhóm 1: tổ chức, quản trị, tích hợp (bảng lượt 4 của governance ở Hình D6.10)\]

&#91;image: Hình D6.3 Nhóm 2: danh mục, lớp ngữ cảnh, hỏi đáp, truy vấn (không gồm 8 bảng context.knowledge\_\* và 5 bảng data product, biên nhận)\]

&#91;image: Hình D6.4 Nhóm 3: agent, đánh giá, Cần bạn (eval.error\_book\_entry ở Hình D6.10)\]

&#91;image: Hình D6.5 Nhóm 4: phiên làm việc, kỹ năng, theo dõi, thông báo (gồm lịch sự kiện ngoài và bản tin theo kỳ)\]

&#91;image: Hình D6.6 Nhóm 5: tài liệu, xung đột, kỳ chốt\]

&#91;image: Hình D6.7 Nhóm 6: gói ngành, biến đổi, nạp dữ liệu, kiểm toán\]

&#91;image: Hình D6.8 Nhóm 7: thu thập chủ động, dự báo (gồm mô hình, tham số công thức và sổ dự báo)\]

&#91;image: Hình D6.9 Nhóm 8: lớp tri thức biên soạn (8 bảng context.knowledge\_\* và bảng ngoài nhóm được tham chiếu)\]

Hình D6.9 (nhóm 8, thêm 10/10/2026): 8 bảng context.knowledge\_\* với 14 khoá ngoại trong context. Nét đứt tới 13 bảng ngoài nhóm (nền xám) là tham chiếu logic, ví dụ source\_answer\_id tới qa.answer, dataset\_version\_id tới catalog.dataset\_version. Nét cam là nguồn đa hình của knowledge\_source\_link theo source\_kind: conflict.claim, catalog.dataset\_version, document.document, document.chunk, context.metric\_version, qa.answer, context.glossary\_term, exec.publication (thêm 11/10/2026, trang hypothesis).

&#91;image: Hình D6.10 Nhóm 9: data product, biên nhận, Error Book, provider\]

Hình D6.10 (nhóm 9, thêm 11/10/2026): 8 bảng lượt 4 của context (data\_product, data\_product\_member), qa (answer\_receipt, answer\_receipt\_figure, receipt\_replay), eval (error\_book\_entry), governance (model\_provider, model\_route\_decision); 8 khoá ngoại trong cùng schema, 3 bảng nền xám là đích khoá ngoại vẽ ở nhóm khác (qa.answer, qa.plan, eval.eval\_run). Các bảng này không vẽ lại ở Hình D6.2 đến D6.4.

&#91;image: Hình D6.11 Nhóm 10: lớp riêng lãnh đạo (exec)\]

Hình D6.11 (nhóm 10, thêm 11/10/2026): 10 bảng schema exec với 10 khoá ngoại; nét đứt tới agent.agent\_run (lô bằng chứng) và context.knowledge\_page (trang giả thuyết của bản công bố). Mọi bảng FORCE ROW LEVEL SECURITY chỉ chủ kho; cột nội dung là bản mã (D4.24).
