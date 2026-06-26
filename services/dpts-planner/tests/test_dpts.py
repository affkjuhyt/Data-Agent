import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dpts import CandidatePool, ExpandingQueue, MockLLM, PlanningProcess, StaticMemoryEstimator
from dpts.node import NodeState


def run(coro):
    return asyncio.run(coro)


def make_process(**kw):
    return PlanningProcess(
        MockLLM(plan_depth=3, branching=3),
        estimator=StaticMemoryEstimator(kw.pop("queue_size", 4)),
        **kw,
    )


# ── candidate pool (N) ────────────────────────────────────────────────────────
def test_pool_pops_open_in_confidence_order():
    pool = CandidatePool()
    pool.store("a", 0.2)
    pool.store("b", 0.9)
    pool.store("c", 0.5)
    assert pool.pop_best_open() == "b"
    assert pool.pop_best_open() == "c"
    assert pool.pop_best_open() == "a"
    assert pool.pop_best_open() is None


def test_pool_terminals_and_delete_associated():
    pool = CandidatePool()
    pool.store("n0", 0.3)
    pool.store_terminal("t1", 0.8)
    pool.store_terminal("t2", 0.6)
    assert pool.pop_terminal() == "t1"
    assert pool.pop_terminal(exclude={"t1"}) == "t2"
    removed = pool.delete_associated(["t1", "n0", "ghost"])
    assert set(removed) == {"t1", "n0"}
    assert "t1" not in pool and "n0" not in pool


# ── expanding queue (Q) ───────────────────────────────────────────────────────
def test_queue_splits_capacity():
    q = ExpandingQueue(estimator=StaticMemoryEstimator(4), exploit_ratio=0.5)
    assert q.capacity == 4
    assert q.max_exploitation == 2
    assert q.max_exploration == 2
    q.add_exploitation("a", 0.5)
    q.add_exploitation("b", 0.6)
    assert not q.has_exploitation_slot()
    assert q.weakest_exploitation() == ("a", 0.5)


# ── plan to first terminal ────────────────────────────────────────────────────
def test_plan_reaches_terminal_with_figure_shape():
    process = make_process()
    plan = run(process.plan("Build a daily sales report"))
    assert plan is not None
    assert plan.user_input == "Build a daily sales report"
    assert len(plan.targets) == 3
    t0 = plan.targets[0]
    assert t0.description
    assert t0.tasks and t0.tasks[0].title and isinstance(t0.tasks[0].depend_on, list)
    assert plan.node_path[0] == process.tree.root_id
    assert plan.node_path[-1] == plan.terminal_node_id


# ── Planner gate: trim broken branch, keep siblings, expand again ─────────────
def test_planner_gate_trims_branch_keeps_siblings_and_replans():
    process = make_process()
    plan = run(process.plan("ETL the orders table"))
    assert plan is not None

    broken = plan.node_path[2]
    parent_expected = plan.node_path[1]
    parent = process.tree.get(parent_expected)
    siblings_before = [c for c in parent.children_ids if c != broken]
    assert siblings_before, "broken node should have siblings (branching > 1)"

    result = process.report_broken_node(broken)
    assert result["parent_id"] == parent_expected
    assert result["reexpand"] is False           # siblings survive -> reuse them
    assert broken in result["trimmed"]
    assert plan.terminal_node_id in result["trimmed"]
    for nid in result["trimmed"]:
        assert process.tree.get(nid).state == NodeState.PRUNED
        assert nid not in process.pool and nid not in process.queue

    for sib in siblings_before:
        assert sib not in result["trimmed"]
        assert process.tree.get(sib).state != NodeState.PRUNED
    assert parent.blocked_attempts
    assert parent.state == NodeState.EXPANDED
    assert parent_expected not in process.pool

    new_plan = run(process.resume())
    assert new_plan is not None
    assert new_plan.terminal_node_id != plan.terminal_node_id
    assert not (set(new_plan.node_path) & set(result["trimmed"]))
    assert new_plan.node_path[0] == process.tree.root_id


def test_planner_gate_reexpands_when_no_sibling():
    # branching=1 -> a broken node leaves the parent childless -> parent re-expands
    process = PlanningProcess(MockLLM(plan_depth=3, branching=1),
                              estimator=StaticMemoryEstimator(2), branching=1)
    plan = run(process.plan("single chain goal"))
    assert plan is not None
    broken = plan.node_path[1]
    result = process.report_broken_node(broken)
    assert result["reexpand"] is True
    assert result["parent_id"] in process.pool


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("PASS", name)
    print("ALL PASSED")
