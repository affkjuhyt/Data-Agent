# Danh mục nguồn dữ liệu đầu vào cho POC ANKT (chỉ số test: Nợ công/GDP)

> Khảo sát web ngày 15/09/2026 (tìm kiếm + gọi thử trực tiếp từ server dự án). Bản máy đọc: [`data/sources.yaml`](data/sources.yaml).
> **Trạng thái:** `V` = đã mở trang/tải file/gọi API thành công · `S` = chỉ thấy qua kết quả tìm kiếm hoặc trang chính thức không mở hết · `U` = chưa xác minh.
> **Giấy phép:** ghi theo điều khoản nguồn công bố; cần rà soát pháp lý trước khi dùng thương mại hoặc phân phối lại.

---

## 1. Tổng quan độ phủ định dạng

| Định dạng | Nguồn tiêu biểu (trạng thái) | Thành phần hệ thống được test |
|---|---|---|
| PDF có text, nhiều bảng | Bản tin nợ công MOF (V, bản mirror), Niên giám thống kê NSO (V), báo cáo IMF/WB/ADB/AMRO (V), VBMA (S) | Docling, trích bảng, trang/bbox, trích dẫn |
| PDF scan / ảnh | Niên giám cũ trên SEADELT (V); Công báo có chữ ký số (U); **tự sinh bằng Augraphy** từ PDF có text | OCR, chống lỗi chữ số |
| DOCX | Báo cáo KT-XH tháng của NSO (V) | Parser văn bản |
| XLS / XLSX | Biểu số liệu tháng NSO (V), XNK theo tháng `.xls` (V) | Structured import nhiều sheet/bảng |
| CSV | IMF SDMX-CSV (V), BIS CSV (V), OECD csvfile (V), WDI bulk zip (S) | Bulk load chuỗi thời gian |
| JSON API | World Bank v2 (V), ASEANstats (V), NSO PX-Web v1 (V, hạn chế), UN Comtrade preview (V), FRED (V, cần key) | Connector REST, phân trang, rate limit |
| SDMX (ML/JSON/CSV) | IMF SDMX 3.0 (V), BIS SDMX (V), OECD (V) | Connector SDMX, codelist |
| XML | Tỷ giá Vietcombank (V) | Parser XML, lịch chạy ngày |
| HTML table / trang JS | SBV tỷ giá & cán cân thanh toán (V), Hải quan (V, render JS), HNX đấu thầu TPCP qua AJAX (V), công khai NSNN MOF (V) | Scraper, headless browser |
| RSS / luồng | baochinhphu.vn (V), VnExpress, CafeF, VietnamPlus, VnEconomy (S); GDELT 15 phút (S) | Polling, dedup, luồng liên tục |
| Infographic / biểu đồ | Infographic NSO (V, dạng PDF); ViInfographicVQA (V); ChartQA, PlotQA (V) | VLM, đọc biểu đồ |
| Văn bản pháp luật | VBHN 59/VBHN-VPQH 2026 trên vanban.chinhphu.vn (V); corpus vbpl 171.556 văn bản trên Hugging Face (V) | KB pháp lý, hiệu lực văn bản |

---

## 2. Nguồn chính thức Việt Nam

| # | Nguồn | URL | Dữ liệu liên quan | Định dạng | Tần suất / độ trễ | TT |
|---|---|---|---|---|---|---|
| VN1 | **MOF — Bản tin nợ công** | Danh mục: `https://qln.mof.gov.vn/ban-tin-no-cong.htm` (S, DNS lỗi từ server). Bản số 17 (2019–6/2023), mirror: `https://static1.vietstock.vn/edocs/14237/document_2_.pdf` (V, 2,4 MB, 24 trang); số 15, 16: `edocs/14235`, `edocs/14236` (S) | Nợ công, nợ CP, nợ nước ngoài quốc gia (%GDP), nghĩa vụ trả nợ/thu NSNN, trả nợ nước ngoài/XK, cơ cấu chủ nợ — **đủ 6 chỉ tiêu nhóm A** | PDF có bảng | ~1 số/năm, trễ 6–12 tháng; từ số 1 (2013) | V/S |
| VN2 | MOF — Quyết định công khai số liệu nợ công | QĐ 2823/QĐ-BTC (2025), bảng 2020–2024 (S, qua trang luật) | Bảng công khai nợ công 5 năm | PDF/DOC | Năm | S |
| VN3 | MOF — Công khai NSNN | `https://ckns.mof.gov.vn/SitePages/home.aspx` (V, SharePoint) | Dự toán, thực hiện, quyết toán NSNN; lịch công bố | HTML + đính kèm (U: PDF/XLS) | Quý/năm | V |
| VN4 | Kho bạc Nhà nước | `https://vst.mof.gov.vn` (V, Oracle WebCenter) | Phát hành TPCP, báo cáo ngân sách (U) | HTML | — | V/U |
| VN5 | **NSO — Báo cáo KT-XH tháng** | Ví dụ T8/2026: `https://www.nso.gov.vn/bai-top/2026/09/bao-cao-tinh-hinh-kinh-te-xa-hoi-thang-tam-va-8-thang-nam-2026/` (V); file `01-Loi-van-T8.2026-final.docx`, `02-Bieu-T8.2026.xlsx` | GDP quý, thu chi NSNN, XNK | **DOCX + XLSX** | Tháng, ngày 3–6 tháng sau | V |
| VN6 | NSO — XNK theo tháng | `https://www.nso.gov.vn/en/data-and-statistics/2025/03/exports-and-imports-value-by-months-of-2025/` (V); ví dụ `wp-content/uploads/2025/03/E01-2025-10.xls` | Kim ngạch XNK | **XLS (cũ)** | Tháng | V |
| VN7 | **NSO — Niên giám thống kê** | 2025: `https://www.nso.gov.vn/wp-content/uploads/2026/07/NG-TOAN-QUOC-2025_WM.pdf` (V) | GDP giá hiện hành, NSNN, chuỗi dài | PDF nhiều bảng (có watermark) | Năm | V |
| VN8 | NSO — Infographic | Quý 2/2026 (V): trang `.../infographic-on-the-socio-economic-situation-in-the-second-quarter...`; file PDF `EN-T6.2026-1.7-update-TMDL_.pdf` | Tóm tắt KT-XH | PDF đồ hoạ | Quý | V |
| VN9 | NSO — PX-Web | UI: `https://pxweb.nso.gov.vn/pxweb/vi/` (S, ~47 bảng tài khoản quốc gia gồm V03.02 GDP giá hiện hành); API: `https://pxweb.nso.gov.vn/api/v1/vi/` (V, JSON nhưng chỉ 8 CSDL, tài khoản quốc gia trả 404) | GDP, NSNN | JSON / export UI | Năm/quý | V/S |
| VN10 | SBV — Tỷ giá trung tâm | `https://sbv.gov.vn/vi/t%E1%BB%B7-gi%C3%A1` (V) | USD/VND trung tâm, tham chiếu | HTML (chỉ ngày hiện tại) | Ngày | V |
| VN11 | SBV — Cán cân thanh toán | `https://sbv.gov.vn/en/balance-of-international-payment` (V) | BoP quý (triệu USD), trễ ~1 năm | HTML table | Quý | V |
| VN12 | SBV — Dự trữ ngoại hối (metadata NSDP) | `https://sbv.gov.vn/en/w/sbv606219` (V); số liệu chủ yếu qua IMF IL | Dự trữ | HTML metadata | Tháng | V |
| VN13 | Hải quan | `https://www.customs.gov.vn/index.jsp?pageId=3521` (V, render JS, xuất Excel/CSV phía client) | XNK sơ bộ/chính thức | HTML JS → XLSX/CSV | 2 kỳ/tháng | V/U |
| VN14 | **HNX — Đấu thầu TPCP** | `https://hnx.vn/vi-vn/trai-phieu/lich-dau-thau.html` (V; chuỗi chứng chỉ thiếu); AJAX: `/ModuleReportBonds/Bond_DauThau/Bond_KetQua_DauThau` (V) | Kỳ hạn, khối lượng gọi/trúng, lãi suất trúng thầu | HTML fragment/AJAX | Ngày/tuần | V |
| VN15 | VBMA — Báo cáo thị trường trái phiếu | `https://vbma.org.vn/vi/reports/monthly` (S, 403) | Thị trường TPCP | PDF | Tuần/tháng/năm | S |
| VN16 | **Luật Quản lý nợ công VBHN 59/2026** | `https://datafiles.chinhphu.vn/cpp/files/vbpq/2026/3/59-vbhn-vpqh.pdf` (V) | Định nghĩa nợ công | PDF | — | V |
| VN17 | Công báo | `https://congbao.chinhphu.vn/van-ban/luat-so-20-2017-qh14-25325.htm` (S); robots cho phép (V) | Luật 20/2017, nghị quyết | HTML + PDF (thường có chữ ký số/scan, U) | — | S |
| VN18 | NQ 26/2026/QH16 (ngưỡng 2026–2030) | Chưa tìm được bản toàn văn chính thức (U); chỉ có trên trang luật thương mại | Trần/cảnh báo 6 chỉ tiêu | — (nạp thủ công kèm metadata) | — | U |
| VN19 | vbpl.vn | `https://vbpl.vn/TW/Pages/vbpq-van-ban-goc.aspx?ItemID=126321` (S); **chặn crawler (WAF)** (V) | Văn bản gốc | HTML JS | — | S |
| VN20 | Tỷ giá Vietcombank | `https://portal.vietcombank.com.vn/Usercontrols/TVPortal.TyGia/pXML.aspx` (V) | 22 ngoại tệ mua/chuyển khoản/bán | **XML** (số có dấu phẩy nghìn) | Realtime snapshot | V |
| VN21 | baochinhphu.vn RSS | `https://baochinhphu.vn/kinh-te.rss` (V, 50 tin, ngày giờ không chuẩn RFC-822) | Tin kinh tế | **RSS** | Liên tục | V |
| VN22 | data.gov.vn | DNS lỗi từ server (U) | >10.600 bộ dữ liệu (theo báo chí) | U | — | U |

---

## 3. Nguồn quốc tế (API/SDMX)

| # | Nguồn | Ví dụ gọi đã chạy được | Nội dung Việt Nam | Định dạng · khoá · giới hạn | TT |
|---|---|---|---|---|---|
| IN1 | **IMF SDMX 3.0 — WEO** | `https://api.imf.org/external/sdmx/3.0/data/dataflow/IMF.RES/WEO/+/VNM.GGXWDG_NGDP.A` | Nợ gộp chính phủ chung % GDP (2000–2031) | SDMX-ML / SDMX-JSON (`Accept: application/json`) / SDMX-CSV · không khoá · ~10 req/5s (U) | V |
| IN2 | IMF — GDD | `https://api.imf.org/external/sdmx/3.0/data/dataflow/IMF.FAD/GDD/+/VNM.*.*` | Nợ công, nợ tư nhân (từ 1950) | như trên | V |
| IN3 | IMF — IL (dự trữ) | `https://api.imf.org/external/sdmx/3.0/data/dataflow/IMF.STA/IL/+/VNM.*.*.A` | Dự trữ quốc tế | như trên | V |
| IN4 | IMF DataMapper | `https://www.imf.org/external/datamapper/api/v1/GGXWDG_NGDP/VNM` | JSON gọn | JSON · **curl bị Akamai 403**, cần header trình duyệt | V/S |
| IN5 | **World Bank WDI** | `https://api.worldbank.org/v2/country/VNM/indicator/NY.GDP.MKTP.CN?format=json&mrv=3` | GDP danh nghĩa VND; `GC.DOD.TOTL.GD.ZS` **rỗng cho VNM** | JSON/XML · không khoá · CC BY 4.0 | V |
| IN6 | World Bank IDS | `https://api.worldbank.org/v2/sources/6/country/VNM/series/DT.TDS.DECT.CD/counterpart-area/WLD/time/YR2023?format=json` | Nghĩa vụ trả nợ nước ngoài, theo từng chủ nợ | JSON · phải dùng đường dẫn đa chiều | V |
| IN7 | World Bank JEDH | `https://api.worldbank.org/v2/sources/54/country/VNM/series/Q.1C0.1C0.C.9A.MOA.RXGT.1.ALL.MV.TO1.ALL/time/all?format=json` | Dự trữ, nợ nước ngoài theo quý | JSON | V |
| IN8 | WDI bulk | `https://databankfiles.worldbank.org/public/ddpext_download/WDI_CSV.zip` | ~1.600 chỉ số × 217 nền kinh tế | ZIP CSV (~280 MB theo tài liệu; tải trực tiếp bị 403 từ server) | S |
| IN9 | **BIS SDMX** | `https://stats.bis.org/api/v1/data/WS_XRU/M.VN.VND.A?startPeriod=2024-01&format=csv`; `.../WS_DEBT_SEC2_PUB/Q.VN.3P.1.1.C.A.F.TO1.A.A.A.A.A.I?format=csv` | Tỷ giá, chứng khoán nợ quốc tế. **Không có** tín dụng tổng, DSR cho VN | CSV/SDMX · không khoá | V |
| IN10 | BIS bulk | `https://data.bis.org/bulkdownload` | Tỷ giá ngày ~80 nền kinh tế từ ~1970 | CSV | S |
| IN11 | OECD SDMX | `https://sdmx.oecd.org/public/rest/data/OECD.CTP.TPS,DSD_REV_COMP_ASAP@DF_RSASAP,/VNM..S13.T_5000..PT_B1GQ.A?startPeriod=2018&format=csvfile` | Thu thuế % GDP (Asia-Pacific) | CSV · ~20 truy vấn/giờ (U) | V |
| IN12 | UN Comtrade | `https://comtradeapi.un.org/public/v1/preview/C/A/HS?reporterCode=704&period=2023&partnerCode=0&flowCode=X&cmdCode=TOTAL` | XK Việt Nam | JSON · preview ≤500 dòng; key miễn phí 500 call/ngày; bulk trả phí | V |
| IN13 | ASEANstats | `https://data.aseanstats.org/api/indicator/FDI.AMS.TOT.INF` | FDI, chỉ số ASEAN | JSON · CC BY 4.0 | V |
| IN14 | FRED | `https://api.stlouisfed.org/fred/series/observations?series_id=VNMGGXWDGG01GDPPT&api_key=KEY&file_type=json` | Nợ CP chung % GDP, GDP USD, CA/GDP | JSON/XML · key miễn phí · 120 req/phút | V |
| IN15 | ADB KIDB | `https://kidb.adb.org/api/v3/sdmx/` (cú pháp dữ liệu U; bot protection) | Tài khoá, nợ nước ngoài, BoP | SDMX | U |

**Thư viện Python đã kiểm tra trên PyPI:** `sdmx1` 2.27.0 (dùng thay `pandasdmx` đã cũ), `wbgapi` 1.0.14, `imfp` 2.0.0, `fredapi` 0.5.2, `comtradeapicall` 1.3.2.

**Bẫy phạm vi cần đưa vào eval:** IMF WEO/GDD đo *nợ gộp chính phủ chung* (GFSM 2014): 2024 ≈ 34% GDP. MOF công bố *nợ công theo luật VN* (gồm nợ được bảo lãnh, nợ CQĐP): ≈ 36–37%. WDI không có số nợ CP trung ương cho VN. BIS chỉ có chứng khoán nợ phát hành quốc tế.

---

## 4. Báo cáo dài có nhiều bảng (tài liệu tiếng Anh)

| # | Tài liệu | URL | TT |
|---|---|---|---|
| RP1 | IMF Article IV Vietnam 2024 | `https://www.imf.org/en/publications/cr/issues/2024/09/27/vietnam-2024-article-iv-consultation-press-release-staff-report-and-statement-by-the-555679` | V |
| RP2 | IMF Article IV Vietnam 2025 (staff report có thể chưa công bố) | `https://www.imf.org/en/publications/cr/issues/2025/10/03/vietnam-2025-article-iv-consultation-press-release-staff-report-and-statement-by-the-570895` | V |
| RP3 | World Bank Taking Stock 09/2025 | `https://documents.worldbank.org/en/publication/documents-reports/documentdetail/099133509042513941` | V |
| RP4 | World Bank Macro Monitoring 01/2026 | `https://documents1.worldbank.org/curated/en/099626201262617816/pdf/IDU-9be10374-e574-4efa-9ffd-0e61497af363.pdf` | V |
| RP5 | ADB Asian Development Outlook 04/2026 — Viet Nam | `https://www.adb.org/sites/default/files/publication/1135881/vie-ado-april-2026.pdf` | V |
| RP6 | AMRO Annual Consultation Report Vietnam 2024 | `https://amro-asia.org/wp-content/uploads/2025/03/2024-Vietnam-ACR_publication_18Feb2025_SI4.pdf` | V |
| RP7 | AMRO Country Fiscal Review Vietnam 02/2025 (tập trung nợ) | `https://amro-asia.org/wp-content/uploads/2025/02/Country-Fiscal-Review_Vietnam_final.pdf` | V |
| RP8 | Niên giám thống kê Việt Nam lịch sử (1913–2024, có bản scan) | `https://seadelt.net/Vietnam/Documents/` | V |

---

## 5. Corpus lớn và bộ benchmark

| Nhóm | Bộ dữ liệu | Quy mô | Giấy phép | Dùng để |
|---|---|---|---|---|
| Pháp luật | `th1nhng0/vietnamese-legal-documents` (HF, từ vbpl.vn) | 171.556 văn bản, 4,37 GB Parquet | CC BY 4.0 | Tải KB pháp lý, đồ thị sửa đổi |
| Pháp luật | `GreenNode/zalo-ai-legal-text-retrieval-vn` | 61,4k đoạn, 818 truy vấn + qrels, 150 MB | MIT | Đo retrieval recall@k, MRR |
| Pháp luật | `duyet/vietnamese-legal-instruct` | 467.732 cặp QA | Chưa rõ | Tải QA |
| Tin tức | `bkai-foundation-models/BKAINewsCorpus` | 16,76 triệu dòng, 28,7 GB | Không nêu (trích arXiv 2403.01616) | Tải embedding lớn |
| Tin tức | GDELT 2.0 Events | Từ 2015, file 15 phút | Mở | Luồng liên tục |
| Layout | DocLayNet | 80.863 trang, ~35 GiB | CDLA-Permissive-1.0 | Layout |
| Bảng | FinTabNet.c | 3,43 GB | CDLA-Permissive-2.0 | Cấu trúc bảng tài chính |
| Bảng | PubTables-1M | 947.642 bảng (dung lượng U, hàng chục GB) | U | Thông lượng trích bảng |
| Bảng | TableBank | 417k bảng | Apache-2.0 | Phát hiện bảng |
| OCR tiếng Việt | VinText | 2.000 ảnh, 56.084 vùng chữ | **Chỉ nghiên cứu** | OCR dấu tiếng Việt |
| OCR tiếng Việt | 5CD-AI/Viet-OCR-VQA | ~137k ảnh | Chưa rõ | OCR + VLM |
| Biểu đồ | ViInfographicVQA | 6.747 infographic, 20.409 QA, 5,03 GB | Không nêu | VLM tiếng Việt |
| Biểu đồ | ChartQA · PlotQA | 21.945 · 224.377 biểu đồ | GPL · CC BY 4.0 | VLM |
| QA số | FinQA · TAT-QA | — | CC BY 4.0 | Mẫu eval suy luận số |
| Sinh tải | Augraphy (PyPI `augraphy`) | — | — | Sinh PDF scan có ground truth |

Không xác minh/không tải được: Viet-Doc-VQA (401), ViNumQA (VLSP 2025), Open-ViTabQA, “ViFinQA” (không tồn tại), `binhvq/news-corpus` (đã gỡ dữ liệu).

---

## 6. Gói dữ liệu test theo tầng

> Ràng buộc hạ tầng: server hiện dùng 81% ổ 163 GB (~31 GB trống). **Tầng L cần bổ sung lưu trữ** (≥ 300 GB) trước khi chạy.

| Tầng | Dung lượng | Thành phần | Kiểm tra |
|---|---|---|---|
| **S — Smoke** | < 1 GB | VN1 (bản tin 15–17), VN5 (DOCX+XLSX 3 tháng), VN6, VN7 (niên giám 2025), VN16, VN20, VN21 (1 ngày); IN1–IN3, IN5–IN7, IN9, IN12; RP1, RP5, RP7; 2 niên giám scan RP8; Zalo legal 150 MB; 200 case FinQA/TAT-QA | Đầu–cuối cho Nợ công/GDP; đủ 10 định dạng; so khớp MOF–IMF–WB |
| **M — Chức năng** | ~10 GB | Corpus pháp luật vbpl 4,37 GB; FinTabNet.c 3,43 GB; phần Economy của ViInfographicVQA; toàn bộ niên giám NSO + ~1.000 PDF scan Augraphy; 1 triệu bài BKAI News; WDI bulk; IMF WEO toàn cầu; BIS XRU/XRD | Độ đúng OCR/bảng; KB pháp lý quy mô; rate limit connector |
| **L — Tải** | 100 GB+ | BKAI News đầy đủ 28,7 GB; DocLayNet ~35 GiB; PubTables-1M; PlotQA; GDELT backfill + luồng 15 phút; ~100k trang scan Augraphy; k6 phát lại >100k câu hỏi | Thông lượng embedding/OCR, kích thước index, độ trễ khi đồng thời cao |

---

## 7. Lưu ý thu thập

- Trang MOF mới là SPA JavaScript, `qln.mof.gov.vn` lỗi DNS từ server → POC dùng bản mirror + upload thủ công có metadata; đề nghị xin nguồn chính thức (hoặc kênh nội bộ) cho bản tin nợ công.
- vbpl.vn chặn crawler; quochoi.vn có thử thách cookie/JS → dùng corpus HF (CC BY 4.0) và `datafiles.chinhphu.vn`.
- HNX thiếu chuỗi chứng chỉ trung gian → cấu hình CA bundle, không tắt kiểm tra TLS trong production.
- IMF DataMapper chặn curl → dùng SDMX 3.0.
- Tin tức báo chí và dữ liệu `vnstock` có ràng buộc bản quyền/phi thương mại; VinText chỉ dùng nghiên cứu.
