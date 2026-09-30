# Data Agent V2: POC dầu/vàng

> Thư mục này là kế hoạch và thiết kế đang có hiệu lực cho V2. Thiết kế v1 (Nợ công/GDP) đã xoá khỏi
> cây (còn trong lịch sử git); phần còn dùng được nằm ở 11, 12, 13 và `diagrams/`.
> Cập nhật lần cuối: 28/09/2026.

## Bắt đầu từ đâu (người mới và phiên Claude mới)

1. Đọc [00-architecture-v2.md](00-architecture-v2.md), nhất là **mục 0: mười hai bất biến**. Đây là
   nguồn chuẩn; file nào mâu thuẫn với 00 thì sửa file đó.
2. Mở [05-implementation-plan.md](05-implementation-plan.md), tìm ID đầu tiên có `TT = todo` trên
   đường găng (mục 5) mà các ID phụ thuộc đã `done`. Không nhảy STEP: không làm planner/agent khi
   effective data chưa có.
3. Đọc đặc tả của use case liên quan trong [08-use-cases.md](08-use-cases.md) và phần thiết kế tương
   ứng trong [03-target-design.md](03-target-design.md).
4. Dùng dữ liệu thật ở [04-sources-oil-gold.md](04-sources-oil-gold.md) và `data/` để viết test.
5. Làm xong: test + màn hình kiểm chứng + sửa cột `TT` thành `done <commit>` trong cùng commit. Khi
   một câu hỏi ở [07](07-decisions-and-risks.md) được chốt: xoá nó khỏi bảng, ghi kết quả thành ADR
   (kỹ thuật) hoặc vào 05 (người, hạn, phạm vi).

Ba quy tắc của nhóm không nằm trong kiến trúc nhưng bắt buộc (đã chốt 16/09/2026):

- **Có màn hình mới xong**, cho cả người dùng cuối lẫn người nhập liệu; dữ liệu phải thấy ở Data Layers.
- **Cấu hình, không mã theo miền**: không file mã nào chứa tên nguồn hay tên hàng hoá.
- **Đi hết các bước**: artifact → release → candidate → DQ → (review) → effective; không ghi thẳng bảng đích.

Quy tắc repo (lint/type/test trong container, so baseline, không commit secret) ở `CLAUDE.md` gốc.

## Các file

| File | Nội dung |
| --- | --- |
| [00-architecture-v2.md](00-architecture-v2.md) | **Nguồn chuẩn.** Bất biến, các lớp, hai pipeline, object model, effective data, query path, capability, luồng chủ động, 14 UC, stack, 10 vấn đề, STEP 1–13, kịch bản 27 bước |
| [03-target-design.md](03-target-design.md) | Hiện thực 00 trên repo: bảng, topic, service, màn hình |
| [04-sources-oil-gold.md](04-sources-oil-gold.md) | Nghiên cứu dữ liệu dầu/vàng: benchmark, nguồn đã kiểm tra, trục thời gian, bối cảnh 2026, bẫy dữ liệu, licence |
| [05-implementation-plan.md](05-implementation-plan.md) | Kế hoạch POC 1 tháng: đội, lịch, backlog Tier A/B có ID và trạng thái, thứ tự cắt, những gì không làm, năng lực, phương pháp forecast sau POC |
| [06-evaluation-plan.md](06-evaluation-plan.md) | Golden set, dung sai, hiện trạng `model/eval`, metric, cách dựng kịch bản 27 bước |
| [07-decisions-and-risks.md](07-decisions-and-risks.md) | Câu hỏi còn chờ, ADR-V2-09…42, đầu vào nghiên cứu (LLM-Wiki, WFM, WikiSkill, Graph Engineering), rủi ro |
| [08-use-cases.md](08-use-cases.md) | Đặc tả UC01–UC14 và bảng ánh xạ 27 bước → UC → ID |
| [09-frontend.md](09-frontend.md) | Frontend V2: hiện trạng, nghiên cứu (AG-UI, A2UI, MCP Apps, WaitGPT, trust và duyệt), kiến trúc, catalog khối, đặc tả màn, kế hoạch 2 frontend |
| [10-database-design.md](10-database-design.md) | Thiết kế database V2: hiện trạng các kho (đo 27/09), vấn đề theo bất biến, quy ước bắt buộc ADR-V2-26…35, bố cục schema và role, dọn dữ liệu cũ |
| [11-ops.md](11-ops.md) | Vận hành: hiện trạng, GitOps, hạ tầng, backup/DR (P0-07), Kafka (P0-03), egress (P0-08), alert, MLOps, kế hoạch bỏ script |
| [12-numeric-core.md](12-numeric-core.md) | Lõi số: NC-E1..E3, NC-T1..T18, rủi ro NC-R, NC-ADR-T |
| [13-ui-principles.md](13-ui-principles.md) | Nguyên tắc P1–P8, quy ước số/ngày formatVN, lỗi có cấu trúc, deep link |
| [14-input-data-db.md](14-input-data-db.md) | Thiết kế DB cho luồng đầu vào: cấu hình nguồn V2, artifact/fetch/release, profile/mapping, candidate, `available_at` cho số lịch sử, outbox, quyền, thứ tự migration, phần cần sửa trong task Linear |
| [15-test-strategy.md](15-test-strategy.md) | Kiểm thử V2: ba cổng merge/story/mốc, DoD theo vai trò, level/kind/`req` tag, assertion tất định, coverage theo requirement, catalog fixture, traceability UC → issue, owner suite QA, mẫu báo lỗi + evidence bundle |
| [diagrams/](diagrams/README.md) | 39 sơ đồ PlantUML: kiến trúc 00–18, giao diện 20–26, vận hành 30–42 |
| [data/sources-oil-gold.yaml](data/sources-oil-gold.yaml) | 28 nguồn: 24 nhập qua Add Data → Bulk Import, 4 tải tay qua Document Hub |
| [data/brent-5d-drop-episodes.csv](data/brent-5d-drop-episodes.csv) | 32 đợt Brent giảm ≥5%/5 phiên (2023–2026), có SHA-256 của release EIA gốc |
| [data/cross-source-reference-2026-08.csv](data/cross-source-reference-2026-08.csv) | Đối chiếu đa nguồn có lớp conflict kỳ vọng |
| [data/test-traceability.csv](data/test-traceability.csv) | Traceability 219 issue C1/C2/C3: UC, test level bắt buộc, suite QA, fixture, kết quả kỳ vọng, owner (15 mục 9) |

## Tình trạng tóm tắt

- **Đã có và dùng lại:** Source Registry + crawl theo lịch (35 nguồn, 18 có cron, đo 27/09), pipeline KB
  Bronze→Silver→Gold qua Kafka, OCR, LightRAG (vector + FTS + Neo4j + rerank), LangGraph với
  checkpointer Postgres, sandbox SQL an toàn, `model/eval` tier online, ClickHouse đang chạy.
- **Đã lấy từ `feat/add-task` (P0-01, commit 3c3ad53):** lõi số `libs/indicator`, page/bbox trong
  citation, verifier tất định trong judge, sửa E2. Không lấy `ankt_connectors/` và migration `ankt.*`
  (05 mục 6); bộ đọc as-of cũ `observation.py` đã xoá, S1-02 viết mới trên ClickHouse.
- **Chưa có:** entity registry, release và bốn trục thời gian, profile/reverse schema/drift, DQ/conflict
  cho số, ReviewCase/correction/effective, operator + QuerySpec, capability registry + MCP, kiểm đủ bằng
  chứng, luồng chủ động, forecast, nguồn Việt Nam có cấu trúc.
- **Dữ liệu:** không có nguồn futures dầu và vàng thế giới theo ngày vừa miễn phí vừa sạch licence;
  EIA công bố giá spot theo tuần; 2026 là năm biến động mạnh (Brent 61–138 USD), rất hợp để kiểm
  conflict, alert và as-of.
- **Đội, hạn, phạm vi (chốt 27/09):** tuyển thêm 2 senior; POC trong 1 tháng, nghiệm thu thứ Ba
  27/10/2026; giá trong nước (xăng dầu, vàng, tỷ giá) **trong** POC để trình diễn cho người dùng
  Việt Nam; thêm 1 frontend từ 05/10; giá vàng thế giới theo ngày crawl từ website LBMA, ghi trong
  báo cáo (ADR-V2-25). Kế hoạch vẫn thiếu ~2,9 tuần-người, bù bằng thứ tự cắt. Forecast, skill, tin
  tức ngoài POC.
- **Câu hỏi còn chờ:** không còn.

## Mốc

| Mốc | Ngày | Kịch bản 27 bước | Kịch bản Việt Nam (06 mục 7) |
| --- | --- | --- | --- |
| C1 | Thứ Sáu 09/10/2026 | Bước 1–6 | Tỷ giá, vàng SJC/PNJ tự nạp |
| C2 | Thứ Sáu 16/10/2026 | Bước 7–16 | Thông cáo Bộ Công Thương → duyệt → effective |
| C3 | Thứ Năm 22/10/2026 | Bước 17–24 | Alert giá xăng, chênh vàng |
| Tổng duyệt | Thứ Hai 26/10/2026 | 1–27 | 1–8 |
| **Nghiệm thu** | **Thứ Ba 27/10/2026** | 1–27 + bảng điểm | 1–8 |

## Việc tuần này (28/09–02/10)

1. P0-09: hai senior chốt hợp đồng dữ liệu trong 3 ngày đầu.
2. S1-01, S1-02: schema Postgres và ClickHouse `obs`; S2-02: artifact bất biến.
3. P0-02 (E3), P0-03 (Kafka), P0-04 (kỳ và đơn vị).
4. P0-05: 30 case hạt giống, đo baseline. FE-00: nền frontend (kiểu sinh từ OpenAPI, URL state, stream V2).

## Lịch sử thay đổi

| Ngày | Thay đổi |
| --- | --- |
| 27/09/2026 | Bản đầu: đối chiếu PDF thiết kế v2.0 với repo, kế hoạch 8 tuần |
| 27/09/2026 | Chốt [00-architecture-v2.md](00-architecture-v2.md) làm kiến trúc chính thức; viết lại 03, 05, 06, 07 theo STEP 1–13 và kịch bản 27 bước; thêm 08; nghiên cứu sâu dữ liệu dầu/vàng (04, `data/`); cập nhật bằng chứng retrieval sau d116062 |
| 27/09/2026 | Chốt đội (+2 senior) và hạn 1 tháng: 05 viết lại thành kế hoạch 4 tuần, nghiệm thu 27/10; forecast, skill, giá trong nước ra ngoài POC; xoá các câu đã chốt khỏi 07 (D1, D5, D8, D9, D11) |
| 27/09/2026 | Chốt D2 (giá trong nước trong POC), D4, D6, D7, D10, D12, D13; lấy lõi số từ `feat/add-task` sang nhánh hiện tại; thêm kịch bản Việt Nam 8 bước (06 mục 7); nghiên cứu phương án giá vàng ngày (04 mục 7) |
| 27/09/2026 | Chốt D3 (crawl JSON LBMA, dự phòng tải tay, phụ lục nguồn trong báo cáo nghiệm thu) và D14 (+1 frontend); thêm 09-frontend.md; 05 phân công FE1/FE2, thêm FE-00, FE-01, D3-01 |
| 28/09/2026 | Xoá 01-gap-analysis và 02-design-review (đã lỗi thời sau P0-01): bằng chứng eval còn đúng chuyển vào 06 mục 5, danh sách "thừa" vào 05 mục 6, phương pháp forecast vào 05 mục 8.1, phần nghiên cứu paper vào phụ lục 07 mục 2; nguồn Vietcombank chuyển vào `sources-oil-gold.yaml`, xoá `sources-public-debt.yaml`; kế hoạch bỏ script vào phụ lục 11. Cập nhật theo việc gỡ release management, compose, `observation.py` và case Nợ công |
