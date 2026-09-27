# 02. Đánh giá thiết kế v2 (PDF 27/09/2026)

> Đối tượng: trưởng nhóm kỹ thuật, kiến trúc sư, anh Linh. Mục đích: chỉ ra phần nào của
> Chương 15–18 nên làm ngay, phần nào phải sửa trước khi làm, phần nào để sau POC.
>
> Viết trước khi có [00-architecture-v2.md](00-architecture-v2.md). Các điểm đã được 00 chốt khác đi:
> kho observation là **ClickHouse** (đề xuất Postgres cho POC trong bản trước của 03 và 07 đã bị thay);
> lịch 8 tuần và backlog V2-001…020 được thay bằng STEP 1–13 (05); kịch bản nghiệm thu là 27 bước
> của 00 mục 15. Phần đánh giá paper (mục 3) vẫn giữ nguyên giá trị.

## 1. Phần thiết kế đúng, giữ nguyên

| Nguyên tắc | Vì sao đúng với hệ thống này |
| --- | --- |
| SOURCE ≠ KNOWLEDGE ≠ INFERENCE ≠ FORECAST (15.4) | Lỗi nguy hiểm nhất của hệ thống là số sai trông đáng tin (R1). Tách loại tri thức cho phép verifier từ chối một câu trả lời trích một forecast như fact |
| Agent chỉ đề xuất, commit service mới ghi (ADR-V2-02) | Karpathy nêu rủi ro chính của mô hình LLM Wiki: LLM đưa lại bản tổng hợp sai của chính nó vào trang sau, lỗi tự khuếch đại. Proposal-only + provenance tới passage gốc chặn vòng này |
| LLM không tính số, forecast là artifact bất đồng bộ (ADR-V2-05) | Trùng với code đã viết trên `feat/add-task` (`formula.py`, `verifier.py`) |
| Licence là metadata bắt buộc (ADR-V2-08) | LBMA Gold Price cần licence khi dùng cho định giá; FRED đã gỡ chuỗi giá vàng LBMA (series `GOLDAMGBD228NLBM` ngừng từ 2022, cần xác nhận lại). Không có trường licence thì không có chỗ ghi các ràng buộc này |
| Không đưa WFM/GNG vào đường găng (ADR-V2-06) | Xem mục 3.2 |
| Không tự sửa production (ADR-V2-07) | Xem mục 3.3 |

## 2. Phần phải sửa trước khi triển khai

### 2.1 Hai POC, một đội, không có thứ tự

Chương 13 giữ POC Nợ công/GDP hạn 23/10. Chương 17 đặt kế hoạch 8 tuần cho dầu/vàng. Bảng
nguồn lực 13.6 (2 backend, 1 data engineer, 1 frontend, 2 research, 0,5 QA, 0,5 ops) phục vụ
cả hai. Đề xuất: **một lộ trình**, dầu/vàng là vertical chính; Nợ công/GDP chỉ giữ mức L1 tra
cứu làm regression, tận dụng cùng observation store. Mốc 23/10 trở thành M1 của lộ trình v2
(fact/trend/comparison cho dầu/vàng + một câu Nợ công/GDP).

Đã chốt 27/09/2026: một lộ trình dầu/vàng, POC trong 1 tháng (nghiệm thu 27/10) với 2 senior
tuyển thêm; Nợ công/GDP không làm mới, chỉ bảo đảm không hỏng (05).

### 2.2 Đặt tên và phạm vi theo miền

Bảng 17.2 đặt `commodity-api`, `commodity-connectors`, `commodity-forecast-worker`. Mục tiêu 2
của Chương 2 lại là "mở rộng theo số chỉ số bằng dữ liệu và cấu hình, không phải bằng cách thêm
agent hay sửa mã". Nếu dầu/vàng có service riêng thì vertical tiếp theo (tỷ giá, lương thực) sẽ
có service riêng nữa. Đề xuất:

| Thiết kế ghi | Đổi thành | Nội dung riêng của dầu/vàng nằm ở |
| --- | --- | --- |
| commodity-api | `indicator-api` (một deployable, module tách: series, observation, compute, knowledge, review, forecast registry) | Dòng trong bảng series, variable, formula |
| commodity-connectors | Worker `web_crawler` hiện có + consumer mới `series-extractor` | Dòng `KBSource` + `extraction_profile` trong `KBSourceConfig.config` |
| commodity-forecast-worker | `forecast-worker` | Dòng `forecast_spec` (target, horizon, feature set) |
| sources-v2.yaml | Nhập qua Bulk Import vào `KBSource` | File seed trong `docs/v2/data/` |

### 2.3 Hai đường dữ liệu: số đi đường có cấu trúc, văn bản đi đường truy xuất

Eval ngày 26/09 cho thấy agent trả sai số từ bảng Table_7 của "Bản tin nợ công" (38,2% cho Nợ
Chính phủ/GDP 2018, số đúng 49,9%). Ngày 27/09 đã tìm ra nguyên nhân (commit d116062): bước tóm
tắt chunk bằng LLM lỗi ở khoảng 32% chunk khi chạy song song, fallback cắt chuỗi tới dấu chấm đầu
tiên nên summary của bảng chỉ còn tiêu đề; rerank chấm trên summary nên bảng đúng tụt hạng. Sau khi
sửa fallback và backfill 480 chunk, agent trả đúng 37,4% và 49,9%.

Bài học không đổi kết luận, mà làm nó rõ hơn: độ đúng của một con số đang phụ thuộc vào chất lượng
một bước tóm tắt bằng LLM trên chunk. Chương 15.7 đề xuất retrieval-as-reasoning; paper LLM-Wiki báo
cáo tăng 2,0–8,1 F1 trên HotpotQA, MuSiQue, 2WikiMultiHopQA, là câu hỏi văn bản nhiều bước, không
phải tra một ô trong bảng số. Kiến trúc chính thức (00, bất biến 3) chốt đúng hướng này.

Quy tắc đề xuất: **mọi con số trả lời phải đi từ observation** (có series, kỳ, release,
source_ref). Bảng trong PDF, XLSX, payload API được trích thành observation ứng viên, qua duyệt,
rồi mới được hỏi. Retrieval-as-reasoning dùng cho câu "vì sao", "sự kiện nào", "định nghĩa gì",
nơi đơn vị trả lời là đoạn văn có trích dẫn.

### 2.4 Observation có phải "knowledge object" không

Bảng 15.5 liệt kê OBSERVATION cạnh CLAIM, EVENT; ADR-V2-04 lại nói graph không chứa observation.
Cần viết rõ: observation thuộc Truth plane (bảng observation, append-only), knowledge object chỉ
**tham chiếu** tới nó bằng `(series_key, period, release_id)`. Một CLAIM "tồn kho Mỹ giảm 3 tuần
liên tiếp" có evidence là ba observation, không sao chép giá trị. Nếu không chốt, hai nơi sẽ lưu
cùng một con số và lệch nhau sau lần điều chỉnh đầu tiên.

### 2.5 Mô hình kỳ đang thiên về năm/quý

Toàn bộ thiết kế v1 (Period A/Q/M, căn kỳ stock/flow theo năm) xây cho số liệu tài khoá. Dầu/vàng
cần thêm:

- Tần suất D và W; lịch giao dịch (Brent/WTI spot của EIA không có số ngày lễ Mỹ); "chuỗi đứt
  thì vẽ đứt" phải phân biệt ngày nghỉ với dữ liệu thiếu.
- Múi giờ và thời điểm chốt giá: giá spot EIA là giá đóng cửa New York; một câu hỏi "giá hôm nay"
  lúc 9h sáng Hà Nội thực ra hỏi về phiên hôm trước.
- Ngày công bố khác kỳ dữ liệu: báo cáo tồn kho EIA tuần kết thúc thứ Sáu công bố thứ Tư tuần sau.
  Đây là chỗ `release_date` quyết định đúng sai của forecast (mục 3.5).

### 2.6 Lịch 8 tuần đặt UI và golden set quá muộn

- Bảng 17.4 để Commodity UI ở tuần 5. Nhóm đã chốt ngày 16/09: năng lực chỉ được coi là xong khi
  có màn hình cho cả người dùng cuối và người nhập liệu. Mỗi tuần phải ra màn hình của phần làm
  trong tuần đó.
- Golden set v2 (V2-018) ở tuần 7, trong khi 16.10 yêu cầu "target retrieval đặt sau baseline
  tuần 1". Không có golden set thì không có baseline. Đề xuất: 40 case hạt giống ở tuần 0, đo hệ
  thống hiện tại trước khi xây, rồi mở rộng lên 150 case theo từng tuần.

### 2.7 Re-crawl đang xoá lịch sử

Phase A (22/09) làm re-crawl thay tài liệu cũ và xoá vết Postgres/Qdrant/Neo4j/Mongo của nó. Quyết
định này chưa từng được anh Linh xác nhận lại, và nó mâu thuẫn trực tiếp với "raw immutable" và
"numeric revision: giữ cả hai vintage" (16.6). Đề xuất tách hai khái niệm: **bản tìm kiếm hiện
hành** (KB được phép thay bằng bản mới nhất) và **lịch sử bất biến** (artifact theo SHA-256 trong
MinIO, release, observation không bao giờ xoá). Đã chốt: 00 bất biến 2, ADR-V2-21.

### 2.8 Kỳ vọng về forecast

Giá dầu và vàng ở tầm 1–30 ngày gần với bước ngẫu nhiên. Alquist, Kilian và Vigfusson
("Forecasting the Price of Oil", Handbook of Economic Forecasting, 2013) cho thấy dự báo "không
đổi" rất khó bị vượt ở tầm ngắn đối với giá dầu. Nếu tiêu chí nghiệm thu là "model ML vượt
baseline" thì kết quả hợp lý nhất của tuần 6 là candidate bị **rejected**. Tiêu chí nghiệm thu
16.10 đã viết đúng ("validated hoặc rejected"); cần nói trước với hội đồng rằng một pipeline từ
chối model yếu một cách có bằng chứng là kết quả đạt, không phải thất bại.

### 2.9 Nguồn tin tức

16.3 đưa "News/event feeds" vào POC. Tin tức kéo theo licence, khối lượng duyệt HITL lớn và rủi ro
knowledge poisoning cao nhất. Đề xuất POC chỉ lấy sự kiện từ nguồn chính thức: thông cáo OPEC,
EIA STEO/WPSR, WGC. Tin tức để sau khi write gate và lint đã chạy ổn.

### 2.10 Bỏ sót góc Việt Nam

Đề tài là an ninh kinh tế Việt Nam, nhưng Chương 16 chỉ nhìn giá thế giới. Ba chuỗi trong nước
làm POC có giá trị nghiệp vụ hơn hẳn và kiểm thử đúng các kỹ thuật khó của v2:

| Chuỗi | Kỹ thuật được kiểm thử |
| --- | --- |
| Giá vàng miếng trong nước (SJC) | Crawl headless (máy chủ nhận 403), dữ liệu nhiều lần trong ngày |
| Chênh lệch vàng trong nước – thế giới | Formula DAG có `FxConvert` và đổi đơn vị lượng ↔ ounce (1 lượng = 37,5 g = 1,20565 troy oz): đúng loại lỗi đơn vị/bội số mà `Quantity` được viết để chặn |
| Giá bán lẻ xăng dầu theo kỳ điều hành | Chuỗi bậc thang theo kỳ điều hành, sự kiện chính sách (điều chỉnh, quỹ bình ổn), câu hỏi truyền dẫn giá Brent → giá trong nước có độ trễ |

Đã chốt 27/09/2026: làm trong POC (D2), xem 05 và 06 mục 7.

### 2.11 Egress

INFRA 11.2 ghi egress proxy theo tên miền là điều kiện bắt buộc trước khi bật connector ra ngoài.
Worker đã crawl 35 nguồn ngoài mà chưa có. POC thêm 8–10 tên miền nữa; đây là lúc làm (P0-08).

## 3. Áp dụng các tài liệu nghiên cứu

Tóm tắt dựa trên abstract và gist gốc, đọc ngày 27/09/2026.

### 3.1 LLM-Wiki (arXiv 2605.25480) và mẫu LLM Wiki của Karpathy

**Nội dung:** biên dịch tài liệu thành trang wiki có liên kết hai chiều; agent search → read →
đi theo link → tự đánh giá đủ bằng chứng; "Error Book" lưu lỗi để tự sửa. Karpathy tách ba lớp:
nguồn thô bất biến, wiki do LLM viết, schema quy ước; ba thao tác ingest / query / lint; và cảnh
báo LLM tự đưa bản tổng hợp sai vào trang sau.

**Lấy gì:**

| Ý tưởng | Hiện thực trong repo | Việc (05) |
| --- | --- | --- |
| Ingest biên dịch tri thức | Worker `semantic_extractor` chạy sau Gold, sinh proposal CLAIM/EVENT với provenance tới passage (page/bbox) | S4-02 |
| Lint | CronJob kiểm stale / conflict / thiếu evidence; tái dùng `kb_conflict_resolution` cho mâu thuẫn văn bản | S6-02 |
| Query search→read→traverse | Capability trên LightRAG + Neo4j có sẵn, replan tối đa 2 vòng, dừng khi mọi EvidenceRequirement bắt buộc đã thoả | S9-02, S10-01 |
| Error Book | Bảng `experience_entry` ghi lỗi từ eval và trace đã phân loại | S13-01 |

**Không lấy:** trang wiki dạng markdown tự do do LLM viết làm nguồn trả lời. Câu trả lời chỉ được
trích passage gốc hoặc observation; knowledge object là chỉ mục có cấu trúc để tìm và nối, không
phải bằng chứng. Đây là cách chặn đúng rủi ro Karpathy nêu.

### 3.2 WFM, Wiki Foundation Model (arXiv 2609.18182)

**Nội dung:** mô hình học trên Wiki Graph (context dày + liên kết topo), message passing có
attention theo truy vấn, giao thức NCCL cho huấn luyện phân tán nhanh 10,5 lần. Abstract không
nêu dữ liệu huấn luyện và số liệu so sánh cụ thể.

**Đánh giá:** cần huấn luyện phân tán nhiều GPU và nhãn truy vấn → evidence; cụm chỉ có một A100
40 GB đã dùng 33 GB cho chat. Giữ quyết định ADR-V2-06. Việc duy nhất làm trong POC: lưu liên
kết có kiểu (`SUPPORTS`, `AFFECTS`, `SUPERSEDES`, `MENTIONS`) và nhãn evidence vàng trong golden
set, để sau này có dữ liệu thử nếu quyết định nghiên cứu.

### 3.3 WikiSkill (arXiv 2608.27454)

**Nội dung:** trải nghiệm thực thi → wiki bền vững → skill thực thi được; skill chuyển được giữa
model, model nhỏ có skill vượt model lớn không skill; bài báo ít bàn về chi phí và trường hợp skill
làm tệ hơn.

**Đánh giá:** điều kiện là lịch sử thực thi đủ nhiều và bộ eval ổn định. Hiện có 123 case cũ, 0
case dầu/vàng. Trong POC chỉ làm hạ tầng tối thiểu: skill = file prompt/procedure có version
trong git, gắn `compatible_models`, chỉ deploy qua eval gate. Thí nghiệm tuần 8 là **tuỳ chọn**:
chọn 1 loại lỗi xuất hiện ≥ 10 lần trong Error Book, viết (tay hoặc LLM gợi ý) một skill, so
trước/sau trên cùng golden set. Không xây "Skill Proposer" tự động trước khi có dữ liệu.

### 3.4 Graph Engineering (arXiv 2608.21156)

**Nội dung:** Task graph (phân rã, phụ thuộc), Capability graph (ghép năng lực với công cụ),
Runtime-State graph (trạng thái bền), phục hồi chọn lọc.

**Đánh giá:** repo đã có ba mảnh dưới tên khác: planner DPTS + executor DAG là task graph;
checkpointer LangGraph là runtime state; `TOOL_REGISTRY` là capability graph dạng tĩnh. Không xây
"graph service" mới. Việc cụ thể:

| Mảnh | Việc |
| --- | --- |
| Task graph | Planner xuất JSON có kiểu: `node_id`, `capability`, `inputs`, `depends_on`, `evidence_requirements` |
| Capability graph | Bảng `capability` (id, provider, input/output schema, quyền, SLO, chi phí) thay `TOOL_REGISTRY`; mcp-gateway sau này đọc cùng bảng |
| Runtime state | Ghi trạng thái từng node vào bảng `task_run`; test kill worker giữa job rồi resume |
| Selective recovery | Khi node lỗi, chỉ chạy lại các node downstream theo `depends_on` |

### 3.5 Forecasting (16.7), bổ sung về rò rỉ dữ liệu

Feature snapshot phải dựng bằng truy vấn point-in-time theo `release_date ≤ as_of`, không theo
`period`. Ví dụ: forecast lập ngày thứ Hai không được dùng số tồn kho EIA của tuần kết thúc thứ Sáu
trước đó, vì số này công bố thứ Tư. Observation store có vintage (T2) là thứ làm backtest trung
thực; không có nó, backtest sẽ đẹp hơn thực tế. Đây là lý do forecast phải đi sau Truth plane,
không làm song song.

Đề xuất kỹ thuật:

| Hạng mục | Chọn |
| --- | --- |
| Baseline bắt buộc | Random walk (last value), drift, ETS; cho vàng thêm random walk trên log |
| Candidate | LightGBM trên log-return với lag, volatility, feature vĩ mô, cờ sự kiện |
| Backtest | Rolling origin, cửa sổ mở rộng, ≥ 100 điểm gốc cho mỗi horizon |
| Khoảng dự báo | Conformal theo phần dư backtest (không giả định phân phối) |
| Tiêu chí validated | MASE < 1 so với random walk ở ít nhất 2/3 horizon và độ phủ khoảng 80% nằm trong 75–85% |
| Thư viện | `statsmodels`, `lightgbm`, `scikit-learn` trong image `forecast-worker` riêng, không thêm vào whitelist sandbox |

## 4. Đánh giá độ rủi ro của từng hạng mục v2

| Hạng mục | Giá trị cho POC | Rủi ro kỹ thuật | Kết luận |
| --- | --- | --- | --- |
| Truth plane (series, release, observation) | Cao | Thấp (code đã có) | Làm trước |
| Reverse schema cho payload API/XLSX | Cao | Trung bình | Làm, LLM chỉ đề xuất profile |
| Knowledge objects + OCC + HITL | Cao | Trung bình | Làm, giới hạn ở CLAIM, EVENT, FORECAST, DECISION |
| Episode/analogy | Trung bình | Cao (cần ≥ 100 episode) | Chỉ schema + 10–15 episode nhập tay |
| Retrieval-as-reasoning | Trung bình | Trung bình | Làm bản có giới hạn vòng |
| Task/Capability registry | Trung bình | Thấp | Làm |
| Forecast pipeline | Trung bình | Trung bình | Làm, chấp nhận kết quả rejected |
| Skill evolution | Thấp cho POC | Cao | Tuỳ chọn tuần 8 |
| WFM, conceptual topology | Thấp cho POC | Rất cao | Không làm |
