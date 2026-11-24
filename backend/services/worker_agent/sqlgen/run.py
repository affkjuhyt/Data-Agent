"""The exploratory SQL path end to end (01 §3.4): six candidates written, each checked (and sent back
once with the checker's instructions), every one that passes run read-only in place, the results
voted on. The caller decides what the vote means for the question (sqlgen/answer.py)."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from libs.calibration import method_weights
from services.worker_agent.llm import Gateway
from services.worker_agent.query.executor import QueryOutcome
from services.worker_agent.sqlgen.audit_rules import AuditContext
from services.worker_agent.sqlgen.check import fingerprint, problems
from services.worker_agent.sqlgen.generate import (
    METHODS,
    Candidate,
    candidates,
    diagnose_and_repair,
    repair,
)
from services.worker_agent.sqlgen.schema import Linked
from services.worker_agent.sqlgen.vote import Decision, signature, vote


@dataclass
class Exploration:
    decision: Decision
    confidence: float
    agreeing: int
    # The vote by way of writing (vote.py, CONFIDENCE_SPEC §4): how far the winner is ahead, and
    # each way's share of its samples in it.
    margin: float = 0.0
    support: dict[str, float] = field(default_factory=dict)
    sql: str | None = None
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    truncated: bool = False
    query_run_id: uuid.UUID | None = None
    # What each candidate did, for the plan and the evidence: method, sql, problems, status, and
    # for one that ran, `group`: candidates of the same group returned the same result (vote.py),
    # numbered in the order they first appear. A vote can be counted again from these alone
    # (AE-23).
    tried: list[dict[str, Any]] = field(default_factory=list)
    # The run of each candidate, aligned with `tried` (None when it did not run or failed): the
    # ablation scores every candidate's own result. Kept in memory only, never stored.
    outcomes: list[QueryOutcome | None] = field(default_factory=list, repr=False)
    tokens: int = 0
    models: list[str] = field(default_factory=list)


async def explore(
    gateway: Gateway,
    question: str,
    linked: Linked,
    dialect: str,
    examples: list[tuple[str, str]],
    run_sql: Callable[[str], Awaitable[QueryOutcome]],
    methods: Sequence[str] = METHODS,
    refuse: Sequence[str] = (),
    audit: AuditContext | None = None,
) -> Exploration:
    banned = {fingerprint(s, dialect) for s in refuse if s}
    written = await candidates(
        gateway,
        question,
        linked,
        dialect,
        examples,
        methods,
        refuse=tuple(refuse),
        plain=linked.plain,
    )
    # A candidate may come from either view (S3): it is checked and repaired against both.
    both = linked.merged()

    async def checked(c: Candidate) -> tuple[Candidate, list[str]]:
        if c.sql is None:
            return c, [c.error or "no SQL"]
        found = problems(c.sql, both, dialect, audit)
        if not found:
            return c, []
        fixed = await repair(gateway, question, both, dialect, c, found)
        return fixed, problems(fixed.sql, both, dialect, audit) if fixed.sql else [
            fixed.error or "no SQL"
        ]

    reviewed = await asyncio.gather(*(checked(c) for c in written))
    results: list[list[list[Any]] | None] = []
    outcomes: list[QueryOutcome | None] = []
    tried: list[dict[str, Any]] = []
    for c, found in reviewed:
        if found or c.sql is None:
            results.append(None)
            outcomes.append(None)
            tried.append(
                {"method": c.method, "sql": c.sql, "problems": found, "status": "rejected"}
            )
            continue
        # RES-02: a statement the semantic audit already refused must not run or win again.
        if fingerprint(c.sql, dialect) in banned:
            results.append(None)
            outcomes.append(None)
            tried.append(
                {
                    "method": c.method,
                    "sql": c.sql,
                    "problems": ["SQL đã bị từ chối vì không đúng ý câu hỏi; viết cách khác."],
                    "status": "rejected",
                }
            )
            continue
        out = await run_sql(c.sql)
        if not out.ok and out.status == "failed" and out.error:
            # The source refused it: diagnosed and rewritten once, checked again, run again.
            fixed = await diagnose_and_repair(gateway, question, both, dialect, c, out.error)
            if (
                fixed.sql
                and not problems(fixed.sql, both, dialect, audit)
                and fingerprint(fixed.sql, dialect) not in banned
            ):
                retried = await run_sql(fixed.sql)
                if retried.ok:
                    c, out = fixed, retried
        results.append(out.rows if out.ok else None)
        outcomes.append(out if out.ok else None)
        tried.append(
            {
                "method": c.method,
                "sql": c.sql,
                "problems": [],
                "status": "ran" if out.ok else "failed",
                "query_run_id": str(out.query_run_id),
            }
        )
    v = vote(
        results,
        total=len(written),
        methods=[str(entry["method"]) for entry in tried],
        weights=method_weights(),
    )
    decision = v.decision
    # S3's check: the question named a quantity the layer defines, and the winning statement
    # reads none of its tables (23/11: "doanh thu" summed off the sales quota by all six).
    # Agreement on another table is no evidence: answered, but to be checked.
    if (
        decision == "answer"
        and v.winner is not None
        and linked.defined_tables
        and not _reads_any(tried[v.winner].get("sql") or "", linked.defined_tables, dialect)
    ):
        decision = "review"
    groups: dict[str, int] = {}
    for entry, rows in zip(tried, results, strict=True):
        if rows is not None:
            entry["group"] = groups.setdefault(signature(rows), len(groups))
    found_out = Exploration(
        decision,
        v.confidence,
        round(v.confidence * len(written)),
        margin=v.margin,
        support=v.support,
        tried=tried,
        tokens=sum(c.tokens for c, _ in reviewed),
        models=sorted({c.model for c, _ in reviewed if c.model}),
        outcomes=outcomes,
    )
    if v.winner is not None:
        best = outcomes[v.winner]
        assert best is not None
        found_out.sql = reviewed[v.winner][0].sql
        found_out.columns = [str(col.get("name")) for col in best.columns]
        found_out.rows = best.rows
        found_out.truncated = best.truncated
        found_out.query_run_id = best.query_run_id
    return found_out


def _reads_any(sql: str, tables: Sequence[str], dialect: str) -> bool:
    """The statement reads at least one of the tables (schema.table), by its parse."""
    import sqlglot
    from sqlglot import exp
    from sqlglot.errors import ParseError

    try:
        read = {
            f"{t.db}.{t.name}".lower() if t.db else t.name.lower()
            for t in sqlglot.parse_one(sql, read=dialect).find_all(exp.Table)
        }
    except ParseError:
        return False
    wanted = {t.lower() for t in tables} | {t.lower().split(".")[-1] for t in tables}
    return bool(read & wanted)
