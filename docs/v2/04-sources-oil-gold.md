# 04. Nghiên cứu dữ liệu giá dầu và giá vàng

> Khảo sát ngày 27/09/2026. Mọi dòng "V" đã được tải thật từ máy chủ `10.10.55.101` (nơi connector
> chạy), không phải từ trình duyệt. File seed cho Bulk Import:
> [data/sources-oil-gold.yaml](data/sources-oil-gold.yaml). Nguồn tra cứu ở cuối file.
>
> Trạng thái: **V** tải được và đã đọc nội dung · **S** endpoint tồn tại nhưng cần key/đăng nhập ·
> **X** máy chủ bị chặn (403/bot wall) · **U** chưa thử.

## 1. Mười phát hiện ảnh hưởng thiết kế

1. **EIA công bố giá spot theo tuần, không theo ngày.** File `RBRTEd.xls` ghi "Release Date
   9/23/2026, Next Release Date 9/30/2026", dữ liệu mới nhất 22/09. Câu "giá Brent hôm nay" không
   trả lời được từ EIA; độ trễ tối đa ~8 ngày. SLA độ tươi của nguồn này là 7 ngày, không phải 1.
2. **EIA ngừng công bố giá futures NYMEX từ 05/04/2024** (`RCLC1d.xls` dừng ở 86,91 USD ngày
   05/04/2024; trang EIA ghi "Futures prices after April 5, 2024, are not available"). Không có nguồn
   futures miễn phí, sạch licence. Một nguồn "ngừng cập nhật" phải được phát hiện là vi phạm SLA,
   không được hiểu là "giá không đổi".
3. **Spot ≠ futures là chuyện có thật và lớn trong 2026.** Ngày 15/09/2026 EIA ghi Brent spot
   130,80 USD trong khi giá futures Brent được báo khoảng 107,46 USD. Đây chính là bước 12 của kịch
   bản nghiệm thu ("người xác định spot ≠ futures"), có dữ liệu thật để dựng.
4. **Cùng "Brent tháng 8/2026", ba nguồn ba số**: EIA spot trung bình 91,08; World Bank Pink Sheet
   90,9; IMF PCPS 88,00. WTI thì EIA 83,90 và IMF 83,74 gần như trùng. Chênh lệch nằm ở Brent vật
   chất so với Brent tham chiếu, là `DEFINITION_DIFFERENCE`, không phải lỗi.
5. **Không có giá vàng thế giới theo ngày vừa miễn phí vừa sạch licence.** FRED xoá toàn bộ chuỗi
   LBMA ngày 31/01/2022; IBA yêu cầu licence cho cả dùng để định giá lẫn phân phối lại; WGC từ
   18/03/2025 chỉ còn cung cấp giới hạn dữ liệu LBMA theo điều khoản ICE. Nguồn sạch nhất là theo
   **tháng** (IMF PCPS `PGOLD`, World Bank Pink Sheet).
6. **Drift ngữ nghĩa thật trong dữ liệu Việt Nam**: từ 16/06/2026 xăng E10RON95-III thay RON95-III
   trong điều hành giá (E10 bắt buộc từ 01/06/2026). Nối hai chuỗi thành một là sai định nghĩa.
7. **Chu kỳ điều hành giá xăng không cố định**: bình thường thứ Năm 15h00 (ví dụ Quyết định
   2398/QĐ-BCT ngày 24/9/2026), nhưng kỳ 07/3/2026 rơi vào thứ Bảy theo Nghị quyết 36/NQ-CP với cửa
   sổ bình quân chỉ 05/3–06/3. Tần suất phải là "theo kỳ điều hành", không phải "W".
8. **Chênh lệch vàng trong nước – thế giới phụ thuộc cách quy đổi**: với vàng thế giới 4.298,01
   USD/oz (26/9) và SJC 144,4 triệu/lượng, chênh lệch là 8,8 triệu nếu dùng tỷ giá bán Vietcombank
   (26.170) và 10,9 triệu nếu dùng tỷ giá mua (25.760). Báo chí đưa "9" hoặc "10 triệu" tuỳ nơi.
9. **Ngưỡng cảnh báo phải có cooldown**: trong 3 năm (22/09/2023–22/09/2026), Brent spot có 32 đợt
   giảm ≥5% trong 5 phiên (gộp các ngày cách nhau ≤7 ngày); riêng 2026 có 8 đợt. Có 28 ngày giảm
   ≥10%/5 phiên. Không có cơ chế gộp/cooldown thì UC12 sinh bão alert.
10. **Nguồn hay đổi địa chỉ hoặc chặn bot**: URL file Pink Sheet đổi mỗi bản (URL cũ vẫn trả 200 với
    dữ liệu tháng 12/2024); OPEC (basket, MOMR), SJC, Stooq trả 403 hoặc bài toán JavaScript;
    Vietcombank ghi "Only one request every 5 minutes!".

## 2. Định danh ngữ nghĩa (semantic identity)

"Giá dầu" và "giá vàng" không phải một con số. Mỗi entity có nhiều benchmark, và series phải mang
đủ các chiều sau để không bị gộp sai.

### 2.1 Dầu

| Benchmark | Bản chất | Đơn vị | Mốc thời gian | Nguồn miễn phí | Licence |
| --- | --- | --- | --- | --- | --- |
| Brent spot FOB (EIA `RBRTE`) | Giá vật chất, đóng cửa ngày | USD/thùng | Ngày giao dịch; công bố thứ Tư hằng tuần | EIA xls/API | Dữ liệu chính phủ Mỹ |
| ICE Brent futures (tháng gần) | Hợp đồng tài chính | USD/thùng | Settlement ~19h30 London | Không có nguồn sạch | ICE licence |
| Dated Brent (Platts) | Đánh giá vật chất | USD/thùng | Chốt London | Không | S&P Global licence |
| WTI spot Cushing (EIA `RWTC`) | Giá vật chất | USD/thùng | Như Brent spot | EIA | Công khai |
| NYMEX WTI futures (`CL`) | Hợp đồng | USD/thùng | Settlement 14h30 New York | EIA đã ngừng từ 05/04/2024 | CME licence |
| Dubai, Brent, WTI trung bình tháng | Trung bình tháng | USD/thùng | Tháng | IMF PCPS, World Bank | Ghi nguồn |
| OPEC Reference Basket | Rổ 13 loại dầu | USD/thùng | Ngày | opec.org (403 từ máy chủ) | Điều khoản OPEC |
| Thành phẩm Singapore (MOPS/Platts) | Giá xăng, dầu diesel thế giới dùng cho điều hành giá VN | USD/thùng, USD/tấn | Bình quân kỳ điều hành | Chỉ gián tiếp qua thông cáo Bộ Công Thương | Platts licence |
| Giá bán lẻ VN (E5RON92, E10RON95-III, diesel 0,05S, mazut) | Giá trần điều hành | VND/lít, VND/kg | Kỳ điều hành (thường thứ Năm 15h00) | moit.gov.vn, petrolimex.com.vn | Công khai |

Quy tắc: `EIA:RBRTE:D` và "ICE Brent" là **hai entity khác nhau** (`BRENT_SPOT`, `BRENT_FUTURES_M1`)
có quan hệ `SAME_UNDERLYING`. So sánh chúng là so sánh có chủ đích (basis), không phải kiểm tra
trùng khớp.

### 2.2 Vàng

| Benchmark | Bản chất | Đơn vị | Mốc thời gian | Nguồn miễn phí | Licence |
| --- | --- | --- | --- | --- | --- |
| LBMA Gold Price AM/PM | Phiên đấu giá do IBA quản trị | USD/troy oz | 10h30 / 15h00 London | Không (FRED xoá 2022) | IBA licence, 8.000 USD/năm cho tổ chức phi tài chính (2026) |
| Spot XAU/USD | OTC, liên tục | USD/troy oz | Mọi lúc | Không có nguồn sạch (Stooq chặn bot) | Nhà cung cấp dữ liệu |
| COMEX GC futures | Hợp đồng | USD/troy oz | Settlement New York | Không | CME licence |
| Shanghai Gold Benchmark | Đấu giá SGE | CNY/gram | AM/PM Thượng Hải | en.sge.com.cn (render JS) | Chưa rõ |
| Trung bình tháng | | USD/troy oz | Tháng | IMF PCPS `PGOLD`, World Bank | Ghi nguồn |
| Vàng miếng SJC, vàng nhẫn 9999 | Giá niêm yết mua/bán | triệu VND/lượng | Nhiều lần/ngày | sjc.com.vn (403), PNJ/DOJI (render JS) | Chưa rõ |

Quy đổi: 1 lượng = 37,5 g = 1,20565 troy oz. Giá vàng trong nước luôn có hai phía (mua, bán); một
"giá vàng SJC" không ghi phía là thiếu chiều.

Nghị định 232/2025/NĐ-CP (hiệu lực 10/10/2025) bỏ độc quyền sản xuất vàng miếng, yêu cầu tổ chức
sản xuất/kinh doanh vàng miếng niêm yết công khai giá mua bán. Hệ quả cho dữ liệu: từ cuối 2025 sẽ
có nhiều thương hiệu vàng miếng, "vàng miếng" không còn đồng nghĩa với "SJC".

## 3. Danh mục nguồn đã kiểm tra

| Nguồn | Dữ liệu | Tần suất dữ liệu / công bố | Trạng thái | Connector | Ghi chú cấu hình |
| --- | --- | --- | --- | --- | --- |
| EIA `RBRTEd.xls`, `RWTCd.xls` | Brent, WTI spot | D / thứ Tư hằng tuần | V | http_file | Sheet `Data 1`, dòng 3 là header, cột A là ngày Excel; sheet `Contents` có `Release Date` và `Next Release Date` → dùng làm `publication_time` |
| EIA `WCESTUS1w.xls` | Tồn kho dầu thô Mỹ trừ SPR | W (tuần kết thúc thứ Sáu) / thứ Tư | V | http_file | Đơn vị nghìn thùng |
| EIA `EER_EPMRU_PF4_Y35NY_DPGd.xls` | Xăng NY Harbor spot | D / thứ Tư | V | http_file | USD/gallon; ví dụ cho đơn vị khác |
| EIA `RCLC1d.xls` | WTI futures tháng 1 | Dừng 05/04/2024 | V (đã chết) | | Dùng làm test "nguồn ngừng cập nhật" |
| EIA API v2 | Cùng dữ liệu | | S (403 không key) | rest_api | Key miễn phí, lưu OpenBao |
| EIA WPSR | Báo cáo tuần (văn bản) | W | V | web_crawl | Nguồn claim/event |
| World Bank Pink Sheet | Brent, WTI, Dubai, vàng, bạc, gạo VN… | M / ~ngày làm việc thứ 2 của tháng | V | web_crawl + theo link | Bản 02/09/2026 có đến 2026M08. Sheet `Monthly Prices`, dòng 5 tên cột, dòng 6 đơn vị, kỳ dạng `2026M08`, thiếu ghi `…` |
| IMF PCPS (SDMX 2.1) | `POILBRE`, `POILWTI`, `POILDUB`, `PGOLD`, `PSILVER` | M / cập nhật 08/09/2026 cho tháng 8 | V | sdmx | `https://api.imf.org/external/sdmx/2.1/data/PCPS/G001.PGOLD.USD.M`; khoá `COUNTRY.INDICATOR.DATA_TRANSFORMATION.FREQUENCY`; kỳ dạng `2026-M08`; licence "© IMF, All Rights Reserved", đọc điều khoản trước production. Trang `external-data.ashx` cũ trả 403 |
| JODI Oil World | Sản xuất, tồn kho, nhu cầu theo nước | M | V | http_file | `world_primary_csv.zip` 23 MB; cột `REF_AREA, TIME_PERIOD, ENERGY_PRODUCT, FLOW_BREAKDOWN, UNIT_MEASURE, OBS_VALUE, ASSESSMENT_CODE`; giá trị thiếu `-`, bảo mật `x`; đơn vị `KBBL/KBD/KL/CONVBBL` |
| CFTC Commitments of Traders | Vị thế WTI, Brent, vàng COMEX | W (thứ Ba) / thứ Sáu | V | http_file | `dea/newcot/f_disagg.txt` (tuần hiện tại), `files/dea/history/fut_disagg_txt_2026.zip`; dữ liệu 22/09/2026 đã có |
| GPR daily (Caldara & Iacoviello) | Chỉ số rủi ro địa chính trị | D | V | http_file | `data_gpr_daily_recent.xls`, cột `DAY, GPRD, GPRD_ACT, GPRD_THREAT…`, mới nhất 21/09/2026; chỉ số nghiên cứu, ghi nguồn bài báo AER 2022 |
| FRED | `DFII10`, `DTWEXBGS`, `VIXCLS`, `DCOILBRENTEU` | D | S | rest_api | `fredgraph.csv` timeout từ máy chủ; API cần key. `DCOILBRENTEU` chính là dữ liệu EIA, không được coi là nguồn xác nhận thứ hai |
| SPDR GLD holdings | Lượng vàng ETF nắm giữ | D | V (PDF) | http_file | URL `GLD_US_archive_EN.csv` trả PDF 120 trang, phải trích bảng từ PDF |
| WGC Goldhub | Cầu vàng, NHTW, ETF | M/Q | S (cần đăng nhập) | tải tay | Dữ liệu giá chịu điều khoản ICE |
| Vietcombank XML | Tỷ giá | Nhiều lần/ngày | V | rest_api | Số kiểu Mỹ `25,760.00`; giới hạn 1 request/5 phút; `DateTime` dạng `9/27/2026 11:40:41 PM` |
| Bộ Công Thương (moit.gov.vn) | Giá thế giới bình quân kỳ theo sản phẩm, quỹ bình ổn, giá bán lẻ | Theo kỳ điều hành | V | web_crawl | Bài "Thông tin về việc điều hành giá xăng dầu ngày …"; số kiểu Việt (`109,730 USD/thùng`, `25.226 đồng/lít`); có số quyết định |
| Petrolimex | Giá bán lẻ vùng 1/vùng 2 | Theo kỳ | V | web_crawl | Đối chiếu với thông cáo Bộ Công Thương |
| SJC | Vàng miếng, nhẫn | Nhiều lần/ngày | X | web_crawl_headless | Trang chủ, `PriceService.ashx`, `xml/tygiavang.xml` đều 403 |
| PNJ, DOJI | Vàng các thương hiệu | Nhiều lần/ngày | V (khung HTML), giá render JS | web_crawl_headless | Không có giá trong HTML tĩnh |
| OPEC basket, MOMR | ORB ngày, báo cáo tháng | D / M | X | headless hoặc tải tay | 403 |
| Stooq XAUUSD | Giá vàng ngày | D | X | | Bài toán proof-of-work JavaScript; điều khoản không rõ. Không dùng |
| Bundesbank BBEX3 | | | X | | Mã chuỗi `BBEX3.D.XAU.USD.EA.AC.C04` không tồn tại; API timeout |

## 4. Bốn trục thời gian theo nguồn

Kiến trúc yêu cầu `event_time`, `publication_time`, `available_at`, `system_time` (00, bất biến 8).
Với từng nguồn, `available_at` lấy từ đâu:

| Nguồn | event_time | publication_time | available_at (dùng cho point-in-time) |
| --- | --- | --- | --- |
| EIA spot | ngày giao dịch | `Release Date` trong sheet Contents (thứ Tư) | = publication_time; không phải ngày giao dịch |
| EIA tồn kho | tuần kết thúc thứ Sáu | thứ Tư kế tiếp 10h30 ET | = publication_time |
| Pink Sheet | tháng | "Updated on …" trong file | = publication_time |
| IMF PCPS | tháng | `PUBLICATION_DATE` trong message SDMX | = publication_time |
| CFTC COT | thứ Ba | thứ Sáu 15h30 ET | = publication_time |
| Bộ Công Thương | cửa sổ bình quân (ví dụ 17/9–24/9) + thời điểm hiệu lực | thời gian đăng bài | thời điểm hiệu lực (15h00) cho giá bán; ngày đăng cho giá thế giới bình quân |
| Vietcombank, SJC | thời điểm niêm yết | = event_time | thời điểm hệ thống crawl (system_time) nếu nguồn không ghi giờ |

Hệ quả cho forecast (UC11): feature "Brent tuần trước" tại as-of thứ Hai không được dùng giá thứ
Sáu nếu EIA chỉ công bố vào thứ Tư. Dùng `available_at`, không dùng `event_time`.

## 5. Bối cảnh thị trường 2026 (dùng cho golden set và demo)

Dầu (theo Wikipedia "2026–2028 world oil market chronology", đối chiếu với EIA spot):

| Thời điểm | Sự kiện | Mức giá |
| --- | --- | --- |
| 07/01/2026 | Đáy năm của Brent spot | 61,08 (EIA) |
| 03–04/03 | Chiến sự Iran leo thang | Brent ~81,5 |
| 20/03 | Iraq tuyên bố bất khả kháng | Brent 112,19 |
| 07/04 | Đỉnh năm của Brent spot | 138,21 (EIA) |
| 08/04 | Công bố ngừng bắn; giảm trong ngày lớn nhất từ 4/2020 | Brent 94,75 |
| 17/04 | Iran tuyên bố mở lại eo biển | Brent 90,38 |
| 29–30/04 | Đe doạ phong toả | Brent 118–126 |
| 24/06 | Tàu chở dầu qua eo biển lại | Brent 73,74 |
| 23/07 | Tấn công tàu ở Biển Đỏ | Brent vượt 100 |
| 20/08 | Mỹ công bố trừng phạt; UAE dừng thương mại với Iran | Brent 93,73 |
| 09/09 | Giao tranh Mỹ–Iran leo thang | Brent 101,21 (futures) |
| 15/09 | | Brent spot EIA 130,80 |
| 22/09 | | Brent spot 114,89; spread Brent–WTI 18,48 |

Trung bình tháng Brent spot (EIA): 1/2026 66,60 · 3/2026 103,13 · 4/2026 117,29 · 6/2026 85,40 ·
8/2026 91,08 · 9/2026 (tới 22/9) 112,96. Spread Brent–WTI trên 18 USD, trong khi mức thường là vài
USD; luật DQ "bước nhảy bất thường" phải biết đây là chế độ thị trường, không phải lỗi dữ liệu.

Vàng: đỉnh LBMA PM 5.405 USD/oz tháng 1/2026 (intraday ~5.590 ngày 28/01); NHTW mua ròng 289 tấn
quý 2/2026 (cao nhất cho một quý 2); giá tháng 8/2026 theo IMF 4.418,54, theo World Bank 4.411;
cuối tháng 9 khoảng 4.300. SJC 144,4 triệu/lượng ngày 26/9, chênh mua bán 3 triệu.

Xăng dầu Việt Nam kỳ 24/9/2026 (Quyết định 2398/QĐ-BCT): giá thế giới bình quân 17/9–24/9: RON92
140,834; RON95 147,204; diesel 0,05S 173,328 USD/thùng; mazut 657,618 USD/tấn. Giá bán tối đa:
E5RON92 26.397; E10RON95-III 27.087; diesel 30.497 đồng/lít; mazut 19.472 đồng/kg. Quỹ bình ổn:
trích lập và chi sử dụng đều 0.

## 6. Bẫy dữ liệu thật → luật DQ/Conflict và case golden

| # | Bẫy (đã quan sát) | Loại theo 00 | Hệ thống phải làm |
| --- | --- | --- | --- |
| T1 | Brent tháng 8/2026: 91,08 / 90,9 / 88,00 | DEFINITION_DIFFERENCE | Không tạo ReviewCase; câu trả lời nêu rõ loại giá của từng nguồn |
| T2 | 15/09/2026 spot 130,80 vs futures ~107 | DEFINITION_DIFFERENCE (spot vs futures) | Nếu một nguồn được map nhầm vào cùng series → ReviewCase; người sửa mapping (kịch bản 27 bước) |
| T3 | EIA futures dừng 05/04/2024 | Freshness / source terminated | Vi phạm SLA → cảnh báo vận hành, không suy ra "giá không đổi" |
| T4 | Pink Sheet đổi URL, URL cũ vẫn 200 | Release detection | Đọc trang landing; so `Updated on` với release trước |
| T5 | RON95-III → E10RON95-III (16/06/2026) | DEFINITION_DIFFERENCE / series break | Series mới + quan hệ `SUCCEEDS`; không nối chuỗi |
| T6 | Kỳ điều hành thứ Bảy 07/3/2026, bình quân 2 ngày | Temporal | Kỳ lấy từ văn bản, không suy từ lịch |
| T7 | `25,760.00` (Vietcombank) và `25.226 đồng/lít` (Bộ Công Thương) | Schema/Numeric | Locale khai báo theo nguồn trong profile; parser giữ mọi cách đọc khi mơ hồ (`libs/indicator/numeric.py`) |
| T8 | Chênh vàng 8,8 vs 10,9 triệu | DEFINITION_DIFFERENCE (phía tỷ giá) | Chuỗi chênh lệch phải ghi rõ phía tỷ giá và phía giá SJC |
| T9 | Mã thiếu `-`, `--`, `NA`, `W` (EIA); `-`, `x` (JODI); `…` (World Bank) | Schema DQ | Map thành trạng thái quan sát (missing/withheld/confidential), không thành 0 |
| T10 | 32 đợt giảm ≥5%/5 phiên trong 3 năm | Alert storm | Gộp theo cửa sổ, cooldown, ngưỡng theo chế độ biến động |
| T11 | `DCOILBRENTEU` (FRED) = EIA `RBRTE` | Provenance | Hai nguồn cùng gốc không tính là xác nhận độc lập |
| T12 | Spread Brent–WTI 18–19 USD | Numeric DQ | Bất thường so lịch sử nhưng đúng; cần khai báo chế độ (regime) để không chặn |

## 7. Làm sao có giá vàng thế giới theo ngày (D3)

Không có nguồn miễn phí sạch licence (mục 1, phát hiện 5). Ba đường, kiểm tra ngày 27/09/2026:

| | A. Licence LBMA chính thức | B. API giá vàng thương mại | C. Chỉ dùng số tháng |
| --- | --- | --- | --- |
| Là gì | LBMA Gold Price (phiên đấu giá IBA, benchmark chuẩn) | Giá spot XAU/USD tổng hợp từ nhiều nguồn (ví dụ MetalpriceAPI, Metals-API, GoldAPI.io) | IMF PCPS `PGOLD`, World Bank Pink Sheet |
| Chi phí | Usage Licence tổ chức phi tài chính **8.000 USD/năm** (tổ chức tài chính 31.000, NHTW/MDB 17.000; biểu phí IBA 2026); phân phối lại dữ liệu trễ ≥ 4 giờ 9.000 USD/năm; cộng phí kênh dữ liệu của vendor | 5–132 USD/tháng tuỳ số request (MetalpriceAPI); gói trả phí mới được dùng thương mại | 0 |
| Thời gian có | Vài tuần (hợp đồng Master Licence Agreement + mua sắm + kênh dữ liệu) | Vài giờ | Đang có |
| Chất lượng cho ANKT | Benchmark, trích dẫn được | Không nêu nguồn gốc giá ("commercial sources and banks"), không cam kết độ chính xác, không cho phép rõ việc lưu trữ, quyền dùng hết khi ngừng thuê bao | Nguồn chính thức, chỉ theo tháng |
| Mở khoá | Mọi thứ | Vàng ngày, volatility vàng, chênh vàng trong nước – thế giới theo ngày | Phân tích theo tháng |

Các bước nếu chọn:

**A (production):**

1. Gửi email tới `IBA-licensing@ice.com` mô tả mục đích (phân tích an ninh kinh tế nội bộ, không định
   giá tài sản, không phân phối lại) để IBA xác định loại licence hoặc miễn. Theo biểu phí, Usage
   Licence áp dụng cho "valuation and pricing activities"; dùng để phân tích có thuộc diện này hay
   không cần IBA trả lời bằng văn bản.
2. Ký Master Licence Agreement, Schedule J (Usage). Lập ngân sách 8.000 USD/năm.
3. Chọn kênh nhận dữ liệu (ICE Data Services hoặc một vendor đã có licence phân phối); licence của IBA
   không bao gồm việc giao dữ liệu.
4. Thêm nguồn `rest_api`, key trong OpenBao, `use_rights: internal_analysis`, entity `GOLD_LBMA_PM`.

**B (POC, trong ngày):**

1. Chọn vendor, đọc điều khoản: cho phép lưu trữ lịch sử, dùng nội bộ bởi cơ quan nhà nước, không
   hết quyền với dữ liệu đã lưu khi ngừng thuê bao. MetalpriceAPI không ghi rõ ba điểm này; cần hỏi
   bằng văn bản hoặc chọn vendor khác ghi rõ.
2. Mua gói trả phí thấp (khoảng 12–32 USD/tháng đủ cho lịch crawl vài lần/ngày).
3. Thêm nguồn `rest_api` (seed `gold-spot-commercial-api` trong `data/sources-oil-gold.yaml`), entity
   **`GOLD_SPOT_VENDOR`**, không phải `GOLD_LBMA_PM` (hai entity khác nhau, ADR-V2-19).
4. Luật DQ cross-source: trung bình tháng của vendor lệch IMF `PGOLD` > 1% → ReviewCase.
5. Mọi màn và câu trả lời ghi nhãn "nguồn thương mại, không phải benchmark".

Đề xuất: **B ngay cho POC** để có chênh vàng theo ngày trong kịch bản Việt Nam (06 mục 7), **A song
song** nếu hội đồng cần số benchmark để trích dẫn chính thức.

Futures dầu theo ngày: không cần cho POC; bước 12 của kịch bản dùng nguồn fixture (06 mục 6).
Production cần licence ICE/CME hoặc vendor.

## 8. Biến giải thích (driver) → series

| Câu hỏi "vì sao" | Series có sẵn | Nguồn event/claim |
| --- | --- | --- |
| Dầu: cung | JODI sản xuất; tồn kho EIA | Thông cáo OPEC+, EIA STEO, WPSR |
| Dầu: địa chính trị | GPR daily (`GPRD_ACT`, `GPRD_THREAT`) | Sự kiện có ngày hiệu lực (Hormuz, trừng phạt) |
| Dầu: dòng tiền | CFTC COT (managed money WTI/Brent) | |
| Dầu → giá xăng VN | Giá thế giới bình quân kỳ (Bộ Công Thương), tỷ giá | Quyết định điều hành, quỹ bình ổn |
| Vàng: lãi suất thực, USD | FRED `DFII10`, `DTWEXBGS` (cần key) | |
| Vàng: NHTW, ETF | WGC (quý), GLD holdings (PDF) | Báo cáo WGC |
| Vàng: rủi ro | GPR, VIX | |
| Vàng trong nước | SJC/PNJ, tỷ giá Vietcombank | Văn bản NHNN, Nghị định 232/2025 |

Case UC10 phải dùng series có `available_at` trước thời điểm đợt giảm, nếu không sẽ "giải thích"
bằng thông tin chưa công bố.

## 9. Danh mục series cho POC

| entity / indicator | series_key | Nguồn | Đơn vị | Chính xác | Tần suất (dữ liệu / công bố) |
| --- | --- | --- | --- | --- | --- |
| BRENT_SPOT | `EIA:RBRTE:D` | EIA | USD/thùng | 2 | D / W |
| WTI_SPOT | `EIA:RWTC:D` | EIA | USD/thùng | 2 | D / W |
| WTI_FUTURES_M1 (lịch sử tới 04/2024) | `EIA:RCLC1:D` | EIA | USD/thùng | 2 | đã dừng |
| BRENT_MONTHLY | `IMF:PCPS:POILBRE:M`, `WB:PINK:BRENT:M` | IMF, WB | USD/thùng | 2 | M / M |
| WTI_MONTHLY, DUBAI_MONTHLY | `IMF:PCPS:POILWTI:M`, `IMF:PCPS:POILDUB:M` | IMF | USD/thùng | 2 | M |
| US_CRUDE_STOCKS | `EIA:WCESTUS1:W` | EIA | nghìn thùng | 0 | W / W |
| OIL_PRODUCTION_{area} | `JODI:CRUDEOIL:INDPROD:{area}:M` | JODI | KBD | 0 | M |
| COT_WTI_MM_NET, COT_GOLD_MM_NET | `CFTC:DISAGG:{code}:MM_NET:W` | CFTC | hợp đồng | 0 | W / W |
| GPR_DAILY | `GPR:GPRD:D` | Caldara & Iacoviello | chỉ số | 2 | D |
| GOLD_MONTHLY | `IMF:PCPS:PGOLD:M`, `WB:PINK:GOLD:M` | IMF, WB | USD/troy oz | 2 | M |
| USDVND_VCB_{BUY,SELL} | `VCB:USDVND:{BUY,SELL}:I` | Vietcombank | VND/USD | 0 | nhiều lần/ngày |
| SJC_BAR_{BUY,SELL} | `SJC:BAR:HCM:{BUY,SELL}:I` | SJC | triệu VND/lượng | 1 | nhiều lần/ngày |
| VN_FUEL_WORLD_AVG_{product} | `MOIT:WORLD_AVG:{RON92,RON95,DO005S,FO180}:P` | Bộ Công Thương | USD/thùng, USD/tấn | 3 | theo kỳ |
| VN_FUEL_RETAIL_{product} | `MOIT:RETAIL:{E5RON92,E10RON95_III,RON95_III,DO005S}:P` | Bộ Công Thương | VND/lít | 0 | theo kỳ |

Chuỗi dẫn xuất: `BRENT_WTI_SPREAD`, `BRENT_RET_{1,5,20}D`, `BRENT_VOL_20D`, `BRENT_SPOT_VS_MONTHLY_BASIS`,
`VN_GOLD_GAP_{BUY_FX,SELL_FX}` (tháng, vì vàng thế giới chỉ có tháng), `VN_FUEL_PASSTHROUGH`
(INFERRED, không phải số công bố).

`I` = intraday (nhiều lần/ngày); `P` = theo kỳ điều hành.

## 10. Quy tắc licence

- `use_rights ∈ {internal_analysis, pending_review, blocked}`; mặc định `pending_review`.
- `blocked`: worker không chạy nguồn (LBMA, ICE, CME, Platts khi chưa có licence).
- `pending_review`: nạp và dùng ở dev, không xuất báo cáo ra ngoài, không vào golden set gửi hội đồng.
- Mọi câu trả lời và file xuất mang dòng ghi nguồn lấy từ trường `licence` (IMF yêu cầu trích dẫn
  "International Monetary Fund. Primary Commodity Price System (PCPS)…").

## Nguồn tra cứu

- EIA, NYMEX Futures Prices (ghi chú "Futures prices after April 5, 2024, are not available"): https://www.eia.gov/dnav/pet/pet_pri_fut_s1_d.htm
- EIA, Spot Prices (release date, cách tính trung bình): https://www.eia.gov/dnav/pet/pet_pri_spt_s1_d.htm
- Wikipedia, 2026–2028 world oil market chronology: https://en.wikipedia.org/wiki/2026%E2%80%932028_world_oil_market_chronology
- Convex, Brent $107.46 (Sep 15, 2026): https://convextrade.com/metrics/brent
- FRED Blog, xoá chuỗi LBMA (tag GOLDAMGBD228NLBM): https://fredblog.stlouisfed.org/tag/goldamgbd228nlbm/
- IBA, Licensing and Data Fee Schedule 2026 (mục 3, LBMA Precious Metals Prices): https://www.ice.com/publicdocs/IBA_MLA_Licensing_Data_Fee_Schedule_2026.pdf
- MetalpriceAPI, bảng giá và điều khoản: https://metalpriceapi.com/pricing, https://metalpriceapi.com/terms
- LBMA Gold Price FAQs: https://www.lbma.org.uk/prices-and-data/lbma-gold-price/lbma-gold-price
- WGC Goldhub gold prices: https://www.gold.org/goldhub/data/gold-prices
- WGC/SSGA Monthly Gold Monitor 9/2026: https://www.ssga.com/library-content/products/fund-docs/etfs/us/insights-investment-ideas/monthly-gold-monitor.pdf
- Central banks buy record gold in Q2 2026: https://www.bajajbroking.in/share-market-news/central-banks-buy-record-gold-in-q2-2026
- Bộ Công Thương, điều hành giá xăng dầu 24/9/2026: https://moit.gov.vn/tin-tuc/mot-so-thong-tin-dieu-hanh-gia-ban-xang-dau-ngay-24-9-2026.html
- Bộ Công Thương, điều hành giá xăng dầu 7/3/2026: https://moit.gov.vn/tin-tuc/mot-so-thong-tin-ve-viec-dieu-hanh-gia-xang-dau-ngay-7-3-2026.html
- Nghị định 80/2023/NĐ-CP: https://vanban.chinhphu.vn/?pageid=27160&docid=208982
- E10RON95-III thay RON95-III từ 16/6/2026: https://luatvietnam.vn/tin-van-ban-moi/tu-16-6-2026-xang-e10ron95-iii-chinh-thuc-thay-the-ron95-iii-trong-dieu-hanh-gia-186-109649-article.html
- Nghị định 232/2025/NĐ-CP: https://vanban.chinhphu.vn/?pageid=27160&docid=215074
- Giá vàng 26/9/2026, SJC 144,4 triệu/lượng: https://vietnamnet.vn/gia-vang-hom-nay-26-9-2026-ket-tuan-giam-sau-sjc-neo-moc-144-4-trieu-luong-2559010.html
- Chênh lệch vàng trong nước – thế giới ~10 triệu (27/9/2026): https://cpchauxuannguyen.home.blog/2026/09/27/chenh-lech-gia-vang-trong-nuoc-va-the-gioi-lai-tang-len-10-trieu-dong-luong/
- IMF PCPS (SDMX): https://api.imf.org/external/sdmx/2.1/data/PCPS/G001.PGOLD.USD.M
- JODI Oil World Database: https://www.jodidata.org/oil/
- CFTC Commitments of Traders: https://www.cftc.gov/MarketReports/CommitmentsofTraders/index.htm
- Caldara, D. và Iacoviello, M. (2022), "Measuring Geopolitical Risk", American Economic Review; dữ liệu: https://www.matteoiacoviello.com/gpr.htm
- Alquist, R., Kilian, L. và Vigfusson, R. (2013), "Forecasting the Price of Oil", Handbook of Economic Forecasting, vol. 2
