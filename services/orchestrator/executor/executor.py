from __future__ import annotations

import asyncio
import logging
import time
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


async def _run_task(node: TaskNode, prompt: str, user_id: str, tenant_id: str = "", executor_start: float = 0.0, lf_parent=None) -> TaskNode:
    """Chạy 1 task, ghi output vào node, không throw."""
    node.start_ms = round((time.monotonic() - executor_start) * 1000)
    t0 = time.monotonic()
    task_span = lf_parent.span(
        name=f"[task] {node.title}",
        input={"agent_id": node.agent_id, "agent_name": node.agent_name, "prompt": prompt[:1000]},
        metadata={"task_index": node.index},
    ) if lf_parent else None
    try:
        output, conv_id = await call_agent(
            agent_id=node.agent_id,
            prompt=prompt,
            user_id=user_id,
            tenant_id=tenant_id,
        )
        node.output = output
        node.conversation_id = conv_id
        node.success = True
    except Exception as e:
        logger.warning("task failed | '%s': %s", node.title, e)
        node.output = str(e)
        node.success = False
    node.latency_ms = round((time.monotonic() - t0) * 1000)
    node.done = True
    if task_span:
        task_span.end(
            output=node.output[:2000] if node.output else "",
            level="ERROR" if not node.success else "DEFAULT",
            metadata={"conversation_id": node.conversation_id, "latency_ms": node.latency_ms},
        )
    return node


async def _execute_target(
    target: Target,
    prior_context: str,
    user_id: str,
    original_query: str = "",
    tenant_id: str = "",
    agent_name_map: dict[str, str] | None = None,
    executor_start: float = 0.0,
    lf_trace=None,
) -> tuple[TargetResult, bool, str]:
    """
    Chạy tất cả tasks trong 1 target theo rounds (song song trong round, tuần tự giữa rounds).
    Trả về (TargetResult, success, target_output).
    """
    rounds, stuck_tasks = build_rounds([t.model_dump() for t in target.tasks])
    if stuck_tasks and target_span:
        target_span.event(
            name="dependency-cycle-detected",
            level="WARNING",
            input={"stuck_tasks": stuck_tasks},
        )
    all_nodes: list[TaskNode] = []
    task_results: list[TaskResult] = []
    target_failed = False
    failed_task_title: str | None = None
    target_span = lf_trace.span(
        name=f"[target] {target.description[:80]}",
        input={"description": target.description, "task_count": len(target.tasks)},
    ) if lf_trace else None

    for round_nodes in rounds:
        # Build prompt cho từng task trong round:
        # original query + target description + context từ prior targets + output các task đã xong
        intra_context = collect_context(all_nodes)

        async def run_one(node: TaskNode) -> TaskNode:
            parts = []
            if node.depend_on:
                # Task phụ thuộc: nhận output thực từ các task nó phụ thuộc
                if intra_context:
                    parts.append(f"## Kết quả từ các task phụ thuộc\n{intra_context}")
            else:
                # Task độc lập: nhận nội dung planner đã extract + context từ target trước
                if prior_context:
                    parts.append(f"## Kết quả từ các bước trước\n{prior_context}")
                if node.input:
                    parts.append(f"## Nội dung\n{node.input}")
            parts.append(f"Nhiệm vụ: {node.title}")
            prompt = "\n\n".join(parts)
            return await _run_task(node, prompt, user_id, tenant_id=tenant_id, executor_start=executor_start, lf_parent=target_span)

        completed = await asyncio.gather(*(run_one(n) for n in round_nodes))
        all_nodes.extend(completed)

        for node in completed:
            task_results.append(TaskResult(
                task_index=node.index,
                task_title=node.title,
                agent_id=node.agent_id,
                agent_name=(agent_name_map or {}).get(node.agent_id, ""),
                output=node.output or "",
                success=node.success,
                error=node.output if not node.success else None,
                conversation_id=node.conversation_id,
                start_ms=node.start_ms,
                latency_ms=node.latency_ms,
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

    if target_span:
        target_span.end(
            output=target_output[:2000] if target_output else "",
            level="ERROR" if not success else "DEFAULT",
        )
    return TargetResult(
        node_id=target.node_id,
        description=target.description,
        task_results=task_results,
        success=success,
        failed_task=failed_task_title,
    ), success, target_output


async def execute_plan(plan: Plan, user_id: str = "orchestrator", original_query: str = "", tenant_id: str = "", agents: list[dict] | None = None, lf_trace=None, skip_node_ids: set[str] | None = None) -> ExecutionResult:
    """
    Vòng lặp chính: chạy targets tuần tự, tasks trong target song song theo depend_on.
    Nếu 1 target fail → dừng, trả về node_id để caller report planner.
    skip_node_ids: các node đã hoàn thành ở lần chạy trước (khi replan), sẽ bị bỏ qua.
    """
    result = ExecutionResult(plan_id=plan.plan_id)
    prior_context = ""
    agent_name_map = {a["id"]: a["name"] for a in (agents or [])}
    executor_start = time.monotonic()
    _skip = skip_node_ids or set()

    for target in plan.targets:
        if target.node_id in _skip:
            logger.info("executor | skip completed target=%s", target.node_id)
            continue
        logger.info("executor | target=%s '%s'", target.node_id, target.description[:60])
        target_result, success, target_output = await _execute_target(target, prior_context, user_id, original_query=original_query, tenant_id=tenant_id, agent_name_map=agent_name_map, executor_start=executor_start, lf_trace=lf_trace)
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
