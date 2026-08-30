import litellm

# Tắt LiteLLM's built-in Langfuse integration — guardrail/input_refine/suggestion/
# planner/summary gọi litellm.completion()/acompletion() trực tiếp và được
# bọc bằng @observability.observe() (xem observability.py). Nếu không tắt, litellm
# tự tạo thêm 1 observation "litellm-acompletion" riêng biệt, không nối vào trace
# hiện tại (khác cơ chế OTEL context propagation của Langfuse v3) → trùng/nhiễu trace.
litellm.success_callback = [cb for cb in litellm.success_callback if cb != "langfuse"]
litellm.failure_callback = [cb for cb in litellm.failure_callback if cb != "langfuse"]

import uvicorn
from app import create_app

app = create_app()

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8003, reload=True)
