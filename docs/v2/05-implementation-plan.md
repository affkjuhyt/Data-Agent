# 05. Kế hoạch triển khai: POC trong 1 tháng

> Theo thứ tự STEP 1–13 của [00-architecture-v2.md](00-architecture-v2.md).
> **Hạn: nghiệm thu thứ Ba 27/10/2026** (đúng 1 tháng từ 27/09/2026), tổng duyệt thứ Hai 26/10.
> **Tiêu chí:** (1) kịch bản 27 bước (00 mục 15) chạy trọn trên dữ liệu thật + nguồn fixture (06 mục 6);
> (2) **kịch bản Việt Nam 8 bước** (06 mục 7) trên dữ liệu thật: giá xăng dầu kỳ điều hành, vàng SJC,
> chênh lệch trong nước – thế giới.
>
> **Cột "TT" là nơi ghi tiến độ**: `todo` → `doing` → `done <commit>`; hạng mục bị cắt ghi `cut`.
> Ai làm xong một ID thì sửa cột này trong cùng commit.

## 1. Đội, phạm vi, giả định

| Vai trò | Người | Làm gì |
| --- | --- | --- |
| BE1, BE2 | 2 backend hiện có | Phần chạm mã cũ: `web_crawler`, pipeline KB, `workflow_runtime`, `model/eval` |
| SR1, SR2 | 2 senior tuyển mới | Module mới: `indicator-api`, ClickHouse, governance, capability, detector |
| DE | 1 data engineer | Artifact, release, profile, recompute, operator, nguồn Việt Nam |
| FE1, FE2 | 1 frontend hiện có + 1 frontend thêm từ 05/10 (D14) | Chi tiết và phân công: [09-frontend.md](09-frontend.md) mục 7 |
| RS1, RS2 | 2 research | Golden set, fixture, entity/series/quan hệ (qua UI), tuyển chọn event/claim, duyệt |
| QA, Ops | 0,5 + 0,5 | |

Phạm vi đã chốt ngày 27/09/2026:

- Tuyển thêm 2 senior; POC trong 1 tháng.
- **Giá trong nước làm trong POC** (D2): thông cáo điều hành giá xăng dầu của Bộ Công Thương, vàng
  SJC/PNJ/DOJI, tỷ giá Vietcombank, chênh vàng trong nước – thế giới.
- Nguồn fixture cho bước 1–7 được chấp nhận (D12). Chưa làm tin tức (D10).
- Giá vàng thế giới theo ngày: crawl JSON website LBMA, dự phòng tải tay, ghi trong báo cáo (D3,
  ADR-V2-25). Thêm 1 frontend từ 05/10 (D14).
- Ngoài POC: forecast (UC11), skill (UC14). Nợ công/GDP không làm tiếp: 6 case regression Nợ công đã
  xoá khỏi `model/eval` ngày 28/09 (R-01).

Giả định (sai thì sửa mục 7): hai senior vào không muộn hơn thứ Hai 05/10, tuần đầu 50% năng suất;
toàn đội làm toàn thời gian cho POC trong 4 tuần + 2 ngày.

## 2. Nguyên tắc để nén vào 1 tháng

1. **Hợp đồng trước (P0-09).** Ba ngày đầu chốt Pydantic model và OpenAPI của mọi object trong 00
   (Entity, Series, Observation với 4 trục thời gian, Claim, Event, Evidence, ReviewCase, Correction,
   Override, Capability) và envelope Kafka; các nhánh việc chạy song song trên stub.
2. **Chỉ làm cái hai kịch bản cần.** Mọi thứ khác là Tier B hoặc ngoài POC (mục 6).
3. **Một extractor, hai việc.** `semantic_extractor` được xây cho thông cáo Bộ Công Thương (văn bản →
   observation candidate + event điều chỉnh giá). Event dầu (EIA, OPEC) quanh 3 đợt giảm do research
   tuyển chọn và nhập qua UI; cả hai vẫn đi ReviewCase/HITL.
4. **Merge `main` hằng ngày**, demo chạy từ `main`.
5. Quy tắc cũ giữ nguyên: có màn hình mới xong; cấu hình, không mã theo miền; đi hết các bước; mỗi ID
   một commit có test, so baseline (CLAUDE.md mục 2).

## 3. Lịch và mốc

```text
          W1 28/09–02/10   W2 05/10–09/10   W3 12/10–16/10   W4 19/10–23/10   26–27/10
Nền       P0, hợp đồng
STEP 1    ██████████████
STEP 2    ███████████████████████████
STEP 3                     ██████████████
Việt Nam                   ███████████████████████████████████████████
STEP 4–5                                    ███████████████████████████
STEP 6–7                   ░░░░░░░░░░░░░░░░ ██████████████
STEP 8                                      ██████████████
STEP 9–11                                                    ██████████████
Eval      ████████████████████████████████████████████████████████████████████████████
Mốc                        C1 09/10         C2 16/10         C3 22/10   Tổng duyệt 26/10
                                                                        NGHIỆM THU 27/10
```

| Mốc | Ngày | Kịch bản 27 bước | Kịch bản Việt Nam |
| --- | --- | --- | --- |
| C1 | Thứ Sáu 09/10 | Bước 1–6 | Tỷ giá, vàng SJC/PNJ tự nạp; thấy ở Series Explorer |
| C2 | Thứ Sáu 16/10 | Bước 7–16 | Thông cáo Bộ Công Thương → candidate → duyệt → effective (bước 1–5) |
| C3 | Thứ Năm 22/10 | Bước 17–24 | Alert điều chỉnh giá xăng, chênh vàng (bước 6–7) |
| Tổng duyệt | Thứ Hai 26/10 | 1–27 liền mạch | 1–8 |
| **Nghiệm thu** | **Thứ Ba 27/10** | 1–27 + bảng điểm | 1–8 |

Trễ một mốc C quá 2 ngày → áp dụng ngay thứ tự cắt ở mục 5.

## 4. Backlog Tier A

TN = tuần-người còn lại (đã trừ phần lấy từ `feat/add-task` ngày 27/09).

### W1 (28/09–02/10): nền và hợp đồng

| ID | Việc | Cách làm | Ai | TN | Xong khi | TT |
| --- | --- | --- | --- | --- | --- | --- |
| P0-01 | Lấy lõi số từ `feat/add-task` | Đã lấy vào working tree ngày 27/09: `libs/indicator`, 8 file test, `PageRegion`, ghi page/bbox ở Silver/Gold, page trong citation, verifier trong judge, sửa E2. **Không** lấy `ankt_connectors/`, migration `ankt.*`, `SourceCenter`/`SourceWizard`, test tích hợp `ankt` (viết lại ở S1-01) | BE1 | 0,1 | 173 test unit + 33 test Docling đạt trong container; ruff/mypy không có lỗi mới so với HEAD | done 3c3ad53 |
| P0-02 | Sửa E2, E3 | E2 đã có trong P0-01 (`grounding.py` dùng `libs.indicator.numeric`). Còn E3: `formatVN.ts` theo quy ước Việt Nam | FE1 | 0,5 | Hiển thị `3.109,2`, `dd/MM/yyyy` | doing (E2 xong) |
| P0-03 | Kafka | Idempotence ở `platform_workers/common/kafka.py`; khai báo topic 03 mục 13 + ACL; tắt auto-create | Ops | 0,5 | Topic lạ bị từ chối | todo |
| P0-04 | Kỳ và đơn vị | `Period` D/W/I/P; `money_per(barrel, troy_oz, luong, litre, kg, tonne)` | BE1 | 0,5 | Test quy đổi lượng/oz | todo |
| P0-05 | Golden set hạt giống + baseline | 30 case từ 06 mục 3; chạy hệ thống hiện tại làm baseline | RS, QA | | Được duyệt | todo |
| P0-06 | Chốt câu còn chờ | D2–D14 đã chốt 27/09; câu mới phát sinh ghi vào 07 mục 1 | PM | | Xoá khỏi 07 | done (27/09) |
| P0-09 | Hợp đồng dữ liệu | Pydantic + OpenAPI skeleton `indicator-api` + envelope Kafka | SR1, SR2 | 1 | Các nhánh W2 build được trên stub | todo |
| S1-01 | Schema Postgres | `registry`, `ingest`, `governance`, `knowledge`; RLS; `version` cho OCC | BE2 | 1 | Migration lên/xuống sạch | todo |
| S1-02 | ClickHouse `obs` | Bảng 03 mục 3.2, row policy; bộ đọc as-of viết mới trong `indicator_api` (`domains/observation` + `adapters/clickhouse`, ADR-V2-36); bản cũ `libs/indicator/observation.py` đọc schema `ankt.*` không tồn tại, đã xoá 28/09 | SR2, Ops | 1 + 0,5 | Test hai version chưa gộp | todo |
| FE-00 | Nền frontend | Sinh kiểu từ OpenAPI (script: SR1), TanStack Query, `urlState`, `streamReducer` + frame V2, SSE hộp thư (09 mục 4) | FE1, SR1 | 0,75 + 0,25 | Màn V2 đầu tiên dùng kiểu sinh ra | todo |
| S2-02 | Artifact bất biến | MinIO theo SHA-256; `_purge_promoted_traces` không đụng artifact/release | DE | 1 | Re-crawl: artifact cũ còn | todo |

### W2 (05/10–09/10): nguồn, pipeline structured, nguồn Việt Nam → C1

| ID | Việc | Cách làm | Ai | TN | Xong khi | TT |
| --- | --- | --- | --- | --- | --- | --- |
| S1-03 | Entity registry | Entity, alias, `SAME_UNDERLYING`, `SUCCEEDS` (RON95-III → E10RON95-III), `NOT_EQUIVALENT` | RS, FE2 | 0,5 FE | Màn Entity & Series | todo |
| S2-01 | Trường nguồn | licence, use_rights, entity, release_info, locale, rate_limit, follow_link_pattern + Bulk Import + form | BE2, FE1 | 0,5 + 0,5 | Nhập `sources-oil-gold.yaml` thấy licence | todo |
| S2-03 | Release + freshness | `ingest.release`; `publication_time` theo `release_info`; `follow_link_pattern` (cần cho Bộ Công Thương); `source.sla.breached` | DE | 0,5 | EIA futures báo vi phạm SLA | todo |
| S3-01/03 | Profile + drift | `series_extractor`: kiểu, locale, đơn vị, fingerprint; drift → chặn + ReviewCase | DE | 0,75 | Fixture đổi cột, 0 dòng lọt | todo |
| S3-02 | Reverse schema | `MappingProposal`; LLM đề xuất; validator + `NOT_EQUIVALENT` | SR1 | 1 | `settle → close_price` bị từ chối | todo |
| S3-04 | Normalize + candidate | Locale, đơn vị, múi giờ, mã thiếu; idempotency; chỉ ghi thay đổi | SR2 | 1 | EIA chạy lại 0–1 dòng | todo |
| S3-05 | Màn Nguồn + Series Explorer (gộp) | Profile, mapping, drift, chuỗi, as-of, provenance (09 mục 5) | FE2 | 1 (W2–W3) | Steward duyệt mapping | todo |
| VN-02 | Vàng trong nước | `web_crawl_headless` cho SJC/PNJ/DOJI; profile `html_table` trên HTML đã render; phía mua/bán; giá chốt cuối ngày | SR2 | 1 | Chuỗi SJC mua/bán tự nạp | todo |
| VN-03 | Tỷ giá Vietcombank | Profile XML, locale en-US (`25,760.00`), rate limit 5 phút | DE | 0,25 | Chuỗi USD mua/bán | todo |
| F-01 | Nguồn fixture | Hai phiên bản CSV theo 06 mục 6 | RS | | Đăng ký qua UI | todo |

### W3 (12/10–16/10): governance, effective, văn bản → C2

| ID | Việc | Cách làm | Ai | TN | Xong khi | TT |
| --- | --- | --- | --- | --- | --- | --- |
| S6-01 | DQ (schema, numeric, cross-source) | `libs/dq`, luật trong `governance.dq_rule` | DE | 0,5 | Fixture lệch −17,8% bị cờ | todo |
| S6-02 | Conflict classifier | 03 mục 8.2; LLM chỉ cho `UNKNOWN` | BE2 | 1 | `cross-source-reference-2026-08.csv` đúng | todo |
| S6-03 | ReviewCase + HITL | Authority (ADR-V2-23), hộp thư SSE, maker ≠ checker, OCC 409; Review Inbox hai bước: đánh giá Đúng/Sai/Không chắc rồi mới sửa (09 mục 3.3, 5) | SR1, FE1 | 1,5 + 1,75 | Bước 10–14 qua UI | todo |
| S7-01 | Correction/override → effective | Append-only; ClickHouse; `observation.published` | SR2 | 1 | Bước 13–15 | todo |
| S7-02 | Tính lại dẫn xuất | basis spot–futures, return, chênh vàng (VN-04) | DE | 0,5 | Bước 16 | todo |
| S8-01 | Operator | `fetch_series`, `compare_period`, `returns`, `detect_drop_episodes`, `event_window`; QuerySpec Pydantic; SQLGlot | DE | 1 (W3–W4) | Khớp `brent-5d-drop-episodes.csv` | todo |
| S4-01 | Page/bbox tới UI | Backend đã có từ P0-01; còn: re-ingest tài liệu dùng trong demo, link `#page=N` trên citation | BE1, FE1 | 0,25 + 0,25 | Trích dẫn mở đúng trang | todo |
| S4-02 | `semantic_extractor` cho thông cáo Bộ Công Thương (VN-01) | Theo link bài mới; trích giá thế giới bình quân theo sản phẩm + cửa sổ, giá bán, quỹ bình ổn, thời điểm hiệu lực, số quyết định → observation candidate + event "điều chỉnh giá", có quote; luôn qua ReviewCase | BE1 | 2 (W2–W3) | Kỳ 24/9/2026 ra đúng số 06 mục 3 | todo |
| VN-04 | Series và chênh vàng | Series sản phẩm xăng dầu (E10RON95-III `SUCCEEDS` RON95-III), `VN_GOLD_GAP_{BUY,SELL}_FX` theo ngày từ LBMA PM (D3-01) | RS (cấu hình qua UI), DE | 0,25 DE | Chênh ngày tính được, ghi rõ phiên LBMA và phía tỷ giá | todo |
| EV-01 | Event dầu quanh 3 đợt giảm | Research nhập event/claim từ EIA WPSR, thông cáo OPEC có quote + trang | RS | | ≥ 5 event/cửa sổ đã duyệt | todo |

### W4 (19/10–23/10) + 26–27/10: agent, chủ động, trình diễn → C3

| ID | Việc | Cách làm | Ai | TN | Xong khi | TT |
| --- | --- | --- | --- | --- | --- | --- |
| S5-01 | Quan hệ Event ↔ Entity ↔ Evidence | `knowledge.relation` + khung đồ thị UI | BE2 | 0,5 | Bước 21 | todo |
| S9-01 | Capability registry | YAML → `registry.capability`; composite `EXPLAIN_PRICE_MOVEMENT`, `COMPARE_EPISODES`, `EXPLAIN_FUEL_ADJUSTMENT`, `GOLD_GAP`; gọi in-process | SR1 | 1 | Runtime chỉ gọi capability đăng ký | todo |
| S9-02 | Resolution + task graph | Alias → entity/time/intent; planner chọn một trong các composite và điền tham số (không tự lập graph mới trong POC) | SR1 | 0,5 | Bước 25–26; câu hỏi VN 7–8 | todo |
| S10-01 | Verifier + sufficiency | Verifier đã nối vào judge (P0-01); còn: freshness, evidence sufficiency, replan ≤ 2 | BE2 | 0,75 | `INSUFFICIENT_EVIDENCE` đúng chỗ | todo |
| S11-01 | Luồng chủ động | `detector`: luật → MAD → significance → capability → dedup/group/cooldown → `alert.raised`; cùng cơ chế cho giá xăng (thay đổi giá bán) | BE1 | 1,5 | Replay EIA 23/09: 1 alert; kỳ 24/9: 1 alert giá xăng | todo |
| S11-02 | Alert inbox + hỏi tiếp | Alert đã gom nhóm, TaskGraphView của investigation | FE2 | 0,5 | Bước 24–25 | todo |
| E-01 | Khối trả lời | Catalog khối (09 mục 4.4): ValueCard, VerifierBadge, RefusalCard, nhãn "Capability đã duyệt"; chart dùng `ChartRenderer` có sẵn | FE1 | 0,5 | Bước 27 | todo |
| FE-01 | TaskGraphView + LineagePanel | xyflow; vẽ task graph khi stream, mở nút xem input/output (WaitGPT, 09 mục 3.2) | FE2 | 0,75 | Bước 18–22, 26 | todo |
| VN-05 | Màn "Việt Nam" | Thẻ giá xăng kỳ này (thế giới bình quân, giá bán, thay đổi, quyết định), lịch sử kỳ điều hành, vàng SJC mua/bán, LBMA PM, chênh lệch; dựng từ ValueCard/SeriesChart (09 mục 5) | FE2 | 1 | Kịch bản Việt Nam bước 7–8 trên UI | todo |
| S10-02 | Eval + harness | 60 case; `model/eval/scenario_27.py` và `scenario_vn.py` | BE2, QA, RS | 0,5 + 1 | Bảng điểm so baseline | todo |
| H-01 | Tổng duyệt + nghiệm thu | Video dự phòng, runbook demo, **báo cáo nghiệm thu có phụ lục "Nguồn dữ liệu và điều kiện sử dụng"** (06 mục 9) | Tất cả | | 26–27/10 | todo |

**D3-01** (DE, 0,25, W2): nguồn `lbma-gold-pm-json` (profile JSON `d`, `v[0..2]`), entity `GOLD_LBMA_PM`,
nguồn dự phòng tải tay; luật DQ so trung bình tháng với IMF `PGOLD` (lệch > 1% → ReviewCase).

## 5. Thứ tự cắt khi trễ

Đã áp dụng trước: S3-05 gộp một màn; S9-02 chỉ chọn composite; E-01 dùng chart có sẵn; event dầu do
research nhập.

1. Kịch bản Việt Nam bỏ alert giá xăng (bước 6), giữ trả lời.
2. VN-02: chỉ SJC qua PNJ (một nguồn), bỏ DOJI.
3. S6-01: chỉ tầng cross-source.
4. S5-01: bảng quan hệ, không vẽ đồ thị.
5. Nếu vẫn trễ: nghiệm thu bước 1–24 và kịch bản Việt Nam ngày 27/10, bước 25–27 muộn hơn một tuần
   (cần anh Linh đồng ý trước).

## 6. Tier B và ngoài POC

| ID | Việc | Tier | TT |
| --- | --- | --- | --- |
| S9-01b | MCP Gateway chuẩn (Streamable HTTP) | B | todo |
| S2-03b | Theo link Pink Sheet, release IMF/Pink Sheet | B | todo |
| S4-01b | Heading "(continued)", bảng qua trang, hình làm evidence, khung bbox | B | todo |
| S5-01b | Bản chiếu Neo4j | B | todo |
| P0-07, P0-08 | Backup Postgres ngoài node, egress allowlist | B (vận hành) | todo |
| VN-06 | Truyền dẫn Brent → giá xăng trong nước (INFERRED) | B | todo |
| S12-01, S12-02 | Forecast (UC11) | Ngoài POC | cut |
| S13-01 | Skill (UC14) | Ngoài POC | cut |
| NEWS-01 | Tin tức | Ngoài POC (D10) | cut |
| R-01 | Regression Nợ công/GDP | 6 case grounding Nợ công đã xoá khỏi `model/eval` (28/09, 22 → 16 case) | cut |

Không làm, vì thừa hoặc đi ngược quyết định đã chốt (rà repo và bản PDF v2.0 ngày 27/09):

1. **Source registry thứ hai.** `ankt.source` trên nhánh `feat/add-task` trùng `KBSource`/`KBSourceRun`
   đang chạy và có UI; giữ `KBSource`. Cùng lý do bỏ `services/ankt_connectors/` (mỗi nhà công bố một
   hàm fetch, ~1.100 dòng): đi ngược "connector theo giao thức, cấu hình được từ UI" (16/09).
2. **YAML làm registry** (V2-001 "sources-v2.yaml + DB migration"): registry nằm trong DB để màn hình
   liệt kê, sửa, lập lịch được; YAML chỉ là file seed nhập qua Bulk Import (`data/sources-oil-gold.yaml`).
3. **Deployable đặt tên theo miền** (`commodity-api`, `commodity-connectors`,
   `commodity-forecast-worker`, Bảng 17.2): dầu/vàng là cấu hình trên service chung (ADR-V2-11, ADR-V2-22).
4. **Hai lộ trình song song** (POC Nợ công/GDP hạn 23/10 và kế hoạch 8 tuần dầu/vàng trên cùng một đội):
   đã gộp thành một lộ trình dầu/vàng (mục 1).
5. **Dagster, MLflow đầy đủ, Neo4j lineage đầy đủ, mcp-gateway deployable riêng** trong POC: dữ liệu
   dầu/vàng dưới 200 nghìn dòng (Brent ngày từ 1987 ≈ 9 nghìn dòng); K8s CronJob đủ, MCP Gateway chạy
   trong `indicator-api`. ClickHouse cho effective observation thì giữ (00, ADR-V2-16).

## 7. Năng lực so với khối lượng Tier A

| Người | Có | Giao | Chênh |
| --- | --- | --- | --- |
| BE1 | 4,4 | P0-01, P0-04, S4-01, S4-02/VN-01, S11-01 = 4,35 | 0 |
| BE2 | 4,4 | S1-01, S2-01, S6-02, S5-01, S10-01, S10-02 = 4,25 | +0,15 |
| SR1 | 3,9 | P0-09, FE-00 (script), S3-02, S6-03, S9-01, S9-02 = 4,75 | −0,85 |
| SR2 | 3,9 | P0-09, S1-02, S3-04, S7-01, VN-02 = 4,5 | −0,6 |
| DE | 4,4 | S2-02, S2-03, S3-01/03, S6-01, S7-02, S8-01, VN-03, VN-04, D3-01 = 5,0 | −0,6 |
| FE1 | 4,4 | P0-02, FE-00, S2-01, S6-03, S4-01, E-01 = 4,25 | +0,15 |
| FE2 (từ 05/10) | 2,9 | S1-03, S3-05, FE-01, S11-02, VN-05 = 3,75 | −0,85, cắt theo 09 mục 7 |
| Ops | 2,2 | P0-03, S1-02 = 1,0 | dư, nhận Tier B vận hành |
| RS1, RS2 | 8,8 | golden set, fixture, entity/series, event dầu, duyệt | vừa |

Sau khi thêm 1 frontend (D14): thiếu khoảng **2,9 tuần-người** rải trên SR1 (0,85), SR2 (0,6), DE
(0,6), FE2 (0,85). Bù bằng: thứ tự cắt ở mục 5 (mục 1–2 khoảng 0,75), ba mục cắt nhỏ của frontend
(09 mục 7, 0,75), và RS nhận cấu hình VN-04 qua UI (0,25). Phần còn lại (~1,2) là rủi ro chấp nhận;
kế hoạch **không có đệm**. Mỗi thứ Sáu rà cột TT, trễ thì cắt ngay trong tuần.

## 8. Sau POC

Forecast (UC11), skill (UC14), tin tức, MCP chuẩn nếu chưa làm, Neo4j projection, Dagster, MLflow,
licence LBMA (D3 phương án A) và futures, tách `indicator-api` theo Bảng 5.1 thiết kế v1.

### 8.1 Phương pháp forecast (S12-01/02)

Forecast đi **sau** Truth plane, không làm song song: feature snapshot phải dựng bằng truy vấn
point-in-time theo `available_at ≤ as_of` (ADR-V2-14), không theo kỳ dữ liệu. Ví dụ forecast lập thứ
Hai không được dùng số tồn kho EIA của tuần kết thúc thứ Sáu trước đó, vì số này công bố thứ Tư. Không
có observation store có vintage thì backtest đẹp hơn thực tế.

| Hạng mục | Chọn |
| --- | --- |
| Baseline bắt buộc | Random walk (giá trị cuối), drift, ETS; với vàng thêm random walk trên log |
| Candidate | LightGBM trên log-return với lag, volatility, feature vĩ mô, cờ sự kiện |
| Backtest | Rolling origin, cửa sổ mở rộng, ≥ 100 điểm gốc cho mỗi horizon |
| Khoảng dự báo | Conformal theo phần dư backtest (không giả định phân phối) |
| Tiêu chí validated | MASE < 1 so với random walk ở ít nhất 2/3 horizon, và độ phủ khoảng 80% nằm trong 75–85% |
| Thư viện | `statsmodels`, `lightgbm`, `scikit-learn` trong image `forecast-worker` riêng, không thêm vào whitelist sandbox |

Kỳ vọng phải nói trước với hội đồng: giá dầu và vàng ở tầm 1–30 ngày gần với bước ngẫu nhiên, dự báo
"không đổi" rất khó bị vượt (Alquist, Kilian, Vigfusson, "Forecasting the Price of Oil", 2013). Một
pipeline từ chối model yếu có bằng chứng (candidate `rejected`) là kết quả đạt, không phải thất bại.
