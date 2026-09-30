from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class TaskNode:
    index: int          # vị trí trong target.tasks[]
    title: str
    input: str          # nội dung cần xử lý (chỉ có ở task độc lập)
    agent_id: str       # agent_id được planner assign trực tiếp
    agent_name: str = ""
    depend_on: list[int] = field(default_factory=list)
    output: Optional[str] = None
    conversation_id: Optional[str] = None  # từ X-Conversation-Id header
    citations: list[dict] = field(default_factory=list)
    success: bool = False
    done: bool = False
    start_ms: int = 0    # relative to executor start
    latency_ms: int = 0


def build_nodes(tasks: list[dict[str, Any]]) -> list[TaskNode]:
    """Nhận tasks[] từ 1 target, trả về TaskNode theo đúng thứ tự."""
    return [
        TaskNode(
            index=i,
            title=t.get("title", f"task_{i}"),
            input=t.get("input", ""),
            agent_id=t.get("agent_id", ""),
            depend_on=[int(d) for d in (t.get("depend_on") or []) if str(d).lstrip('-').isdigit()],
        )
        for i, t in enumerate(tasks)
    ]


def collect_context(nodes: list[TaskNode]) -> str:
    """Gom output của các task đã xong thành context string để inject vào task tiếp theo."""
    parts = []
    for n in nodes:
        if n.done and n.success and n.output:
            parts.append(f"[{n.title}]:\n{n.output}")
    return "\n\n".join(parts)
