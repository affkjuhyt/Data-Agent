# Data Agent — Strategy reset & implementation blueprint (12/11/2026)

> Bộ tài liệu này là kết quả của lượt đánh giá lại toàn diện theo master prompt "Data Agent strategy reset" ngày
> 12/11/2026, trên HEAD `9e503407`. Nó **không thay** `docs/v3/` (vẫn là thiết kế sản phẩm đang hiệu lực); nó là
> *bản chỉnh hướng có bằng chứng* và *gói bàn giao cho coding agent*. Chỗ nào trái với `docs/v3/04-backlog.md` thì
> blueprint thắng cho tới khi backlog đó được hợp nhất ([IMPLEMENTATION_BACKLOG.md](IMPLEMENTATION_BACKLOG.md) §0).

## Đọc theo Part của master prompt

| Part | Nội dung | Tệp |
| --- | --- | --- |
| 1 | Executive assessment | [00-ASSESSMENT.md](00-ASSESSMENT.md) §Part 1 |
| 2 | As-is architecture & root cause (theo domain, mức, bằng chứng `file:dòng`) | [00-ASSESSMENT.md](00-ASSESSMENT.md) §Part 2; chi tiết `evidence/audit-*.md` |
| 3 | REA open source research (adopt / wrap / extract / reference / reject; PoC) | [OPEN_SOURCE_RESEARCH.md](OPEN_SOURCE_RESEARCH.md); chi tiết `evidence/oss-*.md` |
| 4 | Product strategy & MVP (P0/P1/P2/Remove-Defer; mức tự chủ; cổng P0) | [STRATEGY.md](STRATEGY.md) |
| 5 | Target technical architecture (3 phương án, chọn B+, diagrams, ADR-50…59, **sổ tên §9**) | [ARCHITECTURE.md](ARCHITECTURE.md) |
| 6 | Engineering specifications | [DATA_PIPELINE_SPEC.md](DATA_PIPELINE_SPEC.md), [ASK_ENGINE_SPEC.md](ASK_ENGINE_SPEC.md), [ANALYTICS_SPEC.md](ANALYTICS_SPEC.md), [DOCUMENT_INTELLIGENCE_SPEC.md](DOCUMENT_INTELLIGENCE_SPEC.md), [API_AND_SCHEMA_CONTRACTS.md](API_AND_SCHEMA_CONTRACTS.md) |
| 7 | Real data & evaluation (NYC 311, SEC EDGAR, golden ≥ 150, scorecard, gates) | [DATASET_AND_EVALUATION.md](DATASET_AND_EVALUATION.md); chi tiết `evidence/datasets.md` |
| 8 | Implementation roadmap (WBS theo tuần, task theo vai, phụ thuộc) | [IMPLEMENTATION_BACKLOG.md](IMPLEMENTATION_BACKLOG.md) |
| 9 | Coding agent handoff | [CODING_AGENT_INSTRUCTIONS.md](CODING_AGENT_INSTRUCTIONS.md) |
| 10 | Architecture decision & critical review | [00-ASSESSMENT.md](00-ASSESSMENT.md) §Part 10 |

## Bằng chứng

`evidence/` giữ nguyên văn các báo cáo audit mã (5), nghiên cứu OSS (3), nghiên cứu dataset (1) và số đo baseline chạy
ngày 12/11/2026 trên cụm dev local (`baseline.md`). Mọi nhận định trong các tệp chính trỏ về đây. Hai lưu ý trung thực:
(1) baseline truy hồi chạy **không có TEI** (nửa dense/rerank không reachable) nên là số keyword-only; (2) đáp án bộ
AdventureWorks chưa qua giải mù hai người.

## Quy ước ID

- Vấn đề hiện trạng: `D-n` (Data), `A-n` (Ask), `N-n` (Analytics), `U-n` (Document), `P-n` (Platform) — 00-ASSESSMENT §2.2.
- Tính năng: `F-nn` — STRATEGY §3.
- Task: `DF-nn` (data), `AE-nn` (ask), `AL-nn` (analytics), `DI-nn` (document), `EV-nn` (eval/dataset), `PL-nn`
  (platform) — ở cuối mỗi spec và gộp trong IMPLEMENTATION_BACKLOG.
- ADR mới: `ADR-50…59` — ARCHITECTURE §10. ID cũ của `docs/v3` (`R0-n`, `RES-n`, `AGC-n`, `AN-n`, `RG-n`, `AUD-n`) vẫn
  được nhắc khi task đóng chúng.

## Trạng thái

Chưa có dòng code ứng dụng nào được viết theo blueprint này (đúng yêu cầu của master prompt: dừng ở thiết kế). Việc
đầu tiên của coding agent là Phase 0 của backlog: PoC-1/2/3/5 và dựng DS-311.
