"""Tool-grant preset system (sandbox_schema_docs.docx §1.9).

Independent of sandbox content validation (security_visitor.py/sql_guard.py
in services/sandbox-executor) — those control what code/SQL is allowed to
run once a tool call is already happening. This controls WHICH agent role
may call execute_python/execute_sql at all, and runs before TOOL_REGISTRY
dispatch in tool_executor_node so a not-granted call never reaches
Validation/Execution.

Scoped to the sandbox tools only, per the doc's own framing ("agent chuyên
trả lời câu hỏi tổng hợp có thể chỉ cần execute_sql, trong khi agent chuyên
phân tích kỹ thuật mới cần execute_python") — every other builtin tool keeps
using the existing `allowed_tools` allowlist already in tool_executor_node,
untouched by this module. DEFAULT_PRESET grants both, so agents configured
before this feature existed keep their current behavior with no migration.
"""
from dataclasses import dataclass, field

SANDBOX_TOOLS = {"execute_python", "execute_sql"}

PRESETS: dict[str, dict] = {
    "sql_readonly":    {"tools": ["execute_sql"]},
    "python_analysis": {"tools": ["execute_python"]},
    "full_data_agent":  {"tools": ["execute_sql", "execute_python"]},
}

DEFAULT_PRESET = "full_data_agent"


class ToolNotGrantedError(Exception):
    pass


@dataclass
class AgentToolConfig:
    preset: str = DEFAULT_PRESET             # tập tool cơ sở theo agent role
    tools: list[str] = field(default_factory=list)              # allowlist — bổ sung ngoài preset
    disallowed_tools: list[str] = field(default_factory=list)   # denylist — loại bỏ khỏi grant

    def resolve_granted_tools(self) -> set[str]:
        base = set(PRESETS.get(self.preset, PRESETS[DEFAULT_PRESET])["tools"])
        return (base | set(self.tools)) - set(self.disallowed_tools)


def _from_cfg(cfg: dict) -> AgentToolConfig:
    grant_cfg = cfg.get("tool_grant") or {}
    return AgentToolConfig(
        preset=grant_cfg.get("preset", DEFAULT_PRESET),
        tools=grant_cfg.get("tools", []),
        disallowed_tools=grant_cfg.get("disallowed_tools", []),
    )


def check_sandbox_tool_granted(tool_name: str, cfg: dict) -> None:
    """Raises ToolNotGrantedError if tool_name is a sandbox tool the agent's
    resolved grant doesn't include. No-op for every non-sandbox tool —
    kiểm tra grant, KHÔNG thay thế sandbox content validation (§1.3)."""
    if tool_name not in SANDBOX_TOOLS:
        return
    agent_config = _from_cfg(cfg)
    if tool_name not in agent_config.resolve_granted_tools():
        raise ToolNotGrantedError(f"Tool {tool_name} not granted")
