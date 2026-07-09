NODE_REGISTRY = {
    "guardrail": {
        "display":  "Guardrail",
        "category": "default",
        "locked":   True,
        "schema":   {},
    },
    "planner": {
        "display":  "Router",
        "category": "default",
        "locked":   False,
        "schema": {
            "top_k_tools": {
                "type": "number", "default": 5,
                "min": 1, "max": 20,
                "required": False,
                "description": "Số tool tối đa Planner được chọn mỗi lượt",
                "advanced": False,
            },
            "similarity_threshold": {
                "type": "number", "default": 0.7,
                "min": 0.0, "max": 1.0,
                "required": False,
                "description": "Ngưỡng similarity để Planner quyết định dùng tool",
                "advanced": True,
            },
        },
    },
    "metadata_injector": {
        "display":  "Metadata Injector",
        "category": "default",
        "locked":   False,
        "schema": {
            "metadataKv": {
                "type": "text",
                "required": False,
                "description": "Cặp key-value tĩnh chèn vào ngữ cảnh Planner/Responder, dạng \"key1=value1,key2=value2\"",
                "advanced": False,
            },
        },
    },
    "kb_search": {
        "display":  "KB Search",
        "category": "default",
        "locked":   False,
        "schema":   {},
    },
    "mcp": {
        "display":  "MCP Tool",
        "category": "default",
        "locked":   False,
        "schema": {
            "mcp_id": {
                "type": "dropdown", "source": "/api/mcp",
                "required": True,
                "description": "MCP server kết nối",
                "advanced": False,
            },
            "allowed_tools": {
                "type": "multiselect", "source": "mcp.capabilities",
                "required": False,
                "description": "Danh sách tool được phép gọi. Để trống = cho phép tất cả",
                "advanced": False,
            },
        },
    },
    "tool_executor": {
        "display":  "Tool Executor",
        "category": "default",
        "locked":   False,
        "schema": {
            "allowedBuiltinTools": {
                "type": "multiselect",
                "options": [
                    "text_to_sql", "text_to_pandas",
                    "file_read", "grep", "glob",
                    "file_write", "file_edit",
                    "bash",
                    "web_search", "wikipedia", "calculator",
                    "delegate_to_agent",
                ],
                "required": True,
                "description": "Các tool mà agent này được phép sử dụng",
                "advanced": False,
            },
            "dbUrl": {
                "type": "password",
                "required": False,
                "description": "DB connection string cho text_to_sql (postgresql://...)",
                "advanced": False,
            },
            "schemaHint": {
                "type": "textarea",
                "required": False,
                "description": "Schema hint để LLM sinh SQL chính xác hơn",
                "advanced": True,
            },
            "defaultFilePath": {
                "type": "text",
                "required": False,
                "description": "File mặc định cho text_to_pandas",
                "advanced": False,
            },
            "pandasAllowedDir": {
                "type": "text",
                "required": False,
                "description": "Thư mục được phép đọc file cho text_to_pandas",
                "advanced": True,
            },
            "fileReadAllowedDir": {
                "type": "text",
                "required": False,
                "description": "Thư mục được phép đọc file cho file_read",
                "advanced": True,
            },
            "grepAllowedDir": {
                "type": "text",
                "required": False,
                "description": "Thư mục được phép tìm kiếm cho grep/glob",
                "advanced": True,
            },
            "writeAllowedDir": {
                "type": "text",
                "required": False,
                "description": "Thư mục được phép ghi file cho file_write/file_edit",
                "advanced": True,
            },
            "bashEnabled": {
                "type": "boolean",
                "default": False,
                "required": False,
                "description": "Cho phép thực thi lệnh shell (nguy hiểm, chỉ bật khi cần)",
                "advanced": False,
            },
            "bashCwd": {
                "type": "text",
                "required": False,
                "description": "Working directory cho bash",
                "advanced": True,
            },
        },
    },
    "judge": {
        "display":  "Judge",
        "category": "default",
        "locked":   False,
        "schema": {
            "max_iterations": {
                "type": "number", "default": 3,
                "min": 1, "max": 10,
                "required": False,
                "description": "Số lần retry tối đa khi judge FAIL. Đạt giới hạn → trả kết quả hiện tại cho user",
                "advanced": False,
            },
            "model_id": {
                "type": "dropdown", "source": "/api/llm-providers?type=chat",
                "required": False,
                "description": "Model dùng cho judge. Mặc định: model alias 'judge' (qwen3-8b)",
                "advanced": True,
            },
        },
    },
    "human_approval": {
        "display":  "Human Approval",
        "category": "default",
        "locked":   False,
        "schema": {},
    },
    "rrf_ranking": {
        "display":  "RRF Ranking",
        "category": "default",
        "locked":   False,
        "schema": {
            "k": {
                "type": "number", "default": 60,
                "min": 1, "max": 200,
                "required": False,
                "description": "Hằng số k trong công thức RRF = 1/(k + rank). Cao hơn → ít penalty cho rank thấp",
                "advanced": True,
            },
            "top_n": {
                "type": "number", "default": 20,
                "min": 1, "max": 100,
                "required": False,
                "description": "Số kết quả giữ lại sau khi rank",
                "advanced": False,
            },
        },
    },
    "reranker": {
        "display":  "Reranker",
        "category": "default",
        "locked":   False,
        "schema": {
            "top_n": {
                "type": "number", "default": 5,
                "min": 1, "max": 20,
                "required": False,
                "description": "Số chunks giữ lại sau rerank để đưa vào Responder",
                "advanced": False,
            },
        },
    },
    "responder": {
        "display":  "Responder",
        "category": "default",
        "locked":   False,
        "schema": {
            "model_id": {
                "type": "dropdown", "source": "/api/llm-providers?type=chat",
                "required": True,
                "description": "LLM dùng để tổng hợp câu trả lời",
                "advanced": False,
            },
            "system_prompt_id": {
                "type": "dropdown", "source": "/api/system-prompts",
                "required": True,
                "description": "System prompt định nghĩa vai trò và hành vi của agent",
                "advanced": False,
            },
            "temperature": {
                "type": "slider", "default": 0.2,
                "min": 0.0, "max": 2.0,
                "required": False,
                "description": "Độ ngẫu nhiên của câu trả lời. 0=deterministic, 2=sáng tạo nhất",
                "advanced": False,
            },
            "max_tokens": {
                "type": "number", "default": 1000,
                "min": 100, "max": 8000,
                "required": False,
                "description": "Độ dài tối đa câu trả lời (tokens)",
                "advanced": False,
            },
        },
    },
}

# Các tính năng không phải node trên canvas:
#
# human_approval — middleware, chạy trước mcp_node
#   config: human_approval_enabled, mcp_require_approval[]
#
# loop — middleware, chạy sau reranker
#   config: loop_enabled, loop_max_iterations
#
# rewrite_message — middleware, chạy trước planner
#   config: rewrite_enabled
#
# suggestion — background job, chạy song song
#   config: suggestion_enabled
#
# condition — edge property trong flow_edges MongoDB
#   field: "condition": "no_results" | "has_results" | "mcp_failed" | "low_confidence"
#
# notification — bỏ khỏi scope, chưa có use case đủ rõ
