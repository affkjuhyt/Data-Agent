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
  config.py           API_OIDC_ISSUER, API_OIDC_AUDIENCE, API_OIDC_JWKS_URL, DATAAGENT_DB_URL__SVC_<SCHEMA>
  kernel/             dùng chung, không phụ thuộc module nào (.importlinter)
    auth.py           Principal, kiểm token JWKS, require(*năng lực)
    db.py             một engine mỗi vai trò, transaction(role, principal) đặt app.org_id
    problems.py       Problem → application/problem+json (A1.7)
    pagination.py     Page[T], page_request (A1.4)
    sse.py            event_stream, Last-Event-ID (A1.6)
    tracing.py        trace_id từ traceparent (ADR-40)
  catalogue/
    a2_endpoints.json bản chụp A2: 378 endpoint, 22 nhóm, schema của từng nhóm
    __init__.py       endpoint(method, path, operation_id=…) → kwargs của route + kiểm năng lực
    check.py          so OpenAPI sinh ra với A2
  modules/<schema>/   một package mỗi schema; đăng ký router ở modules/__init__.py
tests/unit/api/       suite `api` (make test-unit UNIT_SUITES=api:api)
```

`kernel/db.py` là bản tạm. Khi `libs/dataagent_db` có `scoped_session(role, org_id, user_id)` với cùng
ngữ nghĩa thì module dùng thư viện đó và xoá bản trong kernel.

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

A2 có 378 endpoint, làm dần theo nhóm: một nhóm vào `COMPLETE_GROUPS` trong commit phục vụ endpoint cuối
của nhóm, từ đó bỏ một endpoint là lỗi. Thêm hoặc bỏ endpoint phải sửa tab 4 A2 và `a2_endpoints.json`
trong cùng PR. Xem độ phủ: `python -m services.api.catalogue.check` trong image `api`.

## 5. Thứ tự làm

| Bước | Nhóm A2 (schema) | Vì sao |
| --- | --- | --- |
| API-00 | Khung `api`: kernel, cổng A2, suite, CI | Nền cho mọi module |
| API-01 | Quản trị phần đọc của tôi + bộ giải năng lực (`org`) | Mọi route cần năng lực từ `org.role.capabilities` |
| API-02 | Hỏi đáp (`qa`), Truy vấn (`query`) | Vòng lõi: câu hỏi, luồng SSE có `answer_id`, câu trả lời, biên nhận |
| API-03 | Phiên làm việc (`workspace`) | Màn Phiên |
| API-04 | Cần bạn và đánh giá (`inbox`, `eval`) | Màn Cần bạn, HITL |
| API-05 | Agent (`agent`) | Lượt chạy, `/v1/runs/{id}/events` |
| API-06 | Triển khai: manifest `deploy/k8s/*/api.yaml`, Ansible `service_workloads`, route `/v1` ở gateway, secret `DATAAGENT_DB_URL__*` từ OpenBao | Sau khi API-02 có dữ liệu thật |

Frontend sinh client TypeScript từ `/v1/openapi.json` (tab 7 F3) và tắt `VITE_MOCK_V1` theo từng nhóm khi
nhóm đó vào `COMPLETE_GROUPS`. Các nhóm còn lại đi theo thứ tự màn hình người dùng chạm trước.
