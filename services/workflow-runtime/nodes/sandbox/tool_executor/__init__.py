TOOL_DEFINITIONS: dict[str, dict] = {
    "text_to_sql": {
        "category":    "data",
        "description": "Chuyển câu hỏi tự nhiên thành SQL và thực thi trên database. Dùng khi cần truy vấn dữ liệu có cấu trúc từ DB.",
    },
    "text_to_pandas": {
        "category":    "data",
        "description": "Phân tích dữ liệu file CSV/Excel bằng Pandas. Dùng khi cần thống kê, tổng hợp từ file dữ liệu.",
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
        "description": "Tìm kiếm thông tin mới nhất trên web (DuckDuckGo). Dùng khi cần tin tức, thông tin cập nhật ngoài KB.",
    },
    "wikipedia": {
        "category":    "langchain",
        "description": "Tra cứu thông tin bách khoa từ Wikipedia. Dùng khi cần định nghĩa, lịch sử, thông tin nền.",
    },
    "calculator": {
        "category":    "langchain",
        "description": "Tính toán số học, công thức, biểu thức toán học. Dùng khi cần kết quả chính xác.",
    },
    "delegate_to_agent": {
        "category":    "agent",
        "description": "Ủy quyền sub-task cho agent chuyên biệt khác trong team. Trả về kết quả từ agent đó.",
    },
}

from nodes.sandbox.tool_executor.node import tool_executor_node  # noqa: E402

__all__ = ["tool_executor_node", "TOOL_DEFINITIONS"]
