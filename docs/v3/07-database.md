# Cơ sở dữ liệu V3: từ thiết kế tới DDL, ORM và kiểm thử

Thiết kế CSDL ở tài liệu phân tích thiết kế (tab 3 “Thiết kế cơ sở dữ liệu” và tab 3b “Chi tiết bảng
(D4)”) là chuẩn. Repo dựng lại đúng thiết kế đó bằng một chuỗi sinh tự động: tab 3/3b → `definitions.json`
→ DDL (`dataagent.sql`, `dataagent_audit.sql`) và model SQLAlchemy. Không sửa tay tệp sinh ra.

| Bước | Tệp | Việc |
| --- | --- | --- |
| Nguồn | `backend/schema/csdl/source/tab3.md`, `tab3b.md` | Bản xuất markdown của tab 3 và 3b |
| Đọc thiết kế | `backend/schema/csdl/parse_d4.py` → `definitions.json`, `parse_report.md` | Cột, CHECK, UNIQUE, FK, chỉ mục, phân vùng; phần D4 viết bằng lời thành `notes` của từng bảng |
| Phần viết bằng lời | `backend/schema/csdl/routines/<schema>.sql` (và `<schema>.pre.sql` cho hàm mà CHECK gọi) | Trigger, RLS ngoài `org_isolation`, quyền cột, CHECK và chỉ mục viết bằng lời; mỗi ghi chú có dấu `-- covers: <schema.bảng>#<n>` |
| Độ phủ | `backend/schema/csdl/coverage.md` | Ghi chú chưa có routine nào phủ; phải là “None.” |
| DDL | `backend/schema/csdl/gen_ddl.py` → `backend/alembic/sql/dataagent.sql`, `dataagent_audit.sql` | Vai trò, schema, bảng, outbox, chỉ mục, FK, RLS, quyền, routines, phân vùng tháng |
| ORM | `backend/schema/csdl/gen_orm.py` → `backend/libs/dataagent_db/models/<schema>.py` | Một module mỗi schema, một lớp mỗi bảng (PascalCase) và `Outbox` |
| Migration | `backend/alembic/versions/` | Revision áp tệp SQL sinh ra |
| Kiểm thử | `tests/integration/migrate/` | Số lượng đối tượng, cách ly tổ chức, ORM khớp DDL, các phép thử D5.4 theo từng schema, `scoped_session` |

Sinh lại sau khi thiết kế đổi (từ `backend/schema`):

```bash
python3 -m csdl.parse_d4 && python3 -m csdl.gen_ddl && python3 -m csdl.gen_orm
```

Bản xuất tab 3/3b được cập nhật bằng công cụ xuất markdown của Claude Docs. Thiết kế thêm câu bằng lời thì
`coverage.md` liệt kê nó cho tới khi có routine phủ.

## Kết nối từ ứng dụng

`libs.dataagent_db.scoped_session(role, org_id, user_id=None, **settings)` mở một giao dịch bằng vai trò
`svc_<schema>` và đặt các biến phiên mà RLS đọc bằng `set_config(…, true)` có tham số. URL của mỗi vai trò
lấy từ biến môi trường `DATAAGENT_DB_URL__SVC_<SCHEMA>`; cache prepared statement tắt vì đi qua PgBouncer
chế độ transaction (ADR-31).

| Biến phiên | Kiểu | Ai đọc |
| --- | --- | --- |
| `app.org_id` | uuid | `org_isolation` trên mọi bảng có `org_id` |
| `app.user_id` | uuid | Chính sách chủ kho của schema `exec` |
| `app.exec_workflow` | `'on'` | `svc_exec_worker` (workflow chấm luận điểm) |
| `app.exec_admin` | `'on'` | `svc_exec` khi AC05, AC06 xử lý yêu cầu rời vị trí |
| `app.knowledge_reader` | `'on'` | `svc_context` đọc trang tri thức như người đọc (BR-163) |
| `app.data_space_ids` | literal `uuid[]`, ví dụ `'{…,…}'` | Chính sách người đọc trang tri thức |
| `app.clearance` | số 0–3 (public, internal, confidential, restricted) | Chính sách người đọc trang tri thức |
| `app.caller_id` | uuid | `svc_qa`, `svc_agent` đọc quyết định định tuyến mô hình của chính lượt gọi |

Biến thiếu thì chính sách từ chối, không báo lỗi.

Relay outbox tạm (`svc_outbox_relay`, `unscoped_session`): Debezium đọc outbox từ WAL, không qua RLS
(ADR-06). Khi chưa có Kafka Connect, một relay thăm dò publish thay: nó thấy outbox của mọi tổ chức, chỉ được
đặt `published_at` của dòng chưa publish, không có quyền trên bảng nào khác. Revision `v3_0004` thêm vai trò
này cho cụm đã chạy baseline; bỏ khi Debezium chạy. `scoped_session` từ chối tên biến ngoài danh sách này.

## Những chỗ thiết kế viết bằng lời đã được diễn giải

Mỗi routine ghi diễn giải của mình trong chú thích cạnh dấu `covers`. Một số điểm cần biết khi viết dịch vụ:

- Ghi `audit.audit_log` từ trigger: audit nằm ở CSDL riêng (D1.1), nên trigger phát sự kiện outbox
  `audit.requested`; Audit service ghi.
- `exec`: mọi bảng FORCE RLS; hàm SECURITY DEFINER chạy dưới `da_owner` và vẫn bị `org_isolation` giới
  hạn. Yêu cầu rời vị trí chỉ trỏ được tới kho của chính chủ kho.
- Không cấp được `audit_read` trên `governance.model_route_decision` từ `dataagent.sql`: vai trò này thuộc CSDL
  audit; đọc qua Audit service.
- Chưa kiểm ở CSDL: chủ trang hypothesis khác người công bố (BR-194) — cần đọc `exec` dưới RLS chủ kho, để ở
  tầng ứng dụng.
