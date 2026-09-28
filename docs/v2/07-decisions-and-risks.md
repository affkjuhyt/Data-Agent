# 07. Quyết định, ADR, rủi ro

## 1. Quyết định còn chờ

Bảng chỉ chứa câu **chưa** có câu trả lời. Câu nào được chốt thì xoá khỏi bảng trong cùng commit
làm theo nó: kết quả là quyết định kỹ thuật thì ghi thành ADR ở mục 2; là việc quản lý (người, hạn,
phạm vi) thì ghi vào [05](05-implementation-plan.md). Số hiệu D không đánh lại để các file khác
không trỏ nhầm.

Hiện không còn câu nào chờ.

Đã chốt và xoá khỏi bảng:

- 27/09/2026 (lần 1): D1, D11 (tuyển 2 senior, POC 1 tháng, nghiệm thu 27/10: 05); D5 (ClickHouse:
  00, ADR-V2-16); D8 (raw bất biến: 00, ADR-V2-21); D9 (forecast ngoài POC nên không còn áp dụng).
- 27/09/2026 (lần 2): D2 (giá trong nước **làm trong POC** để trình diễn cho người dùng Việt Nam: 05,
  06 mục 7); D4 (lấy lõi số và các sửa liên quan từ `feat/add-task` sang nhánh hiện tại: P0-01);
  D6 (tên deployable: ADR-V2-22); D7 (authority và maker/checker: ADR-V2-23); D10 (chưa cần tin
  tức: 05 mục 6); D12 (nguồn fixture cho bước 1–7: 06 mục 6); D13 (chính sách licence: ADR-V2-24).
- 27/09/2026 (lần 3): D3 (giá vàng thế giới theo ngày: crawl web, dự phòng tải tay, ghi vào báo cáo:
  ADR-V2-25, 04 mục 7); D14 (thêm 1 frontend từ W2: 05 mục 1).

## 2. ADR

Tiếp nối ADR-V2-01…08 của thiết kế gốc. ADR mâu thuẫn với 00 thì 00 thắng.

| ADR | Quyết định | Lý do |
| --- | --- | --- |
| ADR-V2-09 | Dữ liệu có cấu trúc không đi đường chunk/embedding để phân tích số; mọi con số trả lời đi từ effective observation qua operator | Bất biến 3 của 00. Sự cố d116062 (27/09): summary chunk hỏng do LLM lỗi ở 32% chunk làm bảng đúng tụt hạng rerank; câu trả lời số phụ thuộc chất lượng một bước tóm tắt là quá mong manh |
| ADR-V2-10 | Observation thuộc Truth plane; claim/event chỉ tham chiếu `(series_key, event_time, release_id)` | Tránh hai nơi lưu cùng số |
| ADR-V2-11 | Vertical là cấu hình (nguồn, entity, series, profile, luật, capability composite, golden set) | Mục tiêu "mở rộng bằng cấu hình" |
| ADR-V2-12 | LLM đề xuất mapping/profile khi thêm nguồn hoặc khi drift; release thường chạy tất định | Bất biến 5; tái lập được |
| ADR-V2-13 | Chỉ ghi observation khi giá trị khác effective version hiện hành | File nguồn chứa toàn bộ lịch sử |
| ADR-V2-14 | Point-in-time dùng `available_at`, không dùng `event_time` | EIA công bố spot theo tuần; forecast và as-of không được thấy trước |
| ADR-V2-15 | Postgres là nguồn sự thật cho tri thức và governance; Neo4j, Qdrant là bản chiếu/index | OCC, RLS, giao dịch |
| ADR-V2-16 | Candidate ở Postgres `ingest`; chỉ bản đã duyệt vào ClickHouse; agent chỉ đọc ClickHouse | Agent không bao giờ thấy số chưa qua DQ/HITL |
| ADR-V2-17 | Chỉ `SOURCE_CONFLICT` và `UNKNOWN` mở ReviewCase mặc định | Chặn bão ReviewCase giả (UC06) |
| ADR-V2-18 | Alert gom theo sự kiện gốc, dedup `(rule, entity, window)`, có cooldown | 32 đợt giảm ≥5%/5 phiên trong 3 năm (04) |
| ADR-V2-19 | Entity khác benchmark là entity khác (`BRENT_SPOT` ≠ `BRENT_FUTURES_M1`), nối bằng `SAME_UNDERLYING` | Spot 130,80 vs futures ~107 ngày 15/9/2026 |
| ADR-V2-20 | Series đổi định nghĩa là series mới, nối bằng `SUCCEEDS` | RON95-III → E10RON95-III từ 16/6/2026 |
| ADR-V2-21 | Re-crawl chỉ thay **bản tìm kiếm** của KB; artifact (MinIO theo SHA-256), release, observation không bao giờ bị xoá | Bất biến 2 của 00; Phase A (22/09) đang xoá vết tài liệu cũ, sửa ở S2-02 |
| ADR-V2-22 | Tên: deployable `indicator-api` (Observation/Knowledge/Governance API, sau này thêm MCP Gateway), `forecast-worker`; consumer trong `platform_workers`: `series_extractor`, `semantic_extractor`, `detector` | Chung mọi miền (ADR-V2-11); chốt D6 |
| ADR-V2-23 | Authority: người duyệt được chọn theo (entity, nguồn, loại case); research là maker, người mang vai trò `REVIEWER` là checker; hệ thống chặn tự duyệt | Chốt D7 |
| ADR-V2-25 | Giá vàng thế giới theo ngày lấy bằng crawl web: nguồn chính là file JSON công khai trên website LBMA (`prices.lbma.org.uk/json/gold_pm.json`, LBMA Gold Price PM, USD/GBP/EUR, từ 1968); nếu crawl hỏng thì tải tay qua Document Hub. `use_rights = internal_analysis`; báo cáo POC và mọi màn hiển thị ghi rõ nguồn và điều kiện sử dụng (licence IBA bắt buộc khi dùng để định giá hoặc phân phối lại) | Chốt D3; không có nguồn miễn phí sạch licence (04 mục 7) |
| ADR-V2-24 | Nguồn chưa đọc điều khoản ở `use_rights = pending_review` (IMF, JODI, GPR, WGC, Cboe, SJC/PNJ/DOJI, API vàng thương mại): nạp và dùng trong hệ thống, không đưa vào báo cáo/tài liệu gửi ra ngoài tới khi đọc xong điều khoản | Chốt D13 |
| ADR-V2-36 | `indicator_api` là một deployable nhưng mã chia theo domain: `api/` (router FastAPI), `domains/{registry, observation, knowledge, governance, query, capability}` (model và luật, không import FastAPI hay client kho), `adapters/{postgres, clickhouse, kafka, minio}`. Không thêm lớp `application/` riêng trong POC. `libs/indicator` là thư viện tính toán thuần (Quantity, Period, formula, threshold, verifier), không sở hữu I/O, lưu trữ hay governance | Review cấu trúc 27/09: một deployable là đúng cho 1 tháng, nhưng viết phẳng thì thành monolith; chia domain từ đầu để tách deployable sau POC mà không viết lại |
| ADR-V2-37 | Consumer V2 trong `platform_workers` chỉ làm: đọc message → parse envelope → gọi handler của domain → commit offset. Logic nằm ở `libs/` hoặc `indicator_api/domains`. Không thêm consumer ngoài ba consumer của ADR-V2-22; DQ và conflict chạy trong module governance của `indicator-api` | Mỗi consumer kéo theo topic, ACL, manifest; tách container sau POC không phải sửa logic |
| ADR-V2-38 | Ranh giới import được `.importlinter` chặn trong CI: `libs.indicator` không import `services`, `libs.database_connector`, `libs.ingestion` (contract `indicator-is-pure`); package V2 không import `services.kb_structured_data` hay `libs.database_connector.mongo_connector` (contract `v2-data-boundary`). Mỗi package V2 mới được thêm vào `source_modules` của `v2-data-boundary` trong cùng commit tạo ra nó | ADR-V2-09, ADR-V2-34 chỉ nằm trên giấy thì sẽ bị vi phạm dần; `workflow_runtime` không phải package nên không kiểm được bằng cách này, giữ bằng review và bất biến 7 |
| ADR-V2-39 | Resolution chỉ trả `entity_id`/`series_key` có trong `registry` (qua alias). Không khớp → lỗi có cấu trúc `UNRESOLVED_ENTITY` hoặc hỏi lại, không đoán. QuerySpec chỉ nhận `series_key` đã resolve, không nhận tên tự do như "BRENT" | Vấn đề semantic identity (00 mục 13), bất biến 7; "BRENT" một mình là spot hay futures (ADR-V2-19). Test bắt buộc: alias lạ bị từ chối |
| ADR-V2-40 | Contract test là cổng CI: envelope Pydantic của mọi topic V2 (03 mục 13), OpenAPI của `indicator-api` (file commit, CI so với bản sinh ra), schema tool MCP. Đổi field mà không đổi version → CI đỏ. Test V2 mới đặt theo domain (`tests/unit/<domain>/`, `tests/contract/`); 147 file test hiện có đã chia theo service (`tests/unit/<service>/`, mỗi suite chạy trong image của service đó) và không di chuyển tiếp | Gắn P0-09: hai senior và hai frontend làm song song trên cùng hợp đồng; di chuyển test cũ là churn không đổi hành vi |
| ADR-V2-41 | Tên: giữ `gold` (tầng medallion: `libs/ingestion/gold`, `kb-promote-gold`, topic `kb.promote.gold`) và `kb_conflict_resolution` (xung đột tài liệu KB). Phần V2 đặt tên không trùng: bảng `governance.observation_conflict`, module `domains/governance/conflict`. `libs/ingestion/entity_registry.py` đổi thành `kb_entity_registry.py` trong S1-03 (collection Qdrant `entity_registry` giữ tên) để không lẫn với `registry.entity` | Không file mã nào mang tên hàng hoá (quy tắc nhóm), nên `gold` trong mã luôn là tầng; đổi tên chạm 93 file, topic, manifest và màn Data Layers, cần PR riêng (CLAUDE.md mục 3), không đáng giữa POC |
| ADR-V2-42 | Key raw giữ `raw/{source_id}/{sha256}` (ADR-V2-34), không thêm `tenant_id` | `KBSource.source_id` là UUID toàn cục và thuộc đúng một tenant, nên hai tenant không đè lên nhau; cách ly tenant nằm ở Postgres (RLS, 10 mục 2.2) và quyền của service với bucket, không nằm ở prefix |

### Phụ lục: Đầu vào nghiên cứu

Lý do phía sau các ADR về tri thức, capability và skill: đã đọc gì, lấy gì, không lấy gì. Tóm tắt dựa
trên abstract và gist gốc, đọc ngày 27/09/2026.

**LLM-Wiki (arXiv 2605.25480) và mẫu LLM Wiki của Karpathy.** Biên dịch tài liệu thành trang wiki có
liên kết hai chiều; agent search → read → đi theo link → tự đánh giá đủ bằng chứng; "Error Book" lưu lỗi
để tự sửa. Karpathy tách ba lớp (nguồn thô bất biến, wiki do LLM viết, schema quy ước), ba thao tác
(ingest, query, lint), và cảnh báo LLM tự đưa bản tổng hợp sai của chính nó vào trang sau, lỗi tự khuếch
đại. Paper báo cáo tăng 2,0–8,1 F1 trên HotpotQA, MuSiQue, 2WikiMultiHopQA: câu hỏi văn bản nhiều bước,
không phải tra một ô trong bảng số (vì vậy số đi đường có cấu trúc, ADR-V2-09).

| Lấy | Hiện thực | Việc (05) |
| --- | --- | --- |
| Ingest biên dịch tri thức | `semantic_extractor` chạy sau Gold, sinh proposal CLAIM/EVENT có provenance tới passage (page/bbox) | S4-02 |
| Lint | CronJob kiểm stale / conflict / thiếu evidence; tái dùng `kb_conflict_resolution` cho mâu thuẫn văn bản | S6-02 |
| Query search→read→traverse | Capability trên LightRAG + Neo4j có sẵn, replan tối đa 2 vòng, dừng khi mọi EvidenceRequirement bắt buộc đã thoả | S9-02, S10-01 |
| Error Book | Bảng `experience_entry` ghi lỗi từ eval và trace đã phân loại | S13-01 |

Không lấy: trang wiki markdown tự do do LLM viết làm nguồn trả lời. Câu trả lời chỉ trích passage gốc
hoặc observation; knowledge object là chỉ mục có cấu trúc để tìm và nối, không phải bằng chứng. Đây là
cách chặn đúng rủi ro Karpathy nêu (cùng lý do với "agent chỉ đề xuất", ADR-V2-02 của thiết kế gốc).

**WFM, Wiki Foundation Model (arXiv 2609.18182).** Mô hình học trên Wiki Graph (context dày + liên kết
topo), message passing có attention theo truy vấn, NCCL cho huấn luyện phân tán nhanh 10,5 lần; abstract
không nêu dữ liệu huấn luyện và số so sánh. Không lấy (giữ ADR-V2-06 của thiết kế gốc): cần huấn luyện
phân tán nhiều GPU và nhãn truy vấn → evidence, trong khi cụm chỉ có một A100 40 GB đã dùng 33 GB cho
chat. Việc duy nhất trong POC: lưu liên kết có kiểu (`SUPPORTS`, `AFFECTS`, `SUPERSEDES`, `MENTIONS`) và
nhãn evidence vàng trong golden set, để sau này có dữ liệu thử nếu quyết định nghiên cứu.

**WikiSkill (arXiv 2608.27454).** Trải nghiệm thực thi → wiki bền vững → skill thực thi được; skill
chuyển được giữa model, model nhỏ có skill vượt model lớn không skill; bài ít bàn chi phí và trường hợp
skill làm tệ hơn. Điều kiện là lịch sử thực thi đủ nhiều và bộ eval ổn định, mà hiện có 0 case dầu/vàng.
Lấy tối thiểu: skill = file prompt/procedure có version trong repo, gắn `compatible_models`, chỉ deploy
qua eval gate (bất biến 12, S13-01 ngoài POC). Thí nghiệm khi có dữ liệu: chọn 1 loại lỗi xuất hiện ≥ 10
lần trong Error Book, viết một skill, so trước/sau trên cùng golden set. Không lấy: "Skill Proposer" tự
động trước khi có dữ liệu.

**Graph Engineering (arXiv 2608.21156).** Task graph (phân rã, phụ thuộc), Capability graph (ghép năng lực
với công cụ), Runtime-State graph (trạng thái bền), phục hồi chọn lọc. Repo đã có ba mảnh dưới tên khác:
planner DPTS + executor DAG là task graph, checkpointer LangGraph là runtime state, `TOOL_REGISTRY` là
capability graph dạng tĩnh. Không lấy: một "graph service" mới. Lấy:

| Mảnh | Việc |
| --- | --- |
| Task graph | Planner xuất JSON có kiểu: `node_id`, `capability`, `inputs`, `depends_on`, `evidence_requirements` |
| Capability graph | Bảng capability (id, provider, input/output schema, quyền, SLO, chi phí) thay `TOOL_REGISTRY` (S9-01); MCP Gateway sau này đọc cùng bảng |
| Runtime state | Ghi trạng thái từng node vào bảng `task_run`; test kill worker giữa job rồi resume (Tier B, 06 mục 5) |
| Selective recovery | Node lỗi thì chỉ chạy lại các node downstream theo `depends_on` |

## 3. Rủi ro

| Rủi ro | Dấu hiệu sớm | Giảm thiểu | Theo dõi |
| --- | --- | --- | --- |
| Hạn 1 tháng không có đệm; thiếu ~2,9 tuần-người kể cả khi đã thêm 1 frontend | Mốc C1 (09/10) hoặc C2 (16/10) trễ quá 2 ngày | Thứ tự cắt 05 mục 5 và 09 mục 7 | PM |
| Frontend thứ hai vào muộn hơn 05/10 | Chưa có người ngày 05/10 | FE1 giữ FE-00 và Review Inbox; màn Việt Nam dựng tối giản từ ValueCard/SeriesChart | PM |
| Senior vào muộn hoặc cần thời gian làm quen dài | Sau 05/10 chưa có người, hoặc P0-09 chưa xong 02/10 | Hợp đồng dữ liệu P0-09 do BE hiện có viết trước nếu cần; cắt theo 05 mục 5 | PM |
| ClickHouse mới cho đội | S1-02 chưa xong 02/10 | Test as-of với bản ghi chưa gộp ngay tuần 1; tách database khỏi Langfuse | SR2 |
| Xung đột giả tràn hộp duyệt | > 5 ReviewCase/1.000 observation/tuần | ADR-V2-17; quan hệ `SAME_UNDERLYING`; khai báo regime | BE |
| Bão alert | > 1 alert/sự kiện gốc khi replay | ADR-V2-18; test replay release 23/09 | BE |
| Nguồn chặn bot (OPEC, SJC) | Headless cũng 403 | Tải tay có metadata; nguồn thứ hai (PNJ, DOJI); không để bước demo phụ thuộc | DE |
| Thông cáo Bộ Công Thương đổi cách trình bày | Extractor không tìm thấy giá thế giới bình quân | Proposal thiếu trường → ReviewCase, research nhập tay; theo dõi tỷ lệ bài trích đủ trường | BE1 |
| Giá vàng LBMA lấy bằng crawl website, chưa có licence IBA | LBMA đổi/chặn endpoint JSON; hoặc cần dùng số để định giá/phân phối | Tải tay qua Document Hub (ADR-V2-25); báo cáo POC có phụ lục nguồn và điều kiện sử dụng; production mua Usage Licence (8.000 USD/năm) | DE, PM |
| Nguồn ngừng hoặc đổi địa chỉ | Không có release quá SLA | `source.sla.breached`; `follow_link_pattern` | DE |
| EIA chỉ công bố tuần | Người dùng hỏi "hôm nay" | Luôn trả kèm as-of và ngày công bố; không nội suy | BE |
| Licence chưa rõ | `pending_review` tới W6 | D13; không đưa vào tài liệu gửi hội đồng | RS |
| Lỗi summarization LLM làm hỏng retrieval (~32% khi chạy song song) | Cảnh báo fallback trong log Silver | Fallback theo bảng đã sửa (d116062); giảm `SILVER_SUMMARY_CONCURRENCY` nếu tỷ lệ vẫn cao | BE |
| Ổ `/data` (1 TB, giữ mọi state từ 27/09; 21 % đã dùng ngày 28/09) đầy dần vì image mới cộng dồn; kubelet dọn image ở 85 %, đuổi pod ở 95 % | `/data` trên 70 % (`disk-alert.sh` báo từ 80 %) | ClickHouse và snapshot nhỏ (< 2 GB); prune sau build; không GC registry với `--delete-untagged`; alert `DiskFillingUp` (11 mục 7) | Ops |
| GPU dùng chung 33,2/40 GB | TTFT > 2 giây khi extractor chạy | `semantic_extractor` chạy lô, đồng thời 1 | Ops |
| Cổng lint/type đỏ sẵn che lỗi mới | | So worktree baseline mỗi commit | BE |
| Nhánh sống quá lâu | Nhánh tính năng > 1 tuần chưa merge | Merge `main` hằng tuần; demo chạy từ `main` | Trưởng nhóm |
