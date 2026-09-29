# FE-00 · Nền frontend V2 — kế hoạch chi tiết

Nguồn: [09-frontend.md mục 4.1–4.2](../docs/v2/09-frontend.md), dòng FE-00 trong
[05-implementation-plan.md](../docs/v2/05-implementation-plan.md). Người làm: FE1 (0,75 tuần) + SR1
(script sinh kiểu, 0,25 tuần), W1–W2 (28/09–09/10). Nghiệm thu: **màn V2 đầu tiên dùng kiểu sinh ra**.

## 1. Các quyết định đã chốt (29/09/2026)

| # | Vấn đề | Chốt |
| --- | --- | --- |
| Q1 | P0-09 (OpenAPI `indicator-api`) chưa xong | Chỉ PR sinh kiểu chờ P0-09; mọi PR khác làm ngay |
| Q2 | `gen:api` lấy OpenAPI từ đâu | File `openapi.json` commit trong repo, xuất từ code Python (không cần server); CI xuất lại và so sánh |
| Q3 | Lỗi API mất status/mã | Thêm `ApiError` (`status`, `code`) vào `lib/apiClient.ts`, kế thừa `Error`; không tạo client V2 riêng |
| Q4 | `streamReducer` | Reducer xử lý **cả frame cũ lẫn V2**; PR riêng chuyển `useAgentStream` sang dùng nó |
| Q5 | Xác thực SSE hộp thư | `fetch` + `readSseStream` với `Authorization: Bearer` (như màn Ask); không dùng `EventSource` |
| Q6 | Nút Back với tham số URL | `pushState` khi chọn `series` / `case` / `alert` / `run`; `replaceState` khi đổi `as_of` / `as_of_mode` liên tục |

## 2. Thứ tự PR

```
PR1 ApiError ──► PR2 TanStack Query ──► PR6 Hộp thư SSE
PR3 urlState                               ▲
PR4 streamReducer ──► PR5 Ask dùng reducer │ (dùng readSseStream)
                                           │
P0-09 (SR1/SR2) ──► PR7 Sinh kiểu OpenAPI  (bị chặn)
```

PR1, PR3, PR4 độc lập, làm song song được. Mỗi PR: `npm run typecheck`, `npm test` xanh; knip,
`typecheck:strict`, `format:check` không có phát hiện **mới** so với `main` (các bước này đang chỉ
báo cáo, CLAUDE.md §1).

### PR1 · `ApiError` trong `apiClient`

- `lib/apiClient.ts`: class `ApiError extends Error { status; code? }`. `apiGet`, `apiMutate`,
  `apiUpload` ném `ApiError` thay cho `Error`; `code` đọc từ envelope `error.error_type` hoặc
  `detail.code` (định dạng chính xác phụ thuộc P0-09, xem mục 5).
- Giữ nguyên hành vi cũ: `.message` như trước, sự kiện `app:forbidden` khi 403, `StaleVersionError`
  khi 412 (hệ cũ dùng If-Match/412; V2 dùng `expected_version`/409 — hai cơ chế cùng tồn tại).
- Thêm helper `isVersionConflict(err)` → `status === 409`.
- Test: `lib/apiClient.test.ts` — 2xx, envelope lỗi, 403, 409, 412, body không phải JSON.
- Rủi ro: các màn cũ `catch (e)` rồi đọc `e.message` — không đổi, vì `ApiError` là `Error`.

### PR2 · TanStack Query

- Thêm `@tanstack/react-query` v5 (dependency).
- `lib/queryClient.ts`: `QueryClient` mặc định — không retry với 4xx (`ApiError.status < 500`),
  `staleTime` hợp lý cho màn đọc; bọc `QueryClientProvider` trong `main.tsx` (bên trong `AuthProvider`).
- Quy ước 409: mutation gặp `isVersionConflict` → `invalidateQueries` key của đối tượng → màn hiện
  "đã có người cập nhật" và diff mới (màn hiện diff là việc của S6-03, không thuộc FE-00).
- Chỉ màn V2 dùng; **không** chuyển màn cũ sang Query.
- Test: 409 gọi invalidate đúng key; 4xx không retry.

### PR3 · `lib/urlState.ts`

- Tham số: `series`, `as_of`, `as_of_mode` (`world` | `system`), `case`, `alert`, `run`.
- API: `readUrlState()`, `useUrlState()` (đọc + ghi, lắng nghe `popstate`), ghi theo Q6.
- Phải chung sống với `AppStateContext.tsx`: `writeRoute` đang `replaceState` path và **giữ nguyên
  query string** (để Keycloak đọc `?code=&state=`) → `urlState` không được xoá `code`, `state`,
  `session_state` và các tham số không thuộc nó.
- Kiểm hợp lệ: `as_of` là ngày ISO, `as_of_mode` ngoài hai giá trị → bỏ qua, không lỗi.
- Test: đọc/ghi, push và replace đúng tham số, `popstate`, giữ tham số Keycloak, giá trị sai.

### PR4 · `features/ask/streamReducer.ts` + frame V2

- `sse.ts`: `parseSseData` nhận thêm frame V2 (09 mục 4.2): `__run`, `__task_graph`, `__step`,
  `__block`, `__verification`, `__lineage`, `__refusal`; mở rộng `__evidence`/`__citations`
  (`page`, `bbox`, `release_id`, `available_at`, `licence`). Frame cũ giữ nguyên.
- `streamReducer(state, event) → state` thuần, xử lý **mọi** frame (cũ + V2); khối lạ trong
  `__block` bị từ chối (09 mục 4.3).
- Kiểu frame V2 viết tay tạm trong `features/ask/types.ts` cho tới PR7 (frame đi qua runtime, không
  chắc có trong OpenAPI của `indicator-api` — xem mục 5).
- Test: thứ tự frame, frame lạ, `__reset`, interrupt (`__clarification`, `__pending_review`),
  `__run` error, khối lạ.
- Chưa đụng `useAgentStream` (Q4).

### PR5 · `useAgentStream` dùng `streamReducer`

- Thay khối `switch` trong `features/ask/hooks/useAgentStream.ts` bằng `streamReducer`.
- Test hồi quy màn Ask: token, reset, clarification (resume và followup), evidence, chart, options,
  lỗi, abort. Chạy tay trên dev cluster (mục 3) trước khi merge.
- Tách riêng để có thể revert mà không mất PR4.

### PR6 · Kết nối hộp thư SSE

- `lib/notifications.ts` (hoặc `hooks/useNotificationStream.ts`): một kết nối
  `GET /api/notifications/stream` qua `fetch` + `readSseStream` (Q5).
- Tự reconnect với backoff; lấy lại token (`keycloak.updateToken`) trước khi mở lại; dừng khi
  logout; một kết nối cho toàn app (không mở theo từng màn).
- Nhận sự kiện → `invalidateQueries` key ReviewCase / alert tương ứng (PR2).
- Test bằng stream giả: parse, reconnect, token hết hạn, abort khi unmount.
- **Backend chưa có** (S6-03, SR1, W3) → PR này merge với test giả; nối thật ở S6-03.

### PR7 · Sinh kiểu từ OpenAPI (chờ P0-09)

- SR1: Python CLI trong `indicator-api` xuất `app.openapi()` ra file `openapi.json` commit trong repo.
- FE1: devDependency `openapi-typescript`; script `npm run gen:api` sinh
  `src/lib/api/indicator.d.ts`; bước CI: xuất lại OpenAPI + `gen:api` + `git diff --exit-code`.
- Thay kiểu viết tay tạm (PR4) nếu frame có trong OpenAPI.
- Nghiệm thu FE-00 chỉ đạt khi màn V2 đầu tiên (S1-03 hoặc S3-05, FE2) dùng kiểu này → cập nhật
  cột `TT` của FE-00 trong `05-implementation-plan.md` ở commit đó (CLAUDE.md §9).

## 3. Service cần có từ VM để làm local

Frontend chạy local (`npm run dev`, cổng 3000); mọi API đi qua Kong. Không cần mở trực tiếp cổng
database — frontend chỉ nói chuyện với Kong và Keycloak.

| Service (k8s) | Cổng dev | Cần cho | Bắt buộc |
| --- | --- | --- | --- |
| `keycloak` | 30110 | Đăng nhập, token | Có (mọi PR) |
| `kong` (proxy) | 30100 | Mọi `/api/*` | Có (mọi PR) |
| `auth-api` | sau Kong | App gọi `/api/auth/me/tenants` khi khởi động | Có |
| `kb-backend` | sau Kong | App gọi `/api/data/documents` khi khởi động | Có (thiếu thì app vẫn lên nhưng báo lỗi) |
| `workflow-runtime` | sau Kong | `/api/orchestrate/stream` — kiểm tay màn Ask | PR4–PR5 |
| `litellm` + vLLM main/fast (GPU `:8001`, `:8002`) | 30500 | Runtime cần LLM để trả lời | PR5 |
| Postgres, Redis, Qdrant, Neo4j, ClickHouse… | — | Phụ thuộc của các service trên, không gọi trực tiếp | Gián tiếp |
| `indicator-api` | — | **Chưa tồn tại** (P0-09) | PR7 |
| `/api/notifications/stream` | — | **Chưa tồn tại** (S6-03) | PR6 (thật) |

Biến môi trường (`.env` ở gốc repo, xem `.env.example`):

```
VITE_API_URL=http://<kong-host>:30100
VITE_WORKFLOW_RUNTIME_URL=http://<kong-host>:30100
VITE_FLOW_BUILDER_URL=http://<kong-host>:30100
VITE_KEYCLOAK_URL=http://<keycloak-host>:30110
```

Tài khoản: realm `aeroflow`, tenant `a0000000-0000-0000-0000-000000000001` (docs/RUNBOOK.md §2).

**Origin của trình duyệt**: Kong CORS và client Keycloak `aeroflow-frontend` chỉ cho phép
`http://localhost:3000`, `:8150`, `localhost:30280`, `10.10.55.101:30280`. Mở frontend bằng
`localhost:3000` thì chạy được; mở bằng IP của VM làm việc (vd `http://<ip>:3000`) sẽ bị CORS và
Keycloak từ chối redirect → cần thêm origin (đổi deploy, RUNBOOK §6) hoặc truy cập qua `localhost`.
Realm đang chạy chỉ import `realm-export.json` lúc tạo lần đầu → kiểm lại redirect URI trong admin
console.

## 4. Hạn chế

- Máy hiện tại không gọi được VM (`10.10.55.101:30100` timeout); làm trên VM khác trong LAN.
- Node local v24, CI dùng Node 20 → trước khi đẩy PR chạy thử bằng Node 20 nếu có khác biệt.
- Runtime chưa phát frame V2 (S9-02, S10-01) → PR4 chỉ kiểm bằng fixture, chưa thấy trên màn thật.
- Chưa có màn V2 nào → TanStack Query, `urlState`, hộp thư chưa có nơi dùng thật trong FE-00; knip
  có thể báo export chưa dùng.
- Hai cơ chế OCC cùng tồn tại: If-Match/412 (kb-backend cũ) và `expected_version`/409 (V2).
- Frontend không có e2e trình duyệt trong POC (09 mục 6); kiểm tay qua dev cluster.

## 5. Việc chưa xử lý được / câu hỏi còn mở

| # | Việc | Chờ ai | Chặn |
| --- | --- | --- | --- |
| O1 | OpenAPI `indicator-api` (P0-09) chưa có | SR1, SR2 | PR7, nghiệm thu FE-00 |
| O2 | Vị trí file `openapi.json` và lệnh Python xuất nó | SR1 | PR7 |
| O3 | Định dạng lỗi 409: `code` nằm ở envelope `error.error_type` hay `detail.code`? | SR1 (P0-09) | PR1 đọc `code` đúng chỗ |
| O4 | Endpoint `/api/notifications/stream`: schema sự kiện, route Kong, backend (Redis pub/sub) | SR1 (S6-03) | PR6 nối thật |
| O5 | Frame V2 (`__block`, `__step`…) có nằm trong OpenAPI không, hay giữ kiểu viết tay? | SR1 | PR7 |
| O6 | Khi chuyển module (vd Series → Settings), tham số V2 trên URL giữ hay xoá? `writeRoute` hiện giữ nguyên query | FE1 / PM | PR3 |
| O7 | Knip báo export chưa dùng trước khi có màn V2: chấp nhận tạm hay thêm vào `knip.json`? | FE1 | PR2, PR3, PR6 |
| O8 | Nếu làm từ VM khác qua IP (không phải `localhost`): có thêm origin vào Kong CORS + Keycloak không? | Ops | Chạy local |
