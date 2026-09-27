# 06. Kế hoạch đánh giá

> Tiêu chí nghiệm thu POC là **kịch bản 27 bước** và **kịch bản Việt Nam 8 bước** (mục 7) ([00-architecture-v2.md](00-architecture-v2.md)
> mục 15) chạy tự động và lặp lại được, cộng bảng điểm golden set, **nghiệm thu thứ Ba 27/10/2026**.
> Golden set bắt đầu từ tuần 1 để có baseline của hệ thống hiện tại trước khi xây. File case: `model/eval/cases/commodity.jsonl`.
> Scorer dùng chung `libs/indicator/numeric.py` với verifier ở runtime.

## 1. Schema một case

```json
{
  "case_id": "UC08-BRENT-0001",
  "use_case": "UC08",
  "question": "Giá dầu Brent spot ngày 15/09/2026 là bao nhiêu?",
  "as_of": "2026-09-27T00:00:00+07:00",
  "as_of_mode": "world",
  "expected_capabilities": ["FETCH_SERIES"],
  "answerable": true,
  "expected": {"value": "130.80", "unit": "usd_per_barrel", "decimal_precision": 2,
               "series_key": "EIA:RBRTE:D", "event_time": "2026-09-15"},
  "tolerance": {"type": "published_precision"},
  "expected_evidence": [{"kind": "observation", "series_key": "EIA:RBRTE:D",
                         "event_time": "2026-09-15", "release_published": "2026-09-16"}],
  "expected_refusal_code": null,
  "traps": [],
  "reviewer": "", "approved_at": ""
}
```

`as_of_mode`: `world` (theo `available_at`) hoặc `system` (theo `system_time`), UC13. Case điều tra
có `expected_points` (ý bắt buộc, chấm bằng đối chiếu evidence) và `expected_conflict_class`.
`release_published` 16/09 là ngày công bố dự kiến theo lịch thứ Tư của EIA; research xác nhận lại
khi duyệt case.

## 2. Phân bổ theo use case

| UC | 02/10 (30) | C2 16/10 (45) | Nghiệm thu 27/10 (60) | Trọng tâm chấm |
| --- | --- | --- | --- | --- |
| UC08 structured analytics | 10 | 10 | 12 | value, as-of, đơn vị |
| UC13 as-of | 5 | 6 | 7 | không dùng thông tin công bố sau as-of |
| UC06 conflict | 3 | 6 | 7 | đúng lớp phân loại |
| UC09 knowledge | 3 | 4 | 6 | evidence đúng trang, không bịa |
| UC10 hybrid | 0 | 2 | 5 | đúng đợt, bằng chứng trong cửa sổ, lineage |
| UC12 alert (replay) | 0 | 2 | 4 | số alert, dedup, cooldown |
| UC03/UC05 (qua fixture) | 1 | 4 | 4 | chặn đúng, không lọt dòng |
| Abstain | 4 | 5 | 7 | mã từ chối đúng (gồm câu hỏi dự báo: không trả số) |
| Việt Nam: xăng dầu, vàng, tỷ giá | 4 | 8 | 12 | kỳ điều hành, phía giá, phía tỷ giá, không nối chuỗi E10 |

Forecast (UC11) và regression Nợ công/GDP ngoài POC 1 tháng (05 mục 6). Case chỉ
được dùng khi research đã duyệt (`reviewer`, `approved_at`).

## 3. Case hạt giống từ số liệu thật (04, `data/`)

| Case | Câu hỏi | Kỳ vọng | Bẫy |
| --- | --- | --- | --- |
| Brent spot 15/9 | "Brent ngày 15/9/2026?" | 130,80 (EIA spot) | Nếu trả ~107 phải nói là futures |
| Freshness | "Giá Brent hôm nay?" (hỏi 27/9) | Giá mới nhất 114,89 ngày 22/9, nói rõ EIA công bố theo tuần | Không bịa giá 25–27/9 |
| As-of | as_of 21/9: "Brent mới nhất?" | Số của ngày có release ≤ 21/9 | UC13 |
| Ba nguồn | "Brent tháng 8/2026?" | Nêu 91,08 / 90,9 / 88,00 và vì sao khác | T1, DEFINITION_DIFFERENCE |
| WTI hai nguồn | "WTI tháng 8/2026 theo IMF và EIA?" | 83,74 và 83,90, không phải mâu thuẫn | NO_CONFLICT |
| Futures dừng | "Giá WTI futures tháng gần nhất hôm nay?" | Từ chối `STALE_SOURCE`: EIA dừng từ 05/04/2024 | T3 |
| Đợt giảm | "3 lần gần nhất Brent giảm >5% trong 5 phiên?" | 22/09/2026 (−12,16%), 25/08/2026 (−7,40%), 28/07–07/08/2026 (−12,73%) | `data/brent-5d-drop-episodes.csv` |
| Đếm đợt | "Bao nhiêu lần trong 3 năm?" | 32 theo quy tắc gộp ≤7 ngày; nêu quy tắc | Tham số operator |
| Spread | "Spread Brent–WTI ngày 22/9?" | 18,48 | T12 (bất thường nhưng đúng) |
| Chênh vàng | "Vàng SJC cao hơn thế giới bao nhiêu (26/9)?" | Theo LBMA PM 25/9 (4.261,05): 9,96 triệu (tỷ giá bán Vietcombank) hoặc 12,06 triệu (tỷ giá mua); nêu phiên LBMA và phía tỷ giá. Báo chí đưa 9–10 triệu vì dùng giá spot (~4.298) | T8 |
| E10 | "Giá RON95-III kỳ 24/9/2026?" | Từ 16/6/2026 là E10RON95-III 27.087 đồng/lít; không nối chuỗi | T5 |
| Kỳ ngoài lịch | "Kỳ điều hành 7/3/2026 bình quân từ ngày nào?" | 05/3–06/3/2026 | T6 |
| Giá xăng kỳ 24/9 | "Kỳ điều hành 24/9/2026 xăng E10RON95-III bao nhiêu, tăng bao nhiêu?" | 27.087 đồng/lít, +1.451; hiệu lực 15h00 24/9; Quyết định 2398/QĐ-BCT | Giá trần, không phải giá bình quân |
| Giá thế giới bình quân | "Giá RON95 thế giới bình quân kỳ 24/9/2026?" | 147,204 USD/thùng, cửa sổ 17/9–24/9 | Locale `147,204` là thập phân |
| Quỹ bình ổn | "Kỳ 24/9 có chi quỹ bình ổn không?" | Trích lập và chi đều 0 | |
| Locale | Tỷ giá Vietcombank bán USD 27/9 | 26.170 (nguồn ghi `26,170.00`) | T7 |
| Forecast từ LLM | "Brent tuần sau bao nhiêu?" (khi chưa có forecast job) | Mở job hoặc từ chối; không trả số | Bất biến 6 |

## 4. Dung sai

| Loại | Dung sai |
| --- | --- |
| Tra giá công bố | Khớp tuyệt đối sau làm tròn tới `decimal_precision` |
| Return, spread từ giá công bố | Nửa đơn vị chữ số cuối + sai số lan truyền; trần 0,01 điểm % cho return |
| Volatility năm hoá | Tương đối 1% |
| Quy đổi tiền tệ/đơn vị | Nửa đơn vị hiển thị; tỷ giá phải đúng thời điểm và đúng phía |
| Khác as-of/vintage | **Không đạt** dù số trong dung sai |
| Khác định nghĩa | Không dùng dung sai; chấm lớp phân loại và lời giải thích |

## 5. Metric và ngưỡng

Mục tiêu cột phải là cho ngày nghiệm thu 27/10.

Tier `commodity` trong `model/eval/thresholds.json`, `provisional` tới khi có hai lần chạy ổn định.

| Lớp | Metric | Mục tiêu |
| --- | --- | --- |
| Ingestion | Nguồn tự nạp đúng lịch 7 ngày liên tục | ≥ 8 |
| Ingestion | Chạy lại không sinh bản ghi (idempotency) | 100% |
| Ingestion | Drift fixture bị chặn, 0 dòng lọt | 100% |
| Ingestion | Update-to-query latency (release → effective) cho nguồn auto | p95 ≤ 30 phút |
| Governance | ReviewCase / 1.000 observation mỗi tuần | báo cáo; cảnh báo nếu > 5 |
| Governance | Phân loại conflict đúng trên `data/cross-source-reference-2026-08.csv` | 100% |
| Governance | OCC: ghi đồng thời trả 409 | 100% |
| Truth | Effective observation có provenance đủ (artifact, release, vị trí, 4 trục thời gian) | 100% |
| Agent | `value_correct`, `as_of_correct` | ≥ 0,95 |
| Agent | `unsupported_number_rate` (so với evidence trong trace, không so `case["contexts"]`) | 0 |
| Agent | `refusal_correct` | ≥ 0,9 |
| Agent | Planner chỉ dùng capability đã đăng ký | 100% |
| Retrieval | Recall@10, MRR@10 trên case có nhãn evidence | báo cáo so baseline tuần 1 |
| Proactive | Replay release EIA 23/09/2026: số alert | đúng 1, có investigation |
| Runtime | Chữ đầu tiên câu UC08 | p95 ≤ 2 giây |
| Runtime | UC10 hoàn tất | p95 ≤ 30 giây |
| Runtime | Resume sau kill worker | Tier B: không chạy lại node đã xong |

## 6. Dựng kịch bản 27 bước bằng dữ liệu thật + fixture

Không có nguồn futures Brent miễn phí sạch licence, và không thể hẹn trước một lần đổi schema thật
(04 mục 7). Bước 1–7 dùng **nguồn fixture** do đội kiểm soát; từ bước 8 dùng dữ liệu thật.

| Thành phần | Thiết kế |
| --- | --- |
| Nguồn fixture "Brent feed B" | File CSV trên MinIO bucket `eval-fixtures`, đăng ký như một `http_file` bình thường; `purpose: evaluation`, nhãn "dữ liệu thử" trên mọi màn |
| Phiên bản 1 | Cột `date, price` với giá EIA spot thật 01–14/09/2026 → nạp bình thường |
| Phiên bản 2 (bước 4) | Đổi thành `period, settle, chg, pct`; giá trị 15/09 = 107,46 (mức futures được báo công khai ngày đó) |
| Kỳ vọng bước 5–9 | Drift bị chặn; LLM có thể đề xuất `settle → BRENT_SPOT`; DQ cross-source thấy lệch −17,8% so EIA 130,80; conflict `UNKNOWN` |
| Bước 12–15 | Reviewer chọn `DEFINITION_DIFFERENCE`; mapping mới `settle → BRENT_FUTURES_M1.settlement_price`; checker khác người duyệt |
| Bước 17 | Replay release EIA 23/09/2026 (`sha256 e6e8c9d5…` trong `data/brent-5d-drop-episodes.csv`) |
| Bước 25–27 | Ba đợt gần nhất lấy từ CSV; bằng chứng tài liệu phải có `available_at` trong cửa sổ ±3 ngày |

Harness `model/eval/scenario_27.py` chạy tuần tự, kiểm trạng thái sau mỗi bước qua API, ghi báo cáo
bước đạt/không đạt. Chạy lại được từ đầu (reset tenant eval).

## 7. Kịch bản Việt Nam (8 bước, dữ liệu thật)

Trình diễn cho người dùng Việt Nam (D2). Chạy bằng harness `model/eval/scenario_vn.py`, replay các
artifact đã lưu để lặp lại được.

| Bước | Diễn ra | Dữ liệu thật | UC | ID |
| --- | --- | --- | --- | --- |
| 1 | Bộ Công Thương đăng thông cáo kỳ điều hành (thứ Năm 15h00) | Bài 24/9/2026 | UC02 | S2-03 |
| 2 | Crawler theo link phát hiện bài mới, lưu raw bất biến | | UC02 | S2-02, S2-03 |
| 3 | Extractor trích giá thế giới bình quân theo sản phẩm + cửa sổ, giá bán, quỹ bình ổn, thời điểm hiệu lực, số quyết định, kèm quote | RON95 147,204 USD/thùng; E10RON95-III 27.087 đồng/lít | UC04 | S4-02 |
| 4 | DQ: locale Việt Nam; E10RON95-III là series mới `SUCCEEDS` RON95-III, không nối chuỗi | T5, T7 | UC05, UC03 | S6-01, S1-03 |
| 5 | Số trích từ văn bản luôn qua ReviewCase; research duyệt, checker duyệt → effective | | UC07 | S6-03, S7-01 |
| 6 | Detector: giá bán thay đổi vượt ngưỡng → alert "giá xăng E10RON95-III tăng 1.451 đồng/lít (+5,7%)" kèm giá thế giới bình quân kỳ và Brent cùng cửa sổ | 25.636 → 27.087 | UC12 | S11-01 |
| 7 | Hỏi "Vàng SJC hôm nay cao hơn thế giới bao nhiêu?" → chênh theo hai phía tỷ giá, nêu giả định, nguồn, as-of | 26/9: 9,96 / 12,06 triệu/lượng theo LBMA PM 25/9 (`data/cross-source-reference-2026-08.csv`) | UC08, UC06 | VN-02, VN-03, VN-04 |
| 8 | Hỏi "Giá xăng kỳ này vì sao tăng?" → bảng + evidence (thông cáo, trang/đoạn) + lineage | Kỳ 24/9/2026 | UC10 | S9-01, E-01, VN-05 |

Chênh vàng theo ngày ở bước 7 dùng LBMA Gold Price PM (crawl JSON website LBMA, ADR-V2-25) của phiên
London gần nhất đã công bố, tỷ giá Vietcombank và giá SJC cùng ngày; câu trả lời ghi rõ phiên LBMA,
phía tỷ giá, phía giá SJC và nhãn nguồn LBMA.

## 8. Kiểm thử "mở rộng bằng cấu hình"

Tier B, tuần 4 nếu kịp (không thì ngay sau nghiệm thu): thêm gạo Việt Nam 5% (cột "Rice, Viet Namese 5%" trong Pink Sheet) chỉ bằng cấu hình
(entity, series trong profile Pink Sheet, 5 case). Đạt khi 5 case trả đúng và `git diff` không có
file `.py`, `.ts`, `.tsx` nào đổi.

## 9. Báo cáo nghiệm thu

Nộp cùng buổi nghiệm thu 27/10, sinh từ harness và `model/eval`, gồm:

1. Kết quả từng bước của hai kịch bản (đạt/không đạt, thời gian, ảnh màn hình).
2. Bảng điểm golden set so với baseline tuần 1 (mục 5).
3. **Phụ lục "Nguồn dữ liệu và điều kiện sử dụng"**, sinh tự động từ Source Registry: mỗi nguồn ghi
   cơ quan công bố, URL, cách lấy (API, tải file, crawl web, headless, tải tay), `licence`,
   `use_rights`, lần lấy gần nhất, và giới hạn. Bắt buộc nêu rõ: giá vàng LBMA lấy bằng crawl website
   LBMA, chưa có licence IBA, chỉ dùng phân tích nội bộ (ADR-V2-25); nguồn fixture của bước 1–7 là
   dữ liệu thử (D12); các nguồn `pending_review`.
4. Danh sách việc Tier B và ngoài POC còn lại (05 mục 6).

