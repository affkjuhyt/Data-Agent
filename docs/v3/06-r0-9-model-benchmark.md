# R0-9 — Model benchmark

## Phạm vi chạy

Ngày 13/10/2026, benchmark trực tiếp qua OpenAI-compatible API trên máy dev:

| Tier | Endpoint benchmark | Model phục vụ | Vai được đo |
| --- | --- | --- | --- |
| fast | `http://localhost:8002/v1` | `agent-fast` / Qwen3-4B-AWQ | `guardrail`, `planner` |
| main | `http://localhost:8001/v1` | `agent-main` / Qwen3-14B-AWQ | `responder` |

Lệnh chạy dùng image `localhost:5001/dataagent/worker-agent:latest` vì image API
không chứa `httpx`, dependency của harness benchmark:

```bash
LLM_MAIN_BASE_URL=http://localhost:8001/v1 \
LLM_FAST_BASE_URL=http://localhost:8002/v1 \
docker run --rm --network host \
  -e LLM_MAIN_BASE_URL -e LLM_FAST_BASE_URL \
  -v "$PWD:/src" -w /src localhost:5001/dataagent/worker-agent:latest \
  python -m model.bench.run --target direct --out bench-r0-9.json
```

## Kết quả cấu hình hiện tại

48/48 request thành công, không có error hoặc preemption. Kết quả p50 ở
concurrency 1:

| Vai | Tier | Prompt tokens | Output tokens | TTFT p50 | Decode p50 | Throughput |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| guardrail | fast | 514 | 16 | 43.8 ms | 229.7 tok/s | 8.815 req/s |
| planner | fast | 161 | 400 | 36.8 ms | 223.2 tok/s | 0.560 req/s |
| responder | main | 117 | 226 | 44.6 ms | 114.8 tok/s | 0.467 req/s |

Ở concurrency 4, throughput lần lượt là 31.678, 2.123 và 1.098 req/s cho
`guardrail`, `planner` và `responder`. Hai tier dùng chung một GPU nên các
scenario được chạy tuần tự.

## Quyết định D1 và giới hạn phạm vi

Hạ tầng local hiện chỉ có hai endpoint Qwen3-4B-AWQ và Qwen3-14B-AWQ dùng
chung một GPU. Không có endpoint model lớn hơn, GPU thứ hai, hoặc LiteLLM
gateway riêng để chạy cấu hình đối chứng. Vì vậy không thể thực hiện phép so
sánh “cấu hình hiện tại / model lớn hơn cho planner và SQL” như thiết kế ban
đầu mà không bịa số liệu.

Quyết định D1 trong phạm vi hạ tầng hiện có:

- giữ `agent-fast` (Qwen3-4B-AWQ) cho `guardrail` và `planner`;
- giữ `agent-main` (Qwen3-14B-AWQ) cho `responder`;
- không chuyển planner sang main chỉ dựa trên suy đoán; việc đó cần model
  đối chứng và bộ eval chất lượng tương ứng.

Benchmark 48/48 request thành công, không có error hoặc preemption. Các số ở
trên là baseline vận hành để làm mốc hồi quy; chúng không phải kết luận rằng
Qwen3-14B tốt hơn một model lớn hơn chưa được triển khai. Khi có thêm GPU,
endpoint hoặc policy cho model ứng viên, chạy lại cùng corpus và mở một lần
đánh giá D1 mới.

Phạm vi code hiện tại có ba profile thực chạy (`guardrail`, `planner`,
`responder`). Các vai `judge`, `text_to_sql` và `orchestrator-planner` chưa có
profile tương ứng trong `model/bench/profiles.py`; chúng được ghi nhận là phần
mở rộng kế tiếp, không được giả định là đã đo trong báo cáo này.
