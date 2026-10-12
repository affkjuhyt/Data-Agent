# Chi tiết bảng (D4)

Tab này chứa D4, chi tiết cột, ràng buộc và trigger của từng bảng theo schema (D4.1–D4.24). D4 tách khỏi tab 3 vì tab 3 chạm giới hạn kích thước. Nguyên tắc (D1), cấu trúc schema (D2), danh mục bảng (D3), DDL (D5) và sơ đồ (D6) vẫn ở 3 · Thiết kế cơ sở dữ liệu.

## D4. Chi tiết bảng

Mỗi bảng liệt kê cột riêng của nó; cột chung theo D1 (id, org\_id, created\_at, created\_by, updated\_at, updated\_by) ghi gọn ở cuối mỗi bảng. Từ điển này và DDL ở D5 sinh từ cùng một tệp định nghĩa nên luôn khớp nhau: csdl/parse\_d4.py đọc D4 thành csdl/definitions.json, gen\_ddl.py sinh DDL và data\_dictionary.md, erd/gen\_erd.py sinh sơ đồ D6 (10/10/2026). Kiểu dữ liệu là của PostgreSQL 16.

### D4.1 Schema org

#### org.organization

Tổ chức (khách hàng) dùng hệ thống; gốc của org\_id. Use case: UC-11-08.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| code | text, bắt buộc | Mã tổ chức, duy nhất toàn hệ thống |
| name | text, bắt buộc |  |
| industry\_code | text | Ngành chính, khớp pack.industry\_pack.code; chỉ để gợi ý gói |
| status | text, bắt buộc, mặc định 'active' | active, suspended |
| locale | text, bắt buộc, mặc định 'vi-VN' | Ngôn ngữ mặc định |
| time\_zone | text, bắt buộc, mặc định 'Asia/Ho\_Chi\_Minh' |  |
| identity\_provider | jsonb, bắt buộc, mặc định '{}' | Kiểu nguồn danh tính (ad, ldap, oidc) và tham chiếu cấu hình Keycloak; không chứa bí mật |
| settings | jsonb, bắt buộc, mặc định '{}' | Ngưỡng chung, ví dụ tỷ lệ đúng tối thiểu để mở Data Space (NFR-07) |

Cột chung: id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: UNIQUE (code); CHECK (status IN ('active','suspended')).

#### org.app\_user

Người dùng, tài khoản agent và tài khoản dịch vụ; người dùng đồng bộ từ nguồn danh tính. Use case: UC-11-01, UC-11-08, UC-13-01.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| kind | text, bắt buộc, mặc định 'human' | human, agent, service |
| external\_id | text | Mã ở nguồn danh tính (objectGUID, sub); null với agent |
| username | text, bắt buộc |  |
| display\_name | text, bắt buộc |  |
| email | text | Dữ liệu cá nhân |
| department | text | Đơn vị, dùng cho phạm vi dòng |
| attributes | jsonb, bắt buộc, mặc định '{}' | Thuộc tính đồng bộ (chi nhánh, khối, vùng) dùng trong điều kiện chính sách |
| owner\_user\_id | uuid | Chủ sở hữu khi kind = agent (BR-147); FK app\_user |
| status | text, bắt buộc, mặc định 'active' | active, disabled, left |
| default\_data\_space\_id | uuid | Data Space mở sẵn sau đăng nhập; FK data\_space |
| last\_synced\_at | timestamptz |  |
| last\_login\_at | timestamptz |  |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: CHECK (kind IN ('human','agent','service')); CHECK (status IN ('active','disabled','left')); CHECK (kind <> 'agent' OR owner\_user\_id IS NOT NULL); UNIQUE (org\_id, external\_id); UNIQUE (org\_id, username); FOREIGN KEY (owner\_user\_id) REFERENCES org.app\_user(id).

#### org.user\_group

Nhóm người dùng đồng bộ từ nguồn danh tính. Use case: UC-11-01.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| external\_id | text, bắt buộc | Mã nhóm ở nguồn danh tính |
| name | text, bắt buộc | Ví dụ GRP-RISK-ANALYST |
| source | text, bắt buộc, mặc định 'ad' | ad, ldap, oidc, local |
| member\_count | integer, bắt buộc, mặc định 0 |  |
| last\_synced\_at | timestamptz |  |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: UNIQUE (org\_id, external\_id); CHECK (source IN ('ad','ldap','oidc','local')).

#### org.user\_group\_member

Thành viên của nhóm theo lần đồng bộ gần nhất. Use case: UC-11-01.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| group\_id | uuid, bắt buộc | FK user\_group |
| user\_id | uuid, bắt buộc | FK app\_user |
| synced\_at | timestamptz, bắt buộc, mặc định now() |  |

Cột chung: id, org\_id. Ràng buộc: UNIQUE (group\_id, user\_id); FOREIGN KEY (group\_id) REFERENCES org.user\_group(id) ON DELETE CASCADE; FOREIGN KEY (user\_id) REFERENCES org.app\_user(id) ON DELETE CASCADE. Chỉ mục: (user\_id).

#### org.role

Vai trò và năng lực của vai trò. Use case: UC-11-01.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| code | text, bắt buộc | AC01 đến AC11 hoặc mã do tổ chức thêm |
| name | text, bắt buộc |  |
| capabilities | text\[\], bắt buộc, mặc định '{}' | Ví dụ ask, view\_detail, approve, run\_agent, create\_agent, admin, audit\_read |
| is\_system | boolean, bắt buộc, mặc định false | Vai trò dựng sẵn, không xoá được |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: UNIQUE (org\_id, code).

#### org.user\_role

Vai trò cấp toàn tổ chức (không gắn Data Space), ví dụ Quản trị hệ thống, Kiểm soát. Use case: UC-11-01.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| user\_id | uuid, bắt buộc | FK app\_user |
| role\_id | uuid, bắt buộc | FK role |
| source | text, bắt buộc | group\_mapping, emergency, manual |
| source\_ref | uuid | Mã ánh xạ nhóm hoặc yêu cầu khẩn cấp |
| valid\_until | timestamptz | Bắt buộc với emergency, tối đa 24 giờ (BR-44) |
| approved\_by | uuid | Người thứ hai duyệt tài khoản khẩn cấp |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: CHECK (source IN ('group\_mapping','emergency','manual')); CHECK (source <> 'emergency' OR (valid\_until IS NOT NULL AND approved\_by IS NOT NULL)); UNIQUE (user\_id, role\_id, source); FOREIGN KEY (user\_id) REFERENCES org.app\_user(id); FOREIGN KEY (role\_id) REFERENCES org.role(id).

#### org.data\_space

Không gian dữ liệu của một nhóm nghiệp vụ; ranh giới quyền, nguồn, chỉ số và agent. Use case: UC-04-04, UC-11-08.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| code | text, bắt buộc |  |
| name | text, bắt buộc |  |
| description | text |  |
| steward\_id | uuid, bắt buộc | Người phụ trách dữ liệu; FK app\_user |
| backup\_steward\_id | uuid | Người dự phòng (BR-143); FK app\_user |
| status | text, bắt buộc, mặc định 'draft' | draft, onboarding, active, read\_only, archived |
| default\_policy | jsonb, bắt buộc, mặc định '{}' | Bốn cờ trên màn Tạo Data Space: mask\_pii, agent\_write\_derived\_only, show\_filed\_and\_current, allow\_external\_channels |
| pack\_install\_id | uuid | logic → pack.pack\_install |
| work\_calendar\_id | uuid | FK work\_calendar |
| opened\_at | timestamptz | Lúc mở cho người dùng sau khi bộ đánh giá đạt (BR-122) |
| deleted\_at | timestamptz | Xoá mềm |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: UNIQUE (org\_id, code); CHECK (status IN ('draft','onboarding','active','read\_only','archived')); FOREIGN KEY (steward\_id) REFERENCES org.app\_user(id); FOREIGN KEY (backup\_steward\_id) REFERENCES org.app\_user(id).

#### org.data\_space\_member

Người dùng, nhóm hoặc agent thuộc Data Space, kèm vai trò, phạm vi dòng và hạn dùng. Use case: UC-11-01, UC-11-07, UC-11-08, UC-13-01.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| data\_space\_id | uuid, bắt buộc | FK data\_space |
| principal\_type | text, bắt buộc | user, group, agent |
| principal\_id | uuid, bắt buộc | app\_user.id hoặc user\_group.id |
| role\_id | uuid, bắt buộc | FK role |
| row\_scope | jsonb | Điều kiện phạm vi dòng, ví dụ {"branch":\["HN01","HN02","HN03"\]}; null là toàn bộ |
| source | text, bắt buộc, mặc định 'manual' | group\_mapping, manual, access\_request |
| source\_ref | uuid | Mã ánh xạ nhóm hoặc access\_request |
| valid\_until | timestamptz | Hết hạn tự thu hồi (BR-142) |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: CHECK (principal\_type IN ('user','group','agent')); CHECK (source IN ('group\_mapping','manual','access\_request')); CHECK (source <> 'access\_request' OR valid\_until IS NOT NULL); UNIQUE (data\_space\_id, principal\_type, principal\_id, role\_id); FOREIGN KEY (data\_space\_id) REFERENCES org.data\_space(id); FOREIGN KEY (role\_id) REFERENCES org.role(id). Chỉ mục: (principal\_type, principal\_id); (valid\_until) WHERE valid\_until IS NOT NULL.

#### org.group\_mapping

Ánh xạ nhóm sang vai trò, trong một Data Space hoặc toàn tổ chức. Use case: UC-11-01, UC-11-08.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| group\_id | uuid, bắt buộc | FK user\_group |
| role\_id | uuid, bắt buộc | FK role |
| data\_space\_id | uuid | FK data\_space; null là vai trò toàn tổ chức |
| row\_scope | jsonb | Phạm vi dòng áp cho mọi thành viên của nhóm |
| status | text, bắt buộc, mặc định 'active' | active, disabled |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: CHECK (status IN ('active','disabled')); UNIQUE NULLS NOT DISTINCT (group\_id, role\_id, data\_space\_id); FOREIGN KEY (group\_id) REFERENCES org.user\_group(id); FOREIGN KEY (role\_id) REFERENCES org.role(id); FOREIGN KEY (data\_space\_id) REFERENCES org.data\_space(id).

#### org.access\_request

Yêu cầu cấp quyền vào Data Space. Use case: UC-11-07.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| requester\_id | uuid, bắt buộc | FK app\_user |
| data\_space\_id | uuid, bắt buộc | FK data\_space |
| capability | text, bắt buộc | view\_aggregate, view\_detail, ask, run\_agent |
| reason | text, bắt buộc |  |
| requested\_until | date, bắt buộc | Tối đa 90 ngày kể từ ngày gửi (BR-142) |
| status | text, bắt buộc, mặc định 'pending' | pending, approved, narrowed, rejected, expired, cancelled |
| needs\_compliance | boolean, bắt buộc, mặc định false | Yêu cầu xem chi tiết dữ liệu cá nhân, cần thêm AC06 |
| approver\_id | uuid | FK app\_user |
| decided\_at | timestamptz |  |
| decision\_note | text |  |
| granted\_scope | jsonb | Phạm vi thực cấp nếu chủ sở hữu thu hẹp |
| member\_id | uuid | FK data\_space\_member tạo ra khi duyệt |
| hitl\_request\_id | uuid | logic → inbox.hitl\_request |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: CHECK (capability IN ('view\_aggregate','view\_detail','ask','run\_agent')); CHECK (status IN ('pending','approved','narrowed','rejected','expired','cancelled')); CHECK (status IN ('pending','cancelled','expired') OR (approver\_id IS NOT NULL AND decided\_at IS NOT NULL)); FOREIGN KEY (data\_space\_id) REFERENCES org.data\_space(id); FOREIGN KEY (member\_id) REFERENCES org.data\_space\_member(id). Chỉ mục: (data\_space\_id) WHERE status = 'pending'.

#### org.work\_calendar

Lịch làm việc của tổ chức, dùng cho lịch quét, pipeline, báo cáo, trigger và hạn xử lý. Use case: UC-02-04, UC-04-05, UC-06-03, UC-13-03.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| code | text, bắt buộc |  |
| name | text, bắt buộc |  |
| time\_zone | text, bắt buộc, mặc định 'Asia/Ho\_Chi\_Minh' |  |
| weekend\_days | smallint\[\], bắt buộc, mặc định '{6,7}' | Ngày nghỉ hằng tuần theo ISO (1 = thứ Hai) |
| is\_default | boolean, bắt buộc, mặc định false | Mỗi tổ chức đúng một lịch mặc định |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: UNIQUE (org\_id, code). Chỉ mục: duy nhất (org\_id) WHERE is\_default.

#### org.work\_calendar\_day

Ngày nghỉ lễ và ngày làm việc bù của một lịch. Use case: UC-02-04, UC-04-05, UC-06-03.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| calendar\_id | uuid, bắt buộc | FK work\_calendar |
| day | date, bắt buộc |  |
| kind | text, bắt buộc | holiday, working\_override |
| name | text | Ví dụ Quốc khánh |

Cột chung: id, org\_id, created\_at. Ràng buộc: CHECK (kind IN ('holiday','working\_override')); UNIQUE (calendar\_id, day); FOREIGN KEY (calendar\_id) REFERENCES org.work\_calendar(id) ON DELETE CASCADE.

### D4.2 Schema governance

#### governance.access\_policy

Chính sách dữ liệu theo dòng, theo cột và che giá trị; Policy engine viết lại SQL trước khi chạy. Use case: UC-11-02.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| data\_space\_id | uuid, bắt buộc | logic → org.data\_space |
| dataset\_id | uuid, bắt buộc | logic → catalog.dataset |
| name | text, bắt buộc |  |
| kind | text, bắt buộc | row, column, mask |
| applies\_to | jsonb, bắt buộc, mặc định '{}' | Vai trò, nhóm hoặc người áp dụng; rỗng là mọi người |
| condition\_expr | text | Điều kiện dòng theo thuộc tính người dùng, ví dụ branch\_code = :user.branch |
| columns | text\[\] | Cột bị ẩn hoặc che |
| mask\_method | text | hash, partial, last4, null |
| version | integer, bắt buộc, mặc định 1 |  |
| status | text, bắt buộc, mặc định 'draft' | draft, active, disabled |
| simulated\_at | timestamptz | Phải mô phỏng trước khi bật |
| activated\_at | timestamptz |  |
| activated\_by | uuid |  |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: CHECK (kind IN ('row','column','mask')); CHECK (status IN ('draft','active','disabled')); CHECK ((kind = 'row' AND condition\_expr IS NOT NULL) OR (kind IN ('column','mask') AND columns IS NOT NULL)); CHECK (kind <> 'mask' OR mask\_method IN ('hash','partial','last4','null')); CHECK (status <> 'active' OR simulated\_at IS NOT NULL). Chỉ mục: (dataset\_id) WHERE status = 'active'.

#### governance.behavior\_policy

Chính sách hành vi của agent và hệ thống: chặn, che, cần duyệt, tự xử lý, cho phép, ngân sách. Use case: UC-07-04, UC-07-08, UC-11-02, UC-13-02.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| scope | text, bắt buộc | system, org, data\_space, agent, user |
| data\_space\_id | uuid | logic → org.data\_space |
| subject\_id | uuid | Agent hoặc người dùng khi scope là agent, user |
| kind | text, bắt buộc | block, mask, require\_approval, auto\_handle, allow, budget |
| description | text, bắt buộc | Mô tả bằng lời hiện trên giao diện |
| condition | jsonb, bắt buộc, mặc định '{}' | Điều kiện, ví dụ lệch dưới 0,05% và là ngày chốt kỳ |
| action | jsonb, bắt buộc, mặc định '{}' | Hành động, ví dụ dùng nguồn A, không hỏi lại |
| exceptions | jsonb, bắt buộc, mặc định '\[\]' | Trường hợp vẫn phải hỏi người |
| origin | text, bắt buộc, mặc định 'manual' | manual, hitl, pack |
| origin\_ref | uuid | logic → inbox.hitl\_request khi origin = hitl |
| status | text, bắt buộc, mặc định 'draft' | draft, active, disabled, expired |
| review\_at | date | Hạn xem lại; quy tắc từ HITL là 90 ngày (BR-65) |
| override\_count | smallint, bắt buộc, mặc định 0 | Số lần người dùng sửa kết quả; đến 2 thì tự vô hiệu (BR-65) |
| approved\_by | uuid |  |
| approved\_at | timestamptz |  |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: CHECK (scope IN ('system','org','data\_space','agent','user')); CHECK (kind IN ('block','mask','require\_approval','auto\_handle','allow','budget')); CHECK (origin IN ('manual','hitl','pack')); CHECK (status IN ('draft','active','disabled','expired')); CHECK (origin <> 'hitl' OR review\_at IS NOT NULL); CHECK (scope <> 'data\_space' OR data\_space\_id IS NOT NULL). Chỉ mục: (scope, data\_space\_id) WHERE status = 'active'; (review\_at) WHERE status = 'active'.

#### governance.policy\_backtest

Kết quả chạy thử một chính sách trên dữ liệu lịch sử. Use case: UC-07-08, UC-11-02.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| policy\_kind | text, bắt buộc | access, behavior |
| policy\_id | uuid, bắt buộc | access\_policy.id hoặc behavior\_policy.id |
| window\_from | timestamptz, bắt buộc |  |
| window\_to | timestamptz, bắt buộc |  |
| matched\_count | integer, bắt buộc, mặc định 0 |  |
| auto\_handled\_count | integer, bắt buộc, mặc định 0 |  |
| blocked\_count | integer, bắt buộc, mặc định 0 |  |
| changed\_result\_count | integer, bắt buộc, mặc định 0 | Số truy vấn đổi kết quả (UC-11-02 bước 4) |
| affected\_agent\_ids | uuid\[\], bắt buộc, mặc định '{}' |  |
| detail\_ref | text | Đường dẫn MinIO tới chi tiết |

Cột chung: id, org\_id, created\_at. Ràng buộc: CHECK (policy\_kind IN ('access','behavior')); CHECK (window\_to > window\_from). Chỉ mục: (policy\_kind, policy\_id).

#### governance.retention\_policy

Thời hạn lưu theo loại dữ liệu; job lưu trữ đọc bảng này. Quy tắc: BR-47, BR-57, BR-105.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| data\_kind | text, bắt buộc | Ví dụ audit\_log, query\_run, dlq\_record, upload\_table, prompt\_log |
| retain\_days | integer, bắt buộc |  |
| action | text, bắt buộc, mặc định 'archive' | delete, archive, anonymize |
| target | text, bắt buộc | schema.bảng hoặc bucket MinIO |
| legal\_basis | text |  |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: UNIQUE (org\_id, data\_kind); CHECK (action IN ('delete','archive','anonymize')); CHECK (retain\_days > 0).

#### governance.dsr\_request

Yêu cầu của chủ thể dữ liệu (biết, nhận bản sao, xoá, sửa). Use case: UC-11-06.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| subject\_hash | text, bắt buộc | HMAC-SHA256 của định danh đã chuẩn hoá, khoá theo tổ chức lưu trong OpenBao (băm trần của số CCCD dò ngược được); không lưu định danh gốc. Sửa 12/10/2026 |
| request\_kind | text, bắt buộc | access, copy, delete, correct |
| received\_at | timestamptz, bắt buộc |  |
| due\_at | timestamptz, bắt buộc | Hạn luật định; nhắc trước 5 ngày làm việc (BR-141) |
| status | text, bắt buộc, mặc định 'open' | open, searching, review, executing, done, rejected |
| handler\_id | uuid, bắt buộc | AC06; logic → org.app\_user |
| response\_ref | text | Biên bản trả lời trong MinIO |
| closed\_at | timestamptz |  |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: CHECK (request\_kind IN ('access','copy','delete','correct')); CHECK (status IN ('open','searching','review','executing','done','rejected')). Chỉ mục: (due\_at) WHERE status NOT IN ('done','rejected').

#### governance.dsr\_item

Một vị trí tìm thấy dữ liệu của chủ thể và cách xử lý. Use case: UC-11-06.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| dsr\_request\_id | uuid, bắt buộc | FK dsr\_request |
| location\_kind | text, bắt buộc | chunk, tile\_snapshot, derived\_table, conversation, agent\_memory, source\_system |
| location\_ref | text, bắt buộc | schema.bảng:id hoặc mô tả vị trí ở nguồn |
| proposed\_action | text, bắt buộc | delete, provide, retain |
| legal\_basis | text | Bắt buộc khi retain |
| decision | text, bắt buộc, mặc định 'pending' | pending, done, retained |
| executed\_at | timestamptz |  |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: CHECK (proposed\_action IN ('delete','provide','retain')); CHECK (decision IN ('pending','done','retained')); CHECK (proposed\_action <> 'retain' OR legal\_basis IS NOT NULL); FOREIGN KEY (dsr\_request\_id) REFERENCES governance.dsr\_request(id) ON DELETE CASCADE.

#### governance.model\_config

Mô hình AI theo tác vụ, có phiên bản; phiên bản mới chỉ áp dụng khi bộ đánh giá đạt. Use case: UC-11-05, UC-13-04.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| task | text, bắt buộc | planning, sql\_generation, answer\_writing, doc\_reading, embedding, reranking, ocr, pii\_classification |
| version | integer, bắt buộc |  |
| model\_name | text, bắt buộc | Ví dụ Qwen3-Coder-32B |
| endpoint | text, bắt buộc | Cụm và địa chỉ phục vụ trong mạng nội bộ |
| fallback\_model | text |  |
| limits | jsonb, bắt buộc, mặc định '{}' | max\_tokens, concurrency, ngân sách theo Data Space |
| rollout\_pct | smallint, bắt buộc, mặc định 100 | Chuyển dần, ví dụ 10% trong 24 giờ |
| eval\_run\_id | uuid | logic → eval.eval\_run |
| status | text, bắt buộc, mặc định 'draft' | draft, testing, active, retired |
| activated\_at | timestamptz |  |
| activated\_by | uuid |  |
| provider\_id | uuid, bắt buộc | FK model\_provider; bản cài tạo sẵn provider onprem-vllm và gán cho mọi dòng cũ (thêm 10/10/2026, ADR-41) |
| route\_role | text, bắt buộc, mặc định 'primary' | primary (mô hình tại chỗ mặc định), frontier\_candidate (chỉ dùng khi lời gọi đạt BR-180) |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: UNIQUE (org\_id, task, version); CHECK (status IN ('draft','testing','active','retired')); CHECK (rollout\_pct BETWEEN 0 AND 100); CHECK (status <> 'active' OR task IN ('embedding','ocr') OR eval\_run\_id IS NOT NULL); CHECK (route\_role IN ('primary','frontier\_candidate')); CHECK (route\_role <> 'frontier\_candidate' OR task NOT IN ('embedding','reranking','ocr','pii\_classification','sql\_generation')) (BR-180); FOREIGN KEY (provider\_id) REFERENCES governance.model\_provider(id). Chỉ mục: (org\_id, task) WHERE status = 'active'; duy nhất (org\_id, task, route\_role) WHERE status = 'active' AND rollout\_pct = 100. Trigger trước INSERT, UPDATE: provider kind frontier thì route\_role phải là frontier\_candidate, provider kind on\_prem thì route\_role phải là primary; dòng frontier\_candidate active cũng phải có eval\_run\_id đạt như mô hình tại chỗ.

#### governance.ai\_setting

Quy định AI dạng khoá–giá trị: chi phí tối đa mỗi lượt chạy, bộ lọc đầu vào, đầu ra, thời hạn lưu prompt. Use case: UC-11-05.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| key | text, bắt buộc | max\_cost\_per\_run, input\_filter, output\_filter, prompt\_log\_retention\_days, frontier\_routing ({enabled, tasks, data\_space\_ids}; cờ bật định tuyến frontier theo tổ chức, mặc định {"enabled": false}, BR-180) |
| value | jsonb, bắt buộc |  |
| version | integer, bắt buộc, mặc định 1 | Tăng mỗi lần sửa; bản cũ ghi vào audit\_log |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: UNIQUE (org\_id, key).

#### governance.model\_usage

Số token và thời gian của từng lượt gọi mô hình. Use case: UC-11-04, UC-11-05.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| data\_space\_id | uuid | logic → org.data\_space |
| task | text, bắt buộc |  |
| model\_name | text, bắt buộc |  |
| caller\_kind | text, bắt buộc | question, agent\_run, skill\_run, document, eval |
| caller\_id | uuid |  |
| input\_tokens | integer, bắt buộc, mặc định 0 |  |
| output\_tokens | integer, bắt buộc, mặc định 0 |  |
| latency\_ms | integer, bắt buộc, mặc định 0 |  |
| outcome | text, bắt buộc, mặc định 'ok' | ok, error, fallback |
| called\_at | timestamptz, bắt buộc, mặc định now() |  |
| provider\_id | uuid | FK model\_provider; null với dòng trước 10/10/2026 |
| route\_decision\_id | uuid | logic → model\_route\_decision (bảng phân vùng); có khi tác vụ có ứng viên frontier |

Cột chung: id, org\_id. Phân vùng theo tháng trên called\_at. Ràng buộc: CHECK (outcome IN ('ok','error','fallback')); FOREIGN KEY (provider\_id) REFERENCES governance.model\_provider(id). Chỉ mục: (data\_space\_id, called\_at); (provider\_id, called\_at).

#### governance.model\_provider

Provider mô hình khai báo theo tổ chức: tại chỗ (vLLM) hoặc frontier (API bên ngoài). Tên nhà cung cấp là dữ liệu cấu hình, mã nguồn không rẽ nhánh theo tên; khoá API chỉ nằm ở OpenBao (BR-180, BR-181, ADR-41). Thêm 10/10/2026. Use case: UC-11-05, UC-01-07.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| code | text, bắt buộc | Mã do quản trị đặt, ví dụ onprem-vllm, frontier-a |
| display\_name | text, bắt buộc | Tên hiển thị trên biên nhận, ví dụ “vLLM tại chỗ”, “Nhà cung cấp A (API)” |
| kind | text, bắt buộc | on\_prem, frontier |
| api\_style | text, bắt buộc, mặc định 'openai\_compatible' | openai\_compatible, anthropic\_messages, google\_genai, other; LiteLLM chọn bộ chuyển theo giá trị này |
| endpoint | text, bắt buộc | URL gốc; với frontier phải là https và có trong danh sách đích của egress proxy |
| secret\_ref | text | Đường dẫn khoá API trong OpenBao, ví dụ kv/data-agent/providers/frontier-a; bắt buộc với frontier; CSDL không lưu khoá |
| data\_region | text | Vùng xử lý dữ liệu theo hợp đồng, ví dụ ap-southeast-1; bắt buộc với frontier |
| zero\_retention | boolean, bắt buộc, mặc định false | Hợp đồng cam kết không lưu và không dùng dữ liệu để huấn luyện; frontier chỉ bật được khi true |
| contract\_ref | text | Số hợp đồng, phụ lục xử lý dữ liệu |
| max\_classification | text, bắt buộc, mặc định 'restricted' | Mức phân loại cao nhất được gửi; on\_prem mặc định restricted; frontier bắt buộc public |
| enabled | boolean, bắt buộc, mặc định false | Cờ bật theo tổ chức |
| enabled\_by | uuid | logic → org.app\_user (AC05) |
| approved\_by | uuid | logic → org.app\_user (AC06); bắt buộc khi frontier enabled, khác enabled\_by |
| approved\_at | timestamptz |  |
| price | jsonb, bắt buộc, mặc định '{}' | Giá theo triệu token vào, ra cho từng mô hình, để tính chi phí (UC-11-04) |
| monthly\_budget\_vnd | numeric(14,0) | Ngân sách tháng; vượt thì Model gateway ngừng đường frontier tới đầu tháng sau |
| status | text, bắt buộc, mặc định 'active' | active, suspended, retired |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: UNIQUE (org\_id, code); `CHECK (code ~ '^[a-z0-9][a-z0-9-]{1,39}$')`; CHECK (kind IN ('on\_prem','frontier')); CHECK (api\_style IN ('openai\_compatible','anthropic\_messages','google\_genai','other')); CHECK (max\_classification IN ('public','internal','confidential','restricted')); CHECK (status IN ('active','suspended','retired')); CHECK (kind <> 'frontier' OR (endpoint LIKE 'https://%' AND secret\_ref IS NOT NULL AND data\_region IS NOT NULL AND max\_classification = 'public')); CHECK (NOT (kind = 'frontier' AND enabled) OR (zero\_retention AND enabled\_by IS NOT NULL AND approved\_by IS NOT NULL AND approved\_at IS NOT NULL AND approved\_by <> enabled\_by)); CHECK (monthly\_budget\_vnd IS NULL OR monthly\_budget\_vnd >= 0). Chỉ mục: (org\_id, kind) WHERE enabled AND status = 'active'. RLS: org\_isolation; chỉ svc\_governance và vai trò admin, compliance đọc.

Trigger: trước UPDATE của dòng frontier: đổi endpoint, secret\_ref, data\_region hoặc zero\_retention thì đặt enabled = false, xoá approved\_by, approved\_at (cần duyệt lại). Sau INSERT, UPDATE: ghi audit.audit\_log action model\_provider.changed kèm trước và sau (không có khoá); phát governance.model\_provider.changed để Model gateway nạp lại cấu hình và để đội hạ tầng cập nhật danh sách đích của egress proxy.

#### governance.model\_route\_decision

Quyết định định tuyến của mỗi lời gọi mô hình mà tác vụ có ứng viên frontier: nhãn dữ liệu trong ngữ cảnh, phạm vi data product, lý do, provider được chọn, mã băm prompt sau khi che, số token. Ghi trước khi gọi; lời gọi bị chặn sang tại chỗ cũng ghi (BR-180, BR-181). Không lưu nội dung prompt. Thêm 10/10/2026. Use case: UC-11-05, UC-01-07.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| data\_space\_id | uuid | logic → org.data\_space |
| task | text, bắt buộc | Tác vụ như model\_config.task |
| caller\_kind | text, bắt buộc | question, agent\_run, skill\_run, document, eval |
| caller\_id | uuid |  |
| data\_product\_id | uuid | logic → context.data\_product; null khi ngoài phạm vi |
| product\_scope | text, bắt buộc | in\_scope, out\_of\_scope, not\_applicable |
| context\_labels | text\[\], bắt buộc, mặc định '{}' | Nhãn của mọi phần trong ngữ cảnh: public, internal, confidential, restricted, unlabeled |
| max\_classification | text, bắt buộc | Mức cao nhất; phần unlabeled tính là internal |
| contains\_internal\_data | boolean, bắt buộc | Prompt có kết quả truy vấn nội bộ, đoạn tài liệu nội bộ, tên bảng cột, ghi nhớ agent hoặc thực thể nội bộ trong câu hỏi |
| pii\_redactions | integer, bắt buộc, mặc định 0 | Số cụm dữ liệu cá nhân đã che trước khi xét |
| candidate\_provider\_id | uuid | FK model\_provider; provider frontier ứng viên |
| decision | text, bắt buộc | on\_prem\_default, frontier\_allowed, frontier\_blocked |
| reasons | text\[\], bắt buộc, mặc định '{}' | org\_disabled, task\_not\_allowed, provider\_disabled, budget\_exceeded, label\_not\_public, internal\_data, pii\_remaining, allowed\_public\_only, allowed\_out\_of\_scope |
| chosen\_provider\_id | uuid, bắt buộc | FK model\_provider đã gọi |
| model\_name | text, bắt buộc |  |
| prompt\_sha256 | text, bắt buộc | SHA-256 của prompt sau khi che, đúng nội dung gửi đi |
| input\_tokens | integer | Điền sau lời gọi |
| output\_tokens | integer |  |
| outcome | text, bắt buộc, mặc định 'pending' | pending, ok, error, fallback\_on\_prem |
| model\_usage\_id | uuid | logic → model\_usage |
| decided\_at | timestamptz, bắt buộc, mặc định now() |  |

Cột chung: id, org\_id. Phân vùng theo tháng trên decided\_at. Ràng buộc: CHECK (caller\_kind IN ('question','agent\_run','skill\_run','document','eval')); CHECK (product\_scope IN ('in\_scope','out\_of\_scope','not\_applicable')); CHECK (max\_classification IN ('public','internal','confidential','restricted')); CHECK (context\_labels <@ ARRAY\['public','internal','confidential','restricted','unlabeled'\]); CHECK (decision IN ('on\_prem\_default','frontier\_allowed','frontier\_blocked')); CHECK (outcome IN ('pending','ok','error','fallback\_on\_prem')); CHECK (pii\_redactions >= 0); `CHECK (prompt_sha256 ~ '^[0-9a-f]{64}$')`; CHECK (decision <> 'frontier\_allowed' OR (candidate\_provider\_id IS NOT NULL AND chosen\_provider\_id = candidate\_provider\_id AND NOT ('pii\_remaining' = ANY(reasons)) AND (max\_classification = 'public' OR (product\_scope = 'out\_of\_scope' AND NOT contains\_internal\_data)))); CHECK (decision = 'frontier\_allowed' OR candidate\_provider\_id IS NULL OR chosen\_provider\_id <> candidate\_provider\_id); CHECK (decision <> 'frontier\_blocked' OR cardinality(reasons) > 0); FOREIGN KEY (candidate\_provider\_id) REFERENCES governance.model\_provider(id); FOREIGN KEY (chosen\_provider\_id) REFERENCES governance.model\_provider(id). Chỉ mục: (caller\_kind, caller\_id); (chosen\_provider\_id, decided\_at); (decision, decided\_at) WHERE decision <> 'on\_prem\_default'. RLS: org\_isolation; đọc bởi svc\_governance, vai trò compliance, audit\_read; Agent runtime đọc theo caller\_id để ghi biên nhận.

Trigger: trước INSERT: decision = frontier\_allowed thì chosen provider phải kind frontier, enabled, status active, max\_classification public, và ai\_setting frontier\_routing bật cho task; decision khác thì chosen provider phải kind on\_prem. Trước UPDATE: chỉ cho điền input\_tokens, output\_tokens, outcome, model\_usage\_id một lần khi outcome = pending; chặn DELETE (chỉ dọn theo phân vùng khi quá hạn lưu nhật ký kiểm toán). Sau INSERT với frontier\_allowed: ghi audit.audit\_log action model.frontier\_call gồm provider, model\_name, context\_labels, product\_scope, prompt\_sha256; sau UPDATE điền token thì cộng vào chi phí tháng của provider và phát governance.model\_provider.budget\_exceeded khi vượt monthly\_budget\_vnd.

### D4.3 Schema integration

#### integration.api\_client

Client gọi Data Agent qua REST hoặc MCP; thông tin đăng ký trong Keycloak, bí mật trong OpenBao. Use case: UC-12-01, UC-12-02, UC-12-04.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| name | text, bắt buộc | Ví dụ Copilot nội bộ, Excel add-in |
| kind | text, bắt buộc | rest, mcp |
| identity\_mode | text, bắt buộc, mặc định 'on\_behalf\_of\_user' | on\_behalf\_of\_user (token exchange) hoặc own\_identity (BR-48) |
| owner\_user\_id | uuid, bắt buộc | Đầu mối của client; logic → org.app\_user |
| keycloak\_client\_id | text, bắt buộc |  |
| secret\_ref | text, bắt buộc | Đường dẫn OpenBao; không lưu bí mật |
| tools | text\[\], bắt buộc, mặc định '{}' | Công cụ được gọi, ví dụ ask, get\_metric, run\_skill; hỗ trợ dấu \* (hitl.\*) |
| data\_space\_ids | uuid\[\], bắt buộc, mặc định '{}' | Data Space được phép; logic → org.data\_space |
| quota\_per\_minute | integer, bắt buộc, mặc định 60 |  |
| quota\_per\_day | integer, bắt buộc, mặc định 5000 |  |
| expires\_at | timestamptz, bắt buộc | Tối đa 12 tháng kể từ khi cấp (BR-144); giao diện hiện “Sắp hết hạn” khi còn dưới 30 ngày |
| status | text, bắt buộc, mặc định 'active' | pending\_owner, active, paused, revoked, expired; pending\_owner khi người cấp không phải người phụ trách: client chưa gọi được cho tới khi người phụ trách xác nhận (BR-48) |
| owner\_confirmed\_at | timestamptz | Lúc người phụ trách đồng ý để client hành động với quyền của mình (BR-48); bằng created\_at khi người cấp chính là người phụ trách. Mở rộng tools hoặc data\_space\_ids bởi người khác đặt lại về rỗng và status về pending\_owner |
| secret\_rotated\_at | timestamptz | Bí mật cũ còn hiệu lực 24 giờ sau lần xoay |
| revoked\_at | timestamptz |  |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: CHECK (kind IN ('rest','mcp')); CHECK (identity\_mode IN ('on\_behalf\_of\_user','own\_identity')); CHECK (status IN ('pending\_owner','active','paused','revoked','expired')); CHECK (status NOT IN ('active','paused') OR owner\_confirmed\_at IS NOT NULL); CHECK (expires\_at <= created\_at + interval '12 months'); UNIQUE (org\_id, keycloak\_client\_id). Chỉ mục: (expires\_at) WHERE status = 'active'.

#### integration.webhook\_subscription

Đăng ký nhận sự kiện của một client; mỗi lần gửi có chữ ký HMAC. Use case: UC-12-03.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| api\_client\_id | uuid, bắt buộc | FK api\_client |
| event\_types | text\[\], bắt buộc | Loại sự kiện ở mục 9.3, ví dụ hitl.created, report.delivered |
| target\_url | text, bắt buộc | Phải nằm trong danh sách đích được phép của proxy ra ngoài |
| signing\_key\_ref | text, bắt buộc | Khoá HMAC trong OpenBao |
| status | text, bắt buộc, mặc định 'active' | active, paused |
| consecutive\_failures | smallint, bắt buộc, mặc định 0 | Đến 3 thì tự tạm dừng (UC-12-03 E1) |
| paused\_reason | text |  |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: CHECK (status IN ('active','paused')); CHECK (target\_url \~ '^https://'); FOREIGN KEY (api\_client\_id) REFERENCES integration.api\_client(id).

#### integration.webhook\_delivery

Một lần gửi một sự kiện tới một đăng ký, kèm các lần thử lại. Use case: UC-12-03.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| subscription\_id | uuid, bắt buộc | logic → webhook\_subscription (không đặt khoá ngoại vì bảng phân vùng) |
| event\_id | uuid, bắt buộc | Mã sự kiện, gửi ở header để bên nhận chống trùng |
| event\_type | text, bắt buộc |  |
| payload | jsonb, bắt buộc | Chỉ mã đối tượng và tóm tắt, không có dữ liệu chi tiết (BR-145) |
| attempt\_count | smallint, bắt buộc, mặc định 0 | Tối đa 6 lần trong 24 giờ |
| status | text, bắt buộc, mặc định 'pending' | pending, delivered, retrying, failed |
| last\_status\_code | smallint |  |
| last\_error | text |  |
| next\_attempt\_at | timestamptz |  |
| delivered\_at | timestamptz |  |

Cột chung: id, org\_id, created\_at. Phân vùng theo tháng trên created\_at. Ràng buộc: CHECK (status IN ('pending','delivered','retrying','failed')); CHECK (attempt\_count BETWEEN 0 AND 6). Chỉ mục: (next\_attempt\_at) WHERE status IN ('pending','retrying'); (subscription\_id, created\_at).

#### integration.usage\_counter

Đếm lượt gọi theo phút cho mỗi client, công cụ, Data Space; nguồn của màn Client & sử dụng. Use case: UC-12-02, UC-12-04.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| api\_client\_id | uuid, bắt buộc | logic → api\_client |
| tool | text, bắt buộc |  |
| data\_space\_id | uuid | logic → org.data\_space |
| minute | timestamptz, bắt buộc | Đầu phút |
| calls | integer, bắt buộc, mặc định 0 |  |
| errors | integer, bắt buộc, mặc định 0 |  |
| blocked\_by\_permission | integer, bắt buộc, mặc định 0 | Lượt bị chặn do người dùng không có quyền dữ liệu |
| blocked\_by\_quota | integer, bắt buộc, mặc định 0 |  |
| p95\_ms | integer |  |

Cột chung: id, org\_id. Phân vùng theo tháng trên minute. Ràng buộc: UNIQUE (api\_client\_id, tool, data\_space\_id, minute). Chỉ mục: (api\_client\_id, minute).

#### integration.idempotency\_key

Khoá chống gửi trùng ở gateway: cùng khoá trong 24 giờ trả lại kết quả cũ. Tham chiếu: mục 9.1.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| key | text, bắt buộc | Header Idempotency-Key do client gửi |
| principal\_id | uuid, bắt buộc | Người hoặc client gửi |
| request\_hash | text, bắt buộc | Băm phương thức, đường dẫn và thân; khác băm thì trả 422 |
| response\_status | smallint |  |
| response\_ref | text | Thân phản hồi lưu tạm (Redis hoặc MinIO) |
| expires\_at | timestamptz, bắt buộc, mặc định now() + interval '24 hours' |  |

Cột chung: id, org\_id, created\_at. Ràng buộc: UNIQUE (org\_id, principal\_id, key). Chỉ mục: (expires\_at).

### D4.4 Schema catalog

#### catalog.source

Kết nối tới một nguồn dữ liệu của tổ chức (CSDL, kho dữ liệu, API, tệp, CDC). Use case: UC-04-01, UC-04-04, UC-04-05, UC-07-05.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| data\_space\_id | uuid, bắt buộc | Data Space sở hữu; logic → org.data\_space |
| name | text, bắt buộc | Ví dụ Core banking, DWH bán lẻ |
| kind | text, bắt buộc | postgresql, oracle, sqlserver, mysql, snowflake, bigquery, databricks, clickhouse, kafka\_cdc, object\_store, file, sftp, sharepoint, rest\_api, other |
| connector\_icon | text | Mã biểu tượng ConnectorIcon trên giao diện |
| host | text |  |
| port | integer |  |
| database\_name | text |  |
| conn\_options | jsonb, bắt buộc, mặc định '{}' | TLS, schema mặc định, tham số riêng của từng loại |
| secret\_ref | text, bắt buộc | Đường dẫn OpenBao (BR-14); không lưu mật khẩu |
| writeback\_secret\_ref | text | Tài khoản ghi ngược riêng (UC-06-04); null là không cho ghi ngược |
| writeback\_tables | text\[\], bắt buộc, mặc định '{}' | Bảng đích được phép ghi ngược (schema.bảng); rỗng là không bảng nào (UC-06-04, thêm 12/10/2026) |
| mode | text, bắt buộc, mặc định 'federated' | federated (truy vấn tại chỗ) hoặc ingest (nạp về) |
| authority\_level | text, bắt buộc, mặc định 'reference' | level\_1, level\_2, level\_3, reference |
| freshness\_sla | interval | Hạn độ mới; quá hạn thì trạng thái needs\_fix (BR-56) |
| max\_concurrency | smallint, bắt buộc, mặc định 4 | BR-99 |
| query\_timeout\_s | integer, bắt buộc, mặc định 120 | BR-99 |
| scan\_schemas | text\[\], bắt buộc, mặc định '{}' | Schema được quét |
| scan\_cron | text |  |
| profile\_cron | text |  |
| scan\_window | jsonb | Khung giờ được phép quét theo ngày trong tuần (BR-103) |
| work\_calendar\_id | uuid | logic → org.work\_calendar |
| status | text, bắt buộc, mặc định 'draft' | draft, testing, active, needs\_fix, paused, archived |
| status\_reason | text | Ví dụ trễ quá hạn độ mới, lỗi kết nối |
| last\_checked\_at | timestamptz |  |
| db\_version | text | Phiên bản CSDL nguồn đọc được khi kiểm tra |
| deleted\_at | timestamptz | Xoá mềm |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: CHECK (kind IN ('postgresql','oracle','sqlserver','mysql','snowflake','bigquery','databricks','clickhouse','kafka\_cdc','object\_store','file','sftp','sharepoint','rest\_api','other')); CHECK (mode IN ('federated','ingest')); CHECK (mode = 'ingest' OR kind IN ('postgresql','oracle','sqlserver','mysql','snowflake','bigquery','databricks','clickhouse')); CHECK (authority\_level IN ('level\_1','level\_2','level\_3','reference')); CHECK (status IN ('draft','testing','active','needs\_fix','paused','archived')); CHECK (max\_concurrency BETWEEN 1 AND 64 AND query\_timeout\_s BETWEEN 5 AND 3600); UNIQUE (data\_space\_id, name). Chỉ mục: (data\_space\_id) WHERE deleted\_at IS NULL.

#### catalog.scan\_run

Một lần quét schema hoặc thống kê của một nguồn. Use case: UC-04-01, UC-04-02, UC-04-05.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| source\_id | uuid, bắt buộc | FK source |
| kind | text, bắt buộc | schema, profile |
| trigger | text, bắt buộc | initial, schedule, manual, query\_error, cdc\_schema\_change |
| workflow\_id | text | Mã workflow Temporal |
| status | text, bắt buộc, mặc định 'running' | running, succeeded, partial, failed, cancelled |
| tables\_total | integer |  |
| tables\_scanned | integer, bắt buộc, mặc định 0 |  |
| tables\_skipped | jsonb, bắt buộc, mặc định '\[\]' | Bảng bỏ qua kèm lý do (UC-04-02 E1) |
| resume\_from | text | Bảng tiếp tục ở lượt sau khi hết khung giờ (BR-103) |
| started\_at | timestamptz, bắt buộc, mặc định now() |  |
| finished\_at | timestamptz |  |

Cột chung: id, org\_id, created\_at. Ràng buộc: CHECK (kind IN ('schema','profile')); CHECK (trigger IN ('initial','schedule','manual','query\_error','cdc\_schema\_change')); CHECK (status IN ('running','succeeded','partial','failed','cancelled')); FOREIGN KEY (source\_id) REFERENCES catalog.source(id). Chỉ mục: duy nhất (source\_id, kind) WHERE status = 'running', để không chạy chồng hai lượt quét (BR-103).

#### catalog.dataset

Bảng hoặc view ở nguồn, hoặc bảng dẫn xuất và bảng tải tay trong vùng làm việc. Use case: UC-04-02, UC-04-04, UC-04-06, UC-04-08, UC-06-01, UC-06-02, UC-06-05.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| source\_id | uuid | FK source; null với bảng dẫn xuất, bảng tải tay |
| data\_space\_id | uuid, bắt buộc | logic → org.data\_space |
| origin | text, bắt buộc, mặc định 'source' | source, derived, upload, data\_product, consolidated (dataset hợp nhất của P14, UC-14-05) |
| schema\_name | text, bắt buộc |  |
| table\_name | text, bắt buộc |  |
| object\_kind | text, bắt buộc, mặc định 'table' | table, view, materialized\_view |
| display\_name | text |  |
| description | text |  |
| description\_status | text, bắt buộc, mặc định 'none' | none, suggested, approved (BR-100) |
| row\_estimate | bigint |  |
| history\_kind | text, bắt buộc, mặc định 'none' | none, scd2, snapshot, temporal |
| business\_date\_col | text | Cột ngày nghiệp vụ, dùng cho hỏi theo thời điểm (UC-10-01) |
| knowledge\_time\_col | text | Cột thời điểm ghi nhận, dùng cho chế độ at |
| enabled | boolean, bắt buộc, mặc định false | Chỉ bảng bật mới vào ngữ cảnh và truy vấn được (BR-102) |
| steward\_id | uuid | logic → org.app\_user |
| scope | text, bắt buộc, mặc định 'data\_space' | personal, data\_space (BR-111) |
| expires\_at | timestamptz | Bảng tải tay, mặc định 90 ngày (BR-105) |
| current\_version | integer, bắt buộc, mặc định 1 | Khớp dataset\_version mới nhất |
| quality\_state | text, bắt buộc, mặc định 'unknown' | unknown, ok, warning, blocked (BR-116) |
| deleted\_at | timestamptz | Xoá mềm |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: CHECK (origin IN ('source','derived','upload','data\_product','consolidated')); CHECK ((origin = 'source') = (source\_id IS NOT NULL)); CHECK (object\_kind IN ('table','view','materialized\_view')); CHECK (description\_status IN ('none','suggested','approved')); CHECK (history\_kind IN ('none','scd2','snapshot','temporal')); CHECK (scope IN ('personal','data\_space')); CHECK (quality\_state IN ('unknown','ok','warning','blocked')); CHECK (origin <> 'upload' OR expires\_at IS NOT NULL); FOREIGN KEY (source\_id) REFERENCES catalog.source(id); UNIQUE NULLS NOT DISTINCT (data\_space\_id, source\_id, schema\_name, table\_name). Chỉ mục: (data\_space\_id) WHERE enabled AND deleted\_at IS NULL; (expires\_at) WHERE origin = 'upload'.

#### catalog.dataset\_version

Phiên bản cấu trúc của một bảng; ánh xạ cũ đóng valid\_to thay vì xoá (BR-16). Use case: UC-04-02, UC-04-03, UC-04-06, UC-04-07, UC-10-01.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| dataset\_id | uuid, bắt buộc | FK dataset |
| version | integer, bắt buộc |  |
| schema\_hash | text, bắt buộc | Băm danh sách cột và kiểu |
| diff | jsonb | So với bản trước: cột thêm, xoá, đổi tên, đổi kiểu |
| change\_kind | text, bắt buộc, mặc định 'initial' | initial, additive, breaking |
| proposal\_id | uuid | logic → inbox.proposal; null khi chỉ thêm cột (UC-04-03 1a) |
| valid\_from | timestamptz, bắt buộc |  |
| valid\_to | timestamptz |  |
| scan\_run\_id | uuid | FK scan\_run |
| certified\_at | timestamptz | Lúc người phụ trách dữ liệu chứng nhận phiên bản dataset hợp nhất; null là chưa chứng nhận hoặc đã thu hồi, khi đó không dùng làm số báo cáo cơ quan quản lý (BR-156). Trạng thái hiện hành, lịch sử ở dataset\_version\_certification |
| certified\_by | uuid | logic → org.app\_user; người phụ trách dữ liệu (steward\_id hoặc backup\_steward\_id của Data Space chứa dataset), khác created\_by (BR-156) |

Cột chung: id, org\_id, created\_at, created\_by (thêm 10/10/2026: người tạo phiên bản; với dataset hợp nhất là người sửa nội dung đặc tả hợp nhất gần nhất, lấy từ acquire.consolidation\_run.spec\_editor\_id khi phát hành, bắt buộc và không đổi được; null với phiên bản do lượt quét tạo. Vì người chứng nhận phải khác created\_by, phiên bản hợp nhất luôn cần người thứ hai chứng nhận). Ràng buộc: UNIQUE (dataset\_id, version); CHECK (change\_kind IN ('initial','additive','breaking')); CHECK (change\_kind <> 'breaking' OR proposal\_id IS NOT NULL); CHECK (valid\_to IS NULL OR valid\_to > valid\_from); FOREIGN KEY (dataset\_id) REFERENCES catalog.dataset(id); FOREIGN KEY (scan\_run\_id) REFERENCES catalog.scan\_run(id); CHECK ((certified\_at IS NULL) = (certified\_by IS NULL)); CHECK (certified\_by IS NULL OR (created\_by IS NOT NULL AND certified\_by <> created\_by)). Trigger: phiên bản mới ở trạng thái chưa chứng nhận; dataset origin = 'consolidated' bắt buộc có created\_by; created\_by không sửa được; certified\_at, certified\_by chỉ đổi qua trigger của catalog.dataset\_version\_certification (ghi thẳng bị chặn) và chỉ cho dataset origin = 'consolidated' (BR-156). Semantic compiler chặn dùng phiên bản hợp nhất chưa chứng nhận cho số báo cáo cơ quan quản lý (tab 5 G5.2).

#### catalog.dataset\_version\_certification

Lịch sử chứng nhận và thu hồi chứng nhận phiên bản dataset hợp nhất, chỉ ghi thêm (D1 mục 6). Mỗi lần chứng nhận hoặc thu hồi là một dòng; trigger áp dòng mới vào certified\_at, certified\_by của dataset\_version và ghi catalog.outbox trong cùng giao dịch. Thêm 10/10/2026 (BR-156). Use case: UC-14-05.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| dataset\_version\_id | uuid, bắt buộc | FK dataset\_version |
| action | text, bắt buộc | certify, revoke |
| actor\_id | uuid, bắt buộc | logic → org.app\_user; người phụ trách dữ liệu chứng nhận hoặc thu hồi |
| note | text | Ghi chú khi chứng nhận |
| reason | text | Lý do thu hồi; bắt buộc khi action = revoke |

Cột chung: id, org\_id, created\_at. Ràng buộc: CHECK (action IN ('certify','revoke')); CHECK (action <> 'revoke' OR (reason IS NOT NULL AND btrim(reason) <> '')); FOREIGN KEY (dataset\_version\_id) REFERENCES catalog.dataset\_version(id). Chỉ mục: (dataset\_version\_id, created\_at DESC). Trigger sau INSERT: certify khi phiên bản đã chứng nhận thì báo lỗi, không thì điền certified\_at = created\_at, certified\_by = actor\_id và phát catalog.dataset\_version.certified; revoke khi phiên bản chưa chứng nhận thì báo lỗi, không thì đưa certified\_at, certified\_by về null và phát catalog.dataset\_version.certification\_revoked (payload: dataset\_version\_id, dataset\_id, revoked\_by, reason, người và lúc chứng nhận trước đó). Chứng nhận lại sau thu hồi là một dòng certify mới. svc\_catalog chỉ có SELECT, INSERT; không có UPDATE, DELETE. Chọn bảng lịch sử thay cho cột revoked\_at, revoked\_by, revoke\_reason vì cột sẽ bị ghi đè ở lần thu hồi thứ hai, trái nguyên tắc chỉ ghi thêm.

#### catalog.column\_meta

Cột của một bảng theo phiên bản cấu trúc. Use case: UC-04-02, UC-04-03, UC-04-04, UC-04-08.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| dataset\_id | uuid, bắt buộc | FK dataset |
| dataset\_version | integer, bắt buộc | Phiên bản đầu tiên có cột này |
| removed\_in\_version | integer | Phiên bản cột bị xoá |
| name | text, bắt buộc |  |
| ordinal | smallint, bắt buộc |  |
| data\_type | text, bắt buộc | Kiểu ở nguồn |
| normalized\_type | text, bắt buộc | string, integer, decimal, boolean, date, timestamp, json, binary |
| is\_nullable | boolean, bắt buộc, mặc định true |  |
| is\_key | boolean, bắt buộc, mặc định false |  |
| display\_name | text |  |
| description | text |  |
| description\_status | text, bắt buộc, mặc định 'none' | none, suggested, approved |
| unit | text | Ví dụ VND, triệu VND, % |
| pii\_label | text | Nhãn dữ liệu cá nhân: national\_id, phone, email, account\_no, name, address, other |
| pii\_confirmed | boolean, bắt buộc, mặc định false | Người phụ trách đã xác nhận cách che (UC-04-04 bước 3) |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: UNIQUE (dataset\_id, name, dataset\_version); CHECK (normalized\_type IN ('string','integer','decimal','boolean','date','timestamp','json','binary')); CHECK (description\_status IN ('none','suggested','approved')); CHECK (pii\_label IS NULL OR pii\_label IN ('national\_id','phone','email','account\_no','name','address','other')); FOREIGN KEY (dataset\_id) REFERENCES catalog.dataset(id). Chỉ mục: (dataset\_id) WHERE removed\_in\_version IS NULL; (dataset\_id) WHERE pii\_label IS NOT NULL.

#### catalog.column\_profile

Thống kê giá trị của một cột ở một lần quét; giá trị phổ biến của cột có nhãn cá nhân đã được che. Use case: UC-04-02.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| column\_id | uuid, bắt buộc | FK column\_meta |
| scan\_run\_id | uuid, bắt buộc | FK scan\_run |
| sample\_rows | integer, bắt buộc |  |
| null\_ratio | numeric(6,5) |  |
| distinct\_est | bigint |  |
| min\_value | text |  |
| max\_value | text |  |
| top\_values | jsonb | \[{value, count}\], đã che nếu có nhãn cá nhân |
| pattern\_hint | text | Mẫu giá trị nhận ra, ví dụ số điện thoại, ngày dd/mm/yyyy |

Cột chung: id, org\_id, created\_at. Ràng buộc: UNIQUE (column\_id, scan\_run\_id); CHECK (null\_ratio IS NULL OR null\_ratio BETWEEN 0 AND 1); FOREIGN KEY (column\_id) REFERENCES catalog.column\_meta(id); FOREIGN KEY (scan\_run\_id) REFERENCES catalog.scan\_run(id).

### D4.5 Schema context

#### context.semantic\_entity

Thực thể trong mô hình ngữ nghĩa của Data Space (ví dụ khách hàng, hợp đồng, giao dịch), ánh xạ tới một bảng chính. Use case: UC-07-01, UC-07-02.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| data\_space\_id | uuid, bắt buộc | logic → org.data\_space |
| code | text, bắt buộc |  |
| name | text, bắt buộc |  |
| description | text |  |
| primary\_dataset\_id | uuid, bắt buộc | logic → catalog.dataset |
| key\_columns | text\[\], bắt buộc |  |
| canonical\_entity\_id | uuid | logic → pack.canonical\_entity nếu sinh từ gói ngành |
| status | text, bắt buộc, mặc định 'active' | draft, active, retired |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: UNIQUE (data\_space\_id, code); CHECK (status IN ('draft','active','retired')).

#### context.semantic\_dimension

Chiều phân tích (ví dụ vùng, đơn vị, sản phẩm, kênh), có thể phân cấp. Use case: UC-07-01.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| data\_space\_id | uuid, bắt buộc |  |
| entity\_id | uuid, bắt buộc | FK semantic\_entity |
| code | text, bắt buộc |  |
| name | text, bắt buộc |  |
| expression | text, bắt buộc | Cột hoặc biểu thức SQL |
| parent\_dimension\_id | uuid | Chiều cha trong phân cấp, ví dụ chi nhánh thuộc vùng |
| value\_index\_enabled | boolean, bắt buộc, mặc định true | Lập chỉ mục giá trị để khớp tên gọi trong câu hỏi |
| is\_time | boolean, bắt buộc, mặc định false |  |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: UNIQUE (data\_space\_id, code); FOREIGN KEY (entity\_id) REFERENCES context.semantic\_entity(id); FOREIGN KEY (parent\_dimension\_id) REFERENCES context.semantic\_dimension(id).

#### context.semantic\_measure

Độ đo gốc để ghép thành chỉ số (ví dụ tổng giá trị, số lượng). Use case: UC-07-01.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| data\_space\_id | uuid, bắt buộc |  |
| entity\_id | uuid, bắt buộc | FK semantic\_entity |
| code | text, bắt buộc |  |
| name | text, bắt buộc |  |
| expression | text, bắt buộc | Ví dụ SUM(outstanding\_amt) |
| aggregation | text, bắt buộc | sum, count, count\_distinct, avg, min, max, last |
| unit | text |  |
| additivity | text, bắt buộc, mặc định 'full' | full, semi (không cộng theo thời gian), none (BR-117) |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: UNIQUE (data\_space\_id, code); CHECK (aggregation IN ('sum','count','count\_distinct','avg','min','max','last')); CHECK (additivity IN ('full','semi','none')); FOREIGN KEY (entity\_id) REFERENCES context.semantic\_entity(id).

#### context.metric

Chỉ số nghiệp vụ; định nghĩa nằm ở metric\_version. Mã không đổi sau khi phát hành (BR-26). Use case: UC-07-01.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| data\_space\_id | uuid, bắt buộc |  |
| code | text, bắt buộc | Ví dụ NPL\_RATIO |
| owner\_id | uuid, bắt buộc | Chủ sở hữu duyệt mọi thay đổi; logic → org.app\_user |
| consumer\_group | text | Người dùng chính, nhận thông báo khi đổi phiên bản |
| canonical\_dataset\_id | uuid | Nguồn chuẩn; logic → catalog.dataset |
| pack\_metric\_id | uuid | logic → pack.pack\_metric nếu đến từ gói ngành |
| current\_version | integer | Phiên bản đang dùng |
| in\_filed\_report | boolean, bắt buộc, mặc định false | Có trong báo cáo đã nộp; không xoá được (BR-27) |
| status | text, bắt buộc, mặc định 'draft' | draft, active, retired |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: UNIQUE (data\_space\_id, code); CHECK (status IN ('draft','active','retired')).

#### context.metric\_version

Định nghĩa của chỉ số theo phiên bản: công thức, đơn vị, chiều, kỳ, căn cứ, chứng nhận. Use case: UC-01-02, UC-02-02, UC-04-03, UC-06-05, UC-07-01, UC-09-03, UC-10-03.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| metric\_id | uuid, bắt buộc | FK metric |
| version | integer, bắt buộc |  |
| display\_name | text, bắt buộc | Ví dụ Nợ xấu (%) |
| description | text | Định nghĩa bằng lời |
| expression | text, bắt buộc | Công thức trên độ đo, có bộ lọc |
| filters | jsonb, bắt buộc, mặc định '\[\]' | Bộ lọc mặc định |
| unit | text |  |
| decimals | smallint, bắt buộc, mặc định 2 |  |
| direction | text, bắt buộc, mặc định 'neutral' | higher\_better, lower\_better, neutral (“Chiều tốt” trên màn Chỉ số) |
| period\_semantics | text, bắt buộc | end\_of\_period, sum\_over\_period, average\_over\_period |
| time\_grain | text, bắt buộc, mặc định 'day' | day, month, quarter, year |
| allowed\_dimensions | uuid\[\], bắt buộc, mặc định '{}' | logic → semantic\_dimension |
| additivity | text, bắt buộc, mặc định 'none' | full, semi, none (BR-117) |
| legal\_basis | text | Căn cứ, ví dụ điều khoản văn bản |
| override\_id | uuid | logic → pack.metric\_override nếu là bản hiệu chỉnh (BR-129) |
| change\_note | text | Ví dụ Loại vay nội bộ |
| status | text, bắt buộc, mặc định 'draft' | draft, testing, pending\_review, certified, superseded, broken |
| certified\_by | uuid |  |
| certified\_at | timestamptz |  |
| valid\_from | timestamptz |  |
| valid\_to | timestamptz |  |
| proposal\_id | uuid | logic → inbox.proposal |

Cột chung: id, org\_id, created\_at. Ràng buộc: UNIQUE (metric\_id, version); CHECK (direction IN ('higher\_better','lower\_better','neutral')); CHECK (period\_semantics IN ('end\_of\_period','sum\_over\_period','average\_over\_period')); CHECK (time\_grain IN ('day','month','quarter','year')); CHECK (additivity IN ('full','semi','none')); CHECK (status IN ('draft','testing','pending\_review','certified','superseded','broken')); CHECK (status NOT IN ('certified','superseded') OR (certified\_by IS NOT NULL AND certified\_at IS NOT NULL AND valid\_from IS NOT NULL)); FOREIGN KEY (metric\_id) REFERENCES context.metric(id). Chỉ mục: duy nhất (metric\_id) WHERE status = 'certified', để mỗi chỉ số chỉ có một phiên bản đang dùng.

#### context.relationship

Quan hệ giữa hai bảng; quan hệ suy ra chỉ dùng sau khi duyệt (BR-100). Use case: UC-04-02, UC-07-02.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| data\_space\_id | uuid, bắt buộc |  |
| from\_dataset\_id | uuid, bắt buộc | logic → catalog.dataset |
| from\_columns | text\[\], bắt buộc |  |
| to\_dataset\_id | uuid, bắt buộc |  |
| to\_columns | text\[\], bắt buộc |  |
| cardinality | text, bắt buộc | one\_to\_one, many\_to\_one, one\_to\_many |
| origin | text, bắt buộc, mặc định 'declared' | declared, inferred, manual, pack |
| overlap\_ratio | numeric(5,4) | Độ trùng giá trị khi suy ra |
| status | text, bắt buộc, mặc định 'pending\_review' | pending\_review, active, rejected |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: CHECK (cardinality IN ('one\_to\_one','many\_to\_one','one\_to\_many')); CHECK (origin IN ('declared','inferred','manual','pack')); CHECK (status IN ('pending\_review','active','rejected')); CHECK (origin <> 'inferred' OR status <> 'active' OR updated\_by IS NOT NULL).

#### context.synonym

Cách gọi khác của chỉ số, chiều, cột hoặc giá trị. Use case: UC-07-01, UC-07-02, UC-07-09.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| data\_space\_id | uuid, bắt buộc |  |
| phrase | text, bắt buộc | Cụm từ người dùng hay nói |
| phrase\_norm | text, bắt buộc | Đã bỏ dấu, chữ thường, dùng để khớp |
| target\_kind | text, bắt buộc | metric, dimension, measure, entity, value, column |
| target\_id | uuid, bắt buộc |  |
| target\_value | text | Giá trị cụ thể khi target\_kind = value |
| origin | text, bắt buộc, mặc định 'manual' | manual, agent\_suggested, pack |
| status | text, bắt buộc, mặc định 'pending\_review' | pending\_review, active, rejected |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: CHECK (target\_kind IN ('metric','dimension','measure','entity','value','column')); CHECK (origin IN ('manual','agent\_suggested','pack')); CHECK (status IN ('pending\_review','active','rejected')). Chỉ mục: duy nhất (data\_space\_id, phrase\_norm, target\_kind, target\_id) WHERE status = 'active'.

#### context.value\_index

Giá trị phân loại của chiều, dùng để khớp tên gọi trong câu hỏi (ví dụ “Hải Phòng” khớp mã HP01). Use case: UC-07-02.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| data\_space\_id | uuid, bắt buộc |  |
| dimension\_id | uuid, bắt buộc | FK semantic\_dimension |
| value\_code | text, bắt buộc |  |
| value\_label | text, bắt buộc |  |
| label\_norm | text, bắt buộc |  |
| row\_count | bigint |  |
| refreshed\_at | timestamptz, bắt buộc, mặc định now() |  |

Cột chung: id, org\_id. Ràng buộc: UNIQUE (dimension\_id, value\_code); FOREIGN KEY (dimension\_id) REFERENCES context.semantic\_dimension(id) ON DELETE CASCADE. Chỉ mục: (data\_space\_id, label\_norm).

#### context.example\_query

Câu hỏi mẫu kèm SQL đúng; chỉ dùng làm mẫu khi đã duyệt và còn chạy đúng (BR-118). Use case: UC-07-02.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| data\_space\_id | uuid, bắt buộc |  |
| question | text, bắt buộc |  |
| sql | text, bắt buộc |  |
| metric\_ids | uuid\[\], bắt buộc, mặc định '{}' |  |
| qdrant\_point\_id | uuid | Điểm vector trong collection example\_queries |
| origin | text, bắt buộc, mặc định 'manual' | manual, agent\_suggested, feedback, pack |
| last\_verified\_at | timestamptz |  |
| status | text, bắt buộc, mặc định 'draft' | draft, pending\_review, active, stale, rejected |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: CHECK (origin IN ('manual','agent\_suggested','feedback','pack')); CHECK (status IN ('draft','pending\_review','active','stale','rejected')).

#### context.glossary\_term

Thuật ngữ nghiệp vụ: định nghĩa, ánh xạ, nghĩa mặc định khi có nhiều nghĩa. Use case: UC-07-09.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| data\_space\_id | uuid, bắt buộc |  |
| term | text, bắt buộc | Ví dụ nợ xấu |
| term\_norm | text, bắt buộc |  |
| definition | text, bắt buộc |  |
| meanings | jsonb, bắt buộc, mặc định '\[\]' | Các nghĩa và ánh xạ đích, mỗi nghĩa có điều kiện phải hỏi |
| default\_meaning | smallint | Chỉ số trong meanings |
| legal\_basis | text |  |
| status | text, bắt buộc, mặc định 'pending\_review' | pending\_review, active, retired |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: UNIQUE (data\_space\_id, term\_norm); CHECK (status IN ('pending\_review','active','retired')).

#### context.unmapped\_phrase

Cụm từ người dùng hỏi mà chưa ánh xạ được (BR-80). Use case: UC-01-03, UC-07-09.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| data\_space\_id | uuid, bắt buộc |  |
| phrase\_norm | text, bắt buộc |  |
| sample\_questions | text\[\], bắt buộc, mặc định '{}' | Tối đa 5 câu ví dụ, đã che dữ liệu cá nhân |
| ask\_count | integer, bắt buộc, mặc định 1 |  |
| suggested\_target | jsonb | Gợi ý ánh xạ của agent |
| first\_seen\_at | timestamptz, bắt buộc, mặc định now() |  |
| last\_seen\_at | timestamptz, bắt buộc, mặc định now() |  |
| status | text, bắt buộc, mặc định 'open' | open, mapped, ignored |

Cột chung: id, org\_id. Ràng buộc: UNIQUE (data\_space\_id, phrase\_norm); CHECK (status IN ('open','mapped','ignored')).

#### context.user\_term\_pref

Cách hiểu riêng của một người với thuật ngữ; không áp cho báo cáo phát hành (BR-123). Use case: UC-07-09.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| data\_space\_id | uuid, bắt buộc |  |
| user\_id | uuid, bắt buộc | logic → org.app\_user |
| glossary\_term\_id | uuid, bắt buộc | FK glossary\_term |
| meaning\_index | smallint, bắt buộc |  |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: UNIQUE (user\_id, glossary\_term\_id); FOREIGN KEY (glossary\_term\_id) REFERENCES context.glossary\_term(id) ON DELETE CASCADE.

#### context.knowledge\_page

Trang tri thức của Data Space: loại (chỉ số, thực thể, nguồn, chủ đề), chủ thể, dòng mục lục, phiên bản đang phục vụ và chính sách tính từ nguồn (BR-163). Trang không xoá; ngừng bằng status = retired. Thêm 10/10/2026. Use case: UC-07-10, UC-07-11, UC-07-12.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| data\_space\_id | uuid, bắt buộc | logic → org.data\_space; Data Space chủ của trang |
| kind | text, bắt buộc | metric, entity, source, topic |
| subject\_kind | text, bắt buộc | metric, organization, product, segment, branch, channel, dataset, document\_collection, crawl\_source, topic; không có loại cá nhân (BR-167) |
| subject\_ref | text | Mã chỉ số, khoá thực thể chuẩn (ví dụ NHB), id nguồn; null với trang chủ đề |
| slug | text, bắt buộc | Tên ngắn dùng trong liên kết \[\[slug\]\], ví dụ ngan-hang-b |
| title | text, bắt buộc |  |
| index\_summary | text, bắt buộc, mặc định '' | Một dòng tối đa 300 ký tự cho mục lục planner đọc trước |
| tags | text\[\], bắt buộc, mặc định '{}' |  |
| owner\_id | uuid, bắt buộc | logic → org.app\_user; người phụ trách trang |
| status | text, bắt buộc, mặc định 'draft' | draft, active, stale, retired |
| stale\_reason | text | Loại phát hiện làm trang cần xem lại; bắt buộc khi stale |
| current\_version\_id | uuid | FK knowledge\_page\_version đang phục vụ (reviewed hoặc certified) |
| latest\_version\_no | integer, bắt buộc, mặc định 0 | Số phiên bản lớn nhất đã tạo |
| required\_data\_space\_ids | uuid\[\], bắt buộc, mặc định '{}' | Hợp Data Space của mọi nguồn phiên bản đang phục vụ; người đọc cần quyền trên tất cả (BR-163) |
| classification | text, bắt buộc, mặc định 'internal' | public, internal, confidential, restricted; mức cao nhất của nguồn |
| pii\_column\_refs | text\[\], bắt buộc, mặc định '{}' | Cột dữ liệu cá nhân mà chỗ giữ số chạm tới; số được che theo quyền người đọc |
| has\_external\_source | boolean, bắt buộc, mặc định false | Có nguồn ngoài (dataset hợp nhất P14); câu trả lời mang nhãn nguồn ngoài (BR-165) |
| certification\_suspended | boolean, bắt buộc, mặc định false | Nguồn ngoài bị thu hồi chứng nhận; trang tạm mất hiệu lực chứng nhận (BR-165) |
| policy\_state | text, bắt buộc, mặc định 'pending' | pending, current; pending thì ẩn với người đọc (BR-163) |
| policy\_computed\_at | timestamptz |  |
| last\_read\_at | timestamptz | Lần đọc gần nhất; dùng rà trang mồ côi |
| read\_count\_30d | integer, bắt buộc, mặc định 0 |  |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: CHECK (kind IN ('metric','entity','source','topic')); CHECK cặp kind, subject\_kind (entity chỉ organization, product, segment, branch, channel; không có loại cá nhân, BR-167); CHECK (kind = 'topic' OR subject\_ref IS NOT NULL); `CHECK (slug ~ '^[a-z0-9][a-z0-9-]{1,79}$')`; CHECK (char\_length(index\_summary) <= 300); CHECK (status IN ('draft','active','stale','retired')); CHECK (status NOT IN ('active','stale') OR current\_version\_id IS NOT NULL); CHECK (status <> 'stale' OR stale\_reason IS NOT NULL); CHECK (classification IN ('public','internal','confidential','restricted')); CHECK (policy\_state IN ('pending','current')); UNIQUE (data\_space\_id, slug); FOREIGN KEY (current\_version\_id) REFERENCES context.knowledge\_page\_version(id) DEFERRABLE INITIALLY DEFERRED. Chỉ mục: duy nhất (data\_space\_id, kind, subject\_ref) WHERE subject\_ref IS NOT NULL AND status <> 'retired'; (data\_space\_id, status); GIN (tags); GIN to\_tsvector('simple', title || ' ' || index\_summary) cho mục lục. RLS (BR-163): ngoài org\_isolation, chính sách hạn chế cho SELECT với context\_read và với svc\_context khi app.knowledge\_reader = 'on': trang active hoặc stale, policy\_state = 'current', required\_data\_space\_ids nằm trong app.data\_space\_ids và mức phân loại không quá app.clearance; thiếu thiết lập thì không thấy dòng nào. Hàm context.knowledge\_page\_policy\_refresh tính lại required\_data\_space\_ids, classification, pii\_column\_refs, has\_external\_source từ nguồn của phiên bản đang phục vụ và phát context.knowledge\_page.policy\_changed khi đổi; context.knowledge\_policy\_invalidate đặt policy\_state = 'pending' cho trang dùng một nguồn.

Bổ sung 10/10/2026 (P16, BR-194): thêm 'hypothesis' vào CHECK kind và 'exec\_publication' vào danh sách subject\_kind; cặp kind = 'hypothesis' chỉ đi với subject\_kind = 'exec\_publication', subject\_ref là exec.publication.id. Trang hypothesis do Exec service tạo qua Context service khi lãnh đạo công bố (UC-16-05); owner\_id là người phụ trách Data Space đích, khác người công bố.

#### context.knowledge\_page\_version

Một phiên bản trang: mẫu thân trang, trạng thái duyệt, người duyệt và người chứng nhận. Nội dung bất biến; sửa là tạo phiên bản mới (BR-164, ADR-13). Thêm 10/10/2026. Use case: UC-07-10, UC-07-11, UC-07-12.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| page\_id | uuid, bắt buộc | FK knowledge\_page |
| version | integer, bắt buộc | Tăng dần theo trang, trigger điền |
| parent\_version\_id | uuid | FK knowledge\_page\_version; mặc định là phiên bản đang phục vụ |
| status | text, bắt buộc, mặc định 'draft' | draft, pending\_review, reviewed, certified, rejected, superseded |
| origin | text, bắt buộc | ingest, lint\_fix, saveback, manual |
| body | text, bắt buộc | Mẫu thân trang Markdown với {{claim:..}}, {{metric:..}}, {{agg:..}}, \[\[slug\]\]; tối đa 20.000 ký tự; không có số viết tay (BR-162) |
| body\_sha256 | text, bắt buộc | Mã băm thân trang, trigger tính |
| summary | text, bắt buộc, mặc định '' | Tóm tắt thay đổi cho người duyệt |
| change\_note | text |  |
| change\_size | text, bắt buộc, mặc định 'minor' | minor, major; major đi qua Cần bạn trước khi viết (BR-166) |
| knowledge\_run\_id | uuid | FK knowledge\_run |
| agent\_run\_id | uuid | logic → agent.agent\_run |
| source\_answer\_id | uuid | logic → qa.answer; bắt buộc khi origin = saveback |
| proposal\_id | uuid | logic → inbox.proposal (kind knowledge\_page) |
| reviewed\_by | uuid | logic → org.app\_user; khác created\_by (BR-164) |
| reviewed\_at | timestamptz |  |
| certified\_by | uuid | logic → org.app\_user; khác created\_by và reviewed\_by (BR-164) |
| certified\_at | timestamptz |  |

Cột chung: id, org\_id, created\_at, created\_by. Ràng buộc: UNIQUE (page\_id, version); CHECK (version > 0); CHECK (status IN ('draft','pending\_review','reviewed','certified','rejected','superseded')); CHECK (origin IN ('ingest','lint\_fix','saveback','manual')); CHECK (change\_size IN ('minor','major')); CHECK (char\_length(body) BETWEEN 1 AND 20000); `CHECK (body_sha256 ~ '^[0-9a-f]{64}$')`; CHECK (origin <> 'saveback' OR source\_answer\_id IS NOT NULL); CHECK (status NOT IN ('reviewed','certified','superseded') OR (reviewed\_by IS NOT NULL AND reviewed\_at IS NOT NULL)); CHECK (reviewed\_by IS NULL OR reviewed\_by <> created\_by); CHECK người chứng nhận có đủ hai cột và khác created\_by, reviewed\_by; FOREIGN KEY (page\_id), (parent\_version\_id), (knowledge\_run\_id). Chỉ mục: duy nhất (page\_id) WHERE status IN ('draft','pending\_review'), mỗi trang một bản nháp mở; (knowledge\_run\_id); (source\_answer\_id). Trigger trước INSERT, UPDATE: phiên bản mới ở draft, version = latest\_version\_no + 1 (tự điền), parent mặc định là phiên bản đang phục vụ, body\_sha256 tự tính, thân và tóm tắt không có số viết tay (BR-162); trang retired không nhận phiên bản; nội dung không sửa được; chỉ chuyển draft → pending\_review, rejected; pending\_review → reviewed, rejected, draft; reviewed → certified, superseded; certified → superseded. Gửi duyệt cần ít nhất một khẳng định, {{claim:…}} khớp knowledge\_claim, chỗ giữ số trong thân và khẳng định khớp knowledge\_figure, \[\[slug\]\] trỏ trang còn dùng có dòng knowledge\_link, bản lưu ngược có nguồn là câu trả lời (BR-161, BR-162, BR-167). Chứng nhận cần mọi nguồn ngoài có catalog.dataset\_version.certified\_at (BR-165). Trigger sau UPDATE: reviewed thì phiên bản phục vụ cũ chuyển superseded, trang trỏ current\_version\_id về phiên bản mới, status = 'active', bỏ cờ tạm mất chứng nhận, tính lại chính sách, phát context.knowledge\_page.published; certified phát context.knowledge\_page.certified. Trigger sau INSERT có knowledge\_run\_id: tăng knowledge\_run.pages\_changed, chặn khi vượt page\_limit hoặc lượt không ở analyzing, writing, checking (BR-166). RLS hạn chế cho người đọc: status reviewed, certified hoặc superseded, trang thấy được và người đọc có quyền trên mọi nguồn của chính phiên bản (hàm SECURITY DEFINER context.knowledge\_version\_sources\_ok). svc\_context không có DELETE.

Bổ sung 10/10/2026 (P16, BR-194): thêm 'exec\_publish' vào CHECK origin. Thân trang hypothesis dùng thêm thẻ {{hyp:condition}} điền từ exec.publication khi hiển thị; trigger kiểm số viết tay (BR-162) miễn kiểm thẻ này như chỗ giữ số.

#### context.knowledge\_source\_link

Danh sách nguồn (sources\[\]) của một phiên bản kèm mã băm phần nguồn đã dùng; dùng để tìm trang bị ảnh hưởng khi nguồn đổi và để tính chính sách trang. Nội dung bất biến; chỉ thuộc tính quyền đổi được. Thêm 10/10/2026. Use case: UC-07-10, UC-07-11, UC-07-12.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| page\_version\_id | uuid, bắt buộc | FK knowledge\_page\_version |
| source\_kind | text, bắt buộc | conflict\_claim, dataset\_version, document\_version, document\_chunk, metric\_version, answer, glossary\_term, knowledge\_page |
| source\_ref | uuid, bắt buộc | logic → bảng nguồn theo source\_kind |
| source\_label | text, bắt buộc | Tên hiển thị trong trích dẫn |
| dataset\_version\_id | uuid | logic → catalog.dataset\_version; bắt buộc khi external |
| external | boolean, bắt buộc, mặc định false | Nguồn ngoài (BR-156, BR-165) |
| content\_sha256 | text, bắt buộc | Mã băm phần nguồn dùng để biên soạn; nguồn mới cùng mã băm thì bỏ qua (BR-166) |
| data\_space\_id | uuid, bắt buộc | logic → org.data\_space của nguồn; cập nhật khi nguồn đổi quyền (BR-163) |
| classification | text, bắt buộc, mặc định 'internal' | public, internal, confidential, restricted |
| pii\_column\_refs | text\[\], bắt buộc, mặc định '{}' |  |
| policy\_updated\_at | timestamptz |  |

Cột chung: id, org\_id, created\_at. Ràng buộc: CHECK (source\_kind IN ('conflict\_claim','dataset\_version','document\_version','document\_chunk','metric\_version','answer','glossary\_term','knowledge\_page')); CHECK (NOT external OR dataset\_version\_id IS NOT NULL); CHECK (source\_kind <> 'dataset\_version' OR dataset\_version\_id = source\_ref); `CHECK (content_sha256 ~ '^[0-9a-f]{64}$')`; CHECK classification; UNIQUE (page\_version\_id, source\_kind, source\_ref). Chỉ mục: (source\_kind, source\_ref) để tìm trang bị ảnh hưởng; (dataset\_version\_id). Trigger trước INSERT: phiên bản còn draft. Trigger sau UPDATE OF data\_space\_id, classification, pii\_column\_refs: tính lại chính sách của trang đang phục vụ phiên bản đó trong cùng giao dịch (BR-163). svc\_context chỉ có SELECT, INSERT và UPDATE trên bốn cột quyền.

Bổ sung 10/10/2026 (P16, BR-194): thêm 'exec\_publication' vào CHECK source\_kind; source\_kind này chỉ dùng cho phiên bản của trang kind hypothesis.

#### context.knowledge\_claim

Khẳng định của một phiên bản, mỗi khẳng định một nguồn và một khoảng hiệu lực (BR-161); chỉ ghi thêm. Thêm 10/10/2026. Use case: UC-07-10, UC-07-11, UC-07-12.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| page\_version\_id | uuid, bắt buộc | FK knowledge\_page\_version |
| anchor | text, bắt buộc | c1, c2...; thân trang chèn bằng {{claim:c1}} |
| statement | text, bắt buộc | Câu khẳng định; số chỉ ở dạng chỗ giữ số (BR-162) |
| subject\_key | text | Ví dụ NHB; cùng attribute, period dùng rà mâu thuẫn |
| attribute | text | Ví dụ lai\_suat\_huy\_dong\_12t |
| period | text | Ví dụ 2026-W41 |
| source\_link\_id | uuid, bắt buộc | FK knowledge\_source\_link của cùng phiên bản (BR-161) |
| valid\_from | date, bắt buộc |  |
| valid\_to | date | Null là còn hiệu lực |
| figure\_condition | jsonb | Điều kiện số giữ cho khẳng định định tính còn đúng, ví dụ {"left":"f1","op":">","right":"f2"} |

Cột chung: id, org\_id, created\_at. Ràng buộc: `CHECK (anchor ~ '^[a-z][a-z0-9_]{0,31}$')`; CHECK (char\_length(statement) BETWEEN 1 AND 1000); CHECK (valid\_to IS NULL OR valid\_to > valid\_from); UNIQUE (page\_version\_id, anchor); FOREIGN KEY (page\_version\_id), (source\_link\_id). Chỉ mục: (subject\_key, attribute, period) cho rà mâu thuẫn; (valid\_to) cho rà hết hiệu lực. Trigger trước INSERT: phiên bản còn draft; nguồn thuộc chính phiên bản (BR-161); câu khẳng định không có số viết tay (BR-162). svc\_context chỉ có SELECT, INSERT.

#### context.knowledge\_figure

Ràng buộc của chỗ giữ số trong thân trang và khẳng định tới chỉ số semantic hoặc bảng tổng hợp đã hợp nhất; số điền khi hiển thị (BR-162); chỉ ghi thêm. Thêm 10/10/2026. Use case: UC-07-10, UC-07-12.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| page\_version\_id | uuid, bắt buộc | FK knowledge\_page\_version |
| token | text, bắt buộc | Nguyên văn chỗ giữ số, ví dụ {{metric:lai\_suat\_huy\_dong\_12t\_tb\|period=2026-W41}} |
| binding\_kind | text, bắt buộc | metric, aggregate |
| metric\_code | text | logic → context.metric; bắt buộc khi metric |
| aggregate\_code | text | Mã bảng tổng hợp của dataset hợp nhất; bắt buộc khi aggregate |
| dataset\_id | uuid | logic → catalog.dataset (origin consolidated) |
| params | jsonb, bắt buộc, mặc định '{}' | period, chiều, bộ lọc |
| display\_format | text, bắt buộc, mặc định 'auto' | auto, percent\_2, amount\_billion, count |
| source\_link\_id | uuid, bắt buộc | FK knowledge\_source\_link (phiên bản chỉ số hoặc dataset) |

Cột chung: id, org\_id, created\_at. Ràng buộc: CHECK (token \~ '^\\{\\{(metric|agg):\[a-z0-9\_\]+(\\|\[a-z\_\]+=\[^|{}\]+)\*\\}\\}$'), tức {{metric:mã|tham số=giá trị…}} hoặc {{agg:mã|…}}; CHECK metric có metric\_code và token {{metric:…}}, aggregate có aggregate\_code, dataset\_id và token {{agg:…}}; CHECK (display\_format IN ('auto','percent\_2','amount\_billion','count')); UNIQUE (page\_version\_id, token). Chỉ mục: (metric\_code), (dataset\_id) để tìm trang khi chỉ số đổi định nghĩa. Trigger trước INSERT: phiên bản còn draft, nguồn thuộc chính phiên bản. svc\_context chỉ có SELECT, INSERT.

#### context.knowledge\_link

Liên kết từ một phiên bản trang tới trang khác (\[\[slug\]\]); dùng tìm trang mồ côi và mở rộng ngữ cảnh khi trả lời; chỉ ghi thêm. Thêm 10/10/2026. Use case: UC-07-10, UC-07-11.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| from\_version\_id | uuid, bắt buộc | FK knowledge\_page\_version chứa liên kết |
| from\_page\_id | uuid, bắt buộc | FK knowledge\_page |
| to\_page\_id | uuid, bắt buộc | FK knowledge\_page |
| link\_kind | text, bắt buộc, mặc định 'see\_also' | see\_also, defines, part\_of, competitor\_of, uses\_metric, derived\_from |

Cột chung: id, org\_id, created\_at. Ràng buộc: CHECK (link\_kind IN ('see\_also','defines','part\_of','competitor\_of','uses\_metric','derived\_from')); CHECK (from\_page\_id <> to\_page\_id); UNIQUE (from\_version\_id, to\_page\_id, link\_kind). Chỉ mục: (to\_page\_id). Trigger trước INSERT: phiên bản còn draft và thuộc from\_page\_id. svc\_context chỉ có SELECT, INSERT.

#### context.knowledge\_run

Một lượt biên soạn (ingest) hoặc rà soát (lint) của Agent Tri thức: số nguồn bỏ qua nhờ mã băm, số trang sửa, số trang dời, phát hiện và mục Cần bạn (BR-166). Thêm 10/10/2026. Use case: UC-07-10, UC-07-11.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| data\_space\_id | uuid, bắt buộc | logic → org.data\_space |
| kind | text, bắt buộc | ingest, lint |
| trigger | text, bắt buộc | event, schedule, manual, saveback |
| trigger\_event | text | Ví dụ acquire.dataset.published |
| source\_kind | text |  |
| source\_ref | uuid |  |
| status | text, bắt buộc, mặc định 'queued' | queued, analyzing, writing, checking, completed, partial, failed |
| page\_limit | integer, bắt buộc, mặc định 20 | Số trang tối đa được sửa mỗi lượt, 1 đến 50 (BR-166) |
| sources\_seen | integer, bắt buộc, mặc định 0 |  |
| sources\_skipped\_unchanged | integer, bắt buộc, mặc định 0 | Nguồn bỏ qua vì mã băm không đổi |
| pages\_analyzed | integer, bắt buộc, mặc định 0 |  |
| pages\_changed | integer, bắt buộc, mặc định 0 | Trigger đếm; không vượt page\_limit |
| pages\_deferred | integer, bắt buộc, mặc định 0 | Trang dời sang lượt sau |
| findings\_count | integer, bắt buộc, mặc định 0 |  |
| hitl\_count | integer, bắt buộc, mặc định 0 | Mục Cần bạn đã tạo |
| agent\_run\_id | uuid | logic → agent.agent\_run |
| cost | numeric(12,4) | Chi phí mô hình |
| started\_at | timestamptz |  |
| finished\_at | timestamptz |  |

Cột chung: id, org\_id, created\_at, created\_by. Ràng buộc: CHECK (kind IN ('ingest','lint')); CHECK (trigger IN ('event','schedule','manual','saveback')); CHECK (status IN ('queued','analyzing','writing','checking','completed','partial','failed')); CHECK (page\_limit BETWEEN 1 AND 50); CHECK (pages\_changed <= page\_limit); CHECK (status NOT IN ('completed','partial','failed') OR finished\_at IS NOT NULL). Chỉ mục: duy nhất (data\_space\_id, kind) WHERE status IN ('queued','analyzing','writing','checking'), mỗi Data Space một lượt mỗi loại; (data\_space\_id, created\_at DESC). Không thuộc RLS người đọc; chỉ svc\_context đọc.

#### context.knowledge\_lint\_finding

Phát hiện của lượt rà soát với tập hành động cố định; bỏ qua cần lý do và ẩn 90 ngày (BR-166). Thêm 10/10/2026. Use case: UC-07-11.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| run\_id | uuid, bắt buộc | FK knowledge\_run |
| data\_space\_id | uuid, bắt buộc | logic → org.data\_space |
| page\_id | uuid | FK knowledge\_page; null với metric\_without\_page, undefined\_term |
| related\_page\_id | uuid | FK knowledge\_page; trang mâu thuẫn |
| kind | text, bắt buộc | contradiction, expired\_claim, orphan\_page, metric\_without\_page, undefined\_term, source\_decertified, stale\_condition, policy\_drift |
| severity | text, bắt buộc | low, medium, high |
| fingerprint | text, bắt buộc | Mã băm loại và đối tượng; không báo lặp |
| detail | jsonb, bắt buộc, mặc định '{}' | Khẳng định, số, nguồn liên quan |
| status | text, bắt buộc, mặc định 'open' | open, actioned, resolved, dismissed |
| action | text | propose\_edit, mark\_stale, retire\_page, create\_page, dismiss, reassign |
| action\_reason | text | Bắt buộc khi dismiss |
| page\_version\_id | uuid | FK knowledge\_page\_version; bản nháp sửa khi propose\_edit |
| proposal\_id | uuid | logic → inbox.proposal |
| hitl\_request\_id | uuid | logic → inbox.hitl\_request (kind knowledge\_finding) |
| suppressed\_until | timestamptz | Bỏ qua thì ẩn 90 ngày (BR-166) |
| resolved\_by | uuid | logic → org.app\_user |
| resolved\_at | timestamptz |  |

Cột chung: id, org\_id, created\_at. Ràng buộc: CHECK kind trong 8 loại; CHECK (kind IN ('metric\_without\_page','undefined\_term') OR page\_id IS NOT NULL); CHECK (severity IN ('low','medium','high')); CHECK (status IN ('open','actioned','resolved','dismissed')); CHECK action trong tập cố định propose\_edit, mark\_stale, retire\_page, create\_page, dismiss, reassign; CHECK (status = 'open' OR action IS NOT NULL); CHECK dismissed khi và chỉ khi action = 'dismiss', khi đó action\_reason không rỗng và có suppressed\_until; CHECK resolved, dismissed có resolved\_by, resolved\_at; CHECK propose\_edit có page\_version\_id. Chỉ mục: duy nhất (data\_space\_id, fingerprint) WHERE status IN ('open','actioned'); (run\_id); (page\_id, status). Trigger trước INSERT: chặn phát hiện cùng vân tay đã bỏ qua còn trong hạn ẩn (BR-166); trước UPDATE: không đổi hành động sau khi đã xử lý. Trigger sau INSERT, UPDATE: mark\_stale đưa trang về stale, retire\_page đưa trang về retired; phát context.knowledge\_finding.opened hoặc theo trạng thái để Inbox service tạo, đóng thẻ Cần bạn kind knowledge\_finding. Hàm context.knowledge\_on\_dataset\_decertified (gọi khi nhận catalog.dataset\_version.certification\_revoked) bật certification\_suspended và tạo phát hiện source\_decertified mức cao cho mọi trang dùng phiên bản đó (BR-165). Không thuộc RLS người đọc; không cấp context\_read, chỉ svc\_context đọc.

#### context.data\_product

Data product: phạm vi trả lời có cam kết của một Data Space, gồm câu hỏi trả lời được, dataset đã chuẩn hoá, chỉ số đã chứng nhận, owner và steward có tên, cam kết độ tươi và trạng thái (BR-175, ADR-42). Ví dụ POC: “Giá xăng dầu trong nước”. Khác pipeline.data\_product của P06 (hợp đồng một bảng hoặc mart chia sẻ giữa Data Space); bảng này có thể chứa một sản phẩm P06 làm thành phần. Thêm 10/10/2026. Use case: UC-07-13, UC-07-14, UC-01-07, UC-01-01.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| data\_space\_id | uuid, bắt buộc | logic → org.data\_space |
| code | text, bắt buộc | Mã ngắn, không đổi sau khi active, ví dụ gia\_xang\_dau\_trong\_nuoc |
| name | text, bắt buộc | Ví dụ Giá xăng dầu trong nước |
| description | text, bắt buộc | Phạm vi câu hỏi trả lời được, bằng lời; tối đa 2.000 ký tự |
| sample\_questions | jsonb, bắt buộc, mặc định '\[\]' | Mảng {text, metric\_code, period, expected}; tối thiểu 10 câu khi active, mỗi câu là một eval\_item của eval\_set\_id |
| scope\_entities | jsonb, bắt buộc, mặc định '\[\]' | Chiều và giá trị trong phạm vi, ví dụ \[{"dimension":"mat\_hang","values":\["E5RON92","RON95-III","DO 0,05S-II"\]}\]; mảng rỗng là mọi giá trị của chiều hợp lệ |
| period\_grain | text, bắt buộc | day, week, month, quarter, year, event (kỳ theo lịch sự kiện, ví dụ kỳ điều hành giá) |
| period\_from | date, bắt buộc | Kỳ sớm nhất trả lời được, ví dụ 2024-01-01 |
| period\_to | date | Null là đến hiện tại |
| out\_of\_scope\_note | text | Câu hỏi gần phạm vi nhưng không trả lời, ví dụ giá bán tại một cửa hàng cụ thể |
| owner\_id | uuid, bắt buộc | logic → org.app\_user; người chịu trách nhiệm cam kết (data owner) |
| steward\_id | uuid, bắt buộc | logic → org.app\_user; người giữ định nghĩa, xử lý Error Book; khác owner\_id |
| freshness\_statement | text, bắt buộc | Cam kết hiển thị cho người dùng, ví dụ “Giá xăng dầu trong nước: cập nhật trước 16:30 ngày điều hành” |
| classification | text, bắt buộc, mặc định 'internal' | public, internal, confidential, restricted; mức cao nhất của các thành phần, trigger tính; dùng cho định tuyến mô hình (BR-180) |
| eval\_set\_id | uuid | logic → eval.eval\_set (subject\_type context) chứa câu hỏi mẫu và ca hồi quy Error Book |
| pack\_version\_id | uuid | logic → pack.pack\_version nếu dựng từ mẫu của gói ngành |
| version | integer, bắt buộc, mặc định 1 | Tăng khi đổi phạm vi, thành phần primary hoặc cam kết sau khi active |
| status | text, bắt buộc, mặc định 'draft' | draft, pending\_review, active, suspended, retired |
| suspended\_reason | text | Bắt buộc khi suspended, ví dụ metric\_decertified, dataset\_decertified, manual |
| proposal\_id | uuid | logic → inbox.proposal (kind data\_product) đang chờ duyệt |
| activated\_by | uuid | logic → org.app\_user; người duyệt kích hoạt, khác created\_by |
| activated\_at | timestamptz |  |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: UNIQUE (data\_space\_id, code); CHECK (code \~ '^\[a-z\]\[a-z0-9\_\]{2,63}$'); CHECK (char\_length(description) <= 2000); CHECK (jsonb\_typeof(sample\_questions) = 'array'); CHECK (jsonb\_typeof(scope\_entities) = 'array'); CHECK (period\_grain IN ('day','week','month','quarter','year','event')); CHECK (period\_to IS NULL OR period\_to >= period\_from); CHECK (owner\_id <> steward\_id); CHECK (classification IN ('public','internal','confidential','restricted')); CHECK (status IN ('draft','pending\_review','active','suspended','retired')); CHECK (status <> 'suspended' OR suspended\_reason IS NOT NULL); CHECK (status <> 'pending\_review' OR proposal\_id IS NOT NULL); CHECK (status NOT IN ('active','suspended') OR (activated\_by IS NOT NULL AND activated\_at IS NOT NULL AND eval\_set\_id IS NOT NULL AND jsonb\_array\_length(sample\_questions) >= 10)); CHECK (activated\_by IS NULL OR activated\_by <> created\_by). Chỉ mục: (data\_space\_id, status); GIN to\_tsvector('simple', name || ' ' || description) cho danh mục phạm vi. RLS: org\_isolation; đọc theo quyền Data Space như các bảng context khác.

Trigger: trước UPDATE sang active (BR-175): có ít nhất một thành phần metric mà context.metric.current\_version là phiên bản certified và ít nhất một thành phần dataset role primary có sla\_kind khác none; mọi thành phần dataset truy được theo thời điểm (catalog.dataset.history\_kind khác none, hoặc dataset nạp về có catalog.dataset\_version); Context service kiểm các điều kiện khác schema qua catalog\_read và gọi hàm. Trước UPDATE: không đổi code khi status khác draft; đổi period\_from, period\_to, scope\_entities, freshness\_statement của bản active thì tăng version. Sau INSERT, UPDATE, DELETE ở context.data\_product\_member: tính lại classification theo mức cao nhất của thành phần. Phát context.data\_product.changed để Agent runtime làm mới danh mục phạm vi (cache tối đa 60 giây). Hàm context.data\_product\_on\_member\_decertified(member\_kind, member\_ref), gọi khi nhận context.metric\_version.superseded không còn bản certified hoặc catalog.dataset\_version.certification\_revoked: chuyển các data product active chứa thành phần primary đó sang suspended với suspended\_reason tương ứng.

#### context.data\_product\_member

Thành phần của data product: dataset, chỉ số, trang tri thức, bộ sưu tập tài liệu, sản phẩm dữ liệu dùng chung của P06. Dataset primary mang cam kết độ tươi theo lịch và trạng thái độ tươi hiện tại cho cổng độ tươi (BR-178). Thêm 10/10/2026. Use case: UC-07-13, UC-01-07, UC-01-01.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| data\_product\_id | uuid, bắt buộc | FK data\_product |
| member\_kind | text, bắt buộc | dataset, metric, knowledge\_page, document\_collection, shared\_product |
| member\_ref | uuid, bắt buộc | logic → catalog.dataset, context.metric, context.knowledge\_page, document.collection hoặc pipeline.data\_product theo member\_kind |
| role | text, bắt buộc, mặc định 'primary' | primary, reference; chỉ số đọc từ dataset primary mới qua cổng độ tươi |
| sla\_kind | text, bắt buộc, mặc định 'none' | none, schedule, max\_age; dataset primary phải khác none |
| sla\_cron | text | Lịch cập nhật cam kết theo giờ địa phương, cron 5 trường, ví dụ '30 16 \* \* 4' (16:30 thứ Năm, ngày điều hành giá xăng dầu) |
| sla\_event\_calendar\_code | text | logic → monitor.external\_calendar.code cùng data\_space\_id (UNIQUE (data\_space\_id, code)); mã lịch sự kiện ngoài do P08 quản lý (ví dụ fuel\_price\_regulation, lịch điều hành giá xăng dầu); có thì hạn lấy theo lịch này, sla\_cron chỉ cho giờ trong ngày |
| sla\_timezone | text, bắt buộc, mặc định 'Asia/Ho\_Chi\_Minh' |  |
| sla\_work\_calendar\_id | uuid | logic → org.work\_calendar; hạn rơi vào ngày nghỉ thì dời sang ngày làm việc kế tiếp |
| sla\_max\_age | interval | Với max\_age: tuổi dữ liệu tối đa, ví dụ 1 day cho tỷ giá |
| sla\_grace | interval, bắt buộc, mặc định '0' | Ân hạn sau hạn trước khi che số, ví dụ 30 minutes; tối đa 1 ngày |
| as\_of\_col | text | Cột ngày dữ liệu dùng đo độ tươi; null thì dùng catalog.dataset.business\_date\_col |
| last\_as\_of | timestamptz | Ngày dữ liệu mới nhất đã có trong dataset; Connector hoặc Ingestion service cập nhật sau mỗi lượt nạp |
| last\_loaded\_at | timestamptz | Thời điểm lượt nạp gần nhất xong |
| expected\_as\_of | timestamptz | Ngày dữ liệu phải có theo hạn gần nhất đã qua |
| next\_due\_at | timestamptz | Hạn kế tiếp tính từ lịch |
| freshness\_state | text, bắt buộc, mặc định 'unknown' | unknown, fresh, overdue, missing |
| last\_checked\_at | timestamptz |  |
| classification | text, bắt buộc, mặc định 'internal' | public, internal, confidential, restricted; với knowledge\_page lấy từ trang; public chỉ khi dữ liệu là thông tin công bố (ví dụ giá điều hành của Bộ Công Thương) và có AC06 xác nhận; dùng cho định tuyến mô hình (BR-180) |
| classification\_confirmed\_by | uuid | logic → org.app\_user (AC06); bắt buộc khi classification = public |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: CHECK (member\_kind IN ('dataset','metric','knowledge\_page','document\_collection','shared\_product')); CHECK (role IN ('primary','reference')); CHECK (sla\_kind IN ('none','schedule','max\_age')); CHECK (sla\_kind = 'none' OR member\_kind = 'dataset'); CHECK (sla\_kind <> 'schedule' OR sla\_cron IS NOT NULL OR sla\_event\_calendar\_code IS NOT NULL); CHECK (sla\_kind <> 'max\_age' OR sla\_max\_age IS NOT NULL); CHECK (NOT (member\_kind = 'dataset' AND role = 'primary') OR sla\_kind <> 'none'); CHECK (sla\_grace >= interval '0' AND sla\_grace <= interval '1 day'); CHECK (freshness\_state IN ('unknown','fresh','overdue','missing')); CHECK (classification IN ('public','internal','confidential','restricted')); CHECK (classification <> 'public' OR classification\_confirmed\_by IS NOT NULL); UNIQUE (data\_product\_id, member\_kind, member\_ref); FOREIGN KEY (data\_product\_id) REFERENCES context.data\_product(id) ON DELETE CASCADE. Chỉ mục: (member\_kind, member\_ref); (next\_due\_at) WHERE sla\_kind <> 'none'. RLS: org\_isolation.

Trigger: trước INSERT, UPDATE: data product ở retired thì chặn; member\_ref đúng loại và cùng Data Space do Context service kiểm khi ghi (khác schema, không đặt khoá ngoại). Trước DELETE: data product active thì chỉ gỡ được thành phần reference; gỡ thành phần primary phải qua phiên bản mới (UC-07-13 6a). Hàm context.data\_product\_freshness\_refresh(member\_id, at) tính expected\_as\_of, next\_due\_at và freshness\_state: với schedule, lấy hạn gần nhất D không muộn hơn at trừ sla\_grace (theo sla\_event\_calendar\_code hoặc sla\_cron, dời theo sla\_work\_calendar\_id); expected\_as\_of là ngày của D; fresh khi last\_as\_of không sớm hơn expected\_as\_of, missing khi last\_as\_of null, còn lại overdue. Với max\_age: fresh khi at trừ last\_as\_of không quá sla\_max\_age cộng sla\_grace. Hàm chạy sau mỗi lượt nạp, mỗi 5 phút với thành phần có next\_due\_at đã qua, và khi tool freshness.check gọi; đổi trạng thái thì phát context.data\_product.freshness\_changed.

### D4.6 Schema qa

#### qa.conversation

Một chuỗi hỏi đáp; câu hỏi tiếp theo kế thừa cách hiểu (UC-01-04). Use case: UC-01-01, UC-01-04.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| data\_space\_id | uuid, bắt buộc |  |
| user\_id | uuid, bắt buộc | logic → org.app\_user |
| channel | text, bắt buộc, mặc định 'web' | web, mobile, teams, zalo, mcp, api |
| api\_client\_id | uuid | logic → integration.api\_client khi gọi qua client |
| title | text |  |
| workspace\_id | uuid | logic → workspace.workspace nếu đã đưa vào phiên |
| last\_question\_at | timestamptz |  |
| deleted\_at | timestamptz | Xoá mềm |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: CHECK (channel IN ('web','mobile','teams','zalo','mcp','api')). Chỉ mục: (user\_id, last\_question\_at DESC).

#### qa.question

Một lượt hỏi, kèm thời điểm và chế độ dữ liệu. Use case: UC-01-01, UC-01-03, UC-01-04, UC-10-01, UC-12-02.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| conversation\_id | uuid, bắt buộc | FK conversation |
| seq | integer, bắt buộc | Thứ tự trong cuộc hội thoại |
| text | text, bắt buộc | Đã che dữ liệu cá nhân trước khi lưu |
| parent\_question\_id | uuid | Câu được kế thừa cách hiểu |
| as\_of | date | Ngày nghiệp vụ hỏi tới |
| knowledge\_mode | text, bắt buộc, mặc định 'latest' | latest, period\_close, at |
| knowledge\_at | timestamptz | Bắt buộc khi knowledge\_mode = at |
| purpose | text | Mục đích khai báo, dùng cho BR-59 |
| status | text, bắt buộc, mặc định 'received' | received, clarifying, planning, running, answered, refused, failed |
| received\_at | timestamptz, bắt buộc, mặc định now() |  |
| answered\_at | timestamptz |  |

Cột chung: id, org\_id. Ràng buộc: CHECK (knowledge\_mode IN ('latest','period\_close','at')); CHECK (knowledge\_mode <> 'at' OR knowledge\_at IS NOT NULL); CHECK (status IN ('received','clarifying','planning','running','answered','refused','failed')); UNIQUE (conversation\_id, seq); FOREIGN KEY (conversation\_id) REFERENCES qa.conversation(id); FOREIGN KEY (parent\_question\_id) REFERENCES qa.question(id).

#### qa.plan

Cách hệ thống hiểu câu hỏi (chip) và kế hoạch chạy; sửa chip tạo phiên bản mới. Use case: UC-01-01, UC-01-02, UC-01-03, UC-01-04, UC-07-09, UC-10-01, UC-13-06.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| question\_id | uuid | FK question; null với kế hoạch của lượt chạy agent |
| agent\_run\_id | uuid | logic → agent.agent\_run |
| version | integer, bắt buộc, mặc định 1 |  |
| chips | jsonb, bắt buộc | Chỉ số, phạm vi, kỳ, chiều, chế độ dữ liệu; mỗi chip ghi nguồn gốc (mặc định, cách hiểu riêng, người sửa) |
| steps | jsonb, bắt buộc, mặc định '\[\]' | Plan IR: các bước truy vấn, kỹ năng, tài liệu |
| confidence | numeric(4,3) | Dưới 0,8 thì hỏi lại |
| edited\_by\_user | boolean, bắt buộc, mặc định false |  |
| edit\_note | text |  |

Cột chung: id, org\_id, created\_at. Ràng buộc: CHECK ((question\_id IS NULL) <> (agent\_run\_id IS NULL)); UNIQUE NULLS NOT DISTINCT (question\_id, agent\_run\_id, version); FOREIGN KEY (question\_id) REFERENCES qa.question(id).

#### qa.answer

Câu trả lời: văn bản, số, biểu đồ, trích dẫn và trạng thái kiểm tra. Use case: UC-01-01, UC-01-02, UC-01-03, UC-01-04, UC-01-05, UC-05-04.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| question\_id | uuid, bắt buộc | FK question |
| plan\_id | uuid, bắt buộc | FK plan dùng để trả lời |
| kind | text, bắt buộc, mặc định 'data' | data, document, mixed, clarification, refusal, forecast |
| forecast\_job\_id | uuid | logic → forecast.forecast\_job; bắt buộc khi kind = forecast (UC-15-01) |
| number\_check | jsonb, bắt buộc, mặc định '{}' | Kết quả kiểm số (BR-171): {numbers\_total, traced, untraceable\_draft, regenerated, sentences\_dropped, untraceable\_final}; CHECK untraceable\_final = 0. DDL ở dataagent.sql |
| text | text, bắt buộc |  |
| figures | jsonb, bắt buộc, mặc định '\[\]' | Các số trong câu trả lời kèm query\_run\_id, metric\_version\_id để truy nguồn |
| chart\_spec | jsonb |  |
| citations | jsonb, bắt buộc, mặc định '\[\]' | chunk\_id, document\_id, trang, đoạn; đã kiểm tra tồn tại (UC-05-04 bước 4) |
| filed\_vs\_current | jsonb | Hai số đã nộp và hiện tại khi hỏi về kỳ đã nộp (BR-39) |
| reconcile\_badge | text | matched\_filed, differs\_from\_filed, not\_filed (nhãn “Khớp số đã nộp” trên màn Hỏi đáp) |
| agent\_name | text | Ví dụ Agent Phân tích |
| latency\_ms | integer |  |
| lineage\_hash | text | SHA-256 khi dùng kỳ đã chốt (UC-01-02) |
| data\_product\_id | uuid | logic → context.data\_product; có khi product\_scope = in\_scope (BR-175, thêm 10/10/2026) |
| product\_scope | text, bắt buộc, mặc định 'not\_applicable' | in\_scope, out\_of\_scope, not\_applicable (làm rõ, từ chối); out\_of\_scope hiển thị nhãn “Ngoài phạm vi data product, không có biên nhận” |

Cột chung: id, org\_id, created\_at. Ràng buộc: CHECK (kind IN ('data','document','mixed','clarification','refusal','forecast')); CHECK (kind <> 'forecast' OR forecast\_job\_id IS NOT NULL); CHECK (reconcile\_badge IS NULL OR reconcile\_badge IN ('matched\_filed','differs\_from\_filed','not\_filed')); CHECK (jsonb\_typeof(number\_check) = 'object' AND coalesce((number\_check->>'untraceable\_final')::int, 0) = 0) (BR-171); FOREIGN KEY (question\_id) REFERENCES qa.question(id); FOREIGN KEY (plan\_id) REFERENCES qa.plan(id); CHECK (product\_scope IN ('in\_scope','out\_of\_scope','not\_applicable')); CHECK ((product\_scope = 'in\_scope') = (data\_product\_id IS NOT NULL)); CHECK (product\_scope <> 'in\_scope' OR kind IN ('data','mixed','forecast')) (BR-175). Chỉ mục: (question\_id); (data\_product\_id, created\_at) WHERE data\_product\_id IS NOT NULL.

#### qa.feedback

Đánh giá và báo sai của người dùng; báo sai đã xác nhận thành mục đánh giá (UC-01-05). Use case: UC-01-05, UC-07-06.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| answer\_id | uuid, bắt buộc | FK answer |
| user\_id | uuid, bắt buộc |  |
| rating | text, bắt buộc | up, down, wrong |
| error\_kind | text | wrong\_metric, wrong\_scope, wrong\_period, wrong\_number, wrong\_document, other |
| expected\_value | text |  |
| comment | text |  |
| status | text, bắt buộc, mặc định 'open' | open, in\_review, confirmed, rejected |
| reviewer\_id | uuid |  |
| decided\_at | timestamptz |  |
| reason | text |  |
| proposal\_id | uuid | logic → inbox.proposal |
| eval\_item\_id | uuid | logic → eval.eval\_item |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: CHECK (rating IN ('up','down','wrong')); CHECK (rating <> 'wrong' OR error\_kind IN ('wrong\_metric','wrong\_scope','wrong\_period','wrong\_number','wrong\_document','other')); CHECK (status IN ('open','in\_review','confirmed','rejected')); CHECK (status NOT IN ('confirmed','rejected') OR (reviewer\_id IS NOT NULL AND decided\_at IS NOT NULL)); FOREIGN KEY (answer\_id) REFERENCES qa.answer(id). Chỉ mục: (status) WHERE status IN ('open','in\_review').

#### qa.export\_job

Việc xuất kết quả ra tệp; liên kết tải hết hạn sau 24 giờ (BR-86). Use case: UC-01-06.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| answer\_id | uuid | FK answer |
| artifact\_id | uuid | logic → workspace.artifact |
| requested\_by | uuid, bắt buộc |  |
| format | text, bắt buộc | xlsx, csv, pdf, png |
| include\_detail\_rows | boolean, bắt buộc, mặc định false | Dòng chi tiết; có dữ liệu cá nhân thì cần duyệt |
| row\_count | integer |  |
| status | text, bắt buộc, mặc định 'queued' | queued, waiting\_approval, running, ready, failed, expired |
| hitl\_request\_id | uuid | logic → inbox.hitl\_request |
| object\_key | text | MinIO da-exports |
| expires\_at | timestamptz |  |

Cột chung: id, org\_id, created\_at. Ràng buộc: CHECK (format IN ('xlsx','csv','pdf','png')); CHECK (status IN ('queued','waiting\_approval','running','ready','failed','expired')); CHECK (answer\_id IS NOT NULL OR artifact\_id IS NOT NULL); CHECK (status <> 'ready' OR (object\_key IS NOT NULL AND expires\_at IS NOT NULL)); FOREIGN KEY (answer\_id) REFERENCES qa.answer(id).

#### qa.answer\_receipt

Biên nhận của một câu trả lời trong phạm vi data product: data product và phiên bản, phiên bản bộ biên dịch và Agent runtime, mô hình và provider của mọi lần gọi, độ tươi lúc trả lời, mã băm toàn biên nhận. Ghi cùng giao dịch với qa.answer, khoá ngay, chỉ ghi thêm (BR-176, ADR-43). Câu trả lời ngoài phạm vi không có biên nhận. Thêm 10/10/2026. Use case: UC-01-07, UC-01-01, UC-07-14.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| answer\_id | uuid, bắt buộc | FK answer; một biên nhận mỗi câu trả lời |
| receipt\_no | text, bắt buộc | Mã hiển thị theo ngày, ví dụ BN-20260702-000184; trigger cấp |
| data\_product\_id | uuid, bắt buộc | logic → context.data\_product |
| data\_product\_code | text, bắt buộc | Chép lại để đọc được cả khi product đã retired |
| data\_product\_version | integer, bắt buộc |  |
| plan\_id | uuid, bắt buộc | FK plan; plan dùng để dựng lại truy vấn khi chạy lại |
| answered\_at | timestamptz, bắt buộc | Mốc point-in-time khi chạy lại |
| knowledge\_mode | text, bắt buộc | latest, period\_close, at; chép từ qa.question |
| knowledge\_at | timestamptz |  |
| compiler\_version | text, bắt buộc | Phiên bản semantic compiler, ví dụ semcomp 1.8.2+4f2a91c |
| runtime\_version | text, bắt buộc | Phiên bản Agent runtime và Verifier |
| models | jsonb, bắt buộc, mặc định '\[\]' | Mỗi lần gọi mô hình của lượt: {task, provider\_code, provider\_kind, provider\_display\_name, model\_name, model\_config\_version, route\_decision\_id} |
| frontier\_used | boolean, bắt buộc, mặc định false | Có ít nhất một lời gọi tới provider frontier (BR-181) |
| freshness | jsonb, bắt buộc, mặc định '\[\]' | Mỗi dataset primary lúc trả lời: {dataset\_id, last\_as\_of, expected\_as\_of, due\_at, state} |
| figure\_count | integer, bắt buộc, mặc định 0 |  |
| masked\_count | integer, bắt buộc, mặc định 0 | Số figure bị che do quá hạn hoặc chưa có (BR-178) |
| receipt\_sha256 | text | SHA-256 của JSON chuẩn hoá gồm các cột trên và mọi dòng answer\_receipt\_figure xếp theo figure\_key; trigger tính khi khoá |
| sealed\_at | timestamptz | Thời điểm khoá; sau khi khoá không thêm figure |
| retain\_until | date, bắt buộc, mặc định (now() + interval '7 years')::date | Hạn giữ theo NFR-20; dữ liệu dùng để chạy lại phải giữ tới cùng hạn |

Cột chung: id, org\_id, created\_at. Ràng buộc: UNIQUE (answer\_id); UNIQUE (org\_id, receipt\_no); `CHECK (receipt_no ~ '^BN-[0-9]{8}-[0-9]{6}$')`; CHECK (knowledge\_mode IN ('latest','period\_close','at')); CHECK (knowledge\_mode <> 'at' OR knowledge\_at IS NOT NULL); CHECK (jsonb\_typeof(models) = 'array' AND jsonb\_typeof(freshness) = 'array'); CHECK (masked\_count BETWEEN 0 AND figure\_count); CHECK ((sealed\_at IS NULL) = (receipt\_sha256 IS NULL)); `CHECK (receipt_sha256 IS NULL OR receipt_sha256 ~ '^[0-9a-f]{64}$')`; FOREIGN KEY (answer\_id) REFERENCES qa.answer(id); FOREIGN KEY (plan\_id) REFERENCES qa.plan(id). Chỉ mục: (data\_product\_id, answered\_at DESC); (answered\_at) WHERE frontier\_used. RLS: org\_isolation; người xem biên nhận phải xem được câu trả lời; số trong biên nhận che theo quyền người xem như qa.answer.

Trigger: trước INSERT: qa.answer tương ứng phải có product\_scope = 'in\_scope' và cùng data\_product\_id (BR-175); cấp receipt\_no theo ngày answered\_at. Trước UPDATE: chỉ cho đặt sealed\_at một lần khi đang null, lúc đó trigger tính figure\_count, masked\_count, receipt\_sha256 bằng sha256(convert\_to(json chuẩn hoá, 'UTF8')); mọi UPDATE khác bị chặn. Trước DELETE: chỉ job lưu giữ xóa được dòng quá retain\_until. Agent runtime ghi biên nhận, figure và khoá trong cùng giao dịch với qa.answer; câu trả lời in\_scope không có biên nhận đã khoá thì không hiển thị số (BR-176).

#### qa.answer\_receipt\_figure

Một số trong biên nhận: giá trị, chỉ số và phiên bản đã chứng nhận kèm người ký, nguồn, dataset\_version và as\_of, mã băm truy vấn đã biên dịch, độ tươi và trạng thái che. Chỉ ghi thêm (BR-176, BR-178). Thêm 10/10/2026. Use case: UC-01-07, UC-01-02.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| receipt\_id | uuid, bắt buộc | FK answer\_receipt |
| figure\_key | text, bắt buộc | F1…Fn như qa.answer.figures (tab 5 G5.15) |
| label | text, bắt buộc | Ví dụ Giá trần E5RON92 kỳ 02/07/2026 |
| value | numeric | Giá trị đủ chữ số; null khi bị che hoặc chưa có |
| unit | text | Ví dụ đ/lít, USD/thùng, %, tỷ đồng |
| display\_state | text, bắt buộc, mặc định 'shown' | shown, masked\_stale, not\_available |
| stale\_note | text | Ví dụ “dữ liệu đến 25/06/2026, đã quá hạn cập nhật”; bắt buộc khi masked\_stale |
| requested\_period | text | Kỳ câu hỏi yêu cầu, ví dụ 2026-07-02 hoặc latest |
| metric\_id | uuid | logic → context.metric |
| metric\_version\_id | uuid | logic → context.metric\_version; phiên bản certified lúc trả lời |
| metric\_version\_no | integer |  |
| metric\_certified\_by | uuid | logic → org.app\_user; người ký định nghĩa |
| metric\_certified\_at | timestamptz |  |
| source\_kind | text, bắt buộc | query\_run, compute\_run, period\_result |
| query\_run\_id | uuid | logic → query.query\_run |
| compute\_run\_id | uuid | logic → lượt compute.run của Agent runtime (G5.10) |
| published\_result\_id | uuid | logic → period.published\_result khi đọc số đã công bố |
| compiled\_sql\_sha256 | text | SHA-256 của query\_run.sql\_executed sau chuẩn hoá khoảng trắng |
| expr\_sha256 | text | SHA-256 của phép tính khai báo với compute\_run |
| result\_sha256 | text | query\_run.result\_hash lúc trả lời; dùng so khi chạy lại |
| dataset\_versions | jsonb, bắt buộc, mặc định '\[\]' | \[{dataset\_id, dataset\_version\_id, version, as\_of}\] của mọi bảng truy vấn đọc |
| as\_of | date | Ngày dữ liệu của số, ví dụ 2026-07-02 |
| freshness\_state | text, bắt buộc | fresh, overdue, missing, not\_applicable (số đọc từ dataset reference hoặc kỳ đã qua) |
| due\_at | timestamptz | Hạn cập nhật áp cho số |

Cột chung: id, org\_id, created\_at. Ràng buộc: UNIQUE (receipt\_id, figure\_key); CHECK (display\_state IN ('shown','masked\_stale','not\_available')); CHECK (source\_kind IN ('query\_run','compute\_run','period\_result')); CHECK (freshness\_state IN ('fresh','overdue','missing','not\_applicable')); CHECK ((display\_state = 'shown') = (value IS NOT NULL)); CHECK (display\_state <> 'shown' OR freshness\_state IN ('fresh','not\_applicable')); CHECK (display\_state <> 'masked\_stale' OR (stale\_note IS NOT NULL AND freshness\_state = 'overdue')); CHECK (display\_state <> 'not\_available' OR freshness\_state = 'missing'); `CHECK (source_kind <> 'query_run' OR (query_run_id IS NOT NULL AND compiled_sql_sha256 ~ '^[0-9a-f]{64}$'))`; `CHECK (source_kind <> 'compute_run' OR (compute_run_id IS NOT NULL AND expr_sha256 ~ '^[0-9a-f]{64}$'))`; CHECK (source\_kind <> 'period\_result' OR published\_result\_id IS NOT NULL); CHECK (metric\_version\_id IS NULL OR (metric\_certified\_by IS NOT NULL AND metric\_certified\_at IS NOT NULL)); CHECK (jsonb\_typeof(dataset\_versions) = 'array'); FOREIGN KEY (receipt\_id) REFERENCES qa.answer\_receipt(id). Chỉ mục: (metric\_version\_id); (query\_run\_id). RLS: org\_isolation, theo biên nhận.

Trigger: trước INSERT: biên nhận chưa khoá (sealed\_at null). Trước UPDATE, DELETE: chặn (chỉ ghi thêm); xóa theo dòng biên nhận khi quá retain\_until.

#### qa.receipt\_replay

Một lần chạy lại biên nhận tại thời điểm cũ: dựng lại truy vấn từ plan với cùng phiên bản chỉ số, bộ biên dịch, dataset\_version và knowledge\_at = answered\_at, so mã băm và giá trị từng số. Số tái lập y hệt; câu chữ chỉ sinh lại khi người dùng chọn và luôn mang nhãn có thể khác (BR-177). Thêm 10/10/2026. Use case: UC-01-07, UC-07-14.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| receipt\_id | uuid, bắt buộc | FK answer\_receipt |
| requested\_by | uuid, bắt buộc | logic → org.app\_user; truy vấn chạy với quyền của người này |
| reason | text | Ví dụ Kiểm toán nội bộ quý 4/2026 |
| mode | text, bắt buộc, mặc định 'figures' | figures (chỉ số), figures\_and\_text (sinh lại cả câu chữ bằng mô hình hiện hành) |
| compiler\_version\_used | text, bắt buộc | Bộ biên dịch đã dùng; bình thường trùng bản gốc lấy từ kho artefact |
| status | text, bắt buộc, mặc định 'running' | running, completed, failed |
| outcome | text | identical, differs\_explained, differs, not\_reproducible |
| figure\_results | jsonb, bắt buộc, mặc định '\[\]' | Mỗi figure: {figure\_key, original\_value, replay\_value, sql\_sha256\_match, result\_sha256\_match, cause} |
| differs\_count | integer, bắt buộc, mặc định 0 |  |
| cause\_summary | text | Ví dụ nguồn hiệu chỉnh hồi tố ngày 10/07/2026 (period.retro\_change) |
| retro\_change\_ids | uuid\[\], bắt buộc, mặc định '{}' | logic → period.retro\_change giải thích khác biệt |
| replay\_text | text | Câu chữ sinh lại khi mode = figures\_and\_text |
| replay\_model | text | Mô hình đã sinh lại câu chữ |
| query\_run\_ids | uuid\[\], bắt buộc, mặc định '{}' | logic → query.query\_run (caller\_kind receipt\_replay) |
| error\_book\_entry\_id | uuid | logic → eval.error\_book\_entry khi khác biệt không giải thích được |
| started\_at | timestamptz, bắt buộc, mặc định now() |  |
| finished\_at | timestamptz |  |

Cột chung: id, org\_id, created\_at. Ràng buộc: CHECK (mode IN ('figures','figures\_and\_text')); CHECK (status IN ('running','completed','failed')); CHECK (outcome IS NULL OR outcome IN ('identical','differs\_explained','differs','not\_reproducible')); CHECK (status <> 'completed' OR (outcome IS NOT NULL AND finished\_at IS NOT NULL)); CHECK (outcome <> 'identical' OR differs\_count = 0); CHECK (outcome NOT IN ('differs','differs\_explained') OR differs\_count > 0); CHECK (outcome <> 'differs\_explained' OR cardinality(retro\_change\_ids) > 0 OR cause\_summary IS NOT NULL); CHECK (mode = 'figures\_and\_text' OR replay\_text IS NULL); FOREIGN KEY (receipt\_id) REFERENCES qa.answer\_receipt(id). Chỉ mục: (receipt\_id, started\_at DESC). RLS: org\_isolation, theo biên nhận.

Trigger: sau UPDATE sang completed với outcome differs hoặc not\_reproducible: phát qa.receipt\_replay.mismatch để Eval service tạo mục Error Book source\_kind replay\_mismatch (BR-179) và ghi lại error\_book\_entry\_id; mọi lần chạy lại ghi audit.audit\_log action receipt.replay.

### D4.7 Schema query

#### query.query\_run

Một lần chạy SQL: SQL đã viết lại theo chính sách, nguồn, thời điểm, số dòng, thời gian. Use case: UC-01-01, UC-01-02, UC-01-03, UC-01-04, UC-01-06, UC-02-02, UC-03-01, UC-03-02, UC-10-01, UC-10-05, UC-12-02.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| data\_space\_id | uuid, bắt buộc |  |
| caller\_kind | text, bắt buộc | question, tile, skill\_run, agent\_run, pipeline, period, eval, receipt\_replay (UC-01-07) |
| caller\_id | uuid, bắt buộc |  |
| principal\_id | uuid, bắt buộc | Danh tính chạy truy vấn (người hoặc giao quyền của agent) |
| source\_id | uuid | logic → catalog.source; null khi đọc kỳ chốt |
| route | text, bắt buộc | live, history, period\_snapshot (bộ định tuyến thời điểm) |
| period\_close\_id | uuid | logic → period.period\_close |
| sql\_generated | text, bắt buộc |  |
| sql\_executed | text, bắt buộc | Sau khi Policy engine viết lại |
| policies\_applied | uuid\[\], bắt buộc, mặc định '{}' | logic → governance.access\_policy |
| metric\_version\_ids | uuid\[\], bắt buộc, mặc định '{}' |  |
| as\_of | date |  |
| row\_count | integer |  |
| result\_hash | text |  |
| result\_ref | text | MinIO da-results |
| duration\_ms | integer |  |
| status | text, bắt buộc, mặc định 'running' | queued, running, succeeded, failed, cancelled, timeout |
| error | text |  |
| started\_at | timestamptz, bắt buộc, mặc định now() |  |

Cột chung: id, org\_id. Phân vùng theo tháng trên started\_at. Ràng buộc: CHECK (caller\_kind IN ('question','tile','skill\_run','agent\_run','pipeline','period','eval','receipt\_replay')); CHECK (route IN ('live','history','period\_snapshot')); CHECK (route <> 'period\_snapshot' OR period\_close\_id IS NOT NULL); CHECK (status IN ('queued','running','succeeded','failed','cancelled','timeout')). Chỉ mục: (caller\_kind, caller\_id); (source\_id, started\_at) WHERE status = 'running'.

#### query.query\_result

Kết quả rút gọn (tối đa 1.000 dòng) để hiển thị lại nhanh; giữ 30 ngày, bản đủ ở MinIO. Use case: UC-01-02, UC-02-02.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| query\_run\_id | uuid, bắt buộc | logic → query\_run (bảng phân vùng) |
| columns | jsonb, bắt buộc | Tên, kiểu, đơn vị |
| rows | jsonb, bắt buộc | Đã che theo chính sách của người chạy |
| truncated | boolean, bắt buộc, mặc định false |  |
| expires\_at | timestamptz, bắt buộc, mặc định now() + interval '30 days' |  |

Cột chung: id, org\_id, created\_at. Ràng buộc: UNIQUE (query\_run\_id). Chỉ mục: (expires\_at).

### D4.8 Schema agent

#### agent.agent\_def

Agent: tên, loại, chủ sở hữu, tài khoản agent, trạng thái; cấu hình chi tiết nằm ở agent\_config. Use case: UC-08-01, UC-13-01.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| data\_space\_id | uuid, bắt buộc | logic → org.data\_space |
| code | text, bắt buộc |  |
| name | text, bắt buộc | Ví dụ Agent Đối soát |
| kind | text, bắt buộc | analysis, monitor, report, reconcile, data\_prep, data\_quality, document, custom |
| owner\_user\_id | uuid, bắt buộc | Người thật sở hữu (BR-147); logic → org.app\_user |
| account\_user\_id | uuid, bắt buộc | Tài khoản agent (app\_user.kind = agent) |
| template\_ref | uuid | logic → pack.pack\_skill nếu tạo từ agent mẫu |
| current\_config\_version | integer |  |
| status | text, bắt buộc, mặc định 'draft' | draft, testing, running, paused, retired |
| paused\_reason | text | Ví dụ chủ sở hữu rời tổ chức |
| monthly\_budget | numeric(14,2) | Ngân sách tháng (BR-71) |
| spent\_this\_month | numeric(14,2), bắt buộc, mặc định 0 |  |
| deleted\_at | timestamptz | Xoá mềm |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: UNIQUE (data\_space\_id, code); CHECK (kind IN ('analysis','monitor','report','reconcile','data\_prep','data\_quality','document','custom')); CHECK (status IN ('draft','testing','running','paused','retired')).

#### agent.agent\_config

Cấu hình agent theo phiên bản; sửa luôn tạo phiên bản mới (BR-69). Các trường khớp màn P13 · Agent · Cấu hình & trigger. Use case: UC-03-06, UC-13-01, UC-13-02.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| agent\_id | uuid, bắt buộc | FK agent\_def |
| version | integer, bắt buộc |  |
| goal | text, bắt buộc | Mục tiêu một câu |
| data\_scope | jsonb, bắt buộc, mặc định '{}' | Nguồn, bảng, chỉ số, bộ sưu tập được đọc; không vượt quyền chủ sở hữu |
| skill\_ids | uuid\[\], bắt buộc, mặc định '{}' | logic → skill.skill |
| autonomy | text, bắt buộc, mặc định 'propose\_approve' | suggest\_only (Chỉ gợi ý), propose\_approve (Đề xuất, người duyệt), act\_then\_report (Tự làm, báo sau) |
| checkpoints | jsonb, bắt buộc, mặc định '\[\]' | Điều kiện phải dừng và hỏi, ví dụ hai nguồn lệch vượt ngưỡng |
| denied\_actions | text\[\], bắt buộc, mặc định '{}' | Ví dụ sửa dữ liệu gốc, xem số tài khoản, gửi ra ngoài |
| limits | jsonb, bắt buộc, mặc định '{}' | max\_steps, max\_tokens, query\_timeout\_s, max\_cost\_per\_run, max\_duration\_min |
| model\_by\_task | jsonb, bắt buộc, mặc định '{}' | Mô hình theo loại việc; trống thì dùng governance.model\_config |
| change\_note | text |  |
| status | text, bắt buộc, mặc định 'draft' | draft, testing, passed, deployed, superseded |
| eval\_run\_id | uuid | logic → eval.eval\_run |

Cột chung: id, org\_id, created\_at. Ràng buộc: UNIQUE (agent\_id, version); CHECK (autonomy IN ('suggest\_only','propose\_approve','act\_then\_report')); CHECK (status IN ('draft','testing','passed','deployed','superseded')); CHECK (status NOT IN ('passed','deployed') OR eval\_run\_id IS NOT NULL); FOREIGN KEY (agent\_id) REFERENCES agent.agent\_def(id).

#### agent.agent\_tool

Công cụ một phiên bản cấu hình được dùng và mức quyền. Use case: UC-13-01, UC-13-02.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| agent\_config\_id | uuid, bắt buộc | FK agent\_config |
| tool | text, bắt buộc | Ví dụ query, run\_skill, create\_proposal, notify, agent.call |
| permission\_level | text, bắt buộc | read, write\_derived, send\_external |
| requires\_checkpoint | boolean, bắt buộc, mặc định false | Bắt buộc true với write\_derived trừ khi act\_then\_report và có chính sách; luôn true với send\_external (BR-70) |
| allowed\_targets | uuid\[\] | Với agent.call: danh sách agent được gọi |

Cột chung: id, org\_id, created\_at. Ràng buộc: UNIQUE (agent\_config\_id, tool); CHECK (permission\_level IN ('read','write\_derived','send\_external')); CHECK (permission\_level <> 'send\_external' OR requires\_checkpoint); FOREIGN KEY (agent\_config\_id) REFERENCES agent.agent\_config(id) ON DELETE CASCADE.

#### agent.agent\_trigger

Khi nào agent chạy: lịch, sự kiện, ngưỡng, agent khác gọi, thủ công. Use case: UC-08-01, UC-13-03.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| agent\_id | uuid, bắt buộc | FK agent\_def |
| kind | text, bắt buộc | schedule, event, threshold, agent\_call, manual |
| cron | text |  |
| work\_calendar\_id | uuid | logic → org.work\_calendar |
| event\_type | text | Ví dụ period.closed, job.succeeded |
| condition | jsonb, bắt buộc, mặc định '{}' | Ví dụ lệch > 0,1% so với hôm qua |
| min\_interval | interval | Khoảng cách tối thiểu giữa hai lượt; tín hiệu lặp được gộp (BR-72) |
| no\_overlap | boolean, bắt buộc, mặc định true |  |
| combine | text, bắt buộc, mặc định 'any' | any, all |
| enabled | boolean, bắt buộc, mặc định true |  |
| temporal\_schedule\_id | text |  |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: CHECK (kind IN ('schedule','event','threshold','agent\_call','manual')); CHECK (combine IN ('any','all')); CHECK ((kind <> 'schedule' OR cron IS NOT NULL) AND (kind <> 'event' OR event\_type IS NOT NULL)); FOREIGN KEY (agent\_id) REFERENCES agent.agent\_def(id).

#### agent.agent\_deployment

Triển khai một phiên bản cấu hình theo tỷ lệ lượt, kèm cổng chất lượng và quay lui (SD-17). Use case: UC-13-05.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| agent\_id | uuid, bắt buộc | FK agent\_def |
| config\_version | integer, bắt buộc |  |
| previous\_version | integer |  |
| traffic\_pct | smallint, bắt buộc, mặc định 10 | 10, 50, 100 |
| gates | jsonb, bắt buộc | Ngưỡng quay lui (BR-148): lỗi, tỷ lệ cần người, chi phí, số lượt tối thiểu |
| observed | jsonb, bắt buộc, mặc định '{}' | Số đo trong cửa sổ quan sát |
| status | text, bắt buộc, mặc định 'pending\_approval' | pending\_approval, rolling, completed, rolled\_back, cancelled |
| rollback\_reason | text |  |
| approved\_by | uuid |  |
| hitl\_request\_id | uuid | logic → inbox.hitl\_request |
| started\_at | timestamptz |  |
| finished\_at | timestamptz |  |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: CHECK (traffic\_pct BETWEEN 0 AND 100); CHECK (status IN ('pending\_approval','rolling','completed','rolled\_back','cancelled')); CHECK (status <> 'rolled\_back' OR rollback\_reason IS NOT NULL); FOREIGN KEY (agent\_id) REFERENCES agent.agent\_def(id). Chỉ mục: duy nhất (agent\_id) WHERE status IN ('pending\_approval','rolling'), để mỗi agent chỉ có một lần triển khai đang diễn ra.

#### agent.agent\_run

Một lượt chạy của agent (màn Lượt chạy); lượt con của điều phối nhiều agent trỏ về lượt cha. Use case: UC-02-05, UC-02-06, UC-07-07, UC-08-05, UC-11-03, UC-13-04, UC-13-05, UC-13-06, UC-13-07, UC-13-08.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| agent\_id | uuid, bắt buộc | FK agent\_def |
| config\_version | integer, bắt buộc | Giữ nguyên suốt lượt chạy (BR-69) |
| data\_space\_id | uuid, bắt buộc |  |
| trigger\_kind | text, bắt buộc | schedule, event, threshold, agent\_call, manual, test |
| trigger\_ref | text | Mã sự kiện hoặc lịch |
| initiated\_by | uuid | Người giao việc hoặc khởi chạy |
| parent\_run\_id | uuid | Lượt cha (UC-13-08) |
| depth | smallint, bắt buộc, mặc định 0 | Tối đa 3 (BR-77) |
| is\_test | boolean, bắt buộc, mặc định false | Chạy thử: công cụ ghi và gửi ở chế độ giả lập |
| workflow\_id | text | Temporal |
| as\_of | date |  |
| status | text, bắt buộc, mặc định 'queued' | queued, planning, running, waiting\_human, paused, succeeded, failed, cancelled |
| current\_step | integer |  |
| narrative | jsonb, bắt buộc, mặc định '\[\]' | Diễn biến kể thành câu cho màn Đang làm gì |
| cost | numeric(14,2), bắt buộc, mặc định 0 | Chi phí đã tốn |
| tokens | integer, bắt buộc, mặc định 0 |  |
| waiting\_since | timestamptz | Chờ người quá 7 ngày thì tự huỷ (UC-13-06 E2) |
| started\_at | timestamptz |  |
| finished\_at | timestamptz |  |
| error | text |  |
| stop\_reason | text | completed, loop\_detected, no\_progress, budget\_steps, budget\_tokens, budget\_cost, budget\_duration, user\_cancelled, waiting\_timeout, error; bắt buộc khi succeeded, failed, cancelled (BR-170) |
| stop\_detail | jsonb, bắt buộc, mặc định '{}' | Chi tiết dừng: công cụ và call\_fingerprint lặp, số lần, bước cuối có tiến triển, ngân sách đã dùng so với trần |
| snapshot\_refs | jsonb, bắt buộc, mặc định '\[\]' | Phiên bản kỹ năng, mô hình, bản chụp đã đọc để chạy lại cho cùng kết quả (BR-75) |

Cột chung: id, org\_id, created\_at. Ràng buộc: CHECK (trigger\_kind IN ('schedule','event','threshold','agent\_call','manual','test')); CHECK (status IN ('queued','planning','running','waiting\_human','paused','succeeded','failed','cancelled')); CHECK (depth BETWEEN 0 AND 3); CHECK ((parent\_run\_id IS NULL) = (depth = 0)); FOREIGN KEY (agent\_id) REFERENCES agent.agent\_def(id); FOREIGN KEY (parent\_run\_id) REFERENCES agent.agent\_run(id). Chỉ mục: (agent\_id) WHERE status IN ('queued','planning','running','waiting\_human','paused'); (parent\_run\_id) WHERE parent\_run\_id IS NOT NULL.

Bổ sung 10/10/2026 (BR-170): CHECK (stop\_reason IS NULL OR stop\_reason IN ('completed','loop\_detected','no\_progress','budget\_steps','budget\_tokens','budget\_cost','budget\_duration','user\_cancelled','waiting\_timeout','error')); CHECK (status NOT IN ('succeeded','failed','cancelled') OR stop\_reason IS NOT NULL); chỉ mục (agent\_id, stop\_reason) WHERE stop\_reason IN ('loop\_detected','no\_progress'). DDL ở dataagent.sql (sinh bằng csdl/gen\_ddl.py, 10/10/2026). Trigger agent\_run\_depth\_guard (BR-77): lượt gốc depth = 0, lượt con depth = depth lượt cha + 1, giá trị dịch vụ gửi lên bị ghi đè; lượt cha khác tổ chức hoặc tầng thứ tư thì chặn.

#### agent.agent\_step

Một bước trong lượt chạy: gọi mô hình, công cụ, SQL, agent khác; nguồn của màn Lượt chạy · Kỹ thuật. Use case: UC-08-05, UC-11-03, UC-13-06, UC-13-08.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| run\_id | uuid, bắt buộc | logic → agent\_run (bảng phân vùng) |
| seq | integer, bắt buộc |  |
| kind | text, bắt buộc | plan, llm\_call, tool\_call, query, skill, agent\_call, checkpoint, message |
| name | text, bắt buộc | Tên công cụ hoặc bước |
| input | jsonb | Đã che dữ liệu cá nhân |
| output | jsonb |  |
| summary | text | Câu kể cho người dùng |
| status | text, bắt buộc | ok, error, retried, skipped, simulated |
| attempt | smallint, bắt buộc, mặc định 1 | Thử lại tối đa 3 lần (UC-13-06 E1) |
| child\_run\_id | uuid | Khi kind = agent\_call |
| langfuse\_span\_id | text |  |
| cost | numeric(12,4), bắt buộc, mặc định 0 |  |
| started\_at | timestamptz, bắt buộc |  |
| duration\_ms | integer |  |
| call\_fingerprint | text | SHA-256 của tên công cụ và tham số đã chuẩn hoá (khoá sắp xếp, bỏ khoảng trắng, ngày về ISO, bỏ idempotency\_key); dùng phát hiện lặp (BR-170) |
| result\_hash | text | SHA-256 của kết quả đã chuẩn hoá (hoặc của thông báo lỗi đã che); cùng call\_fingerprint và result\_hash từ 3 lần trong một lượt là lặp (BR-170) |

Cột chung: id, org\_id. Phân vùng theo tháng trên started\_at. Ràng buộc: CHECK (kind IN ('plan','llm\_call','tool\_call','query','skill','agent\_call','checkpoint','message')); CHECK (status IN ('ok','error','retried','skipped','simulated')). Chỉ mục: (run\_id, seq).

Bổ sung 10/10/2026 (BR-170): chỉ mục (run\_id, call\_fingerprint) WHERE call\_fingerprint IS NOT NULL để đếm lặp trong lượt (DDL ở dataagent.sql); phát hiện lặp do Agent runtime làm, CSDL chỉ cung cấp chỉ mục.

#### agent.agent\_checkpoint\_hit

Lần lượt chạy dừng ở một checkpoint và tạo yêu cầu cho người. Use case: UC-07-07, UC-13-06.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| run\_id | uuid, bắt buộc | FK agent\_run |
| checkpoint | text, bắt buộc | Điều kiện đã khớp |
| hitl\_request\_id | uuid, bắt buộc | logic → inbox.hitl\_request |
| resolved\_at | timestamptz |  |
| outcome | text | approved, rejected, edited, expired |

Cột chung: id, org\_id, created\_at. Ràng buộc: CHECK (outcome IS NULL OR outcome IN ('approved','rejected','edited','expired')); FOREIGN KEY (run\_id) REFERENCES agent.agent\_run(id).

#### agent.agent\_memory

Ghi nhớ của agent; không chứa dữ liệu cá nhân, chính sách luôn thắng (BR-76). Use case: UC-13-07.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| agent\_id | uuid | FK agent\_def; null khi dùng chung cả Data Space |
| data\_space\_id | uuid, bắt buộc |  |
| scope | text, bắt buộc | agent, user, data\_space |
| user\_id | uuid | Khi scope = user |
| kind | text, bắt buộc | decision, preference, data\_insight, domain\_context |
| content | text, bắt buộc |  |
| source\_kind | text, bắt buộc | hitl, run, manual, pack |
| source\_ref | uuid |  |
| expires\_at | timestamptz |  |
| use\_count | integer, bắt buộc, mặc định 0 |  |
| correction\_count | smallint, bắt buộc, mặc định 0 | Đến 2 thì tự vô hiệu |
| status | text, bắt buộc, mặc định 'active' | pending\_review, active, disabled, conflict |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: CHECK (scope IN ('agent','user','data\_space')); CHECK ((scope = 'agent' AND agent\_id IS NOT NULL) OR (scope = 'user' AND user\_id IS NOT NULL) OR scope = 'data\_space'); CHECK (kind IN ('decision','preference','data\_insight','domain\_context')); CHECK (source\_kind IN ('hitl','run','manual','pack')); CHECK (status IN ('pending\_review','active','disabled','conflict')); CHECK (scope <> 'data\_space' OR status <> 'active' OR updated\_by IS NOT NULL); FOREIGN KEY (agent\_id) REFERENCES agent.agent\_def(id).

#### agent.background\_task

Việc người dùng giao bằng lời cho agent chạy nền. Use case: UC-08-05.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| requested\_by | uuid, bắt buộc |  |
| agent\_id | uuid, bắt buộc | FK agent\_def |
| description | text, bắt buộc |  |
| due\_at | timestamptz |  |
| workspace\_id | uuid | logic → workspace.workspace nơi đổ kết quả |
| run\_id | uuid | FK agent\_run |
| status | text, bắt buộc, mặc định 'pending\_confirmation' | pending\_confirmation, running, done, failed, cancelled |
| result\_artifact\_id | uuid | logic → workspace.artifact |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: CHECK (status IN ('pending\_confirmation','running','done','failed','cancelled')); FOREIGN KEY (agent\_id) REFERENCES agent.agent\_def(id); FOREIGN KEY (run\_id) REFERENCES agent.agent\_run(id).

### D4.9 Schema eval

#### eval.eval\_set

Bộ đánh giá dùng chung cho lớp ngữ cảnh, kỹ năng, agent và gói ngành. Use case: UC-07-03, UC-07-06, UC-13-04.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| data\_space\_id | uuid | Null với bộ của gói ngành |
| subject\_type | text, bắt buộc | context, skill, agent, pack |
| subject\_id | uuid | Kỹ năng, agent hoặc phiên bản gói; null với lớp ngữ cảnh của Data Space |
| name | text, bắt buộc |  |
| pass\_threshold | numeric(5,4), bắt buộc, mặc định 0.95 | Tỷ lệ đạt tối thiểu |
| min\_items | integer, bắt buộc, mặc định 100 | Lớp ngữ cảnh cần ít nhất 100 câu |
| status | text, bắt buộc, mặc định 'active' | active, archived |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: CHECK (subject\_type IN ('context','skill','agent','pack')); CHECK ((subject\_type = 'context' AND data\_space\_id IS NOT NULL) OR (subject\_type <> 'context' AND subject\_id IS NOT NULL)); CHECK (status IN ('active','archived')).

#### eval.eval\_item

Một mục đánh giá kèm đáp án: câu hỏi chuẩn, ca kiểm thử kỹ năng, tình huống thử agent. Use case: UC-01-05, UC-03-03, UC-03-04, UC-07-06, UC-13-04.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| eval\_set\_id | uuid, bắt buộc | FK eval\_set |
| category | text, bắt buộc, mặc định 'correct\_result' | correct\_result, ask\_at\_right\_time, respect\_permission, correct\_format, refusal |
| input | jsonb, bắt buộc | Câu hỏi hoặc tham số đầu vào, đã che dữ liệu cá nhân (BR-74) |
| as\_of | date |  |
| expected | jsonb, bắt buộc | Cách hiểu, con số kèm dung sai, hành vi mong đợi |
| tolerance | numeric(10,6) |  |
| origin | text, bắt buộc, mặc định 'manual' | manual, feedback, real\_run, hitl\_rule, generated, pack, error\_book (ca hồi quy từ Error Book, origin\_ref = eval.error\_book\_entry.id, UC-07-14) |
| origin\_ref | uuid | Ví dụ qa.feedback.id |
| status | text, bắt buộc, mặc định 'active' | active, needs\_update, retired |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: CHECK (category IN ('correct\_result','ask\_at\_right\_time','respect\_permission','correct\_format','refusal')); CHECK (origin IN ('manual','feedback','real\_run','hitl\_rule','generated','pack','error\_book')); CHECK (origin <> 'error\_book' OR origin\_ref IS NOT NULL); CHECK (status IN ('active','needs\_update','retired')); FOREIGN KEY (eval\_set\_id) REFERENCES eval.eval\_set(id).

#### eval.eval\_run

Một lần chạy bộ đánh giá trên một phiên bản đối tượng (bản đang dùng hoặc bản nháp). Use case: UC-07-03, UC-07-06, UC-11-05, UC-13-04.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| eval\_set\_id | uuid, bắt buộc | FK eval\_set |
| subject\_version | text, bắt buộc | Ví dụ context@draft-proposal, agent\_config v6, model\_config sql\_generation v3 |
| trigger | text, bắt buộc | proposal, nightly, model\_change, manual, deploy |
| trigger\_ref | uuid | Ví dụ inbox.proposal.id |
| item\_count | integer, bắt buộc, mặc định 0 |  |
| passed | integer, bắt buộc, mặc định 0 |  |
| failed | integer, bắt buộc, mặc định 0 |  |
| correct\_refusals | integer, bắt buộc, mặc định 0 |  |
| pass\_rate | numeric(5,4) |  |
| baseline\_run\_id | uuid | Lần chạy so sánh |
| regressions | integer, bắt buộc, mặc định 0 | Số mục đang đạt chuyển sang trượt; lớn hơn 0 thì người duyệt phải xem từng mục (BR-28) |
| status | text, bắt buộc, mặc định 'running' | running, completed, failed |
| started\_at | timestamptz, bắt buộc, mặc định now() |  |
| finished\_at | timestamptz |  |

Cột chung: id, org\_id, created\_at. Ràng buộc: CHECK (trigger IN ('proposal','nightly','model\_change','manual','deploy')); CHECK (status IN ('running','completed','failed')); FOREIGN KEY (eval\_set\_id) REFERENCES eval.eval\_set(id); FOREIGN KEY (baseline\_run\_id) REFERENCES eval.eval\_run(id).

#### eval.eval\_result

Kết quả một mục trong một lần đánh giá, chấm ba tầng: cách hiểu, con số, hành vi. Use case: UC-07-03, UC-07-06.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| eval\_run\_id | uuid, bắt buộc | FK eval\_run |
| eval\_item\_id | uuid, bắt buộc | FK eval\_item |
| understanding\_ok | boolean |  |
| value\_ok | boolean |  |
| behavior\_ok | boolean |  |
| passed | boolean, bắt buộc |  |
| actual | jsonb | Cách agent hiểu, giá trị trả về |
| cause | text | Nguyên nhân sai do hệ thống gợi ý |
| changed\_from\_baseline | boolean, bắt buộc, mặc định false |  |
| reviewed\_by | uuid | Người duyệt đã xem mục đổi kết quả |

Cột chung: id, org\_id, created\_at. Ràng buộc: UNIQUE (eval\_run\_id, eval\_item\_id); FOREIGN KEY (eval\_run\_id) REFERENCES eval.eval\_run(id) ON DELETE CASCADE; FOREIGN KEY (eval\_item\_id) REFERENCES eval.eval\_item(id).

#### eval.error\_book\_entry

Mục Error Book: một lỗi đã xác nhận của câu trả lời hoặc một dự báo lệch vượt ngưỡng, kèm loại lỗi, nguyên nhân gốc, người xử lý, cách sửa và ca hồi quy tự thêm vào bộ đánh giá chạy trong CI. Trạng thái open → fixed → regression\_added; dismissed khi không phải lỗi hoặc trùng (BR-179). Là kênh “tốt dần theo thời gian” cùng gói ngành: cách sửa dùng chung được đề xuất về P09. Thêm 10/10/2026. Use case: UC-07-14, UC-01-05, UC-01-07, UC-15-06.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| entry\_no | text, bắt buộc | Mã hiển thị, ví dụ EB-0042; trigger cấp theo tổ chức |
| data\_space\_id | uuid, bắt buộc | logic → org.data\_space |
| data\_product\_id | uuid | logic → context.data\_product; null khi câu hỏi ngoài phạm vi |
| source\_kind | text, bắt buộc | user\_report, number\_check, reviewer\_reject, replay\_mismatch, forecast\_miss, knowledge\_lint |
| feedback\_id | uuid | logic → qa.feedback; bắt buộc khi user\_report |
| answer\_id | uuid | logic → qa.answer |
| receipt\_id | uuid | logic → qa.answer\_receipt |
| hitl\_request\_id | uuid | logic → inbox.hitl\_request; bắt buộc khi reviewer\_reject |
| replay\_id | uuid | logic → qa.receipt\_replay; bắt buộc khi replay\_mismatch |
| accuracy\_log\_id | uuid | logic → forecast.accuracy\_log; bắt buộc khi forecast\_miss |
| forecast\_detail | jsonb | Với forecast\_miss: {model\_version\_id, period, actual, point, lo95, hi95, abs\_scaled\_error, threshold\_rule, ledger\_entry\_id} (ledger\_entry\_id khi mục từ sổ dự báo, ngưỡng theo BR-186) do Eval service lấy từ payload sự kiện forecast.ledger.missed |
| lint\_finding\_id | uuid | logic → context.knowledge\_lint\_finding; bắt buộc khi knowledge\_lint |
| title | text, bắt buộc | Ví dụ Mức giảm E5RON92 kỳ 02/07/2026 ghi 1.155 đ/lít, công bố 1.055 đ/lít |
| detail | jsonb, bắt buộc, mặc định '{}' | Số đúng, số sai, câu hỏi đã che dữ liệu cá nhân, chip, figure\_key |
| severity | text, bắt buộc, mặc định 'medium' | low, medium, high; high khi số đã vào báo cáo phát hành hoặc lệch dự báo gấp 2 lần ngưỡng |
| error\_kind | text | definition, synonym, data, freshness, model, formula, retrieval; bắt buộc từ fixed |
| root\_cause | text | Nguyên nhân gốc bằng lời; bắt buộc từ fixed |
| assignee\_id | uuid | logic → org.app\_user; mặc định steward của data product, không có thì người phụ trách Data Space |
| fix\_kind | text | metric\_version, synonym, example, knowledge\_page, pack\_version, source\_data, model\_config, prompt, code, none |
| fix\_proposal\_id | uuid | logic → inbox.proposal; đề xuất sửa chỉ số, đồng nghĩa, trang tri thức, gói ngành |
| fix\_ref | text | Tham chiếu khác: mã commit, phiên bản model\_config, phiên bản dataset đã sửa |
| fixed\_by | uuid | logic → org.app\_user |
| fixed\_at | timestamptz |  |
| eval\_item\_id | uuid | logic → eval.eval\_item (origin error\_book) |
| regression\_run\_id | uuid | FK eval\_run; lần chạy CI đầu tiên sau fixed\_at mà ca hồi quy đạt |
| status | text, bắt buộc, mặc định 'open' | open, fixed, regression\_added, dismissed |
| dismiss\_reason | text | Bắt buộc khi dismissed mà không trùng mục khác |
| duplicate\_of\_id | uuid | FK error\_book\_entry |
| reopened\_count | integer, bắt buộc, mặc định 0 | Ca hồi quy trượt sau khi đóng thì mở lại và tăng |
| pack\_contribution | text, bắt buộc, mặc định 'none' | none, proposed, accepted, rejected; đề xuất đưa cách sửa về gói ngành (UC-09-05) |
| pack\_proposal\_ref | text | Mã đề xuất phía gói ngành, đã bỏ dữ liệu của khách |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: UNIQUE (org\_id, entry\_no); CHECK (source\_kind IN ('user\_report','number\_check','reviewer\_reject','replay\_mismatch','forecast\_miss','knowledge\_lint')); CHECK (source\_kind <> 'user\_report' OR feedback\_id IS NOT NULL); CHECK (source\_kind <> 'reviewer\_reject' OR hitl\_request\_id IS NOT NULL); CHECK (source\_kind <> 'replay\_mismatch' OR replay\_id IS NOT NULL); CHECK (source\_kind <> 'forecast\_miss' OR (accuracy\_log\_id IS NOT NULL AND forecast\_detail IS NOT NULL)); CHECK (source\_kind <> 'knowledge\_lint' OR lint\_finding\_id IS NOT NULL); CHECK (source\_kind NOT IN ('user\_report','number\_check','reviewer\_reject') OR answer\_id IS NOT NULL); CHECK (severity IN ('low','medium','high')); CHECK (error\_kind IS NULL OR error\_kind IN ('definition','synonym','data','freshness','model','formula','retrieval')); CHECK (fix\_kind IS NULL OR fix\_kind IN ('metric\_version','synonym','example','knowledge\_page','pack\_version','source\_data','model\_config','prompt','code','none')); CHECK (status IN ('open','fixed','regression\_added','dismissed')); CHECK (status NOT IN ('fixed','regression\_added') OR (error\_kind IS NOT NULL AND root\_cause IS NOT NULL AND assignee\_id IS NOT NULL AND fix\_kind IS NOT NULL AND fixed\_by IS NOT NULL AND fixed\_at IS NOT NULL)); CHECK (status NOT IN ('fixed','regression\_added') OR fix\_kind = 'none' OR fix\_proposal\_id IS NOT NULL OR fix\_ref IS NOT NULL); CHECK (status <> 'regression\_added' OR (eval\_item\_id IS NOT NULL AND regression\_run\_id IS NOT NULL)); CHECK (status <> 'dismissed' OR dismiss\_reason IS NOT NULL OR duplicate\_of\_id IS NOT NULL); CHECK (duplicate\_of\_id IS NULL OR duplicate\_of\_id <> id); CHECK (pack\_contribution IN ('none','proposed','accepted','rejected')); CHECK (pack\_contribution = 'none' OR status IN ('fixed','regression\_added')); FOREIGN KEY (duplicate\_of\_id) REFERENCES eval.error\_book\_entry(id); FOREIGN KEY (regression\_run\_id) REFERENCES eval.eval\_run(id). Chỉ mục: duy nhất (feedback\_id) WHERE feedback\_id IS NOT NULL; duy nhất (accuracy\_log\_id) WHERE accuracy\_log\_id IS NOT NULL; duy nhất (replay\_id) WHERE replay\_id IS NOT NULL; (data\_space\_id, status); (data\_product\_id, status); (assignee\_id) WHERE status = 'open'. RLS: org\_isolation; đọc theo quyền Data Space, cột detail che theo quyền người xem.

Trigger: trước INSERT: cấp entry\_no; assignee\_id null thì Eval service điền steward của data product. Trước UPDATE: chuyển trạng thái chỉ theo open → fixed → regression\_added, open → dismissed, và regression\_added → open khi mở lại; sang fixed với fix\_proposal\_id thì đề xuất phải ở accepted (Eval service kiểm qua inbox\_read); regression\_added chỉ do Eval service đặt khi eval\_result của eval\_item\_id passed trong một eval\_run trigger deploy hoặc nightly có started\_at sau fixed\_at. Sau INSERT: phát eval.error\_book.opened để Notification service báo người xử lý. Hàm eval.error\_book\_on\_regression\_failed(eval\_item\_id), gọi khi eval\_result passed = false: mục regression\_added tương ứng về open, reopened\_count tăng 1, phát eval.error\_book.reopened.

### D4.10 Schema inbox

#### inbox.proposal

Đề xuất thay đổi chờ duyệt: lớp ngữ cảnh, schema, kỹ năng, gói ngành, ghi ngược, hiệu lực văn bản. Use case: UC-01-05, UC-03-05, UC-04-03, UC-05-05, UC-06-04, UC-07-01, UC-07-02, UC-07-03, UC-07-05, UC-07-06, UC-09-02, UC-09-03, UC-09-04.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| data\_space\_id | uuid, bắt buộc |  |
| kind | text, bắt buộc | metric\_change, synonym, relationship, example, glossary, schema\_change, skill\_publish, pack\_activate, pack\_upgrade, metric\_override, source\_authority, writeback, document\_validity, feedback\_fix, data\_product (kích hoạt hoặc đổi phiên bản data product, UC-07-13), knowledge\_page |
| target\_service | text, bắt buộc | Dịch vụ sở hữu đối tượng, sẽ phát hành khi chấp nhận |
| target\_ref | uuid | Đối tượng bị thay đổi |
| title | text, bắt buộc |  |
| diff | jsonb, bắt buộc, mặc định '{}' |  |
| reason | text |  |
| evidence | jsonb, bắt buộc, mặc định '\[\]' | Ví dụ 4 câu hỏi bị sửa giống nhau |
| impact | jsonb, bắt buộc, mặc định '{}' | Số agent, ô, báo cáo, kỹ năng bị ảnh hưởng |
| impact\_level | text, bắt buộc, mặc định 'low' | low, medium, high |
| proposed\_by | uuid, bắt buộc | Người hoặc tài khoản agent |
| assignee\_id | uuid |  |
| eval\_run\_id | uuid | logic → eval.eval\_run |
| requires\_compliance | boolean, bắt buộc, mặc định false | Đổi chỉ số dùng trong báo cáo đã nộp cần thêm AC06 (BR-119) |
| status | text, bắt buộc, mặc định 'open' | open, evaluating, pending\_review, accepted, applied, apply\_failed, rejected, expired, withdrawn |
| expires\_at | timestamptz, bắt buộc, mặc định now() + interval '30 days' | Quá hạn chuyển expired (BR-29) |
| applied\_at | timestamptz |  |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: CHECK (kind IN ('metric\_change','synonym','relationship','example','glossary','schema\_change','skill\_publish','pack\_activate','pack\_upgrade','metric\_override','source\_authority','writeback','document\_validity','feedback\_fix','knowledge\_page','data\_product')); CHECK (impact\_level IN ('low','medium','high')); CHECK (status IN ('open','evaluating','pending\_review','accepted','applied','apply\_failed','rejected','expired','withdrawn')). Chỉ mục: (data\_space\_id, status) WHERE status IN ('open','evaluating','pending\_review'); (assignee\_id) WHERE status = 'pending\_review'.

#### inbox.proposal\_review

Một quyết định duyệt; người duyệt phải khác người đề xuất (kiểm ở tầng ứng dụng). Use case: UC-07-03.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| proposal\_id | uuid, bắt buộc | FK proposal |
| step | smallint, bắt buộc, mặc định 1 | Bước trong chuỗi duyệt nhiều người |
| reviewer\_id | uuid, bắt buộc |  |
| decision | text, bắt buộc | accept, accept\_with\_edit, reject, request\_changes |
| note | text | Bắt buộc khi reject (UC-07-03 4b) |
| reviewed\_regressions | boolean, bắt buộc, mặc định false | Đã xem từng mục bị đổi kết quả (BR-28) |
| decided\_at | timestamptz, bắt buộc, mặc định now() |  |

Cột chung: id, org\_id. Ràng buộc: CHECK (decision IN ('accept','accept\_with\_edit','reject','request\_changes')); CHECK (decision <> 'reject' OR note IS NOT NULL); UNIQUE (proposal\_id, step, reviewer\_id); FOREIGN KEY (proposal\_id) REFERENCES inbox.proposal(id).

#### inbox.hitl\_request

Yêu cầu người quyết định trong Cần bạn; mọi dịch vụ tạo yêu cầu qua đây. Use case: UC-01-05, UC-01-06, UC-02-07, UC-03-05, UC-04-07, UC-06-04, UC-07-03, UC-07-04, UC-07-07, UC-07-08, UC-08-04, UC-08-06, UC-10-02, UC-10-04, UC-11-07, UC-13-05, UC-13-07, UC-13-08.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| data\_space\_id | uuid | Rỗng với yêu cầu toàn tổ chức (bản xuất nhật ký kiểm toán, quyền khẩn cấp); khi đó năng lực của người duyệt xét ở cấp tổ chức. Sửa 12/10/2026 |
| kind | text, bắt buộc | approve\_proposal, conflict, clarification, checkpoint, budget, access, export, writeback, period\_exception, retro\_change, deploy, memory\_share, schema\_drift, report\_publish, data\_review, entity\_match, forecast\_assumption, forecast\_model, legal\_review, knowledge\_finding |
| risk | text, bắt buộc, mặc định 'medium' | low, medium, high |
| title | text, bắt buộc |  |
| context | jsonb, bắt buộc, mặc định '{}' | Dữ liệu để người quyết định, ví dụ hai nguồn và mức lệch |
| options | jsonb, bắt buộc, mặc định '\[\]' | Các phương án |
| source\_service | text, bắt buộc |  |
| source\_ref | uuid | Ví dụ agent\_run, proposal, conflict |
| parent\_run\_label | text | Tên lượt chạy cha khi từ lượt con (BR-77) |
| assignee\_id | uuid |  |
| assignee\_group | text |  |
| assignee\_capability | text | Năng lực của người duyệt (steward, compliance, audit\_read…): ai có năng lực này trong Data Space của yêu cầu, hoặc ở cấp tổ chức khi data\_space\_id rỗng, thấy và duyệt được. Thêm 12/10/2026 |
| due\_at | timestamptz |  |
| status | text, bắt buộc, mặc định 'open' | open, answered, approved, rejected, changes\_requested, reassigned, expired, cancelled |
| response | jsonb | Phương án chọn và ghi chú |
| remember\_as\_rule | boolean, bắt buộc, mặc định false | Bật “Ghi nhớ thành quy tắc” (UC-07-08) |
| resolved\_by | uuid |  |
| resolved\_at | timestamptz |  |
| channel\_card\_ids | uuid\[\], bắt buộc, mặc định '{}' | logic → notify.channel\_delivery |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: CHECK (kind IN ('approve\_proposal','conflict','clarification','checkpoint','budget','access','export','writeback','period\_exception','retro\_change','deploy','memory\_share','schema\_drift','report\_publish','data\_review','entity\_match','forecast\_assumption','forecast\_model','legal\_review','knowledge\_finding','formula\_component')); CHECK (risk IN ('low','medium','high')); CHECK (status IN ('open','answered','approved','rejected','changes\_requested','reassigned','expired','cancelled')); CHECK (status IN ('open','reassigned') OR resolved\_at IS NOT NULL); CHECK (assignee\_id IS NOT NULL OR assignee\_group IS NOT NULL OR assignee\_capability IS NOT NULL). Chỉ mục: (assignee\_id, status, due\_at) WHERE status = 'open'; (source\_service, source\_ref).

#### inbox.hitl\_approval\_step

Một bước trong chuỗi duyệt nhiều người, ví dụ ngoại lệ kỳ chốt cần hai người (BR-132). Use case: UC-07-07, UC-10-02.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| hitl\_request\_id | uuid, bắt buộc | FK hitl\_request |
| step | smallint, bắt buộc |  |
| approver\_role | text, bắt buộc | Năng lực người duyệt bước này phải có, ví dụ steward, compliance; mỗi bước một người khác (BR-132) |
| approver\_id | uuid |  |
| decision | text | approve, reject |
| note | text |  |
| decided\_at | timestamptz |  |

Cột chung: id, org\_id, created\_at. Ràng buộc: UNIQUE (hitl\_request\_id, step); CHECK (decision IS NULL OR decision IN ('approve','reject')); CHECK (decision IS NULL OR (approver\_id IS NOT NULL AND decided\_at IS NOT NULL)); FOREIGN KEY (hitl\_request\_id) REFERENCES inbox.hitl\_request(id) ON DELETE CASCADE.

### D4.11 Schema workspace

#### workspace.workspace

Phiên làm việc dạng notebook của người dùng; có tham số dùng chung cho các ô. Use case: UC-02-01, UC-02-06, UC-03-03, UC-03-07.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| data\_space\_id | uuid, bắt buộc |  |
| owner\_id | uuid, bắt buộc | logic → org.app\_user |
| title | text, bắt buộc | Ví dụ Nợ xấu bán lẻ T9 |
| description | text |  |
| params | jsonb, bắt buộc, mặc định '{}' | Tham số phiên, ví dụ kỳ, vùng |
| template\_of\_skill\_id | uuid | logic → skill.skill nếu tạo từ kỹ năng |
| created\_from | text, bắt buộc, mặc định 'blank' | blank, conversation, template, agent\_run |
| created\_from\_ref | uuid |  |
| tile\_count | integer, bắt buộc, mặc định 0 | Tối đa 200 ô (UC-02-01) |
| last\_refreshed\_at | timestamptz |  |
| status | text, bắt buộc, mặc định 'active' | active, archived |
| deleted\_at | timestamptz | Xoá mềm |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: CHECK (created\_from IN ('blank','conversation','template','agent\_run')); CHECK (tile\_count BETWEEN 0 AND 200); CHECK (status IN ('active','archived')). Chỉ mục: (owner\_id) WHERE deleted\_at IS NULL.

#### workspace.tile

Một ô trong phiên: câu hỏi, SQL, biểu đồ, văn bản, kỹ năng, kết quả agent. Use case: UC-02-01, UC-02-02, UC-02-06, UC-03-03, UC-03-07, UC-06-05.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| workspace\_id | uuid, bắt buộc | FK workspace |
| position | integer, bắt buộc |  |
| kind | text, bắt buộc | question, sql, chart, text, skill\_run, agent\_result, table |
| title | text |  |
| spec | jsonb, bắt buộc, mặc định '{}' | Câu hỏi, SQL đã kiểm, cấu hình biểu đồ, tham số kỹ năng |
| depends\_on | uuid\[\], bắt buộc, mặc định '{}' | Ô phụ thuộc; làm mới theo thứ tự phụ thuộc (UC-02-02) |
| author\_kind | text, bắt buộc, mặc định 'user' | user, agent |
| agent\_suggestion\_state | text | suggested, accepted, dismissed khi ô do agent đề xuất |
| latest\_snapshot\_id | uuid | FK tile\_snapshot |
| stale | boolean, bắt buộc, mặc định false | Có dữ liệu mới, cần chạy lại |
| deleted\_at | timestamptz | Xoá mềm |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: CHECK (kind IN ('question','sql','chart','text','skill\_run','agent\_result','table')); CHECK (author\_kind IN ('user','agent')); CHECK (agent\_suggestion\_state IS NULL OR agent\_suggestion\_state IN ('suggested','accepted','dismissed')); UNIQUE (workspace\_id, position) DEFERRABLE INITIALLY DEFERRED; FOREIGN KEY (workspace\_id) REFERENCES workspace.workspace(id).

#### workspace.tile\_snapshot

Kết quả lưu của một lần làm mới ô; đọc kỳ đã chốt thì ghi mã kỳ. Use case: UC-02-01, UC-02-02.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| tile\_id | uuid, bắt buộc | FK tile |
| query\_run\_ids | uuid\[\], bắt buộc, mặc định '{}' | logic → query.query\_run |
| as\_of | date |  |
| period\_close\_id | uuid | logic → period.period\_close |
| data\_time | jsonb, bắt buộc, mặc định '{}' | Thời điểm dữ liệu từng nguồn; khác nhau thì hiện nhãn (UC-02-02) |
| result\_ref | text | MinIO da-results |
| summary | jsonb | Bản rút gọn để hiển thị nhanh |
| status | text, bắt buộc | ok, failed, partial |
| error | text |  |

Cột chung: id, org\_id, created\_at. Ràng buộc: CHECK (status IN ('ok','failed','partial')); FOREIGN KEY (tile\_id) REFERENCES workspace.tile(id). Chỉ mục: (tile\_id, created\_at DESC).

#### workspace.workspace\_share

Chia sẻ phiên cho người hoặc nhóm, có hạn; người xem thấy số theo quyền của chính họ (BR-89). Use case: UC-02-03.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| workspace\_id | uuid, bắt buộc | FK workspace |
| principal\_type | text, bắt buộc | user, group |
| principal\_id | uuid, bắt buộc |  |
| permission | text, bắt buộc | view, comment, edit |
| granted\_by | uuid, bắt buộc |  |
| expires\_at | timestamptz |  |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: CHECK (principal\_type IN ('user','group')); CHECK (permission IN ('view','comment','edit')); UNIQUE (workspace\_id, principal\_type, principal\_id); FOREIGN KEY (workspace\_id) REFERENCES workspace.workspace(id) ON DELETE CASCADE.

#### workspace.ws\_comment

Bình luận trên phiên hoặc ô, có @nhắc người và agent, có thể thành việc được giao. Use case: UC-02-05.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| workspace\_id | uuid, bắt buộc | FK workspace |
| tile\_id | uuid | FK tile |
| parent\_id | uuid | Bình luận cha |
| author\_id | uuid, bắt buộc |  |
| body | text, bắt buộc |  |
| mentions | uuid\[\], bắt buộc, mặc định '{}' | Người hoặc agent được nhắc; nhắc agent tạo lượt chạy |
| assignee\_id | uuid | Khi bình luận là việc được giao |
| due\_at | timestamptz |  |
| status | text, bắt buộc, mặc định 'open' | open, done |
| resolved\_at | timestamptz |  |
| agent\_run\_id | uuid | logic → agent.agent\_run |
| deleted\_at | timestamptz | Xoá mềm |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: CHECK (status IN ('open','done') AND (status = 'open' OR resolved\_at IS NOT NULL)); FOREIGN KEY (workspace\_id) REFERENCES workspace.workspace(id); FOREIGN KEY (tile\_id) REFERENCES workspace.tile(id); FOREIGN KEY (parent\_id) REFERENCES workspace.ws\_comment(id). Chỉ mục: (assignee\_id) WHERE status = 'open'.

#### workspace.ws\_participant

Người và agent tham gia phiên, với vai trò trong phiên. Use case: UC-02-01, UC-02-03, UC-02-06.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| workspace\_id | uuid, bắt buộc | FK workspace |
| principal\_type | text, bắt buộc | user, agent |
| principal\_id | uuid, bắt buộc |  |
| role | text, bắt buộc | owner, editor, viewer, agent\_helper |
| joined\_at | timestamptz, bắt buộc, mặc định now() |  |

Cột chung: id, org\_id. Ràng buộc: CHECK (principal\_type IN ('user','agent')); CHECK (role IN ('owner','editor','viewer','agent\_helper')); UNIQUE (workspace\_id, principal\_type, principal\_id); FOREIGN KEY (workspace\_id) REFERENCES workspace.workspace(id) ON DELETE CASCADE.

#### workspace.artifact

Kết quả có phiên bản trong phiên: bảng, biểu đồ, đề xuất, bộ dữ liệu, tệp. Use case: UC-02-06, UC-02-07, UC-08-05.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| workspace\_id | uuid, bắt buộc | FK workspace |
| tile\_id | uuid | FK tile |
| kind | text, bắt buộc | table, chart, proposal, dataset, file, text |
| title | text, bắt buộc |  |
| version | integer, bắt buộc, mặc định 1 |  |
| content\_ref | text | MinIO da-results |
| content | jsonb | Nội dung nhỏ lưu thẳng |
| produced\_by\_kind | text, bắt buộc | user, agent\_run, skill\_run |
| produced\_by\_ref | uuid |  |
| evidence\_state | text, bắt buộc, mặc định 'unchecked' | unchecked, grounded, ungrounded (mỗi số có căn cứ hay không) |

Cột chung: id, org\_id, created\_at. Ràng buộc: CHECK (kind IN ('table','chart','proposal','dataset','file','text')); CHECK (produced\_by\_kind IN ('user','agent\_run','skill\_run')); CHECK (evidence\_state IN ('unchecked','grounded','ungrounded')); FOREIGN KEY (workspace\_id) REFERENCES workspace.workspace(id).

#### workspace.session\_report

Báo cáo sinh từ phiên và kết quả bốn kiểm tra trước khi phát hành (UC-02-07). Use case: UC-02-04, UC-02-07, UC-06-05, UC-08-03.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| workspace\_id | uuid, bắt buộc | FK workspace |
| version | integer, bắt buộc, mặc định 1 |  |
| title | text, bắt buộc |  |
| body\_ref | text, bắt buộc | Nội dung báo cáo trong MinIO |
| checks | jsonb, bắt buộc, mặc định '{}' | Số có căn cứ, khớp số đã nộp, quyền người nhận, dữ liệu cá nhân; mỗi mục pass hoặc fail kèm chi tiết |
| status | text, bắt buộc, mặc định 'draft' | draft, checking, ready, pending\_approval, published, failed\_checks |
| approved\_by | uuid |  |
| published\_at | timestamptz |  |
| hitl\_request\_id | uuid | logic → inbox.hitl\_request |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: CHECK (status IN ('draft','checking','ready','pending\_approval','published','failed\_checks')); CHECK (status <> 'published' OR (approved\_by IS NOT NULL AND published\_at IS NOT NULL)); UNIQUE (workspace\_id, version); FOREIGN KEY (workspace\_id) REFERENCES workspace.workspace(id).

### D4.12 Schema skill

#### skill.skill

Kỹ năng phân tích dùng lại được (ví dụ phân rã biến động); phạm vi cá nhân, Data Space hoặc toàn tổ chức. Use case: UC-03-01, UC-03-03, UC-03-05, UC-03-06, UC-03-07.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| data\_space\_id | uuid | Null khi phạm vi toàn tổ chức |
| code | text, bắt buộc |  |
| name | text, bắt buộc |  |
| description | text |  |
| category | text, bắt buộc | decomposition, causal, forecast, reconciliation, quality, report, custom |
| scope | text, bắt buộc, mặc định 'personal' | personal, data\_space, org (BR-96) |
| owner\_id | uuid, bắt buộc |  |
| origin | text, bắt buộc, mặc định 'manual' | manual, from\_session, agent\_suggested, pack |
| origin\_ref | uuid |  |
| current\_version | integer |  |
| replaced\_by\_skill\_id | uuid | Khi ngừng dùng có kỹ năng thay thế (UC-03-06) |
| run\_count\_30d | integer, bắt buộc, mặc định 0 |  |
| deleted\_at | timestamptz | Xoá mềm |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: CHECK (category IN ('decomposition','causal','forecast','reconciliation','quality','report','custom')); CHECK (scope IN ('personal','data\_space','org')); CHECK (scope = 'org' OR data\_space\_id IS NOT NULL); CHECK (origin IN ('manual','from\_session','agent\_suggested','pack')); UNIQUE NULLS NOT DISTINCT (org\_id, data\_space\_id, code).

#### skill.skill\_version

Phiên bản của kỹ năng: tham số, các bước, mẫu kết luận, ràng buộc sandbox. Use case: UC-03-01, UC-03-03, UC-03-04, UC-03-05, UC-03-06, UC-03-07.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| skill\_id | uuid, bắt buộc | FK skill |
| version | integer, bắt buộc |  |
| params\_schema | jsonb, bắt buộc | JSON Schema của tham số |
| steps | jsonb, bắt buộc | Các bước SQL, Python, gọi kỹ năng khác |
| template\_md | text | Mẫu kết luận |
| metric\_version\_ids | uuid\[\], bắt buộc, mặc định '{}' | Chỉ số dùng; đổi chỉ số thì chạy lại kiểm định (BR-13) |
| sandbox\_limits | jsonb, bắt buộc, mặc định '{}' | CPU, bộ nhớ, thời gian, không mạng (BR-94) |
| status | text, bắt buộc, mặc định 'draft' | draft, testing, pending\_review, published, needs\_review, retired |
| eval\_run\_id | uuid | logic → eval.eval\_run |
| approved\_by | uuid |  |
| approved\_at | timestamptz |  |
| approval\_steps | smallint, bắt buộc, mặc định 1 | Toàn tổ chức cần duyệt hai bước (BR-96) |
| retire\_at | timestamptz | Ngừng sau 30 ngày chuyển tiếp (BR-97) |

Cột chung: id, org\_id, created\_at. Ràng buộc: UNIQUE (skill\_id, version); CHECK (status IN ('draft','testing','pending\_review','published','needs\_review','retired')); CHECK (status NOT IN ('published','needs\_review') OR (eval\_run\_id IS NOT NULL AND approved\_by IS NOT NULL)); FOREIGN KEY (skill\_id) REFERENCES skill.skill(id). Chỉ mục: duy nhất (skill\_id) WHERE status = 'published'.

#### skill.skill\_run

Một lần chạy kỹ năng với tham số cụ thể. Use case: UC-03-01, UC-03-02, UC-03-04, UC-08-02.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| skill\_version\_id | uuid, bắt buộc | FK skill\_version |
| data\_space\_id | uuid, bắt buộc |  |
| caller\_kind | text, bắt buộc | user, tile, agent\_run, alert, eval |
| caller\_ref | uuid |  |
| principal\_id | uuid, bắt buộc |  |
| params | jsonb, bắt buộc |  |
| as\_of | date |  |
| status | text, bắt buộc, mặc định 'queued' | queued, running, succeeded, failed, cancelled |
| result | jsonb | Kết luận và số chính |
| result\_ref | text |  |
| started\_at | timestamptz |  |
| finished\_at | timestamptz |  |
| error | text |  |

Cột chung: id, org\_id, created\_at. Ràng buộc: CHECK (caller\_kind IN ('user','tile','agent\_run','alert','eval')); CHECK (status IN ('queued','running','succeeded','failed','cancelled')); FOREIGN KEY (skill\_version\_id) REFERENCES skill.skill\_version(id). Chỉ mục: (skill\_version\_id, created\_at DESC).

#### skill.skill\_run\_step

Kết quả từng bước của một lần chạy kỹ năng. Use case: UC-03-02.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| skill\_run\_id | uuid, bắt buộc | FK skill\_run |
| seq | smallint, bắt buộc |  |
| name | text, bắt buộc |  |
| query\_run\_id | uuid | logic → query.query\_run |
| output | jsonb |  |
| status | text, bắt buộc | ok, error, skipped |
| duration\_ms | integer |  |

Cột chung: id, org\_id, created\_at. Ràng buộc: UNIQUE (skill\_run\_id, seq); CHECK (status IN ('ok','error','skipped')); FOREIGN KEY (skill\_run\_id) REFERENCES skill.skill\_run(id) ON DELETE CASCADE.

#### skill.skill\_suggestion

Kỹ năng agent đề xuất từ chuỗi bước người dùng lặp lại; chỉ dựa trên cấu trúc, không dùng dữ liệu của người khác. Use case: UC-03-07.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| data\_space\_id | uuid, bắt buộc |  |
| pattern | jsonb, bắt buộc | Chuỗi bước đã khái quát, chỉ cấu trúc: loại bước, tên tham số, params\_schema; không có giá trị, kết quả, mẫu dòng hay nội dung ô của phiên (BR-98, sửa 12/10/2026) |
| session\_ids | uuid\[\], bắt buộc, mặc định '{}' | logic → workspace.workspace |
| user\_count | integer, bắt buộc |  |
| status | text, bắt buộc, mặc định 'open' | open, accepted, dismissed |
| dismissed\_until | timestamptz | Không đề xuất lại trong 90 ngày |
| accepted\_skill\_id | uuid | FK skill |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: CHECK (status IN ('open','accepted','dismissed')); CHECK (status <> 'dismissed' OR dismissed\_until IS NOT NULL); FOREIGN KEY (accepted\_skill\_id) REFERENCES skill.skill(id).

#### skill.external\_series

Chuỗi dữ liệu bên ngoài (ví dụ CPI, tỷ giá, lãi suất thị trường), thẩm quyền tham khảo. Use case: UC-03-02.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| code | text, bắt buộc |  |
| name | text, bắt buộc |  |
| provider | text, bắt buộc | Đơn vị công bố |
| frequency | text, bắt buộc | daily, weekly, monthly, quarterly |
| unit | text |  |
| ingest\_mode | text, bắt buộc | proxy, manual\_upload |
| last\_observation\_date | date |  |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: UNIQUE (org\_id, code); CHECK (frequency IN ('daily','weekly','monthly','quarterly')); CHECK (ingest\_mode IN ('proxy','manual\_upload')).

#### skill.external\_observation

Một giá trị của chuỗi ngoài; có ngày công bố để phân tích không “nhìn trước” (BR-93). Use case: UC-03-02.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| series\_id | uuid, bắt buộc | FK external\_series |
| observation\_date | date, bắt buộc | Ngày dữ liệu |
| published\_at | timestamptz, bắt buộc | Ngày công bố; chỉ dùng giá trị đã công bố trước as\_of |
| value | numeric, bắt buộc |  |
| revision | smallint, bắt buộc, mặc định 0 | Lần điều chỉnh số liệu của đơn vị công bố |

Cột chung: id, org\_id, created\_at. Ràng buộc: UNIQUE (series\_id, observation\_date, revision); FOREIGN KEY (series\_id) REFERENCES skill.external\_series(id).

### D4.13 Schema monitor

#### monitor.monitor

Theo dõi chỉ số: luật ngưỡng, thay đổi so kỳ trước hoặc bất thường theo mùa vụ; do một agent thực hiện. Use case: UC-08-01, UC-08-02.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| data\_space\_id | uuid, bắt buộc |  |
| owner\_id | uuid, bắt buộc |  |
| agent\_def\_id | uuid | logic → agent.agent\_def |
| metric\_id | uuid, bắt buộc | logic → context.metric |
| scope\_filter | jsonb, bắt buộc, mặc định '{}' | Vùng, sản phẩm, chi nhánh |
| rule\_kind | text, bắt buộc | threshold, change\_vs\_previous, seasonal\_anomaly |
| rule | jsonb, bắt buộc | Ngưỡng, hướng, độ nhạy |
| schedule\_cron | text, bắt buộc |  |
| notify\_mode | text, bắt buộc, mặc định 'alert\_only' | alert\_only, with\_analysis |
| recipients | jsonb, bắt buộc, mặc định '\[\]' | Người, nhóm và kênh |
| backtest | jsonb | Số lần sẽ báo trong 12 kỳ; trên 30% thì cảnh báo nhiễu (BR-32) |
| status | text, bắt buộc, mặc định 'active' | draft, active, paused |
| paused\_reason | text | Ví dụ chỉ số đổi định nghĩa |
| deleted\_at | timestamptz | Xoá mềm |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: CHECK (rule\_kind IN ('threshold','change\_vs\_previous','seasonal\_anomaly')); CHECK (notify\_mode IN ('alert\_only','with\_analysis')); CHECK (status IN ('draft','active','paused')).

#### monitor.alert

Cảnh báo phát sinh khi luật khớp; gộp tín hiệu lặp trong 24 giờ (BR-33). Use case: UC-08-01, UC-08-02, UC-08-04.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| monitor\_id | uuid, bắt buộc | FK monitor |
| fired\_at | timestamptz, bắt buộc, mặc định now() |  |
| period\_label | text | Ví dụ T9/2026 |
| value | numeric |  |
| threshold\_value | numeric |  |
| summary | text, bắt buộc | Ba dòng: điều gì đổi, vì sao, cần làm gì |
| skill\_run\_id | uuid | logic → skill.skill\_run phân tích nguyên nhân |
| dedup\_key | text, bắt buộc |  |
| occurrences | integer, bắt buộc, mặc định 1 |  |
| kind | text, bắt buộc, mặc định 'metric' | metric, data\_stale |
| status | text, bắt buộc, mặc định 'open' | open, ack, assigned, closed |
| assigned\_to | uuid |  |
| close\_note | text | Bắt buộc khi vượt ngưỡng hơn 2 lần (BR-124) |
| closed\_at | timestamptz |  |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: CHECK (kind IN ('metric','data\_stale')); CHECK (status IN ('open','ack','assigned','closed')); CHECK (status <> 'closed' OR closed\_at IS NOT NULL); CHECK (status <> 'closed' OR occurrences <= 2 OR close\_note IS NOT NULL); FOREIGN KEY (monitor\_id) REFERENCES monitor.monitor(id). Chỉ mục: duy nhất (monitor\_id, dedup\_key) WHERE status <> 'closed'.

#### monitor.report\_schedule

Báo cáo định kỳ hoặc bản tin lãnh đạo, chạy theo lịch làm việc. Use case: UC-02-04, UC-03-06, UC-08-03, UC-08-04.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| data\_space\_id | uuid, bắt buộc |  |
| kind | text, bắt buộc, mặc định 'report' | report, digest |
| workspace\_id | uuid | logic → workspace.workspace; null với bản tin |
| owner\_id | uuid, bắt buộc |  |
| title | text, bắt buộc |  |
| cron | text, bắt buộc |  |
| work\_calendar\_id | uuid | logic → org.work\_calendar |
| cutoff\_time | time | Giờ chốt dữ liệu |
| as\_of\_rule | text, bắt buộc | Ví dụ end\_of\_previous\_month |
| formats | text\[\], bắt buộc, mặc định '{pdf}' | pdf, xlsx, html |
| require\_publish\_check | boolean, bắt buộc, mặc định true | Chạy bốn kiểm tra trước khi gửi |
| status | text, bắt buộc, mặc định 'active' | draft, active, paused |
| deleted\_at | timestamptz | Xoá mềm |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: CHECK (kind IN ('report','digest')); CHECK (kind = 'digest' OR workspace\_id IS NOT NULL); CHECK (status IN ('draft','active','paused')).

#### monitor.report\_recipient

Người nhận báo cáo và nhóm quyền dùng để sinh bản riêng (BR-34). Use case: UC-02-07, UC-08-03.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| report\_schedule\_id | uuid, bắt buộc | FK report\_schedule |
| principal\_type | text, bắt buộc | user, group |
| principal\_id | uuid, bắt buộc |  |
| channels | text\[\], bắt buộc, mặc định '{email}' | email, teams, zalo, in\_app |
| permission\_group | text, bắt buộc | Băm của tập quyền; người cùng tập quyền nhận cùng một bản |

Cột chung: id, org\_id, created\_at. Ràng buộc: CHECK (principal\_type IN ('user','group')); UNIQUE (report\_schedule\_id, principal\_type, principal\_id); FOREIGN KEY (report\_schedule\_id) REFERENCES monitor.report\_schedule(id) ON DELETE CASCADE.

#### monitor.report\_run

Một lần chạy báo cáo hoặc bản tin, mỗi nhóm quyền một tệp kết quả. Use case: UC-08-03, UC-08-04.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| report\_schedule\_id | uuid, bắt buộc | FK report\_schedule |
| kind | text, bắt buộc, mặc định 'report' | report, digest |
| as\_of | date, bắt buộc |  |
| workflow\_id | text |  |
| trigger | text, bắt buộc, mặc định 'schedule' | schedule, run\_now |
| checks | jsonb, bắt buộc, mặc định '{}' | Kết quả kiểm tra trước khi gửi |
| outputs | jsonb, bắt buộc, mặc định '\[\]' | Mỗi phần tử: permission\_group, định dạng, object\_key, trạng thái gửi |
| status | text, bắt buộc, mặc định 'running' | running, sent, partially\_sent, blocked\_by\_checks, failed |
| finished\_at | timestamptz |  |

Cột chung: id, org\_id, created\_at. Ràng buộc: CHECK (kind IN ('report','digest')); CHECK (trigger IN ('schedule','run\_now')); CHECK (status IN ('running','sent','partially\_sent','blocked\_by\_checks','failed')); FOREIGN KEY (report\_schedule\_id) REFERENCES monitor.report\_schedule(id).

#### monitor.external\_calendar

Lịch sự kiện ngoài tổ chức do người khai báo hoặc agent phát hiện, ví dụ kỳ điều hành giá xăng dầu; mỗi kỳ là một external\_event, ngày giờ kỳ không viết cứng trong mã (BR-187). Use case: UC-08-08.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| data\_space\_id | uuid, bắt buộc | logic → org.data\_space |
| code | text, bắt buộc | Ví dụ fuel\_price\_regulation; context.data\_product\_member.sla\_event\_calendar\_code tham chiếu logic tới mã này để tính hạn độ tươi (BR-178) |
| name | text, bắt buộc | Ví dụ Kỳ điều hành giá xăng dầu |
| owner\_id | uuid, bắt buộc | logic → org.app\_user; người xác nhận kỳ khi phát hiện không chắc |
| legal\_basis | text | Căn cứ của nhịp thường lệ, ví dụ Nghị định 80/2023/NĐ-CP |
| recurrence\_hint | text | RRULE gợi ý, ví dụ FREQ=WEEKLY;BYDAY=TH; chỉ dùng để dự kiến kỳ, kỳ thật lấy từ khai báo hoặc phát hiện |
| default\_local\_time | time | Giờ áp dụng thường lệ, ví dụ 15:00 |
| timezone | text, bắt buộc, mặc định 'Asia/Ho\_Chi\_Minh' |  |
| work\_calendar\_id | uuid | logic → org.work\_calendar; ngày nghỉ làm kỳ dự kiến cần xác nhận |
| detection\_source\_id | uuid | logic → acquire.crawl\_source; nguồn phát hiện thông báo kỳ và kết quả kỳ, ví dụ trang tin điều hành giá trên moit.gov.vn |
| forecast\_model\_ids | uuid\[\], bắt buộc, mặc định '{}' | logic → forecast.forecast\_model; mô hình chạy trước mỗi kỳ |
| pre\_bulletin\_days\_before | smallint, bắt buộc, mặc định 1 | Số ngày trước kỳ gửi bản tin dự báo |
| pre\_bulletin\_local\_time | time, bắt buộc, mặc định '20:00' | Giờ gửi bản tin dự báo theo timezone |
| forecast\_lead | interval, bắt buộc, mặc định '2 hours' | Chạy dự báo trước giờ gửi bao lâu |
| post\_bulletin | boolean, bắt buộc, mặc định true | Gửi bản tin kết quả kèm chấm điểm sau kỳ |
| post\_wait\_max | interval, bắt buộc, mặc định '12 hours' | Chờ kết quả tối đa sau expected\_at; quá thì hỏi người phụ trách |
| status | text, bắt buộc, mặc định 'active' | active, paused, retired |
| temporal\_schedule\_id | text | Schedule kiểm phát hiện kỳ và dự kiến kỳ mới (ADR-33) |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: UNIQUE (data\_space\_id, code); CHECK (status IN ('active','paused','retired')); CHECK (pre\_bulletin\_days\_before >= 0 AND pre\_bulletin\_days\_before <= 7); CHECK (forecast\_lead > interval '0' AND forecast\_lead <= interval '24 hours'); CHECK (post\_wait\_max > interval '0' AND post\_wait\_max <= interval '72 hours'); CHECK (status <> 'active' OR detection\_source\_id IS NOT NULL OR recurrence\_hint IS NOT NULL).

#### monitor.external\_event

Một kỳ của lịch sự kiện ngoài: dự kiến từ nhịp thường lệ, do người khai, hoặc phát hiện từ nguồn; mỗi kỳ có một workflow bản tin (BR-187). Use case: UC-08-08, UC-15-08.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| calendar\_id | uuid, bắt buộc | FK external\_calendar |
| period\_label | text, bắt buộc | Nhãn kỳ, ví dụ 2026-07-09 |
| expected\_at | timestamptz, bắt buộc | Thời điểm dự kiến áp dụng, ví dụ 2026-07-09 15:00 +07 |
| origin | text, bắt buộc | recurrence (dự kiến từ recurrence\_hint), declared (người khai), detected (phát hiện từ nguồn) |
| status | text, bắt buộc, mặc định 'expected' | expected, confirmed, shifted, occurred, cancelled |
| confirmed\_by | uuid | logic → org.app\_user; null khi xác nhận từ nguồn |
| evidence | jsonb, bắt buộc, mặc định '{}' | {extracted\_record\_id, url, quote} của thông báo kỳ hoặc bản tin kết quả |
| shifted\_from | timestamptz | expected\_at cũ khi kỳ dời |
| occurred\_at | timestamptz | Thời điểm áp dụng thực tế theo bản tin, ví dụ 2026-07-02 16:00 +07 |
| pre\_bulletin\_at | timestamptz | Tính từ lịch, ví dụ 2026-07-08 20:00 +07; tính lại khi kỳ dời |
| workflow\_id | text | Workflow bản tin của kỳ (Temporal từ R3; trước R3 là lượt quét định kỳ, G8.11) |

Cột chung: id, org\_id, created\_at, updated\_at. Ràng buộc: CHECK (origin IN ('recurrence','declared','detected')); CHECK (status IN ('expected','confirmed','shifted','occurred','cancelled')); CHECK (status <> 'occurred' OR occurred\_at IS NOT NULL); CHECK (status <> 'shifted' OR shifted\_from IS NOT NULL); CHECK (origin <> 'declared' OR confirmed\_by IS NOT NULL); CHECK (origin <> 'detected' OR evidence <> '{}'::jsonb); CHECK (pre\_bulletin\_at IS NULL OR pre\_bulletin\_at < expected\_at); FOREIGN KEY (calendar\_id) REFERENCES monitor.external\_calendar(id). Chỉ mục: duy nhất (calendar\_id, period\_label) WHERE status <> 'cancelled'; (status, pre\_bulletin\_at) WHERE status IN ('expected','confirmed','shifted'). Trigger external\_event\_guard: status chỉ đổi expected → confirmed, shifted, cancelled, occurred; confirmed → shifted, cancelled, occurred; shifted → confirmed, cancelled, occurred; khi đổi expected\_at thì ghi shifted\_from và tính lại pre\_bulletin\_at; mỗi lần đổi ghi monitor.outbox monitor.external\_event.changed.

#### monitor.event\_subscription

Người theo dõi một lịch sự kiện ngoài: loại bản tin và kênh nhận; kênh và giờ yên lặng vẫn theo notify.notification\_pref (BR-127). Use case: UC-08-08.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| calendar\_id | uuid, bắt buộc | FK external\_calendar |
| user\_id | uuid, bắt buộc | logic → org.app\_user |
| kinds | text\[\], bắt buộc, mặc định '{pre,post}' | pre (bản tin dự báo trước kỳ), post (bản tin kết quả và chấm điểm) |
| channels | text\[\], bắt buộc, mặc định '{in\_app}' | in\_app, email, teams, zalo, mobile\_push |
| status | text, bắt buộc, mặc định 'active' | active, paused |

Cột chung: id, org\_id, created\_at, updated\_at. Ràng buộc: UNIQUE (calendar\_id, user\_id); CHECK (cardinality(kinds) > 0 AND kinds <@ ARRAY\['pre','post'\]::text\[\]); CHECK (channels <@ ARRAY\['in\_app','email','teams','zalo','mobile\_push'\]::text\[\] AND 'in\_app' = ANY (channels)); CHECK (status IN ('active','paused')); FOREIGN KEY (calendar\_id) REFERENCES monitor.external\_calendar(id).

#### monitor.event\_bulletin

Một bản tin của một kỳ: trước kỳ (dự báo) hoặc sau kỳ (kết quả và chấm điểm); nội dung lấy từ sổ dự báo, không có số nào ngoài ledger\_entry và dataset đã chứng nhận (BR-187). Use case: UC-08-08.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| event\_id | uuid, bắt buộc | FK external\_event |
| kind | text, bắt buộc | pre, post |
| status | text, bắt buộc, mặc định 'preparing' | preparing, sent, sent\_no\_forecast, blocked, failed |
| ledger\_entry\_ids | uuid\[\], bắt buộc, mặc định '{}' | logic → forecast.ledger\_entry; mỗi mặt hàng một dòng |
| gate\_result | jsonb, bắt buộc, mặc định '{}' | Kết quả cổng: tham số certified đủ, độ tươi đầu vào, mô hình đã duyệt, kỳ đã xác nhận hay dự kiến (BR-183, BR-187) |
| content | jsonb, bắt buộc, mặc định '{}' | Khối nội dung thẻ: tiêu đề, bảng mặt hàng, phân rã, sổ N kỳ, câu bắt buộc |
| recipients\_count | integer, bắt buộc, mặc định 0 |  |
| sent\_at | timestamptz |  |
| web\_link | text | Liên kết SCR-70 (bản tin trước kỳ) hoặc SCR-71 (sau kỳ) |

Cột chung: id, org\_id, created\_at. Ràng buộc: UNIQUE (event\_id, kind); CHECK (kind IN ('pre','post')); CHECK (status IN ('preparing','sent','sent\_no\_forecast','blocked','failed')); CHECK (status NOT IN ('sent','sent\_no\_forecast') OR sent\_at IS NOT NULL); CHECK (status <> 'sent' OR cardinality(ledger\_entry\_ids) > 0); CHECK (status <> 'sent\_no\_forecast' OR kind = 'pre'); CHECK (recipients\_count >= 0); FOREIGN KEY (event\_id) REFERENCES monitor.external\_event(id). Chỉ mục: (status, created\_at DESC).

### D4.14 Schema notify

#### notify.notification

Thông báo gửi cho một người; mọi dịch vụ phát qua sự kiện, Notification service ghi bảng này. Use case: UC-02-03, UC-02-04, UC-02-05, UC-08-02, UC-08-03, UC-08-04, UC-08-06, UC-08-07.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| user\_id | uuid, bắt buộc | logic → org.app\_user |
| data\_space\_id | uuid |  |
| kind | text, bắt buộc | alert, hitl, digest, task\_done, comment, share, report, system |
| title | text, bắt buộc |  |
| body | text | Không chứa dữ liệu chi tiết hay dữ liệu cá nhân |
| link | text | Đường dẫn trong ứng dụng |
| source\_ref | uuid |  |
| priority | text, bắt buộc, mặc định 'normal' | low, normal, high |
| batched\_into | uuid | Thông báo gộp chứa thông báo này |
| read\_at | timestamptz |  |

Cột chung: id, org\_id, created\_at. Phân vùng theo tháng trên created\_at. Ràng buộc: CHECK (kind IN ('alert','hitl','digest','task\_done','comment','share','report','system')); CHECK (priority IN ('low','normal','high')). Chỉ mục: (user\_id, created\_at DESC) WHERE read\_at IS NULL.

#### notify.notification\_pref

Cài đặt nhận thông báo: kênh theo loại sự kiện, giờ yên lặng, mức gộp (BR-127). Use case: UC-08-06, UC-08-07.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| user\_id | uuid, bắt buộc |  |
| kind | text, bắt buộc | Loại sự kiện như ở notification.kind |
| channels | text\[\], bắt buộc, mặc định '{in\_app}' | in\_app, email, teams, zalo, mobile\_push |
| quiet\_hours | jsonb | Ví dụ {from: 22:00, to: 07:00} |
| batch\_mode | text, bắt buộc, mặc định 'instant' | instant, 15m, daily |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: UNIQUE (user\_id, kind); CHECK (batch\_mode IN ('instant','15m','daily')); CHECK (kind <> 'hitl' OR 'in\_app' = ANY (channels)), để yêu cầu duyệt luôn hiện trong ứng dụng (UC-08-07 E1).

#### notify.channel\_binding

Liên kết tài khoản người dùng với Teams, Zalo, email công ty, ứng dụng di động. Use case: UC-08-06, UC-08-07.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| user\_id | uuid, bắt buộc |  |
| channel | text, bắt buộc | teams, zalo, email, mobile\_push |
| external\_address | text, bắt buộc | Mã người dùng ở kênh; dữ liệu cá nhân |
| verified\_at | timestamptz | Zalo OA cần xác nhận số điện thoại công ty |
| status | text, bắt buộc, mặc định 'pending' | pending, active, revoked |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: CHECK (channel IN ('teams','zalo','email','mobile\_push')); CHECK (status IN ('pending','active','revoked')); CHECK (status <> 'active' OR verified\_at IS NOT NULL); UNIQUE (user\_id, channel).

#### notify.channel\_delivery

Bản sao một thẻ (yêu cầu duyệt, cảnh báo, bản tin) trên một kênh và trạng thái; trả lời trên kênh quay về đây. Use case: UC-07-07, UC-08-02, UC-08-06.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| notification\_id | uuid | logic → notification (bảng phân vùng) |
| hitl\_request\_id | uuid | logic → inbox.hitl\_request |
| user\_id | uuid, bắt buộc |  |
| channel | text, bắt buộc | teams, zalo, email, mobile\_push |
| external\_message\_id | text |  |
| status | text, bắt buộc, mặc định 'queued' | queued, sent, delivered, acted, expired, failed |
| action\_taken | jsonb | Phương án người dùng bấm trên kênh |
| acted\_at | timestamptz |  |
| error | text |  |

Cột chung: id, org\_id, created\_at. Ràng buộc: CHECK (channel IN ('teams','zalo','email','mobile\_push')); CHECK (status IN ('queued','sent','delivered','acted','expired','failed')); CHECK (notification\_id IS NOT NULL OR hitl\_request\_id IS NOT NULL). Chỉ mục: (hitl\_request\_id) WHERE hitl\_request\_id IS NOT NULL.

### D4.15 Schema document

#### document.collection

Bộ sưu tập tài liệu theo mục đích; tài liệu kế thừa quyền và mục đích của bộ sưu tập (BR-59, BR-107). Use case: UC-05-01, UC-05-06.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| data\_space\_id | uuid, bắt buộc |  |
| code | text, bắt buộc |  |
| name | text, bắt buộc |  |
| media\_kinds | text\[\], bắt buộc, mặc định '{}' | pdf, image, docx, email, audio, html |
| purpose | text, bắt buộc | Mục đích sử dụng; lượt chạy có mục đích khác không đọc được |
| pii\_policy | text, bắt buộc, mặc định 'mask' | mask, keep\_for\_authorized, reject |
| retention\_days | integer |  |
| extraction\_schema\_id | uuid | FK extraction\_schema đang dùng |
| qdrant\_collection | text, bắt buộc | Tên collection Qdrant chứa vector đoạn |
| embed\_model | text, bắt buộc | Mô hình embedding; đổi mô hình thì lập chỉ mục mới song song (BR-18) |
| status | text, bắt buộc, mặc định 'active' | active, reindexing, archived |
| deleted\_at | timestamptz | Xoá mềm |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: UNIQUE (data\_space\_id, code); CHECK (pii\_policy IN ('mask','keep\_for\_authorized','reject')); CHECK (status IN ('active','reindexing','archived')).

#### document.document\_source

Thư mục, hộp thư hoặc nguồn ghi âm đồng bộ vào một bộ sưu tập theo lịch. Use case: UC-05-01.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| collection\_id | uuid, bắt buộc | FK collection |
| kind | text, bắt buộc | smb\_folder, sharepoint, imap\_mailbox, call\_recording\_api, object\_store |
| location | text, bắt buộc | Đường dẫn hoặc địa chỉ |
| secret\_ref | text | OpenBao |
| sync\_cron | text, bắt buộc |  |
| last\_synced\_at | timestamptz |  |
| status | text, bắt buộc, mặc định 'active' | active, paused, error |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: CHECK (kind IN ('smb\_folder','sharepoint','imap\_mailbox','call\_recording\_api','object\_store')); CHECK (status IN ('active','paused','error')); FOREIGN KEY (collection\_id) REFERENCES document.collection(id).

#### document.document

Tài liệu đã nạp: bản gốc ở MinIO, trạng thái xử lý, số hiệu, hiệu lực, văn bản bị thay thế. Use case: UC-05-01, UC-05-04, UC-05-05.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| collection\_id | uuid, bắt buộc | FK collection |
| document\_source\_id | uuid | FK document\_source khi đồng bộ |
| sha256 | text, bắt buộc | Trùng mã băm trong cùng bộ sưu tập thì không nạp lại |
| media | text, bắt buộc | pdf, image, docx, email, audio, html |
| file\_name | text, bắt buộc |  |
| object\_key | text, bắt buộc | MinIO da-raw-docs |
| size\_bytes | bigint, bắt buộc | Tối đa 200 MB |
| title | text |  |
| doc\_no | text | Số hiệu văn bản |
| issued\_on | date |  |
| effective\_from | date |  |
| effective\_to | date |  |
| supersedes\_id | uuid | Văn bản bị thay thế |
| parent\_document\_id | uuid | Email chứa tệp đính kèm này (UC-05-01 3a) |
| page\_count | integer |  |
| status | text, bắt buộc, mặc định 'received' | received, processing, waiting\_review, ready, failed, removed |
| workflow\_id | text | Temporal doc\_ingest |
| error | text |  |
| deleted\_at | timestamptz | Xoá mềm |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: CHECK (media IN ('pdf','image','docx','email','audio','html')); CHECK (status IN ('received','processing','waiting\_review','ready','failed','removed')); CHECK (effective\_to IS NULL OR effective\_from IS NULL OR effective\_to >= effective\_from); CHECK (size\_bytes <= 209715200); UNIQUE (collection\_id, sha256); FOREIGN KEY (collection\_id) REFERENCES document.collection(id); FOREIGN KEY (document\_source\_id) REFERENCES document.document\_source(id); FOREIGN KEY (supersedes\_id) REFERENCES document.document(id); FOREIGN KEY (parent\_document\_id) REFERENCES document.document(id). Chỉ mục: (collection\_id, doc\_no).

#### document.document\_page

Trang của tài liệu; mỗi bước xử lý lưu theo mã băm trang để chỉ xử lý lại trang đổi (BR-17). Use case: UC-05-01, UC-05-02, UC-05-03, UC-05-04.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| document\_id | uuid, bắt buộc | FK document |
| page\_no | integer, bắt buộc |  |
| page\_hash | text, bắt buộc |  |
| text\_layer | boolean, bắt buộc, mặc định false | Trang có lớp chữ thì đọc trực tiếp, không OCR |
| ocr\_conf | numeric(4,3) | Dưới 0,85 thì không dùng để trả lời cho đến khi duyệt (BR-19) |
| layout\_ref | text | MinIO da-doc-derived |
| image\_ref | text |  |
| text | text | Văn bản đã chuẩn hoá Unicode NFC, đã che theo chính sách |
| human\_corrected | boolean, bắt buộc, mặc định false | Đã sửa tay; không ghi đè khi xử lý lại (BR-108) |
| usable | boolean, bắt buộc, mặc định false |  |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: UNIQUE (document\_id, page\_no); CHECK (NOT usable OR ocr\_conf IS NULL OR ocr\_conf >= 0.85 OR human\_corrected); FOREIGN KEY (document\_id) REFERENCES document.document(id) ON DELETE CASCADE.

#### document.chunk

Đoạn đã chia theo cấu trúc (Điều, Khoản, bảng); vector nằm ở Qdrant, bảng giữ văn bản và mã điểm. Chỉ mục toàn văn trên cột text dùng cho tìm theo từ khoá (Postgres FTS). Use case: UC-05-02, UC-05-03, UC-05-04, UC-05-05.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| document\_id | uuid, bắt buộc | FK document |
| collection\_id | uuid, bắt buộc | Để lọc nhanh theo bộ sưu tập |
| page\_from | integer, bắt buộc |  |
| page\_to | integer, bắt buộc |  |
| section\_path | text | Ví dụ Điều 5 > Khoản 2 |
| context\_header | text | Tên văn bản, số hiệu, đường dẫn mục, ghép trước đoạn khi tạo vector |
| text | text, bắt buộc |  |
| tokens | integer, bắt buộc |  |
| qdrant\_point\_id | uuid | Null khi chưa lập chỉ mục |
| embed\_model | text |  |
| effective\_from | date | Hiệu lực ở mức đoạn khi sửa đổi từng phần (BR-109) |
| effective\_to | date |  |
| human\_corrected | boolean, bắt buộc, mặc định false |  |
| injection\_suspected | boolean, bắt buộc, mặc định false | Có câu ra lệnh cho mô hình (UC-05-02 E2) |
| pii\_masked | boolean, bắt buộc, mặc định true |  |

Cột chung: id, org\_id, created\_at. Ràng buộc: CHECK (page\_to >= page\_from); CHECK (effective\_to IS NULL OR effective\_from IS NULL OR effective\_to >= effective\_from); FOREIGN KEY (document\_id) REFERENCES document.document(id) ON DELETE CASCADE. Chỉ mục: (document\_id, page\_from); GIN trên to\_tsvector('simple', text).

#### document.quality\_issue

Vấn đề chất lượng chờ người xử lý: OCR kém, nghi dữ liệu cá nhân chưa che, trường độ tin thấp hoặc lệch CSDL. Use case: UC-05-02, UC-05-03, UC-05-06.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| document\_id | uuid, bắt buộc | FK document |
| page\_no | integer |  |
| chunk\_id | uuid |  |
| extracted\_record\_id | uuid |  |
| kind | text, bắt buộc | low\_ocr, pii\_unmasked, garbled\_text, empty\_chunk, low\_confidence\_field, field\_mismatch |
| severity | text, bắt buộc, mặc định 'blocking' | blocking, warning |
| detail | jsonb, bắt buộc, mặc định '{}' |  |
| requires\_role | text | AC03 hoặc AC06 với vấn đề dữ liệu cá nhân |
| status | text, bắt buộc, mặc định 'open' | open, resolved, dismissed |
| resolution | text | corrected, confirmed\_mask, page\_dropped, value\_fixed |
| old\_value | text | Giá trị trước khi sửa (BR-108) |
| resolved\_by | uuid |  |
| resolved\_at | timestamptz |  |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: CHECK (kind IN ('low\_ocr','pii\_unmasked','garbled\_text','empty\_chunk','low\_confidence\_field','field\_mismatch')); CHECK (severity IN ('blocking','warning')); CHECK (status IN ('open','resolved','dismissed')); CHECK (status = 'open' OR (resolved\_by IS NOT NULL AND resolved\_at IS NOT NULL)); FOREIGN KEY (document\_id) REFERENCES document.document(id) ON DELETE CASCADE. Chỉ mục: (kind, severity) WHERE status = 'open'.

#### document.extraction\_schema

Lược đồ trích xuất trường từ tài liệu ra bảng, có phiên bản; chỉ kích hoạt khi đạt độ chính xác (BR-110). Use case: UC-05-06.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| collection\_id | uuid, bắt buộc | FK collection |
| version | integer, bắt buộc |  |
| fields | jsonb, bắt buộc | Tên, kiểu, bắt buộc, có phải dữ liệu cá nhân, cột đích |
| target\_dataset\_id | uuid | logic → catalog.dataset (bảng đích trong vùng dữ liệu) |
| join\_key | jsonb | Cách nối với dữ liệu có cấu trúc, ví dụ số hợp đồng với mã khoản vay |
| sample\_accuracy | jsonb | Độ chính xác từng trường trên mẫu |
| status | text, bắt buộc, mặc định 'draft' | draft, testing, active, superseded |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: UNIQUE (collection\_id, version); CHECK (status IN ('draft','testing','active','superseded')); CHECK (status <> 'active' OR sample\_accuracy IS NOT NULL); FOREIGN KEY (collection\_id) REFERENCES document.collection(id). Chỉ mục: duy nhất (collection\_id) WHERE status = 'active'.

#### document.extracted\_record

Bản ghi trích từ một tài liệu theo một phiên bản lược đồ. Use case: UC-05-02, UC-05-03, UC-05-06.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| document\_id | uuid, bắt buộc | FK document |
| extraction\_schema\_id | uuid, bắt buộc | FK extraction\_schema |
| values | jsonb, bắt buộc | Giá trị từng trường, đã che dữ liệu cá nhân |
| confidence | jsonb, bắt buộc, mặc định '{}' | Độ tin từng trường |
| source\_spans | jsonb, bắt buộc, mặc định '{}' | Trang và vị trí lấy giá trị |
| status | text, bắt buộc, mặc định 'extracted' | extracted, needs\_review, confirmed, loaded |
| loaded\_at | timestamptz | Đã đổ ra bảng đích |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: CHECK (status IN ('extracted','needs\_review','confirmed','loaded')); FOREIGN KEY (document\_id) REFERENCES document.document(id) ON DELETE CASCADE; FOREIGN KEY (extraction\_schema\_id) REFERENCES document.extraction\_schema(id).

#### document.transcript\_segment

Đoạn chép lời của ghi âm, có người nói và mốc thời gian. Use case: UC-05-02, UC-05-06.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| document\_id | uuid, bắt buộc | FK document (media = audio) |
| seq | integer, bắt buộc |  |
| speaker | text | Ví dụ agent, customer |
| start\_ms | integer, bắt buộc |  |
| end\_ms | integer, bắt buộc |  |
| text | text, bắt buộc | Đã che dữ liệu cá nhân |
| confidence | numeric(4,3) |  |

Cột chung: id, org\_id, created\_at. Ràng buộc: UNIQUE (document\_id, seq); CHECK (end\_ms > start\_ms); FOREIGN KEY (document\_id) REFERENCES document.document(id) ON DELETE CASCADE.

#### document.email\_message

Thư trong một luồng email; tệp đính kèm là tài liệu con. Use case: UC-05-06.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| document\_id | uuid, bắt buộc | FK document (media = email) |
| thread\_key | text, bắt buộc |  |
| message\_id | text, bắt buộc | Message-ID của thư |
| sender\_hash | text, bắt buộc | Băm địa chỉ gửi |
| subject | text |  |
| sent\_at | timestamptz, bắt buộc |  |
| in\_reply\_to | text |  |

Cột chung: id, org\_id, created\_at. Ràng buộc: UNIQUE (document\_id); FOREIGN KEY (document\_id) REFERENCES document.document(id) ON DELETE CASCADE. Chỉ mục: (thread\_key, sent\_at).

### D4.16 Schema conflict

#### conflict.claim

Khẳng định trích từ nguồn: chủ thể, thuộc tính, giá trị, hiệu lực. Use case: UC-05-02, UC-07-04.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| data\_space\_id | uuid, bắt buộc |  |
| subject\_key | text, bắt buộc | Ví dụ khoản vay HD-001, chỉ tiêu dư nợ T9 |
| attribute | text, bắt buộc |  |
| value\_text | text |  |
| value\_num | numeric |  |
| unit | text |  |
| valid\_from | date |  |
| valid\_to | date |  |
| source\_kind | text, bắt buộc | dataset, document, published\_result, external |
| source\_ref | uuid, bắt buộc | Bảng, đoạn tài liệu hoặc số đã nộp |
| source\_id | uuid | logic → catalog.source, để áp thẩm quyền |
| extracted\_at | timestamptz, bắt buộc, mặc định now() |  |

Cột chung: id, org\_id. Ràng buộc: CHECK (source\_kind IN ('dataset','document','published\_result','external')); CHECK (value\_text IS NOT NULL OR value\_num IS NOT NULL). Chỉ mục: (data\_space\_id, subject\_key, attribute).

#### conflict.conflict

Một vụ xung đột giữa các nguồn: loại, mức chênh, trọng yếu hay không, kết cục. Use case: UC-07-04.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| data\_space\_id | uuid, bắt buộc |  |
| kind | text, bắt buộc | value, definition, validity, scope, identity, language, entry\_error (bảy loại ở UC-07-04) |
| detected\_at\_stage | text, bắt buộc | ingest, answer, scheduled\_check |
| subject\_key | text, bắt buộc |  |
| attribute | text, bắt buộc |  |
| delta | numeric |  |
| delta\_pct | numeric |  |
| material | boolean, bắt buộc |  |
| chosen\_claim\_id | uuid | FK claim được chọn cho câu trả lời |
| rule\_id | uuid | FK resolution\_rule đã áp |
| outcome | text | noted, auto\_resolved, both\_presented, resolved\_by\_human, waiting\_data |
| hitl\_request\_id | uuid | logic → inbox.hitl\_request |
| decision\_note | text |  |
| affected\_answer\_ids | uuid\[\], bắt buộc, mặc định '{}' | Câu trả lời đã dùng số bị loại, được đánh dấu lại |
| status | text, bắt buộc, mặc định 'open' | open, resolved, insufficient\_basis |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: CHECK (kind IN ('value','definition','validity','scope','identity','language','entry\_error')); CHECK (detected\_at\_stage IN ('ingest','answer','scheduled\_check')); CHECK (outcome IS NULL OR outcome IN ('noted','auto\_resolved','both\_presented','resolved\_by\_human','waiting\_data')); CHECK (status IN ('open','resolved','insufficient\_basis')); CHECK (status <> 'resolved' OR outcome IS NOT NULL); FOREIGN KEY (chosen\_claim\_id) REFERENCES conflict.claim(id). Chỉ mục: (data\_space\_id) WHERE status = 'open'.

#### conflict.conflict\_claim

Khẳng định tham gia một vụ xung đột. Use case: UC-07-04.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| conflict\_id | uuid, bắt buộc | FK conflict |
| claim\_id | uuid, bắt buộc | FK claim |
| role | text, bắt buộc | primary, competing |

Cột chung: id, org\_id. Ràng buộc: CHECK (role IN ('primary','competing')); UNIQUE (conflict\_id, claim\_id); FOREIGN KEY (conflict\_id) REFERENCES conflict.conflict(id) ON DELETE CASCADE; FOREIGN KEY (claim\_id) REFERENCES conflict.claim(id).

#### conflict.resolution\_rule

Quy tắc giải quyết xung đột đã duyệt; áp trước thẩm quyền nguồn (BR-30). Use case: UC-07-04.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| data\_space\_id | uuid, bắt buộc |  |
| data\_kind | text, bắt buộc |  |
| condition | jsonb, bắt buộc | Ví dụ lệch dưới 0,05% và là ngày chốt kỳ |
| action | jsonb, bắt buộc | Ví dụ chọn nguồn Core banking |
| origin | text, bắt buộc, mặc định 'manual' | manual, hitl |
| behavior\_policy\_id | uuid | logic → governance.behavior\_policy khi sinh từ quyết định |
| status | text, bắt buộc, mặc định 'active' | active, disabled |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: CHECK (origin IN ('manual','hitl')); CHECK (status IN ('active','disabled')).

#### conflict.source\_authority

Thứ tự ưu tiên nguồn theo loại dữ liệu và ngưỡng trọng yếu. Use case: UC-07-04, UC-07-05.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| data\_space\_id | uuid, bắt buộc |  |
| data\_kind | text, bắt buộc | Ví dụ dư nợ, số khách, văn bản pháp quy |
| ranking | uuid\[\], bắt buộc | Danh sách source\_id theo thứ tự ưu tiên; nguồn tải tay và bên ngoài không đứng trên cấp 1 (BR-121) |
| materiality | jsonb, bắt buộc, mặc định '{"amount\_pct": 0.0005, "count": 0}' | Ngưỡng trọng yếu (BR-120) |
| backtest | jsonb | Kết quả chạy thử trên xung đột 90 ngày |
| approved\_by | uuid |  |
| approved\_at | timestamptz |  |
| status | text, bắt buộc, mặc định 'draft' | draft, active, superseded |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: CHECK (status IN ('draft','active','superseded')); CHECK (status NOT IN ('active','superseded') OR approved\_by IS NOT NULL). Chỉ mục: duy nhất (data\_space\_id, data\_kind) WHERE status = 'active'; duy nhất (data\_space\_id, data\_kind) WHERE status = 'draft'. Sửa bảng xếp hạng đang dùng tạo bản nháp mới; bản active vẫn áp dụng tới khi bản nháp được duyệt, lúc đó bản cũ thành superseded (UC-07-05, sửa 12/10/2026).

### D4.17 Schema period

#### period.period\_calendar

Lịch chốt kỳ của Data Space: loại kỳ, ngày chốt dự kiến, bảng thuộc kỳ, chỉ tiêu đối soát. Use case: UC-10-02.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| data\_space\_id | uuid, bắt buộc |  |
| period\_kind | text, bắt buộc | month, quarter, year, custom |
| close\_rule | text, bắt buộc | Ví dụ ngày làm việc thứ 3 của tháng sau |
| work\_calendar\_id | uuid | logic → org.work\_calendar |
| dataset\_ids | uuid\[\], bắt buộc | Bảng được chụp khi chốt; logic → catalog.dataset |
| reconciliation\_items | jsonb, bắt buộc, mặc định '\[\]' | Chỉ tiêu đối soát và dung sai, ví dụ tổng dư nợ khớp sổ cái |
| report\_codes | text\[\], bắt buộc, mặc định '{}' | Báo cáo cần đóng băng, khai báo bởi gói ngành hoặc tổ chức |
| status | text, bắt buộc, mặc định 'active' | active, paused |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: CHECK (period\_kind IN ('month','quarter','year','custom')); CHECK (status IN ('active','paused')).

#### period.period\_close

Một kỳ chốt; bản 1 không bao giờ đổi, hồi tố tạo version + 1 (BR-43). Use case: UC-10-01, UC-10-02, UC-10-03, UC-10-04, UC-10-05.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| data\_space\_id | uuid, bắt buộc |  |
| period\_calendar\_id | uuid | FK period\_calendar |
| period\_label | text, bắt buộc | Ví dụ 2026-09 |
| business\_date | date, bắt buộc | Ngày nghiệp vụ cuối kỳ |
| version | integer, bắt buộc, mặc định 1 |  |
| status | text, bắt buộc, mặc định 'open' | open, closing, close\_failed, closed, filed, retro\_detected |
| snapshot\_hash | text | SHA-256 cả bản chụp |
| workflow\_id | text |  |
| closed\_by | uuid | Người bấm Chốt kỳ |
| closed\_at | timestamptz |  |
| filed\_report\_code | text |  |
| filed\_at | date |  |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: UNIQUE (data\_space\_id, period\_label, version); CHECK (status IN ('open','closing','close\_failed','closed','filed','retro\_detected')); CHECK (status NOT IN ('closed','filed','retro\_detected') OR (snapshot\_hash IS NOT NULL AND closed\_at IS NOT NULL)); FOREIGN KEY (period\_calendar\_id) REFERENCES period.period\_calendar(id). Chỉ mục: duy nhất (data\_space\_id) WHERE status = 'closing', để mỗi Data Space chỉ chốt một kỳ tại một thời điểm.

#### period.period\_snapshot\_file

Tệp Parquet của một bảng trong kỳ chốt, ở bucket WORM (BR-41). Use case: UC-10-01, UC-10-02, UC-10-05.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| period\_close\_id | uuid, bắt buộc | FK period\_close |
| dataset\_id | uuid, bắt buộc | logic → catalog.dataset |
| dataset\_version | integer, bắt buộc | Phiên bản cấu trúc tại lúc chụp |
| object\_key | text, bắt buộc | MinIO da-period-close |
| row\_count | bigint, bắt buộc |  |
| sha256 | text, bắt buộc | Kiểm trước khi đọc (BR-135) |
| written\_at | timestamptz, bắt buộc, mặc định now() |  |

Cột chung: id, org\_id. Ràng buộc: UNIQUE (period\_close\_id, dataset\_id); FOREIGN KEY (period\_close\_id) REFERENCES period.period\_close(id).

#### period.period\_reconciliation

Kết quả đối soát một chỉ tiêu khi chốt kỳ; ngoại lệ cần hai người duyệt (BR-132). Use case: UC-10-02.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| period\_close\_id | uuid, bắt buộc | FK period\_close |
| item\_code | text, bắt buộc | Ví dụ tổng dư nợ, số khoản vay |
| snapshot\_value | numeric, bắt buộc |  |
| ledger\_value | numeric, bắt buộc | Giá trị ở sổ cái hoặc nguồn đối chiếu |
| tolerance | numeric, bắt buộc |  |
| delta | numeric, bắt buộc |  |
| status | text, bắt buộc | matched, mismatched, exception\_pending, exception\_approved |
| cause\_rows\_ref | text | Dòng gây lệch trong MinIO |
| hitl\_request\_id | uuid | logic → inbox.hitl\_request khi xin ngoại lệ |

Cột chung: id, org\_id, created\_at. Ràng buộc: UNIQUE (period\_close\_id, item\_code); CHECK (status IN ('matched','mismatched','exception\_pending','exception\_approved')); CHECK (status <> 'matched' OR abs(delta) <= tolerance); CHECK (status NOT IN ('exception\_pending','exception\_approved') OR hitl\_request\_id IS NOT NULL); FOREIGN KEY (period\_close\_id) REFERENCES period.period\_close(id).

#### period.published\_result

Con số đã công bố, đóng băng; chỉ thêm, không sửa, không xoá (BR-42): vai trò CSDL của Period service chỉ có quyền INSERT và SELECT trên bảng này. Use case: UC-01-02, UC-10-01, UC-10-03, UC-10-04, UC-10-05.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| period\_close\_id | uuid, bắt buộc | FK period\_close |
| metric\_version\_id | uuid, bắt buộc | logic → context.metric\_version |
| dimension\_key | jsonb, bắt buộc, mặc định '{}' | Lát cắt nếu báo cáo có chiều |
| value | numeric, bắt buộc | Giá trị hệ thống tính trên bản chụp |
| reported\_value | numeric | Giá trị trên báo cáo thực tế đã nộp |
| manual\_override | boolean, bắt buộc, mặc định false | Số nhập tay khác số hệ thống; cần AC06 duyệt (BR-133) |
| override\_reason | text |  |
| approved\_by | uuid |  |
| report\_ref | text, bắt buộc | Mã báo cáo |
| sha256 | text, bắt buộc | Băm giá trị, phiên bản định nghĩa và bản chụp |

Cột chung: id, org\_id, created\_at. Ràng buộc: UNIQUE (period\_close\_id, metric\_version\_id, dimension\_key); CHECK (NOT manual\_override OR (reported\_value IS NOT NULL AND override\_reason IS NOT NULL AND approved\_by IS NOT NULL)); FOREIGN KEY (period\_close\_id) REFERENCES period.period\_close(id).

#### period.retro\_change

Thay đổi hồi tố phát hiện sau ngày chốt trên một số đã công bố. Use case: UC-10-04.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| period\_close\_id | uuid, bắt buộc | FK period\_close |
| published\_result\_id | uuid, bắt buộc | FK published\_result |
| current\_value | numeric, bắt buộc |  |
| delta | numeric, bắt buộc |  |
| below\_materiality | boolean, bắt buộc, mặc định false | Dưới ngưỡng trọng yếu thì không tạo yêu cầu |
| cause\_rows\_ref | text | Dòng có thời điểm ghi nhận sau ngày chốt |
| decision | text | new\_version, no\_action |
| decision\_note | text |  |
| new\_period\_close\_id | uuid | Bản 2 nếu có |
| compliance\_notified | boolean, bắt buộc, mặc định false | Chỉ số gửi cơ quan quản lý vượt ngưỡng luôn báo AC06 (BR-134) |
| detected\_at | timestamptz, bắt buộc, mặc định now() |  |

Cột chung: id, org\_id. Ràng buộc: CHECK (decision IS NULL OR decision IN ('new\_version','no\_action')); CHECK (decision IS NULL OR decision\_note IS NOT NULL); FOREIGN KEY (period\_close\_id) REFERENCES period.period\_close(id); FOREIGN KEY (published\_result\_id) REFERENCES period.published\_result(id).

### D4.18 Schema pack

#### pack.industry\_pack

Gói ngành do nhà cung cấp hoặc khối kiến trúc phát hành. Use case: UC-09-01.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| code | text, bắt buộc | Ví dụ banking, retail, hospitality |
| name | text, bắt buộc |  |
| industry\_code | text, bắt buộc |  |
| publisher | text, bắt buộc | Nhà cung cấp hoặc khối kiến trúc của tổ chức |
| publisher\_key\_id | text, bắt buộc | Khoá công khai dùng kiểm chữ ký (BR-128) |
| description | text |  |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: UNIQUE (org\_id, code).

#### pack.pack\_version

Phiên bản gói ngành: manifest, chữ ký, ghi chú phát hành, thay đổi phá vỡ. Use case: UC-09-01, UC-09-05.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| pack\_id | uuid, bắt buộc | FK industry\_pack |
| version | text, bắt buộc | Ví dụ 2026.3 |
| manifest | jsonb, bắt buộc |  |
| signature | text, bắt buộc |  |
| signature\_verified\_at | timestamptz |  |
| min\_core\_version | text, bắt buộc |  |
| release\_notes | text |  |
| breaking\_changes | jsonb, bắt buộc, mặc định '\[\]' | Có thì bắt buộc kèm hướng dẫn chuyển đổi và báo trước 30 ngày (BR-131) |
| object\_key | text, bắt buộc | MinIO da-packs |
| status | text, bắt buộc, mặc định 'draft' | draft, released, available, withdrawn |
| released\_at | timestamptz |  |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: UNIQUE (pack\_id, version); CHECK (status IN ('draft','released','available','withdrawn')); CHECK (status <> 'available' OR signature\_verified\_at IS NOT NULL); FOREIGN KEY (pack\_id) REFERENCES pack.industry\_pack(id).

#### pack.canonical\_entity

Thực thể trong mô hình chuẩn của ngành (ví dụ khoản vay, khách hàng). Use case: UC-09-01, UC-09-05.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| pack\_version\_id | uuid, bắt buộc | FK pack\_version |
| code | text, bắt buộc |  |
| name | text, bắt buộc |  |
| description | text |  |
| key\_attributes | text\[\], bắt buộc |  |

Cột chung: id, org\_id, created\_at. Ràng buộc: UNIQUE (pack\_version\_id, code); FOREIGN KEY (pack\_version\_id) REFERENCES pack.pack\_version(id) ON DELETE CASCADE.

#### pack.canonical\_attribute

Thuộc tính của thực thể chuẩn, kèm kiểu, bắt buộc, bảng mã chuẩn. Use case: UC-09-01, UC-09-05.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| entity\_id | uuid, bắt buộc | FK canonical\_entity |
| code | text, bắt buộc |  |
| name | text, bắt buộc |  |
| data\_type | text, bắt buộc | string, integer, decimal, boolean, date, timestamp, code |
| required | boolean, bắt buộc, mặc định false |  |
| code\_list | jsonb | Bảng mã chuẩn khi data\_type = code |
| pii | boolean, bắt buộc, mặc định false |  |

Cột chung: id, org\_id, created\_at. Ràng buộc: UNIQUE (entity\_id, code); CHECK (data\_type IN ('string','integer','decimal','boolean','date','timestamp','code')); FOREIGN KEY (entity\_id) REFERENCES pack.canonical\_entity(id) ON DELETE CASCADE.

#### pack.pack\_metric

Chỉ số chuẩn của gói, kèm căn cứ pháp lý hoặc nhãn “chỉ số nội bộ”. Use case: UC-09-01, UC-09-03, UC-09-05.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| pack\_version\_id | uuid, bắt buộc | FK pack\_version |
| code | text, bắt buộc | Không tái sử dụng cho nghĩa khác (BR-131) |
| name | text, bắt buộc |  |
| expression | text, bắt buộc | Công thức trên thực thể và thuộc tính chuẩn |
| unit | text |  |
| additivity | text, bắt buộc, mặc định 'none' | full, semi, none |
| legal\_basis | text |  |
| is\_internal | boolean, bắt buộc, mặc định false | Chỉ số nội bộ, không có căn cứ pháp lý |
| in\_regulatory\_report | boolean, bắt buộc, mặc định false | Có trong báo cáo gửi cơ quan quản lý (BR-36) |

Cột chung: id, org\_id, created\_at. Ràng buộc: UNIQUE (pack\_version\_id, code); CHECK (is\_internal OR legal\_basis IS NOT NULL); CHECK (additivity IN ('full','semi','none')); FOREIGN KEY (pack\_version\_id) REFERENCES pack.pack\_version(id) ON DELETE CASCADE.

#### pack.validation\_rule

Luật kiểm tra của gói (ví dụ tổng khớp sổ cái, mỗi bản ghi có đúng một phân loại). Use case: UC-09-01, UC-09-05.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| pack\_version\_id | uuid, bắt buộc | FK pack\_version |
| code | text, bắt buộc |  |
| description | text, bắt buộc |  |
| expression | text, bắt buộc |  |
| tolerance | numeric |  |
| severity | text, bắt buộc, mặc định 'blocking' | blocking, warning |

Cột chung: id, org\_id, created\_at. Ràng buộc: UNIQUE (pack\_version\_id, code); CHECK (severity IN ('blocking','warning')); FOREIGN KEY (pack\_version\_id) REFERENCES pack.pack\_version(id) ON DELETE CASCADE.

#### pack.pack\_skill

Kỹ năng, agent mẫu và bộ câu hỏi mẫu đi kèm gói ngành. Use case: UC-09-01, UC-13-01.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| pack\_version\_id | uuid, bắt buộc | FK pack\_version |
| kind | text, bắt buộc | skill, agent\_template, eval\_set |
| code | text, bắt buộc |  |
| name | text, bắt buộc |  |
| definition | jsonb, bắt buộc | Định nghĩa kỹ năng, cấu hình agent mẫu hoặc danh sách câu hỏi |

Cột chung: id, org\_id, created\_at. Ràng buộc: UNIQUE (pack\_version\_id, kind, code); CHECK (kind IN ('skill','agent\_template','eval\_set')); FOREIGN KEY (pack\_version\_id) REFERENCES pack.pack\_version(id) ON DELETE CASCADE.

#### pack.pack\_install

Gói đã cài ở một Data Space; mỗi Data Space một bản cài đang hoạt động. Use case: UC-09-02, UC-09-04.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| data\_space\_id | uuid, bắt buộc | logic → org.data\_space |
| pack\_version\_id | uuid, bắt buộc | FK pack\_version |
| previous\_pack\_version\_id | uuid | Giữ 30 ngày để quay lại sau nâng cấp |
| status | text, bắt buộc, mặc định 'mapping' | mapping, calibrating, pending\_activation, active, upgrading, rolled\_back, uninstalled |
| activated\_at | timestamptz |  |
| activated\_by | uuid |  |
| proposal\_id | uuid | logic → inbox.proposal |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: CHECK (status IN ('mapping','calibrating','pending\_activation','active','upgrading','rolled\_back','uninstalled')); FOREIGN KEY (pack\_version\_id) REFERENCES pack.pack\_version(id); FOREIGN KEY (previous\_pack\_version\_id) REFERENCES pack.pack\_version(id). Chỉ mục: duy nhất (data\_space\_id) WHERE status NOT IN ('uninstalled','rolled\_back').

#### pack.entity\_mapping

Ánh xạ thuộc tính chuẩn sang bảng, cột của tổ chức; có thời hiệu (BR-16, BR-37). Use case: UC-04-03, UC-09-02, UC-09-04, UC-10-01.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| pack\_install\_id | uuid, bắt buộc | FK pack\_install |
| attribute\_id | uuid, bắt buộc | FK canonical\_attribute |
| dataset\_id | uuid, bắt buộc | logic → catalog.dataset |
| column\_expr | text, bắt buộc | Cột hoặc biểu thức |
| confidence | numeric(4,3) | Độ tin của đề xuất ánh xạ |
| origin | text, bắt buộc, mặc định 'suggested' | suggested, confirmed, manual |
| valid\_from | timestamptz, bắt buộc, mặc định now() |  |
| valid\_to | timestamptz |  |
| confirmed\_by | uuid |  |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: CHECK (origin IN ('suggested','confirmed','manual')); CHECK (valid\_to IS NULL OR valid\_to > valid\_from); FOREIGN KEY (pack\_install\_id) REFERENCES pack.pack\_install(id); FOREIGN KEY (attribute\_id) REFERENCES pack.canonical\_attribute(id). Chỉ mục: duy nhất (pack\_install\_id, attribute\_id) WHERE valid\_to IS NULL.

#### pack.code\_mapping

Bảng mã của tổ chức sang mã chuẩn (ví dụ phân loại, loại tiền). Use case: UC-09-02.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| pack\_install\_id | uuid, bắt buộc | FK pack\_install |
| attribute\_id | uuid, bắt buộc | FK canonical\_attribute (data\_type = code) |
| source\_code | text, bắt buộc |  |
| canonical\_code | text, bắt buộc |  |
| valid\_from | timestamptz, bắt buộc, mặc định now() |  |
| valid\_to | timestamptz |  |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: FOREIGN KEY (pack\_install\_id) REFERENCES pack.pack\_install(id); FOREIGN KEY (attribute\_id) REFERENCES pack.canonical\_attribute(id). Chỉ mục: duy nhất (pack\_install\_id, attribute\_id, source\_code) WHERE valid\_to IS NULL.

#### pack.metric\_override

Hiệu chỉnh chỉ số ở lớp tổ chức kèm lý do; tham gia gộp ba chiều khi nâng cấp (BR-129). Use case: UC-09-02, UC-09-03, UC-09-04.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| pack\_install\_id | uuid, bắt buộc | FK pack\_install |
| pack\_metric\_code | text, bắt buộc | Mã chỉ số chuẩn; giữ qua các phiên bản gói |
| expression | text, bắt buộc |  |
| scope\_condition | text | Chỉ áp cho một phạm vi (UC-09-03 2a) |
| reason | text, bắt buộc |  |
| status | text, bắt buộc, mặc định 'draft' | draft, pending\_review, active, superseded |
| approved\_by | uuid |  |
| compliance\_approved\_by | uuid | Chỉ số báo cáo cơ quan quản lý cần thêm AC06 |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: CHECK (status IN ('draft','pending\_review','active','superseded')); CHECK (status <> 'active' OR approved\_by IS NOT NULL); FOREIGN KEY (pack\_install\_id) REFERENCES pack.pack\_install(id). Chỉ mục: duy nhất (pack\_install\_id, pack\_metric\_code) WHERE status = 'active'.

#### pack.calibration\_run

Một lần đối chiếu số khi đưa vào ngành, hiệu chỉnh hoặc nâng cấp gói. Use case: UC-09-02, UC-09-03, UC-09-04.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| pack\_install\_id | uuid, bắt buộc | FK pack\_install |
| trigger | text, bắt buộc | onboarding, override, upgrade |
| periods | text\[\], bắt buộc | Kỳ đối chiếu, ví dụ 2 kỳ gần nhất |
| tolerance\_pp | numeric(6,4), bắt buộc, mặc định 0.01 | Ngưỡng lệch, mặc định 0,01 điểm phần trăm (BR-36) |
| passed | integer, bắt buộc, mặc định 0 |  |
| failed | integer, bắt buộc, mặc định 0 |  |
| status | text, bắt buộc, mặc định 'running' | running, passed, failed |
| finished\_at | timestamptz |  |

Cột chung: id, org\_id, created\_at. Ràng buộc: CHECK (trigger IN ('onboarding','override','upgrade')); CHECK (status IN ('running','passed','failed')); FOREIGN KEY (pack\_install\_id) REFERENCES pack.pack\_install(id).

#### pack.calibration\_item

Kết quả đối chiếu một chỉ số hoặc một luật trong một lần đối chiếu. Use case: UC-09-02.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| calibration\_run\_id | uuid, bắt buộc | FK calibration\_run |
| item\_kind | text, bắt buộc | metric, rule |
| item\_code | text, bắt buộc |  |
| period | text |  |
| computed\_value | numeric |  |
| filed\_value | numeric |  |
| delta | numeric |  |
| passed | boolean, bắt buộc |  |
| suggested\_cause | text |  |

Cột chung: id, org\_id, created\_at. Ràng buộc: CHECK (item\_kind IN ('metric','rule')); FOREIGN KEY (calibration\_run\_id) REFERENCES pack.calibration\_run(id) ON DELETE CASCADE.

#### pack.pack\_upgrade

Một lần nâng cấp gói bằng gộp ba chiều (SD-21); không làm khi có kỳ đang chốt (BR-38). Use case: UC-09-04.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| pack\_install\_id | uuid, bắt buộc | FK pack\_install |
| from\_version\_id | uuid, bắt buộc | FK pack\_version |
| to\_version\_id | uuid, bắt buộc | FK pack\_version |
| merge\_result | jsonb, bắt buộc, mặc định '{}' | Theo từng chỉ số và ánh xạ: lấy gói mới, giữ của tổ chức, xung đột |
| conflicts\_open | integer, bắt buộc, mặc định 0 |  |
| calibration\_run\_id | uuid | FK calibration\_run |
| status | text, bắt buộc, mặc định 'available' | available, merging, needs\_decision, testing, pending\_approval, applied, rolled\_back, cancelled |
| apply\_from\_period | text | Áp dụng từ kỳ sau; kỳ đã chốt không đổi |
| rollback\_until | timestamptz | 30 ngày sau khi áp dụng |
| approved\_by | uuid |  |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: CHECK (status IN ('available','merging','needs\_decision','testing','pending\_approval','applied','rolled\_back','cancelled')); CHECK (status <> 'applied' OR (conflicts\_open = 0 AND approved\_by IS NOT NULL)); FOREIGN KEY (pack\_install\_id) REFERENCES pack.pack\_install(id); FOREIGN KEY (from\_version\_id) REFERENCES pack.pack\_version(id); FOREIGN KEY (to\_version\_id) REFERENCES pack.pack\_version(id); FOREIGN KEY (calibration\_run\_id) REFERENCES pack.calibration\_run(id).

#### pack.pack\_check\_run

Kết quả kiểm tra một phiên bản gói trước khi phát hành. Use case: UC-09-05.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| pack\_version\_id | uuid, bắt buộc | FK pack\_version |
| checks | jsonb, bắt buộc | Căn cứ đầy đủ; chạy trên 3 bộ dữ liệu mẫu lệch ≤ 0,01%; câu hỏi mẫu không giảm |
| passed | boolean, bắt buộc |  |
| ran\_by | uuid, bắt buộc |  |

Cột chung: id, org\_id, created\_at. Ràng buộc: FOREIGN KEY (pack\_version\_id) REFERENCES pack.pack\_version(id).

### D4.19 Schema pipeline

#### pipeline.pipeline

Pipeline biến đổi: các bước làm sạch, join, tính cột; chạy trong vùng làm việc, không ghi vào nguồn (BR-22). Use case: UC-06-01, UC-06-02, UC-06-03.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| data\_space\_id | uuid, bắt buộc |  |
| name | text, bắt buộc |  |
| owner\_id | uuid, bắt buộc |  |
| input\_dataset\_ids | uuid\[\], bắt buộc | logic → catalog.dataset |
| steps | jsonb, bắt buộc, mặc định '\[\]' | Mỗi bước: loại, mô tả bằng lời, mã SQL hoặc Python, số dòng bị loại và điều kiện (BR-23) |
| target\_dataset\_id | uuid | logic → catalog.dataset sau khi lưu thành bảng |
| scope | text, bắt buộc, mặc định 'personal' | personal, data\_space (BR-111) |
| status | text, bắt buộc, mặc định 'draft' | draft, active, paused |
| cron | text |  |
| work\_calendar\_id | uuid |  |
| trigger\_on\_job\_id | uuid | logic → ingest.ingestion\_job |
| depends\_on\_pipeline\_ids | uuid\[\], bắt buộc, mặc định '{}' | Chỉ chạy khi pipeline gốc cùng kỳ đã thành công (BR-112) |
| no\_overlap | boolean, bắt buộc, mặc định true |  |
| on\_failure | text, bắt buộc, mặc định 'notify' | notify, retry\_then\_notify, skip |
| deleted\_at | timestamptz | Xoá mềm |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: CHECK (scope IN ('personal','data\_space')); CHECK (status IN ('draft','active','paused')); CHECK (on\_failure IN ('notify','retry\_then\_notify','skip')); UNIQUE (data\_space\_id, name).

#### pipeline.pipeline\_run

Một lần chạy pipeline, kể cả chạy thử trên mẫu. Use case: UC-06-01, UC-06-02, UC-06-03, UC-06-07.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| pipeline\_id | uuid, bắt buộc | FK pipeline |
| mode | text, bắt buộc | preview, full, scheduled |
| sample\_rows | integer | Chạy thử trên 10.000 dòng |
| rows\_in | bigint |  |
| rows\_out | bigint |  |
| rows\_dropped | jsonb, bắt buộc, mặc định '\[\]' | Theo từng bước |
| totals\_check | jsonb | Tổng trước và sau của cột tiền; lệch thì không lưu (UC-06-01 E2) |
| status | text, bắt buộc, mặc định 'running' | running, succeeded, failed, skipped\_overlap |
| workflow\_id | text |  |
| started\_at | timestamptz, bắt buộc, mặc định now() |  |
| finished\_at | timestamptz |  |
| error | text |  |

Cột chung: id, org\_id, created\_at. Ràng buộc: CHECK (mode IN ('preview','full','scheduled')); CHECK (status IN ('running','succeeded','failed','skipped\_overlap')); FOREIGN KEY (pipeline\_id) REFERENCES pipeline.pipeline(id). Chỉ mục: (pipeline\_id, started\_at DESC).

#### pipeline.lineage\_edge

Cạnh dòng dữ liệu giữa hai đối tượng; mọi dịch vụ tạo đối tượng dẫn xuất phải phát sự kiện để ghi cạnh (BR-114). Use case: UC-01-02, UC-02-07, UC-06-02, UC-06-05, UC-06-06.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| data\_space\_id | uuid, bắt buộc |  |
| from\_kind | text, bắt buộc | source, dataset, column, metric, tile, report, skill, agent, data\_product |
| from\_id | uuid, bắt buộc |  |
| to\_kind | text, bắt buộc |  |
| to\_id | uuid, bắt buộc |  |
| via | text | Ví dụ pipeline, semantic, query |
| via\_ref | uuid |  |
| valid\_to | timestamptz | Đóng khi quan hệ không còn |

Cột chung: id, org\_id, created\_at. Ràng buộc: CHECK (from\_kind IN ('source','dataset','column','metric','tile','report','skill','agent','data\_product') AND to\_kind IN ('source','dataset','column','metric','tile','report','skill','agent','data\_product')); UNIQUE NULLS NOT DISTINCT (from\_kind, from\_id, to\_kind, to\_id, valid\_to). Chỉ mục: (from\_kind, from\_id) WHERE valid\_to IS NULL; (to\_kind, to\_id) WHERE valid\_to IS NULL.

#### pipeline.data\_mart

Data mart mô hình sao: bảng sự kiện, hạt, độ đo, chiều, kiểm tra. Use case: UC-06-06.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| data\_space\_id | uuid, bắt buộc |  |
| name | text, bắt buộc |  |
| fact\_dataset\_id | uuid, bắt buộc |  |
| grain | text, bắt buộc | Ví dụ khoản vay × ngày |
| measures | jsonb, bắt buộc |  |
| dimensions | jsonb, bắt buộc |  |
| relations | jsonb, bắt buộc |  |
| checks | jsonb, bắt buộc, mặc định '\[\]' | Tổng khớp nguồn, độ mới, khoá chiều duy nhất |
| relation\_check\_passed | boolean, bắt buộc, mặc định false | Thất bại thì không phát hành được |
| status | text, bắt buộc, mặc định 'draft' | draft, active, retired |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: CHECK (status IN ('draft','active','retired')); CHECK (status <> 'active' OR relation\_check\_passed).

#### pipeline.data\_product

Sản phẩm dữ liệu có hợp đồng, phiên bản và danh sách bên dùng; thay đổi phá vỡ báo trước 30 ngày (BR-60). Use case: UC-06-06.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| data\_space\_id | uuid, bắt buộc | Data Space cung cấp |
| code | text, bắt buộc |  |
| name | text, bắt buộc |  |
| version | integer, bắt buộc, mặc định 1 |  |
| dataset\_id | uuid |  |
| data\_mart\_id | uuid | FK data\_mart |
| contract | jsonb, bắt buộc | Cột, kiểu, ý nghĩa, SLA |
| consumers | uuid\[\], bắt buộc, mặc định '{}' | Data Space đăng ký dùng |
| owner\_id | uuid, bắt buộc | logic → org.app\_user; người phụ trách duyệt phát hành, khác người soạn (thêm 12/10/2026) |
| status | text, bắt buộc, mặc định 'draft' | draft, published, deprecated, retired |
| breaking\_notice\_at | timestamptz |  |
| retire\_after | timestamptz |  |
| approved\_by | uuid |  |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: UNIQUE (data\_space\_id, code, version); CHECK (status IN ('draft','published','deprecated','retired')); CHECK (dataset\_id IS NOT NULL OR data\_mart\_id IS NOT NULL); CHECK (status <> 'published' OR approved\_by IS NOT NULL); CHECK (approved\_by IS NULL OR approved\_by = owner\_id); CHECK (retire\_after IS NULL OR breaking\_notice\_at IS NULL OR retire\_after >= breaking\_notice\_at + interval '30 days'); FOREIGN KEY (data\_mart\_id) REFERENCES pipeline.data\_mart(id).

#### pipeline.writeback\_request

Yêu cầu ghi ngược về nguồn qua xem trước, duyệt, bảng tạm, kiểm tổng (SD-06). Use case: UC-06-04.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| data\_space\_id | uuid, bắt buộc |  |
| source\_id | uuid, bắt buộc | logic → catalog.source |
| target\_table | text, bắt buộc |  |
| from\_dataset\_id | uuid, bắt buộc | Bảng dẫn xuất làm đầu vào |
| requested\_by | uuid, bắt buộc |  |
| diff\_summary | jsonb, bắt buộc, mặc định '{}' | Số dòng thêm, sửa, xoá; mẫu trước và sau, không có cột nhạy cảm |
| diff\_ref | text |  |
| deletes | integer, bắt buộc, mặc định 0 | Phải bằng 0 (BR-113) |
| write\_window | tstzrange |  |
| snapshot\_ref | text | Ảnh chụp bảng đích để hoàn tác 30 ngày |
| approved\_by | uuid | Phải khác người yêu cầu (BR-24) |
| proposal\_id | uuid | logic → inbox.proposal |
| status | text, bắt buộc, mặc định 'draft' | draft, previewed, submitted, approved, rejected, scheduled, executing, executed, cancelled\_mismatch, failed, undo\_requested, undone |
| executed\_at | timestamptz |  |
| totals\_check | jsonb |  |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: CHECK (status IN ('draft','previewed','submitted','approved','rejected','scheduled','executing','executed','cancelled\_mismatch','failed','undo\_requested','undone')); CHECK (deletes = 0); CHECK (approved\_by IS NULL OR approved\_by <> requested\_by); CHECK (status NOT IN ('approved','scheduled','executing','executed') OR approved\_by IS NOT NULL).

### D4.20 Schema ingest

#### ingest.ingestion\_job

Job nạp dữ liệu từ một nguồn về vùng dữ liệu: toàn phần, gia tăng hoặc CDC. Use case: UC-04-06, UC-04-07, UC-04-09.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| data\_space\_id | uuid, bắt buộc |  |
| source\_id | uuid, bắt buộc | logic → catalog.source |
| name | text, bắt buộc |  |
| mode | text, bắt buộc | full, incremental, cdc |
| source\_object | text, bắt buộc | Bảng, chủ đề Kafka, đường dẫn tệp hoặc endpoint |
| target\_dataset\_id | uuid | logic → catalog.dataset |
| cursor\_column | text | Cột con trỏ khi incremental |
| cursor\_value | text |  |
| schedule\_cron | text |  |
| freshness\_sla | interval | Trễ quá hạn thì nguồn “Cần sửa” (BR-56) |
| dlq\_retention\_days | integer, bắt buộc, mặc định 30 | BR-57 |
| status | text, bắt buộc, mặc định 'active' | draft, active, paused, failing, schema\_drift |
| paused\_reason | text | Bắt buộc khi tạm dừng (BR-106) |
| paused\_at | timestamptz |  |
| consecutive\_failures | smallint, bắt buộc, mặc định 0 |  |
| deleted\_at | timestamptz | Xoá mềm |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: CHECK (mode IN ('full','incremental','cdc')); CHECK (mode <> 'incremental' OR cursor\_column IS NOT NULL); CHECK (status IN ('draft','active','paused','failing','schema\_drift')); CHECK (status <> 'paused' OR paused\_reason IS NOT NULL).

#### ingest.job\_run

Một lần chạy job nạp: số dòng đọc, ghi, vào DLQ, kết quả chất lượng. Use case: UC-04-06, UC-04-07, UC-04-09, UC-06-07.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| job\_id | uuid, bắt buộc | FK ingestion\_job |
| workflow\_id | text |  |
| rows\_read | bigint, bắt buộc, mặc định 0 |  |
| rows\_written | bigint, bắt buộc, mặc định 0 |  |
| rows\_dlq | bigint, bắt buộc, mặc định 0 |  |
| cursor\_from | text |  |
| cursor\_to | text |  |
| lag\_seconds | integer | Độ trễ so với nguồn |
| status | text, bắt buộc, mặc định 'running' | running, succeeded, failed, schema\_drift, cancelled |
| started\_at | timestamptz, bắt buộc, mặc định now() |  |
| finished\_at | timestamptz |  |
| error | text |  |

Cột chung: id, org\_id, created\_at. Ràng buộc: CHECK (status IN ('running','succeeded','failed','schema\_drift','cancelled')); FOREIGN KEY (job\_id) REFERENCES ingest.ingestion\_job(id). Chỉ mục: (job\_id, started\_at DESC).

#### ingest.dlq\_record

Bản ghi lỗi chờ xử lý; giữ 30 ngày, nạp lại lỗi quá 3 lần thì khoá tự động (UC-04-07). Use case: UC-04-07, UC-04-09.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| job\_id | uuid, bắt buộc | FK ingestion\_job |
| job\_run\_id | uuid, bắt buộc | FK job\_run |
| payload\_ref | text, bắt buộc | Bản ghi gốc trong MinIO, đã che |
| error\_kind | text, bắt buộc | schema\_mismatch, type\_error, constraint, source\_data |
| error | text, bắt buộc |  |
| replay\_count | smallint, bắt buộc, mặc định 0 |  |
| status | text, bắt buộc, mặc định 'pending' | pending, replayed, discarded, locked |
| discard\_reason | text | Bắt buộc khi bỏ (BR-104) |
| expires\_at | timestamptz, bắt buộc, mặc định now() + interval '30 days' |  |

Cột chung: id, org\_id, created\_at. Ràng buộc: CHECK (error\_kind IN ('schema\_mismatch','type\_error','constraint','source\_data')); CHECK (status IN ('pending','replayed','discarded','locked')); CHECK (status <> 'discarded' OR discard\_reason IS NOT NULL); CHECK (replay\_count <= 3 OR status IN ('locked','replayed','discarded')); FOREIGN KEY (job\_id) REFERENCES ingest.ingestion\_job(id); FOREIGN KEY (job\_run\_id) REFERENCES ingest.job\_run(id). Chỉ mục: (job\_id) WHERE status = 'pending'; (expires\_at).

#### ingest.file\_upload

Tệp CSV, Excel tải lên: nhận dạng cấu trúc, ánh xạ cột, kiểm tra với dữ liệu sẵn có. Use case: UC-04-08.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| data\_space\_id | uuid, bắt buộc |  |
| uploader\_id | uuid, bắt buộc |  |
| file\_name | text, bắt buộc |  |
| object\_key | text, bắt buộc | MinIO da-ingest |
| size\_bytes | bigint, bắt buộc | Tối đa 200 MB |
| sheet | text |  |
| header\_row | integer |  |
| encoding | text |  |
| decimal\_sep | text |  |
| column\_map | jsonb, bắt buộc, mặc định '\[\]' | Cột nguồn, kiểu, đơn vị, cột đích, cột dữ liệu cá nhân bị che |
| validation | jsonb, bắt buộc, mặc định '{}' | Ví dụ mã khớp 99,6%, 14 dòng sai định dạng |
| error\_ratio | numeric(5,4) | Trên 5% thì không cho ghi (UC-04-08 E2) |
| target\_dataset\_id | uuid |  |
| replace\_mode | text | replace, keep khi ghép vào bảng có sẵn |
| expires\_at | timestamptz, bắt buộc, mặc định now() + interval '90 days' | BR-105 |
| status | text, bắt buộc, mặc định 'uploaded' | uploaded, analyzed, mapped, committed, rejected, expired |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: CHECK (size\_bytes <= 209715200); CHECK (replace\_mode IS NULL OR replace\_mode IN ('replace','keep')); CHECK (status IN ('uploaded','analyzed','mapped','committed','rejected','expired')); CHECK (status <> 'committed' OR (error\_ratio IS NOT NULL AND error\_ratio <= 0.05 AND target\_dataset\_id IS NOT NULL)).

#### ingest.quality\_rule

Quy tắc chất lượng trên bảng hoặc cột kèm mức nghiêm trọng. Use case: UC-04-08, UC-06-06, UC-06-07.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| data\_space\_id | uuid, bắt buộc |  |
| dataset\_id | uuid, bắt buộc | logic → catalog.dataset |
| column\_name | text |  |
| kind | text, bắt buộc | not\_null, range, unique, in\_list, sum\_matches, freshness, custom\_sql |
| params | jsonb, bắt buộc, mặc định '{}' |  |
| severity | text, bắt buộc, mặc định 'medium' | high (chặn báo cáo, BR-116), medium, low |
| origin | text, bắt buộc, mặc định 'manual' | manual, agent\_suggested, pack |
| status | text, bắt buộc, mặc định 'active' | draft, active, disabled |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: CHECK (kind IN ('not\_null','range','unique','in\_list','sum\_matches','freshness','custom\_sql')); CHECK (severity IN ('high','medium','low')); CHECK (origin IN ('manual','agent\_suggested','pack')); CHECK (status IN ('draft','active','disabled')). Chỉ mục: (dataset\_id) WHERE status = 'active'.

#### ingest.quality\_result

Kết quả một lần chạy quy tắc chất lượng sau nạp hoặc pipeline. Use case: UC-04-09, UC-06-07.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| rule\_id | uuid, bắt buộc | FK quality\_rule |
| run\_kind | text, bắt buộc | job\_run, pipeline\_run, scheduled, manual |
| run\_ref | uuid |  |
| passed | boolean, bắt buộc |  |
| violations | bigint, bắt buộc, mặc định 0 |  |
| sample\_ref | text | Mẫu dòng vi phạm, đã che |
| acknowledged\_by | uuid | Người xác nhận dùng tiếp khi vi phạm mức cao |
| acknowledged\_at | timestamptz | Thêm 12/10/2026 |
| acknowledged\_note | text | Lý do dùng tiếp, bắt buộc khi có acknowledged\_by (BR-116, thêm 12/10/2026) |
| checked\_at | timestamptz, bắt buộc, mặc định now() |  |

Cột chung: id, org\_id. Ràng buộc: CHECK (run\_kind IN ('job\_run','pipeline\_run','scheduled','manual')); CHECK (acknowledged\_by IS NULL OR (acknowledged\_at IS NOT NULL AND acknowledged\_note IS NOT NULL)); FOREIGN KEY (rule\_id) REFERENCES ingest.quality\_rule(id). Chỉ mục: (rule\_id, checked\_at DESC).

### D4.21 Schema audit (CSDL dataagent\_audit)

#### audit.audit\_log

Nhật ký kiểm toán chỉ ghi thêm, chuỗi băm phát hiện bị sửa (BR-47); nằm trong CSDL dataagent\_audit. Use case: UC-01-01, UC-01-02, UC-01-06, UC-02-03, UC-04-01, UC-06-04, UC-07-07, UC-08-06, UC-10-02, UC-10-03, UC-11-01, UC-11-02, UC-11-03, UC-11-06, UC-11-08, UC-12-01, UC-12-02, UC-12-04.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| seq | bigint, bắt buộc | Số thứ tự tăng dần trong tổ chức, dùng cho chuỗi băm |
| occurred\_at | timestamptz, bắt buộc, mặc định now() |  |
| actor\_kind | text, bắt buộc | user, agent, client, system |
| actor\_id | uuid |  |
| on\_behalf\_of | uuid | Người mà agent hoặc client chạy thay |
| data\_space\_id | uuid |  |
| service | text, bắt buộc |  |
| action | text, bắt buộc | Ví dụ source.created, query.executed, access.denied, mcp.tools\_call |
| object\_kind | text |  |
| object\_id | uuid |  |
| outcome | text, bắt buộc, mặc định 'ok' | ok, denied, error |
| before | jsonb | Đã che dữ liệu cá nhân |
| after | jsonb | Đã che dữ liệu cá nhân |
| agent\_run\_id | uuid |  |
| ip | inet |  |
| prev\_hash | text, bắt buộc |  |
| hash | text, bắt buộc | SHA-256 của nội dung dòng và prev\_hash |

Cột chung: id, org\_id. Phân vùng theo tháng trên occurred\_at. Ràng buộc: CHECK (actor\_kind IN ('user','agent','client','system')); CHECK (outcome IN ('ok','denied','error')). Chỉ mục: (actor\_id, occurred\_at); (object\_kind, object\_id); (org\_id, seq).

#### audit.security\_event

Sự kiện bảo mật: nghi chèn lệnh, lộ dữ liệu cá nhân, chuỗi băm đứt, lượt bị chặn tăng đột biến. Use case: UC-05-02, UC-11-03, UC-12-04.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| occurred\_at | timestamptz, bắt buộc, mặc định now() |  |
| kind | text, bắt buộc | prompt\_injection, pii\_leak\_blocked, hash\_chain\_broken, denied\_spike, signature\_invalid, egress\_blocked |
| severity | text, bắt buộc | low, medium, high, critical |
| data\_space\_id | uuid |  |
| actor\_id | uuid |  |
| source\_ref | text | Ví dụ chunk, agent\_run, api\_client |
| detail | jsonb, bắt buộc, mặc định '{}' |  |
| status | text, bắt buộc, mặc định 'open' | open, investigating, closed |
| handled\_by | uuid |  |

Cột chung: id, org\_id. Ràng buộc: CHECK (kind IN ('prompt\_injection','pii\_leak\_blocked','hash\_chain\_broken','denied\_spike','signature\_invalid','egress\_blocked')); CHECK (severity IN ('low','medium','high','critical')); CHECK (status IN ('open','investigating','closed')). Chỉ mục: (severity, occurred\_at) WHERE status <> 'closed'.

#### audit.export\_job

Bản xuất nhật ký kiểm toán có chữ ký số: bộ lọc, số dòng, duyệt người thứ hai khi quá 100.000 dòng, tệp trong MinIO (BR-138); nằm trong CSDL dataagent\_audit cùng các dòng nó xuất. Thêm 12/10/2026. Use case: UC-11-03.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| filters | jsonb, bắt buộc, mặc định '{}' | Bộ lọc của GET /v1/audit lúc yêu cầu |
| format | text, bắt buộc, mặc định 'csv' | csv, jsonl |
| row\_count | bigint | Số dòng sẽ xuất, đếm lúc tạo |
| status | text, bắt buộc, mặc định 'queued' | queued, waiting\_approval, running, ready, failed, expired |
| requested\_by | uuid, bắt buộc | logic → org.app\_user |
| approved\_by | uuid | logic → org.app\_user; người thứ hai duyệt khi row\_count quá 100.000, khác requested\_by (BR-138) |
| approved\_at | timestamptz |  |
| hitl\_request\_id | uuid | logic → inbox.hitl\_request khi chờ duyệt |
| object\_key | text | Đường dẫn tệp trong MinIO |
| signature | text | Chữ ký số của hệ thống trên SHA-256 của tệp |
| expires\_at | timestamptz | Hết hạn tải tệp |
| created\_at | timestamptz, bắt buộc, mặc định now() |  |
| updated\_at | timestamptz, bắt buộc, mặc định now() |  |

Cột chung: id, org\_id. Ràng buộc: CHECK (format IN ('csv','jsonl')); CHECK (status IN ('queued','waiting\_approval','running','ready','failed','expired')); CHECK (approved\_by IS NULL OR approved\_by <> requested\_by); CHECK (status NOT IN ('running','ready') OR (row\_count IS NOT NULL AND (row\_count <= 100000 OR approved\_by IS NOT NULL))); CHECK (status <> 'ready' OR (object\_key IS NOT NULL AND signature IS NOT NULL)). Chỉ mục: (requested\_by, created\_at DESC). RLS: org\_isolation. Tệp chỉ chứa dữ liệu đã che (BR-138).

### D4.22 Schema acquire

#### acquire.crawl\_source

Nguồn thu thập bên ngoài cùng phạm vi, lịch và cổng duyệt, ví dụ trang lãi suất huy động của một ngân hàng. Thay KBSource của code hiện tại. Use case: UC-14-01, UC-14-02, UC-14-06.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| data\_space\_id | uuid, bắt buộc | logic → org.data\_space |
| name | text, bắt buộc |  |
| goal | text, bắt buộc | Mô tả dữ liệu cần thu, ví dụ “lãi suất huy động 12 tháng của các ngân hàng hằng ngày” |
| kind | text, bắt buộc | web\_page, web\_listing, public\_api, feed, file\_drop |
| seed\_urls | jsonb, bắt buộc, mặc định '\[\]' | URL hạt giống |
| allowed\_domains | text\[\], bắt buộc | Chỉ thu trong các miền này (BR-149) |
| max\_depth | integer, bắt buộc, mặc định 3 | Độ sâu tối đa khi theo liên kết |
| schedule\_cron | text |  |
| rate\_limit\_rps | numeric, bắt buộc, mặc định 1 | Số yêu cầu mỗi giây mỗi miền (BR-149) |
| auth\_mode | text, bắt buộc, mặc định 'none' | none, api\_key |
| secret\_ref | text | Đường dẫn bí mật trong OpenBao; bắt buộc khi api\_key |
| openapi\_url | text | URL tài liệu OpenAPI khi kind = public\_api; có thì đọc schema từ tài liệu thay cho suy ngược (UC-14-01) |
| robots\_checked\_at | timestamptz | Lần kiểm robots.txt gần nhất |
| terms\_note | text | Ghi chú điều khoản sử dụng, lý do chặn nếu có |
| terms\_url | text | Trang điều khoản sử dụng hoặc giấy phép dữ liệu của nguồn (BR-174) |
| terms\_sha256 | text | Băm SHA-256 văn bản điều khoản đã chuẩn hoá tại lần duyệt pháp lý (BR-174) |
| terms\_observed\_sha256 | text | Băm lần kiểm gần nhất (mỗi ngày và trước mỗi lượt); khác terms\_sha256 thì duyệt hết hiệu lực, nguồn tự tạm ngưng (BR-174) |
| terms\_checked\_at | timestamptz | Lần kiểm trang điều khoản gần nhất |
| legal\_status | text, bắt buộc, mặc định 'pending' | pending, approved, rejected, expired; nguồn chỉ active khi approved và chưa quá hạn xem lại (BR-174) |
| legal\_checklist | jsonb, bắt buộc, mặc định '{}' | Kết quả bốn mục duyệt {terms, robots, copyright, personal\_data}, mỗi mục {result, note} |
| legal\_evidence | jsonb, bắt buộc, mặc định '\[\]' | Bằng chứng: ảnh chụp trang điều khoản, robots.txt, ghi chú bản quyền {kind, object\_key, sha256, captured\_at} ở MinIO da-raw-crawl/legal; tối thiểu 1 khi approved |
| legal\_reviewed\_by | uuid | logic → org.app\_user; người duyệt pháp lý (AC06), khác owner\_id |
| legal\_reviewed\_at | timestamptz | Lúc duyệt pháp lý |
| legal\_review\_due\_at | timestamptz | Hạn xem lại, mặc định 6 tháng, tối đa 184 ngày sau legal\_reviewed\_at (BR-174) |
| review\_gate | text, bắt buộc, mặc định 'all' | all, exceptions, auto; auto chỉ bật khi độ chính xác mẫu ≥ 98% trong 4 lượt liên tiếp (UC-14-04) |
| trust\_level | numeric(3,2), bắt buộc | Mức tin cậy nguồn 0 đến 1; dữ liệu mang nhãn “nguồn ngoài” (BR-156) |
| status | text, bắt buộc, mặc định 'draft' | draft, active, paused, quarantined, retired |
| owner\_id | uuid, bắt buộc | logic → org.app\_user; người phụ trách nguồn |
| active\_schema\_version\_id | uuid | FK crawl\_schema\_version đang active |
| consecutive\_blocked | integer, bắt buộc, mặc định 0 | Bị chặn 3 lượt liên tiếp thì tạm ngưng (UC-14-06) |
| last\_run\_at | timestamptz |  |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: CHECK (kind IN ('web\_page','web\_listing','public\_api','feed','file\_drop')); CHECK (auth\_mode IN ('none','api\_key')); CHECK (auth\_mode <> 'api\_key' OR secret\_ref IS NOT NULL); CHECK (review\_gate IN ('all','exceptions','auto')); CHECK (trust\_level BETWEEN 0 AND 1); CHECK (rate\_limit\_rps > 0); CHECK (status IN ('draft','active','paused','quarantined','retired')); CHECK (status <> 'active' OR active\_schema\_version\_id IS NOT NULL); UNIQUE (data\_space\_id, name); FOREIGN KEY (active\_schema\_version\_id) REFERENCES acquire.crawl\_schema\_version(id) DEFERRABLE INITIALLY DEFERRED. Chỉ mục: (data\_space\_id, status); (last\_run\_at) WHERE status = 'active'. Trigger: review\_gate = 'auto' chỉ đặt được khi 4 lượt gần nhất có sample\_accuracy ≥ 0,98 (UC-14-04). Bổ sung 10/10/2026 (BR-174): CHECK (legal\_status IN ('pending','approved','rejected','expired')); CHECK (legal\_status <> 'approved' OR (legal\_reviewed\_by, legal\_reviewed\_at, terms\_url, terms\_sha256 có giá trị AND legal\_review\_due\_at > legal\_reviewed\_at AND legal\_review\_due\_at <= legal\_reviewed\_at + interval '184 days' AND legal\_checklist ?& ARRAY\['terms','robots','copyright','personal\_data'\] AND jsonb\_array\_length(legal\_evidence) >= 1)); CHECK (legal\_reviewed\_by IS NULL OR legal\_reviewed\_by <> owner\_id); hai cột băm khớp '^\[0-9a-f\]{64}$'. Trigger crawl\_source\_legal\_guard: terms\_observed\_sha256 khác terms\_sha256 thì legal\_status = 'expired'; nguồn active mất duyệt thì tự chuyển paused; chuyển active khi chưa approved hoặc legal\_review\_due\_at <= now() thì chặn. Chỉ mục: (legal\_review\_due\_at) WHERE legal\_status = 'approved', cho việc nhắc xem lại 14 ngày trước hạn.

#### acquire.crawl\_schema\_version

Phiên bản schema thu thập của một nguồn: vùng bản ghi, trường, vị trí trích, ánh xạ thuộc tính chuẩn; sửa luôn tạo phiên bản mới, hoàn tác quay về phiên bản đúng gần nhất (BR-151, BR-152). Thay “schema crawler” trong Connector service. Use case: UC-14-02, UC-14-03.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| source\_id | uuid, bắt buộc | FK crawl\_source |
| version | integer, bắt buộc |  |
| status | text, bắt buộc, mặc định 'draft' | draft, testing, active, superseded, rejected |
| record\_selector | text, bắt buộc | Vùng bản ghi lặp: CSS/XPath, JSONPath, bảng hoặc vùng trang PDF |
| fields | jsonb, bắt buộc, mặc định '\[\]' | Mảng {name, type, unit, required, is\_key, locator, canonical\_attr, value\_constraints}; canonical\_attr trỏ tới thuộc tính chuẩn của lớp ngữ cảnh hoặc gói ngành; value\_constraints là miền hợp lệ {min, max, unit, scale, max\_change\_pp, label\_terms}, bắt buộc min, max, unit với trường number, percent, money khi active (BR-168). Ví dụ lãi suất 12 tháng: {min: 0, max: 15, unit: “%/năm”, max\_change\_pp: 1,5, label\_terms: \[“tại quầy”\]} |
| template\_fingerprint | jsonb | Dấu vân tay mẫu: cấu trúc DOM, tập khoá JSON, tiêu đề cột; dùng phát hiện thay đổi (UC-14-03) |
| value\_rules | jsonb, bắt buộc, mặc định '\[\]' | Quan hệ nội tại giữa trường, chỉ cảnh báo, ví dụ {rule: gte, left: rate\_24m, right: rate\_12m, tolerance\_pp: 0.3} (BR-168) |
| sample\_page\_ids | uuid\[\], bắt buộc, mặc định '{}' | logic → crawl\_page; mẫu giữ lại để chạy thử (tối thiểu 20 trang hoặc toàn bộ nếu ít hơn) |
| test\_required\_accuracy | numeric(5,4) | Tỷ lệ trường bắt buộc trích đúng khi chạy thử; từ 0,95 mới được duyệt (BR-151) |
| parent\_version\_id | uuid | FK crawl\_schema\_version; phiên bản gốc khi ánh xạ lại hoặc hoàn tác |
| inferred\_by\_run\_id | uuid | logic → agent.agent\_run; lượt suy ngược của Agent Thu thập |
| approved\_by | uuid | logic → org.app\_user |
| approved\_at | timestamptz |  |

Cột chung: id, org\_id, created\_at, created\_by. Ràng buộc: UNIQUE (source\_id, version); CHECK (status IN ('draft','testing','active','superseded','rejected')); CHECK (status NOT IN ('active','superseded') OR (approved\_by IS NOT NULL AND approved\_at IS NOT NULL)); CHECK (status <> 'active' OR test\_required\_accuracy >= 0.95); FOREIGN KEY (source\_id) REFERENCES acquire.crawl\_source(id); FOREIGN KEY (parent\_version\_id) REFERENCES acquire.crawl\_schema\_version(id). Chỉ mục: duy nhất (source\_id) WHERE status = 'active'. Bổ sung 10/10/2026 (BR-168): CHECK (jsonb\_typeof(value\_rules) = 'array'); CHECK (acquire.value\_constraints\_ok(fields, false)): value\_constraints có thì là object và min ≤ max; CHECK (status <> 'active' OR acquire.value\_constraints\_ok(fields, true)): mọi trường number, percent, money khai min, max, unit.

#### acquire.crawl\_run

Một lượt thu của một nguồn: tải, trích, duyệt, phát hành sang lớp hợp nhất. Thay KBSourceRun của code hiện tại. Use case: UC-14-03, UC-14-04, UC-14-06.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| source\_id | uuid, bắt buộc | FK crawl\_source |
| schema\_version\_id | uuid | FK crawl\_schema\_version dùng để trích |
| trigger | text, bắt buộc | schedule, manual, retry |
| status | text, bắt buộc, mặc định 'queued' | queued, fetching, extracting, in\_review, published, quarantined, failed |
| pages\_fetched | integer, bắt buộc, mặc định 0 |  |
| pages\_changed | integer, bắt buộc, mặc định 0 | Trang có SHA-256 khác lượt trước |
| records\_extracted | integer, bắt buộc, mặc định 0 |  |
| records\_flagged | integer, bắt buộc, mặc định 0 | Bản ghi vào hàng duyệt |
| sample\_accuracy | numeric(5,4) | Độ chính xác trên mẫu ngẫu nhiên 2% (UC-14-04) |
| drift\_score | numeric(5,4) | Điểm lệch cấu trúc so với phiên bản active (UC-14-03) |
| cost | numeric(12,4) | Chi phí mô hình của lượt |
| agent\_run\_id | uuid | logic → agent.agent\_run |
| started\_at | timestamptz |  |
| finished\_at | timestamptz |  |

Cột chung: id, org\_id, created\_at. Ràng buộc: CHECK (trigger IN ('schedule','manual','retry')); CHECK (status IN ('queued','fetching','extracting','in\_review','published','quarantined','failed')); CHECK (status <> 'published' OR schema\_version\_id IS NOT NULL); CHECK (sample\_accuracy IS NULL OR sample\_accuracy BETWEEN 0 AND 1); FOREIGN KEY (source\_id) REFERENCES acquire.crawl\_source(id); FOREIGN KEY (schema\_version\_id) REFERENCES acquire.crawl\_schema\_version(id). Chỉ mục: (source\_id, started\_at DESC); duy nhất (source\_id) WHERE status IN ('queued','fetching','extracting'), để không chạy chồng hai lượt. Trigger: chuyển published chỉ khi phiên bản schema đang active hoặc superseded (BR-152) và lượt không còn bản ghi pending, flagged (BR-153).

#### acquire.crawl\_page

Một trang, tệp hoặc phản hồi API đã tải; bản gốc giữ nguyên trong MinIO có object lock, mọi bản ghi trích truy về đây (BR-150). Thay KBSourceCrawlQueue của code hiện tại. Use case: UC-14-02, UC-14-03, UC-14-06.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| run\_id | uuid, bắt buộc | FK crawl\_run |
| source\_id | uuid, bắt buộc | FK crawl\_source |
| url | text, bắt buộc |  |
| fetched\_at | timestamptz, bắt buộc |  |
| http\_status | smallint |  |
| content\_type | text | Ví dụ text/html, application/json, application/pdf |
| sha256 | text, bắt buộc | Mã băm bản gốc (BR-150) |
| object\_key | text | MinIO da-raw-crawl (object lock, giữ 400 ngày); null khi tải lỗi |
| byte\_size | bigint |  |
| changed | boolean, bắt buộc, mặc định true | Khác bản gốc lượt trước |
| fingerprint | jsonb | Dấu vân tay cấu trúc của trang |
| retain\_until | timestamptz | Hạn giữ bản gốc; quá hạn thì không trích lại được (UC-14-03) |

Cột chung: id, org\_id, created\_at. Bản ghi bất biến. Ràng buộc: CHECK (http\_status IS NULL OR http\_status BETWEEN 100 AND 599); CHECK (sha256 \~ '^\[0-9a-f\]{64}$'); FOREIGN KEY (run\_id) REFERENCES acquire.crawl\_run(id); FOREIGN KEY (source\_id) REFERENCES acquire.crawl\_source(id). Chỉ mục: (run\_id); (source\_id, url, fetched\_at DESC); (retain\_until).

#### acquire.extracted\_record

Bản ghi trích từ một trang gốc theo một phiên bản schema, kèm độ tin từng trường; chỉ bản ghi approved hoặc corrected của lượt đã phát hành mới sang lớp hợp nhất (BR-153). Khi hoàn tác schema, bản ghi trích bằng phiên bản lỗi chuyển superseded. Use case: UC-14-03, UC-14-04, UC-14-05.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| run\_id | uuid, bắt buộc | FK crawl\_run |
| page\_id | uuid, bắt buộc | FK crawl\_page; truy về bản gốc (BR-150) |
| schema\_version\_id | uuid, bắt buộc | FK crawl\_schema\_version |
| entity\_key | text, bắt buộc | Khoá thực thể như nguồn ghi, ví dụ tên ngân hàng trên trang |
| period | text, bắt buộc | Kỳ của giá trị, ví dụ 2026-10-10 hoặc 2026-W41 |
| values | jsonb, bắt buộc | Giá trị theo tên trường |
| field\_confidence | jsonb, bắt buộc, mặc định '{}' | Độ tin từng trường, 0 đến 1 |
| record\_confidence | numeric(5,4), bắt buộc | Dưới 0,85 thì vào hàng duyệt (UC-14-04) |
| status | text, bắt buộc, mặc định 'pending' | pending, flagged, approved, corrected, rejected, superseded |
| review\_reasons | text\[\], bắt buộc, mặc định '{}' | low\_confidence, missing\_required, outlier, new\_category, entity\_uncertain, random\_sample, out\_of\_domain, relation\_warning, semantic\_shift, label\_changed; relation\_warning chỉ cảnh báo, không chặn (BR-168) |
| is\_random\_sample | boolean, bắt buộc, mặc định false | Mẫu ngẫu nhiên 2%, tối thiểu 5 bản ghi mỗi lượt |

Cột chung: id, org\_id, created\_at, updated\_at, updated\_by. Ràng buộc: CHECK (status IN ('pending','flagged','approved','corrected','rejected','superseded')); CHECK (record\_confidence BETWEEN 0 AND 1); CHECK (review\_reasons <@ ARRAY\['low\_confidence','missing\_required','outlier','new\_category','entity\_uncertain','random\_sample','out\_of\_domain','relation\_warning','semantic\_shift','label\_changed'\]); CHECK (status <> 'flagged' OR cardinality(review\_reasons) > 0); FOREIGN KEY (run\_id) REFERENCES acquire.crawl\_run(id); FOREIGN KEY (page\_id) REFERENCES acquire.crawl\_page(id); FOREIGN KEY (schema\_version\_id) REFERENCES acquire.crawl\_schema\_version(id). Chỉ mục: (run\_id, status); (schema\_version\_id) WHERE status <> 'superseded'; (entity\_key, period).

#### acquire.review\_decision

Quyết định của người duyệt trên một bản ghi trích; bản ghi người sửa được giữ làm ví dụ cho bộ trích (BR-153). Use case: UC-14-04.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| record\_id | uuid, bắt buộc | FK extracted\_record |
| decision | text, bắt buộc | confirm, correct, reject |
| corrected\_values | jsonb | Giá trị sau sửa; bắt buộc khi correct |
| reason | text | Bắt buộc khi reject |
| decided\_by | uuid, bắt buộc | logic → org.app\_user |
| decided\_at | timestamptz, bắt buộc, mặc định now() |  |
| used\_as\_example | boolean, bắt buộc, mặc định false | Đã đưa vào ví dụ cho bộ trích |

Cột chung: id, org\_id, created\_at. Ràng buộc: CHECK (decision IN ('confirm','correct','reject')); CHECK (decision <> 'correct' OR corrected\_values IS NOT NULL); CHECK (decision <> 'reject' OR reason IS NOT NULL); FOREIGN KEY (record\_id) REFERENCES acquire.extracted\_record(id). Chỉ mục: (record\_id, decided\_at DESC); (decided\_by, decided\_at).

#### acquire.schema\_drift

Thay đổi cấu trúc nguồn phát hiện ở một lượt thu và cách xử lý: nhẹ thì agent ánh xạ lại thành phiên bản nháp, nặng thì tạm ngưng nguồn, cách ly dữ liệu lượt và tạo yêu cầu Cần bạn (BR-152). Use case: UC-14-03.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| source\_id | uuid, bắt buộc | FK crawl\_source |
| run\_id | uuid, bắt buộc | FK crawl\_run phát hiện thay đổi |
| from\_version\_id | uuid, bắt buộc | FK crawl\_schema\_version đang active khi phát hiện |
| drift\_kind | text, bắt buộc, mặc định 'structural' | structural, semantic; semantic là lệch ngầm: cấu trúc giữ nguyên nhưng nghĩa giá trị đổi (BR-168) |
| severity | text, bắt buộc | minor, major; major khi mất trường khoá, tỷ lệ trích bắt buộc dưới 80% hoặc đổi định dạng |
| signals | jsonb, bắt buộc, mặc định '{}' | Điểm lệch, trường mất, tỷ lệ trích bắt buộc, đổi định dạng |
| action | text, bắt buộc | auto\_remap, revert, paused, quarantine; quarantine chỉ dùng cho semantic: cách ly lượt, không tự hoàn tác |
| proposed\_version\_id | uuid | FK crawl\_schema\_version agent đề xuất |
| resolution | text | approved\_new, manual\_fix, reverted, false\_alarm; null khi còn mở; false\_alarm chỉ cho semantic; semantic đóng phải có resolved\_by |
| resolved\_by | uuid | logic → org.app\_user |
| resolved\_at | timestamptz |  |

Cột chung: id, org\_id, created\_at. Ràng buộc: CHECK (severity IN ('minor','major')); CHECK (action IN ('auto\_remap','revert','paused')); CHECK ((severity = 'minor') = (action = 'auto\_remap')); CHECK (resolution IS NULL OR resolution IN ('approved\_new','manual\_fix','reverted')); CHECK ((resolution IS NULL) = (resolved\_at IS NULL)); FOREIGN KEY (source\_id) REFERENCES acquire.crawl\_source(id); FOREIGN KEY (run\_id) REFERENCES acquire.crawl\_run(id); FOREIGN KEY (from\_version\_id) REFERENCES acquire.crawl\_schema\_version(id); FOREIGN KEY (proposed\_version\_id) REFERENCES acquire.crawl\_schema\_version(id). Chỉ mục: (source\_id) WHERE resolution IS NULL. Bổ sung 10/10/2026 (BR-168): action thêm 'quarantine', resolution thêm 'false\_alarm'; CHECK (drift\_kind IN ('structural','semantic')); CHECK ((drift\_kind = 'semantic') = (action = 'quarantine')); CHECK (drift\_kind <> 'semantic' OR severity = 'major'); CHECK (drift\_kind <> 'semantic' OR (signals ? 'affected\_fields' AND signals ? 'evidence')); CHECK (resolution IS DISTINCT FROM 'false\_alarm' OR drift\_kind = 'semantic'); CHECK (drift\_kind <> 'semantic' OR resolution IS NULL OR resolved\_by IS NOT NULL). Với semantic, signals ghi affected\_fields, direction, share\_shifted, median\_ratio, label\_changes, cause\_guess. Trigger crawl\_run\_publish\_guard thêm: nguồn còn schema\_drift semantic chưa đóng thì không phát hành lượt nào của nguồn.

#### acquire.entity\_link

Liên kết khoá thực thể của một nguồn với khoá chuẩn, ví dụ tên viết tắt và tên đầy đủ của một ngân hàng cùng về một mã chuẩn; không chắc thì hỏi người. Use case: UC-14-05.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| data\_space\_id | uuid, bắt buộc | logic → org.data\_space |
| entity\_type | text, bắt buộc | Loại thực thể, ví dụ bank |
| canonical\_key | text, bắt buộc | Khoá chuẩn, ví dụ SBV:\<mã ngân hàng do NHNN cấp>; chép từ reference\_entity khi có (BR-169) |
| reference\_entity\_id | uuid | FK reference\_entity; bắt buộc khi confirmed (BR-169) |
| source\_id | uuid, bắt buộc | FK crawl\_source |
| source\_key | text, bắt buộc | Khoá như nguồn ghi |
| match\_score | numeric(5,4) | 0 đến 1 |
| method | text, bắt buộc | identifier, exact, fuzzy, human; fuzzy chỉ tự xác nhận khi match\_score ≥ 0,95, dưới đó người duyệt (BR-169) |
| status | text, bắt buộc, mặc định 'pending' | pending, confirmed, rejected |
| decided\_by | uuid | logic → org.app\_user |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: CHECK (method IN ('exact','fuzzy','human')); CHECK (status IN ('pending','confirmed','rejected')); CHECK (match\_score IS NULL OR match\_score BETWEEN 0 AND 1); CHECK (method <> 'human' OR decided\_by IS NOT NULL); FOREIGN KEY (source\_id) REFERENCES acquire.crawl\_source(id). Chỉ mục: duy nhất (source\_id, entity\_type, source\_key) WHERE status <> 'rejected'; (data\_space\_id, entity\_type, canonical\_key); (data\_space\_id) WHERE status = 'pending'. Bổ sung 10/10/2026 (BR-169): method thêm 'identifier'; FOREIGN KEY (reference\_entity\_id) REFERENCES acquire.reference\_entity(id); CHECK (status <> 'confirmed' OR reference\_entity\_id IS NOT NULL); CHECK (method <> 'identifier' OR reference\_entity\_id IS NOT NULL); CHECK (NOT (method = 'fuzzy' AND status = 'confirmed') OR match\_score >= 0.95). Trigger entity\_link\_reference\_sync: có reference\_entity\_id thì canonical\_key chép từ danh mục, entity\_type phải trùng loại của thực thể chuẩn.

#### acquire.reference\_entity

Danh mục thực thể chuẩn của tổ chức: ngân hàng, tổ chức tín dụng, chi nhánh ngân hàng nước ngoài, cơ quan, doanh nghiệp. Khoá là mã định danh gốc (mã ngân hàng do NHNN cấp, SWIFT/BIC, mã số thuế); tên, tên viết tắt và tên miền chính thức chỉ là thuộc tính để so khớp dự phòng (BR-169). Dùng chung cho mọi Data Space của tổ chức; nạp từ danh mục NHNN, danh bạ SWIFT, tra cứu mã số thuế hoặc gói ngành. pack.canonical\_entity và context.semantic\_entity mô tả loại thực thể, không giữ từng thực thể, nên không dùng lại được cho việc này. Use case: UC-14-05.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| entity\_type | text, bắt buộc | bank, credit\_institution, foreign\_bank\_branch, gov\_agency, enterprise |
| canonical\_key | text, bắt buộc | SBV:\<mã NHNN>, nếu không có thì BIC:<8 ký tự đầu>, rồi MST:\<mã số thuế>; đặt một lần, không đổi |
| legal\_name | text, bắt buộc | Tên pháp lý, ví dụ Ngân hàng TMCP A |
| short\_names | text\[\], bắt buộc, mặc định '{}' | Tên viết tắt, tên thương mại, tên cũ đã duyệt |
| sbv\_code | text | Mã ngân hàng do NHNN cấp, 8 chữ số |
| swift\_bic | text | 8 hoặc 11 ký tự theo ISO 9362 |
| tax\_code | text | Mã số thuế 10 số, hoặc 13 số dạng 10-3 cho đơn vị phụ thuộc |
| official\_domains | text\[\], bắt buộc, mặc định '{}' | Tên miền chính thức, dùng khớp trang nguồn |
| registry\_source | text, bắt buộc | sbv\_registry, swift\_directory, tax\_registry, pack, manual |
| registry\_ref | text | Số văn bản, ngày trích danh mục gốc |
| registry\_checked\_at | timestamptz | Lần đối chiếu danh mục gốc gần nhất |
| status | text, bắt buộc, mặc định 'active' | active, merged, dissolved; đổi tên vẫn active, tên cũ vào short\_names |
| successor\_id | uuid | FK reference\_entity; bắt buộc khi merged |
| valid\_from | date | Ngày bắt đầu hiệu lực |
| valid\_to | date | Ngày hết hiệu lực (sáp nhập, giải thể) |
| verified\_by | uuid | logic → org.app\_user; bắt buộc khi registry\_source = manual |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: CHECK (entity\_type IN ('bank','credit\_institution','foreign\_bank\_branch','gov\_agency','enterprise')); CHECK (registry\_source IN ('sbv\_registry','swift\_directory','tax\_registry','pack','manual')); CHECK (status IN ('active','merged','dissolved')); `CHECK (canonical_key ~ '^(SBV|BIC|MST):[0-9A-Z-]+$')`; `CHECK (sbv_code IS NULL OR sbv_code ~ '^[0-9]{8}$')`; `CHECK (swift_bic IS NULL OR swift_bic ~ '^[A-Z]{6}[A-Z0-9]{2}([A-Z0-9]{3})?$')`; `CHECK (tax_code IS NULL OR tax_code ~ '^[0-9]{10}(-[0-9]{3})?$')`; CHECK (sbv\_code IS NOT NULL OR swift\_bic IS NOT NULL OR tax\_code IS NOT NULL); CHECK (registry\_source <> 'manual' OR verified\_by IS NOT NULL); CHECK ((status = 'merged') = (successor\_id IS NOT NULL) AND (successor\_id IS NULL OR successor\_id <> id)); CHECK (valid\_to IS NULL OR valid\_from IS NULL OR valid\_to >= valid\_from); UNIQUE (org\_id, entity\_type, canonical\_key). Chỉ mục: duy nhất (org\_id, sbv\_code), (org\_id, swift\_bic), (org\_id, tax\_code) khi khác null; GIN trên official\_domains và short\_names. Chưa có API quản lý danh mục (điểm mở, tab 6 O4.4).

#### acquire.consolidation\_spec

Đặc tả hợp nhất cho một loại thực thể: nguồn, chuẩn hoá, ưu tiên nguồn, bảng tổng hợp, mẫu đoạn tóm tắt và dataset đích. Use case: UC-14-05.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| data\_space\_id | uuid, bắt buộc | logic → org.data\_space |
| name | text, bắt buộc |  |
| entity\_type | text, bắt buộc |  |
| grain | text, bắt buộc | Khoá thực thể × kỳ, ví dụ bank × day |
| source\_ids | uuid\[\], bắt buộc | logic → crawl\_source |
| normalization | jsonb, bắt buộc, mặc định '{}' | Đơn vị, tiền tệ, kỳ, múi giờ, bảng mã, văn bản |
| source\_priority | jsonb, bắt buộc, mặc định '\[\]' | Thứ tự ưu tiên nguồn khi lệch |
| aggregates | jsonb, bắt buộc, mặc định '\[\]' | Bảng tổng hợp theo chiều: trung bình, trung vị, nhỏ nhất, lớn nhất, số nguồn theo ngày, tuần, tháng |
| summary\_template | text | Mẫu đoạn tóm tắt sinh tự động có trích nguồn |
| allow\_interpolation | boolean, bắt buộc, mặc định false | Thiếu nguồn cho một kỳ thì đánh dấu thiếu, chỉ nội suy khi bật |
| target\_dataset\_id | uuid | logic → catalog.dataset (origin = 'consolidated') |
| owner\_id | uuid, bắt buộc | logic → org.app\_user |
| status | text, bắt buộc, mặc định 'draft' | draft, active, retired |
| last\_edited\_by | uuid, bắt buộc | logic → org.app\_user; người sửa nội dung đặc tả gần nhất, trigger ghi; thành created\_by của phiên bản dataset phát hành (BR-156) |
| last\_edited\_at | timestamptz, bắt buộc, mặc định now() | Lúc sửa nội dung đặc tả gần nhất |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: UNIQUE (data\_space\_id, name); CHECK (status IN ('draft','active','retired')); CHECK (cardinality(source\_ids) > 0); CHECK (status <> 'active' OR target\_dataset\_id IS NOT NULL). Trigger (BR-156): khi INSERT, last\_edited\_by = created\_by; khi UPDATE đổi một trong entity\_type, grain, source\_ids, normalization, source\_priority, aggregates, summary\_template, allow\_interpolation, target\_dataset\_id thì last\_edited\_by = updated\_by, last\_edited\_at = now(); mọi UPDATE khác giữ giá trị cũ, kể cả khi ghi thẳng vào hai cột này. Không dùng updated\_by làm người tạo phiên bản vì cột này cũng đổi khi kích hoạt, ngừng, đổi tên, đổi chủ đặc tả và khi tác vụ hệ thống ghi.

#### acquire.consolidation\_run

Một lượt hợp nhất: chuẩn hoá, nhận diện thực thể, khử trùng lặp, đối chiếu xung đột, phát hành dataset phiên bản mới; chỉ bảng tổng hợp, khẳng định và đoạn tóm tắt được nhúng vector (BR-154, BR-155). Use case: UC-14-05.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| spec\_id | uuid, bắt buộc | FK consolidation\_spec |
| input\_run\_ids | uuid\[\], bắt buộc | logic → crawl\_run đã published |
| status | text, bắt buộc, mặc định 'running' | running, published, failed |
| records\_in | integer, bắt buộc, mặc định 0 |  |
| records\_out | integer, bắt buộc, mặc định 0 |  |
| conflicts\_opened | integer, bắt buộc, mặc định 0 | Số vụ mở ở conflict.conflict (BR-155) |
| dataset\_version\_id | uuid | logic → catalog.dataset\_version phát hành |
| claims\_created | integer, bắt buộc, mặc định 0 | Số khẳng định ghi vào conflict.claim |
| chunks\_embedded | integer, bắt buộc, mặc định 0 | Số điểm vector Qdrant từ bảng tổng hợp, khẳng định, đoạn tóm tắt (BR-154) |
| started\_at | timestamptz, bắt buộc |  |
| finished\_at | timestamptz |  |
| spec\_editor\_id | uuid, bắt buộc | logic → org.app\_user; chép consolidation\_spec.last\_edited\_by lúc bắt đầu lượt, trigger ghi; Agent Hợp nhất ghi giá trị này vào catalog.dataset\_version.created\_by khi phát hành (BR-156) |

Cột chung: id, org\_id, created\_at. Ràng buộc: CHECK (status IN ('running','published','failed')); CHECK (status <> 'published' OR dataset\_version\_id IS NOT NULL); CHECK (cardinality(input\_run\_ids) > 0); FOREIGN KEY (spec\_id) REFERENCES acquire.consolidation\_spec(id). Chỉ mục: (spec\_id, started\_at DESC); duy nhất (spec\_id) WHERE status = 'running'. Trigger: mọi lượt trong input\_run\_ids phải ở published (BR-153); spec\_editor\_id luôn lấy từ đặc tả khi INSERT, giá trị gửi lên bị ghi đè và không sửa được (BR-156).

### D4.23 Schema forecast

#### forecast.forecast\_job

Việc dự báo bất đồng bộ tạo từ câu hỏi hoặc lịch; chạy như lượt agent nền, xong thì đẩy kết quả vào đúng hội thoại (BR-159). Use case: UC-15-01, UC-15-02.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| data\_space\_id | uuid, bắt buộc | logic → org.data\_space |
| question\_id | uuid | logic → qa.question; null khi chạy theo lịch |
| requested\_by | uuid, bắt buộc | logic → org.app\_user |
| metric\_code | text, bắt buộc | logic → context.metric |
| scope | jsonb, bắt buộc, mặc định '{}' | Phạm vi theo chiều, ví dụ khối bán lẻ |
| assumptions | jsonb, bắt buộc, mặc định '\[\]' | Giả định người hỏi đặt cho biến ngoại sinh, mảng {feature, from\_period, change\_kind, value} như scenario.assumptions; trả kèm kết quả (UC-15-01) |
| frequency | text, bắt buộc | day, week, month, quarter |
| horizon | integer, bắt buộc | Số kỳ dự báo, ví dụ 3 tháng |
| cutoff\_at | timestamptz, bắt buộc | Thời điểm cắt; chỉ dùng dữ liệu ghi nhận trước đó (BR-157) |
| status | text, bắt buộc, mặc định 'queued' | queued, preparing, waiting\_human, training, backtesting, ready, failed, cancelled |
| eta\_at | timestamptz | Thời gian dự kiến báo cho người hỏi |
| notify\_channels | text\[\], bắt buộc, mặc định '{}' | Kênh người dùng chọn để báo khi xong, ngoài thông báo trong ứng dụng (UC-15-01) |
| progress | jsonb, bắt buộc, mặc định '{}' | Bước đang làm, hiện ở màn Đang làm gì |
| model\_version\_id | uuid | FK model\_version dùng cho kết quả |
| agent\_run\_id | uuid | logic → agent.agent\_run |
| finished\_at | timestamptz |  |

Cột chung: id, org\_id, created\_at, created\_by. Ràng buộc: CHECK (frequency IN ('day','week','month','quarter')); CHECK (horizon > 0); CHECK (status IN ('queued','preparing','waiting\_human','training','backtesting','ready','failed','cancelled')); CHECK (status <> 'ready' OR model\_version\_id IS NOT NULL); FOREIGN KEY (model\_version\_id) REFERENCES forecast.model\_version(id). Chỉ mục: (question\_id); (status, eta\_at) WHERE status NOT IN ('ready','failed','cancelled'); (data\_space\_id, metric\_code, created\_at DESC).

#### forecast.forecast\_model

Mô hình dự báo cho một chỉ số, phạm vi, tần suất; trỏ tới phiên bản đang phát hành. Use case: UC-15-01, UC-15-04, UC-15-05, UC-15-06.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| data\_space\_id | uuid, bắt buộc | logic → org.data\_space |
| metric\_code | text, bắt buộc | logic → context.metric |
| scope | jsonb, bắt buộc, mặc định '{}' |  |
| frequency | text, bắt buộc | day, week, month, quarter |
| owner\_id | uuid, bắt buộc | logic → org.app\_user; người phụ trách chỉ số, duyệt phiên bản |
| status | text, bắt buộc, mặc định 'active' | active, degraded, retired; degraded theo BR-160 |
| current\_version\_id | uuid | FK model\_version đang phát hành |
| retrain\_cron | text | Mặc định mỗi kỳ dữ liệu mới với chuỗi tháng, mỗi tuần với chuỗi ngày |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: CHECK (frequency IN ('day','week','month','quarter')); CHECK (status IN ('active','degraded','retired')); UNIQUE (data\_space\_id, metric\_code, frequency, scope); FOREIGN KEY (current\_version\_id) REFERENCES forecast.model\_version(id) DEFERRABLE INITIALLY DEFERRED. Trigger ràng buộc, kiểm cuối giao dịch: current\_version\_id là phiên bản published hoặc degraded của chính mô hình; status = 'degraded' khi và chỉ khi phiên bản đó degraded (BR-160).

#### forecast.model\_version

Phiên bản mô hình: thuật toán, biến và giả định, dữ liệu huấn luyện, thẻ mô hình; chỉ phát hành khi MASE < 1 trên backtest và người phụ trách chỉ số duyệt, người tạo không tự duyệt (BR-158). Use case: UC-15-02, UC-15-03, UC-15-04, UC-15-06.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| model\_id | uuid, bắt buộc | FK forecast\_model |
| version | integer, bắt buộc |  |
| algorithm | text, bắt buộc | naive, seasonal\_naive, ets, theta, arima, sarimax, lightgbm, ensemble, formula\_mc (dự báo theo công thức và Monte Carlo, G8.9) |
| ensemble\_of | jsonb | Mô hình thành phần khi ensemble trung bình 3 mô hình đầu |
| features | jsonb, bắt buộc, mặc định '\[\]' | Mảng {name, source, lag, transform, future\_assumption}; source là chuỗi hợp nhất P14, skill.external\_observation, lịch hoặc sự kiện |
| params | jsonb, bắt buộc, mặc định '{}' |  |
| training\_from | date, bắt buộc |  |
| training\_to | date, bắt buộc |  |
| data\_snapshot\_refs | jsonb, bắt buộc, mặc định '\[\]' | dataset\_version, query\_run dùng để huấn luyện |
| n\_points | integer | Số điểm chuỗi huấn luyện, từ 24; bắt buộc khi phát hành (BR-172) |
| n\_exog | smallint, bắt buộc, mặc định 0 | Số biến ngoại sinh; tối đa min(8, n\_points / 12) (BR-172) |
| pooled\_series | smallint, bắt buộc, mặc định 1 | Số chuỗi cùng loại huấn luyện chung; LightGBM cần n\_points ≥ 60, hoặc ≥ 36 khi pooled\_series ≥ 5 (BR-172) |
| backtest\_id | uuid | FK backtest\_run |
| status | text, bắt buộc, mặc định 'draft' | draft, pending\_approval, published, degraded, retired, rejected |
| model\_card | jsonb, bắt buộc, mặc định '{}' | Mục tiêu, phạm vi, dữ liệu, biến và giả định, thuật toán, kết quả backtest, giới hạn đã biết (UC-15-04) |
| artifact\_key | text | MinIO da-forecast-models |
| approved\_by | uuid | logic → org.app\_user |
| approved\_at | timestamptz |  |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: UNIQUE (model\_id, version); CHECK (algorithm IN ('naive','seasonal\_naive','ets','theta','arima','sarimax','lightgbm','ensemble','formula\_mc')); CHECK (status IN ('draft','pending\_approval','published','degraded','retired','rejected')); CHECK (training\_to > training\_from); CHECK (algorithm <> 'ensemble' OR ensemble\_of IS NOT NULL); CHECK (status NOT IN ('published','degraded') OR (approved\_by IS NOT NULL AND approved\_at IS NOT NULL AND backtest\_id IS NOT NULL)); CHECK (approved\_by IS NULL OR approved\_by <> created\_by); FOREIGN KEY (model\_id) REFERENCES forecast.forecast\_model(id); FOREIGN KEY (backtest\_id) REFERENCES forecast.backtest\_run(id) DEFERRABLE INITIALLY DEFERRED. Chỉ mục: duy nhất (model\_id) WHERE status IN ('published','degraded'). Trigger: phiên bản mới ở draft hoặc pending\_approval; chỉ chuyển draft → pending\_approval, rejected; pending\_approval → published, rejected, draft; published → degraded, retired; degraded → retired (BR-160). Chuyển published cần backtest\_run đã xong của chính phiên bản với mase < 1 (BR-158).

Bổ sung 10/10/2026 (BR-172): CHECK (n\_points IS NULL OR n\_points >= 24); CHECK (pooled\_series >= 1); CHECK (algorithm <> 'lightgbm' OR coalesce(n\_points, 0) >= 60 OR (coalesce(n\_points, 0) >= 36 AND pooled\_series >= 5)); CHECK (n\_exog >= 0 AND n\_exog <= least(8, coalesce(n\_points, 0) / 12)). Trigger model\_version\_guard thêm khi chuyển published: n\_points và backtest\_run.in\_sample\_mase phải có; mase / in\_sample\_mase > 2 thì chặn (quá khớp). Thành phần LightGBM trong ensemble do Forecast service kiểm cùng quy tắc.

Bổ sung 10/10/2026 (BR-183, ADR-44): algorithm thêm 'formula\_mc'. Trigger model\_version\_guard thêm: phiên bản formula\_mc chỉ chuyển pending\_approval khi có đúng một dòng forecast.formula\_model trỏ tới nó; backtest\_run của phiên bản này là backtest cuốn chiếu trên các kỳ đã công bố (frequency = week, từ 20 điểm cắt) so với naive “giữ giá kỳ trước”, metrics thêm reconstruction\_mae; điều kiện phát hành BR-158 giữ nguyên.

#### forecast.backtest\_run

Backtest cuốn chiếu của một phiên bản: tối thiểu 20 điểm cắt với chuỗi ngày, tuần và 6 điểm cắt với chuỗi tháng, quý (ADR-15), mỗi điểm dự báo đủ tầm, so với mô hình chuẩn seasonal naive. Use case: UC-15-03, UC-15-04.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| model\_version\_id | uuid, bắt buộc | FK model\_version |
| frequency | text, bắt buộc | day, week, month, quarter; chép từ forecast\_model khi tạo; quyết định số điểm cắt tối thiểu (ADR-15) |
| cutoffs | date\[\], bắt buộc | Các điểm cắt |
| horizon | integer, bắt buộc |  |
| metrics | jsonb, bắt buộc | {mase, smape, coverage80, coverage95, n\_errors, interval\_method} theo từng bước tầm; n\_errors là số sai số dùng cho khoảng, kể cả sai số gộp từ chuỗi con (G8.5) |
| baseline\_metrics | jsonb, bắt buộc | Cùng chỉ số của seasonal naive |
| mase | numeric(8,4), bắt buộc | MASE tổng hợp trên mọi điểm cắt và tầm; phiên bản chỉ phát hành khi < 1 (BR-158) |
| in\_sample\_mase | numeric(8,4) | MASE trong mẫu của mô hình được chọn; mase / in\_sample\_mase > 2 là quá khớp, không phát hành (BR-172). CHECK (in\_sample\_mase IS NULL OR in\_sample\_mase > 0) |
| candidates | jsonb, bắt buộc, mặc định '\[\]' | Kết quả mọi mô hình ứng viên; mô hình MASE ≥ 1 bị loại |
| started\_at | timestamptz, bắt buộc |  |
| finished\_at | timestamptz |  |

Cột chung: id, org\_id, created\_at. Ràng buộc: CHECK (frequency IN ('day','week','month','quarter')); CHECK (cardinality(cutoffs) >= CASE WHEN frequency IN ('day','week') THEN 20 ELSE 6 END); CHECK (mase >= 0); CHECK (in\_sample\_mase IS NULL OR in\_sample\_mase > 0); CHECK (horizon > 0); FOREIGN KEY (model\_version\_id) REFERENCES forecast.model\_version(id). Chỉ mục: (model\_version\_id). Trigger: frequency lấy từ forecast\_model của phiên bản; bỏ trống thì điền, khác thì chặn (ADR-15).

#### forecast.prediction

Giá trị dự báo một kỳ kèm khoảng 80% và 95% và đóng góp biến; kết quả của mô hình chưa duyệt gắn is\_experimental, không dùng cho báo cáo, cảnh báo (BR-158). Use case: UC-15-01, UC-15-05.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| model\_version\_id | uuid, bắt buộc | FK model\_version |
| job\_id | uuid | FK forecast\_job |
| scenario\_id | uuid | FK scenario; null là kịch bản gốc |
| cutoff\_at | timestamptz, bắt buộc |  |
| period | date, bắt buộc | Ngày đầu kỳ dự báo |
| point | numeric, bắt buộc |  |
| lo80 | numeric, bắt buộc | Cận dưới khoảng 80%, cách tính theo interval\_method |
| hi80 | numeric, bắt buộc |  |
| lo95 | numeric, bắt buộc | Cận dưới khoảng 95%, cách tính theo interval\_method |
| hi95 | numeric, bắt buộc |  |
| interval\_method | text, bắt buộc, mặc định 'conformal' | conformal (từ 20 sai số của chính chuỗi ở bước tầm này), conformal\_pooled (gộp sai số chuẩn hoá của các chuỗi con cùng chỉ số), parametric (khoảng tham số, hiện nhãn “khoảng tham số”) (G8.5, ADR-15), monte\_carlo (phân vị của các lần mô phỏng, dự báo theo công thức G8.9, ADR-44) |
| exog\_uncertainty | text, bắt buộc, mặc định 'none' | none, simulated, scenario\_fixed; dự báo gốc có biến ngoại sinh phải là simulated, kịch bản là scenario\_fixed (BR-173) |
| contributions | jsonb, bắt buộc, mặc định '{}' | Đóng góp từng biến: hệ số SARIMAX hoặc SHAP |
| is\_experimental | boolean, bắt buộc, mặc định false | Nhãn “thử nghiệm, chưa duyệt” (UC-15-01) |

Cột chung: id, org\_id, created\_at. Bản ghi bất biến. Ràng buộc: CHECK (interval\_method IN ('conformal','conformal\_pooled','parametric','monte\_carlo')); CHECK (lo95 <= lo80 AND lo80 <= point AND point <= hi80 AND hi80 <= hi95); UNIQUE NULLS NOT DISTINCT (model\_version\_id, scenario\_id, cutoff\_at, period); FOREIGN KEY (model\_version\_id) REFERENCES forecast.model\_version(id); FOREIGN KEY (job\_id) REFERENCES forecast.forecast\_job(id); FOREIGN KEY (scenario\_id) REFERENCES forecast.scenario(id). Chỉ mục: (job\_id); (model\_version\_id, cutoff\_at DESC). Trigger: dự báo từ phiên bản chưa published hoặc degraded phải có is\_experimental = true (BR-158).

Bổ sung 10/10/2026 (BR-173): CHECK (exog\_uncertainty IN ('none','simulated','scenario\_fixed')); CHECK ((scenario\_id IS NOT NULL) = (exog\_uncertainty = 'scenario\_fixed')). Trigger prediction\_exog\_guard: phiên bản có n\_exog > 0 thì dự báo gốc (scenario\_id null) phải ghi simulated.

Bổ sung 10/10/2026 (BR-184, ADR-44): interval\_method thêm 'monte\_carlo'; dự báo của phiên bản formula\_mc ghi interval\_method = 'monte\_carlo' và exog\_uncertainty = 'simulated'; contributions theo cấu trúc phân rã {world, fx, tax, fund, proxy\_error} như forecast.ledger\_entry.contributions.

#### forecast.scenario

Kịch bản giả định giá trị tương lai của biến ngoại sinh, ví dụ lãi suất điều hành +0,5 điểm từ tháng 12. Use case: UC-15-05.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| model\_id | uuid, bắt buộc | FK forecast\_model |
| name | text, bắt buộc |  |
| assumptions | jsonb, bắt buộc, mặc định '\[\]' | Mảng {feature, from\_period, change\_kind, value} |

Cột chung: id, org\_id, created\_at, created\_by. Ràng buộc: UNIQUE (model\_id, created\_by, name); FOREIGN KEY (model\_id) REFERENCES forecast.forecast\_model(id).

#### forecast.accuracy\_log

Sai số khi kỳ đã dự báo có số thực tế; cơ sở phát hiện suy giảm khi MASE cuốn chiếu vượt 1,2 lần mức backtest hoặc hai kỳ liền nằm ngoài khoảng 95% (BR-160). Use case: UC-15-06.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| model\_version\_id | uuid, bắt buộc | FK model\_version |
| period | date, bắt buộc |  |
| actual | numeric, bắt buộc | Số thực tế |
| point | numeric, bắt buộc | Số đã dự báo |
| abs\_scaled\_error | numeric, bắt buộc | Sai số tuyệt đối chia sai số seasonal naive trong mẫu |
| in80 | boolean, bắt buộc |  |
| in95 | boolean, bắt buộc |  |
| rolling\_mase | numeric | MASE cuốn chiếu 6 kỳ |
| recorded\_at | timestamptz, bắt buộc, mặc định now() |  |

Cột chung: id, org\_id. Ràng buộc: UNIQUE (model\_version\_id, period); CHECK (abs\_scaled\_error >= 0); FOREIGN KEY (model\_version\_id) REFERENCES forecast.model\_version(id). Chỉ mục: (model\_version\_id, period DESC).

#### forecast.formula\_model

Mô hình dự báo theo công thức cho một chỉ số điều hành của một mặt hàng, ví dụ giá trần xăng E5RON92: trỏ phiên bản công thức chỉ số đã chứng nhận, cách nowcast giá thế giới, mô hình quyết định Quỹ bình ổn và số lần mô phỏng; gắn một-một với một forecast.model\_version có algorithm = formula\_mc (BR-183, ADR-44). Use case: UC-15-07, UC-15-08.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| model\_version\_id | uuid, bắt buộc | FK model\_version; phiên bản có algorithm = 'formula\_mc' |
| metric\_version\_id | uuid, bắt buộc | logic → context.metric\_version; công thức giá cơ sở đã chứng nhận (expression, legal\_basis) |
| product\_code | text, bắt buộc | Mặt hàng: e5ron92, ron95iii, do005s, dau\_hoa, mazut180 |
| formula | jsonb, bắt buộc | Cây biểu thức biên dịch từ metric\_version.expression: mảng hạng {code, op, inputs}; chỉ tham chiếu formula\_component.code và hai biến đầu vào world\_avg, fx\_rate |
| window\_rule | jsonb, bắt buộc, mặc định '{}' | Cách lấy kỳ bình quân giá thế giới theo văn bản, ví dụ {from: previous\_event, to: day\_before\_event, days: trading}; người phụ trách chứng nhận cùng công thức |
| input\_series | jsonb, bắt buộc, mặc định '{}' | Chuỗi công khai cho nowcast {world\_proxy: \[series\_code\], fx: series\_code, published\_world\_avg: trường của dataset giá xăng dầu}; không dùng Platts, MOPS khi chưa có giấy phép (ADR-45) |
| nowcast\_method | text, bắt buộc, mặc định 'bridge\_regression' | bridge\_regression (hồi quy cầu nối từ chuỗi công khai về giá thế giới bình quân Bộ Công Thương công bố), published\_only (chỉ dùng số đã công bố, không nowcast) |
| fund\_model | jsonb, bắt buộc, mặc định '{}' | Mô hình xác suất quyết định Quỹ: {method, contribute\_levels, use\_levels, coefficients, trained\_periods, log\_score, baseline\_log\_score} (G8.9) |
| n\_draws | integer, bắt buộc, mặc định 10000 | Số lần mô phỏng Monte Carlo mỗi lần chạy (BR-184) |
| miss\_abs\_threshold | numeric, bắt buộc, mặc định 300 | Ngưỡng sai tuyệt đối, đ/lít, để tạo mục Error Book forecast\_miss (BR-186) |
| rounding | text, bắt buộc, mặc định 'integer' | Cách làm tròn giá công bố: integer (đồng), ten (bội 10 đồng) |
| owner\_id | uuid, bắt buộc | logic → org.app\_user; người phụ trách data product, chứng nhận tham số |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: UNIQUE (model\_version\_id); CHECK (nowcast\_method IN ('bridge\_regression','published\_only')); CHECK (n\_draws >= 10000); CHECK (miss\_abs\_threshold > 0); CHECK (rounding IN ('integer','ten')); CHECK (product\_code \~ '^\[a-z0-9\_\]+$'); CHECK (jsonb\_typeof(formula) = 'object'); FOREIGN KEY (model\_version\_id) REFERENCES forecast.model\_version(id). Chỉ mục: (metric\_version\_id); (product\_code). Trigger formula\_model\_guard: model\_version của dòng phải có algorithm = 'formula\_mc'; khi model\_version ở published hoặc degraded thì formula, window\_rule, metric\_version\_id không sửa được (đổi công thức là phiên bản mới, BR-183). Forecast service kiểm metric\_version còn certified và mọi formula\_component được formula tham chiếu có giá trị certified tại kỳ dự báo trước mỗi lần chạy (BR-183).

#### forecast.formula\_component

Tham số của công thức giá cơ sở theo ngày hiệu lực: thuế nhập khẩu bình quân gia quyền, thuế tiêu thụ đặc biệt, thuế bảo vệ môi trường, thuế giá trị gia tăng, chi phí định mức, lợi nhuận định mức và các hệ số quy đổi. Mỗi giá trị lấy từ văn bản gốc, có số hiệu và link, do người khác người nhập chứng nhận; mô hình ngôn ngữ không đặt giá trị (BR-182). Use case: UC-15-07.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| code | text, bắt buộc | Mã tham số, ví dụ import\_tax\_rate, excise\_tax\_rate, env\_tax, vat\_rate, cost\_norm, profit\_norm, litre\_per\_bbl, blend\_ratio |
| product\_code | text, bắt buộc | Mặt hàng như formula\_model.product\_code; '\*' khi áp cho mọi mặt hàng (ví dụ vat\_rate) |
| value | numeric, bắt buộc | Giá trị theo unit, đúng như văn bản |
| unit | text, bắt buộc | percent, vnd\_per\_litre, vnd\_per\_kg, usd\_per\_bbl, ratio |
| valid\_from | timestamptz, bắt buộc | Thời điểm hiệu lực theo văn bản |
| valid\_to | timestamptz | Thời điểm hết hiệu lực; đặt khi có giá trị mới hoặc khi văn bản ghi thời hạn (ví dụ mức gia hạn đến hết một ngày) |
| legal\_doc\_number | text, bắt buộc | Số hiệu văn bản, ví dụ 9017/BTC-QLG, 9149/BTC-QLG, 34/2026/NQ-CP, 66/2025/QH15 |
| legal\_doc\_url | text, bắt buộc | Link văn bản gốc |
| document\_id | uuid | logic → document.document; bản lưu trong kho tài liệu, dùng kiểm độ tươi và trích dẫn |
| extracted\_record\_id | uuid | logic → acquire.extracted\_record khi giá trị được trích từ trang nguồn |
| status | text, bắt buộc, mặc định 'draft' | draft, pending\_review, certified, superseded, rejected |
| certified\_by | uuid | logic → org.app\_user |
| certified\_at | timestamptz |  |
| hitl\_request\_id | uuid | logic → inbox.hitl\_request kind formula\_component |
| note | text | Ví dụ “mức gia hạn theo Nghị quyết 34/2026/NQ-CP” |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: CHECK (unit IN ('percent','vnd\_per\_litre','vnd\_per\_kg','usd\_per\_bbl','ratio')); CHECK (status IN ('draft','pending\_review','certified','superseded','rejected')); CHECK (valid\_to IS NULL OR valid\_to > valid\_from); CHECK (status NOT IN ('certified','superseded') OR (certified\_by IS NOT NULL AND certified\_at IS NOT NULL)); CHECK (certified\_by IS NULL OR certified\_by <> created\_by); CHECK (legal\_doc\_url \~ '^https?://'); CHECK (length(btrim(legal\_doc\_number)) > 0); CHECK (unit <> 'percent' OR (value >= 0 AND value <= 100)). Chỉ mục: duy nhất (org\_id, code, product\_code, valid\_from) WHERE status = 'certified'; (code, product\_code, valid\_from DESC). Trigger formula\_component\_guard: chỉ chuyển draft → pending\_review, rejected; pending\_review → certified, rejected, draft; certified → superseded; khi chuyển certified, bản certified cùng (code, product\_code) có valid\_to null được đặt valid\_to = valid\_from của bản mới (bản mới phải có valid\_from lớn hơn valid\_from bản cũ) và chuyển superseded khi tới valid\_from; chặn hai bản certified cùng (code, product\_code) có khoảng \[valid\_from, valid\_to) chồng nhau; dòng certified, superseded chỉ được sửa valid\_to và status (BR-182).

#### forecast.ledger\_entry

Sổ dự báo: mỗi dự báo đã phát hành (bản tin, câu trả lời, theo dõi) cho một kỳ của một phạm vi là một dòng ghi trước kỳ; phần dự báo bất biến, phần chấm điểm điền khi có số công bố; kỳ sai vẫn giữ và hiện trên sổ (BR-185, BR-186). Use case: UC-15-07, UC-15-08, UC-08-08.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| forecast\_model\_id | uuid, bắt buộc | FK forecast\_model |
| model\_version\_id | uuid, bắt buộc | FK model\_version |
| prediction\_id | uuid, bắt buộc | FK prediction; dự báo gốc (scenario\_id null) |
| job\_id | uuid | FK forecast\_job |
| period | date, bắt buộc | Kỳ được dự báo, ví dụ 2026-07-09 (kỳ điều hành) |
| scope | jsonb, bắt buộc, mặc định '{}' | Ví dụ {product\_code: e5ron92} |
| issued\_at | timestamptz, bắt buộc | Thời điểm phát hành, ví dụ 2026-07-08 20:00 +07 |
| issued\_via | text, bắt buộc | bulletin, answer, monitor, manual |
| event\_id | uuid | logic → monitor.external\_event |
| event\_at | timestamptz | Thời điểm kỳ áp dụng dự kiến lúc phát hành |
| baseline\_value | numeric, bắt buộc | Giá kỳ trước, ví dụ 19.730 đ/lít kỳ 02/7/2026 |
| point | numeric, bắt buộc | Trung vị phân phối |
| quantiles | jsonb, bắt buộc | {p02\_5, p10, p25, p50, p75, p90, p97\_5} |
| prob\_up | numeric(5,4) | Xác suất giá kỳ tới cao hơn baseline\_value |
| fund\_probs | jsonb, bắt buộc, mặc định '{}' | Xác suất các mức trích lập và chi Quỹ, ví dụ {contribute: {"0": 0.12, "200": 0.70, "300": 0.18}, use: {"0": 0.92, "300": 0.08}} |
| contributions | jsonb, bắt buộc, mặc định '{}' | Phân rã {world, fx, tax, fund, proxy\_error}, mỗi phần {vnd\_per\_litre, source\_refs} (BR-184) |
| status | text, bắt buộc, mặc định 'open' | open, scored, void |
| actual | numeric | Giá công bố của kỳ |
| actual\_source\_ref | jsonb | {extracted\_record\_id, dataset\_version\_id, url} của bản tin công bố |
| actual\_components | jsonb | Thành phần công bố: world\_avg, fund\_contribute, fund\_use |
| abs\_error | numeric | abs(actual − point), đ/lít |
| crps | numeric | CRPS tính trên mẫu mô phỏng đã lưu |
| pit | numeric(5,4) | Phân vị của actual trong phân phối dự báo |
| in80 | boolean |  |
| in95 | boolean |  |
| miss | boolean | Ngoài khoảng 95% hoặc abs\_error vượt ngưỡng của mô hình (BR-186) |
| miss\_attribution | jsonb | Phần lệch nhiều nhất, ví dụ {component: fund, predicted\_mode: 200, actual: 0} |
| error\_book\_entry\_id | uuid | logic → eval.error\_book\_entry (source\_kind forecast\_miss, UC-07-14); Forecast service ghi khi nhận eval.error\_book.opened của mục Eval service tạo từ sự kiện forecast.ledger.missed (BR-186) |
| accuracy\_log\_id | uuid | FK accuracy\_log |
| scored\_at | timestamptz |  |
| void\_reason | text | Chỉ khi kỳ bị huỷ hoặc không diễn ra, không dùng khi dự báo sai |

Cột chung: id, org\_id, created\_at, created\_by. Ràng buộc: CHECK (issued\_via IN ('bulletin','answer','monitor','manual')); CHECK (status IN ('open','scored','void')); CHECK (status <> 'scored' OR (actual IS NOT NULL AND abs\_error IS NOT NULL AND in80 IS NOT NULL AND in95 IS NOT NULL AND miss IS NOT NULL AND scored\_at IS NOT NULL)); CHECK (status <> 'void' OR (actual IS NULL AND void\_reason IS NOT NULL AND length(btrim(void\_reason)) > 0)); CHECK (event\_at IS NULL OR issued\_at < event\_at); CHECK (abs\_error IS NULL OR abs\_error >= 0); CHECK (crps IS NULL OR crps >= 0); CHECK (prob\_up IS NULL OR (prob\_up >= 0 AND prob\_up <= 1)); CHECK (pit IS NULL OR (pit >= 0 AND pit <= 1)); CHECK (in80 IS NOT TRUE OR in95 IS TRUE); CHECK (in95 IS NOT FALSE OR miss IS TRUE); UNIQUE (prediction\_id, issued\_via); FOREIGN KEY (forecast\_model\_id) REFERENCES forecast.forecast\_model(id); FOREIGN KEY (model\_version\_id) REFERENCES forecast.model\_version(id); FOREIGN KEY (prediction\_id) REFERENCES forecast.prediction(id); FOREIGN KEY (job\_id) REFERENCES forecast.forecast\_job(id); FOREIGN KEY (accuracy\_log\_id) REFERENCES forecast.accuracy\_log(id). Chỉ mục: (forecast\_model\_id, period DESC); (status, period) WHERE status = 'open'; (event\_id). Trigger ledger\_entry\_guard: các cột forecast\_model\_id, model\_version\_id, prediction\_id, job\_id, period, scope, issued\_at, issued\_via, event\_id, event\_at, baseline\_value, point, quantiles, prob\_up, fund\_probs, contributions không sửa được sau khi ghi; status chỉ đổi open → scored, open → void, scored → scored (chấm lại khi giá công bố được đính chính, bản chấm cũ giữ trong accuracy\_log theo UC-15-06); không có DELETE (svc\_forecast không có quyền DELETE trên bảng này) (BR-185).

Bổ sung 10/10/2026 cho bảng sẵn có (BR-183, BR-184, ADR-44) ghi ngay dưới từng bảng: forecast.model\_version (algorithm formula\_mc), forecast.prediction (interval\_method monte\_carlo), inbox.hitl\_request (kind formula\_component, D4.10).

### D4.24 Schema exec

Schema của Exec vault service cho P16 Lớp riêng lãnh đạo (UC-16-01 đến UC-16-06, thêm 10/10/2026). Mọi cột chứa nội dung là bytea bản mã; cột bản rõ chỉ là cột vận hành (owner\_id, due\_at, status, kind, thời điểm). Hai lớp bảo vệ độc lập: mã hoá phía thiết bị và trong OpenBao (ADR-46) là lớp chính, nên DBA hay vai trò bỏ qua RLS cũng chỉ thấy bản mã; RLS chỉ chủ kho là lớp thứ hai. Mọi bảng FORCE ROW LEVEL SECURITY; chính sách chủ kho dùng owner\_id = current\_setting('app.user\_id')::uuid, Gateway đặt app.user\_id cùng app.org\_id. Vai trò: svc\_exec (Exec service, thay chủ kho), svc\_exec\_worker (workflow chấm và Agent Bằng chứng trên Temporal, đặt app.exec\_workflow = 'on'), exec\_read chỉ cấp trên exec.publication cho svc\_context. Không cấp quyền đọc bảng exec cho vai trò quản trị nào (BR-189). Tham số mật mã: AEAD XChaCha20-Poly1305 (nonce 24 byte, tag 16 byte); khoá bọc 72 byte; hộp niêm phong X25519 (crypto\_box\_seal, thêm 48 byte); điều kiện RSA-OAEP SHA-256 với khoá Transit RSA-4096 (512 byte).

#### exec.vault

Kho riêng của một lãnh đạo: khoá công khai X25519 để workflow niêm phong kết quả gửi chủ kho, khoá riêng đã bọc bằng khoá kho (VMK), tên khoá điều kiện trong OpenBao, chính sách rời vị trí. Máy chủ không giữ VMK hay bản rõ (BR-189, ADR-46). Use case: UC-16-01, UC-16-06.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| owner\_id | uuid, bắt buộc | logic → org.app\_user; chủ kho, mỗi người một kho |
| status | text, bắt buộc, mặc định 'active' | active, sealed, shredded |
| aead\_alg | text, bắt buộc, mặc định 'xchacha20poly1305' | Thuật toán AEAD của VMK và DEK |
| box\_public\_key | bytea, bắt buộc | Khoá công khai X25519, 32 byte |
| box\_private\_key\_ct | bytea, bắt buộc | Khoá riêng X25519 mã hoá bằng VMK trên thiết bị; ghi rỗng khi huỷ khoá |
| transit\_key\_name | text, bắt buộc | Khoá điều kiện trong OpenBao Transit, dạng exec-cond-{org\_id}-{owner\_id}; RSA-4096 riêng mỗi kho |
| transit\_key\_version | integer, bắt buộc, mặc định 1 | Phiên bản khoá Transit hiện hành |
| exit\_policy | text, bắt buộc, mặc định 'seal' | seal, selective\_handover, shred; theo hợp đồng (BR-195) |
| contract\_ref | text | Số hợp đồng hoặc phụ lục quy định exit\_policy |
| sealed\_read\_until | date | Hạn chủ kho còn mở kho đã niêm phong; quá hạn thì huỷ khoá |
| recovery\_checked\_at | timestamptz | Lần gần nhất chủ kho mở thử bằng mã khôi phục giấy (R-34) |
| sealed\_at | timestamptz |  |
| shredded\_at | timestamptz |  |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: CHECK (status IN ('active','sealed','shredded')); CHECK (aead\_alg IN ('xchacha20poly1305')); CHECK (exit\_policy IN ('seal','selective\_handover','shred')); CHECK (octet\_length(box\_public\_key) = 32); CHECK (status = 'shredded' OR octet\_length(box\_private\_key\_ct) = 72); CHECK (transit\_key\_version > 0); CHECK (status <> 'sealed' OR sealed\_at IS NOT NULL); CHECK (status <> 'shredded' OR shredded\_at IS NOT NULL); UNIQUE (org\_id, owner\_id); UNIQUE (transit\_key\_name). Chỉ mục: (status); (sealed\_read\_until) WHERE status = 'sealed'. RLS: ngoài org\_isolation, chính sách hạn chế owner\_only cho mọi lệnh với svc\_exec: owner\_id = current\_setting('app.user\_id')::uuid; svc\_exec\_worker chỉ SELECT owner\_id, status, box\_public\_key, transit\_key\_name, transit\_key\_version (quyền cột) khi app.exec\_workflow = 'on'; chính sách exec\_admin cho svc\_exec khi app.exec\_admin = 'on' (AC05, AC06 qua UC-16-06) chỉ SELECT id, owner\_id, status, exit\_policy, sealed\_read\_until, không có cột khoá; vì RLS không giới hạn cột cho cùng một vai trò, quyền này cấp qua hàm SECURITY DEFINER exec.vault\_admin\_overview() (chỉ svc\_exec được gọi), chính sách của bảng không có nhánh quản trị. Trigger trước UPDATE: chỉ chuyển active → sealed, shredded; sealed → shredded; shredded là trạng thái cuối; box\_public\_key, transit\_key\_name không đổi; chuyển shredded thì đặt box\_private\_key\_ct = '\\x' và xoá mọi dòng exec.vault\_key\_wrap của kho trong cùng giao dịch (BR-195). svc\_exec không có DELETE.

#### exec.vault\_key\_wrap

Một cách mở kho: VMK bọc bằng khoá dẫn xuất từ đầu ra WebAuthn PRF của passkey, hmac-secret của khoá phần cứng, hoặc mã khôi phục giấy 24 từ qua Argon2id. Kho cần ít nhất 2 khoá bọc còn hiệu lực, một khoá ngoại tuyến (BR-189). Use case: UC-16-01, UC-16-06.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| vault\_id | uuid, bắt buộc | FK vault |
| owner\_id | uuid, bắt buộc | logic → org.app\_user; bằng vault.owner\_id |
| kind | text, bắt buộc | passkey\_prf, hardware\_key, recovery\_code |
| credential\_id | bytea | Mã credential WebAuthn; null với recovery\_code |
| label | text, bắt buộc | Tên chủ kho đặt, ví dụ “iPhone”, “YubiKey két sắt”, “Mã giấy 10/2026” |
| kdf | text, bắt buộc | hkdf\_sha256 với PRF và hmac-secret; argon2id với mã giấy |
| kdf\_salt | bytea, bắt buộc | Muối 32 byte gửi vào PRF hoặc KDF |
| wrapped\_vmk | bytea, bắt buộc | VMK mã hoá AEAD bằng khoá dẫn xuất, 72 byte |
| last\_used\_at | timestamptz |  |
| revoked\_at | timestamptz |  |

Cột chung: id, org\_id, created\_at, created\_by. Ràng buộc: CHECK (kind IN ('passkey\_prf','hardware\_key','recovery\_code')); CHECK ((kind = 'recovery\_code') = (credential\_id IS NULL)); CHECK (kdf IN ('hkdf\_sha256','argon2id')); CHECK ((kind = 'recovery\_code') = (kdf = 'argon2id')); CHECK (octet\_length(kdf\_salt) = 32); CHECK (octet\_length(wrapped\_vmk) = 72); FOREIGN KEY (vault\_id) REFERENCES exec.vault(id). Chỉ mục: duy nhất (vault\_id, credential\_id) WHERE credential\_id IS NOT NULL; (vault\_id) WHERE revoked\_at IS NULL. RLS: owner\_only như exec.vault; svc\_exec\_worker không có quyền. Trigger trước UPDATE: chỉ đổi last\_used\_at, revoked\_at; chặn thu hồi làm kho còn dưới 2 khoá bọc còn hiệu lực hoặc không còn khoá ngoại tuyến (hardware\_key hoặc recovery\_code), trừ khi kho đang chuyển shredded (BR-189). Thay khoá là thêm dòng mới; svc\_exec có DELETE chỉ trong trigger huỷ khoá của exec.vault.

#### exec.thesis

Luận điểm: nội dung (nhận định, lý do, mức tự tin, thẻ, bản sao điều kiện) là bản mã AEAD bằng DEK riêng; điều kiện chấm là bản mã RSA-OAEP bằng khoá Transit của kho; due\_at và status bản rõ để workflow lập lịch. Nội dung bất biến sau khi ghi (BR-188). Use case: UC-16-01, UC-16-03, UC-16-04, UC-16-05.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| vault\_id | uuid, bắt buộc | FK vault |
| owner\_id | uuid, bắt buộc | logic → org.app\_user; bằng vault.owner\_id |
| content\_ct | bytea, bắt buộc | Nonce và bản mã XChaCha20-Poly1305 của JSON {statement, rationale, confidence, tags, condition}; dữ liệu kèm (AAD) là id, owner\_id, content\_version |
| wrapped\_dek | bytea, bắt buộc | DEK 256 bit bọc bằng VMK, 72 byte |
| content\_version | smallint, bắt buộc, mặc định 1 | Phiên bản định dạng bản rõ |
| cond\_ct | bytea, bắt buộc | RSA-OAEP SHA-256 của JSON {metric\_ref, ref\_kind, scope, period, comparator, threshold, threshold\_hi, unit}; chỉ svc\_exec\_worker giải mã qua OpenBao (BR-189) |
| cond\_key\_version | integer, bắt buộc | Phiên bản khoá Transit đã dùng |
| due\_at | timestamptz, bắt buộc | Thời điểm chấm, làm tròn tới giờ (R-35); ví dụ 2026-10-22 18:00 với kỳ điều hành 22/10/2026 |
| committed\_at | timestamptz, bắt buộc, mặc định now() | Mốc ghi, dùng xếp hiệu chỉnh theo thời gian |
| status | text, bắt buộc, mặc định 'open' | open, invalid\_condition, scoring, scored, unresolvable, withdrawn |
| evidence\_state | text, bắt buộc, mặc định 'pending' | pending, two\_sided, one\_sided, insufficient; trigger của thesis\_evidence cập nhật |
| evidence\_refreshed\_at | timestamptz |  |
| supersedes\_id | uuid | FK thesis; luận điểm mà luận điểm này thay |
| withdrawn\_at | timestamptz |  |
| workflow\_id | text | Mã ExecThesisWorkflow trên Temporal |

Cột chung: id, org\_id, created\_at, created\_by. Không có updated\_\*: nội dung bất biến, chỉ status, evidence\_state, evidence\_refreshed\_at, withdrawn\_at, workflow\_id đổi được (quyền cột). Ràng buộc: CHECK (status IN ('open','invalid\_condition','scoring','scored','unresolvable','withdrawn')); CHECK (evidence\_state IN ('pending','two\_sided','one\_sided','insufficient')); CHECK (due\_at >= committed\_at + interval '1 day'); CHECK (content\_version > 0); CHECK (octet\_length(content\_ct) BETWEEN 41 AND 65536); CHECK (octet\_length(wrapped\_dek) = 72); CHECK (octet\_length(cond\_ct) = 512); CHECK (cond\_key\_version > 0); CHECK ((status = 'withdrawn') = (withdrawn\_at IS NOT NULL)); CHECK (withdrawn\_at IS NULL OR withdrawn\_at < due\_at); CHECK (supersedes\_id IS NULL OR supersedes\_id <> id); FOREIGN KEY (vault\_id) REFERENCES exec.vault(id); FOREIGN KEY (supersedes\_id) REFERENCES exec.thesis(id). Chỉ mục: (owner\_id, due\_at); (status, due\_at) WHERE status IN ('open','scoring'); (supersedes\_id). RLS: owner\_only cho svc\_exec; svc\_exec\_worker chỉ SELECT id, owner\_id, vault\_id, cond\_ct, cond\_key\_version, due\_at, status và UPDATE status, evidence\_state, evidence\_refreshed\_at khi app.exec\_workflow = 'on' (không có quyền trên content\_ct, wrapped\_dek). Trigger trước INSERT: kho active, owner\_id bằng vault.owner\_id, kho có từ 2 khoá bọc còn hiệu lực trong đó một khoá ngoại tuyến (BR-189); supersedes\_id cùng chủ kho; due\_at phải tròn giờ theo UTC (phút, giây bằng 0; kiểm ở trigger vì date\_trunc trên timestamptz không dùng được trong CHECK). Trigger trước UPDATE: chặn đổi content\_ct, wrapped\_dek, cond\_ct, cond\_key\_version, due\_at, committed\_at, owner\_id; chỉ chuyển open → scoring, invalid\_condition, withdrawn; scoring → scored, unresolvable, open; withdrawn chỉ khi now() < due\_at (BR-188). svc\_exec không có DELETE.

#### exec.thesis\_evidence

Một mục trong lô bằng chứng do Agent Bằng chứng tìm mù (BR-190): phía, loại, độ mạnh bản rõ; nội dung (tóm tắt, số, query\_run\_id, nguồn, kỳ, độ mới) niêm phong bằng box\_public\_key của kho nên chỉ chủ kho mở được. Lô mới không xoá lô cũ. Use case: UC-16-03.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| thesis\_id | uuid, bắt buộc | FK thesis |
| owner\_id | uuid, bắt buộc | logic → org.app\_user |
| batch\_no | integer, bắt buộc | Lô thứ mấy của luận điểm; màn hiện lô lớn nhất, so với lô trước |
| side | text, bắt buộc | support, against (so với điều kiện đúng) |
| item\_kind | text, bắt buộc | evidence, none\_found, insufficient |
| strength | text | weak, moderate, strong; chỉ có với evidence (luật ở tab 5 G10.3) |
| freshness\_ok | boolean, bắt buộc | Nguồn còn trong hạn độ mới lúc tìm |
| sealed\_ct | bytea, bắt buộc | Hộp niêm phong X25519 cho chủ kho |
| agent\_run\_id | uuid, bắt buộc | logic → agent.agent\_run của Agent Bằng chứng |
| recorded\_at | timestamptz, bắt buộc, mặc định now() |  |

Cột chung: id, org\_id, created\_at. Bản ghi bất biến. Ràng buộc: CHECK (side IN ('support','against')); CHECK (item\_kind IN ('evidence','none\_found','insufficient')); CHECK (strength IS NULL OR strength IN ('weak','moderate','strong')); CHECK ((item\_kind = 'evidence') = (strength IS NOT NULL)); CHECK (item\_kind <> 'evidence' OR freshness\_ok); CHECK (batch\_no > 0); CHECK (octet\_length(sealed\_ct) BETWEEN 49 AND 65536); FOREIGN KEY (thesis\_id) REFERENCES exec.thesis(id). Chỉ mục: (thesis\_id, batch\_no DESC). RLS: owner\_only SELECT cho svc\_exec; svc\_exec\_worker chỉ INSERT. Không vai trò nào có UPDATE, DELETE. Trigger ràng buộc kiểm cuối giao dịch: mỗi (thesis\_id, batch\_no) có ít nhất một dòng side = 'support' và một dòng side = 'against' (BR-190); batch\_no bằng lô lớn nhất cộng 1; luận điểm đang open. Trigger sau INSERT: cập nhật thesis.evidence\_state (two\_sided khi cả hai phía có evidence; insufficient khi có dòng insufficient; còn lại one\_sided) và evidence\_refreshed\_at.

#### exec.thesis\_score

Điểm chấm của luận điểm, chỉ ghi thêm (BR-191): kết quả bản rõ; giá trị quan sát, điều kiện đã dùng, phiên bản chỉ số hoặc data product, query\_run\_id và biên nhận niêm phong cho chủ kho. Brier tính trên thiết bị vì mức tự tin là bản mã. Use case: UC-16-04.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| thesis\_id | uuid, bắt buộc | FK thesis |
| owner\_id | uuid, bắt buộc | logic → org.app\_user |
| kind | text, bắt buộc | final, restatement |
| outcome | text, bắt buộc | correct, incorrect, unresolvable |
| as\_of | timestamptz, bắt buộc | Thời điểm dữ liệu dùng để chấm, bằng thesis.due\_at |
| attempt\_no | smallint, bắt buộc, mặc định 1 | Lần thử chấm thành công; tối đa 14 |
| sealed\_ct | bytea, bắt buộc | Hộp niêm phong cho chủ kho |
| workflow\_run\_id | text, bắt buộc | Mã lượt Temporal |
| scored\_at | timestamptz, bắt buộc, mặc định now() |  |

Cột chung: id, org\_id, created\_at. Bản ghi bất biến. Ràng buộc: CHECK (kind IN ('final','restatement')); CHECK (outcome IN ('correct','incorrect','unresolvable')); CHECK (kind = 'final' OR outcome <> 'unresolvable'); CHECK (attempt\_no BETWEEN 1 AND 14); CHECK (octet\_length(sealed\_ct) BETWEEN 49 AND 16384); FOREIGN KEY (thesis\_id) REFERENCES exec.thesis(id). Chỉ mục: duy nhất (thesis\_id) WHERE kind = 'final'; (owner\_id, scored\_at DESC). RLS: owner\_only SELECT cho svc\_exec; svc\_exec\_worker chỉ INSERT. Không vai trò nào có UPDATE, DELETE. Trigger trước INSERT: kind final cần thesis ở scoring và as\_of = due\_at; restatement cần đã có dòng final. Trigger sau INSERT kind final: thesis.status chuyển scored (correct, incorrect) hoặc unresolvable.

#### exec.memory\_item

Mục bộ nhớ riêng: ưu tiên, lý do quyết định, câu hỏi đã hỏi, bản chụp câu trả lời, ghi chú (gồm mục nhận bàn giao). Huỷ một mục là xoá DEK đã bọc và bản mã. Use case: UC-16-02, UC-16-06.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| vault\_id | uuid, bắt buộc | FK vault |
| owner\_id | uuid, bắt buộc | logic → org.app\_user |
| kind | text, bắt buộc | priority, decision\_reason, question, answer\_snapshot, note |
| content\_ct | bytea | Bản mã AEAD của nội dung; null sau khi huỷ |
| wrapped\_dek | bytea | DEK bọc bằng VMK, 72 byte; null sau khi huỷ |
| content\_version | smallint, bắt buộc, mặc định 1 |  |
| related\_thesis\_id | uuid | FK thesis |
| handover\_item\_id | uuid | logic → exec.handover\_item của kho người bàn giao; có với mục nhận bàn giao |
| shredded\_at | timestamptz |  |

Cột chung: id, org\_id, created\_at, created\_by. Ràng buộc: CHECK (kind IN ('priority','decision\_reason','question','answer\_snapshot','note')); CHECK ((shredded\_at IS NULL) = (content\_ct IS NOT NULL AND wrapped\_dek IS NOT NULL)); CHECK (shredded\_at IS NOT NULL OR (octet\_length(wrapped\_dek) = 72 AND octet\_length(content\_ct) BETWEEN 41 AND 262144)); CHECK (handover\_item\_id IS NULL OR kind = 'note'); FOREIGN KEY (vault\_id) REFERENCES exec.vault(id); FOREIGN KEY (related\_thesis\_id) REFERENCES exec.thesis(id). Chỉ mục: (owner\_id, created\_at DESC); (owner\_id, kind). RLS: owner\_only cho svc\_exec; svc\_exec\_worker không có quyền. Trigger trước INSERT: kho active, đủ khoá bọc như exec.thesis (BR-189). Trigger trước UPDATE: chỉ cho phép huỷ (đặt content\_ct, wrapped\_dek null và shredded\_at = now()); không sửa nội dung. svc\_exec không có DELETE.

#### exec.key\_access\_log

Sổ truy cập khoá, chỉ ghi thêm (BR-192): mỗi lần workflow giải mã điều kiện, niêm phong kết quả, mỗi lần thiết bị mở kho, bọc lại để bàn giao, xoay hoặc huỷ khoá. Ghi trước khi gọi OpenBao, kèm correlation\_id mà OpenBao ghi ở nhật ký audit (header X-Exec-Correlation) để đối chiếu hai phía. Use case: UC-16-03, UC-16-04, UC-16-06.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| vault\_id | uuid, bắt buộc | logic → exec.vault |
| owner\_id | uuid, bắt buộc | logic → org.app\_user; chủ kho |
| thesis\_id | uuid | logic → exec.thesis; null với sự kiện mức kho |
| key\_kind | text, bắt buộc | condition\_transit, vault\_key, box\_key |
| action | text, bắt buộc | decrypt\_condition, unlock, seal\_evidence, seal\_score, rewrap\_handover, rotate, shred |
| purpose | text, bắt buộc | validate, evidence\_refresh, scoring, unlock, handover, rotation, shred |
| actor\_kind | text, bắt buộc | workflow, owner\_device, admin\_request |
| actor\_ref | text, bắt buộc | Mã workflow và activity, nhãn khoá bọc, hoặc mã handover |
| correlation\_id | uuid, bắt buộc | Gửi kèm lời gọi OpenBao, có trong nhật ký audit của OpenBao |
| at | timestamptz, bắt buộc, mặc định now() |  |

Cột chung: id, org\_id. Bản ghi bất biến. Ràng buộc: CHECK (key\_kind IN ('condition\_transit','vault\_key','box\_key')); CHECK (action IN ('decrypt\_condition','unlock','seal\_evidence','seal\_score','rewrap\_handover','rotate','shred')); CHECK (purpose IN ('validate','evidence\_refresh','scoring','unlock','handover','rotation','shred')); CHECK (actor\_kind IN ('workflow','owner\_device','admin\_request')); CHECK (action <> 'decrypt\_condition' OR (key\_kind = 'condition\_transit' AND actor\_kind = 'workflow' AND thesis\_id IS NOT NULL)); UNIQUE (correlation\_id). Chỉ mục: (owner\_id, at DESC); (thesis\_id, at DESC). RLS: owner\_only SELECT cho svc\_exec; svc\_exec và svc\_exec\_worker chỉ INSERT. Không vai trò nào có UPDATE, DELETE. Trigger trước INSERT action decrypt\_condition: đếm dòng cùng thesis\_id trong 24 giờ; từ 3 thì chặn bằng cách không ghi dòng (ném lỗi sẽ huỷ cả sự kiện; workflow chỉ gọi OpenBao khi INSERT ... RETURNING trả dòng) và phát exec.key.decrypt\_limit\_exceeded để Audit service ghi audit.security\_event (BR-192).

#### exec.handover

Yêu cầu xử lý kho khi chủ kho rời vị trí: niêm phong, bàn giao có chọn lọc hoặc huỷ khoá theo hợp đồng (BR-195). Bảng không có cột bản mã nên AC05, AC06 đọc được qua chính sách exec\_admin. Use case: UC-16-06.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| vault\_id | uuid, bắt buộc | FK vault |
| owner\_id | uuid, bắt buộc | logic → org.app\_user; chủ kho rời vị trí |
| kind | text, bắt buộc | seal, selective\_handover, shred |
| reason | text, bắt buộc | leave\_position, contract\_end, owner\_request, sealed\_expired |
| requested\_by | uuid, bắt buộc | logic → org.app\_user; chủ kho hoặc AC05 |
| owner\_participates | boolean, bắt buộc | Chủ kho tự xác nhận bằng passkey |
| approved\_by | uuid | logic → org.app\_user; AC06, bắt buộc với shred khi chủ kho không tham gia |
| approved\_at | timestamptz |  |
| successor\_id | uuid | logic → org.app\_user; bắt buộc với selective\_handover |
| status | text, bắt buộc, mặc định 'requested' | requested, approved, in\_progress, completed, rejected, cancelled |
| items\_total | integer, bắt buộc, mặc định 0 | Số mục bàn giao |
| contract\_ref | text | Số hợp đồng hoặc phụ lục |
| certificate\_sha256 | text | Băm biên bản huỷ khoá (tên và phiên bản khoá Transit, số khoá bọc đã xoá, thời điểm, ngày hết hạn sao lưu OpenBao) |
| completed\_at | timestamptz |  |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: CHECK (kind IN ('seal','selective\_handover','shred')); CHECK (reason IN ('leave\_position','contract\_end','owner\_request','sealed\_expired')); CHECK (status IN ('requested','approved','in\_progress','completed','rejected','cancelled')); CHECK (kind <> 'selective\_handover' OR (successor\_id IS NOT NULL AND owner\_participates)); CHECK (successor\_id IS NULL OR successor\_id <> owner\_id); CHECK (kind <> 'shred' OR owner\_participates OR reason = 'sealed\_expired' OR status IN ('requested','rejected','cancelled') OR approved\_by IS NOT NULL); CHECK (approved\_by IS NULL OR approved\_by <> requested\_by); CHECK (items\_total >= 0); CHECK (status <> 'completed' OR completed\_at IS NOT NULL); CHECK (kind <> 'shred' OR status <> 'completed' OR certificate\_sha256 \~ '^\[0-9a-f\]{64}$'); FOREIGN KEY (vault\_id) REFERENCES exec.vault(id). Chỉ mục: duy nhất (vault\_id) WHERE status IN ('requested','approved','in\_progress'); (status, created\_at). RLS: owner\_only cho svc\_exec; chính sách exec\_admin cho svc\_exec khi app.exec\_admin = 'on' (SELECT, INSERT, UPDATE status, approved\_by, approved\_at). Trigger sau UPDATE sang completed: seal đặt vault sealed và sealed\_read\_until theo hợp đồng; shred đặt vault shredded và phát exec.vault.shred\_requested qua outbox để worker xoá khoá Transit (BR-195).

#### exec.handover\_item

Một mục bàn giao đang trung chuyển: chủ kho cũ giải mã trên thiết bị và niêm phong lại bằng box\_public\_key của kho người kế nhiệm; người nhận mở, mã hoá lại vào kho mình rồi dòng bị xoá. Luận điểm và điểm chấm không bàn giao. Use case: UC-16-06.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| handover\_id | uuid, bắt buộc | FK handover |
| owner\_id | uuid, bắt buộc | logic → org.app\_user; chủ kho bàn giao |
| successor\_id | uuid, bắt buộc | logic → org.app\_user; người nhận |
| source\_kind | text, bắt buộc | memory\_item, thesis\_note |
| source\_id | uuid, bắt buộc | logic → exec.memory\_item hoặc exec.thesis của kho cũ |
| payload\_sealed | bytea, bắt buộc | Hộp niêm phong X25519 cho kho người nhận |
| imported\_at | timestamptz | Người nhận đã nhập vào kho mình |

Cột chung: id, org\_id, created\_at, created\_by. Ràng buộc: CHECK (source\_kind IN ('memory\_item','thesis\_note')); CHECK (octet\_length(payload\_sealed) BETWEEN 49 AND 262144); CHECK (successor\_id <> owner\_id); UNIQUE (handover\_id, source\_kind, source\_id); FOREIGN KEY (handover\_id) REFERENCES exec.handover(id). Chỉ mục: (successor\_id) WHERE imported\_at IS NULL. RLS: chủ kho cũ INSERT (owner\_id = app.user\_id); người nhận SELECT và UPDATE imported\_at (successor\_id = app.user\_id). Trigger trước INSERT: handover kind selective\_handover ở approved hoặc in\_progress, owner\_id và successor\_id khớp handover; tăng handover.items\_total. Việc dọn hằng ngày xoá dòng đã nhập hoặc quá 30 ngày; đây là ngoại lệ duy nhất có DELETE trong schema exec.

#### exec.publication

Bản công bố của một luận điểm, là bản rõ do lãnh đạo chọn câu chữ; nguồn của trang tri thức kind hypothesis (BR-194). Điều kiện ở cột có cấu trúc để thân trang không có số viết tay (BR-162). Use case: UC-16-05.

| Cột | Kiểu | Ý nghĩa |
| --- | --- | --- |
| thesis\_id | uuid, bắt buộc | FK thesis |
| owner\_id | uuid, bắt buộc | logic → org.app\_user |
| title | text, bắt buộc |  |
| statement\_public | text, bắt buộc | Câu giả thuyết trung tính, không chứa số viết tay |
| ref\_kind | text, bắt buộc | metric, data\_product |
| metric\_ref | text, bắt buộc | Mã chỉ số (context.metric) hoặc mã data product (context.data\_product) |
| scope | jsonb, bắt buộc, mặc định '{}' | Phạm vi theo chiều, ví dụ {"product": "E5RON92"} |
| period\_label | text, bắt buộc | Ví dụ 2026-10-22, 2026-W45 |
| comparator | text, bắt buộc | gte, lte, gt, lt, between |
| threshold | numeric, bắt buộc |  |
| threshold\_hi | numeric | Bắt buộc khi between |
| unit | text, bắt buộc | Ví dụ VND/l, % |
| due\_at | timestamptz, bắt buộc | Bằng thesis.due\_at |
| attribution | text, bắt buộc, mặc định 'role\_only' | named, role\_only |
| attribution\_label | text, bắt buộc | Tên hoặc chức danh hiển thị, ví dụ “Thành viên Ban điều hành” |
| confidence\_public | numeric(3,2) | Chỉ có khi lãnh đạo chọn hiện |
| rationale\_public | text | Chỉ có khi lãnh đạo chọn hiện |
| target\_data\_space\_id | uuid, bắt buộc | logic → org.data\_space |
| knowledge\_page\_id | uuid | logic → context.knowledge\_page kind hypothesis |
| status | text, bắt buộc, mặc định 'pending\_review' | pending\_review, published, rejected, withdrawn |
| published\_at | timestamptz |  |
| withdrawn\_at | timestamptz |  |

Cột chung: id, org\_id, created\_at, created\_by, updated\_at, updated\_by. Ràng buộc: CHECK (ref\_kind IN ('metric','data\_product')); CHECK (comparator IN ('gte','lte','gt','lt','between')); CHECK ((comparator = 'between') = (threshold\_hi IS NOT NULL)); CHECK (threshold\_hi IS NULL OR threshold\_hi > threshold); CHECK (attribution IN ('named','role\_only')); CHECK (confidence\_public IS NULL OR confidence\_public BETWEEN 0.50 AND 0.99); CHECK (status IN ('pending\_review','published','rejected','withdrawn')); CHECK (status <> 'published' OR (published\_at IS NOT NULL AND knowledge\_page\_id IS NOT NULL)); CHECK (status <> 'withdrawn' OR withdrawn\_at IS NOT NULL); CHECK (statement\_public !\~ '\[0-9\]'); FOREIGN KEY (thesis\_id) REFERENCES exec.thesis(id). Chỉ mục: duy nhất (thesis\_id) WHERE status IN ('pending\_review','published'); (target\_data\_space\_id, status). RLS: owner\_only cho svc\_exec; exec\_read (cấp cho svc\_context) SELECT khi status IN ('pending\_review','published'); người đọc trang thấy theo chính sách trang (BR-163). Trigger trước INSERT: thesis cùng chủ kho, ở open hoặc scored; due\_at bằng thesis.due\_at. Trigger sau UPDATE: published, withdrawn phát exec.publication.published, exec.publication.withdrawn để Context service chuyển trang.

Bổ sung cho D4.5 do P16 (10/10/2026, đã nhập vào bộ sinh ngày 11/10/2026, ghi ở D4.5 dưới từng bảng): context.knowledge\_page thêm kind 'hypothesis' với subject\_kind 'exec\_publication' (subject\_ref là exec.publication.id) vào CHECK kind và CHECK cặp kind, subject\_kind; context.knowledge\_source\_link thêm source\_kind 'exec\_publication'; context.knowledge\_page\_version thêm origin 'exec\_publish'; thân trang hypothesis dùng thêm thẻ {{hyp:condition}} điền từ exec.publication khi hiển thị, được trigger BR-162 miễn kiểm như chỗ giữ số. RLS hạn chế của trang không đổi. Trang kind hypothesis không là nguồn figures cho câu trả lời (G9.5) và không là nguồn bằng chứng của Agent Bằng chứng cho kho của chính người công bố (BR-190, BR-194).
