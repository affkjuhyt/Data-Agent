from typing import Any

TOOL_DEFINITIONS: dict[str, dict[str, Any]] = {
    "text_to_sql": {
        "category":    "data",
        "description": "Chuyển câu hỏi tự nhiên thành SQL và thực thi trên database. Dùng khi cần truy vấn dữ liệu có cấu trúc từ DB.",
    },
    "text_to_pandas": {
        "category":    "data",
        "description": "Phân tích dữ liệu file CSV/Excel bằng Pandas. Dùng khi cần thống kê, tổng hợp từ file dữ liệu.",
    },
    "execute_python": {
        "category":    "sandbox",
        "description": "Thực thi code Python (pandas/numpy/plotly/matplotlib) trong Docker sandbox cô lập, không network, có resource limit. Dùng khi cần tự viết logic phân tích tùy biến.",
    },
    "execute_sql": {
        "category":    "sandbox",
        "description": "Thực thi câu SQL (chỉ SELECT) trên Snowflake qua service account read-only, kết quả giới hạn 10.000 dòng. Dùng khi đã có sẵn câu SQL cần chạy trực tiếp.",
    },
    "file_read": {
        "category":    "filesystem",
        "description": "Đọc nội dung file text từ hệ thống (log, tài liệu, config).",
    },
    "grep": {
        "category":    "filesystem",
        "description": "Tìm kiếm regex pattern trong các file. Trả về dòng khớp kèm tên file và số dòng.",
    },
    "glob": {
        "category":    "filesystem",
        "description": "Liệt kê file/folder theo glob pattern. Dùng để khám phá cấu trúc thư mục.",
    },
    "file_write": {
        "category":    "filesystem-write",
        "description": "Ghi nội dung vào file. Tạo mới hoặc ghi đè file hiện tại.",
    },
    "file_edit": {
        "category":    "filesystem-write",
        "description": "Chỉnh sửa file bằng cách thay thế chuỗi cụ thể. Dùng khi cần sửa chính xác một đoạn text trong file.",
    },
    "bash": {
        "category":    "shell",
        "description": "Thực thi lệnh shell. Dùng khi cần chạy script, CLI tool, hoặc tác vụ hệ thống.",
    },
    "web_search": {
        "category":    "langchain",
        "description": "Tìm kiếm thông tin mới nhất trên web (DuckDuckGo). PHẢI dùng khi user đề cập 'web', 'internet', 'tìm kiếm online', 'tin tức', 'mới nhất'. KHÔNG dùng kb_search thay thế.",
    },
    "wikipedia": {
        "category":    "langchain",
        "description": "Tra cứu thông tin bách khoa từ Wikipedia. Dùng khi cần định nghĩa, lịch sử, thông tin nền.",
    },
    "calculator": {
        "category":    "langchain",
        "description": "Tính toán số học, công thức, biểu thức toán học. PHẢI dùng cho MỌI phép tính kể cả đơn giản như 1+1, 2*3. KHÔNG tự tính trong đầu.",
    },
    "delegate_to_agent": {
        "category":    "agent",
        "description": "Ủy quyền sub-task cho agent chuyên biệt khác trong team. Trả về kết quả từ agent đó.",
    },
}

from typing import Any
from nodes.sandbox.tool_executor.node import tool_executor_node  # noqa: E402

__all__ = ["tool_executor_node", "TOOL_DEFINITIONS"]
