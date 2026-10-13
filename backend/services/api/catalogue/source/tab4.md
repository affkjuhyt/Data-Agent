# Đặc tả API

Tab này đặc tả giao diện lập trình của Data Agent V3: quy ước chung (A1), danh mục endpoint theo dịch vụ (A2), chi tiết request và response từng endpoint (A3), tệp OpenAPI (A4). Danh mục dựng lại từ 103 đặc tả use case và mục 9.2, đã chuẩn hoá tên và cách gọi; cột dữ liệu bám theo thiết kế cơ sở dữ liệu ở tab 3. Trạng thái (09/10/2026): xong A1 đến A4; 301 endpoint có đặc tả chi tiết, tệp OpenAPI 3.1 đã qua kiểm tra hợp lệ với 240 endpoint. Ngày 10/10/2026 thêm nhóm Thu thập và Dự báo (41 endpoint, P14, P15) và 2 endpoint chứng nhận dataset hợp nhất ở nhóm Nguồn và dữ liệu (BR-156); sau đó thêm 2 endpoint ở nhóm Thu thập (PATCH /v1/consolidation-specs/{id}, POST /v1/crawl-sources/{id}:legal-review) và 16 endpoint lớp tri thức ở nhóm Lớp ngữ cảnh. 61 endpoint này nằm ở hai tệp OpenAPI bổ sung đã qua kiểm tra hợp lệ, nay là bản tham khảo (A4, ADR-48). Lượt 4 thêm 77 endpoint: data product, biên nhận, Error Book, provider mô hình (29), dự báo theo công thức và sổ dự báo (12), lịch sự kiện ngoài (8), nhóm mới Lớp riêng lãnh đạo (28). Tổng hiện có (12/10/2026): 383 endpoint trong 22 nhóm (A2), gồm GET /v1/me thêm ngày 11/10/2026 ở nhóm Quản trị, PUT /v1/me/data-space-preference thêm ngày 12/10/2026 ở nhóm Quản trị, GET /v1/me/clients và POST /v1/me/clients/{id}:confirm thêm ngày 12/10/2026 ở nhóm Tích hợp (người phụ trách xác nhận client, BR-48), GET /v1/audit/exports/{id} thêm ngày 12/10/2026 ở nhóm Kiểm toán (nơi Location của :export trỏ tới); OpenAPI của mọi endpoint, kể cả 77 endpoint lượt 4, sinh từ code FastAPI và được kiểm với A2 trong CI (A4, ADR-48).

## A1. Quy ước chung

### A1.1 Địa chỉ, phiên bản, định dạng

- Gốc: https://{máy chủ}/v1. Phiên bản lớn nằm trong đường dẫn; thay đổi không tương thích tạo /v2 và giữ /v1 tối thiểu 6 tháng.
- Dữ liệu JSON UTF-8, tên trường snake\_case. Mốc thời gian theo RFC 3339 có múi giờ; ngày nghiệp vụ dạng YYYY-MM-DD. Số tiền và tỷ lệ trả dạng chuỗi thập phân để không mất chính xác (ví dụ "1234567.89").
- Mã đối tượng là UUIDv7 dạng chuỗi. Tài nguyên có mã nghiệp vụ (chỉ số, kỹ năng) nhận cả mã và id ở đường dẫn.
- Tải tệp dùng multipart/form-data; tải về luôn qua liên kết ký có hạn, không trả byte trong API.

### A1.2 Xác thực, tổ chức, Data Space

- Mọi lời gọi mang Authorization: Bearer \<access token> do Keycloak cấp (OIDC). Client của hệ thống khác dùng client credentials hoặc token exchange thay người dùng (UC-12-01).
- Tổ chức lấy từ token (claim org\_id); Gateway đặt app.org\_id cho kết nối CSDL, nên không truyền org\_id trong API.
- Tài nguyên thuộc một Data Space nhận header X-Data-Space: \<id hoặc mã>. Thiếu header với tài nguyên cần Data Space thì trả 400 data\_space\_required. Tài nguyên toàn tổ chức (quản trị, lịch, gói ngành) không cần header.
- Cột “Quyền” ở A2 là năng lực trong org.role.capabilities: ask, view\_detail, upload, prepare\_data, admin\_data, steward, approve, create\_skill, run\_agent, create\_agent, writeback, pack\_author, admin, compliance, audit\_read. Ba giá trị đặc biệt: channel (lời gọi từ Teams, Zalo, xác thực bằng chữ ký HMAC thay cho token người dùng), client (token của api\_client cho MCP), — (mọi người dùng đã đăng nhập). Ngoài năng lực, Policy engine còn áp chính sách dòng, cột, che (UC-11-02) cho mọi dữ liệu trả về.

### A1.3 Cách đặt đường dẫn

- Danh sách và tạo: GET, POST /v1/{tài nguyên}. Đọc, sửa một phần, xoá: GET, PATCH, DELETE /v1/{tài nguyên}/{id}. Thay cả tập con: PUT /v1/{tài nguyên}/{id}/{tập con}.
- Thao tác làm đổi trạng thái hoặc có hiệu ứng ngoài CRUD dùng POST /v1/{tài nguyên}/{id}:{động từ}, ví dụ :close, :publish, :decide, :resolve, :pause. Động từ ở dạng nguyên mẫu, chữ thường, nối bằng gạch ngang.
- Không dùng động từ trong tên tài nguyên ngoài dạng :{động từ}.

### A1.4 Danh sách, lọc, sắp xếp

- Phân trang bằng con trỏ: ?page\_size= (mặc định 50, tối đa 500) và ?page\_token=. Phản hồi {"items": \[...\], "next\_page\_token": "..."}; next\_page\_token rỗng là hết.
- Lọc bằng tham số cùng tên trường (?status=open&kind=conflict); khoảng thời gian dùng from, to. Sắp xếp ?order\_by=created\_at desc.
- Danh sách chỉ trả trường tóm tắt; chi tiết lấy bằng GET theo id.

### A1.5 Ghi đồng thời và chống gửi trùng

- Mọi phản hồi đọc một tài nguyên có header ETag. PATCH, PUT và thao tác :{động từ} nên gửi If-Match; lệch phiên bản trả 412 precondition\_failed.
- POST nhận header Idempotency-Key (tối đa 128 ký tự). Cùng khoá trong 24 giờ trả lại đúng phản hồi lần đầu; cùng khoá nhưng thân khác trả 422 idempotency\_mismatch (integration.idempotency\_key).

### A1.6 Việc chạy lâu và luồng sự kiện

- Việc có thể quá 2 giây (quét, chạy kỹ năng, chốt kỳ, đánh giá, đối chiếu) trả 202 Accepted, header Location trỏ tới tài nguyên lượt chạy (scan-runs, skill-runs, eval-runs, report-runs, runs…) và thân {"id", "status", "poll\_after\_ms"}.
- Câu hỏi và lượt chạy agent có luồng Server-Sent Events: GET /v1/questions/{id}/stream, GET /v1/runs/{id}/events, GET /v1/forecasts/{id}/events. Mỗi sự kiện có event (understood, progress, clarification, answer, checkpoint, error, done; luồng dự báo: progress, waiting\_human, eta\_changed, result, error, done) và data JSON; client nối lại bằng Last-Event-ID.
- Khi xong, hệ thống còn phát sự kiện Kafka (mục 9.3) và webhook cho client đã đăng ký.

### A1.7 Lỗi

Mọi lỗi trả application/problem+json theo RFC 9457:

| Trường | Ý nghĩa |
| --- | --- |
| type | URI tài liệu của loại lỗi, dạng https://docs.dataagent/errors/{code} |
| title | Tên lỗi ngắn |
| status | Mã HTTP |
| code | Mã lỗi ổn định cho máy đọc, ví dụ policy\_denied |
| detail | Giải thích cho người đọc, không lộ tên bảng hay dữ liệu ngoài quyền |
| trace\_id | Mã vết, khớp Langfuse và nhật ký |
| errors | Lỗi theo trường khi kiểm tra đầu vào: \[{field, code, message}\] |

| HTTP | code | Khi nào |
| --- | --- | --- |
| 400 | invalid\_request, data\_space\_required | Thân sai định dạng, thiếu header Data Space |
| 401 | unauthenticated | Thiếu hoặc hết hạn token |
| 403 | forbidden, policy\_denied, purpose\_mismatch | Thiếu năng lực; chính sách chặn; mục đích không khớp bộ sưu tập (BR-59) |
| 404 | not\_found | Không tồn tại hoặc ngoài quyền (không phân biệt để tránh dò) |
| 409 | conflict, invalid\_state | Trùng khoá; thao tác không hợp lệ với trạng thái hiện tại |
| 412 | precondition\_failed | If-Match không khớp |
| 413 | payload\_too\_large | Tệp quá 200 MB. Mã 410 gone dùng khi liên kết tải hoặc kết quả đã quá hạn lưu |
| 422 | validation\_failed, business\_rule\_violated, idempotency\_mismatch | Vi phạm ràng buộc; vi phạm quy tắc nghiệp vụ (kèm mã BR trong detail) |
| 423 | period\_locked | Ghi vào kỳ đã chốt hoặc đang chốt |
| 429 | rate\_limited, quota\_exceeded | Vượt hạn mức; header Retry-After |
| 503 | source\_unavailable, model\_unavailable | Nguồn hoặc mô hình tạm không phục vụ |

### A1.8 Hạn mức và nhật ký

- Mỗi phản hồi có RateLimit-Limit, RateLimit-Remaining, RateLimit-Reset. Hạn mức người dùng mặc định 120 lời gọi mỗi phút; client theo api\_client.quota\_per\_minute, quota\_per\_day.
- Mọi lời gọi ghi vào audit.audit\_log (người, client, thay ai, hành động, kết quả); lời gọi bị chặn ghi outcome = denied.

## A2. Danh mục endpoint

Mỗi nhóm ghi dịch vụ sở hữu. Cột Use case dùng số rút gọn (01-01 là UC-01-01). Tổng 379 endpoint trong 22 nhóm, phủ đủ 115 use case.

### Hỏi đáp

Dịch vụ: Agent runtime (qa). 17 endpoint.

| Phương thức | Đường dẫn | Chức năng | Use case | Quyền |
| --- | --- | --- | --- | --- |
| POST | /v1/questions | Gửi câu hỏi; trả 202 kèm question\_id | 01-01, 01-04, 05-04, 10-01 | ask |
| GET | /v1/questions/{id} | Trạng thái câu hỏi, kế hoạch hiện hành | 01-01 | ask |
| GET | /v1/questions/{id}/stream | Luồng SSE: cách hiểu, tiến độ, câu trả lời | 01-01 | ask |
| PATCH | /v1/questions/{id}/plan | Sửa chip cách hiểu, chạy lại | 01-03 | ask |
| GET | /v1/conversations | Danh sách hội thoại của tôi | 01-04 | ask |
| GET | /v1/answers/{id} | Câu trả lời đầy đủ: số, biểu đồ, trích dẫn | 01-01 | ask |
| GET | /v1/answers/{id}/lineage | Cách tính một con số | 01-02 | ask |
| POST | /v1/answers/{id}/feedback | Đánh giá hoặc báo sai | 01-05 | ask |
| PATCH | /v1/feedback/{id} | Xử lý báo sai (xác nhận, bác) | 01-05 | approve |
| POST | /v1/feedback/{id}:to-eval | Đưa báo sai đã xác nhận vào bộ đánh giá | 01-05 | approve |
| POST | /v1/answers/{id}/exports | Xuất kết quả ra tệp | 01-06 | ask |
| GET | /v1/exports/{id} | Trạng thái và liên kết tải (24 giờ) | 01-06 | ask |
| POST | /v1/explore/queries | Khám phá theo chỉ số không qua câu hỏi | 01-04 | ask |
| GET | /v1/answers/{id}/receipt | Biên nhận của câu trả lời trong phạm vi data product | 01-07 | ask |
| GET | /v1/receipts/{receipt\_no} | Mở biên nhận theo mã BN-…, kiểm lại mã băm | 01-07 | ask |
| POST | /v1/receipts/{id}:replay | Chạy lại tại thời điểm cũ, so từng số | 01-07 | ask |
| GET | /v1/receipt-replays/{id} | Kết quả một lần chạy lại | 01-07 | ask |

### Truy vấn

Dịch vụ: Query engine (query). 3 endpoint.

| Phương thức | Đường dẫn | Chức năng | Use case | Quyền |
| --- | --- | --- | --- | --- |
| GET | /v1/query-runs/{id} | Thông tin lượt chạy SQL | 01-02 | ask |
| GET | /v1/query-runs/{id}/sql | SQL đã viết lại theo chính sách | 01-02 | view\_detail |
| GET | /v1/query-runs/{id}/rows | Dòng kết quả, phân trang | 01-02 | view\_detail |
| POST | /v1/query-runs | Chạy truy vấn chỉ đọc trên một bảng (lọc) hoặc SQL, theo quyền người gọi (thêm 12/10/2026) | 04-02, 06-01, 06-02 | view\_detail |

### Phiên làm việc

Dịch vụ: Workspace service (workspace). 22 endpoint.

| Phương thức | Đường dẫn | Chức năng | Use case | Quyền |
| --- | --- | --- | --- | --- |
| GET | /v1/workspaces | Danh sách phiên | 02-01 | ask |
| POST | /v1/workspaces | Tạo phiên (trống, từ hội thoại, mẫu, lượt chạy) | 02-01 | ask |
| GET | /v1/workspaces/{id} | Chi tiết phiên kèm ô | 02-01 | ask |
| PATCH | /v1/workspaces/{id} | Sửa tiêu đề, tham số | 02-01 | ask |
| POST | /v1/workspaces/{id}/tiles | Thêm ô | 02-01, 02-06 | ask |
| PATCH | /v1/tiles/{id} | Sửa ô, chấp nhận hoặc bỏ ô agent đề xuất | 02-01, 02-06 | ask |
| POST | /v1/tiles/{id}:run | Chạy lại một ô | 02-06 | ask |
| POST | /v1/workspaces/{id}:refresh | Làm mới cả phiên theo thứ tự phụ thuộc; 202 | 02-02 | ask |
| PUT | /v1/workspaces/{id}/shares | Chia sẻ có hạn | 02-03 | ask |
| DELETE | /v1/workspaces/{id}/shares/{principal\_id} | Thu hồi chia sẻ | 02-03 | ask |
| POST | /v1/workspaces/{id}/participants | Mời người hoặc agent vào phiên | 02-06 | ask |
| POST | /v1/tiles/{id}/comments | Bình luận, @nhắc, giao việc | 02-05 | ask |
| PATCH | /v1/comments/{id} | Sửa bình luận, đánh dấu xong | 02-05 | ask |
| POST | /v1/workspaces/{id}/reports | Tạo báo cáo từ phiên | 02-07 | ask |
| POST | /v1/session-reports/{id}:check | Chạy bốn kiểm tra trước phát hành | 02-07 | ask |
| POST | /v1/session-reports/{id}:publish | Gửi duyệt và phát hành báo cáo | 02-07 | approve |
| GET | /v1/session-reports | Báo cáo của một phiên (thêm 12/10/2026) | 02-07 | ask |
| GET | /v1/session-reports/{id} | Một báo cáo của phiên: mục, căn cứ từng số, kết quả bốn kiểm tra (thêm 12/10/2026) | 02-07 | ask |
| GET | /v1/session-reports/{id}/document | Đọc nội dung markdown của báo cáo (thêm 13/10/2026) | 02-07 | ask |
| GET | /v1/workspaces/{id}/shares | Danh sách chia sẻ hiện có của phiên (thêm 12/10/2026) | 02-03 | ask |
| POST | /v1/workspaces/{id}/shares:preview | Xem trước mỗi người nhận thấy gì theo quyền của họ, chưa lưu (thêm 12/10/2026) | 02-03 | ask |
| GET | /v1/tiles/{id}/comments | Bình luận và việc được giao của một ô (thêm 12/10/2026) | 02-05 | ask |

### Kỹ năng

Dịch vụ: Skill service (skill). 14 endpoint.

| Phương thức | Đường dẫn | Chức năng | Use case | Quyền |
| --- | --- | --- | --- | --- |
| GET | /v1/skills | Thư viện kỹ năng | 03-01 | ask |
| POST | /v1/skills | Tạo kỹ năng (tay hoặc từ phiên) | 03-03 | create\_skill |
| GET | /v1/skills/{id} | Chi tiết kỹ năng và phiên bản | 03-01 | ask |
| POST | /v1/skills/{id}/runs | Chạy kỹ năng; 202 | 03-01, 03-02 | ask |
| GET | /v1/skills/{id}/runs | Lịch sử lượt chạy kỹ năng | 03-01 | ask |
| GET | /v1/skill-runs/{id} | Kết quả lượt chạy kỹ năng | 03-01, 03-02 | ask |
| POST | /v1/skills/{id}/versions | Tạo phiên bản mới | 03-03 | create\_skill |
| PATCH | /v1/skill-versions/{id} | Sửa phiên bản nháp | 03-03 | create\_skill |
| POST | /v1/skill-versions/{id}:test | Chạy kiểm định; 202 | 03-04 | create\_skill |
| POST | /v1/skill-versions/{id}:publish | Gửi duyệt phát hành | 03-05 | create\_skill |
| POST | /v1/skill-versions/{id}:retire | Ngừng dùng, chỉ định thay thế | 03-06 | approve |
| POST | /v1/skills/{id}:share | Đổi phạm vi chia sẻ | 03-07 | create\_skill |
| GET | /v1/skill-suggestions | Kỹ năng agent đề xuất | 03-07 | ask |
| POST | /v1/skill-suggestions/{id}:accept | Nhận đề xuất | 03-07 | create\_skill |
| POST | /v1/skill-suggestions/{id}:dismiss | Bỏ đề xuất 90 ngày | 03-07 | ask |
| POST | /v1/skill-versions/{id}:try | Chạy thử một lần phiên bản nháp với tham số, không ghi kết quả (thêm 12/10/2026) | 03-03, 03-04 | create\_skill |
| GET | /v1/tools | Danh mục công cụ theo bốn mức quyền (thêm 12/10/2026) | 03-01, 13-02 | ask |

### Nguồn và danh mục

Dịch vụ: Connector service (catalog). 17 endpoint. Hai endpoint chứng nhận dataset hợp nhất (thêm 10/10/2026, BR-156) nằm ở tệp OpenAPI bổ sung cho tới lần gộp (A4); chỉ người phụ trách dữ liệu (steward\_id hoặc backup\_steward\_id) của Data Space chứa dataset gọi được, ngoài năng lực steward.

| Phương thức | Đường dẫn | Chức năng | Use case | Quyền |
| --- | --- | --- | --- | --- |
| GET | /v1/connectors | Danh mục connector cho màn Kết nối dữ liệu (thêm 13/10/2026) | 04-01 | admin\_data |
| GET | /v1/secret-refs | Các reference OpenBao đã dùng trong tổ chức, không trả bí mật (thêm 13/10/2026) | 04-01 | admin\_data |
| GET | /v1/sources | Danh sách nguồn | 04-01 | admin\_data |
| GET | /v1/sources/{id} | Một nguồn: chế độ, kết quả kiểm tra, giới hạn tải, lịch quét; không có bí mật (thêm 12/10/2026) | 04-01 | admin\_data |
| GET | /v1/sources/{id}/api-config | Đọc cấu hình request và mapping của nguồn REST; không có bí mật | 04-01 | admin\_data |
| POST | /v1/sources | Đăng ký nguồn | 04-01 | admin\_data |
| PUT | /v1/sources/{id}/api-config | Lưu cấu hình request và mapping của nguồn REST | 04-01 | admin\_data |
| PATCH | /v1/sources/{id} | Sửa nguồn, giới hạn tải | 04-01 | admin\_data |
| POST | /v1/sources/{id}:test | Kiểm tra kết nối và quyền tài khoản | 04-01 | admin\_data |
| POST | /v1/sources/{id}:activate | Chọn bảng và kích hoạt nguồn sau lượt quét cấu trúc đầu (thêm 13/10/2026) | 04-01, 04-04 | admin\_data |
| PUT | /v1/sources/{id}/scan-schedule | Đặt lịch quét | 04-05 | admin\_data |
| POST | /v1/sources/{id}/scans | Quét ngay; 202 | 04-02 | admin\_data |
| GET | /v1/scan-runs/{id} | Tiến độ lượt quét | 04-02 | admin\_data |
| GET | /v1/datasets | Danh mục bảng | 04-04 | ask |
| GET | /v1/datasets/{id} | Bảng, cột, hồ sơ dữ liệu | 04-02 | ask |
| PATCH | /v1/datasets/{id} | Bật, tắt, người phụ trách, mô tả | 04-04 | admin\_data |
| GET | /v1/datasets/{id}/versions | Lịch sử cấu trúc | 04-03 | ask |
| POST | /v1/dataset-versions/{id}:certify | Chứng nhận phiên bản dataset hợp nhất | 14-05 | steward |
| POST | /v1/dataset-versions/{id}:revoke-certification | Thu hồi chứng nhận, bắt buộc lý do | 14-05 | steward |

### Nạp dữ liệu

Dịch vụ: Ingestion service (ingest). 18 endpoint.

| Phương thức | Đường dẫn | Chức năng | Use case | Quyền |
| --- | --- | --- | --- | --- |
| POST | /v1/sources/{id}/jobs | Tạo job nạp | 04-06 | admin\_data |
| GET | /v1/jobs | Giám sát job nạp | 04-09 | admin\_data |
| PATCH | /v1/jobs/{id} | Sửa job | 04-06 | admin\_data |
| POST | /v1/jobs/{id}:pause | Tạm dừng, bắt buộc lý do | 04-09 | admin\_data |
| POST | /v1/jobs/{id}:resume | Chạy tiếp | 04-07, 04-09 | admin\_data |
| POST | /v1/jobs/{id}:backfill | Nạp lại một khoảng | 04-06 | admin\_data |
| GET | /v1/job-runs | Lượt chạy theo job | 04-09 | admin\_data |
| GET | /v1/jobs/{id}/dlq | Bản ghi lỗi | 04-07 | admin\_data |
| POST | /v1/dlq/{id}:replay | Nạp lại bản ghi lỗi | 04-07 | admin\_data |
| POST | /v1/dlq/{id}:discard | Bỏ bản ghi kèm lý do | 04-07 | admin\_data |
| POST | /v1/uploads | Tải tệp CSV, Excel (multipart) | 04-08 | upload |
| PATCH | /v1/uploads/{id} | Sửa ánh xạ cột, bảng đích | 04-08 | upload |
| POST | /v1/uploads/{id}:commit | Ghi vào bảng | 04-08 | upload |
| GET | /v1/quality-rules | Quy tắc chất lượng | 06-07 | ask |
| POST | /v1/quality-rules | Tạo quy tắc | 06-07 | admin\_data |
| PATCH | /v1/quality-rules/{id} | Sửa quy tắc | 06-07 | admin\_data |
| GET | /v1/quality-results | Kết quả kiểm tra chất lượng | 06-07 | ask |
| POST | /v1/quality-results/{id}:acknowledge | Xác nhận dùng tiếp khi vi phạm mức cao | 06-07 | approve |

### Tài liệu

Dịch vụ: Document service (document). 15 endpoint.

| Phương thức | Đường dẫn | Chức năng | Use case | Quyền |
| --- | --- | --- | --- | --- |
| GET | /v1/collections | Bộ sưu tập | 05-06 | ask |
| POST | /v1/collections | Tạo bộ sưu tập | 05-06 | admin\_data |
| POST | /v1/document-sources | Đăng ký nguồn đồng bộ | 05-01 | admin\_data |
| POST | /v1/document-sources/{id}:sync | Đồng bộ ngay | 05-01 | admin\_data |
| POST | /v1/documents | Tải tài liệu (multipart); 202 | 05-01 | upload |
| GET | /v1/documents/{id} | Trạng thái, tiến độ tiền xử lý | 05-02 | ask |
| PATCH | /v1/documents/{id} | Sửa số hiệu, hiệu lực, văn bản thay thế | 05-05 | admin\_data |
| GET | /v1/documents/{id}/pages/{n} | Trang: ảnh, văn bản, đoạn | 05-04 | ask |
| GET | /v1/documents/{id}/versions | Dòng thời gian hiệu lực | 05-05 | ask |
| POST | /v1/search/documents | Tìm lai ghép vector và từ khoá | 05-04 | ask |
| GET | /v1/quality-issues | Hàng chờ chất lượng | 05-03 | admin\_data |
| POST | /v1/quality-issues/{id}:resolve | Xử lý vấn đề | 05-03 | admin\_data, steward |
| POST | /v1/extraction-schemas | Tạo lược đồ trích xuất | 05-06 | admin\_data |
| POST | /v1/extraction-schemas/{id}:test | Chạy thử trên mẫu; 202 | 05-06 | admin\_data |
| POST | /v1/extraction-schemas/{id}:activate | Kích hoạt | 05-06 | admin\_data |
| GET | /v1/documents | Tài liệu theo bộ sưu tập và trạng thái tiền xử lý (thêm 12/10/2026) | 05-01, 05-02, 05-05 | ask |
| GET | /v1/extraction-schemas | Lược đồ trích xuất của bộ sưu tập (thêm 12/10/2026) | 05-06 | ask |
| GET | /v1/extraction-schemas/{id} | Một lược đồ trích xuất: trường, kiểu, bắt buộc, độ chính xác trên mẫu, cột đích (thêm 12/10/2026) | 05-06 | ask |
| GET | /v1/document-sources | Nguồn đồng bộ tài liệu đã đăng ký (thêm 12/10/2026) | 05-01 | ask |

### Biến đổi dữ liệu

Dịch vụ: Pipeline service (pipeline). 18 endpoint.

| Phương thức | Đường dẫn | Chức năng | Use case | Quyền |
| --- | --- | --- | --- | --- |
| GET | /v1/pipelines | Danh sách pipeline | 06-01 | ask |
| POST | /v1/pipelines | Tạo pipeline | 06-01 | prepare\_data |
| PATCH | /v1/pipelines/{id} | Sửa các bước | 06-01 | prepare\_data |
| POST | /v1/pipelines/{id}:preview | Chạy thử trên mẫu | 06-01 | prepare\_data |
| GET | /v1/pipelines/{id}/steps/{seq}/preview | Xem trước một bước | 06-01 | prepare\_data |
| POST | /v1/pipelines/{id}/messages | Gửi yêu cầu cho agent chuẩn bị dữ liệu | 06-01 | prepare\_data |
| POST | /v1/pipelines/{id}:materialize | Lưu thành bảng | 06-02 | prepare\_data |
| PUT | /v1/pipelines/{id}/schedule | Đặt lịch | 06-03 | prepare\_data |
| GET | /v1/pipelines/{id}/schedule | Đọc lịch pipeline | 06-03 | ask |
| POST | /v1/pipelines/{id}/schedule:preview | Xem trước lịch pipeline | 06-03 | prepare\_data |
| GET | /v1/pipelines/{id}/output-draft | Đọc bản nháp bảng đầu ra | 06-02 | ask |
| GET | /v1/lineage | Dòng dữ liệu của một đối tượng | 06-05 | ask |
| POST | /v1/marts | Tạo data mart | 06-06 | admin\_data |
| POST | /v1/data-products | Tạo sản phẩm dữ liệu | 06-06 | admin\_data |
| POST | /v1/data-products/{id}:publish | Phát hành | 06-06 | approve |
| POST | /v1/writebacks | Tạo yêu cầu ghi ngược (dry\_run để xem diff) | 06-04 | writeback |
| POST | /v1/writebacks/{id}:submit | Gửi duyệt | 06-04 | writeback |
| POST | /v1/writebacks/{id}:undo | Hoàn tác trong 30 ngày | 06-04 | writeback |
| GET | /v1/pipelines/{id} | Một pipeline: bước, lịch, lần chạy gần nhất (thêm 12/10/2026) | 06-02, 06-03 | ask |
| GET | /v1/writebacks/{id} | Một yêu cầu ghi ngược: diff, mẫu trước sau, bước ghi, người duyệt, trạng thái (thêm 12/10/2026) | 06-04 | writeback |
| GET | /v1/marts | Danh sách data mart (thêm 12/10/2026) | 06-06 | ask |
| GET | /v1/marts/{id} | Một data mart: bảng sự kiện, chiều, quan hệ, kiểm tra, chiều agent gợi ý (thêm 12/10/2026) | 06-06 | ask |
| PATCH | /v1/marts/{id} | Sửa mart: thêm chiều, thước đo, quan hệ (kể cả nhận gợi ý của agent) (thêm 12/10/2026) | 06-06 | admin\_data |

### Lớp ngữ cảnh

Dịch vụ: Context service (context). 39 endpoint, trong đó 16 endpoint lớp tri thức (/v1/knowledge\*) và 11 endpoint data product (/v1/data-products\*).

| Phương thức | Đường dẫn | Chức năng | Use case | Quyền |
| --- | --- | --- | --- | --- |
| GET | /v1/metrics | Danh sách chỉ số | 07-01 | ask |
| POST | /v1/metrics | Tạo chỉ số | 07-01 | steward |
| GET | /v1/metrics/{id} | Chỉ số và các phiên bản | 07-01 | ask |
| POST | /v1/metrics/{id}/versions | Tạo phiên bản nháp | 07-01 | steward |
| POST | /v1/metric-versions/{id}:preview | Chạy thử, so với bản đang dùng và số đã nộp | 07-01 | steward |
| POST | /v1/relationships | Khai báo quan hệ | 07-02 | steward |
| POST | /v1/synonyms | Thêm từ đồng nghĩa | 07-02 | steward |
| POST | /v1/examples | Thêm câu hỏi mẫu | 07-02 | steward |
| GET | /v1/terms | Từ điển nghiệp vụ | 07-09 | ask |
| POST | /v1/terms | Thêm thuật ngữ | 07-09 | steward |
| GET | /v1/unmapped-phrases | Cụm từ chưa ánh xạ | 07-09 | steward |
| PUT | /v1/me/term-preferences | Cách hiểu riêng của tôi | 07-09 | ask |
| GET | /v1/knowledge/index | Mục lục tri thức cho planner và người dùng | 07-10, 07-12 | ask |
| GET | /v1/knowledge-pages | Danh sách trang tri thức | 07-10 | steward |
| POST | /v1/knowledge-pages | Tạo trang (trạng thái draft) | 07-10 | steward |
| GET | /v1/knowledge-pages/{id} | Đọc trang đang phục vụ, số điền theo quyền người đọc | 07-10, 07-12 | ask |
| GET | /v1/knowledge-pages/{id}/versions | Lịch sử phiên bản của trang | 07-10 | ask |
| GET | /v1/knowledge-page-versions/{id} | Đọc một phiên bản (kể cả đã thay) để mở lại trích dẫn | 07-10, 07-11 | ask |
| POST | /v1/knowledge-pages/{id}/versions | Tạo phiên bản nháp có nguồn, khẳng định, ràng buộc số | 07-10, 07-11 | steward |
| POST | /v1/knowledge-page-versions/{id}:submit | Gửi duyệt; tạo đề xuất knowledge\_page trong Cần bạn | 07-10, 07-12 | steward |
| POST | /v1/knowledge-page-versions/{id}:certify | Chứng nhận phiên bản đã duyệt | 07-10 | steward |
| POST | /v1/knowledge-pages/{id}:retire | Ngừng trang; không xoá | 07-11 | steward |
| POST | /v1/knowledge-pages:from-answer | Lưu câu trả lời thành trang hoặc đoạn, qua duyệt | 07-12 | ask |
| POST | /v1/knowledge-runs | Chạy biên soạn hoặc rà soát ngay | 07-10, 07-11 | steward |
| GET | /v1/knowledge-runs | Danh sách lượt biên soạn, rà soát | 07-10, 07-11 | steward |
| GET | /v1/knowledge-runs/{id} | Một lượt: số nguồn bỏ qua, trang sửa, trang dời, phát hiện | 07-10, 07-11 | steward |
| GET | /v1/knowledge-findings | Danh sách phát hiện rà soát | 07-11 | steward |
| POST | /v1/knowledge-findings/{id}:resolve | Xử lý phát hiện bằng một hành động cố định | 07-11 | steward |
| GET | /v1/data-products/index | Danh mục phạm vi cho Understander (dataproduct.match) | 07-13, 01-01 | ask |
| GET | /v1/data-products | Danh sách data product | 07-13 | ask |
| POST | /v1/data-products | Tạo data product (draft) | 07-13 | steward |
| GET | /v1/data-products/{id} | Phạm vi, thành phần, cam kết, trạng thái | 07-13 | ask |
| PATCH | /v1/data-products/{id} | Sửa bản draft; bản active tạo phiên bản mới qua duyệt | 07-13 | steward |
| POST | /v1/data-products/{id}/members | Thêm thành phần và cam kết độ tươi | 07-13 | steward |
| DELETE | /v1/data-products/{id}/members/{member\_id} | Gỡ thành phần | 07-13 | steward |
| POST | /v1/data-products/{id}:submit | Chạy bộ đánh giá, gửi duyệt kích hoạt | 07-13 | steward |
| POST | /v1/data-products/{id}:suspend | Tạm ngưng, câu hỏi trả lời ngoài phạm vi | 07-13 | steward |
| POST | /v1/data-products/{id}:retire | Ngừng data product; không xoá, biên nhận cũ vẫn đọc được | 07-13 | steward |
| GET | /v1/data-products/{id}/freshness | Độ tươi từng dataset so cam kết (freshness.check) | 07-13, 01-07 | ask |
| GET | /v1/entities | Mô hình thực thể: thực thể, khoá, bảng nguồn, quan hệ kèm bản số (thêm 12/10/2026) | 07-02 | ask |
| GET | /v1/entities/{key} | Một thực thể: định nghĩa, khoá liên kết giữa nguồn, hạt dữ liệu, chỉ số dùng nó (thêm 12/10/2026) | 07-02 | ask |
| GET | /v1/examples | Câu hỏi mẫu (đã xác nhận và gợi ý) (thêm 12/10/2026) | 07-02, 07-06 | ask |
| PATCH | /v1/examples/{id} | Sửa SQL hoặc xác nhận câu hỏi mẫu; SQL chạy lại trước khi lưu (thêm 12/10/2026) | 07-02, 07-06 | steward |
| GET | /v1/synonyms | Từ đồng nghĩa (thêm 12/10/2026) | 07-02 | ask |

### Xung đột

Dịch vụ: Conflict service (conflict). 6 endpoint.

| Phương thức | Đường dẫn | Chức năng | Use case | Quyền |
| --- | --- | --- | --- | --- |
| GET | /v1/conflicts | Danh sách xung đột | 07-04 | ask |
| GET | /v1/conflicts/{id} | So sánh hai nguồn | 07-04 | ask |
| POST | /v1/conflicts/{id}:resolve | Chọn phương án, có thể lưu thành quy tắc | 07-04 | steward |
| GET | /v1/source-authorities | Thẩm quyền nguồn theo loại dữ liệu | 07-05 | ask |
| PUT | /v1/source-authorities | Đặt thứ tự ưu tiên và ngưỡng trọng yếu | 07-05 | steward |
| POST | /v1/resolution-rules | Thêm quy tắc giải quyết | 07-04 | steward |

### Cần bạn và đánh giá

Dịch vụ: Inbox service (inbox), Eval service (eval). 16 endpoint.

| Phương thức | Đường dẫn | Chức năng | Use case | Quyền |
| --- | --- | --- | --- | --- |
| GET | /v1/inbox | Việc chờ tôi quyết định | 07-03, 07-07 | ask |
| GET | /v1/hitl-requests/{id} | Chi tiết yêu cầu | 07-07 | ask |
| POST | /v1/hitl-requests/{id}:resolve | Trả lời, duyệt, từ chối, chuyển người | 07-07, 07-08 | ask |
| GET | /v1/proposals/{id} | Đề xuất: diff, ảnh hưởng, kết quả đánh giá | 07-03 | ask |
| POST | /v1/proposals/{id}:decide | Chấp nhận, sửa rồi chấp nhận, từ chối | 07-03, 07-13, 07-14, 16-05 | approve |
| POST | /v1/eval-sets/{id}/items | Thêm mục đánh giá | 07-06 | steward |
| POST | /v1/eval-sets/{id}/runs | Chạy bộ đánh giá; 202 | 07-06 | steward |
| GET | /v1/eval-runs/{id} | Kết quả đánh giá từng mục | 07-06 | ask |
| GET | /v1/error-book-entries | Danh sách mục Error Book (errorbook.search) | 07-14 | steward |
| POST | /v1/error-book-entries | Ghi mục từ lỗi đã xác nhận (errorbook.create); dự báo lệch tạo qua sự kiện forecast.ledger.missed | 07-14, 15-06 | steward |
| GET | /v1/error-book-entries/{id} | Chi tiết một mục kèm biên nhận, cách sửa, ca hồi quy | 07-14 | steward |
| PATCH | /v1/error-book-entries/{id} | Loại lỗi, nguyên nhân gốc, người xử lý, mức | 07-14 | steward |
| POST | /v1/error-book-entries/{id}:mark-fixed | Gắn cách sửa, chuyển fixed | 07-14 | steward |
| POST | /v1/error-book-entries/{id}:add-regression | Tạo ca hồi quy origin error\_book (tự gọi khi fixed) | 07-14 | steward |
| POST | /v1/error-book-entries/{id}:dismiss | Không phải lỗi hoặc trùng mục khác | 07-14 | steward |
| POST | /v1/error-book-entries/{id}:propose-to-pack | Đề xuất cách sửa cho gói ngành, đã bỏ dữ liệu của khách | 07-14, 09-05 | steward |
| GET | /v1/eval-sets | Bộ đánh giá theo loại và chủ thể (thêm 12/10/2026) | 07-06, 13-04 | ask |
| GET | /v1/eval-sets/{id} | Một bộ đánh giá: số câu, lượt chạy, câu sai của lượt mới nhất (thêm 12/10/2026) | 07-06 | ask |

### Chính sách

Dịch vụ: Policy engine (governance). 6 endpoint.

| Phương thức | Đường dẫn | Chức năng | Use case | Quyền |
| --- | --- | --- | --- | --- |
| GET | /v1/policies | Chính sách dữ liệu và hành vi | 11-02 | ask |
| POST | /v1/policies | Tạo chính sách (nháp) | 07-08, 11-02 | admin |
| POST | /v1/policies/{id}:simulate | Mô phỏng dưới danh tính một người | 11-02 | admin |
| POST | /v1/policies/{id}:backtest | Chạy thử trên lịch sử | 07-08, 11-02 | admin |
| POST | /v1/policies/{id}:activate | Bật chính sách | 07-08, 11-02 | admin |
| POST | /v1/permissions:simulate | Xem một bảng dưới hai vai trò | 11-08 | admin |
| GET | /v1/policies/{id} | Một chính sách: điều kiện, hành động, ngoại lệ, backtest, hạn xem lại (thêm 12/10/2026) | 11-02, 07-08 | ask |
| POST | /v1/policies/{id}:disable | Tắt chính sách đang bật hoặc bỏ bản nháp, bắt buộc lý do (thêm 12/10/2026) | 11-02, 07-08 | admin |

### Theo dõi, báo cáo, thông báo

Dịch vụ: Monitor & report (monitor), Notification (notify). 29 endpoint.

| Phương thức | Đường dẫn | Chức năng | Use case | Quyền |
| --- | --- | --- | --- | --- |
| POST | /v1/monitors | Đặt theo dõi | 08-01 | ask |
| PATCH | /v1/monitors/{id} | Sửa theo dõi | 08-01 | ask |
| POST | /v1/monitors/{id}:backtest | Chạy thử luật trên 12 kỳ | 08-01 | ask |
| GET | /v1/alerts | Cảnh báo | 08-02 | ask |
| POST | /v1/alerts/{id}:ack | Xác nhận | 08-02 | ask |
| POST | /v1/alerts/{id}:assign | Giao người khác | 08-02 | ask |
| POST | /v1/alerts/{id}:close | Đóng kèm ghi chú | 08-02 | ask |
| POST | /v1/reports | Đặt báo cáo định kỳ | 02-04 | ask |
| PATCH | /v1/reports/{id} | Sửa lịch, người nhận | 02-04 | ask |
| POST | /v1/reports/{id}:pause | Tạm dừng | 02-04 | ask |
| POST | /v1/reports/{id}:run-now | Chạy ngay; 202 | 08-03 | ask |
| GET | /v1/report-runs/{id} | Kết quả một lần chạy | 08-03, 08-04 | ask |
| PUT | /v1/me/digest | Đăng ký bản tin | 08-04 | ask |
| GET | /v1/me/notifications | Thông báo của tôi | 08-07 | ask |
| GET | /v1/me/notification-prefs | Cài đặt nhận thông báo | 08-07 | ask |
| PUT | /v1/me/notification-prefs | Đổi cài đặt | 08-07 | ask |
| POST | /v1/me/notification-preferences:test | Gửi thử thông báo | 08-07 | ask |
| GET | /v1/channel-rules | Quy tắc các kênh thông báo | 08-07 | ask |
| PUT | /v1/me/channels/{channel} | Liên kết Teams, Zalo, email, di động | 08-06 | ask |
| POST | /v1/channels/{kind}/callback | Nhận thao tác người dùng trên kênh ngoài (ký HMAC) | 08-06 | channel |
| POST | /v1/tasks | Giao việc cho agent nền | 08-05 | run\_agent |
| GET | /v1/tasks/{id} | Trạng thái việc | 08-05 | run\_agent |
| POST | /v1/tasks/{id}:cancel | Huỷ việc | 08-05 | run\_agent |
| GET | /v1/external-calendars | Lịch sự kiện ngoài của Data Space | 08-08 | ask |
| POST | /v1/external-calendars | Khai báo lịch sự kiện ngoài | 08-08 | steward |
| PATCH | /v1/external-calendars/{id} | Sửa giờ bản tin, nguồn phát hiện, mô hình, trạng thái | 08-08 | steward |
| GET | /v1/external-calendars/{id}/events | Kỳ dự kiến, đã xác nhận, dời, đã diễn ra | 08-08 | ask |
| POST | /v1/external-events/{id}:confirm | Xác nhận, dời hoặc huỷ một kỳ | 08-08 | steward |
| PUT | /v1/external-calendars/{id}/subscription | Theo dõi hoặc bỏ theo dõi lịch, chọn loại bản tin và kênh | 08-08 | ask |
| GET | /v1/event-bulletins/{id} | Bản tin trước kỳ hoặc sau kỳ, kết quả cổng, trạng thái gửi | 08-08 | ask |
| POST | /v1/external-events/{id}:run-bulletin | Chạy ngay bản tin trước kỳ; 202 | 08-08 | steward |
| GET | /v1/monitors | Danh sách theo dõi (thêm 12/10/2026) | 08-01 | ask |
| POST | /v1/alerts/{id}:reopen | Mở lại cảnh báo đã đóng (thêm 12/10/2026) | 08-02 | ask |
| GET | /v1/reports | Báo cáo định kỳ (thêm 12/10/2026) | 08-03 | ask |
| GET | /v1/reports/{id} | Một báo cáo định kỳ: lịch, người nhận, lần chạy gần nhất (thêm 12/10/2026) | 08-03 | ask |

### Gói ngành

Dịch vụ: Pack service (pack). 17 endpoint.

| Phương thức | Đường dẫn | Chức năng | Use case | Quyền |
| --- | --- | --- | --- | --- |
| GET | /v1/packs | Gói ngành đã nhập | 09-01 | ask |
| POST | /v1/packs:import | Nhập tệp gói, kiểm chữ ký | 09-01 | admin |
| GET | /v1/pack-versions/{id} | Nội dung phiên bản gói | 09-01 | ask |
| POST | /v1/packs/{id}/versions | Tạo phiên bản (môi trường biên soạn) | 09-05 | pack\_author |
| POST | /v1/pack-versions/{id}:validate | Chạy kiểm tra trước phát hành; 202 | 09-05 | pack\_author |
| POST | /v1/pack-versions/{id}:publish | Ký số và phát hành | 09-05 | pack\_author |
| POST | /v1/pack-installs | Chọn gói cho Data Space | 09-02 | admin\_data |
| POST | /v1/pack-installs/{id}:suggest-mappings | Đề xuất ánh xạ; 202 | 09-02 | admin\_data |
| PUT | /v1/pack-installs/{id}/mappings | Xác nhận ánh xạ và bảng mã | 09-02 | admin\_data |
| POST | /v1/pack-installs/{id}:calibrate | Đối chiếu số; 202 | 09-02 | admin\_data |
| POST | /v1/pack-installs/{id}:activate | Gửi duyệt kích hoạt | 09-02 | admin\_data |
| POST | /v1/metric-overrides | Hiệu chỉnh chỉ số theo tổ chức | 09-03 | steward |
| POST | /v1/metric-overrides/{id}:approve | Duyệt hiệu chỉnh | 09-03 | approve |
| POST | /v1/pack-installs/{id}:upgrade | Gộp ba chiều với phiên bản mới (dry\_run) | 09-04 | admin\_data |
| PUT | /v1/pack-upgrades/{id}/decisions | Chọn phương án cho từng xung đột | 09-04 | admin\_data |
| POST | /v1/pack-upgrades/{id}:apply | Áp dụng nâng cấp | 09-04 | approve |
| POST | /v1/pack-upgrades/{id}:rollback | Quay lại trong 30 ngày | 09-04 | admin |
| POST | /v1/packs | Tạo gói riêng của tổ chức (thêm 12/10/2026) | 09-05 | pack\_author |
| GET | /v1/pack-installs/{id} | Một lần cài gói: Data Space, tiến độ ánh xạ, trạng thái kích hoạt (thêm 12/10/2026) | 09-02 | ask |
| GET | /v1/pack-installs/{id}/mappings | Ánh xạ của một lần cài gói (thêm 12/10/2026) | 09-02 | ask |
| GET | /v1/calibration-runs/{id} | Kết quả đối chiếu số với số đã nộp và nguyên nhân lệch (thêm 12/10/2026) | 09-02 | ask |
| GET | /v1/metric-overrides/{id} | Một hiệu chỉnh chỉ số: công thức gốc và của tổ chức, lý do, chạy thử, trạng thái duyệt (thêm 12/10/2026) | 09-03 | ask |
| GET | /v1/pack-upgrades/{id} | Một lần nâng cấp gói: xung đột gộp ba chiều, quyết định, chạy thử, trạng thái (thêm 12/10/2026) | 09-04 | ask |

### Kỳ chốt

Dịch vụ: Period service (period). 10 endpoint.

| Phương thức | Đường dẫn | Chức năng | Use case | Quyền |
| --- | --- | --- | --- | --- |
| GET | /v1/periods | Danh sách kỳ | 10-02 | ask |
| GET | /v1/periods/{id} | Kỳ: trạng thái, bản, tệp | 10-02 | ask |
| POST | /v1/periods/{id}:close | Chốt kỳ; 202 | 10-02 | steward |
| GET | /v1/periods/{id}/reconciliation | Đối soát từng chỉ tiêu | 10-02 | ask |
| POST | /v1/periods/{id}/exceptions | Xin ngoại lệ cho chỉ tiêu lệch | 10-02 | steward |
| POST | /v1/periods/{id}:publish | Đánh dấu đã nộp, đóng băng số | 10-03 | steward |
| GET | /v1/periods/{id}/retro-changes | Thay đổi hồi tố | 10-04 | ask |
| POST | /v1/retro-changes/{id}:decide | Tạo bản mới hoặc không cần, kèm lý do | 10-04 | steward |
| GET | /v1/periods/compare | So sánh hai bản hoặc hai thời điểm | 10-05 | ask |
| GET | /v1/periods/compare/{id}/rows | Dòng gây chênh | 10-05 | view\_detail |

### Quản trị

Dịch vụ: Admin service (org, governance). 27 endpoint.

| Phương thức | Đường dẫn | Chức năng | Use case | Quyền |
| --- | --- | --- | --- | --- |
| GET | /v1/admin/users | Người dùng và vai trò hiệu lực | 11-01 | admin |
| GET | /v1/admin/users/{id} | Chi tiết, nguồn gốc từng quyền | 11-01 | admin |
| PUT | /v1/admin/group-mappings | Ánh xạ nhóm sang vai trò | 11-01 | admin |
| POST | /v1/admin/emergency-access | Cấp quyền khẩn cấp 24 giờ, cần người thứ hai | 11-01 | admin |
| GET | /v1/data-spaces | Data Space tôi được vào | 11-08 | ask |
| GET | /v1/me | Hồ sơ của tôi: danh tính, vai trò và năng lực toàn tổ chức, Data Space mặc định | 11-01 | — |
| POST | /v1/data-spaces | Tạo Data Space | 11-08 | admin |
| PATCH | /v1/data-spaces/{id} | Sửa Data Space, chính sách mặc định | 11-08 | admin |
| PUT | /v1/data-spaces/{id}/members | Thành viên, vai trò, phạm vi dòng | 11-08 | steward |
| PUT | /v1/data-spaces/{id}/sources | Gán nguồn | 04-04 | admin\_data |
| POST | /v1/access-requests | Yêu cầu quyền | 11-07 | — |
| POST | /v1/access-requests/{id}:decide | Duyệt, thu hẹp, từ chối | 11-07 | steward |
| GET | /v1/access-requests | Danh sách yêu cầu quyền của người gửi hoặc steward | 11-07 | — |
| GET | /v1/access-requests/{id} | Một yêu cầu quyền trong Data Space hiện tại | 11-07 | — |
| GET | /v1/admin/models | Mô hình theo tác vụ | 11-05 | admin |
| PUT | /v1/admin/models/{task} | Đổi mô hình (chạy bộ đánh giá trước) | 11-05 | admin |
| PUT | /v1/admin/ai-settings/{key} | Quy định AI | 11-05 | admin |
| GET | /v1/admin/system/health | Tình trạng dịch vụ, GPU, hàng đợi | 11-04 | admin |
| GET | /v1/calendars | Lịch làm việc | 02-04 | ask |
| PUT | /v1/calendars/{id} | Sửa lịch, ngày nghỉ | 02-04 | admin |
| POST | /v1/dsr | Mở yêu cầu của chủ thể dữ liệu | 11-06 | compliance |
| GET | /v1/dsr/{id} | Vị trí tìm thấy và hành động đề xuất | 11-06 | compliance |
| POST | /v1/dsr/{id}:execute | Thực hiện xoá, cung cấp | 11-06 | compliance |
| GET | /v1/admin/model-providers | Provider mô hình tại chỗ và frontier | 11-05 | admin |
| POST | /v1/admin/model-providers | Khai báo provider (frontier mặc định tắt) | 11-05 | admin |
| PATCH | /v1/admin/model-providers/{id} | Sửa, bật tắt; đổi endpoint hoặc khoá thì cần duyệt lại | 11-05 | admin |
| POST | /v1/admin/model-providers/{id}:approve | AC06 duyệt bật provider frontier | 11-05 | compliance |
| POST | /v1/admin/model-providers/{id}:test | Gọi thử với prompt mẫu công khai | 11-05 | admin |
| GET | /v1/admin/model-route-decisions | Nhật ký định tuyến và lời gọi frontier | 11-05, 01-07 | compliance |
| GET | /v1/principals | Tìm người, nhóm AD, agent để mời hoặc chia sẻ (thêm 12/10/2026) | 02-03, 11-08, 11-01 | ask |
| GET | /v1/admin/roles | Vai trò và năng lực của từng vai trò (thêm 12/10/2026) | 11-01, 11-08 | admin |
| GET | /v1/admin/groups | Nhóm danh tính và số thành viên | 11-01 | admin |
| GET | /v1/data-spaces/{id}/members | Thành viên Data Space: vai trò, phạm vi dòng, hạn dùng (thêm 12/10/2026) | 11-08 | steward |
| GET | /v1/data-spaces/{id}/permissions | Ma trận vai trò và năng lực trong Data Space | 11-08 | steward |
| GET | /v1/data-products/catalog | Catalog sản phẩm đã phát hành của Data Space khác | 06-06, 11-08 | ask |
| POST | /v1/data-products:request-use | Tạo AccessRequest chờ Data Space sở hữu sản phẩm quyết định | 07-13, 11-08 | ask |
| GET | /v1/admin/ai-settings | Quy định AI hiện hành (thêm 12/10/2026) | 11-05 | admin |
| GET | /v1/admin/agent-usage | Vận hành agent theo ngày và theo agent: lượt chạy, cần người, lỗi, thời gian, chi phí so ngân sách (thêm 12/10/2026) | 11-04, 13-06 | admin |
| POST | /v1/schedules:preview | Các lần chạy tới của một lịch chưa lưu, theo lịch làm việc và ngày nghỉ (thêm 12/10/2026) | 06-03, 04-05, 02-04 | ask |
| GET | /v1/dsr | Yêu cầu của chủ thể dữ liệu (thêm 12/10/2026) | 11-06 | compliance |

### Kiểm toán

Dịch vụ: Audit service (audit). 4 endpoint.

| Phương thức | Đường dẫn | Chức năng | Use case | Quyền |
| --- | --- | --- | --- | --- |
| GET | /v1/audit | Tra cứu nhật ký | 11-03 | audit\_read |
| POST | /v1/audit:verify | Kiểm toàn vẹn chuỗi băm | 11-03 | audit\_read |
| POST | /v1/audit:export | Xuất CSV có chữ ký; quá 100.000 dòng cần duyệt | 11-03 | audit\_read |
| GET | /v1/audit/exports/{id} | Trạng thái một bản xuất nhật ký | 11-03 | audit\_read |
| GET | /v1/security-events | Sự kiện bảo mật | 11-03 | audit\_read |

### Tích hợp

Dịch vụ: Gateway, MCP server (integration). 9 endpoint.

| Phương thức | Đường dẫn | Chức năng | Use case | Quyền |
| --- | --- | --- | --- | --- |
| POST | /v1/admin/clients | Cấp client REST hoặc MCP | 12-01 | admin |
| PATCH | /v1/admin/clients/{id} | Sửa công cụ, hạn mức, hạn dùng | 12-01 | admin |
| POST | /v1/admin/clients/{id}:rotate-secret | Xoay bí mật | 12-01 | admin |
| POST | /v1/admin/clients/{id}:revoke | Thu hồi khẩn | 12-01 | admin |
| GET | /v1/admin/clients/{id}/usage | Sử dụng theo giờ, công cụ | 12-04 | admin |
| GET | /v1/me/clients | Client tôi phụ trách, kể cả client chờ tôi xác nhận | 12-01 | — |
| POST | /v1/me/clients/{id}:confirm | Người phụ trách đồng ý cho client hành động với quyền của mình (BR-48) | 12-01 | — |
| POST | /v1/webhooks | Đăng ký webhook | 12-03 | admin |
| GET | /v1/webhooks/{id}/deliveries | Lịch sử gửi | 12-03 | admin |
| POST | /v1/webhook-deliveries/{id}:retry | Gửi lại | 12-03 | admin |
| POST | /mcp | MCP tools/list, tools/call (JSON-RPC) | 12-02 | client |
| GET | /v1/admin/clients | Danh sách client REST, MCP kèm phạm vi, lượt gọi, hạn, trạng thái (thêm 12/10/2026) | 12-01, 12-04 | admin |

### Agent

Dịch vụ: Agent runtime (agent). 24 endpoint.

| Phương thức | Đường dẫn | Chức năng | Use case | Quyền |
| --- | --- | --- | --- | --- |
| GET | /v1/agents | Đội agent | 13-01 | ask |
| POST | /v1/agents:draft-from-text | Đề xuất cấu hình từ mô tả | 13-01 | create\_agent |
| POST | /v1/agents | Tạo agent | 13-01 | create\_agent |
| GET | /v1/agents/{id} | Agent, phiên bản đang chạy | 13-01 | ask |
| GET | /v1/agents/{id}/configs | Các phiên bản cấu hình | 13-02 | ask |
| POST | /v1/agents/{id}/configs | Lưu phiên bản cấu hình | 13-02 | create\_agent |
| PUT | /v1/agents/{id}/triggers | Đặt trigger | 13-03 | create\_agent |
| POST | /v1/agents/{id}/test-runs | Chạy thử giả lập; 202 | 13-04 | create\_agent |
| POST | /v1/agents/{id}/eval-runs | Chạy bộ thử; 202 | 13-04 | create\_agent |
| POST | /v1/agents/{id}/deployments | Triển khai theo tỷ lệ lượt | 13-05 | create\_agent |
| PATCH | /v1/deployments/{id} | Đổi tỷ lệ lượt | 13-05 | create\_agent |
| POST | /v1/deployments/{id}:rollback | Quay lui | 13-05 | create\_agent |
| GET | /v1/runs | Lượt chạy | 13-06 | ask |
| GET | /v1/runs/{id} | Lượt chạy (view=simple hoặc technical) | 13-06 | ask |
| GET | /v1/runs/{id}/events | Luồng SSE diễn biến lượt chạy | 13-06 | ask |
| GET | /v1/runs/{id}/children | Lượt con | 13-08 | ask |
| POST | /v1/runs/{id}:pause | Tạm dừng | 13-06 | run\_agent |
| POST | /v1/runs/{id}:resume | Chạy tiếp | 13-06 | run\_agent |
| POST | /v1/runs/{id}:cancel | Huỷ | 13-06 | run\_agent |
| PUT | /v1/runs/{id}/plan | Sửa kế hoạch khi tạm dừng | 13-06 | run\_agent |
| GET | /v1/agents/{id}/memories | Bộ nhớ agent | 13-07 | ask |
| POST | /v1/agents/{id}/memories | Thêm ghi nhớ | 13-07 | create\_agent |
| PATCH | /v1/memories/{id} | Sửa, vô hiệu ghi nhớ | 13-07 | create\_agent |
| DELETE | /v1/memories/{id} | Xoá ghi nhớ | 13-07 | create\_agent |
| POST | /v1/agents/{id}:lock | Khoá agent: dừng mọi lượt chạy và trigger, chuyển yêu cầu HITL đang mở cho chủ sở hữu (thêm 12/10/2026) | 13-06, 13-02 | create\_agent |
| POST | /v1/agents/{id}:unlock | Mở khoá agent (thêm 12/10/2026) | 13-06, 13-02 | create\_agent |
| POST | /v1/runs/{id}:retry | Chạy lại lượt lỗi thành lượt mới cùng đầu vào và phiên bản cấu hình (thêm 12/10/2026) | 13-06 | run\_agent |

### Thu thập

Dịch vụ: Acquisition service (acquire). 28 endpoint. Mọi POST ghi nhận Idempotency-Key (A1.5); việc chạy lâu trả 202 (A1.6).

| Phương thức | Đường dẫn | Chức năng | Use case | Quyền |
| --- | --- | --- | --- | --- |
| GET | /v1/crawl-sources | Nguồn thu thập, giám sát | 14-01, 14-06 | admin\_data, steward |
| POST | /v1/crawl-sources | Đăng ký nguồn thu thập | 14-01 | admin\_data, steward |
| GET | /v1/crawl-sources/{id} | Nguồn, schema đang dùng, lượt gần nhất | 14-01, 14-06 | admin\_data, steward |
| PATCH | /v1/crawl-sources/{id} | Sửa lịch, tốc độ, cổng duyệt | 14-01 | admin\_data, steward |
| POST | /v1/crawl-sources/{id}:legal-review | Duyệt pháp lý nguồn trước khi bật, xem lại định kỳ | 14-01 | compliance |
| POST | /v1/crawl-sources/{id}:pause | Tạm ngưng, bắt buộc lý do | 14-03, 14-06 | admin\_data, steward |
| POST | /v1/crawl-sources/{id}:resume | Chạy tiếp | 14-03, 14-06 | admin\_data, steward |
| POST | /v1/crawl-sources/{id}/runs | Chạy ngay một lượt; 202 | 14-06 | admin\_data, steward |
| GET | /v1/crawl-runs | Lượt thu | 14-06 | admin\_data, steward |
| GET | /v1/crawl-runs/{id} | Lượt thu, trang đã tải, chỉ số | 14-06 | admin\_data, steward |
| POST | /v1/crawl-sources/{id}/schema-inference | Suy ngược schema; 202 | 14-02 | admin\_data, steward |
| GET | /v1/crawl-sources/{id}/schemas | Các phiên bản schema | 14-02, 14-03 | admin\_data, steward |
| POST | /v1/crawl-schemas/{id}:test | Chạy thử trên mẫu giữ lại; 202 | 14-02 | admin\_data, steward |
| POST | /v1/crawl-schemas/{id}:approve | Duyệt schema | 14-02 | admin\_data, steward |
| POST | /v1/crawl-schemas/{id}:revert | Hoàn tác về phiên bản đúng gần nhất; 202 | 14-03 | admin\_data, steward |
| GET | /v1/schema-drifts | Sự cố thay đổi cấu trúc | 14-03 | admin\_data, steward |
| POST | /v1/schema-drifts/{id}:resolve | Xử lý sự cố | 14-03 | admin\_data, steward |
| GET | /v1/crawl-runs/{id}/records | Bản ghi trích, hàng duyệt | 14-04 | admin\_data, steward |
| POST | /v1/extracted-records/{id}:review | Xác nhận, sửa, bỏ bản ghi | 14-04 | admin\_data, steward |
| POST | /v1/extracted-records:bulk-review | Duyệt hàng loạt cùng lý do | 14-04 | admin\_data, steward |
| POST | /v1/crawl-runs/{id}:publish | Phát hành lượt sang lớp hợp nhất | 14-04 | admin\_data, steward |
| GET | /v1/consolidation-specs | Đặc tả hợp nhất | 14-05 | admin\_data, steward |
| POST | /v1/consolidation-specs | Tạo đặc tả hợp nhất | 14-05 | admin\_data, steward |
| PATCH | /v1/consolidation-specs/{id} | Sửa đặc tả hợp nhất; kích hoạt, ngừng | 14-05 | admin\_data, steward |
| POST | /v1/consolidation-specs/{id}/runs | Chạy hợp nhất; 202 | 14-05 | admin\_data, steward |
| GET | /v1/consolidation-runs/{id} | Kết quả hợp nhất | 14-05 | admin\_data, steward |
| GET | /v1/entity-links | Liên kết thực thể chờ quyết định | 14-05 | admin\_data, steward |
| POST | /v1/entity-links/{id}:decide | Xác nhận, bác liên kết thực thể | 14-05 | admin\_data, steward |
| POST | /v1/crawl-sources:suggest | Agent đề xuất nguồn thu thập từ mô tả mục tiêu, kèm kiểm robots.txt và điều khoản (thêm 12/10/2026) | 14-01 | admin\_data, steward |

### Dự báo

Dịch vụ: Forecast service (forecast). 27 endpoint. Mọi POST ghi nhận Idempotency-Key (A1.5); việc dự báo và huấn luyện trả 202 (A1.6), tiến độ qua luồng SSE.

| Phương thức | Đường dẫn | Chức năng | Use case | Quyền |
| --- | --- | --- | --- | --- |
| POST | /v1/forecasts | Tạo việc dự báo; 202, hoặc trả ngay dự báo còn hiệu lực | 15-01, 15-02 | ask |
| GET | /v1/forecasts/{id} | Việc dự báo, tiến độ, kết quả | 15-01, 15-02 | ask |
| GET | /v1/forecasts/{id}/events | Luồng SSE tiến độ dự báo | 15-01 | ask |
| POST | /v1/forecasts/{id}:cancel | Huỷ | 15-01 | ask |
| GET | /v1/forecast-models | Mô hình dự báo | 15-04, 15-06 | ask |
| GET | /v1/forecast-models/{id} | Mô hình, phiên bản đang phát hành | 15-05 | ask |
| GET | /v1/forecast-model-versions/{id} | Phiên bản, thẻ mô hình | 15-02, 15-03, 15-04 | ask |
| GET | /v1/forecast-model-versions/{id}/backtest | Kết quả backtest so mô hình chuẩn | 15-03, 15-04 | ask |
| POST | /v1/forecast-model-versions/{id}:submit | Gửi duyệt | 15-04 | steward |
| POST | /v1/forecast-model-versions/{id}:approve | Duyệt, phát hành mô hình | 15-04 | steward |
| POST | /v1/forecast-model-versions/{id}:reject | Từ chối, bắt buộc lý do | 15-04 | steward |
| POST | /v1/forecast-models/{id}:retrain | Huấn luyện lại; 202 | 15-06 | steward |
| POST | /v1/forecast-models/{id}/scenarios | Chạy kịch bản biến ngoại sinh | 15-05 | ask |
| GET | /v1/forecast-models/{id}/predictions | Dự báo theo kỳ, khoảng 80/95% | 15-05 | ask |
| GET | /v1/forecast-models/{id}/accuracy | Độ chính xác theo kỳ | 15-06 | ask |
| GET | /v1/formula-models | Mô hình dự báo theo công thức, phiên bản công thức chỉ số, mặt hàng | 15-07 | ask |
| POST | /v1/formula-models | Tạo mô hình công thức từ phiên bản chỉ số công thức đã chứng nhận | 15-07 | steward |
| GET | /v1/formula-models/{id} | Mô hình công thức, cửa sổ kỳ, nguồn nowcast, mô hình Quỹ | 15-07 | ask |
| GET | /v1/formula-models/{id}/components | Tham số theo ngày hiệu lực, văn bản gốc, người chứng nhận | 15-07 | ask |
| POST | /v1/formula-models/{id}/components | Đề xuất giá trị tham số mới từ văn bản | 15-07 | steward |
| POST | /v1/formula-components/{id}:certify | Chứng nhận tham số, người khác người nhập | 15-07 | steward |
| POST | /v1/formula-models/{id}/runs | Chạy dự báo Monte Carlo cho kỳ tới; 202 | 15-07, 08-08 | ask |
| GET | /v1/forecasts/{id}/distribution | Phân phối, xác suất Quỹ, phân rã kèm nguồn | 15-07 | ask |
| GET | /v1/forecast-ledger | Sổ dự báo theo mô hình, mặt hàng, kỳ, trạng thái chấm | 15-08 | ask |
| GET | /v1/forecast-ledger/{id} | Một dòng sổ: dự báo đã phát hành, kết quả, chấm điểm, mục Error Book | 15-08 | ask |
| POST | /v1/forecast-ledger/{id}:score | Chấm lại khi giá công bố được đính chính; 202 | 15-08 | steward |
| GET | /v1/forecast-models/{id}/calibration | Chỉ số hiệu chỉnh N kỳ: độ phủ 80%, 95%, MAE, CRPS, số kỳ miss | 15-08 | ask |

### Lớp riêng lãnh đạo

Dịch vụ: Exec vault service (exec). 28 endpoint. Năng lực mới exec\_vault trong org.role.capabilities, do Quản trị hệ thống cấp cho thành viên ban điều hành; năng lực này chỉ mở kho của chính người gọi. Mọi nội dung đi qua API ở dạng bản mã do thiết bị tạo (BR-189); máy chủ không có endpoint giải mã. Mọi POST ghi nhận Idempotency-Key (A1.5). Duyệt trang giả thuyết dùng POST /v1/proposals/{id}:decide của nhóm Cần bạn.

| Phương thức | Đường dẫn | Chức năng | Use case | Quyền |
| --- | --- | --- | --- | --- |
| GET | /v1/exec/vault | Trạng thái kho, khoá công khai, danh sách khoá bọc (không có bản rõ) | 16-01, 16-06 | exec\_vault |
| POST | /v1/exec/vault | Tạo kho: khoá công khai, khoá riêng đã bọc, ít nhất 2 khoá bọc | 16-01 | exec\_vault |
| POST | /v1/exec/vault/key-wraps | Thêm khoá bọc (passkey, khoá phần cứng, mã giấy) | 16-01 | exec\_vault |
| DELETE | /v1/exec/vault/key-wraps/{id} | Thu hồi khoá bọc; còn ít nhất 2, một ngoại tuyến | 16-01 | exec\_vault |
| GET | /v1/exec/transit-key | Khoá công khai RSA và phiên bản khoá điều kiện của kho | 16-01 | exec\_vault |
| GET | /v1/exec/theses | Luận điểm của chủ kho: cột vận hành và bản mã | 16-01, 16-03, 16-04 | exec\_vault |
| POST | /v1/exec/theses | Ghi luận điểm (bản mã nội dung, bản mã điều kiện, due\_at) | 16-01 | exec\_vault |
| GET | /v1/exec/theses/{id} | Một luận điểm | 16-01 | exec\_vault |
| POST | /v1/exec/theses/{id}:withdraw | Rút trước hạn | 16-01 | exec\_vault |
| GET | /v1/exec/theses/{id}/evidence | Các lô bằng chứng niêm phong | 16-03 | exec\_vault |
| POST | /v1/exec/theses/{id}/evidence:refresh | Yêu cầu làm mới bằng chứng; 202 | 16-03 | exec\_vault |
| GET | /v1/exec/theses/{id}/scores | Điểm chấm của luận điểm | 16-04 | exec\_vault |
| GET | /v1/exec/scores | Mọi điểm chấm của chủ kho, để tính hiệu chỉnh trên thiết bị | 16-04 | exec\_vault |
| GET | /v1/exec/key-access-log | Sổ truy cập khoá | 16-03, 16-04, 16-06 | exec\_vault |
| GET | /v1/exec/memory-items | Mục bộ nhớ riêng (bản mã) | 16-02 | exec\_vault |
| POST | /v1/exec/memory-items | Ghi mục bộ nhớ riêng | 16-02 | exec\_vault |
| POST | /v1/exec/memory-items/{id}:shred | Huỷ một mục | 16-02 | exec\_vault |
| POST | /v1/exec/private-questions | Hỏi riêng bằng mô hình tại chỗ, không lưu bản rõ; 202 | 16-02 | exec\_vault |
| GET | /v1/exec/private-questions/{id}/stream | Luồng SSE câu trả lời hỏi riêng | 16-02 | exec\_vault |
| POST | /v1/exec/theses/{id}/publications | Tạo bản công bố, trang giả thuyết nháp và đề xuất duyệt | 16-05 | exec\_vault |
| GET | /v1/exec/publications/{id} | Bản công bố và trạng thái duyệt | 16-05 | exec\_vault |
| POST | /v1/exec/publications/{id}:withdraw | Rút bản công bố, trang chuyển retired | 16-05 | exec\_vault |
| POST | /v1/exec/handovers | Yêu cầu niêm phong, bàn giao có chọn lọc hoặc huỷ khoá | 16-06 | exec\_vault, admin |
| GET | /v1/exec/handovers/{id} | Yêu cầu và tiến độ | 16-06 | exec\_vault, admin, compliance |
| POST | /v1/exec/handovers/{id}/items | Chủ kho tải mục đã niêm phong lại cho người nhận | 16-06 | exec\_vault |
| GET | /v1/exec/handovers/{id}/items | Người nhận lấy mục bàn giao | 16-06 | exec\_vault |
| POST | /v1/exec/handovers/{id}:approve | AC06 duyệt huỷ khoá khi chủ kho không tham gia | 16-06 | compliance |
| POST | /v1/exec/handovers/{id}:accept | Người nhận xác nhận đã nhập | 16-06 | exec\_vault |

## A3. Chi tiết endpoint

Cách đọc: cột “Gửi lên” liệt kê trường thân request (dấu \* là bắt buộc) và tham số truy vấn (?tên); kiểu và ý nghĩa từng trường lấy theo cột cùng tên ở bảng tương ứng trong tab 3b D4 (từ 10/10/2026 D4 tách khỏi tab 3). Cột “Trả về” ghi schema tài nguyên: schema PascalCase là bảng CSDL cùng tên (ví dụ Question là qa.question) bỏ các cột nội bộ org\_id, created\_by, updated\_by, deleted\_at và mọi tham chiếu bí mật. “202, Location tới …” là việc chạy lâu theo A1.6. Lỗi riêng chỉ ghi lỗi đặc thù; lỗi chung (401, 403 forbidden, 404, 429…) theo A1.7. Bản đầy đủ máy đọc được là tệp OpenAPI ở A4.

### A3 · Hỏi đáp

| Endpoint | Gửi lên | Trả về | Lỗi riêng |
| --- | --- | --- | --- |
| POST /v1/questions | text\*, as\_of, knowledge\_mode, knowledge\_at, purpose, parent\_question\_id, conversation\_id, stream | 202, Location tới question | 400 data\_space\_required; 403 policy\_denied; 422 business\_rule\_violated (knowledge\_mode = at thiếu knowledge\_at); 429 rate\_limited |
| GET /v1/questions/{id} | — | Question. Kèm plan hiện hành và answer\_id khi đã trả lời | 404 not\_found |
| GET /v1/questions/{id}/stream | — | Luồng SSE. Sự kiện understood, progress, clarification, answer, error, done | 404 not\_found |
| PATCH /v1/questions/{id}/plan | chips\*, edit\_note | 202, Location tới plan. Tạo phiên bản plan mới và chạy lại; mỗi chip ghi nguồn gốc người sửa | 409 invalid\_state (câu hỏi đang chạy); 422 validation\_failed (chip không ánh xạ được) |
| GET /v1/conversations | ?q, ?from, ?to | Danh sách Conversation | — |
| GET /v1/answers/{id} | — | Answer. figures kèm query\_run\_id, metric\_version\_id; số ngoài quyền đã bị che theo chính sách | 404 not\_found |
| GET /v1/answers/{id}/lineage | ?figure | JSON. Cây truy nguồn: chỉ số và phiên bản, bảng, cột, bộ lọc, lượt chạy SQL, kỳ chốt, mã băm (UC-01-02) | 403 policy\_denied (bước ngoài quyền hiện “ngoài quyền”, không lộ tên bảng) |
| POST /v1/answers/{id}/feedback | rating\*, error\_kind, expected\_value, comment | Feedback | 422 validation\_failed (rating = wrong thiếu error\_kind) |
| PATCH /v1/feedback/{id} | status\*, reason | Feedback | 409 invalid\_state; 412 precondition\_failed |
| POST /v1/feedback/{id}:to-eval | eval\_set\_id\* | EvalItem | 409 invalid\_state (chưa confirmed) |
| POST /v1/answers/{id}/exports | format\*, include\_detail\_rows | 202, Location tới export\_job. Có dòng chi tiết chứa dữ liệu cá nhân thì chuyển waiting\_approval và tạo yêu cầu duyệt (BR-85) | 403 policy\_denied; 422 business\_rule\_violated (vượt giới hạn dòng, BR-86) |
| GET /v1/exports/{id} | — | ExportJob. Khi ready trả kèm download\_url ký có hạn 15 phút; liên kết hết hạn sau 24 giờ | 410 gone (đã hết hạn) |
| POST /v1/explore/queries | metric\_ids\*, dimensions, filters, as\_of, compare | QueryRun. Khám phá theo chỉ số không qua mô hình ngôn ngữ; semantic compiler sinh SQL trực tiếp | 422 business\_rule\_violated (chiều không hợp lệ với chỉ số) |
| GET /v1/answers/{id}/receipt | — | AnswerReceipt: receipt\_no, data product và phiên bản, answered\_at, compiler\_version, runtime\_version, models, frontier\_used, freshness, receipt\_sha256, figures (AnswerReceiptFigure, số che theo quyền người xem) | 404 not\_found (câu trả lời ngoài phạm vi, không có biên nhận; hoặc người xem không xem được câu trả lời) |
| GET /v1/receipts/{receipt\_no} | ?verify | AnswerReceipt; ?verify=true tính lại SHA-256 và trả hash\_match | 404 not\_found; 409 conflict (hash\_match = false, ghi security\_event) |
| POST /v1/receipts/{id}:replay | mode (figures \| figures\_and\_text), reason | 202, Location tới receipt-replays/{id}; chạy với quyền người gọi, knowledge\_at = answered\_at (BR-177) | 403 policy\_denied (thiếu quyền trên một dataset của biên nhận); 410 gone (quá retain\_until) |
| GET /v1/receipt-replays/{id} | — | ReceiptReplay: status, outcome (identical, differs\_explained, differs, not\_reproducible), figure\_results, cause\_summary, replay\_text kèm nhãn câu chữ có thể khác, error\_book\_entry\_id | — |

### A3 · Truy vấn

| Endpoint | Gửi lên | Trả về | Lỗi riêng |
| --- | --- | --- | --- |
| GET /v1/query-runs/{id} | — | QueryRun | 404 not\_found |
| GET /v1/query-runs/{id}/sql | — | JSON. Trả {sql\_executed, policies\_applied}; sql\_generated chỉ trả cho vai trò có view\_detail | 403 forbidden |
| GET /v1/query-runs/{id}/rows | ?cursor | Danh sách JSON. Tối đa 10.000 dòng mỗi lượt chạy; đã che theo chính sách người gọi | 403 forbidden; 410 gone (quá 30 ngày) |
| POST /v1/query-runs | dataset\_id hoặc sql, filters, limit, as\_of | 202, Location tới query\_run; dòng đọc qua GET /v1/query-runs/{id}/rows. SQL chỉ đọc, viết lại theo chính sách trước khi chạy (BR-45) | 422 validation\_failed (SQL có lệnh ghi hoặc nhiều câu, BR-22); 404 not\_found (bảng ngoài quyền) |

### A3 · Phiên làm việc

| Endpoint | Gửi lên | Trả về | Lỗi riêng |
| --- | --- | --- | --- |
| GET /v1/workspaces | ?owner, ?status | Danh sách Workspace | — |
| POST /v1/workspaces | title\*, description, params, created\_from, created\_from\_ref, template\_of\_skill\_id | Workspace | — |
| GET /v1/workspaces/{id} | — | Workspace. Kèm danh sách tile, participants, quyền của người gọi | 404 not\_found |
| PATCH /v1/workspaces/{id} | title, description, params, status | Workspace | 412 precondition\_failed |
| POST /v1/workspaces/{id}/tiles | kind\*, title, spec\*, depends\_on, position | Tile | 422 business\_rule\_violated (quá 200 ô) |
| PATCH /v1/tiles/{id} | title, spec, depends\_on, position, agent\_suggestion\_state | Tile | 412 precondition\_failed |
| POST /v1/tiles/{id}:run | — | 202, Location tới tile\_snapshot | — |
| POST /v1/workspaces/{id}:refresh | as\_of | 202, Location tới tile\_snapshot. Chạy theo thứ tự depends\_on; ô lỗi không chặn ô độc lập | — |
| PUT /v1/workspaces/{id}/shares | shares\* | Danh sách WorkspaceShare. Người xem thấy số theo quyền của chính họ (BR-89) | — |
| DELETE /v1/workspaces/{id}/shares/{principal\_id} | — | 204 | — |
| POST /v1/workspaces/{id}/participants | principal\_type\*, principal\_id\*, role\* | WsParticipant | 403 policy\_denied (agent vượt quyền chủ sở hữu) |
| POST /v1/tiles/{id}/comments | body\*, parent\_id, mentions, assignee\_id, due\_at | WsComment. Nhắc agent trong mentions tạo lượt chạy | — |
| PATCH /v1/comments/{id} | body, status, assignee\_id, due\_at | WsComment | 403 forbidden (không phải tác giả hoặc người được giao) |
| POST /v1/workspaces/{id}/reports | title\*, tile\_ids | SessionReport | — |
| POST /v1/session-reports/{id}:check | — | 202, Location tới session\_report. Bốn kiểm tra: số có căn cứ, khớp số đã nộp, quyền người nhận, dữ liệu cá nhân | — |
| POST /v1/session-reports/{id}:publish | recipients | SessionReport | 409 invalid\_state (chưa qua kiểm tra); 422 business\_rule\_violated (có số không căn cứ) |
| GET /v1/session-reports | ?workspace\_id, ?status, ?cursor | Danh sách SessionReport | — |
| GET /v1/session-reports/{id} | — | SessionReport (nơi Location của :check trỏ tới) | 404 not\_found |
| GET /v1/session-reports/{id}/document | — | Nội dung markdown của SessionReport do worker báo cáo lưu trong MinIO, qua quyền của phiên | 404 not\_found; 503 source\_unavailable (kho tệp chưa cấu hình hoặc không tới được) |
| GET /v1/workspaces/{id}/shares | — | Danh sách WorkspaceShare | 404 not\_found |
| POST /v1/workspaces/{id}/shares:preview | shares\* | Mỗi người nhận: ô thấy được, ô bị che và lý do (BR-89); không ghi gì | 404 not\_found |
| GET /v1/tiles/{id}/comments | ?cursor | Danh sách Comment | 404 not\_found |

### A3 · Kỹ năng

| Endpoint | Gửi lên | Trả về | Lỗi riêng |
| --- | --- | --- | --- |
| GET /v1/skills | ?category, ?scope, ?q | Danh sách Skill | — |
| POST /v1/skills | code\*, name\*, description, category\*, scope, origin, origin\_ref | Skill | 409 conflict (trùng mã) |
| GET /v1/skills/{id} | — | Skill. Kèm phiên bản đang phát hành và params\_schema | — |
| POST /v1/skills/{id}/runs | params\*, as\_of | 202, Location tới skill\_run | 422 validation\_failed (params sai params\_schema); 409 invalid\_state (kỹ năng chưa phát hành) |
| GET /v1/skills/{id}/runs | ?cursor, ?page_size | Page[SkillRun] | — |
| GET /v1/skill-runs/{id} | — | SkillRun. Kèm các bước skill\_run\_step | — |
| POST /v1/skills/{id}/versions | params\_schema\*, steps\*, template\_md, metric\_version\_ids, sandbox\_limits | SkillVersion | — |
| PATCH /v1/skill-versions/{id} | params\_schema, steps, template\_md, sandbox\_limits | SkillVersion | 409 invalid\_state (không sửa bản đã phát hành, BR-12) |
| POST /v1/skill-versions/{id}:test | — | 202, Location tới eval\_run | — |
| POST /v1/skill-versions/{id}:publish | — | SkillVersion. Tạo đề xuất skill\_publish trong Cần bạn; phạm vi toàn tổ chức cần duyệt hai bước (BR-96) | 409 invalid\_state (chưa đạt kiểm định) |
| POST /v1/skill-versions/{id}:retire | replaced\_by\_skill\_id, reason\* | SkillVersion. Ngừng sau 30 ngày chuyển tiếp (BR-97) | — |
| POST /v1/skills/{id}:share | scope\* | Skill | — |
| GET /v1/skill-suggestions | ?status | Danh sách SkillSuggestion | — |
| POST /v1/skill-suggestions/{id}:accept | — | Skill | — |
| POST /v1/skill-suggestions/{id}:dismiss | — | SkillSuggestion. Không đề xuất lại trong 90 ngày | — |
| POST /v1/skill-versions/{id}:try | params\* | 202, Location tới skill\_run (mode try; không cập nhật thư viện) | 409 invalid\_state (không ở draft); 422 validation\_failed (tham số sai params\_schema) |
| GET /v1/tools | ?level | Danh sách Tool: mã, mô tả, mức (read, write\_derived, external\_send, system\_action) | — |

### A3 · Nguồn và danh mục

| Endpoint | Gửi lên | Trả về | Lỗi riêng |
| --- | --- | --- | --- |
| GET /v1/connectors | — | Danh mục connector cho màn Kết nối dữ liệu kèm số nguồn đã nối của tổ chức; không có credential | — |
| POST /v1/connectors/rest:try | request\* | Xếp hàng kiểm tra request REST; worker đọc OpenBao và không lưu secret | 422 validation\_failed |
| GET /v1/secret-refs | — | Các reference OpenBao (secret/tenants/<org>/…) đã dùng trong tổ chức, {ref, label}; không trả secret | — |
| GET /v1/sources | ?status, ?kind | Danh sách Source | — |
| GET /v1/sources/{id} | — | Source như trong danh sách: status, status\_reason, db\_version, last\_checked\_at, has\_credentials, has\_writeback\_credentials, writeback\_tables; không có secret\_ref | 404 not\_found |
| GET /v1/sources/{id}/api-config | — | Cấu hình request REST, mapping cột và health; không trả secret | 422 business\_rule\_violated (nguồn không phải REST) |
| POST /v1/sources | name\*, kind\*, host, port, database\_name, mode, authority\_level, freshness\_sla, max\_concurrency, query\_timeout\_s, scan\_schemas, scan\_window, work\_calendar\_id, credentials, writeback\_credentials | Source. credentials được lưu vào OpenBao, không lưu CSDL (BR-14) | 409 conflict (trùng tên trong Data Space); 422 business\_rule\_violated (mode = federated với nguồn không hỗ trợ) |
| PUT /v1/sources/{id}/api-config | request\*, mapping | Cấu hình request REST, mapping cột và health; credential chỉ là secret\_ref | 422 validation\_failed (URL không dùng HTTPS); 422 business\_rule\_violated (nguồn không phải REST) |
| PATCH /v1/sources/{id} | name, authority\_level, freshness\_sla, max\_concurrency, query\_timeout\_s, scan\_schemas, scan\_window, status, credentials, writeback\_tables | Source | 412 precondition\_failed; 422 validation\_failed (giới hạn tải ngoài khoảng, BR-99) |
| POST /v1/sources/{id}:test | — | 202, Location tới source. Connector worker mở kết nối bằng tài khoản ở secret\_ref (API không giữ mật khẩu kho), kiểm TLS, SELECT, quyền ghi, độ trễ, phiên bản rồi ghi status, status\_reason, db\_version, last\_checked\_at lên source; tài khoản có quyền ghi bị cảnh báo. Sửa 12/10/2026 | 409 invalid\_state (nguồn đã lưu trữ) |
| POST /v1/sources/{id}:activate | tables\[\] {name\*, data\_space\_id\*, owner\_id\*}, schema\_scan, profiling, on\_schema\_change | Source. Bật các bảng đã chọn, tắt các bảng còn lại của nguồn, đặt người phụ trách từng bảng và lịch quét; mỗi bảng vào data\_space\_id đã chọn (UC-04-04 E1: một nguồn dùng chung cho nhiều Data Space) | 409 invalid\_state (nguồn chưa qua kiểm tra kết nối); 403 forbidden (data\_space\_id là Data Space mà người gọi không có admin\_data); 422 validation\_failed (owner\_id không phải người dùng còn hoạt động); 422 business\_rule\_violated (cột dữ liệu cá nhân chưa xác nhận che, UC-04-04 bước 3) |
| PUT /v1/sources/{id}/scan-schedule | scan\_cron, profile\_cron, scan\_window, work\_calendar\_id | Source. Trả kèm 5 lần quét kế tiếp | — |
| POST /v1/sources/{id}/scans | kind, tables | 202, Location tới scan\_run | 409 conflict (đang có lượt quét chạy, BR-103) |
| GET /v1/scan-runs/{id} | — | ScanRun | — |
| GET /v1/datasets | ?source\_id, ?enabled, ?q, ?origin | Danh sách Dataset | — |
| GET /v1/datasets/{id} | — | Dataset. Kèm column\_meta hiện hành và column\_profile mới nhất; giá trị phổ biến của cột cá nhân đã che | — |
| GET /v1/datasets/{id}/columns/{column} | — | Thống kê cột: tỷ lệ null, distinct, min/max và phân bố | 403 masked\_column; 404 not\_found |
| POST /v1/datasets/{id}:derive | filters, drop\_duplicates | Đăng ký bảng dẫn xuất; worker materialize theo outbox | 404 not\_found |
| PATCH /v1/datasets/{id} | enabled, steward\_id, display\_name, description, description\_status, business\_date\_col, knowledge\_time\_col, columns | Dataset | 422 business\_rule\_violated (bật bảng có cột cá nhân chưa xác nhận cách che, BR-102) |
| GET /v1/datasets/{id}/versions | — | Danh sách DatasetVersion | — |
| POST /v1/dataset-versions/{id}:certify | note | DatasetVersion (certified\_at, certified\_by điền), kèm DatasetVersionCertification vừa ghi; sự kiện catalog.dataset\_version.certified. Từ đây phiên bản dùng được cho số báo cáo cơ quan quản lý (BR-156) | 403 policy\_denied (người gọi không phải người phụ trách dữ liệu của Data Space chứa dataset; người chứng nhận trùng người tạo phiên bản, BR-156); 409 invalid\_state (phiên bản đã được chứng nhận); 422 business\_rule\_violated (dataset không có origin consolidated, BR-156) |
| POST /v1/dataset-versions/{id}:revoke-certification | reason\* | DatasetVersion (certified\_at, certified\_by về null), kèm DatasetVersionCertification vừa ghi; sự kiện catalog.dataset\_version.certification\_revoked: báo cáo cơ quan quản lý, báo cáo phát hành và tệp kỳ chốt đang dùng phiên bản bị gắn cờ cần xem lại (BR-156) | 403 policy\_denied (người gọi không phải người phụ trách dữ liệu của Data Space chứa dataset); 409 invalid\_state (phiên bản chưa được chứng nhận) |

### A3 · Nạp dữ liệu

| Endpoint | Gửi lên | Trả về | Lỗi riêng |
| --- | --- | --- | --- |
| POST /v1/sources/{id}/jobs | name\*, mode\*, source\_object\*, target\_dataset\_id, cursor\_column, schedule\_cron, freshness\_sla, dlq\_retention\_days, ?dry\_run | IngestionJob; ?dry\_run=true chạy thử cấu hình trên 1.000 dòng, không ghi, trả mẫu và lỗi (thêm 12/10/2026) | 422 validation\_failed (incremental thiếu cursor\_column) |
| GET /v1/jobs | ?status, ?source\_id | Danh sách IngestionJob. Kèm lượt chạy gần nhất, độ trễ, số bản ghi lỗi | — |
| PATCH /v1/jobs/{id} | name, schedule\_cron, freshness\_sla, dlq\_retention\_days | IngestionJob | — |
| POST /v1/jobs/{id}:pause | reason\* | IngestionJob. BR-106 | — |
| POST /v1/jobs/{id}:resume | — | IngestionJob | 409 invalid\_state (đang schema\_drift chưa duyệt) |
| POST /v1/jobs/{id}:backfill | from\*, to\* | 202, Location tới job\_run | 422 business\_rule\_violated (khoảng quá giới hạn của nguồn) |
| GET /v1/job-runs | ?job\_id, ?status, ?from, ?to | Danh sách JobRun | — |
| GET /v1/jobs/{id}/dlq | ?status, ?error\_kind | Danh sách DlqRecord | — |
| POST /v1/dlq/{id}:replay | — | 202, Location tới dlq\_record | 409 invalid\_state (đã khoá sau 3 lần lỗi) |
| POST /v1/dlq/{id}:discard | reason\* | DlqRecord. BR-104 | — |
| POST /v1/uploads | file\*, sheet | FileUpload. multipart/form-data; trả kết quả nhận dạng cấu trúc và kiểm tra | 413 payload\_too\_large; 422 validation\_failed (định dạng không hỗ trợ) |
| PATCH /v1/uploads/{id} | sheet, header\_row, encoding, decimal\_sep, column\_map, target\_dataset\_id, replace\_mode | FileUpload | — |
| POST /v1/uploads/{id}:commit | — | Dataset. Bảng tải tay hết hạn sau 90 ngày (BR-105) | 422 business\_rule\_violated (dòng lỗi trên 5%) |
| GET /v1/quality-rules | ?dataset\_id, ?severity | Danh sách QualityRule | — |
| POST /v1/quality-rules | dataset\_id\*, column\_name, kind\*, params, severity | QualityRule | — |
| PATCH /v1/quality-rules/{id} | params, severity, status | QualityRule | — |
| GET /v1/quality-results | ?dataset\_id, ?passed, ?from, ?to | Danh sách QualityResult | — |
| POST /v1/quality-results/{id}:acknowledge | note\* | QualityResult. Cho phép dùng tiếp dữ liệu vi phạm mức cao trong báo cáo (BR-116) | — |

### A3 · Tài liệu

| Endpoint | Gửi lên | Trả về | Lỗi riêng |
| --- | --- | --- | --- |
| GET /v1/collections | — | Danh sách Collection | — |
| POST /v1/collections | code\*, name\*, media\_kinds, purpose\*, pii\_policy, retention\_days | Collection | — |
| POST /v1/document-sources | collection\_id\*, kind\*, location\*, sync\_cron\*, credentials | DocumentSource | — |
| POST /v1/document-sources/{id}:sync | — | 202, Location tới document\_source | — |
| POST /v1/documents | file\*, collection\_id\*, doc\_no, effective\_from | 202, Location tới document. multipart/form-data; trùng mã băm trong bộ sưu tập trả lại tài liệu đã có | 413 payload\_too\_large |
| GET /v1/documents/{id} | — | Document. Kèm tiến độ từng bước tiền xử lý và số vấn đề chất lượng đang mở | — |
| PATCH /v1/documents/{id} | title, doc\_no, issued\_on, effective\_from, effective\_to, supersedes\_id | Document. Đổi hiệu lực hoặc văn bản thay thế tạo đề xuất document\_validity (UC-05-05) | — |
| GET /v1/documents/{id}/pages/{n} | — | DocumentPage. Kèm image\_url ký có hạn và các đoạn của trang | 403 purpose\_mismatch |
| GET /v1/documents/{id}/versions | — | Danh sách Document | — |
| POST /v1/search/documents | query\*, collection\_ids, as\_of, top\_k, filters | Danh sách Chunk. Kết hợp Qdrant và Postgres FTS, xếp hạng lại; chỉ đoạn còn hiệu lực tại as\_of; trả kèm điểm và vị trí trang | 403 purpose\_mismatch |
| GET /v1/quality-issues | ?kind, ?severity, ?status | Danh sách QualityIssue | — |
| POST /v1/quality-issues/{id}:resolve | resolution\*, corrected\_text | QualityIssue | 403 forbidden (vấn đề dữ liệu cá nhân cần AC06) |
| POST /v1/extraction-schemas | collection\_id\*, fields\*, target\_dataset\_id, join\_key | ExtractionSchema | — |
| POST /v1/extraction-schemas/{id}:test | sample\_document\_ids | 202, Location tới extraction\_schema | — |
| POST /v1/extraction-schemas/{id}:activate | — | ExtractionSchema | 422 business\_rule\_violated (độ chính xác dưới ngưỡng, BR-110) |
| GET /v1/documents | ?collection\_id, ?status, ?q, ?cursor | Danh sách Document kèm bước tiền xử lý hiện tại | — |
| GET /v1/extraction-schemas | ?collection\_id, ?cursor | Danh sách ExtractionSchema | — |
| GET /v1/extraction-schemas/{id} | — | ExtractionSchema (nơi Location của :test trỏ tới) | 404 not\_found |
| GET /v1/document-sources | ?collection\_id | Danh sách nguồn tài liệu, không có bí mật | — |

### A3 · Biến đổi dữ liệu

| Endpoint | Gửi lên | Trả về | Lỗi riêng |
| --- | --- | --- | --- |
| GET /v1/pipelines | ?scope, ?status | Danh sách Pipeline | — |
| POST /v1/pipelines | name\*, input\_dataset\_ids\*, steps, scope, instruction | Pipeline. instruction là mô tả bằng lời để agent đề xuất các bước | — |
| PATCH /v1/pipelines/{id} | name, steps, scope, status, on\_failure, depends\_on\_pipeline\_ids | Pipeline | 412 precondition\_failed |
| POST /v1/pipelines/{id}:preview | sample\_rows | PipelineRun. Chạy trên 10.000 dòng; trả số dòng bị loại từng bước và kiểm tra tổng | — |
| POST /v1/pipelines/{id}:materialize | table\_name\*, scope | 202, Location tới pipeline\_run | 422 business\_rule\_violated (tổng cột tiền lệch, UC-06-01 E2) |
| PUT /v1/pipelines/{id}/schedule | cron, work\_calendar\_id, trigger\_on\_job\_id, depends\_on\_pipeline\_ids, no\_overlap, on\_failure | Pipeline | — |
| GET /v1/lineage | ?object\_kind, ?object\_id, ?direction, ?depth | JSON. Đồ thị nút và cạnh từ lineage\_edge; nút ngoài quyền hiện ẩn danh | — |
| GET /v1/lineage/targets | — | Danh sách dataset trong Data Space để chọn làm đối tượng xem dòng dữ liệu | — |
| POST /v1/marts | name\*, fact\_dataset\_id\*, grain\*, measures, dimensions, relations, checks | DataMart | — |
| POST /v1/data-products | code\*, name\*, dataset\_id, data\_mart\_id, contract\*, owner\_id\* | DataProduct | — |
| POST /v1/data-products/{id}:publish | — | DataProduct | 403 policy\_denied (không phải owner\_id của sản phẩm, hoặc là người soạn); 422 business\_rule\_violated (thay đổi phá vỡ chưa báo trước 30 ngày, BR-60) |
| POST /v1/writebacks | source\_id\*, target\_table\*, from\_dataset\_id\*, write\_window; ?dry\_run | WritebackRequest | 403 forbidden (nguồn không có tài khoản ghi ngược); 422 business\_rule\_violated (có dòng xoá, BR-113) |
| POST /v1/writebacks/{id}:submit | — | WritebackRequest. Tạo đề xuất writeback; người duyệt phải khác người yêu cầu (BR-24) | — |
| POST /v1/writebacks/{id}:undo | reason\* | 202, Location tới writeback\_request | 409 invalid\_state (quá 30 ngày, chưa executed hoặc đã yêu cầu hoàn tác) |
| GET /v1/pipelines/{id} | — | Pipeline | 404 not\_found |
| GET /v1/writebacks/{id} | — | WritebackRequest (nơi Location của :undo trỏ tới) | 404 not\_found |
| GET /v1/marts | ?status, ?cursor | Danh sách DataMart | — |
| GET /v1/marts/{id} | — | DataMart | 404 not\_found |
| PATCH /v1/marts/{id} | name, dimensions, measures, relations | DataMart; kiểm quan hệ chạy lại (relation\_check\_passed) | 409 invalid\_state (mart đã retired); 422 validation\_failed |

### A3 · Lớp ngữ cảnh

| Endpoint | Gửi lên | Trả về | Lỗi riêng |
| --- | --- | --- | --- |
| GET /v1/metrics | ?q, ?status, ?certified | Danh sách Metric | — |
| POST /v1/metrics | code\*, owner\_id\*, consumer\_group, canonical\_dataset\_id, definition | Metric | 409 conflict (trùng mã) |
| GET /v1/metrics/{id} | — | Metric. Kèm danh sách phiên bản và nơi đang dùng (artifact, agent, báo cáo) | — |
| POST /v1/metrics/{id}/versions | display\_name\*, description, expression\*, filters, unit, decimals, direction, period\_semantics\*, time\_grain, allowed\_dimensions, additivity, legal\_basis, change\_note | MetricVersion | 422 business\_rule\_violated (đổi mã chỉ số đã phát hành, BR-26) |
| POST /v1/metric-versions/{id}:preview | periods | 202, Location tới eval\_run. Chạy trên 6 kỳ, so với bản đang dùng và số đã nộp; tạo đề xuất metric\_change khi gửi duyệt | — |
| POST /v1/relationships | from\_dataset\_id\*, from\_columns\*, to\_dataset\_id\*, to\_columns\*, cardinality\* | Relationship. Tạo ở trạng thái pending\_review (BR-100) | — |
| POST /v1/synonyms | phrase\*, target\_kind\*, target\_id\*, target\_value | Synonym | 409 conflict (cụm từ đã gán cho đích khác) |
| POST /v1/examples | question\*, sql\*, metric\_ids | ExampleQuery | 422 validation\_failed (SQL không chạy được) |
| GET /v1/terms | ?q | Danh sách GlossaryTerm | — |
| POST /v1/terms | term\*, definition\*, meanings, default\_meaning, legal\_basis | GlossaryTerm | — |
| GET /v1/unmapped-phrases | ?status, ?min\_count | Danh sách UnmappedPhrase | — |
| PUT /v1/me/term-preferences | preferences\* | Danh sách UserTermPref. Không áp cho báo cáo phát hành (BR-123) | — |
| GET /v1/knowledge/index | ?q, ?kind, ?metric\_code, ?entity\_key, ?limit | Danh sách KnowledgeIndexEntry, tối đa 20, xếp theo RRF của FTS và vector Qdrant knowledge\_index; chỉ trang người hỏi đủ quyền (BR-163) | — |
| GET /v1/knowledge-pages | ?status, ?kind, ?owner\_id, ?q, ?cursor | Danh sách KnowledgePage | — |
| POST /v1/knowledge-pages | kind\*, subject\_kind\*, subject\_ref, slug\*, title\*, index\_summary, tags, owner\_id\* | KnowledgePage ở trạng thái draft | 409 conflict (trùng slug hoặc chủ thể); 422 business\_rule\_violated (trang về cá nhân, BR-167) |
| GET /v1/knowledge-pages/{id} | ?as\_of, ?render | RenderedKnowledgePage: phiên bản đang phục vụ, khẳng định còn hiệu lực tại as\_of, số điền qua query.semantic theo quyền người đọc kèm query\_run\_id, nguồn, nhãn | 404 not\_found (không có hoặc người đọc thiếu quyền trên một nguồn, BR-163) |
| GET /v1/knowledge-pages/{id}/versions | ?status, ?cursor | Danh sách KnowledgePageVersion | — |
| GET /v1/knowledge-page-versions/{id} | ?render, ?diff | RenderedKnowledgePage của đúng phiên bản; ?diff=parent kèm khác biệt | 404 not\_found (thiếu quyền trên một nguồn của phiên bản, BR-163) |
| POST /v1/knowledge-pages/{id}/versions | body\*, summary, change\_note, change\_size, origin, knowledge\_run\_id, sources\*, claims\*, figures, links | KnowledgePageVersion draft; sources, claims, figures nối bằng source\_key | 409 conflict (trang đã có bản nháp mở); 422 business\_rule\_violated (số viết tay BR-162, khẳng định thiếu nguồn BR-161, lượt chạm giới hạn trang BR-166) |
| POST /v1/knowledge-page-versions/{id}:submit | — | KnowledgePageVersion pending\_review kèm proposal\_id (kind knowledge\_page); duyệt qua POST /v1/proposals/{id}:decide | 422 business\_rule\_violated (chỗ giữ số chưa ràng buộc BR-162, khẳng định không khớp thân trang BR-161) |
| POST /v1/knowledge-page-versions/{id}:certify | note | KnowledgePageVersion certified | 403 policy\_denied (người tạo hoặc người duyệt tự chứng nhận, BR-164); 422 business\_rule\_violated (nguồn ngoài chưa chứng nhận, BR-165) |
| POST /v1/knowledge-pages/{id}:retire | reason\* | KnowledgePage retired | — |
| POST /v1/knowledge-pages:from-answer | answer\_id\*, mode, target\_page\_id, title, note | KnowledgePageVersion origin saveback, đã gửi duyệt | 422 business\_rule\_violated (số không có query\_run, trích dẫn không qua kiểm, có dữ liệu cá nhân, BR-167); 403 policy\_denied (người lưu không có quyền trên trang đích) |
| POST /v1/knowledge-runs | kind\*, data\_space\_id\*, source\_kind, source\_ref, page\_limit | 202, Location tới knowledge-runs/{id} | 409 conflict (Data Space đang có lượt cùng loại) |
| GET /v1/knowledge-runs | ?kind, ?status, ?cursor | Danh sách KnowledgeRun | — |
| GET /v1/knowledge-runs/{id} | — | KnowledgeRun | — |
| GET /v1/knowledge-findings | ?run\_id, ?status, ?kind, ?severity, ?page\_id, ?cursor | Danh sách KnowledgeLintFinding | — |
| POST /v1/knowledge-findings/{id}:resolve | action\*, reason, assignee\_id | KnowledgeLintFinding sau hành động; propose\_edit trả kèm page\_version\_id bản nháp | 409 conflict (phát hiện đã xử lý); 422 validation\_failed (bỏ qua không ghi lý do, BR-166) |
| GET /v1/data-products/index | ?metric\_codes, ?dimension\_values, ?period, ?q | Danh sách DataProductScope của data product active: code, version, chỉ số, thực thể, kỳ, ngoài phạm vi; kèm match {data\_product\_id, in\_scope, reasons} khi có metric\_codes | — |
| GET /v1/data-products | ?status, ?owner\_id, ?steward\_id, ?q, ?cursor | Danh sách DataProduct kèm freshness\_state tổng | — |
| POST /v1/data-products | code\*, name\*, description\*, sample\_questions, scope\_entities, period\_grain\*, period\_from\*, period\_to, out\_of\_scope\_note, owner\_id\*, steward\_id\*, freshness\_statement\*, pack\_version\_id | DataProduct ở draft; tạo eval\_set context nếu chưa có | 409 conflict (trùng code); 422 validation\_failed (owner trùng steward) |
| GET /v1/data-products/{id} | ?version | DataProduct kèm members, freshness từng dataset primary, kết quả bộ đánh giá gần nhất, số mục Error Book đang mở | 404 not\_found |
| POST /v1/data-products/{id}:draft-version | — | Mở bản nháp version + 1 và proposal để agent/editor chỉnh sửa; bản đang phục vụ không đổi | 409 invalid\_state |
| PATCH /v1/data-products/{id} | name, description, sample\_questions, scope\_entities, period\_from, period\_to, out\_of\_scope\_note, owner\_id, steward\_id, freshness\_statement | DataProduct; với bản active trả bản nháp version + 1, bản cũ phục vụ đến khi duyệt | 409 conflict (đổi code sau khi active); 422 validation\_failed |
| POST /v1/data-products/{id}/members | member\_kind\*, member\_ref\*, role, sla\_kind, sla\_cron, sla\_event\_calendar\_code, sla\_timezone, sla\_work\_calendar\_id, sla\_max\_age, sla\_grace, as\_of\_col | DataProductMember kèm next\_due\_at, freshness\_state tính ngay | 422 business\_rule\_violated (chỉ số chưa certified, dataset không truy được theo thời điểm, dataset primary thiếu cam kết, BR-175); 409 conflict (đã là thành phần) |
| DELETE /v1/data-products/{id}/members/{member\_id} | — | 204 | 422 business\_rule\_violated (gỡ thành phần primary của bản active, phải qua phiên bản mới) |
| POST /v1/data-products/{id}:submit | note | 202 kèm eval\_run\_id; đạt thì DataProduct pending\_review và proposal\_id (kind data\_product), duyệt qua POST /v1/proposals/{id}:decide | 422 business\_rule\_violated (dưới 10 câu mẫu, tỷ lệ đạt dưới 95%, thiếu chỉ số certified hoặc dataset primary, BR-175) |
| POST /v1/data-products/{id}:suspend | reason\* | DataProduct suspended | 409 conflict (không ở active) |
| POST /v1/data-products/{id}:retire | reason\* | DataProduct retired | — |
| GET /v1/data-products/{id}/freshness | ?at, ?periods | Mỗi dataset primary: last\_as\_of, last\_loaded\_at, expected\_as\_of, due\_at, next\_due\_at, state (fresh, overdue, missing); với ?periods trả trạng thái từng kỳ (shown, masked\_stale, not\_available) theo BR-178 | — |
| GET /v1/entities | ?q | Danh sách Entity | — |
| GET /v1/entities/{key} | — | Entity | 404 not\_found |
| GET /v1/examples | ?status, ?q, ?cursor | Danh sách Example | — |
| PATCH /v1/examples/{id} | question, sql, status | Example | 422 validation\_failed (SQL không chạy được); 404 not\_found |
| GET /v1/synonyms | ?q, ?cursor | Danh sách Synonym | — |

### A3 · Xung đột

| Endpoint | Gửi lên | Trả về | Lỗi riêng |
| --- | --- | --- | --- |
| GET /v1/conflicts | ?status, ?kind, ?material | Danh sách Conflict | — |
| GET /v1/conflicts/{id} | — | Conflict. Kèm các claim tham gia, nguồn, thẩm quyền, gợi ý phương án | — |
| POST /v1/conflicts/{id}:resolve | chosen\_claim\_id, outcome\*, note, remember\_as\_rule | Conflict. remember\_as\_rule tạo resolution\_rule và behavior\_policy nguồn hitl | 409 invalid\_state (đã giải quyết) |
| GET /v1/source-authorities | ?data\_kind | Danh sách SourceAuthority | — |
| PUT /v1/source-authorities | data\_kind\*, ranking\*, materiality | SourceAuthority. Tạo bản nháp và chạy thử trên xung đột 90 ngày; có hiệu lực sau duyệt | 422 business\_rule\_violated (nguồn tải tay hoặc bên ngoài đứng trên cấp 1, BR-121) |
| POST /v1/resolution-rules | data\_kind\*, condition\*, action\* | ResolutionRule | — |

### A3 · Cần bạn và đánh giá

| Endpoint | Gửi lên | Trả về | Lỗi riêng |
| --- | --- | --- | --- |
| GET /v1/inbox | ?kind, ?risk, ?status, ?assignee | Danh sách HitlRequest. Gồm hitl\_request và proposal đang chờ người gọi | — |
| GET /v1/hitl-requests/{id} | — | HitlRequest | — |
| POST /v1/hitl-requests/{id}:resolve | decision\*, option, note, reassign\_to, remember\_as\_rule | HitlRequest | 409 invalid\_state (đã xử lý hoặc hết hạn); 403 forbidden (không phải người được giao); 422 business\_rule\_violated (bước duyệt thứ hai trùng người thứ nhất) |
| GET /v1/proposals/{id} | — | Proposal. Kèm diff, ảnh hưởng, kết quả eval\_run và các mục đổi kết quả | — |
| POST /v1/proposals/{id}:decide | decision\*, note, reviewed\_regressions, edited\_diff | Proposal | 403 forbidden (người duyệt là người đề xuất); 422 business\_rule\_violated (có mục đổi kết quả chưa xem, BR-28) |
| POST /v1/eval-sets/{id}/items | category, input\*, as\_of, expected\*, tolerance | EvalItem | — |
| POST /v1/eval-sets/{id}/runs | subject\_version | 202, Location tới eval\_run | — |
| GET /v1/eval-runs/{id} | — | EvalRun. Kèm eval\_result từng mục và so sánh với lần chạy nền | — |
| GET /v1/error-book-entries | ?status, ?error\_kind, ?source\_kind, ?data\_product\_id, ?metric\_code, ?assignee\_id, ?cursor | Danh sách ErrorBookEntry, mới nhất trước | — |
| POST /v1/error-book-entries | source\_kind\*, data\_space\_id\*, data\_product\_id, feedback\_id, answer\_id, receipt\_id, hitl\_request\_id, replay\_id, accuracy\_log\_id, forecast\_detail, lint\_finding\_id, title\*, detail, severity | ErrorBookEntry open; cùng nguồn đã có mục thì trả 200 với mục có sẵn (idempotent theo feedback\_id, accuracy\_log\_id, replay\_id) | 422 validation\_failed (thiếu tham chiếu bắt buộc theo source\_kind, BR-179) |
| GET /v1/error-book-entries/{id} | — | ErrorBookEntry kèm tóm tắt biên nhận, lần chạy lại, đề xuất sửa và trạng thái, eval\_item và kết quả CI gần nhất | 404 not\_found |
| PATCH /v1/error-book-entries/{id} | error\_kind, root\_cause, assignee\_id, severity, title | ErrorBookEntry | 409 conflict (mục đã dismissed) |
| POST /v1/error-book-entries/{id}:mark-fixed | fix\_kind\*, fix\_proposal\_id, fix\_ref, note | ErrorBookEntry fixed; tự tạo ca hồi quy như :add-regression | 422 business\_rule\_violated (thiếu loại lỗi, nguyên nhân gốc hoặc cách sửa; đề xuất chưa accepted, BR-179) |
| POST /v1/error-book-entries/{id}:add-regression | eval\_set\_id, input, expected, tolerance, as\_of | eval\_item\_id (origin error\_book, origin\_ref = id); mục sang regression\_added khi lần chạy CI kế tiếp đạt | 409 conflict (mục chưa fixed hoặc đã có ca hồi quy); 422 validation\_failed (input còn dữ liệu cá nhân, BR-74) |
| POST /v1/error-book-entries/{id}:dismiss | reason, duplicate\_of\_id (một trong hai bắt buộc) | ErrorBookEntry dismissed | 409 conflict (mục không ở open) |
| POST /v1/error-book-entries/{id}:propose-to-pack | pack\_id\*, summary\* | pack\_contribution proposed, pack\_proposal\_ref; nội dung gửi đi chỉ gồm cách sửa và ca kiểm thử đã bỏ dữ liệu khách | 409 conflict (mục chưa fixed) |
| GET /v1/eval-sets | ?subject\_type, ?subject\_id, ?cursor | Danh sách EvalSet | — |
| GET /v1/eval-sets/{id} | — | EvalSet kèm lượt chạy gần nhất | 404 not\_found |

### A3 · Chính sách

| Endpoint | Gửi lên | Trả về | Lỗi riêng |
| --- | --- | --- | --- |
| GET /v1/policies | ?type, ?scope, ?status | Danh sách BehaviorPolicy. type = access trả AccessPolicy | — |
| POST /v1/policies | type\*, definition\* | BehaviorPolicy. Tạo ở trạng thái draft | — |
| POST /v1/policies/{id}:simulate | as\_user\_id\*, sample\_sql | JSON. Trả kết quả trước và sau khi áp chính sách dưới danh tính người dùng thử | — |
| POST /v1/policies/{id}:backtest | window\_days | 202, Location tới policy\_backtest | — |
| POST /v1/policies/{id}:activate | — | BehaviorPolicy | 409 invalid\_state (chưa mô phỏng); 422 business\_rule\_violated (nới quyền mà chính sách khác đã chặn, BR-137) |
| POST /v1/permissions:simulate | dataset\_id\*, role\_ids, user\_ids | JSON. Trả cùng một bảng dưới hai vai trò hoặc hai người | — |
| GET /v1/policies/{id} | — | BehaviorPolicy hoặc AccessPolicy | 404 not\_found |
| POST /v1/policies/{id}:disable | reason\* | BehaviorPolicy hoặc AccessPolicy (status = disabled); lý do vào nhật ký kiểm toán | 409 invalid\_state (đã disabled hoặc expired) |

### A3 · Theo dõi, báo cáo, thông báo

| Endpoint | Gửi lên | Trả về | Lỗi riêng |
| --- | --- | --- | --- |
| POST /v1/monitors | metric\_id\*, scope\_filter, rule\_kind\*, rule\*, schedule\_cron\*, notify\_mode, recipients | Monitor. Trả kèm backtest; báo trên 30% số kỳ thì cảnh báo nhiễu (BR-32) | — |
| PATCH /v1/monitors/{id} | scope\_filter, rule, schedule\_cron, notify\_mode, recipients, status | Monitor | — |
| POST /v1/monitors/{id}:backtest | periods | Monitor | — |
| GET /v1/alerts | ?status, ?monitor\_id, ?from, ?to | Danh sách Alert | — |
| POST /v1/alerts/{id}:ack | — | Alert | — |
| POST /v1/alerts/{id}:assign | assignee\_id\* | Alert | — |
| POST /v1/alerts/{id}:close | close\_note | Alert | 422 business\_rule\_violated (lặp hơn 2 lần cần ghi chú, BR-124) |
| POST /v1/reports | kind, workspace\_id, title\*, cron\*, work\_calendar\_id, cutoff\_time, as\_of\_rule\*, formats, require\_publish\_check, recipients | ReportSchedule | 422 validation\_failed (kind = report thiếu workspace\_id) |
| PATCH /v1/reports/{id} | title, cron, work\_calendar\_id, cutoff\_time, as\_of\_rule, formats, status, recipients | ReportSchedule | — |
| POST /v1/reports/{id}:pause | — | ReportSchedule | — |
| POST /v1/reports/{id}:run-now | as\_of | 202, Location tới report\_run | — |
| GET /v1/report-runs/{id} | — | ReportRun. Mỗi nhóm quyền một tệp; liên kết tải ký có hạn | — |
| PUT /v1/me/digest | enabled, schedule, metrics | ReportSchedule | — |
| GET /v1/me/notifications | ?unread | Danh sách Notification | — |
| GET /v1/me/notification-prefs | — | Danh sách NotificationPref | — |
| PUT /v1/me/notification-prefs | prefs\* | Danh sách NotificationPref | 422 business\_rule\_violated (tắt in\_app cho hitl, BR-127) |
| PUT /v1/me/channels/{channel} | external\_address, verification\_code | ChannelBinding. Lần đầu gửi mã xác nhận; lần hai kèm verification\_code để kích hoạt | — |
| POST /v1/channels/{kind}/callback | payload | 204. Xác thực bằng chữ ký HMAC của kênh; ghi channel\_delivery.action\_taken rồi gọi hitl:resolve thay người dùng đã liên kết | 401 unauthenticated (chữ ký sai) |
| POST /v1/tasks | agent\_id, description\*, due\_at, workspace\_id | 202, Location tới background\_task. Agent xác nhận lại cách hiểu trước khi chạy (pending\_confirmation) | — |
| GET /v1/tasks/{id} | — | BackgroundTask | — |
| POST /v1/tasks/{id}:cancel | — | BackgroundTask | — |
| GET /v1/external-calendars | ?status | Danh sách ExternalCalendar kèm kỳ tới và số người theo dõi | — |
| POST /v1/external-calendars | code\*, name\*, legal\_basis, recurrence\_hint, default\_local\_time, timezone, work\_calendar\_id, detection\_source\_id, forecast\_model\_ids, pre\_bulletin\_days\_before, pre\_bulletin\_local\_time, forecast\_lead, post\_bulletin, post\_wait\_max | ExternalCalendar; tạo Schedule dự kiến kỳ (ADR-33) | 422 validation\_failed (không có recurrence\_hint lẫn detection\_source\_id) |
| PATCH /v1/external-calendars/{id} | name, recurrence\_hint, default\_local\_time, detection\_source\_id, forecast\_model\_ids, pre\_bulletin\_local\_time, forecast\_lead, post\_bulletin, post\_wait\_max, status | ExternalCalendar; kỳ chưa diễn ra được tính lại pre\_bulletin\_at | — |
| GET /v1/external-calendars/{id}/events | ?from, ?to, ?status | Danh sách ExternalEvent kèm evidence và trạng thái hai bản tin | — |
| POST /v1/external-events/{id}:confirm | action\* (confirm, shift, cancel), expected\_at, note | ExternalEvent; shift ghi shifted\_from và tính lại giờ bản tin; sự kiện monitor.external\_event.changed | 409 invalid\_state (kỳ đã occurred hoặc cancelled) |
| PUT /v1/external-calendars/{id}/subscription | kinds\*, channels\*, status | EventSubscription | 422 validation\_failed (thiếu in\_app trong channels) |
| GET /v1/event-bulletins/{id} | — | EventBulletin kèm gate\_result, content, web\_link, liên kết LedgerEntry | — |
| POST /v1/external-events/{id}:run-bulletin | reason | 202, Location tới event\_bulletin kind = pre; ghi LedgerEntry mới (issued\_via = bulletin) | 409 invalid\_state (kỳ đã diễn ra) |
| GET /v1/monitors | ?status, ?cursor | Danh sách Monitor | — |
| POST /v1/alerts/{id}:reopen | reason\* | Alert (status = open) | 409 invalid\_state (chưa đóng) |
| GET /v1/reports | ?status, ?cursor | Danh sách Report | — |
| GET /v1/reports/{id} | — | Report | 404 not\_found |

### A3 · Gói ngành

| Endpoint | Gửi lên | Trả về | Lỗi riêng |
| --- | --- | --- | --- |
| GET /v1/packs | — | Danh sách IndustryPack | — |
| POST /v1/packs:import | file\* | PackVersion. multipart/form-data; kiểm chữ ký trước khi nhận (BR-128) | 422 business\_rule\_violated (chữ ký không hợp lệ) |
| GET /v1/pack-versions/{id} | — | PackVersion. Kèm thực thể chuẩn, chỉ số chuẩn, luật kiểm tra, kỹ năng mẫu | — |
| POST /v1/packs/{id}/versions | version\*, manifest\*, min\_core\_version\*, release\_notes, breaking\_changes | PackVersion | — |
| POST /v1/pack-versions/{id}:validate | — | 202, Location tới pack\_check\_run | — |
| POST /v1/pack-versions/{id}:publish | — | PackVersion | 409 invalid\_state (chưa qua kiểm tra); 422 business\_rule\_violated (chỉ số thiếu căn cứ hoặc tái dùng mã, BR-131) |
| POST /v1/pack-installs | pack\_version\_id\* | PackInstall | 409 conflict (Data Space đã có gói đang hoạt động) |
| POST /v1/pack-installs/{id}:suggest-mappings | — | 202, Location tới entity\_mapping | — |
| PUT /v1/pack-installs/{id}/mappings | entity\_mappings\*, code\_mappings | Danh sách EntityMapping | — |
| POST /v1/pack-installs/{id}:calibrate | periods | 202, Location tới calibration\_run | — |
| POST /v1/pack-installs/{id}:activate | — | PackInstall. Tạo đề xuất pack\_activate | 422 business\_rule\_violated (đối chiếu chưa đạt, BR-36) |
| POST /v1/metric-overrides | pack\_install\_id\*, pack\_metric\_code\*, expression\*, scope\_condition, reason\* | MetricOverride | — |
| POST /v1/metric-overrides/{id}:approve | — | MetricOverride | 403 forbidden (chỉ số báo cáo cơ quan quản lý cần thêm AC06) |
| POST /v1/pack-installs/{id}:upgrade | to\_version\_id\*; ?dry\_run | PackUpgrade | 423 period\_locked (có kỳ đang chốt, BR-38) |
| PUT /v1/pack-upgrades/{id}/decisions | decisions\* | PackUpgrade | — |
| POST /v1/pack-upgrades/{id}:apply | apply\_from\_period | PackUpgrade | 409 invalid\_state (còn xung đột chưa quyết) |
| POST /v1/pack-upgrades/{id}:rollback | reason\* | PackUpgrade | 409 invalid\_state (quá 30 ngày) |
| POST /v1/packs | code\*, name\*, description | IndustryPack (riêng của tổ chức, chưa có phiên bản) | 409 conflict (trùng code) |
| GET /v1/pack-installs/{id} | — | PackInstall | 404 not\_found |
| GET /v1/pack-installs/{id}/mappings | ?entity, ?cursor | Danh sách Mapping | 404 not\_found |
| GET /v1/calibration-runs/{id} | — | CalibrationRun (nơi Location của :calibrate trỏ tới) | 404 not\_found |
| GET /v1/metric-overrides/{id} | — | MetricOverride | 404 not\_found |
| GET /v1/pack-upgrades/{id} | — | PackUpgrade | 404 not\_found |

### A3 · Kỳ chốt

| Endpoint | Gửi lên | Trả về | Lỗi riêng |
| --- | --- | --- | --- |
| GET /v1/periods | ?status, ?from, ?to | Danh sách PeriodClose | — |
| GET /v1/periods/{id} | — | PeriodClose. Kèm các bản (version), tệp bản chụp, trạng thái đối soát | — |
| POST /v1/periods/{id}:close | — | 202, Location tới period\_close | 409 invalid\_state (đang chốt); 423 period\_locked (đã chốt) |
| GET /v1/periods/{id}/reconciliation | — | Danh sách PeriodReconciliation | — |
| POST /v1/periods/{id}/exceptions | item\_code\*, reason\* | HitlRequest. Ngoại lệ cần hai người duyệt (BR-132) | — |
| POST /v1/periods/{id}:publish | report\_code\*, filed\_at\*, overrides | PeriodClose | 422 business\_rule\_violated (số nhập tay thiếu lý do hoặc chưa duyệt, BR-133) |
| GET /v1/periods/{id}/retro-changes | — | Danh sách RetroChange | — |
| POST /v1/retro-changes/{id}:decide | decision\*, decision\_note\* | RetroChange. decision = new\_version tạo bản chốt mới; bản cũ giữ nguyên (BR-43) | — |
| GET /v1/periods/compare | ?a, ?b, ?metric | JSON. Trả chênh lệch theo chỉ số, nguyên nhân (hồi tố, đổi định nghĩa, đổi ánh xạ) | — |
| GET /v1/periods/compare/{id}/rows | — | Danh sách JSON. Dòng gây chênh; đã che theo chính sách | — |

### A3 · Quản trị

| Endpoint | Gửi lên | Trả về | Lỗi riêng |
| --- | --- | --- | --- |
| GET /v1/admin/users | ?q, ?kind, ?status, ?group | Danh sách AppUser. Kèm vai trò hiệu lực theo từng Data Space | — |
| GET /v1/admin/users/{id} | — | AppUser. Kèm nguồn gốc từng quyền: nhóm, khẩn cấp, yêu cầu quyền, thủ công | — |
| PUT /v1/admin/group-mappings | mappings\*, preview | Danh sách GroupMapping | 422 business\_rule\_violated (một người vừa duyệt vừa kiểm soát trong cùng Data Space, BR-136) |
| POST /v1/admin/emergency-access | user\_id\*, role\_id\*, reason\*, valid\_until, second\_approver\_id\* | UserRole | 422 business\_rule\_violated (quá 24 giờ hoặc người duyệt trùng người cấp, BR-44) |
| GET /v1/data-spaces | — | Danh sách DataSpace. Chỉ các Data Space người gọi là thành viên; kèm vai trò của người gọi | — |
| GET /v1/me | — | AppUser của người gọi (id, username, display\_name, email, department, kind, default\_data\_space\_id) kèm locale, time\_zone của tổ chức; vai trò toàn tổ chức với nguồn (group\_mapping, emergency, manual) và hạn; năng lực toàn tổ chức. Vai trò theo Data Space lấy ở GET /v1/data-spaces | 403 forbidden (đăng nhập được nhưng không có tài khoản đang hoạt động trong tổ chức) |
| PUT /v1/me/data-space-preference | open\_last\_used\*, data\_space\_id | Lựa chọn Data Space mở lần sau; chỉ nhận Data Space người gọi là thành viên và chưa archive | 404 not\_found (không thuộc Data Space hoặc đã archive) |
| GET /v1/me/onboarding | — | Trạng thái hướng dẫn bắt đầu, các bước và mẫu agent | 403 forbidden |
| POST /v1/me/onboarding:skip | — | Bỏ qua hướng dẫn bắt đầu | — |
| POST /v1/me/onboarding:complete | — | Hoàn tất hướng dẫn bắt đầu | — |
| POST /v1/me/onboarding:try-agent | template\_ref\* | Tạo agent mẫu và khởi chạy lượt thử | 422 invalid\_request |
| POST /v1/data-spaces | code\*, name\*, description, steward\_id\*, backup\_steward\_id, default\_policy, work\_calendar\_id | DataSpace | 409 conflict (trùng mã) |
| PATCH /v1/data-spaces/{id} | name, description, steward\_id, backup\_steward\_id, default\_policy, status, work\_calendar\_id | DataSpace | 422 business\_rule\_violated (gỡ người phụ trách cuối cùng, BR-143) |
| PUT /v1/data-spaces/{id}/members | members\* | Danh sách DataSpaceMember | 403 policy\_denied (agent vượt quyền chủ sở hữu) |
| PUT /v1/data-spaces/{id}/sources | source\_ids\* | DataSpace | — |
| POST /v1/access-requests | data\_space\_id\*, capability\*, reason\*, requested\_until\* | AccessRequest | 422 business\_rule\_violated (thời hạn quá 90 ngày, BR-142) |
| POST /v1/access-requests/{id}:decide | decision\*, granted\_scope, note | AccessRequest | 403 forbidden (dữ liệu cá nhân cần thêm AC06); 409 invalid\_state |
| GET /v1/admin/models | — | Danh sách ModelConfig. Kèm độ trễ p95 và kết quả bộ đánh giá gần nhất | — |
| PUT /v1/admin/models/{task} | model\_name\*, endpoint\*, fallback\_model, limits, rollout\_pct | 202, Location tới eval\_run. Tạo phiên bản testing và chạy bộ đánh giá của mọi Data Space; tự kích hoạt khi không giảm (BR-140) | — |
| PUT /v1/admin/ai-settings/{key} | value\* | AiSetting | — |
| GET /v1/admin/system/health | — | JSON. Lấy từ Prometheus: dịch vụ, mô hình, GPU, hàng đợi, sao lưu, DR; trả trạng thái no\_data khi không truy cập được | — |
| GET /v1/calendars | — | Danh sách WorkCalendar | — |
| PUT /v1/calendars/{id} | name, time\_zone, weekend\_days, is\_default, days | WorkCalendar | — |
| POST /v1/dsr | request\_kind\*, received\_at\*, due\_at\*, subject\_identifier | 202, Location tới dsr\_request. subject\_identifier chỉ dùng để tính băm, không lưu; hệ thống tìm vị trí dữ liệu rồi chuyển sang review | — |
| GET /v1/dsr/{id} | — | DsrRequest. Kèm dsr\_item: vị trí, hành động đề xuất, căn cứ | — |
| POST /v1/dsr/{id}:execute | items\* | 202, Location tới dsr\_request | 422 business\_rule\_violated (giữ lại thiếu căn cứ) |
| GET /v1/admin/model-providers | ?kind, ?enabled | Danh sách ModelProvider kèm chi phí tháng hiện tại; không bao giờ trả khoá | — |
| POST /v1/admin/model-providers | code\*, display\_name\*, kind\*, api\_style, endpoint\*, secret\_ref, data\_region, zero\_retention, contract\_ref, price, monthly\_budget\_vnd | ModelProvider; frontier tạo ở enabled = false, max\_classification = public | 409 conflict (trùng code); 422 validation\_failed (frontier thiếu https, secret\_ref, data\_region) |
| PATCH /v1/admin/model-providers/{id} | display\_name, endpoint, secret\_ref, data\_region, zero\_retention, contract\_ref, price, monthly\_budget\_vnd, enabled, status | ModelProvider; đổi endpoint, secret\_ref, data\_region, zero\_retention thì tự tắt và xoá duyệt | 422 business\_rule\_violated (bật frontier chưa có duyệt AC06 hoặc zero\_retention = false, BR-180) |
| POST /v1/admin/model-providers/{id}:approve | note\* | ModelProvider có approved\_by, approved\_at | 403 policy\_denied (người duyệt cũng là người bật) |
| POST /v1/admin/model-providers/{id}:test | model\_name\* | Kết quả gọi thử: độ trễ, số token, lỗi; prompt mẫu cố định nhãn public; ghi model\_route\_decision caller\_kind eval | 503 model\_unavailable (provider không trả lời hoặc egress proxy chặn) |
| GET /v1/admin/model-route-decisions | ?decision, ?reason, ?provider\_id, ?caller\_kind, ?caller\_id, ?from, ?to, ?cursor | Danh sách ModelRouteDecision (không có nội dung prompt, chỉ SHA-256) | — |
| GET /v1/principals | ?q\*, ?kind | Danh sách Principal: id, kind (user, group, agent), tên hiển thị; không có thuộc tính cá nhân | — |
| GET /v1/admin/roles | — | Danh sách Role kèm capabilities | — |
| GET /v1/data-spaces/{id}/members | ?cursor | Danh sách DataSpaceMember | 404 not\_found |
| GET /v1/admin/ai-settings | — | Danh sách AiSetting | — |
| GET /v1/admin/agent-usage | ?from, ?to, ?granularity | Chuỗi tổng hợp theo ngày và theo agent | — |
| POST /v1/schedules:preview | cron\*, work\_calendar\_id, timezone, count | Danh sách thời điểm chạy kế tiếp; không ghi gì | 422 validation\_failed (cron sai) |
| GET /v1/dsr | ?status, ?cursor | Danh sách DsrRequest (không có định danh, chỉ bản băm) | — |

### A3 · Kiểm toán

| Endpoint | Gửi lên | Trả về | Lỗi riêng |
| --- | --- | --- | --- |
| GET /v1/audit | ?actor\_id, ?on\_behalf\_of, ?object\_kind, ?object\_id, ?action, ?outcome, ?from, ?to, ?agent\_run\_id | Danh sách AuditLog. Giá trị trước và sau đã che dữ liệu cá nhân; việc tra cứu cũng được ghi nhật ký | — |
| POST /v1/audit:verify | from\_seq | JSON. Trả {ok, broken\_at\_seq, checked\_rows}; đứt chuỗi tạo security\_event mức cao | — |
| POST /v1/audit:export | filters\*, format | 202, Location tới /v1/audit/exports/{id} (audit.export\_job, CSDL dataagent\_audit). Tệp có chữ ký số; quá 100.000 dòng cần người thứ hai duyệt (BR-138) | — |
| GET /v1/audit/exports/{id} | — | ExportJob (status, row\_count, format, approved\_by, expires\_at); không trả object\_key | 404 not\_found |
| GET /v1/security-events | ?kind, ?severity, ?status | Danh sách SecurityEvent | — |

### A3 · Tích hợp

| Endpoint | Gửi lên | Trả về | Lỗi riêng |
| --- | --- | --- | --- |
| POST /v1/admin/clients | name\*, kind\*, identity\_mode, owner\_user\_id\*, tools, data\_space\_ids, quota\_per\_minute, quota\_per\_day, expires\_at\* | ApiClient. Phản hồi kèm client\_secret một lần duy nhất; status pending\_owner khi owner\_user\_id khác người cấp, active khi chính họ (BR-48) | 422 business\_rule\_violated (hạn quá 12 tháng, BR-144; danh tính riêng với công cụ trả dữ liệu chi tiết, BR-48; người phụ trách không có quyền hỏi trong một Data Space được cấp, BR-48) |
| PATCH /v1/admin/clients/{id} | tools, data\_space\_ids, quota\_per\_minute, quota\_per\_day, expires\_at, status | ApiClient | — |
| POST /v1/admin/clients/{id}:rotate-secret | — | ApiClient. Trả bí mật mới một lần; bí mật cũ còn hiệu lực 24 giờ | — |
| POST /v1/admin/clients/{id}:revoke | reason\* | ApiClient. Token đang dùng bị vô hiệu ngay | — |
| GET /v1/admin/clients/{id}/usage | ?from, ?to, ?granularity, ?tool | Danh sách UsageCounter | — |
| GET /v1/me/clients | ?status | Danh sách ApiClient có owner\_user\_id là người gọi | — |
| POST /v1/me/clients/{id}:confirm | — | ApiClient, status active, owner\_confirmed\_at đặt; chỉ chính người phụ trách được gọi. If-Match bắt buộc (ETag của client như người phụ trách đã xem), để họ không đồng ý một phạm vi đã bị sửa sau khi xem | 404 not\_found (không phải client của người gọi); 409 invalid\_state (client không ở pending\_owner); 412 precondition\_failed (thiếu If-Match hoặc client đã đổi) |
| POST /v1/webhooks | api\_client\_id\*, event\_types\*, target\_url\* | WebhookSubscription. Phản hồi kèm khoá ký HMAC một lần | 422 business\_rule\_violated (địa chỉ không thuộc danh sách đích được phép) |
| GET /v1/webhooks/{id}/deliveries | ?status, ?from, ?to | Danh sách WebhookDelivery | — |
| POST /v1/webhook-deliveries/{id}:retry | — | 202, Location tới webhook\_delivery | — |
| POST /mcp | jsonrpc\*, method\*, params, id | JSON. JSON-RPC 2.0 theo MCP (Streamable HTTP); tools/call chạy như câu hỏi thường với quyền theo BR-48; tối đa 200 dòng (BR-49) | 403 policy\_denied; 429 quota\_exceeded |
| GET /v1/admin/clients | ?status, ?cursor | Danh sách ApiClient, không có bí mật | — |

### A3 · Agent

| Endpoint | Gửi lên | Trả về | Lỗi riêng |
| --- | --- | --- | --- |
| GET /v1/agents | ?status, ?kind, ?owner | Danh sách AgentDef. Kèm số lượt chạy đang chạy, chờ người, chi phí tháng | — |
| POST /v1/agents:draft-from-text | description\*, template\_ref | AgentConfig. Trả cấu hình đề xuất (chưa lưu); phần vượt quyền chủ sở hữu được đánh dấu | — |
| POST /v1/agents | code\*, name\*, kind\*, template\_ref, monthly\_budget, config | AgentDef. Tạo tài khoản agent với chủ sở hữu là người gọi (BR-147) | 409 conflict (trùng mã); 403 policy\_denied (phạm vi dữ liệu vượt quyền chủ sở hữu) |
| GET /v1/agents/{id} | — | AgentDef. Kèm phiên bản cấu hình đang chạy và triển khai đang diễn ra | — |
| GET /v1/agents/{id}/configs | — | Danh sách AgentConfig | — |
| POST /v1/agents/{id}/configs | goal\*, data\_scope, skill\_ids, autonomy, checkpoints, denied\_actions, limits, model\_by\_task, change\_note, tools | AgentConfig | 422 business\_rule\_violated (công cụ gửi ra ngoài thiếu checkpoint, BR-70; act\_then\_report chưa đạt 95% bộ thử) |
| PUT /v1/agents/{id}/triggers | triggers\* | Danh sách AgentTrigger. Trả kèm 5 lần chạy kế tiếp hoặc số lần khớp trong 30 ngày; khớp quá 50 lần một ngày thì cảnh báo | — |
| POST /v1/agents/{id}/test-runs | config\_version, as\_of | 202, Location tới agent\_run. is\_test = true; công cụ ghi và gửi chạy giả lập | — |
| POST /v1/agents/{id}/eval-runs | config\_version | 202, Location tới eval\_run | — |
| POST /v1/agents/{id}/deployments | config\_version\*, traffic\_pct, gates | AgentDeployment | 409 conflict (đang có triển khai); 409 invalid\_state (phiên bản chưa đạt bộ thử) |
| PATCH /v1/deployments/{id} | traffic\_pct\* | AgentDeployment | — |
| POST /v1/deployments/{id}:rollback | reason\*, to\_version | AgentDeployment | — |
| GET /v1/runs | ?agent\_id, ?status, ?from, ?to, ?parent\_run\_id | Danh sách AgentRun | — |
| GET /v1/runs/{id} | ?view | AgentRun. view = simple trả diễn biến kể thành câu, technical trả các bước | — |
| GET /v1/runs/{id}/events | — | Luồng SSE. Sự kiện step\_started, step\_finished, checkpoint, narrative, cost, done | — |
| GET /v1/runs/{id}/children | — | Danh sách AgentRun | — |
| POST /v1/runs/{id}:pause | — | AgentRun. Dừng an toàn ở ranh giới bước | 409 invalid\_state |
| POST /v1/runs/{id}:resume | — | AgentRun | 409 invalid\_state |
| POST /v1/runs/{id}:cancel | reason | AgentRun. Kết quả trung gian giữ 30 ngày | — |
| PUT /v1/runs/{id}/plan | steps\*, edit\_note | Plan. Tạo phiên bản kế hoạch mới; chạy lại từ bước bị sửa | 409 invalid\_state (lượt chạy không ở trạng thái paused) |
| GET /v1/agents/{id}/memories | ?kind, ?scope, ?status | Danh sách AgentMemory | — |
| POST /v1/agents/{id}/memories | scope\*, user\_id, kind\*, content\*, expires\_at | AgentMemory. scope = data\_space tạo yêu cầu duyệt cho chủ sở hữu Data Space | 422 business\_rule\_violated (nội dung chứa dữ liệu cá nhân, BR-76) |
| PATCH /v1/memories/{id} | content, expires\_at, status | AgentMemory | — |
| DELETE /v1/memories/{id} | — | 204 | — |
| POST /v1/agents/{id}:lock | reason\* | Agent (status = locked) | 409 invalid\_state (đã khoá) |
| POST /v1/agents/{id}:unlock | — | Agent (status như trước khi khoá; trigger không tự bật lại) | 409 invalid\_state (chưa khoá) |
| POST /v1/runs/{id}:retry | — | 202, Location tới run mới (retry\_of = lượt cũ, BR-75) | 409 invalid\_state (lượt chưa failed) |

### A3 · Thu thập

| Endpoint | Gửi lên | Trả về | Lỗi riêng |
| --- | --- | --- | --- |
| GET /v1/crawl-sources | ?status, ?kind, ?owner\_id, ?has\_issue | Danh sách CrawlSource. Kèm lần thu cuối, độ mới so với lịch, tỷ lệ trích đúng, số mục chờ duyệt, lỗi tải gần nhất | — |
| POST /v1/crawl-sources | name\*, goal\*, kind\*, seed\_urls\*, allowed\_domains\*, max\_depth, schedule\_cron, rate\_limit\_rps, auth\_mode, secret\_ref, review\_gate, trust\_level, openapi\_url, terms\_url | CrawlSource (status = draft). Kiểm robots.txt, điều khoản và thử tải 1 trang trước khi lưu; sự kiện acquire.source.created. Dữ liệu mang nhãn nguồn ngoài (BR-149, BR-156) | 422 business\_rule\_violated (robots.txt hoặc điều khoản cấm thu thập tự động; trang cần đăng nhập, captcha, BR-149); 422 validation\_failed (seed\_urls ngoài allowed\_domains) |
| GET /v1/crawl-sources/{id} | — | CrawlSource. Kèm phiên bản schema active và 5 lượt gần nhất | — |
| PATCH /v1/crawl-sources/{id} | name, goal, allowed\_domains, max\_depth, schedule\_cron, rate\_limit\_rps, review\_gate, trust\_level, owner\_id | CrawlSource | 422 business\_rule\_violated (review\_gate = auto khi độ chính xác mẫu ngẫu nhiên chưa đạt 98% trong 4 lượt liên tiếp) |
| POST /v1/crawl-sources/{id}:legal-review | legal\_status\* (approved, rejected), terms\_url\*, legal\_checklist\*, legal\_evidence\*, legal\_review\_due\_at, note | CrawlSource. Ghi terms\_sha256 từ bản điều khoản vừa chụp, legal\_reviewed\_by, legal\_reviewed\_at; hạn xem lại mặc định 6 tháng; đóng yêu cầu Cần bạn kind = legal\_review; sự kiện acquire.source.legal\_reviewed (BR-174) | 403 policy\_denied (người duyệt là người phụ trách nguồn, BR-174); 422 validation\_failed (thiếu bằng chứng hoặc thiếu một trong bốn mục terms, robots, copyright, personal\_data; hạn xem lại quá 184 ngày) |
| POST /v1/crawl-sources/{id}:pause | reason\* | CrawlSource (status = paused) | 409 invalid\_state |
| POST /v1/crawl-sources/{id}:resume | — | CrawlSource (status = active) | 409 invalid\_state (chưa có schema active hoặc còn schema\_drift nặng chưa xử lý, BR-152); 422 business\_rule\_violated (duyệt pháp lý chưa có, hết hiệu lực hoặc quá hạn xem lại, BR-174) |
| POST /v1/crawl-sources/{id}/runs | — | 202, Location tới crawl\_run. trigger = manual; xong lượt phát acquire.run.completed. Bản gốc lưu MinIO da-raw-crawl kèm SHA-256 (BR-150) | 409 invalid\_state (nguồn không active hoặc đang có lượt chạy) |
| GET /v1/crawl-runs | ?source\_id, ?status, ?from, ?to | Danh sách CrawlRun | — |
| GET /v1/crawl-runs/{id} | — | CrawlRun. Kèm danh sách CrawlPage (http\_status, sha256, changed), sample\_accuracy, drift\_score, cost | — |
| POST /v1/crawl-sources/{id}/schema-inference | sample\_urls, hints | 202, Location tới agent\_run. Agent Thu thập tải tối thiểu 5 mẫu, tạo CrawlSchemaVersion status = draft | 409 invalid\_state (nguồn retired) |
| GET /v1/crawl-sources/{id}/schemas | ?status | Danh sách CrawlSchemaVersion | — |
| POST /v1/crawl-schemas/{id}:test | sample\_page\_ids | 202, Location tới agent\_run. Kết quả: tỷ lệ trường bắt buộc đúng, độ tin từng trường, bảng trước sau trên mẫu giữ lại (tối thiểu 20 trang) | — |
| POST /v1/crawl-schemas/{id}:approve | note | CrawlSchemaVersion (status = active); phiên bản trước chuyển superseded; nguồn active (BR-151) | 409 invalid\_state (chưa chạy thử); 422 business\_rule\_violated (dưới 95% trường bắt buộc đúng, BR-151) |
| POST /v1/crawl-schemas/{id}:revert | reason\* | 202, Location tới crawl\_run. Quay về phiên bản đúng gần nhất; bản ghi trích bằng phiên bản lỗi chuyển superseded; trích lại từ bản gốc; dataset hợp nhất phát hành phiên bản mới (BR-152). Trang hết hạn lưu bản gốc được liệt kê | 409 invalid\_state (không có phiên bản đúng trước đó) |
| GET /v1/schema-drifts | ?source\_id, ?drift\_kind, ?severity, ?resolution | Danh sách SchemaDrift. Kèm signals và phiên bản đề xuất; sự cố tạo tự động mỗi lượt (sự kiện acquire.schema.drift\_detected) | — |
| POST /v1/schema-drifts/{id}:resolve | resolution\* (approved\_new, manual\_fix, reverted, false\_alarm), proposed\_version\_id, note | SchemaDrift. Đóng yêu cầu Cần bạn kind = schema\_drift; nguồn chạy tiếp. Lệch ngầm (drift\_kind = semantic) đóng bằng false\_alarm thì lượt cách ly được phát hành; bằng approved\_new hoặc manual\_fix thì trích lại các lượt cách ly bằng phiên bản mới (BR-168) | 409 invalid\_state (đã xử lý); 422 business\_rule\_violated (approved\_new với phiên bản chưa duyệt, BR-152; false\_alarm cho thay đổi cấu trúc, BR-168) |
| GET /v1/crawl-runs/{id}/records | ?status, ?review\_reasons, ?is\_random\_sample | Danh sách ExtractedRecord. Kèm vùng gốc (object\_key, vị trí trích) để tô sáng trên bản chụp | — |
| POST /v1/extracted-records/{id}:review | decision\* (confirm, correct, reject), corrected\_values, reason | ExtractedRecord, ReviewDecision. Bản sửa giữ làm ví dụ cho bộ trích (BR-153) | 409 invalid\_state (lượt đã phát hành); 422 validation\_failed (correct thiếu corrected\_values) |
| POST /v1/extracted-records:bulk-review | record\_ids hoặc filter (run\_id, review\_reasons), decision\*, reason | Số bản ghi theo kết quả | 409 invalid\_state (lượt đã phát hành) |
| POST /v1/crawl-runs/{id}:publish | — | CrawlRun (status = published); sự kiện acquire.run.published, kích hoạt hợp nhất | 422 business\_rule\_violated (còn mục chặn trong hàng duyệt, BR-153; dữ liệu trích bằng schema chưa duyệt, BR-152; nguồn còn lệch ngầm chưa xử lý, BR-168) |
| GET /v1/consolidation-specs | ?entity\_type, ?status | Danh sách ConsolidationSpec | — |
| POST /v1/consolidation-specs | name\*, entity\_type\*, grain\*, source\_ids\*, normalization, source\_priority, aggregates, summary\_template, allow\_interpolation, target\_dataset\_id | ConsolidationSpec | 422 validation\_failed (nguồn thuộc Data Space khác) |
| PATCH /v1/consolidation-specs/{id} | name, entity\_type, grain, source\_ids, normalization, source\_priority, aggregates, summary\_template, allow\_interpolation, target\_dataset\_id, owner\_id, status (active, retired) | ConsolidationSpec. Đổi một trong entity\_type, grain, source\_ids, normalization, source\_priority, aggregates, summary\_template, allow\_interpolation, target\_dataset\_id thì trigger ghi last\_edited\_by = người gọi, last\_edited\_at = now(); đổi name, owner\_id, status chỉ đổi updated\_by. Lượt hợp nhất sau chép last\_edited\_by làm người tạo phiên bản dataset (BR-156). Sự kiện acquire.spec.updated | 409 invalid\_state (đặc tả retired; đặc tả đang có lượt hợp nhất chạy khi sửa nội dung); 422 validation\_failed (nguồn thuộc Data Space khác; active khi thiếu target\_dataset\_id; source\_ids rỗng) |
| POST /v1/consolidation-specs/{id}/runs | input\_run\_ids | 202, Location tới consolidation\_run. Chỉ bảng tổng hợp, khẳng định, đoạn tóm tắt được nhúng vector (BR-154); lệch trên ngưỡng trọng yếu mở xung đột (BR-155) | 409 invalid\_state (đặc tả đang có lượt hợp nhất chạy) |
| GET /v1/consolidation-runs/{id} | — | ConsolidationRun. Kèm dataset\_version\_id, conflicts\_opened, claims\_created, chunks\_embedded; phát hành thì phát acquire.dataset.published | — |
| GET /v1/entity-links | ?entity\_type, ?status, ?source\_id | Danh sách EntityLink | — |
| POST /v1/entity-links/{id}:decide | status\* (confirmed, rejected), reference\_entity\_id | EntityLink (method = human); canonical\_key chép từ thực thể chuẩn (BR-169) | 409 invalid\_state (đã quyết định); 422 validation\_failed (confirmed thiếu reference\_entity\_id hoặc thực thể chuẩn khác loại, BR-169) |
| POST /v1/crawl-sources:suggest | goal\*, examples | 202, Location tới agent\_run; kết quả là danh sách nguồn đề xuất, chưa đăng ký nguồn nào | — |

### A3 · Dự báo

| Endpoint | Gửi lên | Trả về | Lỗi riêng |
| --- | --- | --- | --- |
| POST /v1/forecasts | metric\_code\*, frequency\*, horizon\*, scope, question\_id, assumptions, notify\_channels | Có dự báo còn hiệu lực: 200, ForecastJob (status = ready) kèm Prediction. Chưa có: 202, Location tới forecast\_job, thân thêm eta\_at. Chỉ dùng dữ liệu biết trước cutoff\_at (BR-157, BR-159) | 422 business\_rule\_violated (chuỗi dưới 2 chu kỳ mùa vụ hoặc 24 điểm; kèm gợi ý tần suất khác) |
| GET /v1/forecasts/{id} | — | ForecastJob. Kèm progress, eta\_at; khi ready kèm Prediction (point, lo80, hi80, lo95, hi95), giả định, ngày dữ liệu và nhãn (đã duyệt, thử nghiệm, mô hình chuẩn) | — |
| GET /v1/forecasts/{id}/events | — | Luồng SSE. Sự kiện progress, waiting\_human, eta\_changed, result, error, done; nối lại bằng Last-Event-ID. Kafka: forecast.job.state\_changed, forecast.job.completed | — |
| POST /v1/forecasts/{id}:cancel | reason | ForecastJob (status = cancelled) | 409 invalid\_state (đã ready hoặc failed) |
| GET /v1/forecast-models | ?metric\_code, ?frequency, ?status | Danh sách ForecastModel. Kèm MASE cuốn chiếu và nhãn suy giảm | — |
| GET /v1/forecast-models/{id} | — | ForecastModel. Kèm phiên bản đang phát hành và lịch huấn luyện lại | — |
| GET /v1/forecast-model-versions/{id} | — | ModelVersion. Kèm model\_card, features và future\_assumption từng biến | — |
| GET /v1/forecast-model-versions/{id}/backtest | — | BacktestRun. metrics theo từng tầm so với baseline\_metrics (seasonal naive), candidates | — |
| POST /v1/forecast-model-versions/{id}:submit | note | ModelVersion (status = pending\_approval); tạo yêu cầu Cần bạn kind = forecast\_model | 422 business\_rule\_violated (MASE ≥ 1 so với seasonal naive, BR-158) |
| POST /v1/forecast-model-versions/{id}:approve | note | ModelVersion (status = published); phiên bản cũ chuyển retired, dự báo tính lại; sự kiện forecast.model.published (BR-158) | 403 policy\_denied (người tạo tự duyệt); 409 invalid\_state (chưa ở pending\_approval, hoặc mô hình đã ngừng: không nhận phiên bản mới, sửa 12/10/2026) |
| POST /v1/forecast-model-versions/{id}:reject | reason\* | ModelVersion (status = rejected) | 409 invalid\_state |
| POST /v1/forecast-models/{id}:retrain | reason, training\_to | 202, Location tới forecast\_job. Phiên bản mới qua backtest và duyệt (UC-15-04) | 409 invalid\_state (đang có lượt huấn luyện) |
| POST /v1/forecast-models/{id}/scenarios | name\*, assumptions\* | Scenario kèm Prediction theo kịch bản và chênh lệch so với kịch bản gốc | 422 business\_rule\_violated (mô hình không dùng biến trong assumptions) |
| GET /v1/forecast-models/{id}/predictions | ?scenario\_id, ?model\_version\_id, ?from, ?to | Danh sách Prediction (point, lo80, hi80, lo95, hi95, contributions). Luôn kèm giả định và nhãn mô hình (BR-158, BR-160) | — |
| GET /v1/forecast-models/{id}/accuracy | ?from, ?to | Danh sách AccuracyLog. Kèm rolling\_mase, tỷ lệ nằm trong khoảng 80% và 95%; chỉ đọc, không ghi gì: suy giảm do worker chấm điểm phát forecast.model.degraded (BR-160, sửa 12/10/2026) | — |
| GET /v1/formula-models | ?metric\_code, ?product\_code, ?status | Danh sách FormulaModel kèm trạng thái phiên bản (published, degraded) và reconstruction\_mae | — |
| POST /v1/formula-models | model\_id\*, metric\_version\_id\*, product\_code\*, formula\*, window\_rule\*, input\_series\*, nowcast\_method, n\_draws, miss\_abs\_threshold, rounding | FormulaModel kèm ModelVersion draft algorithm = formula\_mc | 422 business\_rule\_violated (metric\_version chưa certified, formula tham chiếu tham số không tồn tại, BR-183) |
| GET /v1/formula-models/{id} | — | FormulaModel. Kèm fund\_model (method, log\_score so với mốc), input\_series, owner | — |
| GET /v1/formula-models/{id}/components | ?at, ?status | Danh sách FormulaComponent của các mã mà formula tham chiếu; ?at trả giá trị hiệu lực tại thời điểm | — |
| POST /v1/formula-models/{id}/components | code\*, product\_code\*, value\*, unit\*, valid\_from\*, valid\_to, legal\_doc\_number\*, legal\_doc\_url\*, document\_id, extracted\_record\_id, note | FormulaComponent (status = pending\_review); tạo yêu cầu Cần bạn kind = formula\_component | 422 validation\_failed (thiếu số hiệu hoặc link văn bản, BR-182) |
| POST /v1/formula-components/{id}:certify | note | FormulaComponent (status = certified); bản cũ được đặt valid\_to; sự kiện forecast.formula\_component.certified | 403 policy\_denied (người nhập tự chứng nhận); 409 invalid\_state (chồng hiệu lực với bản certified khác) |
| POST /v1/formula-models/{id}/runs | period\*, product\_codes, event\_id, issue (none, bulletin, answer) | 202, Location tới forecast\_job; khi ready kèm Prediction (interval\_method = monte\_carlo) và LedgerEntry nếu issue khác none | 422 business\_rule\_violated (cổng không đạt: thiếu tham số certified hoặc đầu vào quá hạn, kèm danh sách, BR-183) |
| GET /v1/forecasts/{id}/distribution | ?product\_code | quantiles, prob\_up, fund\_probs, contributions {world, fx, tax, fund, proxy\_error} kèm source\_refs, histogram 50 khoảng; câu bắt buộc về Quỹ (BR-184) | 409 invalid\_state (việc chưa ready) |
| GET /v1/forecast-ledger | ?forecast\_model\_id, ?product\_code, ?from, ?to, ?status, ?miss | Danh sách LedgerEntry mới nhất trước; kèm chỉ số hiệu chỉnh N kỳ (BR-185) | — |
| GET /v1/forecast-ledger/{id} | — | LedgerEntry kèm bản tin đã gửi (EventBulletin), actual\_components, miss\_attribution, error\_book\_entry\_id | — |
| POST /v1/forecast-ledger/{id}:score | reason\* | 202, Location tới việc chấm; bản chấm cũ giữ trong accuracy\_log | 409 invalid\_state (dòng void hoặc chưa có giá công bố) |
| GET /v1/forecast-models/{id}/calibration | ?last\_n, ?product\_code | coverage80, coverage95, mae, crps, crps\_naive, miss\_count, n\_scored; dưới 10 kỳ đã chấm trả insufficient = true | — |

### A3 · Lớp riêng lãnh đạo

| Endpoint | Gửi lên | Trả về | Lỗi riêng |
| --- | --- | --- | --- |
| GET /v1/exec/vault | — | Vault kèm danh sách VaultKeyWrap (kind, label, kdf, kdf\_salt, wrapped\_vmk, last\_used\_at); box\_private\_key\_ct là bản mã, thiết bị mở bằng VMK | 404 not\_found (chưa có kho) |
| POST /v1/exec/vault | box\_public\_key\*, box\_private\_key\_ct\*, key\_wraps\* (ít nhất 2, một ngoại tuyến), exit\_policy, contract\_ref | Vault. Exec service tạo khoá Transit RSA-4096 riêng của kho trong OpenBao và trả transit\_public\_key (BR-189) | 409 conflict (đã có kho); 422 business\_rule\_violated (dưới 2 khoá bọc hoặc không có khoá ngoại tuyến) |
| POST /v1/exec/vault/key-wraps | kind\*, credential\_id, label\*, kdf\*, kdf\_salt\*, wrapped\_vmk\* | VaultKeyWrap | 409 invalid\_state (kho sealed hoặc shredded) |
| DELETE /v1/exec/vault/key-wraps/{id} | — | 204 | 422 business\_rule\_violated (kho còn dưới 2 khoá hoặc mất khoá ngoại tuyến, BR-189) |
| GET /v1/exec/transit-key | — | JSON. public\_key\_pem, key\_version, algorithm = RSA-OAEP-SHA256 | — |
| GET /v1/exec/theses | ?status, ?due\_from, ?due\_to | Danh sách Thesis (cột vận hành và bản mã nội dung); không trả cond\_ct | — |
| POST /v1/exec/theses | id\* (UUIDv7 do thiết bị sinh, nằm trong AAD của bản mã; thêm 13/10/2026), content\_ct\*, wrapped\_dek\*, content\_version\*, cond\_ct\*, cond\_key\_version\*, due\_at\*, supersedes\_id | 201, Thesis (status = open); khởi chạy ExecThesisWorkflow; sự kiện exec.thesis.committed không nội dung (BR-188) | 409 conflict (cond\_key\_version không còn hiện hành); 409 invalid\_state (kho sealed); 422 business\_rule\_violated (kho thiếu khoá bọc, due\_at dưới 1 ngày) |
| GET /v1/exec/theses/{id} | — | Thesis kèm evidence\_state, số lô bằng chứng, publication\_id | 404 not\_found (không phải chủ kho) |
| POST /v1/exec/theses/{id}:withdraw | — | Thesis (status = withdrawn); workflow dừng | 409 invalid\_state (đã tới due\_at hoặc đã chấm) |
| GET /v1/exec/theses/{id}/evidence | ?batch\_no | Danh sách ThesisEvidence theo lô (side, item\_kind, strength, freshness\_ok, sealed\_ct); mặc định lô mới nhất và lô liền trước | — |
| POST /v1/exec/theses/{id}/evidence:refresh | — | 202, Location tới agent\_run. Gộp vào lô kế tiếp nếu đã có yêu cầu trong 1 giờ (BR-192) | 409 invalid\_state (luận điểm không open); 429 rate\_limited (đã đủ 3 lần giải mã trong 24 giờ) |
| GET /v1/exec/theses/{id}/scores | — | Danh sách ThesisScore (final, restatement) | — |
| GET /v1/exec/scores | ?from, ?to | Danh sách ThesisScore của chủ kho kèm thesis\_id, committed\_at; thiết bị ghép với mức tự tin đã giải mã để tính Brier (tab 5 G10.5) | — |
| GET /v1/exec/key-access-log | ?thesis\_id, ?from, ?to, ?action | Danh sách KeyAccessLog kèm correlation\_id để đối chiếu nhật ký audit OpenBao | — |
| GET /v1/exec/memory-items | ?kind, ?from, ?to | Danh sách MemoryItem (bản mã); thiết bị tải hết để dựng chỉ mục tìm kiếm cục bộ (tab 5 G10.7) | — |
| POST /v1/exec/memory-items | id\* (UUIDv7 do thiết bị sinh, nằm trong AAD của bản mã; thêm 13/10/2026), kind\*, content\_ct\*, wrapped\_dek\*, content\_version\*, related\_thesis\_id | 201, MemoryItem | 409 invalid\_state (kho sealed); 422 business\_rule\_violated (kho thiếu khoá bọc) |
| POST /v1/exec/memory-items/{id}:shred | — | MemoryItem (content\_ct, wrapped\_dek null, shredded\_at) | 409 invalid\_state (đã huỷ) |
| POST /v1/exec/private-questions | text\*, context\_items (tối đa 8 mục đã giải mã, tối đa 4.000 token), as\_of | 202, Location tới luồng SSE. Không tạo qa.question; Model gateway chỉ dùng mô hình tại chỗ, không ghi prompt log (BR-193) | 503 model\_unavailable (mô hình tại chỗ quá tải; không chuyển frontier) |
| GET /v1/exec/private-questions/{id}/stream | — | Luồng SSE. Sự kiện understood, progress, answer (văn bản, figures kèm query\_run\_id), done; trạng thái tạm xoá sau 15 phút | 410 gone (quá 15 phút) |
| POST /v1/exec/theses/{id}/publications | title\*, statement\_public\*, target\_data\_space\_id\*, attribution\*, attribution\_label\*, confidence\_public, rationale\_public, ref\_kind\*, metric\_ref\*, scope, period\_label\*, comparator\*, threshold\*, threshold\_hi, unit\* | 202, Location tới publication. Workflow giải mã cond\_ct để đối chiếu điều kiện công bố (ghi sổ, purpose validate); Context service tạo trang kind hypothesis bản nháp và đề xuất knowledge\_page giao người phụ trách Data Space đích (BR-194) | 422 business\_rule\_violated (câu giả thuyết có số; điều kiện khác điều kiện đã ghi); 409 invalid\_state (luận điểm rút hoặc không chấm được) |
| GET /v1/exec/publications/{id} | — | Publication kèm knowledge\_page\_id, trạng thái duyệt | — |
| POST /v1/exec/publications/{id}:withdraw | reason | Publication (status = withdrawn); trang chuyển retired | 409 invalid\_state |
| POST /v1/exec/handovers | kind\*, reason\*, successor\_id, contract\_ref | 201, Handover. Chủ kho gửi thì owner\_participates = true sau xác nhận passkey; AC05 gửi thì chỉ làm đúng exit\_policy của hợp đồng (seal hoặc shred), không chọn phương án khác (BR-195) | 422 business\_rule\_violated (selective\_handover khi chủ kho không tham gia; kind khác exit\_policy); 409 conflict (đang có yêu cầu mở) |
| GET /v1/exec/handovers/{id} | — | Handover; AC05, AC06 thấy cùng nội dung (bảng không có bản mã) | — |
| POST /v1/exec/handovers/{id}/items | items\* \[{source\_kind, source\_id, payload\_sealed}\] | Danh sách HandoverItem; tăng items\_total | 409 invalid\_state (handover chưa approved hoặc in\_progress) |
| GET /v1/exec/handovers/{id}/items | — | Danh sách HandoverItem của người nhận | 404 not\_found (không phải người nhận) |
| POST /v1/exec/handovers/{id}:approve | note | Handover (status = approved); shred chạy worker huỷ khoá, trả certificate\_sha256 khi completed | 403 policy\_denied (người duyệt là người yêu cầu); 409 invalid\_state |
| POST /v1/exec/handovers/{id}:accept | — | Handover (status = completed khi mọi mục đã nhập); dòng trung chuyển xoá theo lịch | 409 invalid\_state |

## A4. Tệp OpenAPI

Quyết định từ 11/10/2026 (tab 6 ADR-48): OpenAPI không viết tay trong tài liệu nữa. FastAPI tự sinh openapi.json từ code ở mỗi build backend; A2 và A3 là đặc tả nguồn để kiểm. CI so danh sách endpoint (phương thức + đường dẫn) của OpenAPI sinh ra với A2: thiếu hoặc thừa một endpoint là chặn merge; Schemathesis chạy trên chính OpenAPI đó. Từ điển dữ liệu và DDL vẫn sinh từ csdl/definitions.json (ADR-12). Các tệp OpenAPI mô tả dưới đây là bản tham khảo khi viết code, không gộp và không cập nhật nữa.

Tệp dataagent-openapi.yaml (OpenAPI 3.1.0) mô tả đủ 240 endpoint trên 221 đường dẫn và 101 schema. Tệp sinh tự động từ cùng tệp định nghĩa với A3 (thư mục api: catalog.py cho danh mục, specs1.py đến specs4.py cho chi tiết, apigen.py cho bộ sinh) và từ định nghĩa bảng ở tab 3b D4 (thư mục csdl), nên A2, A3, tab 3b và tệp OpenAPI khớp nhau tại ngày sinh 09/10/2026; sau ADR-48 tệp không cập nhật nữa. Hai nhóm Thu thập và Dự báo (43 endpoint, 40 đường dẫn, 17 schema mới) cùng 2 endpoint chứng nhận dataset hợp nhất của nhóm Nguồn và dữ liệu (2 đường dẫn, schema DatasetVersionCertification mới và DatasetVersion rút gọn chỉ có cột liên quan chứng nhận) thêm ngày 10/10/2026 nằm ở tệp bổ sung dataagent-openapi-p14-p15.yaml (OpenAPI 3.1.0), sinh bằng apigen\_p14\_p15.py từ A2, A3 và D4.22, D4.23, D4.4; tệp tự khai báo phần chung tối thiểu theo A1 và đã qua kiểm tra hợp lệ ngày 10/10/2026. Theo ADR-48 không còn bước nhập vào catalog.py và sinh tệp gộp; các tệp này là bản tham khảo.

Lớp tri thức biên soạn (10/10/2026): 16 endpoint thêm vào nhóm Lớp ngữ cảnh (A2, A3, đường dẫn /v1/knowledge\*, Context service) nằm ở tệp bổ sung dataagent-openapi-knowledge.yaml, sinh bằng apigen\_knowledge.py từ A2, A3 và D4.5; tệp tự khai báo phần chung tối thiểu theo A1. Tệp này là bản tham khảo theo ADR-48, không gộp.

Data product, biên nhận, Error Book và provider mô hình (thêm 10/10/2026): 29 endpoint mới ở A2, A3 trong bốn nhóm sẵn có: Hỏi đáp 4 (/v1/answers/{id}/receipt, /v1/receipts\*, /v1/receipt-replays/{id}), Lớp ngữ cảnh 11 (/v1/data-products\*), Cần bạn và đánh giá 8 (/v1/error-book-entries\*), Quản trị 6 (/v1/admin/model-providers\*, /v1/admin/model-route-decisions). Chưa có trong tệp OpenAPI. Cùng lượt có 12 endpoint dự báo theo công thức và sổ dự báo (nhóm Dự báo), 8 endpoint lịch sự kiện ngoài (nhóm Theo dõi, báo cáo, thông báo) và 28 endpoint nhóm mới Lớp riêng lãnh đạo, cũng không có tệp OpenAPI viết tay; OpenAPI của chúng sinh từ code (ADR-48, tab 6 O4.4). Số endpoint từng nhóm và tổng ở A2 đã cập nhật ngày 11/10/2026: 379 endpoint, 22 nhóm.

| Mục | Nội dung |
| --- | --- |
| Schema tài nguyên | Sinh từ bảng CSDL cùng tên, bỏ cột nội bộ; cột liệt kê giá trị trạng thái thành enum; numeric thành chuỗi decimal; khoảng thời gian thành Range |
| Schema chung | Problem (RFC 9457), Accepted (phản hồi 202), Range |
| Bảo mật | bearer JWT do Keycloak cấp; header X-Data-Space khai báo ở components.parameters |
| Mở rộng | Mỗi thao tác có x-use-cases (use case phục vụ) và x-capability (năng lực cần có), dùng để kiểm thử phân quyền tự động |
| Nhóm | tags theo 21 nhóm ở A2 |
| Kiểm chứng | Qua openapi-spec-validator ngày 09/10/2026; đối chiếu không thiếu, không thừa endpoint so với A2 |
| Kiểm chứng tệp bổ sung P14, P15 | dataagent-openapi-p14-p15.yaml qua openapi-spec-validator 0.9.0 ngày 10/10/2026. Đối chiếu A2: 43 endpoint (Thu thập 26, Dự báo 15, Nguồn và dữ liệu 2), 40 đường dẫn, 43 operationId khác nhau, 19 schema tài nguyên (17 mới của P14, P15, DatasetVersionCertification và DatasetVersion rút gọn); không thiếu, không thừa; mọi schema nêu ở cột Trả về của A3 đều có trong tệp. Sinh lại và kiểm lại cùng ngày (lần 4) sau khi thêm PATCH /v1/consolidation-specs/{id} và POST /v1/crawl-sources/{id}:legal-review cùng các cột mới của D4.22, D4.23: hợp lệ, 45 endpoint (Thu thập 28), 42 đường dẫn, 45 operationId khác nhau; con số tổng ở đoạn trên và ở A2 đã cập nhật theo số này |
| Kiểm chứng tệp bổ sung lớp tri thức | dataagent-openapi-knowledge.yaml (OpenAPI 3.1.0, sinh bằng apigen\_knowledge.py) qua openapi-spec-validator 0.9.0 ngày 10/10/2026: 16 endpoint nhóm Lớp ngữ cảnh, 13 đường dẫn, 16 operationId khác nhau, 13 schema (8 schema tài nguyên từ bảng knowledge\_ ở D4.5, KnowledgeIndexEntry, RenderedFigure, RenderedKnowledgePage, Problem, Accepted); không thiếu, không thừa so với A2; duyệt phiên bản dùng POST /v1/proposals/{id}:decide của tệp chính |
| Kiểm trong CI (ADR-48) | OpenAPI sinh từ code FastAPI: so tập (phương thức, đường dẫn) với A2, thiếu hoặc thừa thì chặn merge; kiểm mọi thao tác có operationId, tag nhóm A2, x-use-cases, x-capability; Schemathesis chạy trên OpenAPI đó. Tệp openapi.json sinh ra là artefact CI, không sửa tay |

Cách dùng OpenAPI sinh từ code: sinh client TypeScript cho frontend (tab 7 F3); chạy mock server cho nhóm giao diện làm song song; kiểm thử hợp đồng (contract test) mỗi lần build để bảo đảm dịch vụ trả đúng schema; sinh tài liệu tra cứu cho đối tác tích hợp từ các tag Tích hợp và Hỏi đáp.
