# Probe: hybrid numbers ∥ documents specialists

After deploy of `investigate_numbers` / `investigate_documents` and thinking on
`responder`. Run on Data Space **DS-AW** (AdventureWorksDW + `adventureworks-docs`).

## Checklist (mỗi câu hỏi)

Trên Ask / timeline / trace:

| # | Quan sát | Pass nếu |
| --- | --- | --- |
| 1 | Plan có `investigate_numbers` và `investigate_documents` | Cả hai xuất hiện, `depends_on` rỗng (song song) |
| 2 | Lead không xâu thủ công catalog→SQL→documents cho cùng câu hybrid | Không có chuỗi dài search_catalog / query_metric / search_documents thay hai chuyên gia |
| 3 | Hai bước chạy gần như cùng lúc | Status running chồng nhau hoặc hoàn thành gần nhau |
| 4 | Observation về lead là tóm tắt | Không dump raw SQL / full passages vào bước lead |
| 5 | Answer có số neo dataset + citation `[n]` | Số không chỉ nằm trong prose LLM; có dẫn tài liệu khi có hit |
| 6 | Thinking (nếu thấy latency compose) | Compose chậm hơn trước nhưng JSON/block vẫn hợp lệ (strip `<think>`) |

Câu chỉ số / chỉ tài liệu (đối chứng):

| Câu | Expect |
| --- | --- |
| Chỉ số | Có số; **không** gọi `investigate_documents` |
| Chỉ tài liệu | Có citation; **không** gọi `investigate_numbers` |

## Bộ câu thử (Ask, tiếng Việt)

Hybrid (bắt buộc hai nhánh):

1. *Internet Sales năm 2013 so với 2012 thay đổi thế nào, và trong tài liệu / công văn Adventure Works có ghi chú hay quyết định nào liên quan không?*
2. *Doanh thu nhóm Bikes năm 2013 là bao nhiêu, và chính sách hoặc hướng dẫn sản phẩm nào trong kho tài liệu nói về bảo dưỡng / lắp đặt liên quan Bikes?*
3. *So sánh doanh thu theo vùng (nếu có), rồi tìm trong tài liệu business có email hoặc quyết định giải thích biến động năm 2013 không?*

Chỉ số:

4. *Tổng Internet Sales năm 2013 là bao nhiêu?*

Chỉ tài liệu:

5. *Trong tài liệu Adventure Works, quy trình bôi trơn / bảo dưỡng nói gì?*

## Sau probe tay

```bash
# Cần AGENT_EVAL_ORG (id) + AGENT_EVAL_AS (username) từ môi trường / vault
make agent-eval AGENT_EVAL_ARGS='--limit 20 --json'
```

Ghi lại: `passed`, mean tokens, mean seconds — so với baseline trước thinking+specialist (nếu có).
Thinking thắng chỉ khi passed tăng mà seconds không ~×2 trên cùng set.

## Nếu fail

| Triệu chứng | Việc tiếp |
| --- | --- |
| Plan không gọi `investigate_*` | Siết PLANNER hoặc rút card của tool thô khi hybrid |
| Gọi tuần tự (documents depends_on numbers) | Nhắc lại rule depends_on rỗng trong prompt / ví dụ few-shot |
| Compose vỡ JSON | Kiểm tra strip `<think>` + `max_tokens` responder trên pod |
| Documents rỗng / ConnectError embed | Từ **máy bạn** và từ **pod**: `curl http://10.10.55.101:8003/v1/models` và `:8004/health` phải 200. VPN laptop ≠ TEI đang listen — nếu `:8001` OK mà `:8003`/`:8004` connection refused thì bật lại TEI trên GPU host (RUNBOOK LLM/embed) |
| `explore_sql` fail dù có metric | Plan sai: phải `query_metric` / `investigate_numbers` với mã catalog (VD `DOANH_THU_INTERNET`), không explore |
