# Data Agent V2: POC dầu/vàng

> **Từ 06/10/2026 định hướng sản phẩm nằm ở [`docs/v3/`](../v3/README.md)** (sản phẩm doanh nghiệp, không
> còn là POC). Thư mục này vẫn có hiệu lực cho Observation Engine (chuỗi chỉ số, as-of, DQ, HITL số liệu),
> là Data Space tham chiếu đầu tiên của V3. **Nghiệm thu 27/10 đã dời** (06/10/2026); lịch, mốc C1–C3
> và cột TT trong 05 không còn là kế hoạch đang chạy; project Linear P-NOP-2 đã đóng, việc mới ở P-NOP-3.
>
> Thư mục này là kế hoạch và thiết kế đang có hiệu lực cho V2. Thiết kế v1 (Nợ công/GDP) đã xoá khỏi
> cây (còn trong lịch sử git); phần còn dùng được nằm ở 11, 12, 13. Sơ đồ PlantUML và ERD sinh tự
> động đã xoá 06/10/2026 (lỗi thời, còn gọi `flow_builder`); ORM là nguồn sự thật của schema.
> Cập nhật lần cuối: 28/09/2026.

## Bắt đầu từ đâu

Đây là tài liệu nền của Observation Engine, không còn là kế hoạch thực thi độc lập. Với product,
agent-core, frontend và backlog hiện hành, đọc [docs/v3/README.md](../v3/README.md) trước; backlog
đang chạy là [docs/v3/04-backlog.md](../v3/04-backlog.md).

`00-architecture-v2.md`, `12-numeric-core.md`, `14-input-data-db.md` và dữ liệu trong `data/` vẫn là
nguồn tham chiếu cho Observation Engine. Các mốc C1–C3, trạng thái `TT` và kế hoạch POC trong các
file cũ bên dưới chỉ giữ để truy nguyên lịch sử.

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
| [05-implementation-plan.md](05-implementation-plan.md) | Kế hoạch POC lịch sử; không dùng để lấy việc mới |
| [06-evaluation-plan.md](06-evaluation-plan.md) | Golden set, dung sai, hiện trạng `model/eval`, metric, cách dựng kịch bản 27 bước |
| [07-decisions-and-risks.md](07-decisions-and-risks.md) | Câu hỏi còn chờ, ADR-V2-09…42, đầu vào nghiên cứu (LLM-Wiki, WFM, WikiSkill, Graph Engineering), rủi ro |
| [08-use-cases.md](08-use-cases.md) | Đặc tả UC01–UC14 và bảng ánh xạ 27 bước → UC → ID |
| [09-frontend.md](09-frontend.md) | Nghiên cứu frontend V2 lịch sử; giao diện hiện hành ở [docs/v3/05-ui-v3.md](../v3/05-ui-v3.md) |
| [10-database-design.md](10-database-design.md) | Thiết kế database V2: hiện trạng các kho (đo 27/09), vấn đề theo bất biến, quy ước bắt buộc ADR-V2-26…35, bố cục schema và role, dọn dữ liệu cũ |
| [11-ops.md](11-ops.md) | Vận hành: hiện trạng, GitOps, hạ tầng, backup/DR (P0-07), Kafka (P0-03), egress (P0-08), alert, MLOps, kế hoạch bỏ script |
| [12-numeric-core.md](12-numeric-core.md) | Lõi số: NC-E1..E3, NC-T1..T18, rủi ro NC-R, NC-ADR-T |
| [13-ui-principles.md](13-ui-principles.md) | Nguyên tắc P1–P8, quy ước số/ngày formatVN, lỗi có cấu trúc, deep link |
| [14-input-data-db.md](14-input-data-db.md) | Thiết kế DB cho luồng đầu vào: cấu hình nguồn V2, artifact/fetch/release, profile/mapping, candidate, `available_at` cho số lịch sử, outbox, quyền, thứ tự migration, phần cần sửa trong task Linear |
| [15-test-strategy.md](15-test-strategy.md) | Kiểm thử V2: ba cổng merge/story/mốc, DoD theo vai trò, level/kind/`req` tag, assertion tất định, coverage theo requirement, catalog fixture, traceability UC → issue, owner suite QA, mẫu báo lỗi + evidence bundle |
| [data/sources-oil-gold.yaml](data/sources-oil-gold.yaml) | 28 nguồn: 24 nhập qua Add Data → Bulk Import, 4 tải tay qua Document Hub |
| [data/brent-5d-drop-episodes.csv](data/brent-5d-drop-episodes.csv) | 32 đợt Brent giảm ≥5%/5 phiên (2023–2026), có SHA-256 của release EIA gốc |
| [data/cross-source-reference-2026-08.csv](data/cross-source-reference-2026-08.csv) | Đối chiếu đa nguồn có lớp conflict kỳ vọng |
| [data/test-traceability.csv](data/test-traceability.csv) | Traceability lịch sử của POC C1/C2/C3; không phải backlog hiện hành |

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
- **Đội, hạn, phạm vi (lịch sử 27/09):** POC dầu/vàng và các mốc nghiệm thu đã được dời khi chuyển
  sang V3; Data Space tham chiếu, dữ liệu và ADR-V2-25 vẫn được giữ cho Observation Engine.
- **Câu hỏi còn chờ:** không còn.

## Trạng thái kế hoạch

Kế hoạch POC và các mốc C1–C3 đã kết thúc về mặt định hướng từ 06/10/2026. Không dùng các mốc,
ngày nghiệm thu hoặc cột `TT` trong tài liệu này để điều phối công việc; dùng backlog V3.

## Việc tuần này (lịch sử)

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
