from __future__ import annotations

from typing import Any
import asyncio
import logging
import time
from contextlib import nullcontext
from dataclasses import dataclass, field

from observability import get_langfuse_client
from planner.dpts.plan import Plan, Target
from schemas import TargetResult, TaskResult
from .task_queue import TaskNode, build_rounds, collect_context
from .agent_client import call_agent, traceparent_of

logger = logging.getLogger(__name__)


@dataclass
class ExecutionResult:
    plan_id: str
    target_results: list[TargetResult] = field(default_factory=list)
    all_outputs: list[str] = field(default_factory=list)
    failed_node_id: str | None = None
    success: bool = True


async def _run_task(
    node: TaskNode,
    prompt: str,
    user_id: str,
    tenant_id: str = "",
    user_roles: list[str] | None = None,
    orchestrated: bool = True,
    executor_start: float = 0.0,
    guardrail_cfg: dict[str, Any] | None = None,
) -> TaskNode:
    node.start_ms = round((time.monotonic() - executor_start) * 1000)
    t0 = time.monotonic()

    # Span thủ công cho task — execute_plan() không phải LangChain runnable
    # nên CallbackHandler không tự tạo span; đây là parent mà traceparent header
    # (dựng thủ công từ span.trace_id/span.id) sẽ trỏ tới khi call_agent() gọi
    # HTTP sang workflow-runtime.
    lf = get_langfuse_client()
    ctx = lf.start_as_current_span(
        name=f"[task] {node.title}",
        input={"agent_id": node.agent_id, "agent_name": node.agent_name, "prompt": prompt[:1000]},
        metadata={"task_index": node.index},
    ) if lf else nullcontext()

    with ctx as span:
        try:
            output, conv_id, citations = await call_agent(
                agent_id=node.agent_id,
                prompt=prompt,
                user_id=user_id,
                tenant_id=tenant_id,
                user_roles=user_roles,
                orchestrated=orchestrated,
                traceparent=traceparent_of(span),
                guardrail_cfg=guardrail_cfg,
            )
            node.output = output
            node.conversation_id = conv_id
            node.citations = citations
            node.success = True
        except Exception as e:
            logger.warning("task failed | '%s': %s", node.title, e)
            node.output = str(e)
            node.success = False
        node.latency_ms = round((time.monotonic() - t0) * 1000)
        node.done = True
        if span is not None:
            span.update(
                output=node.output[:2000] if node.output else "",
                metadata={"conversation_id": node.conversation_id, "latency_ms": node.latency_ms, "success": node.success},
            )
    return node


async def _execute_target(
    target: Target,
    prior_context: str,
    user_id: str,
    original_query: str = "",
    tenant_id: str = "",
    user_roles: list[str] | None = None,
    agent_name_map: dict[str, str] | None = None,
    orchestrated: bool = True,
    executor_start: float = 0.0,
    guardrail_cfg: dict[str, Any] | None = None,
) -> tuple[TargetResult, bool, str]:
    """
    Chạy tất cả tasks trong 1 target theo rounds (song song trong round, tuần tự giữa rounds).
    """
    lf = get_langfuse_client()
    ctx = lf.start_as_current_span(
        name=f"[target] {target.description[:60]}",
        input={"description": target.description, "task_count": len(target.tasks)},
    ) if lf else nullcontext()

    with ctx:
        rounds, _ = build_rounds([t.model_dump() for t in target.tasks])
        all_nodes: list[TaskNode] = []
        task_results: list[TaskResult] = []
        target_failed = False
        failed_task_title: str | None = None

        for round_nodes in rounds:
            intra_context = collect_context(all_nodes)

            async def run_one(node: TaskNode) -> TaskNode:
                # agent_name_map đã có sẵn (build từ danh sách agents ở execute_plan)
                # — gán vào node TRƯỚC khi _run_task mở span "[task]", để input
                # span hiện đúng tên agent thay vì rơi về agent_id (frontend
                # trimInput() fallback agent_name → agent_id khi rỗng).
                node.agent_name = (agent_name_map or {}).get(node.agent_id, "")
                parts = []
                if node.depend_on:
                    if intra_context:
                        parts.append(f"## Kết quả từ các task phụ thuộc\n{intra_context}")
                else:
                    if prior_context:
                        parts.append(f"## Kết quả từ các bước trước\n{prior_context}")
                    if node.input:
                        parts.append(f"## Nội dung\n{node.input}")
                parts.append(f"Nhiệm vụ: {node.title}")
                return await _run_task(
                    node, "\n\n".join(parts), user_id,
                    tenant_id=tenant_id, user_roles=user_roles, orchestrated=orchestrated,
                    executor_start=executor_start, guardrail_cfg=guardrail_cfg,
                )

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
                    citations=node.citations,
                    start_ms=node.start_ms,
                    latency_ms=node.latency_ms,
                ))
                if not node.success and not target_failed:
                    target_failed = True
                    failed_task_title = node.title

            if target_failed:
                break

        success = not target_failed
        target_output = "\n\n".join(
            f"[{n.title}]: {n.output}" for n in all_nodes if n.success and n.output
        )
        return TargetResult(
            node_id=target.node_id,
            description=target.description,
            task_results=task_results,
            success=success,
            failed_task=failed_task_title,
        ), success, target_output


async def execute_plan(
    plan: Plan,
    user_id: str = "orchestrator",
    original_query: str = "",
    tenant_id: str = "",
    user_roles: list[str] | None = None,
    agents: list[dict[str, Any]] | None = None,
    skip_node_ids: set[str] | None = None,
    orchestrated: bool = True,
    guardrail_cfg: dict[str, Any] | None = None,
) -> ExecutionResult:
    """
    Vòng lặp chính: targets tuần tự, tasks trong mỗi target song song theo depend_on.
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
        target_result, success, target_output = await _execute_target(
            target, prior_context, user_id,
            original_query=original_query, tenant_id=tenant_id, user_roles=user_roles,
            agent_name_map=agent_name_map, orchestrated=orchestrated,
            executor_start=executor_start, guardrail_cfg=guardrail_cfg,
        )
        result.target_results.append(target_result)

        if success:
            result.all_outputs.append(target_output)
            prior_context = target_output
        else:
            result.failed_node_id = target.node_id
            result.success = False
            logger.warning("executor | target %s failed", target.node_id)
            break

    return result
