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

## Gate CI (NOP-152)

Job `contract-compat` của CI (cục bộ: `make contract-compat`, base mặc định `origin/main`) chạy
`python -m libs.contracts.compat --base <generated/ của nhánh gốc> --head backend/libs/contracts/generated`.
Checker chỉ đọc JSON, không import model. Nó so bản đã chốt (`generated/` ở đỉnh nhánh gốc) với
`generated/` của PR và in mỗi thay đổi bị chặn một dòng, ví dụ:

```
BLOCKED API Observation.unit: type string -> object (x-schema-version 1 -> 1)
BLOCKED API Observation.obs_status -> ObsStatus: enum value 'x' added to a closed enum (x-schema-version 1 -> 1)
BLOCKED event mapping.proposed.v1: partition key source_id -> series_key (payload mapping.proposed.v1 is published: add v2 instead)
```

| Exit | Nghĩa |
| --- | --- |
| 0 | tương thích, hoặc mọi thay đổi breaking đã bump |
| 1 | có thay đổi bị chặn, hoặc file đầu vào không đọc được |
| 2 | nhánh gốc chưa có `generated/` (trước khi NOP-150 vào main): chỉ báo, không fail |

Bị chặn thì sửa theo một trong các cách:

- **Model REST**: tăng `SCHEMA_VERSION` của model được nêu. Breaking trong một enum hay def không có
  version thì được gán cho mọi model dùng nó, và từng model đó phải bump.
- **Event**: file `<topic>.vN` đã phát hành thì không đổi shape, kể cả khi đã bump `SCHEMA_VERSION`, vì consumer
  chọn model theo `schema_version` của envelope. Thêm `(topic, N+1)` vào `PAYLOAD_MODELS`.
- **Xoá** một model hay một file payload: tăng major `CONTRACT_VERSION`.
- **Nới `pattern`/`format`**: máy không phân biệt được regex mới rộng hơn hay hẹp hơn, nên mọi thay đổi đều bị
  chặn, trừ khi được khai báo trong `compat-accepted.json`. Mỗi mục khoá đúng chuỗi cũ và chuỗi mới, và chỉ áp
  cho `pattern`/`format`; reviewer duyệt mục đó trong diff:

  ```json
  [{"schema": "SeriesRef", "field": "unit", "keyword": "pattern",
    "from": "^[a-z]+$", "to": "^[a-z0-9]+$", "reason": "đơn vị có chữ số (m3)"}]
  ```

  `field` là phần sau tên model trong dòng `BLOCKED`. Mục cũ không còn khớp với gì nữa và được dọn khi bump
  `CONTRACT_VERSION`.

Gate đọc hai thông tin `schema_export` gắn vào `generated/`: `x-open-enum` (enum mở ở trên; độ mở được lấy
theo **base**, nên không thể vừa mở enum vừa thêm giá trị trong cùng một PR) và `x-partition-key` (khoá phân
vùng của mỗi payload).

Doc không nói tới chiều required → optional. Gate coi đó là **breaking**: consumer còn model cũ sẽ từ chối
payload thiếu field đó. Gate bỏ qua `title`, `description`, `examples` và `default`; đổi `default` là đổi nghĩa,
và người review phải bắt.

Fixture của gate ở `tests/fixtures/contracts/compat/` (`expected.json` ghi kết quả phải ra). Test ở
`tests/unit/contracts/test_compat.py`.

## Frontend

Frontend sinh kiểu V2 từ `generated/contracts.openapi.json` (sau NOP-162: từ OpenAPI của indicator-api,
cùng các model này) và không tự viết DTO V2.
