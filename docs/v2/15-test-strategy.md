# 15. Chiến lược kiểm thử, traceability và Definition of Done cho V2

> Linear: NOP-166 (con của NOP-165 "Quality Engineering & Test V2"). Viết 30/09/2026.
> Kiến trúc và bất biến: [00-architecture-v2.md](00-architecture-v2.md). Tiêu chí nghiệm thu, golden
> set, dung sai: [06-evaluation-plan.md](06-evaluation-plan.md). Use case: [08-use-cases.md](08-use-cases.md).
> Cách chạy test trong repo (image theo service, testcontainers, e2e): [tests/README.md](../../tests/README.md).
> Traceability matrix cấp issue: [data/test-traceability.csv](data/test-traceability.csv).

File này trả lời: một issue V2 phải có test gì thì được merge, một feature phải qua suite nào thì được
coi là xong, một mốc C1/C2/C3 cần gì để đạt, và làm sao chứng minh từng requirement đã được kiểm.
Definition of Ready/Done theo vertical slice UC (7 cột, applicability theo layer) thuộc NOP-211; file
này là phần kiểm thử của DoD đó. Pipeline CI chạy các cổng ở đây là NOP-240; môi trường eval tenant,
seed và reset là NOP-241.

## 1. Nguyên tắc

1. **Test đi cùng code.** Unit và contract test là một phần của leaf issue, không phải ticket QA sau.
   Suite QA (NOP-167…176) chỉ kiểm phần xuyên module: tích hợp, E2E, đúng dữ liệu, bảo mật, tải, lỗi.
2. **Mỗi test truy được về requirement**: UC → issue → fixture → kết quả kỳ vọng → lần chạy (mục 7).
3. **Tất định trước.** Số, quyền, thời gian, trạng thái được kiểm bằng so sánh chính xác. Không dùng LLM
   làm giám khảo cho những gì kiểm tất định được (mục 5).
4. **Dữ liệu kiểm thử có tên.** Mọi test dùng fixture trong catalog (mục 8), chạy lại cho cùng kết quả,
   không gọi nguồn thật hay model thật trong unit/integration.
5. **Coverage theo requirement là thước đo chính.** Code coverage (`fail_under = 48`) vẫn giữ nhưng chỉ
   là tín hiệu phụ; một issue Must không có test gắn tag là "chưa kiểm", dù coverage cao đến đâu.
6. **Một test đỏ là regression thật** (CLAUDE.md mục 1): sửa, hoặc giải thích trong issue; không bỏ qua
   vì "có sẵn từ trước".
7. **Mỗi bất biến của 00 mục 0 có ít nhất một test âm**: cố tình vi phạm và thấy bị chặn. Ví dụ: UPDATE
   artifact raw, số có cấu trúc đi đường chunk, câu trả lời chứa số không có trong bằng chứng, planner gọi
   tool chưa đăng ký, thấy dữ liệu công bố sau as-of, ghi không kèm `expected_version`, gọi LLM cho một
   cập nhật không đáng kể.

## 2. Ba cổng

| Cổng | Khi nào | Ai chịu trách nhiệm | Điều kiện |
| --- | --- | --- | --- |
| **Merge** | Mỗi PR của một leaf issue | Người làm issue | Test ở cột `test_levels` của issue (CSV) có mặt, gắn `req`, xanh; DoD theo vai trò (mục 3) |
| **Story/Feature** | Khi mọi con của story đã merge | Owner suite QA ở cột `qa_gate` | Suite QA tương ứng xanh trên eval tenant; case của story trong suite có kết quả |
| **Mốc** | C1 09/10, C2 16/10, C3 22/10, nghiệm thu 27/10 | Lead + owner suite | Bảng dưới; báo cáo coverage theo requirement của mốc không còn issue `uncovered` hay `failing` |

| Mốc | Suite QA phải xanh | Tích hợp (INT) | Kịch bản | Golden set |
| --- | --- | --- | --- | --- |
| C1 | NOP-167 contract, NOP-168 pipeline C1, NOP-169 isolation | NOP-160, NOP-161, NOP-164 | 27 bước: 1–6; Việt Nam: tỷ giá, SJC tự nạp | 30 case đã duyệt + baseline tuần 1 (NOP-42) |
| C2 | + NOP-170 tài liệu, NOP-171 governance | + NOP-155, NOP-239 | 27 bước: 7–16; Việt Nam: 1–5 | 45 case |
| C3 | + NOP-172 agent, NOP-173 tải, NOP-174 lỗi, NOP-175 bảo mật | + NOP-157, NOP-158 | 27 bước: 17–24; Việt Nam: 6–7 | 45 case, tier `commodity` chạy hằng ngày (NOP-78) |
| Nghiệm thu | + NOP-176 browser E2E | | 1–27 và 1–8, **hai lần liên tiếp từ trạng thái sạch** (NOP-66) | 60 case, ngưỡng 06 mục 5 |

Mốc trễ vì một suite đỏ thì áp dụng thứ tự cắt ở 05 mục 5; không hạ ngưỡng để qua mốc.

## 3. Definition of Done theo vai trò

Chung cho mọi leaf issue có mã:

- Test mới gắn đúng **một** level (`unit` / `integration` / `e2e`), các kind liên quan, và
  `req("<NOP-id>", "<UCxx>")` (mục 4). Bug fix: có test đỏ trước khi sửa, xanh sau khi sửa.
- `make test-unit` xanh cho suite của service bị chạm; `make test-integration` khi chạm DB, Kafka,
  MinIO, ClickHouse; không có finding ruff/mypy mới so với baseline (CLAUDE.md mục 2, 8).
- Fixture lấy từ catalog (mục 8). Unit/integration không gọi mạng ngoài và không gọi model thật: model
  đi qua stub của ModelGateway (NOP-159).
- Đổi test level của issue thì sửa dòng của issue trong `data/test-traceability.csv` cùng PR.

| Vai trò | Bắt buộc trước merge | Không đủ để coi là xong |
| --- | --- | --- |
| **BE** | `unit`: logic miền (validator, phân loại, operator, resolver) với luồng chính, **mỗi luật nghiệp vụ** và mỗi mã lỗi. `contract`: request/response đúng OpenAPI kèm ví dụ, mã lỗi đã chốt (`409 VERSION_CONFLICT`, 403, 404, 422), envelope sự kiện. `integration`: Postgres qua alembic head, chạy bằng `app_user` (RLS), OCC hai người ghi thì đúng một người thắng, ghi qua outbox, chạy lại lần hai không đổi kết quả | Test chỉ gọi hàm với mock DB rồi kiểm "không lỗi" |
| **Data** (schema, migration, store) | Migration lên/xuống/lên sạch; ràng buộc (unique một phần, append-only, grant); FORCE RLS với tenant chưa đặt → 0 dòng và ghi tenant khác bị từ chối; row policy ClickHouse; seed và backfill chạy hai lần như một | Kiểm schema bằng mắt trên DB dev |
| **FE** | `unit` (vitest) cho từng trạng thái UX đã khai ở DoR: loading, empty, error, không quyền, xung đột (409). `contract`: kiểu sinh từ OpenAPI (`tsc` xanh), dữ liệu giả lấy từ ví dụ contract (NOP-151), không tự bịa shape. Số và ngày qua `formatVN`. Truy vấn phần tử theo role/label | Snapshot test làm test duy nhất; luồng nhiều màn (thuộc NOP-176) |
| **AI/ML** | `ai_eval` trên tập có nhãn, ghim phiên bản model + prompt + cấu hình; metric và ngưỡng ghi trong `model/eval/thresholds.json` (`provisional` tới khi có hai lần chạy ổn định); báo cáo đính vào issue. `unit` cho adapter: output hỏng, thiếu trường, timeout đều ra lỗi có mã. Đổi model/prompt đi qua NOP-76 | Điểm trung bình không kèm tập, phiên bản, số case |
| **DevOps** | Manifest/role render và kiểm được (dry-run), smoke trên namespace dev (health, readiness), lệnh rollback đã chạy thử, luật alert bắn trên tình huống giả. Secret, network policy, ACL có test phủ định (bị chặn thật) | "Đã deploy được" mà không có smoke tự động |
| **INT** | `integration` + `e2e` trên eval tenant qua service thật, model stub; kiểm trạng thái từng chặng (hàng DB, sự kiện Kafka, object raw); chạy hai lần từ trạng thái sạch cho cùng kết quả | Chỉ kiểm HTTP 200 ở đầu cuối |
| **QA suite** | Owner độc lập với người làm chính phần được kiểm (mục 9); mỗi case gắn `req`; báo cáo có git SHA, image, cấu hình model, fixture | Case không truy được về issue |

Story không có test ở cổng merge: story xong khi các con xong **và** suite ở `qa_gate` xanh, và các case
của suite gắn `req` với ID story (đó là cách story có test trong báo cáo mục 7). Feature xong khi mọi story
xong và đạt cổng mốc của nó. Issue PM/BA được lead duyệt, không có test.

## 4. Level, kind và tag

**Level (đúng một, đã có trong `conftest.py`):**

| Level | Chạy ở đâu | Được dùng gì |
| --- | --- | --- |
| `unit` | `tests/unit/<service>/`, image của service, mọi PR | Python thuần, SQLite/in-memory; không Docker, không mạng |
| `integration` | `tests/integration/<service>/`, testcontainers, mọi PR | Postgres/Kafka/Redis/MinIO/ClickHouse thật trong container; model stub |
| `e2e` | `tests/integration/test_*.py` (`make e2e`), harness NOP-66, Playwright NOP-176 | Cụm dev/eval tenant qua gateway |

**Kind (không hoặc nhiều, đăng ký trong `pyproject.toml`):**

| Kind | Nghĩa | Nơi chính |
| --- | --- | --- |
| `contract` | Ghim contract đã công bố: OpenAPI, envelope sự kiện, frame agent, port ObservationStore | NOP-167; mọi BE/FE leaf chạm contract |
| `security` | Quyền, cô lập tenant, input không tin cậy | NOP-169, NOP-175 |
| `resilience` | Hành vi khi phụ thuộc lỗi: retry, gửi trùng, restart, timeout | NOP-174 |
| `performance` | Đo độ trễ/thông lượng so ngân sách | NOP-173 (k6 ở `tests/loadtest/`, `model/bench`) |
| `ai_eval` | Chấm output model trên tập có nhãn | `model/eval`, issue ML/AI |

**Requirement:** `@pytest.mark.req(...)` nhận các ID: `UC01`…`UC14`, `NOP-123`, mã kế hoạch (`S6-03`,
`VN-04`, `E-01`), bước kịch bản `SC27-01`…`SC27-27` và `SCVN-1`…`SCVN-8`. ID sai định dạng làm hỏng
collection (có chủ ý: test gõ nhầm ID sẽ lặng lẽ rơi khỏi báo cáo).

```python
@pytest.mark.integration
@pytest.mark.security
@pytest.mark.req("NOP-148", "UC07")
def test_unset_tenant_reads_no_review_cases(pg_app_user): ...
```

Vitest không có marker: đặt tag trong tên test, trong ngoặc vuông.

```ts
it("[NOP-248][UC07][contract] hiện VERSION_CONFLICT khi người khác đã duyệt trước", ...)
```

Đặt tên: file `test_<hành vi>.py`, test `test_<điều kiện>_<kết quả>` (`test_same_bytes_retry_creates_one_artifact`).
Tên nói hành vi nghiệp vụ, không nói hàm được gọi.

## 5. Assertion tất định và khi nào cần người

Phải tất định:

| Loại | Quy tắc |
| --- | --- |
| Số | So bằng `libs.indicator.numeric` với dung sai 06 mục 4, ghi dung sai ngay trong test. Không `pytest.approx` mặc định. Khác as-of/vintage là **trượt** dù số trong dung sai |
| Thời gian | Đồng hồ cố định (inject `now`), `as_of` và múi giờ ghi rõ; kiểm đủ bốn trục (event, publication, `available_at`, system). Không `sleep`; chờ bất đồng bộ bằng poll có hạn giờ |
| Quyền | Kiểm cả phía phủ định: người không quyền nhận 403/404, tenant khác nhận 0 dòng. Một test chỉ kiểm "người có quyền làm được" là chưa đủ |
| Trạng thái | Assert trên trạng thái lưu (hàng DB, object MinIO, sự kiện Kafka), không trên log. Bất biến "raw không đổi" kiểm bằng SHA-256 trước/sau |
| Idempotency | Chạy lại cùng đầu vào lần hai, so số bản ghi và nội dung với lần một |
| Đồng thời | OCC: hai người ghi cùng `expected_version`, đúng một thắng, người kia 409; lặp đủ lần để thứ tự không may mắn |
| Output LLM | Không assert văn bản của model. Test kiểm lớp tất định phía sau: đề xuất sai (`settle → close_price`) bị validator từ chối, JSON hỏng ra lỗi có mã |
| Thứ tự, ID | Không phụ thuộc thứ tự trả về khi API không hứa thứ tự; không assert UUID sinh ngẫu nhiên |

Được dùng người duyệt (ghi tên và ngày duyệt, như `reviewer`/`approved_at` của golden set):

- Tạo và duyệt nhãn: golden case, nhãn bằng chứng vàng cho Recall/MRR, lớp conflict kỳ vọng, nhãn trích
  xuất thông cáo. Nhãn đã duyệt rồi thì việc chấm là tất định.
- Chất lượng lời giải thích UC09/UC10 (ý trong `expected_points` có được diễn đạt đúng không) khi đối
  chiếu evidence không đủ: hai người chấm theo rubric, bất đồng thì lead quyết.
- Nghiệm thu UX của màn hình (đúng quy tắc "có màn hình mới xong"), ảnh chụp đính vào issue.

LLM làm giám khảo chỉ được dùng cho độ trôi chảy/mạch lạc của văn bản, luôn báo cáo kèm, **không bao giờ**
là điều kiện đạt duy nhất, và không bao giờ cho số, quyền, thời gian, phân loại.

Test chập chờn là bug. CI không tự chạy lại cho tới khi xanh. Tạm tắt chỉ bằng `skip` có lý do ghi
`NOP-…` và owner, tối đa tới mốc C kế tiếp; nếu test thuộc một issue Must thì issue đó hiện `failing`
trong báo cáo mục 7 và chặn mốc.

## 6. Công cụ theo level

| Việc | Công cụ | Lệnh |
| --- | --- | --- |
| Unit/integration Python | pytest trong image service | `make test-unit`, `make test-integration` |
| Unit/contract FE | vitest + tsc | `make frontend-test`, `make frontend-lint` |
| Contract | OpenAPI + JSON Schema envelope, kiểm tương thích trong CI | NOP-152, NOP-167 |
| E2E API/kịch bản | harness `scenario_27.py`, `scenario_vn.py` | NOP-66 |
| Browser E2E | Playwright (`tests/e2e/ui.e2e.mjs` mở rộng) | NOP-176 |
| Tải | k6 (`tests/loadtest/`), `make bench`, `make bench-e2e` | NOP-173 |
| Lỗi phụ thuộc | testcontainers dừng/khởi động lại container; stub model trả timeout/JSON hỏng | NOP-174 |
| AI | `model/eval` tier `commodity` | NOP-66, NOP-78 |

## 7. Báo cáo coverage theo requirement

```bash
# mỗi suite ghi một file JSON (các suite chạy ở image khác nhau)
make test-unit PYTEST_ARGS="--req-report-dir=.req-reports"
make test-integration PYTEST_ARGS="--req-report-dir=.req-reports"
cd frontend && npx vitest run --reporter=json --outputFile=../.req-reports/frontend.json
# gộp; --expect + --milestone trả exit 1 khi còn issue của mốc chưa đạt
python3 tests/req_coverage.py .req-reports/*.json \
  --expect docs/v2/data/test-traceability.csv --milestone C1
```

Mỗi issue Must (dòng của CSV, trừ Feature và PM) ở một trong bốn trạng thái: `uncovered` (không test đạt
nào gắn `req` của nó), `missing <level>` (có test đạt nhưng thiếu một level/kind ghi trong cột
`test_levels`, ví dụ BE chỉ có `unit` mà thiếu `contract`), `failing` (có test đỏ), `passing`. Story cần
test đạt ở bất kỳ level nào. Cổng mốc: mọi issue của mốc ở `passing`. Báo cáo được đính vào NOP-165 mỗi thứ Sáu, cùng lúc rà cột TT ở 05.

Harness không phải pytest (NOP-66, NOP-173, NOP-176) ghi kết quả của mình đúng định dạng file mà
`--req-report-dir` sinh ra (`{"source": "pytest", "tests": [{nodeid, level, kinds, reqs, outcome}]}`) để
gộp chung; đó là một phần "xong khi" của các issue đó.

## 8. Catalog fixture

Fixture nhỏ dạng văn bản nằm trong `tests/fixtures/v2/`. Artifact nhị phân (XLS, PDF, HTML đã render) nằm
trong bucket `eval-fixtures` của MinIO (NOP-99, NOP-241), gọi theo SHA-256 qua một manifest; test không
tải từ nguồn thật. Dữ liệu tham chiếu trong `docs/v2/data/` được dùng nguyên.

| ID | Nội dung | Nguồn | Owner |
| --- | --- | --- | --- |
| FX-EIA-RBRTE | Các release `RBRTEd.xls` của EIA, SHA-256 trong `data/brent-5d-drop-episodes.csv` | EIA thật | Quan Le |
| FX-EIA-0923 | Release EIA 23/09/2026 (`sha256 e6e8c9d5…`): Brent 130,80 → 114,89 (−12,16%) | EIA thật | Quan Le |
| FX-BRENTB-V1 / V2 | Nguồn thử "Brent feed B": v1 `date, price` (EIA 01–14/09); v2 `period, settle, chg, pct`, 15/09 = 107,46 (06 mục 6) | Đội tạo (F-01) | Quan Le |
| FX-XSRC-0826 | `data/cross-source-reference-2026-08.csv`, cột `expected_classification` | Research | Quan Le |
| FX-EPISODES | `data/brent-5d-drop-episodes.csv`: 32 đợt, 3 đợt gần nhất 22/09, 25/08, 28/07/2026 | Research | Quan Le |
| FX-SOURCES-YAML | `data/sources-oil-gold.yaml` (28 nguồn) + một nguồn licence bị chặn | Research | Quan Le |
| FX-MOIT-0924 | Thông cáo Bộ Công Thương kỳ 24/9/2026 (RON95 147,204 USD/thùng; E10RON95-III 27.087 đ/l, +1.451; QĐ 2398/QĐ-BCT) | Artifact đã lưu | Quan Le |
| FX-VCB-XML, FX-SJC-HTML, FX-LBMA-JSON, FX-IMF-PCPS | Artifact đã lưu của Vietcombank (`26,170.00`), SJC/PNJ đã render, LBMA PM (25/9 = 4.261,05), IMF PCPS | Artifact đã lưu | Quan Le |
| FX-DOCS | EIA WPSR, OPEC MOMR (mục "(continued)"), SPDR GLD 120 trang (bảng qua trang), tài liệu demo có trang, một PDF chứa prompt injection (NOP-175) | Artifact đã lưu + đội tạo | Đức Long |
| FX-DQ-VIOLATIONS | Vàng −200, Brent 7200, `reference_time > published_at` cho giá quan sát; spread Brent–WTI 18 USD (hợp lệ) | Đội tạo | Quan Le |
| FX-ASOF | Giá ngày 10/9 công bố 10/9, sửa 11/9, nạp 12/9; EIA as-of 21/9 → 15/9 | Đội tạo + EIA | Đức Long |
| FX-UNITS | Lượng/oz/thùng/lít; kỳ D/W/I/P; kỳ điều hành 7/3/2026 (05/3–06/3); ca `formatVN` | Đội tạo | Đức Long |
| FX-CATALOG, FX-CAPABILITY | Seed mặt hàng/alias/quan hệ (`SUCCEEDS`, `NOT_EQUIVALENT`); manifest capability | Đội tạo | Trần Hải Long |
| FX-ROLES | Hai tenant eval; vai trò DATA_STEWARD, REVIEWER, ANALYST; hai người maker/checker | NOP-241 | Quan Le |
| FX-KAFKA-REPLAY | Envelope trùng, sai thứ tự, sai schema | Đội tạo | Đức Long |
| FX-MODEL-STUB | Stub ModelGateway: đề xuất sai `settle → BRENT_SPOT`, JSON hỏng, timeout, câu trả lời ghi sẵn | NOP-159 | Linh Thien |
| FX-GOLDEN | `model/eval/cases/commodity.jsonl` (NOP-42) | Research | Quan Le |
| FX-CONTRACT | Ví dụ hợp lệ/không hợp lệ của Contract v1 (NOP-150, NOP-151) | Đội tạo | Trần Hải Long |

## 9. Traceability matrix

### 9.1 Cấp issue

[data/test-traceability.csv](data/test-traceability.csv): **219 issue** thuộc mốc C1 (88), C2 (56), C3
(75), lấy từ Linear ngày 30/09/2026. Mỗi dòng: `issue, milestone, feature, parent, layer, uc, title,
impl_owner, test_levels, qa_gate, qa_owner, fixtures, expected`.

- `test_levels` của leaf = cổng merge (mục 3). Mặc định theo lớp: BE `unit, contract, integration`;
  Data `unit, integration`; FE `unit, contract`; AI `ai_eval, unit`; DevOps `integration`; INT
  `integration, e2e`; cộng `security` / `resilience` / `performance` / `contract` cho issue chạm phần đó.
- Story và Feature: `story gate` / `feature gate` = các suite ở `qa_gate`. PM/BA: `review`.
- `fixtures` và `expected` ghi ở cấp story, leaf kế thừa; dữ liệu cụ thể lấy từ 06 và 08.
- Kiểm 30/09: 219/219 dòng có test level và suite QA; 218/219 có người làm (**NOP-263 chưa gán**).
- Backlog đổi (thêm/xoá issue, đổi mốc) thì sửa CSV cùng lúc.

Tier B và ngoài POC (UC11 Forecast NOP-179, UC14 Skill NOP-186, NOP-16, NOP-34, NOP-41, NOP-45, NOP-71,
NOP-73) không nằm trong matrix; suite QA của chúng (NOP-185, NOP-191) vào khi chúng vào phạm vi.

### 9.2 Cấp use case

| UC | Feature → story | Suite QA | Fixture | Kết quả kỳ vọng (điều kiện xong ở 08) | Golden / kịch bản | Owner suite |
| --- | --- | --- | --- | --- | --- | --- |
| UC01 Nguồn có cấu trúc | NOP-50: 21, 26, 28, 31, 160 · NOP-51: 15, 39 · NOP-52: 43, 46, 161 · NOP-53: 14, 47, 68 · NOP-64: 48, 215 | NOP-168, 169, 167 | FX-EIA-RBRTE, FX-SOURCES-YAML, FX-VCB-XML, FX-SJC-HTML, FX-LBMA-JSON, FX-IMF-PCPS | Thêm IMF PCPS qua UI không sửa mã; chạy lại không sinh bản ghi; Data Layers thấy artifact; Series Explorer thấy series + release | SC27-01…03 | Quan Le |
| UC02 Nguồn tài liệu | NOP-54: 60, 61, 239 · NOP-26: 220, 221 | NOP-170 | FX-DOCS | Trích dẫn mở đúng trang; "(continued)" gộp; bảng qua trang nối; số OCR dưới ngưỡng → ReviewCase | SCVN-1…2 | Đức Long |
| UC03 Reverse schema | NOP-52: 37, 40, 46, 161 · NOP-21 | NOP-168 | FX-BRENTB-V1/V2, FX-MODEL-STUB | Đổi tên cột → chặn → đề xuất mới → duyệt → nạp tiếp; 0 dòng lọt khi chờ; `settle → close_price` bị từ chối | 4 case UC03/UC05; SC27-04…06 | Quan Le |
| UC04 Trích xuất ngữ nghĩa | NOP-54: 63, 177, 18, 13 | NOP-170, 171 | FX-MOIT-0924, FX-DOCS | Kỳ 24/9/2026 đúng giá, cửa sổ, quỹ, quyết định, mỗi số có quote; ≥ 5 sự kiện đã duyệt mỗi cửa sổ | SC27-21, SCVN-3 | Đức Long |
| UC05 Chất lượng dữ liệu | NOP-55: 20, 218, 219 · NOP-14 | NOP-171 | FX-DQ-VIOLATIONS, FX-BRENTB-V2 | Mỗi tầng ≥ 1 luật trên dữ liệu thật + 1 fixture vi phạm; Brent-B −17,8% bị cờ; spread 18 USD không bị cờ | SC27-07, SCVN-4 | Quan Le |
| UC06 Xung đột nguồn | NOP-55: 22, 218, 219 | NOP-171 | FX-XSRC-0826 | 100% dòng đúng lớp; chỉ SOURCE_CONFLICT/UNKNOWN mở ReviewCase | 7 case UC06; SC27-08…09 | Quan Le |
| UC07 Người duyệt | NOP-49: 25, 29, 32, 155 · NOP-64: 67 | NOP-171, 169 | FX-BRENTB-V2, FX-ROLES | Bước 10–16 qua UI; maker ≠ checker; OCC 409; sửa mapping không sửa raw | SC27-10…16, SCVN-5 | Quan Le |
| UC08 Phân tích có cấu trúc | NOP-56: 35, 38 · NOP-57: 17, 33, 44, 156, 157 · NOP-53: 69, 70 | NOP-172, 176 | FX-EIA-RBRTE, FX-EPISODES, FX-GOLDEN | `value_correct`, `as_of_correct` ≥ 0,95 trên case fact/trend/so sánh; LLM không sinh SQL tự do trên observation | 12 case UC08; SC27-19; SCVN-7 | Đức Long |
| UC09 Điều tra tri thức | NOP-54: 178, 60 · NOP-57: 44, 65 | NOP-172, 175 | FX-DOCS, FX-GOLDEN | Trích claim đã duyệt hoặc đoạn có trang; thiếu → `INSUFFICIENT_EVIDENCE`; Recall@10/MRR@10 so baseline | 6 case UC09; SC27-20 | Đức Long |
| UC10 Điều tra hybrid | NOP-56: 35 · NOP-57: 27, 65, 157 · NOP-54: 13, 178 | NOP-172, 176 | FX-EPISODES, FX-DOCS | Bước 25–27: bảng + chart + bằng chứng + lineage cho 3 đợt, đúng số CSV; chỉ bằng chứng trong cửa sổ | 5 case UC10; SC27-22, 25…27; SCVN-8 | Đức Long |
| UC11 Forecast | Ngoài POC (NOP-179) | NOP-185 (Tier B) | — | — | — | — |
| UC12 Cảnh báo chủ động | NOP-58: 19, 23, 158 | **Chưa có suite riêng** (mục 11); tạm NOP-172, 174, 176 | FX-EIA-0923, FX-MOIT-0924 | Replay EIA 23/09: đúng 1 alert kèm investigation, 0 alert trùng WTI/spread; kỳ 24/9: 1 alert giá xăng | 4 case UC12; SC27-17…18, 23…24; SCVN-6 | Đức Long (đề xuất) |
| UC13 As-of | NOP-64: 59 (108, 110, 153, 164) · NOP-46: 209 | NOP-167, 168 | FX-ASOF | As-of world 21/9 → Brent 15/9; bản sửa 11/9 không thấy ở as-of 10/9; câu trả lời cũ tái lập từ `release_id` | 7 case UC13 | Quan Le |
| UC14 Skill | Ngoài POC (NOP-186) | NOP-191 (Tier B) | — | — | — | — |
| Xuyên suốt | NOP-64: 12, 24, 36, 163, 212…217, 240, 241 · NOP-72: 74…78, 159 | NOP-167, 169, 173, 174, 175 | FX-CONTRACT, FX-KAFKA-REPLAY, FX-ROLES, FX-MODEL-STUB | Theo từng dòng CSV | — | theo suite |

## 10. Owner suite QA và báo lỗi

Đội không có QA riêng (05 mục 1: 0,5 người), nên suite QA do dev đảm nhận theo quy tắc **không tự
kiểm phần mình làm chính**; phần trùng thì người còn lại review test. Đề xuất, chờ lead chốt:

| Suite | Owner đề xuất | Lý do |
| --- | --- | --- |
| NOP-167 Contract | Trần Hải Long | Chủ Contract v1 (NOP-150/152); contract test kiểm hai phía nên chấp nhận |
| NOP-168 Pipeline C1 | Quan Le | Chủ fixture F-01; pipeline do Đức Long làm (trùng NOP-132 thì Đức Long review) |
| NOP-169 Isolation C1 | Trần Hải Long | RLS/role do Đức Long làm |
| NOP-170 Tài liệu C2 | Đức Long | Cấu trúc tài liệu do Quan Le làm (trùng NOP-195…197 thì Quan Le review) |
| NOP-171 Governance C2 | Quan Le | Governance do Đức Long và Trần Hải Long làm |
| NOP-172 Agent C3 | Đức Long (+ Linh Thien cho metric `ai_eval`) | Agent BE do Quan Le làm |
| NOP-173 Tải | Quan Le | Chủ NOP-217, NOP-213 |
| NOP-174 Lỗi phụ thuộc | Đức Long | Chủ outbox/Kafka; trùng NOP-98 thì Quan Le review |
| NOP-175 Bảo mật | Trần Hải Long | |
| NOP-176 Browser E2E | Trần Hải Long | Chủ nghiệm thu UI |

### 10.1 Mẫu báo lỗi (issue Linear)

```markdown
Tiêu đề: [Bug][UCxx] <hành vi sai, một câu>   ví dụ: [Bug][UC07] Checker duyệt được case do chính mình sửa

Requirement: UCxx · NOP-<issue bị vi phạm> · SC27-nn / SCVN-n (nếu có)
Mức độ: S1 | S2 | S3 | S4
Phát hiện bởi: <test nodeid hoặc suite QA> · Môi trường: eval tenant <id>, namespace <ns>

Tái hiện
1. Fixture: FX-... (sha256 ...)
2. Lệnh/bước: <lệnh pytest, curl, hoặc thao tác UI>
Kỳ vọng: <kèm con số/trạng thái chính xác>
Thực tế: <kèm con số/trạng thái chính xác>

Evidence bundle: <đường dẫn file đính kèm .evidence/NOP-xxx-yyyymmdd-hhmm.zip>
Test tái hiện: <nodeid của test đỏ, thêm vào suite trước khi sửa>
```

| Mức độ | Định nghĩa | Ảnh hưởng |
| --- | --- | --- |
| S1 | Sai số liệu trả cho người dùng, lộ dữ liệu chéo tenant, raw bị sửa/xoá, vi phạm một trong mười hai bất biến | Chặn mốc; sửa trước mọi việc khác |
| S2 | Luồng Must hỏng, không có đường vòng | Chặn cổng story/feature |
| S3 | Luồng Must hỏng nhưng có đường vòng | Sửa trong mốc |
| S4 | Hiển thị, chữ, bố cục | Theo lịch |

### 10.2 Evidence bundle

Thư mục `.evidence/<NOP-id>-<yyyymmdd-hhmm>/` (không commit, nén rồi đính vào issue):

| File | Nội dung |
| --- | --- |
| `manifest.json` | git SHA backend/frontend, tag image, phiên bản cấu hình model/prompt, fixture ID + SHA-256, tenant, `as_of`, đồng hồ inject, lệnh đã chạy |
| `requests/` | Request/response HTTP liên quan, **đã xoá token và secret** |
| `frames.jsonl` | Frame SSE của lượt hỏi/hộp thư |
| `events.jsonl` | Envelope Kafka liên quan (topic, key, offset) |
| `state/` | Kết quả truy vấn các hàng DB/ClickHouse và object MinIO liên quan (trước/sau) |
| `trace.txt` | Trace ID và đoạn log liên quan |
| `ui/` | Ảnh chụp hoặc video Playwright (lỗi UI) |
| `test-output.txt` | Output của test đỏ |

## 11. Khoảng trống cần chốt (30/09/2026)

1. **UC12 không có suite QA riêng.** NOP-172 chỉ phủ UC08–UC10; NOP-173/174/176 kiểm tải, lỗi, UI.
   Kiểm "replay EIA 23/09 → đúng 1 alert, dedup WTI/spread, cooldown, update nhỏ không gọi LLM" cần
   một chủ: mở rộng NOP-172 thành UC08–UC10 + UC12, hoặc thêm một issue QA C3 riêng.
2. **NOP-263 chưa có người làm** (C1, Urgent).
3. **Owner suite QA NOP-167…176 chưa gán** trên Linear; bảng mục 10 là đề xuất.
4. **Kiểm độc lập UC13 as-of** hiện dựa vào NOP-164 (INT, do chính người làm store) và contract NOP-167;
   NOP-168 nên thêm case as-of world/system vào phạm vi.
