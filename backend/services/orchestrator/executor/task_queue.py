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


def build_rounds(tasks: list[dict[str, Any]]) -> tuple[list[list[TaskNode]], list[dict[str, Any]]]:
    """
    Nhận tasks[] từ 1 target, trả về (rounds, stuck_tasks).
    stuck_tasks không rỗng khi phát hiện circular/broken dependency.
    """
    nodes = [
        TaskNode(
            index=i,
            title=t.get("title", f"task_{i}"),
            input=t.get("input", ""),
            agent_id=t.get("agent_id", ""),
            depend_on=[int(d) for d in (t.get("depend_on") or []) if str(d).lstrip('-').isdigit()],
        )
        for i, t in enumerate(tasks)
    ]

    done_set: set[int] = set()
    rounds: list[list[TaskNode]] = []
    stuck_tasks: list[dict[str, Any]] = []

    remaining = list(range(len(nodes)))
    while remaining:
        ready = [i for i in remaining if all(d in done_set for d in nodes[i].depend_on)]
        if not ready:
            stuck_tasks = [{"index": nodes[i].index, "title": nodes[i].title, "depend_on": nodes[i].depend_on} for i in remaining]
            logger.warning("task_queue | dependency cycle or broken deps, forcing remaining tasks: %s", stuck_tasks)
            ready = remaining
        rounds.append([nodes[i] for i in ready])
        done_set.update(ready)
        remaining = [i for i in remaining if i not in done_set]

    return rounds, stuck_tasks


def collect_context(nodes: list[TaskNode]) -> str:
    """Gom output của các task đã xong thành context string để inject vào task tiếp theo."""
    parts = []
    for n in nodes:
        if n.done and n.success and n.output:
            parts.append(f"[{n.title}]:\n{n.output}")
    return "\n\n".join(parts)
