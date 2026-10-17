"""Writing SQL candidates with the on-prem model (01 §3.4 N-version; ADR-41: never a frontier model
for SQL, the gateway's `text_to_sql` alias routes on-prem).

Three ways of writing, each sampled twice: skeleton first (the clauses, then the details), from
the Data Space's verified examples (context.example_query) with the question's literals masked,
and divide and conquer (the parts, then one query). About a tenth of the questions only one way
gets right, which is why the vote needs all three. A candidate the checker rejects is sent back
once with the checker's instructions: the checker decides, the model only fixes.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass

from services.worker_agent.llm import Gateway, ModelUnavailable
from services.worker_agent.sqlgen.schema import Linked

ROLE = "text_to_sql"
METHODS = ("skeleton", "examples", "divide")
TEMPERATURES = (0.2, 0.7)
MAX_EXAMPLES = 3

RULES = """You write one read-only {dialect} SELECT statement answering the question, over the tables
below only. Qualify every table with its schema (schema.table). Name the columns you return; never
SELECT *. Use the values exactly as listed when the question names them. Give readable column
aliases. Answer only JSON: {{"sql": "<the query>"}}."""

HOW = {
    "skeleton": "First decide the skeleton (SELECT … FROM … JOIN … WHERE … GROUP BY … ORDER BY), "
    "then fill in the columns and conditions.",
    "examples": "Follow the way the verified examples below are written.",
    "divide": "Split the question into its parts (what is measured, over which rows, grouped by "
    "what), write each, then combine them into one statement.",
}

_LITERAL = re.compile(r"\d+(?:[.,/]\d+)*|'[^']*'")


@dataclass
class Candidate:
    method: str
    sql: str | None
    tokens: int = 0
    model: str | None = None
    error: str | None = None


def schema_text(linked: Linked) -> str:
    lines = []
    for t in linked.tables:
        lines.append(f"TABLE {t.qualified} -- {t.label}")
        for c in t.columns:
            note = f" -- {c.label}" + (f": {c.description}" if c.description else "")
            lines.append(f"  {c.name} {c.kind}{note}")
    by_id = {t.dataset_id: t for t in linked.tables}
    for fk in linked.fks:
        a, b = by_id[fk.from_table], by_id[fk.to_table]
        pairs = " AND ".join(
            f"{a.name}.{x} = {b.name}.{y}"
            for x, y in zip(fk.from_columns, fk.to_columns, strict=True)
        )
        lines.append(f"JOIN {a.qualified} TO {b.qualified} ON {pairs}")
    for table, column, value in linked.values:
        lines.append(f"VALUE {table}.{column} = '{value}'")
    return "\n".join(lines)


def _examples(examples: list[tuple[str, str]]) -> str:
    """Verified pairs with their literals masked, so the model copies the shape, not the numbers."""
    return "\n\n".join(
        f"Question: {_LITERAL.sub('…', q)}\nSQL: {sql}" for q, sql in examples[:MAX_EXAMPLES]
    )


def _system(method: str, dialect: str, examples: list[tuple[str, str]]) -> str:
    text = RULES.format(dialect=dialect) + "\n" + HOW[method]
    if method == "examples" and examples:
        text += "\n\nVerified examples:\n" + _examples(examples)
    return text


async def _ask(
    gateway: Gateway, method: str, system: str, user: str, temperature: float
) -> Candidate:
    try:
        reply = await gateway.json_chat(ROLE, system, user, max_tokens=900, temperature=temperature)
    except ModelUnavailable as exc:
        return Candidate(method, None, error=str(exc))
    sql = reply.content.get("sql")
    if not isinstance(sql, str) or not sql.strip():
        return Candidate(method, None, reply.tokens, reply.model, "the model gave no SQL")
    return Candidate(method, sql.strip().rstrip(";"), reply.tokens, reply.model)


async def candidates(
    gateway: Gateway,
    question: str,
    linked: Linked,
    dialect: str,
    examples: list[tuple[str, str]],
) -> list[Candidate]:
    """Six candidates, three ways twice, asked together."""
    user = f"{schema_text(linked)}\n\nQuestion: {question}"
    return list(
        await asyncio.gather(
            *(
                _ask(gateway, m, _system(m, dialect, examples), user, t)
                for m in METHODS
                for t in TEMPERATURES
            )
        )
    )


async def repair(
    gateway: Gateway,
    question: str,
    linked: Linked,
    dialect: str,
    candidate: Candidate,
    problems: list[str],
) -> Candidate:
    user = (
        f"{schema_text(linked)}\n\nQuestion: {question}\n\nYour SQL:\n{candidate.sql}\n\n"
        "It fails these checks; fix exactly these and keep the rest:\n- " + "\n- ".join(problems)
    )
    fixed = await _ask(gateway, candidate.method, RULES.format(dialect=dialect), user, 0.0)
    fixed.tokens += candidate.tokens
    return fixed
