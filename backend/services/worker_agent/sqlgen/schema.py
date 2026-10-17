"""The Data Space's tables as the exploratory SQL path sees them, and linking a question to them.

DeepEye-SQL's unified schema linking (01 §3.4): what the question names directly (a table's or a
column's name or label), what it names by value (a known value of a text column: "Hà Nội" is a
city), then closed over the approved foreign keys so a join the question needs has its middle
table. Only what is linked is shown to the model, so the context stays small.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from services.worker_agent.understand.text import norm

MAX_TABLES = 4


@dataclass(frozen=True)
class Col:
    name: str
    label: str
    kind: str  # catalog.column_meta.normalized_type
    description: str | None = None
    samples: tuple[str, ...] = ()


@dataclass(frozen=True)
class Tbl:
    dataset_id: uuid.UUID
    schema: str
    name: str
    label: str
    columns: tuple[Col, ...]
    description: str | None = None

    @property
    def qualified(self) -> str:
        return f"{self.schema}.{self.name}"


@dataclass(frozen=True)
class Fk:
    from_table: uuid.UUID
    from_columns: tuple[str, ...]
    to_table: uuid.UUID
    to_columns: tuple[str, ...]


@dataclass
class Schema:
    tables: list[Tbl]
    fks: list[Fk] = field(default_factory=list)
    # Verified question → SQL pairs of the Data Space (context.example_query, active).
    examples: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class Linked:
    tables: list[Tbl]
    fks: list[Fk]
    values: list[tuple[str, str, str]]  # table, column, the value the question names


def _says(question: str, phrase: str) -> bool:
    p = norm(phrase.replace("_", " "))
    return bool(p) and f" {p} " in question


def link(question: str, schema: Schema, *, max_tables: int = MAX_TABLES) -> Linked:
    q = f" {norm(question)} "
    scored: list[tuple[int, int, Tbl]] = []
    values: list[tuple[str, str, str]] = []
    for order, t in enumerate(schema.tables):
        score = 3 * (_says(q, t.label) or _says(q, t.name))
        for c in t.columns:
            score += 2 * (_says(q, c.label) or _says(q, c.name))
            for v in c.samples:
                if _says(q, v):
                    score += 2
                    values.append((t.name, c.name, v))
        if score:
            scored.append((-score, order, t))
    chosen = [t for _, _, t in sorted(scored, key=lambda x: (x[0], x[1]))][:max_tables]
    ids = {t.dataset_id for t in chosen}
    # A table joined to two of the chosen ones is the middle of their join.
    for t in schema.tables:
        if t.dataset_id in ids:
            continue
        touches = {fk.to_table for fk in schema.fks if fk.from_table == t.dataset_id} | {
            fk.from_table for fk in schema.fks if fk.to_table == t.dataset_id
        }
        if len(touches & ids) >= 2:
            chosen.append(t)
            ids.add(t.dataset_id)
    fks = [fk for fk in schema.fks if fk.from_table in ids and fk.to_table in ids]
    names = {t.name for t in chosen}
    return Linked(chosen, fks, [v for v in values if v[0] in names])
