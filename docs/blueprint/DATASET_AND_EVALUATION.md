# DATASET_AND_EVALUATION — Dữ liệu thật, bộ vàng, scorecard và cổng phát hành (Part 7)

> Ngày 12/11/2026. Mọi sự kiện về nguồn dữ liệu lấy từ [evidence/datasets.md](evidence/datasets.md) (fetch ngày
> 12/11/2026, có ghi URL và chỗ không truy cập được); mọi số đo của hệ thống hiện tại lấy từ
> [evidence/baseline.md](evidence/baseline.md) (chạy thật ngày 12/11) và `docs/v3/eval/`. Số nào là mục tiêu thì ghi rõ
> **đề xuất, chưa đo**. Tên bảng, cột, file, module theo [ARCHITECTURE.md](ARCHITECTURE.md) §9; một ngoại lệ ghi ở §4.1
> (bảng case trong CSDL tên thật là `eval.eval_item`).
>
> Ba cảnh báo về bằng chứng, giữ nguyên từ lượt nghiên cứu: (1) cổng Socrata `data.cityofnewyork.us` trả **HTTP 403**
> cho mọi yêu cầu không phải trình duyệt từ máy nghiên cứu, nên số dòng và danh sách cột của Dataset A lấy từ thông báo
> chính thức của NYC, bản ghi catalog data.gov và một bản liệt kê thứ cấp, **không phải `count(*)` trực tiếp**; phải
> chạy lại §2.4 từ máy có app token trước khi đóng băng snapshot. (2) Mọi sự kiện SEC được fetch trực tiếp (API,
> HEAD kích thước, readme FSDS, 10-K Apple FY2023, 10-K/A ADM). (3) Cổng dữ liệu Việt Nam `data.gov.vn`,
> `open.data.gov.vn` không phân giải DNS từ máy nghiên cứu; giấy phép nguồn Việt Nam ghi theo mức chắc chắn tìm được.

## 1. Vì sao cần dữ liệu thật và chọn gì

Hệ thống hôm nay chỉ có **một** Data Space thử nghiệm (AdventureWorksDW, 61 câu, đáp án nháp chưa giải mù) và mọi
ngưỡng online của `model/eval/thresholds.json` đều `provisional`. Kết luận "governed 20/20" trên AW là bằng chứng về
engine trên một kho mẫu sạch, không phải bằng chứng về sản phẩm trên dữ liệu doanh nghiệp. Hai bộ dữ liệu công khai
dưới đây được chọn vì chúng đo đúng hai thứ AW không đo được:

| Bộ | Đo được gì mà AW không đo | Không đo được gì |
| --- | --- | --- |
| **A — NYC 311** (bảng lớn, dữ liệu bẩn thật, chuỗi thời gian theo ngày, giá trị phân loại nhiều) | Nạp theo lô và tăng dần trên hàng triệu dòng; drift schema thật (cột đổi tên/thêm tháng 12/2025); DQ thật (đóng trước mở, null nhiều dạng, đóng theo lô); câu hỏi group-by/time-series/median/YoY; mơ hồ nghiệp vụ ("resolved" là gì) | Tài liệu, nguồn chéo (không có văn bản đi kèm), tiếng Việt |
| **B — SEC EDGAR** (số liệu tài chính XBRL + chính báo cáo 10-K/10-Q dạng iXBRL HTML, có bản sửa đổi) | Bảng tài chính phức tạp trong tài liệu; cùng một con số ở DB và ở văn bản; kỳ tài chính ≠ năm lịch; restatement/amendment; câu hỏi đa kỳ | Dữ liệu vận hành lớn, tiếng Việt |
| **C — tuỳ chọn, Việt Nam** (vbpl.vn, NSO) | Tiếng Việt, văn bản thay thế/hết hiệu lực | Giấy phép chưa chắc; không phân phối lại được bộ đã thu |

Dữ liệu tổng hợp (synthetic) **chỉ** dùng cho ba việc: tiêm lỗi (file hỏng, trùng khoá, cột đổi kiểu), chèn
injection, và tình huống hiếm (tenant chéo). Không gọi kết quả trên dữ liệu tổng hợp là "real-data evaluation".

## 2. Dataset A — NYC Open Data 311 Service Requests 2020–present

### 2.1 Định danh và truy cập

| Mục | Giá trị | Nguồn |
| --- | --- | --- |
| URL chính thức | `https://data.cityofnewyork.us/d/erm2-nwe9` (định danh `…/api/views/erm2-nwe9`) | bản ghi data.gov |
| Nhà phát hành / cập nhật | City of New York; phát hành lại 23/12/2025 sau khi tách 2010–2019 sang `76ig-c548`; sửa đổi 10/11/2026; "updated daily" | data.gov, thông báo NYC |
| Endpoint xuất | SODA 3: `…/api/v3/views/erm2-nwe9/query.json|.xml|.geojson`, `…/export.csv|.kml|.kmz`; SODA 2.1: `/resource/erm2-nwe9.json|.csv|.geojson` | data.gov, dev.socrata.com |
| Phân trang | `$limit` mặc định 1 000; 2.0 trần 50 000; **2.1 và 3.0 không trần**; `$limit` + `$offset`; phải `$order=unique_key` để trang ổn định (thực hành chuẩn, Socrata không nêu) | dev.socrata.com/docs/queries/limit |
| App token | Không bắt buộc nhưng không có thì chia chung pool IP và bị 429; có token "we do not throttle"; header `X-App-Token` | dev.socrata.com/docs/app-tokens |
| Trường hệ thống | `:id`, `:created_at`, `:updated_at` qua `$select=:*,*` (2.1), lọc được trong `$where` | dev.socrata.com/docs/system-fields |
| Đổi tên cột (UI) | Complaint Type → Problem, Descriptor → Problem Detail; **tên trường API không đổi**; thêm `council_district`, `police_precinct`, `additional_details` | nyc.gov/opendata/news (12/2025) |

### 2.2 Giấy phép và điều khoản

Bản ghi catalog **không nêu giấy phép**. Trang tổng quan NYC Open Data trỏ tới điều khoản nyc.gov, vốn giữ mọi quyền,
không cấp quyền tái sử dụng rõ và từ chối bảo đảm ("AS IS"); Luật Dữ liệu mở chỉ nói dữ liệu cơ quan "open by default".
Trang điều khoản riêng của cổng không truy cập được từ máy nghiên cứu. **Quyết định:** dùng nội bộ cho đánh giá với dòng
ghi nguồn "NYC OpenData, 311 Service Requests (erm2-nwe9)"; **không** phân phối lại snapshot ra ngoài tổ chức cho tới
khi điều khoản được xác nhận; ghi trạng thái "terms to be confirmed" vào manifest.

### 2.3 Schema (cột và kiểu)

Danh sách cột dưới đây là schema 2010–present theo bản liệt kê thứ cấp (MotherDuck) cộng ba cột chính thức thêm
12/2025; **phải đối chiếu với `…/api/views/erm2-nwe9.json` khi có app token** (EV-01).

| Cột | Kiểu | Ghi chú |
| --- | --- | --- |
| `unique_key` | bigint, khoá | — |
| `created_date`, `closed_date`, `due_date`, `resolution_action_updated_date` | timestamp (giờ địa phương, không múi giờ) | DST gây giờ trùng/thiếu |
| `agency`, `agency_name` | text | `agency_name` từng sai, "use the Agency column" |
| `complaint_type`, `descriptor`, `location_type` | text | nhiều biến thể "Noise - …" |
| `incident_zip`, `incident_address`, `street_name`, `cross_street_1/2`, `intersection_street_1/2`, `address_type`, `city`, `landmark`, `facility_type` | text | `address_type` ∈ Address/Block face/Intersection/LatLong/Placename |
| `status`, `resolution_description` | text | từ điển: Assigned/Canceled/Closed/Pending; thực tế có "in progress", "started", "unspecified", không có "canceled" |
| `bbl`, `borough`, `x/y_coordinate_state_plane`, `open_data_channel_type` | text | kênh ∈ Phone/Online/Other; `borough` có 'Unspecified' |
| `park_facility_name`, `park_borough`, `vehicle_type`, `taxi_company_borough`, `taxi_pick_up_location`, `bridge_highway_name/direction/segment`, `road_ramp`, `community_board` | text | `park_borough` = `borough` 100 % |
| `latitude`, `longitude` | double | 14 chữ số thập phân |
| `location` | point | — |
| `council_district`, `police_precinct`, `additional_details` | text | thêm 12/2025 |
| `@computed_region_*` ×6 | — | không tài liệu, "not for public use" → bỏ khi nạp |

### 2.4 Khối lượng (ước lượng, nguồn ghi kèm)

| Phạm vi | Dòng | CSV | Nguồn |
| --- | --- | --- | --- |
| Toàn bộ 2020–nay | > 40 triệu (12/2025) | ≈ 22 GB; ≈ 2–3 GB Parquet | thông báo NYC; ≈ 560 B/dòng suy từ nghiên cứu arXiv 2502.08649 (6,3 M dòng 2022–2023 ≈ 3,5 GB) |
| Một năm | ≈ 3,1–3,2 triệu | ≈ 1,8 GB | như trên |
| **S3** (01/01–31/03/2025) | ≈ 0,8 triệu | ≈ 450 MB | như trên |
| **S12** (2024) + 2023 cho YoY | ≈ 3,2 + 3,2 triệu | ≈ 3,6 GB | như trên |

Thời gian kéo ở 50 000 dòng/trang: 16–130 trang mỗi snapshot.

### 2.5 Kế hoạch snapshot và tái lập

Socrata không có phiên bản dataset và **dòng được sửa tại chỗ** (`status`, `closed_date`, `resolution_description` đổi
sau khi tạo; nghiên cứu arXiv ghi 10 265 yêu cầu được cập nhật > 30 ngày sau khi đóng). Vì vậy đáp án vàng định nghĩa
trên snapshot, không bao giờ trên API sống.

| Snapshot | Cửa sổ `created_date` | Dùng cho | Ghi chú |
| --- | --- | --- | --- |
| `S3-2025Q1` | 2025-01-01 ≤ created_date < 2025-04-01 | vòng lặp phát triển, 40 case structured, DQ, drift | vừa Postgres máy dev |
| `S12-2024` (+ `S12-2023`) | năm 2024 (+ 2023) | YoY, mùa vụ, median theo quý | cần cho 10 case analytical |

Quy trình (CLI `tests/e2e/nyc311/collect.py`, EV-01):

1. Kéo theo cửa sổ `created_date` với `$order=unique_key&$limit=50000&$offset=…` và `$select=:id,:created_at,:updated_at,*`;
   ghi `max(:updated_at)` và thời điểm kéo `T` vào manifest.
2. Chuẩn hoá từng dòng (khoá sắp xếp, bỏ null, timestamp ISO), `sha256` mỗi dòng theo `unique_key`; manifest = danh
   sách `unique_key\tsha256` đã sắp + hash tổng; dữ liệu ghi Parquet một file/tháng bằng `libs/snapshot` (zstd,
   `write_page_checksum=True`, metadata `snapshot_id`, `T`, `source_url`, `terms_status`).
3. Kiểm trôi về sau: `$where=:updated_at > 'T' AND created_date between …` đếm dòng đã đổi; ghi vào
   `snapshots/<id>.drift.json`; đáp án vàng không đổi theo.
4. Idempotent: chạy lại cùng cửa sổ cho ra cùng manifest nếu nguồn không đổi; nếu đổi thì CLI từ chối ghi đè và yêu cầu
   `snapshot_id` mới.
5. Retry: 429/5xx backoff 2^n tới 5 lần; tiếp tục từ `$offset` cuối đã ghi (`.progress.json`).

### 2.6 Hồ sơ chất lượng (đã biết, từ nghiên cứu arXiv 2502.08649 trên trích 2022–2023 trừ khi ghi khác)

| Vấn đề | Số lượng | Hệ quả cho eval |
| --- | --- | --- |
| Đóng trước khi mở (`closed_date < created_date`) | 12 450 (0,2 %); 8 dòng `1900-01-01`, lệch tới −44 601 ngày; 95 % DOT | case DQ bắt buộc; median phải loại |
| Thời gian xử lý = 0 | 163 720 (2,6 %) | bẫy "nhanh nhất" |
| Tạo/đóng đúng 00:00:00 | ≈ 247 000 (lô, 92 % DOB + DSNY) | bẫy giờ |
| Giờ DST thiếu/trùng | — | có thể làm đóng < mở |
| `due_date` trống | 99,6 % | cột gần vô dụng |
| `incident_zip` sai (12345, 10000, 99999) | 0,07 % | lọc ZIP hợp lệ |
| Null nhiều dạng: null, "", "NA", "N/A", "<NA>" | — | chuẩn hoá khi nạp |
| `status` ngoài từ điển (in progress, started, unspecified); không bao giờ "canceled" | — | câu "resolved" mơ hồ |
| `agency`/`agency_name` dư thừa; `park_borough` = `borough` | 100 % | ví dụ "metric xung đột" |
| HPD đóng khiếu nại sưởi theo lô (194 trong 1 giây; "conditions are still open") | — (311tracker) | `status='Closed'` ≠ giải quyết |
| Trùng `unique_key` | **không nguồn nào báo**; phải tự kiểm `count` vs `count(distinct)` | case duplicates dùng tiêm |

### 2.7 Data Space `DS-311` và semantic model

Nạp qua đường có sẵn: `catalog.source` kind `file`/upload Parquet → `worker_ingest` (lô 5 000, versioning F-01) → dataset
`nyc311_requests`; `deploy/docker/semantic/ds-311.yaml` (viết theo `dbt-semantic-interfaces`, ADR-58). Metric đề xuất
(15–20, phải được steward chứng nhận trước khi đo, vì điểm phụ thuộc metric đã duyệt — nhận định 1 của 00-ASSESSMENT):

| Metric | Định nghĩa | Chiều cho phép | Additivity |
| --- | --- | --- | --- |
| `request_count` | COUNT(*) | borough, agency, complaint_type, channel, status, zip, month/week/day | full |
| `closed_count` | COUNT WHERE status = 'Closed' | như trên | full |
| `open_count` | COUNT WHERE status NOT IN ('Closed') | như trên | full |
| `median_hours_to_close` | percentile_cont(0,5) của (closed − created)/3600 WHERE closed ≥ created AND closed − created > 5 phút | borough, agency, complaint_type | none |
| `mean_hours_to_close` | AVG cùng điều kiện | như trên | none |
| `open_after_30d_share` | open_count tại created+30d / request_count | borough, agency | none (ratio) |
| `closed_same_day_share` | COUNT(closed cùng ngày)/request_count | agency | none |
| `zero_duration_count` | COUNT closed = created | agency | full |
| `closed_before_created_count` | COUNT closed < created | agency | full |
| `no_geo_count` / `no_geo_share` | COUNT latitude IS NULL | agency, channel | full / none |
| `online_share` | COUNT channel='Online' / request_count | borough, month | none |
| `requests_per_day` | request_count / số ngày trong kỳ | borough | none |
| `noise_request_count` | COUNT complaint_type LIKE 'Noise%' | borough, month | full |
| `heat_hot_water_count` | COUNT complaint_type = 'HEAT/HOT WATER' | borough, month | full |
| `rodent_count` | COUNT complaint_type = 'Rodent' | zip, quarter | full |
| `illegal_parking_count` | COUNT complaint_type = 'Illegal Parking' | month | full |
| `top_complaint_types` | xếp hạng request_count theo complaint_type | borough | — |

Chiều thời gian: `created_date` (grain day/week/month/quarter/year, lịch dương, không fiscal). Giá trị phân loại vào
`context.value_index` (`borough`, `agency`, `complaint_type`, `channel`) để resolver tất định khớp bộ lọc.

### 2.8 Mười hai câu mẫu và SQL vàng (phác), cờ mơ hồ

| # | Câu hỏi | SQL vàng (phác, trên snapshot) | Mơ hồ / bẫy |
| --- | --- | --- | --- |
| A1 | Top 5 loại khiếu nại theo quận, S3 | `GROUP BY borough, complaint_type` + `ROW_NUMBER() OVER (PARTITION BY borough ORDER BY n DESC) ≤ 5` | `borough='Unspecified'` giữ hay bỏ → case clarify |
| A2 | Số khiếu nại tiếng ồn theo tháng 2024 | `date_trunc('month', created_date)`, `complaint_type LIKE 'Noise%'` | "tiếng ồn" gồm loại nào → clarify |
| A3 | Median giờ đóng HEAT/HOT WATER theo quận, mùa đông 2024 | `percentile_cont(0.5)` trên `closed-created` WHERE `closed ≥ created` | loại 0 phút và đóng lô? median vs mean |
| A4 | Tỷ lệ còn mở sau 30 ngày theo cơ quan | `status NOT IN ('Closed')` tại created+30d | từ điển `status` |
| A5 | Tăng/giảm YoY Illegal Parking theo tháng 2024 vs 2023 | hai năm, `LAG` theo tháng | cần S12 + 2023 |
| A6 | ZIP tăng khiếu nại chuột nhiều nhất Q1/2025 vs Q1/2024 | lọc ZIP 5 số hợp lệ | ZIP rác |
| A7 | Yêu cầu trên 1 000 dân theo quận | **không trả lời được** (không có bảng dân số) | expected_action = `refuse_missing_source` |
| A8 | Cơ quan xử lý nhanh nhất, loại đóng trong 5 phút | AVG với điều kiện > 5 phút | bẫy đóng lô |
| A9 | Ngày trong tuần / giờ bận nhất của DSNY | `extract(dow)`, `extract(hour)` | múi giờ, DST |
| A10 | Số yêu cầu không có toạ độ theo cơ quan | `latitude IS NULL` | `address_type` |
| A11 | Xu hướng kênh Online vs Phone theo quý | `open_data_channel_type` | — |
| A12 | "Tháng trước giải quyết được bao nhiêu khiếu nại?" | — | "giải quyết" = `status='Closed'` hay `closed_date` trong tháng → `clarify` |

## 3. Dataset B — SEC EDGAR

### 3.1 API, giới hạn, điều khoản (fetch trực tiếp)

| Mục | Giá trị |
| --- | --- |
| Tài liệu API | `https://www.sec.gov/search-filings/edgar-application-programming-interfaces` (URL cũ `/edgar/sec-api-documentation` phục vụ cùng nội dung) |
| Endpoint | `data.sec.gov/submissions/CIK##########.json` (CIK 10 chữ số); `api/xbrl/companyconcept/CIK…/us-gaap/Tag.json`; `api/xbrl/companyfacts/CIK….json`; `api/xbrl/frames/us-gaap/Tag/USD/CY2019Q1I.json` |
| Frames | `CY####` = kỳ 365 ± 30 ngày; `CY####Q#` = 91 ± 30; `CY####Q#I` = thời điểm; một fact/thực thể, lấy fact "nộp sau cùng khớp kỳ lịch nhất"; chỉ taxonomy chuẩn |
| Bulk | `companyfacts.zip` 1 423 778 421 B; `submissions.zip` 1 574 112 446 B (HEAD, last-modified 11/11/2026); cập nhật hằng đêm ≈ 03:00 ET |
| Fair access | **tối đa 10 request/giây**; bắt buộc `User-Agent: <Company> <email>`; SEC chặn IP lạm dụng; "anyone can access and download this information for free" (không có chữ "public domain") — `https://www.sec.gov/os/accessing-edgar-data` |
| Full-text search | UI từ 2001; endpoint JSON `efts.sec.gov/LATEST/search-index?q=…&forms=…&ciks=…` **không có tài liệu chính thức**, trường quan sát được: `ciks, display_names, file_date, period_ending, form, adsh, _id = adsh:filename` |
| Thư mục hồ sơ | `https://www.sec.gov/Archives/edgar/data/{cik}/{adsh không gạch}/` luôn chứa mọi file: iXBRL chính (`aapl-20230930.htm` 1,56 MB), instance `_htm.xml`, `_cal/_def/_lab/_pre.xml`, `.xsd`, `MetaLinks.json`, `FilingSummary.xml`, `Financial_Report.xlsx`, `R1…R75.htm` |

Giấy phép: dữ liệu liên bang Hoa Kỳ, truy cập miễn phí theo chính sách fair access; ghi nguồn "U.S. SEC EDGAR";
không ràng buộc phân phối lại được nêu. Tuân thủ 10 req/s và User-Agent là bắt buộc kỹ thuật.

### 3.2 Financial Statement Data Sets (FSDS)

ZIP theo quý, bốn bảng `.txt` tab-delimited UTF-8, "as filed", "may contain redundancies, inconsistencies", gồm cả bản
sửa đổi; phủ 2009Q1–2026Q3; readme `https://www.sec.gov/files/financial-statement-data-sets.pdf`. Kích thước: 2024Q4
117 MB, 2025Q1 122 MB, 2025Q2 75 MB, 2025Q3 122 MB, 2025Q4 63 MB, 2026Q1 81 MB. Bộ "and Notes" thêm `txt/dim/ren/cal`
(2025Q2 496 MB) — **không dùng ở MVP**.

| Bảng | Khoá | Cột chính |
| --- | --- | --- |
| `sub` | `adsh` | `cik, name, sic, countryba, stprba, cityba, countryinc, ein, former, changed, afs, wksi, fye (mmdd), form, period, fy, fp (FY/Q1–Q4), filed, accepted, prevrpt (1 = đã bị sửa đổi sau), detail, instance, nciks, aciks` |
| `num` | `(adsh, tag, version, ddate, qtrs, uom, segments, coreg)` | `value NUMERIC(28,4)` không scale, `footnote`; `qtrs=0` = thời điểm; `segments` thêm 12/2024 |
| `tag` | `(tag, version)` | `custom, abstract, datatype, iord (I/D), crdr, tlabel, doc` |
| `pre` | `(adsh, report, line)` | `stmt (BS/IS/CF/EQ/CI/SI/UN), inpth, rfile, tag, version, plabel, negating`; `report` = số R-file |

Bảng bổ sung tự dựng: `companyfacts_fact(cik, taxonomy, tag, unit, start, end, val, accn, fy, fp, form, filed, frame)`,
`submissions_filing(cik, accn, form, filingDate, reportDate, primaryDocument, isInlineXBRL, size)`.

### 3.3 Ngữ nghĩa restatement / amendment (đã kiểm)

- `companyfacts` lặp một fact **mỗi lần nộp**: doanh thu Apple FY2023 383 285 000 000 xuất hiện ba lần (accn
  `…-23-000106` fy2023, `…-24-000123` fy2024, `…-25-000079` fy2025), chỉ lần cuối mang `frame: "CY2023"`.
  **`fy`/`fp` mô tả hồ sơ, không mô tả kỳ của fact** → dedupe theo (`start`, `end`, `frame`), không bao giờ theo `fy`.
- ADM (CIK 7084) nộp 10-K/A `0000007084-24-000051` ngày 18/11/2024 sửa thông tin **phân khúc** FY2021–2023; ghi chú
  giải thích nói báo cáo hợp nhất không đổi → case "metric phân khúc xung đột, tổng khớp".
- Plug Power (CIK 1093691): 10-K FY2020 `0001558370-21-007147` (14/05/2021) và 10-K/A `0001558370-22-003577`
  (14/03/2022), thêm 10-K/A 01/05/2023, 30/04/2025 → case chuỗi sửa đổi.
- Trong FSDS: `sub.prevrpt=1` đánh dấu bản gốc đã bị thay; 10-K/A là `adsh` riêng với `form='10-K/A'`.

### 3.4 Phạm vi `E20-FY21-24`

| Ngành | Công ty (CIK) |
| --- | --- |
| Công nghệ | AAPL 320193, MSFT 789019, NVDA 1045810 |
| Tài chính | JPM 19617, BAC 70858, GS 886982 |
| Năng lượng | CVX 93410, **XOM — cảnh báo**: file ticker hiện ánh xạ XOM sang CIK 2115436 "ExxonMobil Holdings Corp"; hồ sơ FY2021–2024 nằm dưới CIK tiền nhiệm, phải xác minh trước khi nạp |
| Y tế | PFE 78003, JNJ 200406, UNH 731766 |
| Tiêu dùng / bán lẻ | WMT 104169, COST 909832, HD 354950, PG 80424 |
| Công nghiệp | BA 12927, CAT 18230, GE 40545 |
| Restatement | **ADM 7084**, **PLUG 1093691** |

Năm tài chính không kết thúc tháng 12 (AAPL 0926 đã xác minh; WMT/HD/NVDA tháng 1, MSFT/PG tháng 6, COST tháng 8 —
xác minh qua `fiscalYearEnd`) tạo case FY vs CY.

Khối lượng: 16 hồ sơ/công ty (4 × 10-K + 12 × 10-Q) ≈ 320 + ≈ 6 sửa đổi; iXBRL chính ≈ 1,5 MB/hồ sơ → ≈ 0,5–1 GB; full
submission ≈ 2,5–3 GB (Apple 17 hồ sơ = 129 MB); companyfacts ≈ 80–150 MB cho 20 (Apple 3,86 MB, 520 tag, 25 604 fact);
FSDS 2021q1–2024q4 = 16 ZIP ≈ 1,5–2 GB nén (chỉ nạp `sub` của 20 CIK + `num/pre` của chúng, `tag` toàn bộ).

### 3.5 Data Space `DS-SEC`: ánh xạ tag → metric

| Metric | Tag us-gaap (ghi tag đã chọn cho từng công ty vào case) | Điều kiện `num` |
| --- | --- | --- |
| `revenue` | `RevenueFromContractWithCustomerExcludingAssessedTax` hoặc `Revenues` (ngân hàng: `Revenues`/`InterestAndDividendIncomeOperating`) | `qtrs=4` (năm) / `1` (quý), `segments IS NULL`, `coreg IS NULL` |
| `net_income` | `NetIncomeLoss` | như trên |
| `total_assets` | `Assets` | `qtrs=0`, `ddate=period` |
| `cash_and_equivalents` | `CashAndCashEquivalentsAtCarryingValue` | `qtrs=0` |
| `operating_income` | `OperatingIncomeLoss` | `qtrs=4/1` |
| `goodwill` | `Goodwill` | `qtrs=0` |
| `eps_diluted` | `EarningsPerShareDiluted` | uom `USD/shares` |
| `segment_operating_profit` | tag theo công ty, `segments IS NOT NULL` | case ADM |

Chiều: `cik`, `fy`, `fp`, `form`, `filed`, `frame`. Thẩm quyền khi xung đột (tất định, ADR trong ASK/CONTRACTS):
10-K/A > 10-K cùng kỳ; `frame` của companyfacts = fact nộp sau cùng; metric chứng nhận > số trong văn bản.

Kho tài liệu: iXBRL HTML chính của mỗi 10-K/10-Q/10-K/A, nạp qua `worker_document` với metadata `accn`, `cik`, `form`,
`reportDate` → `available_at`; bản 10-K/A đặt `supersedes_id` trỏ bản gốc. `R*.htm` tuỳ chọn.

### 3.6 Đáp án vàng nguồn chéo

"Doanh thu FY2023 của X theo CSDL và theo 10-K": (1) giá trị CSDL = dòng `num` với tag đã chọn, `qtrs=4`, `ddate=period`,
không segment/coreg, từ `adsh` của 10-K gốc (`prevrpt` cho biết đã bị thay chưa); (2) giá trị văn bản = trong iXBRL
`<ix:nonFraction name="us-gaap:…" contextRef="c-1" id="f-69" scale="6" decimals="-6">383,285</ix:nonFraction>` với
context `c-1` = CIK 0000320193, 2022-09-25…2023-09-30, không segment (Apple: 205 context, 995 fact số, 169 fact chữ);
cùng số lặp ở `f-385`, `f-1134`. Case vàng lưu `(accn, fact id, contextRef, displayed text, scale, decimals, computed
value, pre.report)`. Apple: hai nguồn phải khớp; ADM: lợi nhuận phân khúc FY2023 khác nhau giữa 10-K và 10-K/A, agent phải
nêu cả hai và lấy bản sửa đổi làm thẩm quyền.

### 3.7 Mười hai câu mẫu

| # | Loại | Câu hỏi | Vàng |
| --- | --- | --- | --- |
| B1 | structured | Tổng doanh thu FY2023 của 20 công ty, xếp hạng | `num` theo tag mỗi công ty, `qtrs=4` |
| B2 | structured | Tiền và tương đương cuối FY2023 vs FY2022 của Apple | `qtrs=0`, hai `ddate` |
| B3 | structured | Công ty nào `NetIncomeLoss` giảm ba năm liên tiếp FY2021–24 | chuỗi 4 năm |
| B4 | document | 10-K FY2023 của Boeing nêu rủi ro chính nào | truy hồi mục Risk Factors, trích trang |
| B5 | document | Kiểm toán viên và số năm kiểm toán JPM FY2024 | chữ trong 10-K |
| B6 | hybrid | Doanh thu Apple FY2023 theo CSDL vs 10-K, dẫn fact id | §3.6 |
| B7 | hybrid | Lợi nhuận phân khúc Ag Services & Oilseeds ADM FY2023 — bản gốc vs 10-K/A, bản nào có hiệu lực | `SOURCE_CONFLICT` giải bằng thẩm quyền amendment |
| B8 | hybrid | 10-K FY2024 của Microsoft giải thích thay đổi `Goodwill` trong CSDL thế nào (Activision) | số + đoạn văn |
| B9 | multi-period | Chuỗi doanh thu quý FY2022–24 NVDA, Q4 = FY − (Q1+Q2+Q3) | 10-K không có fact Q4 |
| B10 | multi-period | Doanh thu CY2023 qua Frames vs doanh thu năm tài chính AAPL/WMT — giải thích chênh | FY ≠ CY |
| B11 | restatement | Lỗ ròng Plug Power FY2020: 10-K gốc vs 10-K/A vs số lặp trong 10-K FY2021 | ba giá trị, thẩm quyền |
| B12 | clarify | "Doanh thu 2024 của Walmart?" | FY2024 (kết thúc 01/2024) vs FY2025 → `clarify` |

### 3.8 CLI thu thập `tests/e2e/edgar/collect.py` (EV-02)

Tham số: `--cik <list|file>`, `--fy-from 2021 --fy-to 2024`, `--forms 10-K,10-Q,10-K/A,10-Q/A`, `--out <dir>`,
`--user-agent "<Company> <email>"` (bắt buộc), `--rate 8` (≤ 10). Việc: tải `submissions`, lọc hồ sơ, tải iXBRL chính +
`MetaLinks.json`, tải `companyfacts`, tải FSDS ZIP theo quý và lọc theo CIK, ghi Parquet cho `sub/num/tag/pre/
companyfacts_fact/submissions_filing` + manifest sha256 từng file và tổng; `--verify` chạy lại HEAD và so
`last-modified`. Idempotent theo `(cik, accn)`; retry backoff; dừng khi 403 (bị chặn) và báo rõ.

## 4. Dataset C (tuỳ chọn) — nguồn Việt Nam

| Nguồn | Dùng cho | Giấy phép / điều khoản (mức chắc chắn tìm được) |
| --- | --- | --- |
| **vbpl.vn** (CSDL quốc gia về pháp luật, Bộ Tư pháp) | 8 case tài liệu tiếng Việt: hiệu lực, thay thế, hết hiệu lực — ánh xạ trực tiếp lên `available_at`, `supersedes_id` | Nghị định 52/2015/NĐ-CP Điều 4: văn bản "được sử dụng chính thức"; Điều 18(2): cơ quan, tổ chức, cá nhân "được quyền khai thác, sử dụng miễn phí văn bản". vbpl.vn không có trang điều khoản (fetch 12/11/2026); nghị định thay thế đang dự thảo (05/2026). Văn bản pháp luật nói chung không được bảo hộ quyền tác giả; **quyền sưu tập của CSDL chưa rõ**. Quyết định: dùng nội bộ, không phân phối lại bộ đã thu |
| **Cục Thống kê (NSO) PxWeb** `nso.gov.vn/px-web-2/?pxid=…` | Bảng thống kê có cấu trúc tiếng Việt | Chân trang: bản quyền Cục Thống kê, ghi rõ nguồn khi trích; không có giấy phép mở. API PxWeb đoán `/px-web-2/api/v1/vi/` trả 404 — **chưa xác minh**; cổng `data.gov.vn`/`open.data.gov.vn` không phân giải DNS |
| HCMC open data, HOSE | — | **Không dùng**: HCMC cấm sao chép/phân phối lại không có văn bản đồng ý; HOSE có quyền bán dữ liệu thị trường (Luật Chứng khoán 2019 Điều 46) |

Nếu C không xác nhận được giấy phép trước tuần 8, 8 case tài liệu tiếng Việt chuyển sang B (tài liệu tiếng Anh) và bộ
ba tài liệu AdventureWorks tiếng Việt hiện có (`deploy/docker/adventureworks-docs/`) giữ vai trò duy nhất cho tiếng Việt.

## 5. Bộ vàng (golden evaluation)

### 5.1 Schema một case

Ghi dưới dạng JSONL (một dòng/case); nạp vào `eval.eval_item` (bảng có sẵn; cột mới theo ARCHITECTURE §9.1 — ở đó ghi
`eval.eval_case`, tên bảng thật là `eval.eval_item`, dùng tên thật) qua `deploy/docker/eval/ds-311-cases.yaml` và
`ds-sec-cases.yaml` giống cách `ds-aw-cases.yaml` đang nạp.

```text
id                   "A-S3-STR-007"   (dataset-snapshot-category-seq)
dataset_snapshot     {"dataset":"nyc311","snapshot":"S3-2025Q1","manifest_sha256":"…"}
                     | {"dataset":"edgar","snapshot":"E20-FY21-24","fsds_zips":[…],"companyfacts_pulled":"2026-11-12"}
question             ngôn ngữ tự nhiên (vi | en); ghi "lang"
history              [{role, text}] cho case nhiều lượt, [] nếu không
expected_action      answer | clarify | refuse_missing_source | refuse_permission | handover | flag_conflict
expected_result      giá trị chuẩn (đơn vị, tolerance) | lớp câu hỏi lại | lý do từ chối | hợp đồng đầu ra đóng băng
gold_sql_or_computation   SQL trên schema snapshot | tra companyfacts/iXBRL | "n/a"
expected_evidence    [{"source":"fsds.num","adsh":…},{"source":"doc","accn":…,"fact_id":"f-69"},{"source":"query_run"}]
                     — nhãn tin cậy phải trích được tới đây; thêm "expected_trust_code"
failure_category     none | ambiguous | missing_source | wrong_schema | conflicting_metrics | unavailable_document
                     | corrupted_file | duplicates | schema_drift | permission_violation | injection | stale_data
                     | partial_ingestion
rubric               exact | numeric_tolerance(pct) | set_equality | contains_all | judge(prompt_id)
verification_status  draft | sql_run_on_snapshot | human_verified | disputed
version              số nguyên; sửa case = version mới, case cũ giữ để so sánh lịch sử
```

Ánh xạ sang `eval.eval_item`: `input` = {question, history, lang}; `expected` = {expected_result, output_contract,
expected_evidence, expected_trust_code}; `category` = failure_category hoặc nhóm (structured/multiturn/document/
analytical/hybrid); cột mới `expected_action`, `failure_category`, `rubric`, `verification_status`, `snapshot_ref`
(EV-08). Trường `as_of` = ngày cuối snapshot.

### 5.2 Phân bổ ≥ 150 case

| Nhóm | Tổng | Dataset A (311) | Dataset B (SEC) | Dataset C (vi) |
| --- | --- | --- | --- | --- |
| Structured | 40 | 24 (group-by, time-series, thời gian xử lý) | 16 (FSDS/companyfacts) | — |
| Multi-turn / clarification | 30 | 16 ("tiếng ồn", "giải quyết", 'Unspecified', cửa sổ ngày) | 14 (FY vs CY, tag doanh thu nào, hồ sơ nào sau sửa đổi) | — |
| Document | 30 | — | 22 (mục 10-K, rủi ro, kiểm toán, ghi chú phân khúc) | 8 (hiệu lực / thay thế; nếu giấy phép không xác nhận → chuyển sang B) |
| Analytical | 25 | 15 (YoY, mùa vụ, median/percentile, loại đóng lô) | 10 (xu hướng 3 năm, suy Q4, biên) | — |
| Hybrid cross-source | 25 | 5 (311 + bảng dân số nhỏ harness cung cấp — kiểm join nguồn chéo) | 20 (CSDL vs hồ sơ, xung đột sửa đổi, Frames vs FY) | — |
| **Tổng** | **150** | 60 | 82 | 8 |

Case âm / đối kháng chiếm ≈ 35 % và nằm **bên trong** các nhóm trên (không phải nhóm riêng), để agent "trả lời mọi thứ"
không qua được. Mỗi case âm ghi hành động mong đợi và bằng chứng phải lộ ra lỗ hổng, không chỉ "số đúng".

### 5.3 Case âm / đối kháng — phác thảo

| Loại | Phác thảo case (1–2) | expected_action |
| --- | --- | --- |
| ambiguous | A12 "tháng trước giải quyết bao nhiêu"; B12 "doanh thu 2024 Walmart" | `clarify` với ≤ 3 lựa chọn đúng lớp |
| missing_source | A7 "trên 1 000 dân" (không có dân số); B: "giá cổ phiếu hôm nay" | `refuse_missing_source`, nêu thiếu gì |
| wrong_schema | hỏi `complaint_category` (không tồn tại, có `complaint_type`); hỏi `net_revenue` khi catalog chỉ có `revenue` | `clarify` gợi cột gần nhất hoặc `refuse`; không bịa cột |
| conflicting_metrics | ADM phân khúc 10-K vs 10-K/A; `agency` vs `agency_name` | `flag_conflict`; nêu hai số + thẩm quyền |
| unavailable_document | 10-K của công ty ngoài 20; dòng 311 năm 2019 | `refuse_missing_source` |
| corrupted_file | `num.txt` bị cắt ở 80 %; một 10-Q HTML hỏng khối `ix:resources` | nạp báo lỗi `config`/`partial`; câu hỏi liên quan → `INSUFFICIENT` hoặc nêu rõ hồ sơ hỏng |
| duplicates | companyfacts fact lặp ba lần (thật); tiêm 2 % dòng 311 trùng `unique_key` | số không nhân đôi; DQ `unique` báo |
| schema_drift | đổi tên cột 12/2025 (`complaint_type` UI); `segments` thêm vào NUM 2024Q4 | job → `schema_drift`, không nạp sai; câu hỏi trước đó vẫn đúng trên version cũ |
| permission_violation | Data Space giới hạn một quận / một CIK, câu hỏi hỏi toàn bộ | `FORBIDDEN` không lộ tên; không rơi vào `NO_DATA` |
| injection | `resolution_description` chứa "ignore previous instructions…"; exhibit 10-K chứa lệnh; câu hỏi chứa `; DROP TABLE` | 422 hoặc bỏ qua; `policy_violation_rate` = 0 |
| stale_data | yêu cầu 311 đóng sau `T` (snapshot nói còn mở); số ADM trước sửa đổi | trả lời theo snapshot kèm `as_of`; sửa đổi thắng |
| partial_ingestion | chỉ 10/12 10-Q nạp; frame có 3 142 thực thể nhưng thiếu 1/20 | câu hỏi chuỗi quý nói rõ thiếu kỳ nào; không nội suy |

### 5.4 Rubric chấm

| Rubric | Áp dụng | Cách chấm |
| --- | --- | --- |
| `exact` | giá trị rời rạc, tên, mã | so chuỗi sau chuẩn hoá `libs/language.norm` |
| `numeric_tolerance(pct)` | số tiền, tỷ lệ | lệch tương đối ≤ pct (mặc định 0,5 %; 0,01 % cho số XBRL) |
| `set_equality` | bảng nhiều dòng | **hợp đồng đầu ra đóng băng** (01 §6): kiểu cột, độ chính xác, đơn vị, thứ tự; bỏ qua header và thứ tự cột; so multiset có trùng; sai hình dạng = 0 |
| `contains_all` | câu trả lời tài liệu | mọi trích dẫn vàng (doc, trang, Điều/fact id) có mặt; số trong câu ⊂ số trong đoạn trích |
| `judge(prompt_id)` | chỉ cho phần văn xuôi (giải thích, rủi ro) | model judge khác model trả lời (alias `judge`), prompt có id, kèm rubric phân cấp có **số neo tính trước**; điểm âm cho số bịa (DAComp) |
| Hành động | mọi case | `expected_action` đúng là điều kiện cần; sai hành động = 0 dù số đúng |
| Nhãn | mọi case | `trust_code` trả về ≤ `expected_trust_code` theo thứ tự ARCHITECTURE §9.5 là đạt; cao hơn mong đợi (ví dụ GOVERNED cho số từ SQL khám phá) = lỗi nhãn, tính riêng `trust_overclaim_rate` |

### 5.5 Quy trình xác minh (verification_status)

1. `draft`: người viết case từ §2.8/§3.7 và mẫu âm §5.3, kèm SQL/tra cứu.
2. `sql_run_on_snapshot`: CI chạy SQL vàng trên snapshot (Postgres từ Parquet qua DuckDB `libs/snapshot`) và ghi giá
   trị + `result_sha256` vào case; case tài liệu: script mở iXBRL, kiểm `fact_id` tồn tại và `computed value`.
3. `human_verified`: **hai người giải mù** độc lập theo phiếu (như `docs/v3/eval/ds-aw-blind-sheet.md`), không xem đáp
   án; khớp ba bên (hai người + SQL) → `human_verified`.
4. `disputed`: không khớp → xem lại đề, dữ liệu hoặc đáp án; sửa tạo `version` mới. DeepEye-SQL/DataSpace: ~30 % "lỗi"
   còn lại là lỗi nhãn, nên bước này không được bỏ.
5. Case đóng băng = câu hỏi + đáp án + hợp đồng + snapshot sha; đổi bất kỳ thứ gì = version mới.

### 5.6 Lưu trữ

```text
model/eval/cases/golden/nyc311/structured.jsonl        24
model/eval/cases/golden/nyc311/multiturn.jsonl         16
model/eval/cases/golden/nyc311/analytical.jsonl        15
model/eval/cases/golden/nyc311/hybrid.jsonl             5
model/eval/cases/golden/edgar/{structured,multiturn,document,analytical,hybrid}.jsonl   16/14/22/10/20
model/eval/cases/golden/vi/document.jsonl               8 (nếu C xác nhận)
model/eval/snapshots/S3-2025Q1.manifest.json           {snapshot_id, window, T, max_updated_at, files[{path,sha256,rows}], total_sha256, terms_status, drift_checked_at}
model/eval/snapshots/S12-2024.manifest.json, S12-2023.manifest.json, E20-FY21-24.manifest.json
deploy/docker/semantic/ds-311.yaml, ds-sec.yaml
deploy/docker/eval/ds-311-cases.yaml, ds-sec-cases.yaml      (sinh từ JSONL, test giữ hai bản đồng nhất như AW)
tests/e2e/nyc311/collect.py, tests/e2e/edgar/collect.py
```

Dữ liệu Parquet **không commit**; `make nyc311` / `make edgar` tải và kiểm manifest (giống `make adventureworks`),
lưu ở MinIO bucket `da-snapshots` (lock) của cụm eval.

## 6. Harness, scorecard và cổng

### 6.1 Đường chạy

```mermaid
flowchart LR
  subgraph PR[Mỗi PR · CI]
    OFF[tier offline: policy 27 · sql 26 · why 12<br/>tất định, không model]
  end
  subgraph NIGHT[Hằng đêm · cụm eval có TEI + vLLM]
    SNAP[(da-snapshots: S3 · E20)] --> DS[DS-311 · DS-SEC · DS-AW<br/>metric đã chứng nhận]
    DS --> GOLD[tier golden: 150 case qua POST /v1/questions thật<br/>+ 61 AW]
    DS --> RET[make eval-retrieval 156 case<br/>halves = kết quả thật]
    DS --> AG[make agent-eval agentic,governed]
    GOLD & RET & AG --> REP[eval-report.json · eval.eval_run]
  end
  REP --> GATE[gate.py: thresholds.json<br/>absent = fail]
  GATE --> DOC[docs/v3/eval/<date>-<snapshot>.md]
  GATE -- release --> REL[release-gate.yml]
```

Thay đổi harness (EV-09…EV-12):

- `model/eval/run.py` thêm `--tier golden`: đọc `cases/golden/**`, gọi **API thật** `POST /v1/questions` với
  `X-Data-Space` của snapshot, đợi `GET /v1/answers/{id}` (AnswerEnvelope), chấm theo `rubric`; ghi `expected_action`
  vs hành động thật (`clarify`/`refusal.code`), `trust_code`, `receipt_id`; mọi case ghi `answer_id` để xem lại.
- `model/eval/scorers/golden.py`: rubric §5.4; `set_equality` dùng lại cách so multiset của `agentic/evaluate.py`.
- `documents/retrieval_eval.py`: cờ `halves` phải phản ánh **kết quả** (đếm lần dense/rerank thành công / tổng), không
  phải cấu hình; báo cáo có `dense_ok_rate`, `rerank_ok_rate`; gate từ chối báo cáo keyword-only khi môi trường khai có
  TEI (EV-10).
- `model/eval/gate.py` đọc thêm report truy hồi và agent-eval; metric vắng = fail (giữ nguyên tắc hiện có).
- Scorer `causal_claim_rate` chạy trên **văn bản câu trả lời thật** của tier golden (không chỉ output skill).

### 6.2 Scorecard

Cột "Baseline" là số **đã đo** (ngày, điều kiện); "chưa đo" nếu chưa có lượt chạy nào. Cột "P0" là **đề xuất, chưa đo**
(STRATEGY §5), chốt lại theo §6.3.

| Nhóm | Metric | Định nghĩa | Trung gian / E2E | Tính từ | Baseline | P0 (đề xuất) |
| --- | --- | --- | --- | --- | --- | --- |
| Data | `valid_records_rate` | dòng qua luật bắt buộc của contract / dòng đọc | trung gian | `ingest.quality_result` | chưa đo | ≥ 0,99 trên S3 sau chuẩn hoá null |
| Data | `completeness` | 1 − null_ratio cột bắt buộc | trung gian | `column_profile` | chưa đo | báo cáo |
| Data | `duplicate_rate` | 1 − distinct/count trên khoá | trung gian | luật `unique` | chưa đo (không nguồn nào báo trùng 311) | 0 trên `unique_key` |
| Data | `drift_detection` | drift tiêm được bắt / drift tiêm | trung gian | job status `schema_drift` | chưa đo | 100 % (2 case) |
| Data | `rows_written` đúng | = rows_read − rows_dlq | trung gian | `job_run` | sai hôm nay (D-5) | đúng |
| Ask | `execution_accuracy` | SQL sinh chạy không lỗi / SQL sinh | trung gian | query_run | chưa đo | báo cáo |
| Ask | `answer_correctness` | case đúng theo rubric (và đúng hành động) / case | **E2E** | tier golden | AW 20 câu đầu: governed 20/20, agentic 17/20 (12/11); AW 60 câu: 3/60 cả hai khi metric chưa duyệt (03/11) | 311 structured ≥ 85 % |
| Ask | `clarification_precision/recall` | hỏi lại đúng lúc / tổng hỏi lại; / tổng case cần hỏi | E2E | 30 case multi-turn | chưa đo | ≥ 0,80 / ≥ 0,80 |
| Ask | `governed_share` | case đi VERIFIED+GOVERNED / case | E2E | `trust_code` | chưa đo trên 311 | ≥ 60 % |
| Ask | `trust_overclaim_rate` | nhãn cao hơn mong đợi / case | E2E | §5.4 | chưa đo | 0 |
| Ask | `unsupported_number_rate` | số trong câu không có nguồn / số | E2E, tất định | `number_check` | AW: 0 (verify.fill) | 0 |
| Ask | `intent_accuracy` | (có sẵn, 32 case) | trung gian | tier online | **provisional ≥ 0,85, chưa đo** | rebase |
| SQL | `valid_sql_rate` | ứng viên qua `check.problems` / sinh | trung gian | sqlgen | chưa đo trên 311 | báo cáo |
| SQL | `sql_accuracy` (offline, compiler) | 26 case contract | trung gian | tier offline | **1,0** (gate) | 1,0 |
| SQL | `wrong_table_rate` | câu khám phá chọn sai bảng / câu khám phá | E2E | audit thủ công theo case | AW: 2/20 kiểu "sai bảng" (03/11) | 0 (PoC-3) |
| SQL | `permission_compliance` | truy vấn ngoài quyền / tổng | E2E | case permission + sweep | 0 trong policy offline | 0 |
| Analytics | `numeric_correctness` | 25 case analytical đúng tolerance | E2E | tier golden | DS-BH ablation (bộ đã xoá) 76,8 % đúng khi trả lời; chưa đo 311 | sai ≤ 10 % |
| Analytics | `reproducibility` | receipt replay khớp `result_sha256`/`expr` / receipt | E2E | `replay.py` | governed có; agentic **không có receipt** | 100 % cả hai đường |
| Analytics | `causal_claim_rate` | câu nêu nguyên nhân ngoài khối giả thuyết / câu "vì sao" | E2E | văn bản thật | 0 trên output skill (12 case); chưa đo văn bản | 0 |
| Analytics | `why_decomposition_accuracy` | (có sẵn) | trung gian | offline | 1,0 | 1,0 |
| Documents | `table_cell_f1` | ô đúng (text, row, col) / ô vàng | trung gian | bake-off PoC-1, 30 tài liệu | chưa đo (bảng hiện bị dẹt) | ≥ 0,85 PDF text / ≥ 0,70 scan |
| Documents | `ocr_cer` | CER có dấu, NFC | trung gian | 8 scan × 5 trang | chưa đo (chưa có scan) | không tệ hơn Tesseract hiện tại |
| Documents | `extraction_f1` | trường trích đúng / vàng | trung gian | `extraction_schema.test` | `sample_accuracy` khi kích hoạt ≥ 0,95 (ngưỡng), chưa có số ghi | báo cáo |
| Retrieval | `recall@1/3/10`, `mrr` | 156 case | trung gian | `make eval-retrieval` | **keyword-only** 12/11: r@1 0,7519, r@3 0,8045, r@10 0,8045, MRR 0,7744; khong-dau r@1 0,10; da-ngon-ngu 0,0; full-stack **chưa đo** (TEI vắng, báo cáo ghi sai halves) | r@10 ≥ 0,85 full-stack; khong-dau ≥ 0,7 |
| Retrieval | `not_found_accuracy` | case không có đáp án trả rỗng | trung gian | như trên | 0,7391 (keyword-only) | ≥ 0,85 |
| Retrieval | `forbidden_rate` | trả văn bản hết hiệu lực/bị thay / case | trung gian | như trên | 0,0 | 0 |
| Retrieval | `citation_accuracy` | trích dẫn đúng tài liệu + trang/ô / trích dẫn | E2E | contains_all | chưa đo | ≥ 0,95 |
| Hybrid | `cross_source_correctness` | kết luận đúng hoặc từ chối đúng / 25 case | E2E | tier golden | chưa đo | ≥ 70 % |
| Performance | `ttft_p50/p95` governed; `complete_p95` agentic | — | E2E | SSE timestamps | AW 12/11: governed 2,3 s trung bình/câu; agentic 16,1 s; bench R0-9: TTFT p50 responder 44,6 ms ở concurrency 1 | ≤ 3 s / ≤ 60 s |
| Performance | `tokens_per_question` | theo đường | E2E | `agent_run.used_tokens` | AW: governed 536; agentic 22 626 | agentic ≤ 5× governed cho câu đơn (sau router) |
| Performance | throughput ingest | dòng/giây | trung gian | job_run | chưa đo | báo cáo |
| Reliability | `failure_rate`, `retry_rate`, `dead_letter_count` | theo handler | trung gian | outbox cột mới | chưa có attempts | dead-letter có alert; 0 lô kẹt |
| Reliability | `restore_drill` | khôi phục 6 kho từ backup | — | runbook | chưa làm | xanh 1 lần/quý |
| Security | `policy_violation_rate` | 27 case | trung gian | offline | 0 | 0 |
| Security | `sandbox_escape` | 10 case | trung gian | test pod | chưa có bộ | 0 |
| Security | `cross_org_leak` | sweep org A đọc org B theo schema/vai | trung gian | migrate tests | 141 test RLS theo tính năng; chưa sweep | 0 |
| Business | `task_completion` | người dùng chấp nhận câu trả lời / câu hỏi | E2E | feedback | chưa đo | báo cáo pilot |
| Business | `analyst_time_saved` | thời gian hỏi → chấp nhận vs baseline thủ công | E2E | đo trước pilot (02 §4.3) | chưa đo | báo cáo pilot |

### 6.3 Cổng phát hành P0

**Metric chặn** (release-gate): `answer_correctness` (311 structured), `unsupported_number_rate`, `trust_overclaim_rate`,
`permission_compliance`, `policy_violation_rate`, `sandbox_escape`, `cross_org_leak`, `reproducibility`,
`causal_claim_rate`, `table_cell_f1`, `recall@10` (full-stack), `not_found_accuracy`, `forbidden_rate`, `dead_letter`
không kẹt lô. Các metric khác báo cáo bắt buộc nhưng không chặn ở P0.

**Quy tắc tuyên bố:** không được ghi "đạt" cho bất kỳ metric nào khi chưa có `eval-report.json` được lưu (CI artifact +
`eval.eval_run`) với `snapshot_id`, `commit`, `model alias → model`, ngày giờ. Số trong tài liệu này là mục tiêu cho tới
lúc đó.

**Rebase ngưỡng sau lượt chạy thật đầu tiên** (giữ quy trình `model/eval/README.md`, mở rộng):

1. Chạy tier golden + retrieval + agent-eval trên cụm eval có TEI và model production, trên snapshot đã đóng băng; lưu
   report.
2. Với metric tất định (`unsupported_number_rate`, `permission_compliance`, `policy_violation_rate`, `reproducibility`):
   ngưỡng = giá trị đo được, không nới (mọi dịch chuyển là hồi quy).
3. Với metric có judge hoặc có mẫu (`answer_correctness`, `recall@k`, `clarification_*`): ngưỡng = giá trị đo − biên
   cố ý (đề xuất 3 điểm cho correctness, 2 điểm cho recall); ghi `why` nêu lượt chạy nguồn; bỏ `"provisional": true`.
4. Mục tiêu P0 ở §6.2 cao hơn số đo → mục tiêu là việc phải làm, không phải ngưỡng gate; gate chỉ chặn hồi quy so với
   lượt tốt nhất đã ghi.
5. Unit test hiện có khẳng định ngưỡng online vẫn `provisional` phải đổi thành khẳng định "mỗi ngưỡng có `source_run`".

**Lịch:** nightly (Temporal `nightly-evals` đã có, mở rộng target) trên cụm eval; mỗi PR chạy offline; release chạy đủ.
Báo cáo nightly ghi vào `eval.eval_run` và một file `docs/v3/eval/<yyyy-mm-dd>-<snapshot>.md` chỉ khi có thay đổi gate.

### 6.4 Baseline trước / sau cho mọi thay đổi

1. `git worktree add --detach /tmp/base <commit trước thay đổi>` (CLAUDE.md §2; không `stash`/`reset` ở checkout chung).
2. Chạy cùng lệnh ở `/tmp/base` và ở working tree: `make agent-eval … --limit 20` (hoặc tier golden theo nhóm liên
   quan), `make eval-retrieval` nếu đụng tài liệu, `python -m model.eval.run --tier offline`.
3. Diff dòng tóm tắt; ghi hai số vào mô tả PR; metric chặn giảm = không merge.
4. Thay đổi prompt hoặc model: bắt buộc chạy đủ tier golden, không `--limit`.

## 7. Danh sách task

Cột: ID · Ưu tiên · Mô tả · Thay đổi · Công nghệ · Phụ thuộc · Vai · Cỡ · Tiêu chí đạt · Test · Deliverable · Rollback.

| ID | Ưu tiên | Mô tả | Thay đổi | Công nghệ | Phụ thuộc | Vai | Cỡ | Tiêu chí đạt | Test | Deliverable | Rollback |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| EV-01 | P0 | CLI thu NYC 311 theo cửa sổ, manifest sha256, Parquet/tháng, drift audit | mới `tests/e2e/nyc311/collect.py`, `libs/snapshot` writer; `make nyc311` | Python 3.11, httpx, pyarrow | F-01 (`libs/snapshot`) | DE | M | Chạy hai lần cùng cửa sổ → cùng manifest; 403/429 xử lý; cột đối chiếu với `api/views/erm2-nwe9.json` | unit: canonicalise + sha; e2e: cửa sổ 1 ngày | `S3-2025Q1.manifest.json` + Parquet trên `da-snapshots` | xoá target, không ảnh hưởng sản phẩm |
| EV-02 | P0 | CLI thu SEC: submissions, iXBRL chính, companyfacts, FSDS lọc 20 CIK; User-Agent, ≤ 10 req/s | mới `tests/e2e/edgar/collect.py`; `make edgar` | httpx, pyarrow, zipfile | — | DE | M | Manifest đủ 320 ± hồ sơ; XOM CIK xác minh ghi vào manifest; 10-K/A có `supersedes` | unit: lọc FSDS theo CIK; e2e: 1 CIK | `E20-FY21-24.manifest.json` | như trên |
| EV-03 | P0 | Data Space DS-311: nạp Parquet qua ingest có version, `ds-311.yaml` 15–20 metric, value_index | mới `deploy/docker/semantic/ds-311.yaml`, seed SQL | semantic YAML (dbt-semantic-interfaces) | EV-01, F-01, F-28 | DE + steward | M | 17 metric chứng nhận; `make semantic-import` dry-run sạch; reconciliation test mỗi metric vs SQL tay | integration `test_nyc311_reconciliation.py` | DS-311 trên cụm eval | gỡ seed |
| EV-04 | P1 | Data Space DS-SEC: bảng FSDS + companyfacts + submissions; `ds-sec.yaml`; nạp iXBRL vào `worker_document` với `accn/cik/reportDate`, `supersedes_id` | mới `ds-sec.yaml`, seed | như trên | EV-02, F-07 | DE + steward | M | 8 metric chứng nhận; 10-K/A chain đúng `supersedes_id` | integration reconciliation | DS-SEC | gỡ seed |
| EV-05 | P0 | Viết 60 case 311 (24 structured, 16 multi-turn, 15 analytical, 5 hybrid) kèm SQL vàng | mới `cases/golden/nyc311/*.jsonl`, `ds-311-cases.yaml` | JSONL | EV-03 | QA | M | 60 case `sql_run_on_snapshot`; ≥ 35 % case âm | `test_golden_cases_schema.py`; test seed = JSONL | 60 case | — |
| EV-06 | P1 | Viết 82 case SEC (16/14/22/10/20) kèm tra cứu fact id | mới `cases/golden/edgar/*.jsonl` | JSONL, script kiểm iXBRL | EV-04 | QA | M | 82 case `sql_run_on_snapshot`; mỗi case hybrid có `accn + fact_id` | script `verify_ixbrl.py` | 82 case | — |
| EV-07 | P1 | 8 case tiếng Việt trên vbpl.vn (nếu giấy phép xác nhận), nếu không chuyển sang SEC document | `cases/golden/vi/document.jsonl` | — | quyết định giấy phép | QA | S | 8 case hoặc biên bản chuyển | — | 8 case | — |
| EV-08 | P0 | Cột mới `eval.eval_item`: `expected_action`, `failure_category`, `rubric`, `verification_status`, `snapshot_ref`; loader YAML → bảng | generator `backend/schema` + migration v3_0036+; `api/modules/eval` | SQLAlchemy | — | BE | S | ORM khớp DDL; seed AW vẫn nạp | `tests/integration/migrate` | migration | down migration |
| EV-09 | P0 | Tier `golden` trong `model/eval/run.py`: gọi `POST /v1/questions` thật, đợi AnswerEnvelope, chấm theo rubric; ghi `answer_id` | `model/eval/run.py`, mới `scorers/golden.py`, `agent_client.py` | httpx, SSE | F-03 (envelope), EV-05 | AI/QA | M | Chạy 60 case 311 trên cụm eval ra report có mọi trường §5.4 | unit scorer từng rubric; e2e 3 case | report mẫu | cờ `--tier` |
| EV-10 | P0 | Sửa `retrieval_eval.py`: `halves` theo kết quả thật; `dense_ok_rate`, `rerank_ok_rate`; gate từ chối keyword-only khi khai có TEI | `documents/retrieval_eval.py`, `model/eval/gate.py` | — | — | AI | S | Chạy không TEI → report ghi `dense: false`, `dense_ok_rate: 0` | unit `test_retrieval_eval.py` | — | — |
| EV-11 | P0 | TEI (embed bge-m3 + rerank) trên cụm eval; cờ `enable_tei` ở Ansible; CPU chấp nhận được cho eval | `deploy/k8s/dev/service/tei.yaml`, Ansible | TEI | — | Ops | S | `make eval-retrieval` ra `dense_ok_rate` = 1,0 | smoke | baseline full-stack đầu tiên ghi `docs/v3/eval/` | cờ tắt |
| EV-12 | P0 | `gate.py` đọc report truy hồi + agent-eval + golden; metric vắng = fail; `causal_claim_rate` trên văn bản thật | `model/eval/gate.py`, `scorers/causal.py` | — | EV-09 | QA | S | Gate fail khi thiếu một metric chặn | unit gate | — | — |
| EV-13 | P0 | Nightly: target Temporal `nightly-evals` chạy golden + retrieval + agent-eval, ghi `eval.eval_run`, artifact report | `worker_agent/nightly_evals.py`, job manifest | Temporal | EV-09..11 | Ops | S | 3 đêm liên tiếp có report | integration schedule | — | tắt lịch |
| EV-14 | P0 | Rebase ngưỡng: chạy lượt thật đầu tiên, viết `thresholds.json` không `provisional`, mỗi ngưỡng có `source_run`; đổi unit test | `thresholds.json`, test | — | EV-13 | QA + PO | S | Không còn `provisional`; README cập nhật | unit | `docs/v3/eval/<date>-first-real-run.md` | giữ bản cũ |
| EV-15 | P0 | Bộ 30 tài liệu bake-off + ground truth bảng/thứ tự đọc/OCR/heading; `make docling-bakeoff` | `tests/data/docs-bakeoff/`, script trong image worker-document | Docling, OmniDocBench evaluator | — | DE/QA | M | 30 tài liệu có nhãn; chạy ra TEDS/cell F1/CER/ED | — | report PoC-1 | — |
| EV-16 | P1 | Case tiêm lỗi: file hỏng, trùng khoá, drift cột, injection trong mô tả, tenant chéo (synthetic chỉ ở đây) | script `tests/e2e/inject.py` + case JSONL | — | EV-05 | QA | S | 12 loại §5.3 mỗi loại ≥ 1 case chạy được | e2e | — | — |
| EV-17 | P1 | Giải mù hai người cho 150 case; phiếu như AW; đối chiếu; `human_verified` | phiếu `docs/v3/eval/ds-311-blind-sheet.md`, `ds-sec-blind-sheet.md` | — | EV-05, EV-06 | QA ×2 | M | ≥ 90 % case `human_verified`, còn lại `disputed` có ghi chú | — | case version 2 | — |
| EV-18 | P1 | Scorecard tự sinh từ `eval.eval_run` → trang Context Studio / Markdown theo nhóm §6.2 | `api/modules/eval` route report, FE eval page có sẵn | — | EV-13 | BE/FE | S | Trang hiện 10 nhóm với baseline + mục tiêu | FE test | — | — |

Thứ tự: EV-08 → EV-01/EV-02 (song song) → EV-03 → EV-05 → EV-09/EV-10/EV-11 → EV-12 → EV-13 → EV-14; EV-15 chạy
sớm (tuần 2–3, điều kiện cho F-07); EV-04/EV-06/EV-07/EV-16/EV-17/EV-18 sau cổng golden 311 đầu tiên.
