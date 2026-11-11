"""The exploratory SQL path end to end (01 §3.4): six candidates written, each checked (and sent back
once with the checker's instructions), every one that passes run read-only in place, the results
voted on. The caller decides what the vote means for the question (sqlgen/answer.py)."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from services.worker_agent.llm import Gateway
from services.worker_agent.query.executor import QueryOutcome
from services.worker_agent.sqlgen.check import fingerprint, problems
from services.worker_agent.sqlgen.generate import (
    METHODS,
    Candidate,
    candidates,
    diagnose_and_repair,
    repair,
)
from services.worker_agent.sqlgen.schema import Linked
from services.worker_agent.sqlgen.vote import Decision, vote


@dataclass
class Exploration:
    decision: Decision
    confidence: float
    agreeing: int
    sql: str | None = None
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    truncated: bool = False
    query_run_id: uuid.UUID | None = None
    # What each candidate did, for the plan and the evidence: method, sql, problems, status.
    tried: list[dict[str, Any]] = field(default_factory=list)
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
) -> Exploration:
    banned = {fingerprint(s, dialect) for s in refuse if s}
    written = await candidates(
        gateway, question, linked, dialect, examples, methods, refuse=tuple(refuse)
    )

    async def checked(c: Candidate) -> tuple[Candidate, list[str]]:
        if c.sql is None:
            return c, [c.error or "no SQL"]
        found = problems(c.sql, linked, dialect)
        if not found:
            return c, []
        fixed = await repair(gateway, question, linked, dialect, c, found)
        return fixed, problems(fixed.sql, linked, dialect) if fixed.sql else [
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
            fixed = await diagnose_and_repair(gateway, question, linked, dialect, c, out.error)
            if (
                fixed.sql
                and not problems(fixed.sql, linked, dialect)
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
    v = vote(results, total=len(written))
    found_out = Exploration(
        v.decision,
        v.confidence,
        round(v.confidence * len(written)),
        tried=tried,
        tokens=sum(c.tokens for c, _ in reviewed),
        models=sorted({c.model for c, _ in reviewed if c.model}),
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
