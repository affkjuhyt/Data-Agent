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
    "kb_search": {
        "display":  "KB Search",
        "category": "default",
        "locked":   False,
        "schema": {
            "kb_connection_id": {
                "type": "dropdown", "source": "/api/kb-connections",
                "required": True,
                "description": "Knowledge base cần tìm kiếm",
                "advanced": False,
            },
            "mode": {
                "type": "dropdown",
                "options": ["naive", "local", "global", "hybrid"],
                "default": "hybrid",
                "required": False,
                "description": "Chế độ tìm kiếm LightRAG",
                "advanced": False,
            },
            "top_k": {
                "type": "number", "default": 10,
                "min": 1, "max": 50,
                "required": False,
                "description": "Số chunks trả về tối đa",
                "advanced": False,
            },
            "max_depth": {
                "type": "number", "default": 3,
                "min": 1, "max": 5,
                "required": False,
                "description": "Độ sâu graph traversal",
                "advanced": True,
                "depends_on": {"mode": ["global", "hybrid"]},
            },
        },
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
                "description": "Danh sách tool được phép gọi",
                "advanced": False,
            },
        },
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
                "description": "Hằng số k trong công thức RRF",
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
                "description": "Số chunks giữ lại sau rerank",
                "advanced": False,
            },
        },
    },
    "responder": {
        "display":  "Synthesizer",
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
                "description": "System prompt định nghĩa vai trò của agent",
                "advanced": False,
            },
            "temperature": {
                "type": "slider", "default": 0.2,
                "min": 0.0, "max": 2.0,
                "required": False,
                "description": "Độ ngẫu nhiên của câu trả lời",
                "advanced": False,
            },
            "max_tokens": {
                "type": "number", "default": 1000,
                "min": 100, "max": 8000,
                "required": False,
                "description": "Độ dài tối đa câu trả lời",
                "advanced": False,
            },
        },
    },
    "human_approval": {
        "display":  "Human Approval",
        "category": "optional",
        "locked":   False,
        "schema": {
            "timeout_minutes": {
                "type": "number", "default": 30,
                "min": 1, "max": 1440,
                "required": False,
                "description": "Thời gian chờ duyệt tối đa (phút)",
                "advanced": False,
            },
        },
    },
    "condition": {
        "display":  "Condition",
        "category": "optional",
        "locked":   False,
        "schema": {
            "expression": {
                "type": "text",
                "required": True,
                "description": "Biểu thức điều kiện (Python expression)",
                "advanced": False,
            },
            "branches": {
                "type": "list",
                "required": True,
                "description": "Danh sách branch khi condition đúng/sai",
                "advanced": False,
            },
        },
    },
    "loop": {
        "display":  "Loop",
        "category": "optional",
        "locked":   False,
        "schema": {
            "max_iterations": {
                "type": "number", "default": 3,
                "min": 1, "max": 10,
                "required": False,
                "description": "Số lần lặp tối đa",
                "advanced": False,
            },
        },
    },
    "notification": {
        "display":  "Notification",
        "category": "optional",
        "locked":   False,
        "schema": {
            "event_type": {
                "type": "text",
                "required": True,
                "description": "Loại sự kiện gửi đi",
                "advanced": False,
            },
            "payload_template": {
                "type": "json",
                "required": False,
                "description": "Template JSON cho payload",
                "advanced": True,
            },
        },
    },
}
