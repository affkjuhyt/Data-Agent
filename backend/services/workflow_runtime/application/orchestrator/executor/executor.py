from __future__ import annotations

import asyncio
import logging
import time
from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import Any

from services.workflow_runtime.application.orchestrator.planner.dpts.plan import Plan, Target
from services.workflow_runtime.application.orchestrator.schemas import TargetResult, TaskResult
from services.workflow_runtime.infrastructure.observability import get_langfuse_client

from .agent_client import call_agent
from .task_queue import TaskNode, build_nodes, collect_context

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
    # nên CallbackHandler không tự tạo span; đây là parent của mọi span
    # workflow_runtime tạo khi call_agent() chạy in-process (OTEL context là
    # ambient, không cần traceparent header).
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
    Chạy tasks trong 1 target theo depend_on: mỗi task khởi chạy ngay khi mọi task
    nó phụ thuộc đã xong, không đợi cả "round" — task chỉ phụ thuộc 1 task nhanh
    không bị giữ lại bởi 1 task chậm không liên quan. Task phụ thuộc nhận output
    của các task nó phụ thuộc (kể cả bắc cầu). Có task lỗi thì không khởi chạy
    task mới; task đang chạy được chạy hết.
    """
    lf = get_langfuse_client()
    ctx = lf.start_as_current_span(
        name=f"[target] {target.description[:60]}",
        input={"description": target.description, "task_count": len(target.tasks)},
    ) if lf else nullcontext()

    with ctx:
        nodes = build_nodes([t.model_dump() for t in target.tasks])
        by_index = {n.index: n for n in nodes}
        pending = [n.index for n in nodes]
        finished: set[int] = set()
        launched: list[TaskNode] = []
        running: dict[asyncio.Task[TaskNode], int] = {}
        target_failed = False
        failed_task_title: str | None = None

        def dependency_context(node: TaskNode) -> str:
            seen: set[int] = set()
            stack = list(node.depend_on)
            while stack:
                dep = stack.pop()
                if dep in seen or dep not in by_index:
                    continue
                seen.add(dep)
                stack.extend(by_index[dep].depend_on)
            return collect_context([by_index[i] for i in sorted(seen)])

        async def run_one(node: TaskNode, dep_context: str) -> TaskNode:
            # agent_name_map đã có sẵn (build từ danh sách agents ở execute_plan)
            # — gán vào node TRƯỚC khi _run_task mở span "[task]", để input
            # span hiện đúng tên agent thay vì rơi về agent_id (frontend
            # trimInput() fallback agent_name → agent_id khi rỗng).
            node.agent_name = (agent_name_map or {}).get(node.agent_id, "")
            parts = []
            if node.depend_on:
                if dep_context:
                    parts.append(f"## Kết quả từ các task phụ thuộc\n{dep_context}")
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

        def launch(index: int) -> None:
            node = by_index[index]
            pending.remove(index)
            launched.append(node)
            running[asyncio.create_task(run_one(node, dependency_context(node)))] = index

        try:
            while pending or running:
                if not target_failed:
                    ready = [i for i in pending if all(d in finished for d in by_index[i].depend_on)]
                    if not ready and not running:
                        logger.warning(
                            "executor | dependency cycle or broken deps, forcing remaining tasks: %s",
                            [{"index": i, "title": by_index[i].title, "depend_on": by_index[i].depend_on} for i in pending],
                        )
                        ready = list(pending)
                    for index in ready:
                        launch(index)
                if not running:
                    break
                done, _ = await asyncio.wait(running, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    node = task.result()
                    finished.add(running.pop(task))
                    if not node.success and not target_failed:
                        target_failed = True
                        failed_task_title = node.title
        finally:
            # Cancelled turn (client gone): don't leave agent calls running.
            for task in running:
                task.cancel()

        task_results = [
            TaskResult(
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
            )
            for node in launched
        ]

        success = not target_failed
        target_output = "\n\n".join(
            f"[{n.title}]: {n.output}" for n in launched if n.success and n.output
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
