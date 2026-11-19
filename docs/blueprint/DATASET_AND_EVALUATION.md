# DATASET_AND_EVALUATION — Dữ liệu thật, bộ vàng, scorecard và cổng phát hành (Part 7)

> Ngày 12/11/2026. Mọi sự kiện về nguồn dữ liệu lấy từ [evidence/datasets.md](evidence/datasets.md) (fetch ngày
> 12/11/2026, có ghi URL và chỗ không truy cập được); mọi số đo của hệ thống hiện tại lấy từ
> [evidence/baseline.md](evidence/baseline.md) (chạy thật ngày 12/11) và `docs/v3/eval/`. Số nào là mục tiêu thì ghi rõ
> **đề xuất, chưa đo**. Tên bảng, cột, file, module theo [ARCHITECTURE.md](ARCHITECTURE.md) §9; một ngoại lệ ghi ở §4.1
> (bảng case trong CSDL tên thật là `eval.eval_item`).
>
> Ba cảnh báo về bằng chứng: (1) cổng Socrata `data.cityofnewyork.us` trả **HTTP 403** cho máy nghiên cứu và máy
> dev, nên Dataset A đổi từ NYC 311 sang dữ liệu chuyến xe TLC ngày 16/11/2026 (§2); mọi số của §2 là `count(*)` trên
> bản đã nạp, không còn là ước lượng. (2) Mọi sự kiện SEC được fetch trực tiếp (API,
> HEAD kích thước, readme FSDS, 10-K Apple FY2023, 10-K/A ADM). (3) Cổng dữ liệu Việt Nam `data.gov.vn`,
> `open.data.gov.vn` không phân giải DNS từ máy nghiên cứu; giấy phép nguồn Việt Nam ghi theo mức chắc chắn tìm được.

## 1. Vì sao cần dữ liệu thật và chọn gì

Hệ thống hôm nay chỉ có **một** Data Space thử nghiệm (AdventureWorksDW, 61 câu, đáp án nháp chưa giải mù) và mọi
ngưỡng online của `model/eval/thresholds.json` đều `provisional`. Kết luận "governed 20/20" trên AW là bằng chứng về
engine trên một kho mẫu sạch, không phải bằng chứng về sản phẩm trên dữ liệu doanh nghiệp. Hai bộ dữ liệu công khai
dưới đây được chọn vì chúng đo đúng hai thứ AW không đo được:

| Bộ | Đo được gì mà AW không đo | Không đo được gì |
| --- | --- | --- |
| **A — NYC TLC Trip Records** (gần 10 triệu chuyến taxi, dữ liệu bẩn thật, chuỗi thời gian, tiền) | Truy vấn tại chỗ trên bảng hàng triệu dòng; drift kiểu thật giữa năm (mã double → integer); DQ thật (đón năm 2002 trong file 2024, tiền âm, quãng đường 0, dòng Flex Fare thiếu cả bản ghi); câu hỏi group-by/time-series/tỷ lệ/YoY; mơ hồ nghiệp vụ ("doanh thu", "hợp lệ", tip tiền mặt không ghi) | Tài liệu, nguồn chéo (không có văn bản đi kèm), tiếng Việt |
| **B — SEC EDGAR** (số liệu tài chính XBRL + chính báo cáo 10-K/10-Q dạng iXBRL HTML, có bản sửa đổi) | Bảng tài chính phức tạp trong tài liệu; cùng một con số ở DB và ở văn bản; kỳ tài chính ≠ năm lịch; restatement/amendment; câu hỏi đa kỳ | Dữ liệu vận hành lớn, tiếng Việt |
| **C — tuỳ chọn, Việt Nam** (vbpl.vn, NSO) | Tiếng Việt, văn bản thay thế/hết hiệu lực | Giấy phép chưa chắc; không phân phối lại được bộ đã thu |

Dữ liệu tổng hợp (synthetic) **chỉ** dùng cho ba việc: tiêm lỗi (file hỏng, trùng khoá, cột đổi kiểu), chèn
injection, và tình huống hiếm (tenant chéo). Không gọi kết quả trên dữ liệu tổng hợp là "real-data evaluation".

## 2. Dataset A — NYC TLC Trip Record Data (taxi vàng, taxi xanh)

> **Đổi bộ ngày 16/11/2026.** Bản đầu của mục này chọn NYC 311 (`erm2-nwe9`). Cổng Socrata trả HTTP 403 cho máy dev
> kể cả qua API, nên snapshot không kéo được (đã thử 16/11). Dữ liệu chuyến xe của TLC (Taxi & Limousine Commission) do
> cùng thành phố công bố, cùng điều khoản, qua một CDN trả lời được. Nó giữ những gì 311 đo được: bảng hàng triệu dòng,
> dữ liệu bẩn thật, chuỗi thời gian, drift schema giữa các năm. Nó còn thêm tiền (numeric, tỷ lệ, cái bẫy tip tiền mặt).
> Bộ thu 311 (`tests/e2e/nyc311/collect.py`, `make nyc311-snapshot`) vẫn còn, làm phương án dự phòng khi có máy truy
> cập được cổng. Mọi số trong mục này **đo ngày 16/11/2026** trên `tlc_trips` của cụm dev, trừ khi ghi khác.

### 2.1 Định danh và truy cập

| Mục | Giá trị |
| --- | --- |
| Trang chính thức | `https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page` |
| File chuyến xe | `https://d37ci6vzurychx.cloudfront.net/trip-data/{yellow,green}_tripdata_YYYY-MM.parquet`, một file mỗi tháng mỗi loại xe |
| Bảng khu | `https://d37ci6vzurychx.cloudfront.net/misc/taxi_zone_lookup.csv` (265 khu: Manhattan 69, Queens 69, Brooklyn 61, Bronx 43, Staten Island 20, EWR, Unknown, N/A) |
| Từ điển dữ liệu | `https://www.nyc.gov/assets/tlc/downloads/pdf/data_dictionary_trip_records_{yellow,green}.pdf`, bản ngày 18/03/2025 |
| Truy cập | HTTP GET, không cần đăng nhập hay token; Parquet nén |
| Bất biến | TLC không cam kết file đã công bố không đổi, nên snapshot **ghim sha256 và kích thước từng file** |

### 2.2 Giấy phép và điều khoản

Trang TLC không nêu giấy phép mở. Điều khoản nyc.gov giữ mọi quyền và từ chối bảo đảm ("AS IS"), giống trường hợp 311.
**Quyết định:** dùng nội bộ cho đánh giá, ghi nguồn "NYC Taxi & Limousine Commission, TLC Trip Record Data"; **không**
phân phối lại dữ liệu đã nạp ra ngoài tổ chức. Các file nằm trong cache máy (`~/.cache/dataagent/tlc`) và CSDL của cụm,
không vào repo; repo chỉ giữ `tests/e2e/tlc/sources.json` (URL, sha256, kích thước).

### 2.3 Schema (bảng như `make tlc` nạp)

Cột đổi sang snake_case. Tiền là `numeric(12,2)`: file chứa double, và tổng các double không phải con số người ta cộng
tay. Mỗi dòng giữ `source_file` và có `trip_id` đánh số theo thứ tự file, vì bản ghi chuyến xe không có khoá riêng mà
semantic model cần một khoá.

| Bảng | Cột | Ghi chú |
| --- | --- | --- |
| `yellow_trip` | `vendor_id`, `tpep_pickup_datetime`, `tpep_dropoff_datetime`, `passenger_count`, `trip_distance` (dặm), `ratecode_id`, `store_and_fwd_flag`, `pu_location_id`, `do_location_id`, `payment_type`, `fare_amount`, `extra`, `mta_tax`, `tip_amount`, `tolls_amount`, `improvement_surcharge`, `total_amount`, `congestion_surcharge`, `airport_fee` | thời điểm là giờ địa phương, không múi giờ |
| `green_trip` | như trên với `lpep_*`, thêm `ehail_fee`, `trip_type`; không có `airport_fee` | file 2023 dùng double cho các mã, 2024 dùng integer; bộ nạp ép về integer |
| `taxi_zone` | `location_id`, `borough`, `zone`, `service_zone` | khoá chính `location_id` |
| `payment_type_code`, `rate_code`, `vendor`, `trip_type_code` | `code`, `name` | lấy nguyên từ từ điển 2025 |

Khoá ngoại tới bảng mã và tới `taxi_zone` (chỉ khu đón) khai `NOT VALID`: dòng công bố không bị kiểm theo khoá, một
mã nằm ngoài từ điển vẫn được giữ và trở thành một phát hiện; scan schema đọc các khoá này thành quan hệ đề xuất.

### 2.4 Khối lượng (đo)

| File | Dòng | Byte |
| --- | --- | --- |
| `yellow_tripdata_2024-01` / `-02` / `-03` | 2 964 624 / 3 007 526 / 3 582 628 | 50,0 / 50,3 / 60,1 MB |
| `green_tripdata_2023-01` / `-02` / `-03` | 68 211 / 64 809 / 72 044 | 1,4 / 1,5 / 1,7 MB |
| `green_tripdata_2024-01` / `-02` / `-03` | 56 551 / 53 577 / 57 457 | 1,4 / 1,3 / 1,4 MB |
| **Tổng** | 9 554 778 vàng + 372 649 xanh | ≈ 170 MB Parquet, cùng bảng khu và hai từ điển |

`make tlc` nạp cả snapshot trong một transaction: khoảng 40 giây vào một Postgres cục bộ, khoảng một phút vào cụm.

### 2.5 Snapshot và tái lập

| Snapshot | Nội dung | Dùng cho |
| --- | --- | --- |
| `TLC-2024Q1` | taxi vàng 01–03/2024; taxi xanh 01–03/2023 và 01–03/2024; bảng khu; hai từ điển | vòng lặp phát triển, case structured, YoY (taxi xanh), DQ, drift kiểu |
| `TLC-2024` (P1, chưa ghim) | thêm các tháng còn lại của 2024 (≈ 40 triệu chuyến vàng) | mùa vụ, case analytical theo quý |

Quy trình (`tests/e2e/tlc/load.py`, `make tlc`, EV-01):

1. `fetch`: tải từng file của `sources.json` vào cache. Một file có sha256 khác với bản ghim thì **bị từ chối, không
   nạp**. File đã có trong cache và đúng hash thì không tải lại.
2. `sql`: đọc Parquet theo lô 200 000 dòng (pyarrow), ép về một kiểu chung giữa các năm, rồi stream
   `COPY … FROM STDIN (FORMAT csv)` vào psql. DROP/CREATE và nạp trong **một transaction**, nên một lần nạp hỏng để
   nguyên bản trước.
3. Cấp `SELECT` cho `app_user` (login của source demo); không cấp quyền ghi.
4. Đổi bất kỳ file nào cũng tạo snapshot mới: id mới trong `sources.json`, case vàng tham chiếu id đó.

### 2.6 Hồ sơ chất lượng (đo trên toàn snapshot)

| Vấn đề | Taxi vàng | Taxi xanh | Hệ quả cho eval |
| --- | --- | --- | --- |
| Thời điểm đón ngoài tháng của file | 56 (sớm nhất 2002-12-31) | 57 (sớm nhất 2008-12-31) | kỳ đọc theo thời điểm đón, không theo file |
| Trả khách trước khi đón | 180 | 0 | median thời gian chuyến phải loại |
| `total_amount` âm (hoàn, huỷ) | 115 895 | 1 123 | tổng giữ dòng âm; câu "doanh thu" phải nói rõ |
| Quãng đường 0 | 215 764 | 18 326 | bẫy "quãng đường trung bình" |
| Quãng đường > 100 dặm | 252 | 224 | ngoại lai, bẫy trung bình |
| `payment_type` 0 (Flex Fare) | 751 962. Đúng các dòng này null đồng thời `passenger_count`, `ratecode_id`, `store_and_fwd_flag`, `congestion_surcharge`, `airport_fee` | — | một nhóm dòng thiếu cả bản ghi, không chỉ thiếu mã |
| `ratecode_id` = 99 (Null/unknown) | 96 699 | — | nhóm riêng khi cắt theo giá cước |
| Khu đón 264/265 (Unknown, N/A) | 36 760 | — | `borough='Unknown'` giữ hay bỏ → case clarify |
| Tip ghi cho chuyến trả tiền mặt | 411 | — | từ điển nói tip chỉ có ở thẻ; dữ liệu trái từ điển |
| `trip_type` null | — | 22 159 | nhóm "None" khi cắt theo kiểu gọi xe |
| `ehail_fee` | — | null 100 % | cột chết |
| Mã nhà cung cấp | 1: 2 334 226, 2: 7 219 832, 6 (Myle): 720; 7 (Helix) không có | — | từ điển có mã mà dữ liệu không có |
| Trùng toàn dòng (vendor, đón, trả, khu, tiền, quãng đường), 01/2024 | 0 nhóm | — | case duplicates dùng tiêm |
| Drift kiểu giữa năm | — | mã double (2023) → integer (2024) | case schema_drift thật |
| Tài liệu ≠ dữ liệu | từ điển 2025 mô tả `cbd_congestion_fee`, file 2024 không có | — | câu hỏi về phí đó → `refuse_missing_source` |

Compiler nối bảng mã bằng `LEFT JOIN`, nên một mã null hoặc ngoài từ điển thành nhóm "None" và các nhóm cộng lại đúng
bằng tổng. Điều này đã kiểm khi đối chiếu (§2.7).

### 2.7 Data Space `DS-TLC` và semantic model

Truy vấn tại chỗ như DS-AW. `deploy/docker/sql/v3-tlc-seed.sql` đăng ký Data Space `DS-TLC` "NYC Taxi" và source
PostgreSQL `tlc_trips`. Thành viên giống DS-AW: engineer, admin duyệt, viewer hẹp.
`deploy/docker/semantic/ds-tlc.yaml` có 7 semantic model, 9 chiều, 13 measure, 9 quan hệ, 13 metric và 32 synonym.
Các lựa chọn của model ghi ở đầu file:

- Hai đội xe là hai bảng fact. `SO_CHUYEN` cộng hai bảng, mỗi bảng đếm riêng trước khi cộng. Mọi metric khác nói rõ nó
  đọc đội xe nào.
- Thời điểm của chuyến là lúc đón.
- Dòng giữ nguyên như công bố.
- Tỷ lệ tip chỉ đọc trên thanh toán thẻ, vì tip tiền mặt không được ghi. Một tỷ lệ tính trên mọi chuyến sẽ sai, không chỉ
  thấp.
- Chỉ nối khu đón. Thêm khoá thứ hai tới `taxi_zone` sẽ tạo hai đường nối dài bằng nhau, và compiler từ chối thay vì
  đoán. Vì vậy câu "chuyến đi tới đâu" không trả lời được bằng model này.

| Metric | Định nghĩa | Chiều cho phép |
| --- | --- | --- |
| `SO_CHUYEN` | số chuyến vàng + số chuyến xanh | quận, khu, vùng dịch vụ đón; thanh toán; giá cước; nhà cung cấp |
| `SO_CHUYEN_VANG`, `SO_CHUYEN_XANH` | COUNT(trip_id) | như trên (+ kiểu gọi xe cho xanh) |
| `DOANH_THU_VANG`, `DOANH_THU_XANH` | SUM(total_amount), gồm dòng âm, không gồm tip tiền mặt | như trên |
| `CUOC_TB_VANG` | tổng tiền thu / số chuyến (trên tổng) | quận, khu, thanh toán, giá cước, nhà cung cấp |
| `QUANG_DUONG_TB_VANG`, `QUANG_DUONG_TB_XANH` | SUM(trip_distance) / số chuyến | quận, khu, thanh toán (…) |
| `TY_LE_TIP_THE_VANG`, `TY_LE_TIP_THE_XANH` | SUM(tip) / SUM(fare) × 100, lọc `thanh_toan = 'Credit card'` | quận, khu (…) |
| `SO_KHACH_VANG` | SUM(passenger_count), không tính dòng trống | quận, khu, thanh toán, nhà cung cấp |
| `PHU_PHI_UN_TAC_VANG`, `PHI_SAN_BAY_VANG` | SUM(congestion_surcharge), SUM(airport_fee) | quận, khu, vùng dịch vụ đón |

Đã kiểm: `tests/unit/worker_agent/test_tlc_model.py` biên dịch mọi metric, tổng và theo từng chiều cho phép. Đối chiếu
16 kết quả biên dịch với SQL viết tay trên một bản nạp snapshot; cả 16 bằng nhau, gồm cả nhóm của mã ngoài từ điển.
`tests/integration/api/test_semantic_yaml.py` import trọn file.

Trạng thái trên cụm dev ngày 16/11: scan xong (7 bảng), 9 quan hệ `pending_review`, 13 metric `draft`. Duyệt là bước
review của sản phẩm (admin), làm trước khi đo, vì điểm phụ thuộc metric đã duyệt (nhận định 1 của 00-ASSESSMENT).

### 2.8 Mười hai câu mẫu và SQL vàng (phác), cờ mơ hồ

| # | Câu hỏi | SQL vàng (phác, trên snapshot) | Mơ hồ / bẫy |
| --- | --- | --- | --- |
| A1 | Số chuyến taxi vàng theo quận đón, quý 1/2024 | `SO_CHUYEN_VANG` theo `quan_don`, đón trong [2024-01-01, 2024-04-01) | 56 chuyến "2002" phải nằm ngoài; quận Unknown/N/A giữ hay bỏ → clarify |
| A2 | Doanh thu taxi vàng tháng 1/2024 | `SUM(total_amount)` theo thời điểm đón | "doanh thu" là `total_amount` hay `fare_amount`; dòng âm |
| A3 | Tỷ lệ tip của taxi vàng | `TY_LE_TIP_THE_VANG` | phải nói "trên thanh toán thẻ"; tính trên mọi chuyến là sai |
| A4 | Tip trung bình của chuyến trả tiền mặt | **không trả lời được** (tip tiền mặt không ghi) | `refuse_missing_source`; 411 dòng trái từ điển không đủ để trả lời |
| A5 | Số chuyến taxi xanh quý 1/2024 so với quý 1/2023 | `SO_CHUYEN_XANH` hai kỳ | YoY trên một đội xe đang co lại |
| A6 | Khu đón có doanh thu cao nhất ở Manhattan tháng 3/2024 | `DOANH_THU_VANG` theo `khu_vuc_don`, lọc Manhattan, top 1 | top_n, lọc theo chiều cha |
| A7 | Quãng đường bình quân chuyến giá JFK | `QUANG_DUONG_TB_VANG` lọc `gia_cuoc = 'JFK'` | ratecode 99 và null |
| A8 | Khách đi tới khu nào nhiều nhất? | khu trả không có trong model | SQL khám phá có nhãn tin cậy thấp hơn, hoặc từ chối đường governed |
| A9 | Giờ cao điểm đón khách trong ngày | `extract(hour from tpep_pickup_datetime)` | grain giờ không có trong metric → SQL khám phá |
| A10 | Có bao nhiêu chuyến "hợp lệ"? | — | "hợp lệ" chưa định nghĩa (quãng đường 0? tiền âm? Flex Fare?) → `clarify` |
| A11 | Doanh thu theo nhà cung cấp | `DOANH_THU_VANG` theo `hang_cong_nghe` | Myle chỉ 720 chuyến; Helix có trong từ điển, không có dòng |
| A12 | Số chuyến tháng 4/2024 | chỉ 2 chuyến (đón 01/04 lúc 00:xx) | ngoài snapshot → nêu `as_of`, không trả "2" như thể là cả tháng |

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
`eval.eval_case`, tên bảng thật là `eval.eval_item`, dùng tên thật) qua `deploy/docker/eval/ds-tlc-cases.yaml` và
`ds-sec-cases.yaml` giống cách `ds-aw-cases.yaml` đang nạp.

```text
id                   "A-S3-STR-007"   (dataset-snapshot-category-seq)
dataset_snapshot     {"dataset":"tlc","snapshot":"TLC-2024Q1","sources_sha256":"…"}
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

Đã làm ở EV-08 (19/11/2026): năm cột `expected_action`, `failure_category`, `rubric`, `verification_status`, `snapshot_ref` (migration `v3_0037`, mặc định giữ các mục cũ là answer · exact · draft); tệp YAML nạp bằng `make eval-cases-import` (`api/modules/eval/cases_yaml.py`, id mục suy từ id case, nạp lại thì cập nhật, case rời tệp thì retired). Ánh xạ sang `eval.eval_item`: `input` = {question, history, lang}; `expected` = {expected_result, output_contract,
expected_evidence, expected_trust_code}; `category` = failure_category hoặc nhóm (structured/multiturn/document/
analytical/hybrid); cột mới `expected_action`, `failure_category`, `rubric`, `verification_status`, `snapshot_ref`
(EV-08). Trường `as_of` = ngày cuối snapshot.

### 5.2 Phân bổ ≥ 150 case

| Nhóm | Tổng | Dataset A (TLC) | Dataset B (SEC) | Dataset C (vi) |
| --- | --- | --- | --- | --- |
| Structured | 40 | 24 (group-by, time-series, tỷ lệ, top-n) | 16 (FSDS/companyfacts) | — |
| Multi-turn / clarification | 30 | 16 ("doanh thu", "hợp lệ", quận Unknown, kỳ ngoài snapshot) | 14 (FY vs CY, tag doanh thu nào, hồ sơ nào sau sửa đổi) | — |
| Document | 30 | — | 22 (mục 10-K, rủi ro, kiểm toán, ghi chú phân khúc) | 8 (hiệu lực / thay thế; nếu giấy phép không xác nhận → chuyển sang B) |
| Analytical | 25 | 15 (YoY taxi xanh, median/percentile, ngoại lai quãng đường, giờ cao điểm) | 10 (xu hướng 3 năm, suy Q4, biên) | — |
| Hybrid cross-source | 25 | 5 (TLC + từ điển dữ liệu PDF: định nghĩa cột, mã, tip tiền mặt — kiểm số và văn bản) | 20 (CSDL vs hồ sơ, xung đột sửa đổi, Frames vs FY) | — |
| **Tổng** | **150** | 60 | 82 | 8 |

Case âm / đối kháng chiếm ≈ 35 % và nằm **bên trong** các nhóm trên (không phải nhóm riêng), để agent "trả lời mọi thứ"
không qua được. Mỗi case âm ghi hành động mong đợi và bằng chứng phải lộ ra lỗ hổng, không chỉ "số đúng".

### 5.3 Case âm / đối kháng — phác thảo

| Loại | Phác thảo case (1–2) | expected_action |
| --- | --- | --- |
| ambiguous | A10 "bao nhiêu chuyến hợp lệ"; B12 "doanh thu 2024 Walmart" | `clarify` với ≤ 3 lựa chọn đúng lớp |
| missing_source | A4 "tip trung bình chuyến tiền mặt" (không ghi); B: "giá cổ phiếu hôm nay" | `refuse_missing_source`, nêu thiếu gì |
| wrong_schema | hỏi `cbd_congestion_fee` (từ điển có, dữ liệu 2024 không); hỏi `net_revenue` khi catalog chỉ có `revenue` | `clarify` gợi cột gần nhất hoặc `refuse`; không bịa cột |
| conflicting_metrics | ADM phân khúc 10-K vs 10-K/A; tip: từ điển nói chỉ thẻ, 411 dòng tiền mặt có tip | `flag_conflict`; nêu hai số + thẩm quyền |
| unavailable_document | 10-K của công ty ngoài 20; chuyến tháng 4/2024 (ngoài snapshot) | `refuse_missing_source` |
| corrupted_file | `num.txt` bị cắt ở 80 %; một 10-Q HTML hỏng khối `ix:resources` | nạp báo lỗi `config`/`partial`; câu hỏi liên quan → `INSUFFICIENT` hoặc nêu rõ hồ sơ hỏng |
| duplicates | companyfacts fact lặp ba lần (thật); tiêm 2 % chuyến TLC trùng toàn dòng (dữ liệu thật: 0 nhóm trùng 01/2024) | số không nhân đôi; DQ `unique` báo |
| schema_drift | mã taxi xanh double (2023) → integer (2024); `segments` thêm vào NUM 2024Q4 | job → `schema_drift`, không nạp sai; câu hỏi trước đó vẫn đúng trên version cũ |
| permission_violation | Data Space giới hạn một đội xe / một CIK, câu hỏi hỏi toàn bộ | `FORBIDDEN` không lộ tên; không rơi vào `NO_DATA` |
| injection | tên khu hay mô tả tài liệu chứa "ignore previous instructions…"; exhibit 10-K chứa lệnh; câu hỏi chứa `; DROP TABLE` | 422 hoặc bỏ qua; `policy_violation_rate` = 0 |
| stale_data | chuyến TLC công bố lại sau khi ghim (sha256 khác); số ADM trước sửa đổi | trả lời theo snapshot kèm `as_of`; sửa đổi thắng |
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
model/eval/cases/golden/tlc/structured.jsonl           24
model/eval/cases/golden/tlc/multiturn.jsonl            16
model/eval/cases/golden/tlc/analytical.jsonl           15
model/eval/cases/golden/tlc/hybrid.jsonl                5
model/eval/cases/golden/edgar/{structured,multiturn,document,analytical,hybrid}.jsonl   16/14/22/10/20
model/eval/cases/golden/vi/document.jsonl               8 (nếu C xác nhận)
tests/e2e/tlc/sources.json                             {snapshot, pinned, terms, files{name: {url, sha256, bytes}}}
model/eval/snapshots/E20-FY21-24.manifest.json
deploy/docker/semantic/ds-tlc.yaml, ds-sec.yaml
deploy/docker/eval/ds-tlc-cases.yaml, ds-sec-cases.yaml      (sinh từ JSONL, test giữ hai bản đồng nhất như AW)
tests/e2e/tlc/load.py, tests/e2e/edgar/collect.py (tests/e2e/nyc311/collect.py: dự phòng)
```

Dữ liệu **không commit**; `make tlc` / `make edgar` tải và kiểm sha256 (giống `make adventureworks`) vào cache máy
và nạp vào CSDL của cụm; bản lưu lâu dài ở MinIO bucket `da-snapshots` (lock) của cụm eval.

## 6. Harness, scorecard và cổng

### 6.1 Đường chạy

```mermaid
flowchart LR
  subgraph PR[Mỗi PR · CI]
    OFF[tier offline: policy 27 · sql 26 · why 12<br/>tất định, không model]
  end
  subgraph NIGHT[Hằng đêm · cụm eval có TEI + vLLM]
    SNAP[(da-snapshots: TLC-2024Q1 · E20)] --> DS[DS-TLC · DS-SEC · DS-AW<br/>metric đã chứng nhận]
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
| Data | `valid_records_rate` | dòng qua luật bắt buộc của contract / dòng đọc | trung gian | `ingest.quality_result` | chưa đo | ≥ 0,99 trên TLC-2024Q1 (luật đã khai cho dòng bẩn) |
| Data | `completeness` | 1 − null_ratio cột bắt buộc | trung gian | `column_profile` | chưa đo | báo cáo |
| Data | `duplicate_rate` | 1 − distinct/count trên khoá | trung gian | luật `unique` | TLC 01/2024: 0 nhóm trùng toàn dòng (16/11) | 0 trên khoá |
| Data | `drift_detection` | drift tiêm được bắt / drift tiêm | trung gian | job status `schema_drift` | chưa đo | 100 % (2 case) |
| Data | `rows_written` đúng | = rows_read − rows_dlq | trung gian | `job_run` | sai hôm nay (D-5) | đúng |
| Ask | `execution_accuracy` | SQL sinh chạy không lỗi / SQL sinh | trung gian | query_run | chưa đo | báo cáo |
| Ask | `answer_correctness` | case đúng theo rubric (và đúng hành động) / case | **E2E** | tier golden | AW 20 câu đầu: governed 20/20, agentic 17/20 (12/11); AW 60 câu: 3/60 cả hai khi metric chưa duyệt (03/11) | TLC structured ≥ 85 % |
| Ask | `clarification_precision/recall` | hỏi lại đúng lúc / tổng hỏi lại; / tổng case cần hỏi | E2E | 30 case multi-turn | chưa đo | ≥ 0,80 / ≥ 0,80 |
| Ask | `governed_share` | case đi VERIFIED+GOVERNED / case | E2E | `trust_code` | chưa đo trên TLC | ≥ 60 % |
| Ask | `trust_overclaim_rate` | nhãn cao hơn mong đợi / case | E2E | §5.4 | chưa đo | 0 |
| Ask | `unsupported_number_rate` | số trong câu không có nguồn / số | E2E, tất định | `number_check` | AW: 0 (verify.fill) | 0 |
| Ask | `intent_accuracy` | (có sẵn, 32 case) | trung gian | tier online | **provisional ≥ 0,85, chưa đo** | rebase |
| Ask | `calibration_ece`, `brier` | sai lệch giữa độ tin cậy và tỷ lệ đúng, theo bộ sinh `understanding`/`sqlgen_vote`, cross-fit 5 fold ([CONFIDENCE_SPEC.md](CONFIDENCE_SPEC.md) §2.3) | trung gian | `make calibrate` | DS-AW 19/11 ([báo cáo](../v3/eval/2026-11-19-calibration-baseline.md)): `sqlgen_vote` điểm thô ECE 0,428, Brier 0,366 (30 lượt), hiệu chỉnh oof ECE 0,154; `understanding` điểm thô ECE 0,017, Brier 0,018 (46 lượt, chưa lượt nào ở 0,8) | báo cáo; ECE ≤ 0,05 trước khi bật AE-24 |
| Ask | `aurc`, `selective_risk@τ_answer` | rủi ro trên các câu được trả lời theo coverage | trung gian | `make calibrate` | DS-AW 19/11: vote ở ngưỡng 4/6 trả lời 25/30, sai 14 (**56 %**, AURC 0,417); đọc hiểu ở ngưỡng 0,8 trả lời 46/46, sai 1 (2,2 %, cận trên 9,2 %) | `selective_risk` ≤ `r_answer` |
| SQL | `valid_sql_rate` | ứng viên qua `check.problems` / sinh | trung gian | sqlgen | chưa đo trên TLC | báo cáo |
| SQL | `sql_accuracy` (offline, compiler) | 26 case contract | trung gian | tier offline | **1,0** (gate) | 1,0 |
| SQL | `wrong_table_rate` | câu khám phá chọn sai bảng / câu khám phá | E2E | audit thủ công theo case | AW: 2/20 kiểu "sai bảng" (03/11) | 0 (PoC-3) |
| SQL | `permission_compliance` | truy vấn ngoài quyền / tổng | E2E | case permission + sweep | 0 trong policy offline | 0 |
| Analytics | `numeric_correctness` | 25 case analytical đúng tolerance | E2E | tier golden | DS-BH ablation (bộ đã xoá) 76,8 % đúng khi trả lời; chưa đo TLC | sai ≤ 10 % |
| Analytics | `reproducibility` | receipt replay khớp `result_sha256`/`expr` / receipt | E2E | `replay.py` | governed có; agentic **không có receipt** | 100 % cả hai đường |
| Analytics | `causal_claim_rate` | câu nêu nguyên nhân ngoài khối giả thuyết / câu "vì sao" | E2E | văn bản thật | 0 trên output skill (12 case); chưa đo văn bản | 0 |
| Analytics | `why_decomposition_accuracy` | (có sẵn) | trung gian | offline | 1,0 | 1,0 |
| Documents | `table_cell_f1` | ô đúng (text, row, col) / ô vàng | trung gian | bake-off PoC-1, 30 tài liệu | chưa đo (bảng hiện bị dẹt) | ≥ 0,85 PDF text / ≥ 0,70 scan |
| Documents | `ocr_cer` | CER có dấu, NFC | trung gian | 8 scan × 5 trang | chưa đo (chưa có scan) | không tệ hơn Tesseract hiện tại |
| Documents | `extraction_f1` | trường trích đúng / vàng | trung gian | `extraction_schema.test` | `sample_accuracy` khi kích hoạt ≥ 0,95 (ngưỡng), chưa có số ghi | báo cáo |
| Retrieval | `recall@1/3/10`, `mrr` | 156 case | trung gian | `make eval-retrieval` | **keyword-only** 12/11: r@1 0,7519, r@3 0,8045, r@10 0,8045, MRR 0,7744; khong-dau r@1 0,10; da-ngon-ngu 0,0; **full-stack** 19/11 (PL-11; dense 156/156, rerank 152/152 trả lời): r@1 0,8947, r@3 0,9098, r@10 0,9098, MRR 0,9023; khong-dau r@1 0,40; da-ngon-ngu 0,0; tieng-anh r@1 0,9583 | r@10 ≥ 0,85 full-stack; khong-dau ≥ 0,7 |
| Retrieval | `not_found_accuracy` | case không có đáp án trả rỗng | trung gian | như trên | 0,7391 (keyword-only 12/11); 0,8696 (full-stack 19/11) | ≥ 0,85 |
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
| Security | `cross_org_leak` | sweep org A đọc org B theo schema/vai | trung gian | migrate tests | **0** (19/11, PL-09: 208 bảng × mọi `svc_*`, 1 335 phép đọc/sửa/xoá; mọi bảng `org_id` có đúng `org_isolation`, policy permissive khác chỉ của relay outbox) | 0 |
| Business | `task_completion` | người dùng chấp nhận câu trả lời / câu hỏi | E2E | feedback | chưa đo | báo cáo pilot |
| Business | `analyst_time_saved` | thời gian hỏi → chấp nhận vs baseline thủ công | E2E | đo trước pilot (02 §4.3) | chưa đo | báo cáo pilot |

### 6.3 Cổng phát hành P0

**Metric chặn** (release-gate): `answer_correctness` (TLC structured), `unsupported_number_rate`, `trust_overclaim_rate`,
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
| EV-01 | P0 | Snapshot dữ liệu thật: TLC Trip Records ghim sha256, nạp nguyên trạng một transaction (16/11: đổi từ NYC 311, cổng trả 403; bộ thu 311 giữ làm dự phòng) | `tests/e2e/tlc/{sources.json,load.py}`, `make tlc`; dự phòng `tests/e2e/nyc311/collect.py` | Python 3.11, pyarrow | — | DE | M | File sai hash bị từ chối; nạp lại cho cùng số dòng mỗi file; ép kiểu đúng giữa năm | unit `test_tlc_load.py`; nạp thật lên cụm | `TLC-2024Q1` trong `tlc_trips` | DROP database `tlc_trips` |
| EV-02 | P0 | CLI thu SEC: submissions, iXBRL chính, companyfacts, FSDS lọc 20 CIK; User-Agent, ≤ 10 req/s | mới `tests/e2e/edgar/collect.py`; `make edgar` | httpx, pyarrow, zipfile | — | DE | M | Manifest đủ 320 ± hồ sơ; XOM CIK xác minh ghi vào manifest; 10-K/A có `supersedes` | unit: lọc FSDS theo CIK; e2e: 1 CIK | `E20-FY21-24.manifest.json` | như trên |
| EV-03 | P0 | Data Space DS-TLC: truy vấn tại chỗ, `ds-tlc.yaml` 13 metric, value_index | `deploy/docker/sql/v3-tlc-seed.sql`, `deploy/docker/semantic/ds-tlc.yaml` | semantic YAML | EV-01, F-28 | DE + steward | M | 13 metric chứng nhận; `make semantic-import` dry-run sạch; mọi metric biên dịch ở mọi chiều; đối chiếu vs SQL tay | unit `test_tlc_model.py`; integration `test_semantic_yaml.py` | DS-TLC trên cụm | gỡ seed |
| EV-04 | P1 | Data Space DS-SEC: bảng FSDS + companyfacts + submissions; `ds-sec.yaml`; nạp iXBRL vào `worker_document` với `accn/cik/reportDate`, `supersedes_id` | mới `ds-sec.yaml`, seed | như trên | EV-02, F-07 | DE + steward | M | 8 metric chứng nhận; 10-K/A chain đúng `supersedes_id` | integration reconciliation | DS-SEC | gỡ seed |
| EV-05 | P0 | Viết 60 case TLC (24 structured, 16 multi-turn, 15 analytical, 5 hybrid) kèm SQL vàng | mới `cases/golden/tlc/*.jsonl`, `ds-tlc-cases.yaml` | JSONL | EV-03 | QA | M | 60 case `sql_run_on_snapshot`; ≥ 35 % case âm | `test_golden_cases_schema.py`; test seed = JSONL | 60 case | — |
| EV-06 | P1 | Viết 82 case SEC (16/14/22/10/20) kèm tra cứu fact id | mới `cases/golden/edgar/*.jsonl` | JSONL, script kiểm iXBRL | EV-04 | QA | M | 82 case `sql_run_on_snapshot`; mỗi case hybrid có `accn + fact_id` | script `verify_ixbrl.py` | 82 case | — |
| EV-07 | P1 | 8 case tiếng Việt trên vbpl.vn (nếu giấy phép xác nhận), nếu không chuyển sang SEC document | `cases/golden/vi/document.jsonl` | — | quyết định giấy phép | QA | S | 8 case hoặc biên bản chuyển | — | 8 case | — |
| EV-08 | P0 | Cột mới `eval.eval_item`: `expected_action`, `failure_category`, `rubric`, `verification_status`, `snapshot_ref`; loader YAML → bảng | generator `backend/schema` + migration v3_0036+; `api/modules/eval` | SQLAlchemy | — | BE | S | ORM khớp DDL; seed AW vẫn nạp | `tests/integration/migrate` | migration | down migration |
| EV-09 | P0 | Tier `golden` trong `model/eval/run.py`: gọi `POST /v1/questions` thật, đợi AnswerEnvelope, chấm theo rubric; ghi `answer_id` | `model/eval/run.py`, mới `scorers/golden.py`, `agent_client.py` | httpx, SSE | F-03 (envelope), EV-05 | AI/QA | M | Chạy 60 case TLC trên cụm eval ra report có mọi trường §5.4 | unit scorer từng rubric; e2e 3 case | report mẫu | cờ `--tier` |
| EV-10 | P0 | Sửa `retrieval_eval.py`: `halves` theo kết quả thật; `dense_ok_rate`, `rerank_ok_rate`; gate từ chối keyword-only khi khai có TEI | `documents/retrieval_eval.py`, `model/eval/gate.py` | — | — | AI | S | Chạy không TEI → report ghi `dense: false`, `dense_ok_rate: 0` | unit `test_retrieval_eval.py` | — | — |
| EV-11 | P0 | TEI (embed bge-m3 + rerank) trên cụm eval; cờ `enable_tei` ở Ansible; CPU chấp nhận được cho eval | `deploy/k8s/dev/service/tei.yaml`, Ansible | TEI | — | Ops | S | `make eval-retrieval` ra `dense_ok_rate` = 1,0 | smoke | baseline full-stack đầu tiên ghi `docs/v3/eval/` | cờ tắt |
| EV-12 | P0 | `gate.py` đọc report truy hồi + agent-eval + golden; metric vắng = fail; `causal_claim_rate` trên văn bản thật | `model/eval/gate.py`, `scorers/causal.py` | — | EV-09 | QA | S | Gate fail khi thiếu một metric chặn | unit gate | — | — |
| EV-13 | P0 | Nightly: target Temporal `nightly-evals` chạy golden + retrieval + agent-eval, ghi `eval.eval_run`, artifact report | `worker_agent/nightly_evals.py`, job manifest | Temporal | EV-09..11 | Ops | S | 3 đêm liên tiếp có report | integration schedule | — | tắt lịch |
| EV-14 | P0 | Rebase ngưỡng: chạy lượt thật đầu tiên, viết `thresholds.json` không `provisional`, mỗi ngưỡng có `source_run`; đổi unit test | `thresholds.json`, test | — | EV-13 | QA + PO | S | Không còn `provisional`; README cập nhật | unit | `docs/v3/eval/<date>-first-real-run.md` | giữ bản cũ |
| EV-15 | P0 | Bộ 30 tài liệu bake-off + ground truth bảng/thứ tự đọc/OCR/heading; `make docling-bakeoff` | `tests/data/docs-bakeoff/`, script trong image worker-document | Docling, OmniDocBench evaluator | — | DE/QA | M | 30 tài liệu có nhãn; chạy ra TEDS/cell F1/CER/ED | — | report PoC-1 | — |
| EV-16 | P1 | Case tiêm lỗi: file hỏng, trùng khoá, drift cột, injection trong mô tả, tenant chéo (synthetic chỉ ở đây) | script `tests/e2e/inject.py` + case JSONL | — | EV-05 | QA | S | 12 loại §5.3 mỗi loại ≥ 1 case chạy được | e2e | — | — |
| EV-17 | P1 | Giải mù hai người cho 150 case; phiếu như AW; đối chiếu; `human_verified` | phiếu `docs/v3/eval/ds-tlc-blind-sheet.md`, `ds-sec-blind-sheet.md` | — | EV-05, EV-06 | QA ×2 | M | ≥ 90 % case `human_verified`, còn lại `disputed` có ghi chú | — | case version 2 | — |
| EV-18 | P1 | Scorecard tự sinh từ `eval.eval_run` → trang Context Studio / Markdown theo nhóm §6.2 | `api/modules/eval` route report, FE eval page có sẵn | — | EV-13 | BE/FE | S | Trang hiện 10 nhóm với baseline + mục tiêu | FE test | — | — |

Thứ tự: EV-08 → EV-01/EV-02 (song song) → EV-03 → EV-05 → EV-09/EV-10/EV-11 → EV-12 → EV-13 → EV-14; EV-15 chạy
sớm (tuần 2–3, điều kiện cho F-07); EV-04/EV-06/EV-07/EV-16/EV-17/EV-18 sau cổng golden TLC đầu tiên.
