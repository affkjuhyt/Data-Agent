# Contract V2: version và tương thích (NOP-150)

`libs/contracts` là hợp đồng dây (wire) giữa indicator-api, platform_workers, workflow_runtime,
kb_backend và frontend. Model nội bộ, logic, state machine ở lại service sở hữu; ở đây chỉ có shape
đi qua REST và Kafka.

## Bốn loại version

| Version | Ở đâu | Ý nghĩa |
| --- | --- | --- |
| `CONTRACT_VERSION` | `libs/contracts/__init__.py`, `info.version` của `generated/contracts.openapi.json` | semver của cả package |
| `SCHEMA_VERSION` | `ClassVar` trên từng model, xuất ra `x-schema-version` | shape của một model |
| `schema_version` | field của `EventEnvelope` | shape của payload một topic; có trong `PAYLOAD_MODELS[(topic, version)]` |
| `envelope_version` | field của `EventEnvelope` (`Literal[1]`) | shape của chính envelope |

Event được version độc lập với storage: thêm cột vào `ingest.*` hay ClickHouse không đổi event nào cho
đến khi một model trong `payloads.py` đổi.

## Thay đổi tương thích (không bump `SCHEMA_VERSION`; `CONTRACT_VERSION` tăng minor)

- Thêm field **optional có default**.
- Thêm model mới, topic mới, payload version mới (version cũ giữ nguyên trong `PAYLOAD_MODELS`).
- Nới ràng buộc (bỏ `min_length`, mở rộng pattern, tăng `le`).
- Thêm giá trị vào enum **mở**: `ErrorCode`, `ParseErrorCode`, `EventType`, `ReviewCaseKind`. Thứ tự
  deploy: consumer trước, producer sau — consumer cũ sẽ từ chối giá trị nó chưa biết.

## Thay đổi breaking (bump `SCHEMA_VERSION` của model đó; `CONTRACT_VERSION` tăng major)

- Xoá hoặc đổi tên field; đổi kiểu field; optional → required; thêm field required.
- Siết ràng buộc (pattern hẹp hơn, `ge`/`le` chặt hơn, `min_length` lớn hơn).
- Xoá giá trị enum bất kỳ.
- Thêm giá trị vào enum **đóng** — consumer phải xử lý đủ mọi giá trị: `ObsStatus`, `TimeBasis`,
  `Origin`, `ChangeKind`, `VersionKind`, `ReleaseProcessingStatus`, `PublicationTimeBasis`, `UseRights`,
  `Purpose`.
- Thêm biến thể vào union phân biệt bằng `kind` / `from` (`ReleaseInfo`, `ReleaseCalendar`,
  `ExtractionProfile`, `SourceRef`).
- Đổi trường khoá phân vùng của một topic (`PARTITION_KEY_FIELD`).
- Đổi nghĩa của field mà không đổi shape. Máy không bắt được; người review phải bắt.

Với payload sự kiện, breaking = thêm `(topic, N+1)` vào `PAYLOAD_MODELS` và giữ `(topic, N)` cho đến
khi mọi producer đã chuyển. Consumer gặp version nó không có thì đẩy message sang DLQ, không đoán
(`UnknownPayloadVersion`).

Nợ đã biết: `unit`, `source_unit`, `frequency` là chuỗi có pattern ở v1. Khi P0-04 thêm D/W/I/P và
`money_per` vào `libs.indicator`, các field đó chuyển sang enum — đó là breaking change có kế hoạch,
bump `SCHEMA_VERSION` của `SeriesRef`, `Observation`, `ObservationCandidate`, `MappingRules`.

## Quy trình đổi contract

1. Sửa model; nếu breaking thì tăng `SCHEMA_VERSION` của model và `CONTRACT_VERSION`.
2. Sửa ví dụ trong `examples/v1/<Model>.json` nếu shape đổi.
3. `python -m libs.contracts.schema_export` (chạy với pydantic 2.9.2, môi trường kb-backend) rồi commit
   `generated/`.
4. Nếu `Observation` đổi: `python -m tests.unit.contracts.compat_fixtures` để làm mới fixture của gate.
5. `make test-unit UNIT_SUITES=contracts:kb-backend`.

Gate CI so `generated/` của nhánh với nhánh gốc và chặn thay đổi breaking không bump version là
NOP-152; fixture cho gate đó ở `tests/fixtures/contracts/compat/` (`expected.json` ghi kết quả phải ra).

## Frontend

Frontend sinh kiểu V2 từ `generated/contracts.openapi.json` (sau NOP-162: từ OpenAPI của indicator-api,
cùng các model này) và không tự viết DTO V2.
