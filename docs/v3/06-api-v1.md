# 06. API /v1: khối `api`

> Ngày 11/10/2026. Nguồn chuẩn là tài liệu thiết kế hệ thống "Data Agent V3 — Tài liệu phân tích thiết kế
> hệ thống" (Claude Doc `Bj37w3AihkBFvQzPZxcrBc`): tab 4 (Đặc tả API, A1–A3), tab 6 (ADR-28 đến ADR-31,
> ADR-48), tab 3/3b (CSDL). Tệp này ghi cách các quyết định đó thành mã trong repo. Chỗ nào lệch tài
> liệu thì tài liệu đúng; sửa tệp này cùng commit.

## 1. Quyết định đang áp dụng

| Chủ đề | Quyết định | Nguồn |
| --- | --- | --- |
| Triển khai | Modular monolith, 5 khối: `api` (mọi module nghiệp vụ đồng bộ), `worker-ingest`, `worker-agent`, `crawler`, `ml-forecast`. 8 deployable cũ gộp dần vào 5 khối; không thêm service cũ nào nữa | ADR-28 |
| Ranh giới module | Mỗi module một schema, kết nối bằng vai trò `svc_<schema>`; module gọi nhau chỉ qua `__init__` của package; ghi sang schema khác qua API hoặc sự kiện | ADR-28, ADR-01 |
| Ngăn xếp | Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 + asyncpg | ADR-29 |
| Hợp đồng | OpenAPI sinh từ code; CI so danh sách endpoint với A2 và chặn khi thừa hoặc lệch; các tệp `dataagent-openapi*.yaml` chỉ là bản tham khảo, không đưa vào repo | ADR-48 |
| Xác thực | `api` tự kiểm token Keycloak qua JWKS (gateway không thay việc này); tổ chức lấy từ claim `org_id`; năng lực từ `org.role.capabilities` | ADR-30, A1.2 |
| Cô lập tổ chức | Mỗi giao dịch đặt `app.org_id` (và `app.user_id`) bằng `set_config(..., true)`; cấm SET mức phiên; tắt statement cache của asyncpg vì pgbouncer transaction pooling | ADR-31 |
| CSDL | DDL đích dựng lại từ tab 3/3b vào `backend/schema/csdl/`, sinh `backend/alembic/sql/dataagent.sql`; ORM ở `backend/libs/dataagent_db` (phiên làm phần CSDL) | ADR-12, ADR-29 |
| `/v1/data-products` | Một tài nguyên, trường `layer` = `published` (P06) hoặc `context` (P07); hai dòng `POST /v1/data-products` ở A2 là một thao tác | quyết định 11/10/2026 |
| Gateway | Kong vẫn chạy ở cụm dev; APISIX hay Kong chốt ở Giai đoạn 0 sau phép thử đường dẫn `{id}:verb` | ADR-30 |

## 2. Bố cục mã

```
backend/services/api/
  app.py              create_app(): FastAPI, problem handlers, trace, router của các module
  config.py           API_OIDC_ISSUER, API_OIDC_AUDIENCE, API_OIDC_JWKS_URL (URL CSDL do libs.dataagent_db đọc:
                      DATAAGENT_DB_URL__SVC_<SCHEMA>)
  kernel/             dùng chung, không phụ thuộc module nào (.importlinter)
    auth.py           Principal, kiểm token JWKS, IdentityResolver, require(*năng lực), CurrentDataSpace
    db.py             transaction(role, principal): scoped_session của libs.dataagent_db gắn với người gọi
    problems.py       Problem → application/problem+json (A1.7)
    pagination.py     Page[T], page_request (A1.4)
    sse.py            event_stream, Last-Event-ID (A1.6)
    tracing.py        trace_id từ traceparent (ADR-40)
  catalogue/
    a2_endpoints.json bản chụp A2: 432 dòng (431 endpoint, POST /v1/data-products ở hai nhóm), 22 nhóm, schema của từng nhóm
    __init__.py       endpoint(method, path, operation_id=…) → kwargs của route + kiểm năng lực
    check.py          so OpenAPI sinh ra với A2
  modules/<schema>/   một package mỗi schema; đăng ký router ở modules/__init__.py
    org/              danh tính, năng lực, GET /v1/data-spaces
tests/unit/api/       suite `api` (make test-unit UNIT_SUITES=api:api)
tests/integration/api/ suite `api` trên DDL thật, kết nối bằng vai trò svc_* (make test-integration INTEGRATION_SUITES=api:api)
```

Engine, URL và việc đặt `app.*` theo giao dịch nằm ở `libs/dataagent_db` (dùng chung với worker); kernel chỉ
gắn chúng với `Principal` của request.

## 2b. Danh tính và năng lực

Token chỉ mang `sub` (Keycloak) và `org_id`. Mỗi request, `OrgIdentityResolver` (module `org`, vai trò
`svc_org`) đổi `sub` thành `org.app_user` đang `active` rồi gom năng lực:

- toàn tổ chức: `role.capabilities` của `user_role` còn hạn (khẩn cấp hết hạn sau tối đa 24 giờ, BR-44)
  cộng `group_mapping` đang `active`, không gắn Data Space, của các nhóm người dùng thuộc;
- với header `X-Data-Space` (id hoặc mã): thêm vai trò của `data_space_member` còn hạn (BR-142) trong Data
  Space đó, khớp theo người dùng hoặc nhóm. Không phải thành viên hiện hành thì trả 404 như Data Space không
  tồn tại; vai trò toàn tổ chức, kể cả `admin`, không tự mở một Data Space.

Đăng nhập Keycloak được nhưng không có `app_user` đang hoạt động: 403. Route của tài nguyên thuộc Data Space
khai `CurrentDataSpace`: thiếu header thì 400 `data_space_required` (A1.2).

## 3. Viết một endpoint

Route không chép lại A2: nhóm, tóm tắt, `x-use-cases`, `x-capability` và cả phép kiểm năng lực đều đọc từ
`a2_endpoints.json`. Đường dẫn không có trong A2 thì lỗi ngay khi import.

```python
@router.get("/v1/conversations", **endpoint("GET", "/v1/conversations", operation_id="listConversations"))
async def list_conversations(
    principal: CurrentPrincipal,
    page: Annotated[PageRequest, Depends(page_request)],
    request: Request,
) -> Page[ConversationSummary]:
    async with request.app.state.databases.transaction("svc_qa", principal) as session:
        ...
```

- Lỗi: `raise Problem(409, "invalid_state", "...")`; không trả `HTTPException` tự chế, không lộ tên bảng.
- Danh sách: `Page[T]` và `page_request`; con trỏ là khoá sắp xếp của dòng cuối.
- Việc quá 2 giây: 202, header `Location`, thân `{id, status, poll_after_ms}` (A1.6).
- Luồng: `event_stream(...)` với `id` tăng dần; đọc `last_event_id` để nối lại.
- Quyền `channel` và `client` (A1.2) chưa có cách xác thực; `endpoint()` từ chối các route đó cho tới khi
  module `integration` làm xong.

## 4. Cổng A2 (ADR-48)

`tests/unit/api/test_catalogue.py` chạy `catalogue.check.compare` trên app thật; CI chặn khi:

- có thao tác không nằm trong A2;
- nhóm, `x-capability` hoặc `x-use-cases` lệch dòng A2;
- thiếu endpoint của một nhóm đã khai báo ở `COMPLETE_GROUPS` (`catalogue/check.py`).

`tests/unit/api/test_a3_contract.py` giữ hình dạng yêu cầu theo A3: với mọi thao tác của nhóm trong
`COMPLETE_GROUPS`, thân yêu cầu có đúng các trường A3 ghi (trường `*` là bắt buộc) và nhận đủ tham số truy
vấn `?tên` của A3; tham số lọc thêm theo A1.4 được phép. `catalogue/a3_contract.json` sinh từ bản xuất tab 4
(`catalogue/source/tab4.md`) bằng `python services/api/catalogue/build_a3.py` (từ `backend`).

Lỗi CSDL thành problem+json ở kernel (`kernel/problems.py`): SQLSTATE 23514 (CHECK, trigger) → 422
`business_rule_violated` kèm câu của trigger; 23505 → 409 `conflict`; 42501 → 403 `forbidden`; 23503,
23502 → 422 `validation_failed`. Thông điệp của PostgreSQL có tên bảng hoặc ràng buộc không đi ra ngoài
(A1.7); module không cần tự kiểm lại luật CSDL đã giữ.

Idempotency-Key (A1.5) nằm ở kernel (`kernel/idempotency.py`), áp cho mọi POST dưới `/v1`, module không làm gì
thêm. Khoá giữ trong `integration.idempotency_key` theo tổ chức và người gửi (suy từ `sub` của token), được ghi
trước khi handler chạy nên bản gửi trùng lúc bản đầu còn chạy nhận 409. Phản hồi đầu (mã, Content-Type,
Location, ETag, thân) lưu 24 giờ ở Redis (`REDIS_URL`); gửi lại trả đúng phản hồi đó kèm `Idempotent-Replayed:
true`, cùng khoá khác thân trả 422 `idempotency_mismatch`, lỗi 5xx nhả khoá để gửi lại chạy lại. Thiếu
`REDIS_URL` thì header bị bỏ qua (cảnh báo khi khởi động). Bản phát lại chỉ trả sau khi route chạy xong xác thực
và kiểm năng lực (`replay_guard`, `catalogue.endpoint` gắn cho mọi POST), nên người đã mất quyền nhận 401/403 chứ
không nhận lại phản hồi cũ.

ETag và If-Match (A1.5) cũng ở kernel (`kernel/etag.py`). Mọi GET 200 dạng JSON có ETag là băm thân trả về (luồng SSE
thì không). PATCH, PUT và POST `…/{id}:{động từ}` có If-Match được so với ETag của GET `…/{id}` mà chính người gọi
đọc lúc đó (gọi trong tiến trình); lệch trả 412 `precondition_failed` trước khi handler chạy. Không có GET tương ứng
hoặc không gửi If-Match thì không kiểm (A1 ghi "nên gửi"); route bắt buộc If-Match tự kiểm (POST
`/v1/me/clients/{id}:confirm`). Đây không phải khoá: hai người đọc cùng ETag vẫn có thể cùng qua trong vài mili
giây; bảng cần chặt hơn giữ OCC của riêng nó.

Sắp xếp `?order_by=<trường> asc|desc` (A1.4) dùng `order_by(...)` và `keyset(...)` của `kernel/pagination.py`: route
nêu các trường được sắp (cột không NULL) và thứ tự mặc định, kernel lo ORDER BY, con trỏ (kèm id làm khoá phụ) và
giới hạn trang; trường ngoài danh sách trả 422, page_token cấp cho thứ tự khác trả 400. Đã áp cho
`GET /v1/me/clients`, `GET /v1/webhooks/{id}/deliveries`, `GET /v1/security-events`; các danh sách khác chuyển dần
sang cùng helper.

Hạn mức và nhật ký (A1.8) cũng ở kernel. `kernel/ratelimit.py`: mỗi người (tổ chức + `sub` của token) tối đa
`API_RATE_LIMIT_PER_MINUTE` lời gọi mỗi phút (mặc định 120), đếm theo cửa sổ một phút ở Redis; mọi phản hồi `/v1`
có `RateLimit-Limit`, `RateLimit-Remaining`, `RateLimit-Reset`, vượt thì 429 `rate_limited` kèm `Retry-After`.
Client gọi `/mcp` dùng hạn mức riêng của `api_client`. `kernel/access_log.py`: mọi lời gọi `/v1` thêm một dòng
`audit.audit_log` sau khi có mã phản hồi (`action` = `api.<operation_id>`, đối tượng là `{id}` của đường dẫn,
`outcome` ok / denied cho 401, 403, 429 / error); không ghi thân. Ghi lỗi thì chỉ ghi log ứng dụng, vì lời gọi đã
xảy ra; việc bắt buộc phải có nhật ký trước khi làm (Kiểm toán, `/mcp`) tự ghi trong chính thao tác.

A2 có 431 endpoint (48 thêm ngày 12/10/2026), làm dần theo nhóm: một nhóm vào `COMPLETE_GROUPS` trong commit phục vụ endpoint cuối
của nhóm, từ đó bỏ một endpoint là lỗi. Thêm hoặc bỏ endpoint phải sửa tab 4 A2 và `a2_endpoints.json`
trong cùng PR. Xem độ phủ: `python -m services.api.catalogue.check` trong image `api`.

## 5. Thứ tự làm

| Bước | Nhóm A2 (schema) | Vì sao |
| --- | --- | --- |
| API-00 | Khung `api`: kernel, cổng A2, suite, CI | Nền cho mọi module |
| API-01 | Bộ giải danh tính và năng lực (`org`), `GET /v1/data-spaces`, `GET /v1/me` | Mọi route cần năng lực từ `org.role.capabilities` |
| API-02 | Hỏi đáp (`qa`), Truy vấn (`query`) | Vòng lõi: câu hỏi, luồng SSE có `answer_id`, câu trả lời, biên nhận |
| API-03 | Phiên làm việc (`workspace`) | Màn Phiên |
| API-04 | Cần bạn và đánh giá (`inbox`, `eval`) | Màn Cần bạn, HITL |
| API-05 | Agent (`agent`) | Lượt chạy, `/v1/runs/{id}/events` |
| API-06 | Triển khai: manifest `deploy/k8s/*/service/api.yaml`, Ansible `service_workloads`, route `/v1` ở gateway, secret `DATAAGENT_DB_URL__*` từ OpenBao | Sau khi API-02 có dữ liệu thật |

`GET /v1/me` được thêm vào A2 (nhóm Quản trị) ngày 11/10/2026; các `/v1/me/*` khác frontend đang gọi vẫn là
PROPOSAL và đi theo nhóm của chúng.

Frontend sinh client TypeScript từ `/v1/openapi.json` (tab 7 F3) và tắt mock theo từng nhóm A2 đã nối
(`frontend/src/mocks/integrated.ts`, [05 §4](05-ui-v3.md)). Các nhóm còn lại đi theo thứ tự màn hình người dùng
chạm trước.

## 6. Ai trả lời câu hỏi: khối `worker-agent`

`api` chỉ ghi câu hỏi và phát `question.received` vào `qa.outbox`; `backend/services/worker_agent` (khối thứ hai
của ADR-28) trả lời. Bước G1 của [09](09-target-deploy-and-cleanup.md):

- **Nhận sự kiện.** `events.OutboxTableSource` đọc `qa.outbox` bằng vai trò `svc_outbox_relay` (đọc mọi tổ chức,
  chỉ được đóng dấu `published_at`), `FOR UPDATE SKIP LOCKED` để nhiều bản sao không lấy trùng, và chỉ đóng dấu khi
  handler đã commit. Đây là nguồn tạm khi cụm chưa có Debezium (ADR-06/32); handler chỉ thấy `Event`, nên thay bằng
  consumer Kafka là việc cục bộ của `events.py`.
- **Trả lời.** `questions.answer_question` chạy như `svc_qa` trong tổ chức của sự kiện: ghi `qa.plan` (Plan IR của
  R0-10), `qa.answer`, chuyển câu hỏi `received → refused/answered`; luồng `GET /v1/questions/{id}/stream` suy sự
  kiện từ đó. Handler idempotent: nhận lại sự kiện thì không làm gì.
- **Yêu cầu duyệt.** `hitl.open_request` mở `inbox.hitl_request` từ sự kiện `hitl.requested` mà mọi module ghi vào
  outbox của mình (payload từ `inbox.public.hitl_requested`), đọc cả outbox của CSDL audit qua khoá kết nối
  `svc_outbox_relay_audit`; `resolutions` xử lý `hitl.resolved` (bản xuất kiểm toán được duyệt thì chuyển `queued`).
  Worker cần `DATAAGENT_DB_URL__SVC_QA`, `__SVC_INBOX`, `__SVC_AUDIT`, `__SVC_OUTBOX_RELAY`,
  `__SVC_OUTBOX_RELAY_AUDIT`; api cần một URL cho mỗi `svc_<schema>` nó dùng và `__SVC_AUDIT` (CSDL `<db>_audit`).
- **Giới hạn hiện tại.** Bộ biên dịch ngữ nghĩa và bộ thực thi vẫn ở runtime cũ, nên câu hỏi kết thúc bằng lời từ
  chối có mã (`INSUFFICIENT_EVIDENCE` khi Data Space chưa có data product đang hoạt động, `TOOL_UNAVAILABLE` khi đã
  có), không bao giờ bằng con số không truy được (BR-171). Chuyển lõi agent sang đây là việc R1.

Chạy: `python -m services.worker_agent.main`; suite `worker_agent` (unit + tích hợp trên DDL thật).
