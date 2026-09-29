# P0-09 · Hợp đồng dữ liệu V2 — kế hoạch chi tiết

Nguồn: story [P0-09.md](P0-09.md) (**ưu tiên số 1**: story đã nói thì làm theo story, story không nói
thì chọn cách tối thiểu); dòng P0-09 trong [05-implementation-plan.md](../../docs/v2/05-implementation-plan.md);
[03-target-design.md](../../docs/v2/03-target-design.md) mục 3 và 13; ADR-V2-36, 37, 38 trong
[07-decisions-and-risks.md](../../docs/v2/07-decisions-and-risks.md).
Người làm: SR1 + SR2 (1 tuần-người). Hạn: 3 ngày đầu W1 (**02/10**); mốc cứng C1 **09/10**.
Xong khi (story): các nhánh tuần 2 build và chạy được trên bản mẫu; CI so OpenAPI sinh ra với file đã
commit; đổi một trường mà không tăng phiên bản thì contract test đỏ và chặn phát hành; Observation
thiếu `available_at` bị từ chối và nêu tên trường.

Sơ đồ: [P0-09-vi-tri-trong-he-thong.png](P0-09-vi-tri-trong-he-thong.png),
[luong-nghiep-vu.png](luong-nghiep-vu.png).

| Màu trên sơ đồ | Thành phần | Trong kế hoạch |
| --- | --- | --- |
| Cam (task này làm) | `indicator-api`: danh mục (bước 1), công bố số liệu hiệu lực (bước 2), QuerySpec (bước 3), quản trị/hồ sơ duyệt (bước 4–6), tri thức (sự kiện, nhận định, bằng chứng); Kafka (bước 5–6) | Model trong `domains/`, router theo danh sách của story. Tri thức chỉ có model trong `domains/knowledge`, **không** có router (story không liệt kê). Kafka: chỉ envelope |
| Xanh (dùng tới) | FE Hỏi đáp, Khám phá chuỗi, Hộp duyệt; `series_extractor` | Gọi bản mẫu / dựng envelope |
| Xám (ngoài phạm vi) | Cảnh báo + hộp thư SSE, `workflow_runtime`, kho lưu trữ (trừ Kafka), Kong/Keycloak | Không làm |

Hiện trạng (29/09, `main` = ba49f5a): chưa có `services/indicator_api/`, `tests/contract/`, file
OpenAPI hay envelope trên nhánh nào.

## 1. Cách làm theo story

| # | Vấn đề | Story nói | Kế hoạch |
| --- | --- | --- | --- |
| C1 | Nơi đặt model nghiệp vụ | `indicator_api/domains/{registry,observation,knowledge,governance,query,capability}/` chứa model Pydantic thuần | Làm đúng như vậy |
| C2 | Nơi đặt envelope | "envelope sự kiện dùng chung cho platform_workers và indicator-api", không nói đặt ở đâu | Đặt **chỉ envelope** ở `backend/libs/shared/event_envelope.py`. Image `platform_workers` chỉ copy `backend/libs` và chính nó (`platform_workers/Dockerfile:25-27`), nên đặt trong `indicator_api` thì worker không import được. Dùng lib có sẵn, không tạo lib mới |
| C3 | Trường envelope | "mã sự kiện, phiên bản schema, tenant_id, thời điểm, khoá thứ tự" (+ "trace" ở mục Chưa thiết kế) | `event_type`, `schema_version`, `tenant_id`, `occurred_at`, `partition_key`, `trace_id?`, `payload` |
| C4 | Payload của 12 topic | Chỉ yêu cầu envelope "cho đủ 12 topic", mang khoá phân vùng | `event_type` là enum 12 topic, mỗi topic có quy tắc khoá. `payload` là object JSON; **không** thiết kế model payload từng topic (việc của task sở hữu topic: S3-02, S3-04…) |
| C5 | Khoá phân vùng | `source_id / release_id / series_key / case_id / entity_id` | Theo story. `knowledge.*` dùng `entity_id` (03 mục 13 ghi `object_id`: sửa 03 cho khớp trong cùng commit) |
| C6 | "Quy tắc ánh xạ phiên bản 3" (AC-4) | Đối tượng trong story: "đề xuất quy tắc ánh xạ" = `MappingProposal` | AC-4 làm trên `MappingProposal` (có `version`), không thêm đối tượng mới |
| C7 | Lỗi | 8 mã: `NO_DATA_FOR_PERIOD`, `FREQUENCY_NOT_AVAILABLE`, `SERIES_NOT_APPROVED`, `STALE_SOURCE`, `ACCESS_DENIED`, `INSUFFICIENT_EVIDENCE`, `UNRESOLVED_ENTITY`, 409 `VERSION_CONFLICT` | Đúng 8 mã, không thêm. Thiếu trường (AC-3) dùng 422 mặc định của FastAPI, vốn đã nêu tên trường trong `detail[].loc`; đây là lỗi kỹ thuật, không phải lỗi nghiệp vụ |
| C8 | Phiên bản định nghĩa | Mục 6: mỗi đối tượng có "Phiên bản định nghĩa, tăng khi đổi, ví dụ 1"; "kiểm tra so định nghĩa hiện tại với **bản đã chốt**"; "CI so OpenAPI sinh ra với file đã commit" | Mỗi model có phiên bản riêng, ghi vào schema (`x-schema-version`). **Bản đã chốt chính là `openapi.json` đã commit**, không tạo thư mục snapshot riêng. Contract test so từng schema; schema đổi mà `x-schema-version` không tăng thì đỏ và nêu tên trường |
| C9 | OCC | `expected_version`; lệch thì 409 `VERSION_CONFLICT`, bản ghi trước được giữ | Như story |
| C10 | Công nghệ | FastAPI 0.115 + Pydantic 2.9.2, cùng pin với `kb_backend`, `workflow_runtime` | Như story; image Python 3.11 như `workflow_runtime` |
| C11 | Đường dẫn REST | "story này chốt" | Tiền tố `/api/indicator/...` (không trùng route Kong hiện có); bảng ở PR "stub-api" |
| C12 | Định dạng body lỗi nghiệp vụ | Không nói | Tối thiểu: `{"error": {"code", "message"}}`; 409 thêm `current_version` |

Những gì plan trước tự thêm và **đã bỏ**: lib mới `libs/contracts`, mã `INVALID_REQUEST`, thư mục
snapshot, model payload cho 12 topic, route tri thức, luật `available_at >= publication_time`, ghim
lại pydantic của `platform_workers`, đối tượng `MappingVersion`. Phần deploy lên dev cluster (story không
liệt kê) chuyển sang mục 5, thành việc riêng.

## 2. Thứ tự làm

Story ghi "chỉ chạm một lớp nên không tách task con". Vẫn là **một task**, chia thành 3 PR để review:

```
PR "domains" indicator_api: khung + model domains + mã lỗi ─┬─► PR "stub-api" router bản mẫu + openapi.json + contract test
PR "envelope" envelope (libs/shared)                         ─┘
```

| PR | Ai | Ngày |
| --- | --- | --- |
| PR "domains" | SR2 | T3 29/09 – T4 30/09 |
| PR "envelope" | SR1 | T4 30/09 |
| PR "stub-api" | SR1 + SR2 | T5 01/10 – T6 02/10 |

Mỗi PR phải đạt: test mới xanh; `lint-imports` xanh; mypy strict sạch cho package mới; ruff không có
phát hiện mới trong file đã sửa (CLAUDE.md §8). Không chạy test bằng Python 3.10 của host.

### PR "domains" · Khung `indicator_api` + model trong `domains/`

```
backend/services/indicator_api/
  __init__.py      bắt buộc để import-linter thấy package (story)
  main.py          FastAPI app
  api/             (PR "stub-api")
  domains/registry/      Entity, EntityRelation (SAME_UNDERLYING, SUCCEEDS, NOT_EQUIVALENT, COMPONENT_OF), Series
  domains/observation/   Observation, ObservationCandidate
  domains/knowledge/     Claim, Event, Evidence
  domains/governance/    ReviewCase, Correction, Override, Approval, MappingProposal, mã lỗi
  domains/query/         QuerySpec
  domains/capability/    Capability
  adapters/{postgres,clickhouse,kafka,minio}/   __init__.py trống (story)
  Dockerfile, requirements.txt
```

- Observation bắt buộc đúng danh sách của story: `event_time_start`, `event_time_end`,
  `market_timezone`, `publication_time`, `available_at`, `system_time`, `release_id`,
  `artifact_sha256`, `version_kind` (machine · correction · override · revision),
  `effective_version`, `source_ref`; cùng `series_key`, `value`, `unit` từ bảng `obs.observation`
  (03 mục 3.2). Chỉ kiểm "bắt buộc có" và kiểu; không tự đặt luật giữa các trục thời gian.
- `value: Decimal` (lõi số không dùng float, 12-numeric-core).
- Mỗi model khai báo phiên bản định nghĩa = 1 (C8).
- `Series.frequency: str` tạm thời (dữ liệu giả dùng `"D"`). Không chờ P0-04 và không tạo enum riêng:
  `Frequency` của `libs.indicator.period` hiện chỉ có A/Q/M, còn D/W/I/P là việc của P0-04. Khi P0-04
  xong, **P0-04** đổi trường này sang `Frequency` và tăng phiên bản định nghĩa của `Series` (1 → 2);
  contract test của P0-09 buộc bước tăng phiên bản đó. Trong thời gian chờ, giá trị lạ vẫn được nhận,
  chấp nhận được với bản mẫu.
- Mã lỗi: enum 8 mã của story + bảng mã → HTTP status.
- `.importlinter`: thêm `services.indicator_api` vào `v2-data-boundary` trong cùng commit (story);
  thêm contract "`domains/` không import FastAPI hay client kho". Cấm `fastapi` cần
  `include_external_packages = True`, cờ này ảnh hưởng mọi contract hiện có, nên chạy `lint-imports`
  trước và sau.
- `ci.yml`: thêm `backend/services/indicator_api` vào danh sách mypy strict.
- Test (`tests/unit/indicator_api/`, thêm vào `UNIT_SUITES` và matrix `python-test`, Python 3.11):
  Brent 130,80 ngày 15/09/2026 thiếu `available_at` bị từ chối, lỗi nêu `available_at` (AC-3); thiếu
  từng trường bắt buộc khác cũng bị từ chối (BR-2).

### PR "envelope" · Envelope dùng chung

- `backend/libs/shared/event_envelope.py`: model envelope (C3), enum `event_type` cho 12 topic ở 03
  mục 13 (bỏ `forecast.*`), bảng khoá (C5):

| Topic | Khoá |
| --- | --- |
| `artifact.stored`, `source.sla.breached`, `mapping.proposed` | `source_id` |
| `extraction.completed`, `validation.completed` | `release_id` |
| `review.case.created`, `review.approved` | `case_id` |
| `observation.published`, `indicator.recomputed` | `series_key` |
| `knowledge.proposal.created`, `knowledge.verified`, `alert.raised` | `entity_id` |

- Hàm đọc envelope từ bytes: thiếu trường bắt buộc thì từ chối và nêu tên trường (story, bước 6).
- Không sửa producer: nối envelope vào producer là P0-03 K2 (Ops).
- Test ở hai suite, để chắc cả hai image đều import được (story: "indicator-api đọc envelope bằng cùng
  model Pydantic"): `tests/unit/platform_workers/` dựng envelope; `tests/unit/indicator_api/` đọc lại
  và từ chối envelope thiếu `tenant_id`.

### PR "stub-api" · Router bản mẫu + `openapi.json` + contract test

Router đúng danh sách của story: observations, series, entities, query, governance, review,
capabilities.

| Method | Path | Bản mẫu | AC |
| --- | --- | --- | --- |
| GET | `/api/indicator/entities`, `/entities/{entity_id}` | Brent, WTI và quan hệ | AC-1 |
| GET | `/api/indicator/series`, `/series/{series_key}` | `EIA:RBRTE:D`… với `status` | AC-1 |
| GET | `/api/indicator/series/{series_key}/observations` | Số giả đủ trường bắt buộc (story, bước 2) | AC-1 |
| POST | `/api/indicator/observations` | Thiếu trường thì 422 nêu tên trường | AC-3 |
| POST | `/api/indicator/query` | Chuỗi đang chờ duyệt thì `SERIES_NOT_APPROVED` | AC-6 |
| GET/PATCH | `/api/indicator/governance/mapping-proposals/{id}` | PATCH mang `expected_version`; lệch thì 409 `VERSION_CONFLICT`, bản đầu được giữ | AC-4 |
| GET/POST | `/api/indicator/review/cases`, `/review/cases/{id}/decision` | ReviewCase giả; quyết định mang `expected_version` | BR-3 |
| GET | `/api/indicator/capabilities` | Danh sách capability | AC-1 |

- Bản mẫu giữ dữ liệu và phiên bản trong bộ nhớ. Dữ liệu giả lấy theo ví dụ trong story (Brent 130,80
  ngày 15/09/2026).
- Xác thực dùng lại `libs.shared.auth` như các service khác (header Kong `X-Kong-Verified`,
  `X-Gateway-Key`, `X-Tenant-Id`).
- `openapi.json` commit trong repo (story), xuất bằng một lệnh Python, không cần chạy server.
  FastAPI chỉ đưa vào OpenAPI những model có route dùng tới. Muốn màn hình sinh kiểu cho **mọi** đối
  tượng (AC-5) thì phải thêm Claim, Event, Evidence, Correction, Override, Approval,
  ObservationCandidate và envelope vào `components.schemas` bằng cách ghi đè `app.openapi()`.
- `tests/contract/` (story):
  - OpenAPI sinh ra phải khớp file đã commit.
  - Với mỗi schema, so với bản trong `openapi.json` đã commit. Schema đổi mà `x-schema-version`
    không tăng thì đỏ, in ví dụ `Observation.unit đổi kiểu, phiên bản vẫn 1` (AC-2, mục 6 của story).
  - Chạy trong image `indicator_api`; thêm vào CI.

## 3. Đối chiếu tiêu chí của story

| Story | Kiểm ở đâu |
| --- | --- |
| AC-1 Làm song song trên bản mẫu | Router bản mẫu (PR "stub-api") |
| AC-2 Đổi trường không tăng phiên bản bị chặn | Contract test (PR "stub-api") |
| AC-3 Thiếu `available_at` bị từ chối | Test model (PR "domains"), `POST /observations` (PR "stub-api") |
| AC-4 Xung đột phiên bản | `PATCH …/mapping-proposals/{id}` (PR "stub-api") |
| AC-5 Màn hình dùng kiểu sinh ra | FE-00 PR7 nghiệm thu; P0-09 bảo đảm `openapi.json` có đủ mọi đối tượng |
| AC-6 Mã lỗi thống nhất | `POST /query` (PR "stub-api") |
| BR-1 … BR-5 | `domains/` là nơi định nghĩa duy nhất; PR "domains" (BR-2); C9 (BR-3); C8 + review PR (BR-4); import-linter (BR-5) |

Tài liệu cập nhật trong cùng commit: 03 mục 13 (trường envelope, khoá `knowledge.*` = `entity_id`) và
bảng đường dẫn REST; `CLAUDE.md` §7 và `docs/v2/README.md` (thêm `services/indicator_api`,
`tests/contract`); cột `TT` của P0-09 trong 05 ở commit cuối.

## 4. Hạn chế và rủi ro

- `Series.frequency` để kiểu `str` cho tới khi P0-04 xong (xem PR "domains"). Giữ đúng "Phụ thuộc: không có"
  của story; phần siết kiểu thuộc P0-04, cần ghi vào mô tả P0-04 để BE1 biết.
- `make test-unit` lấy image `$(IMAGE_REGISTRY)/dataagent/<image>:latest` do deploy build. Image
  `indicator-api` chưa được deploy nên ở local phải tự build, hoặc chạy bằng `python:3.11` +
  `requirements.txt` như matrix CI.
- "Chặn phát hành": release management đã bị gỡ (0e6ce20) và `make deploy` không kiểm CI. Contract test
  chỉ chặn thật khi là **required check** của `main`. Chưa kiểm được branch protection (máy không có
  `gh`); job `python-lint` đang đỏ sẵn trên `main`.
- Bản mẫu không có Postgres/ClickHouse (S1-01, S1-02): lọc as-of chỉ trên dữ liệu giả.

## 5. Còn phải hỏi

| # | Câu hỏi | Ai | Chặn |
| --- | --- | --- | --- |
| Q1 | AC-1 cần bản mẫu chạy trên dev cluster (qua Kong, cho FE2 từ 05/10) hay chạy local là đủ? Nếu cần thì deploy (manifest, `service_workloads` trong Ansible, route trong `kong-setup.sh`) là một task riêng | PM + SR1 | FE gọi bản mẫu |
| Q2 | Job contract có được đặt làm required check trên `main` không? | Admin repo | AC-2 "chặn phát hành" |
| Q3 | Ai là "Người vận hành" duyệt PR tăng phiên bản định nghĩa (BR-4)? | PM | BR-4 |
