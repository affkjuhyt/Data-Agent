from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from planner.dpts.plan import Plan, Target
from schemas import TargetResult, TaskResult
from .task_queue import TaskNode, build_rounds, collect_context
from .agent_client import call_agent

logger = logging.getLogger(__name__)


@dataclass
class ExecutionResult:
    plan_id: str
    target_results: list[TargetResult] = field(default_factory=list)
    all_outputs: list[str] = field(default_factory=list)   # output mỗi target theo thứ tự
    failed_node_id: str | None = None                       # node_id đầu tiên fail (để report planner)
    success: bool = True


async def _run_task(node: TaskNode, prompt: str, user_id: str, session_id: str | None = None) -> TaskNode:
    """Chạy 1 task, ghi output vào node, không throw."""
    try:
        output, conv_id = await call_agent(
            agent_id=node.agent_id,
            prompt=prompt,
            user_id=user_id,
            session_id=session_id,
        )
        node.output = output
        node.conversation_id = conv_id
        node.success = True
    except Exception as e:
        logger.warning("task failed | '%s': %s", node.title, e)
        node.output = str(e)
        node.success = False
    node.done = True
    return node


async def _execute_target(
    target: Target,
    prior_context: str,
    user_id: str,
    session_id: str | None = None,
) -> tuple[TargetResult, bool]:
    """
    Chạy tất cả tasks trong 1 target theo rounds (song song trong round, tuần tự giữa rounds).
    Trả về (TargetResult, success).
    """
    rounds = build_rounds([t.model_dump() for t in target.tasks])
    all_nodes: list[TaskNode] = []
    task_results: list[TaskResult] = []
    target_failed = False
    failed_task_title: str | None = None

    for round_nodes in rounds:
        # Build prompt cho từng task trong round:
        # target description + context từ prior targets + output các task đã xong trong cùng target
        intra_context = collect_context(all_nodes)

        async def run_one(node: TaskNode) -> TaskNode:
            parts = []
            if prior_context:
                parts.append(f"## Kết quả từ các bước trước\n{prior_context}")
            if intra_context:
                parts.append(f"## Kết quả từ các task trước trong bước này\n{intra_context}")
            parts.append(f"## Nhiệm vụ\n{target.description}\n\nTask: {node.title}")
            prompt = "\n\n".join(parts)
            return await _run_task(node, prompt, user_id, session_id=session_id)

        completed = await asyncio.gather(*(run_one(n) for n in round_nodes))
        all_nodes.extend(completed)

        for node in completed:
            task_results.append(TaskResult(
                task_index=node.index,
                task_title=node.title,
                agent_id=node.agent_id,
                output=node.output or "",
                success=node.success,
                error=node.output if not node.success else None,
                conversation_id=node.conversation_id,
            ))
            if not node.success and not target_failed:
                target_failed = True
                failed_task_title = node.title

        if target_failed:
            break

    success = not target_failed
    # Gom output của tất cả tasks thành target output
    target_output = "\n\n".join(
        f"[{n.title}]: {n.output}" for n in all_nodes if n.success and n.output
    )

    return TargetResult(
        node_id=target.node_id,
        description=target.description,
        task_results=task_results,
        success=success,
        failed_task=failed_task_title,
    ), success, target_output  # type: ignore[return-value]


async def execute_plan(plan: Plan, user_id: str = "orchestrator", session_id: str | None = None) -> ExecutionResult:
    """
    Vòng lặp chính: chạy targets tuần tự, tasks trong target song song theo depend_on.
    Nếu 1 target fail → dừng, trả về node_id để caller report planner.
    """
    result = ExecutionResult(plan_id=plan.plan_id)
    prior_context = ""

    for target in plan.targets:
        logger.info("executor | target=%s '%s'", target.node_id, target.description[:60])
        target_result, success, target_output = await _execute_target(target, prior_context, user_id, session_id=session_id)
        result.target_results.append(target_result)

        if success:
            result.all_outputs.append(target_output)
            prior_context = target_output  # output target này = context cho target tiếp theo
        else:
            result.failed_node_id = target.node_id
            result.success = False
            logger.warning("executor | target %s failed", target.node_id)
            break

    return result
