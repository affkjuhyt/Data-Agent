"""Deep analysis skill (OP-6): methodology loaded before heavy tools — not a longer chat.

Activated when the Ask turn carries depth=deep (event.payload or purpose). Product stays
query-in-place + trust labels; no web search outside the Data Space.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# Clean-room methodology (Perplexity load_skill / Anthropic research launch ideas, our wording).
METHODOLOGY = """# Kỹ năng: phân tích sâu (deep)
whenToUse: báo cáo nhiều phần; "vì sao" cần nhiều chiều; so sánh nhiều kỳ và nhóm; payload depth=deep.
Cách làm (nạp trước khi gọi tool nặng):
1. Tách mục tiêu thành các nhánh độc lập (số / tài liệu / đóng góp) và chạy song song khi không phụ thuộc.
2. Mỗi khẳng định số phải có observation từ công cụ trước; ghi giới hạn dữ liệu (kỳ cắt, thiếu chiều).
3. Ưu tiên chỉ số chuẩn; explore_sql chỉ khi catalog không khớp.
4. Kết thúc bằng bố cục báo cáo tái lập (headline, số neo, bảng/chart, caveats) — không chat dài thêm.
CẤM: tìm web thay semantic/SQL trong Data Space; bỏ VERIFY/PRESENT gate; hỏi làm rõ lịch sự."""


def requested(payload: Mapping[str, Any] | None, purpose: str | None = None) -> bool:
    """True when the turn asked for deep analysis."""
    raw = (payload or {}).get("depth") or (payload or {}).get("mode")
    if isinstance(raw, str) and raw.strip().lower() == "deep":
        return True
    return bool(purpose and "deep" in purpose.strip().lower())


def inject(system: str, *, active: bool) -> str:
    if not active:
        return system
    return f"{system}\n\n{METHODOLOGY}"
