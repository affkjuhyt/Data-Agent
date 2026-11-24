"""The Data Space's tables as the exploratory SQL path sees them, and linking a question to them.

DeepEye-SQL's unified schema linking (01 §3.4): what the question names directly (a table's or a
column's name or label), what it names by value (a known value of a text column: "Hà Nội" is a
city), then closed over the approved foreign keys so a join the question needs has its middle
table. Only what is linked is shown to the model, so the context stays small.

S2 (24/11/2026): the quantity the question names is linked by the semantic layer first. A metric
or measure it names (its name or an approved synonym) brings its business definition, the SQL of
its measures on their tables with the period rule, and links those tables before any lexical match:
the 23/11 ablation's candidates read "doanh thu" off the sales quota table and "doanh thu đại lý"
off a reseller's declared annual revenue, because those were the words the lexical linker matched.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from services.worker_agent.understand.text import norm
from services.worker_agent.understand.value_retrieval import near_matches

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


@dataclass(frozen=True)
class Definition:
    """A metric or measure of the semantic layer as the SQL writer reads it (S2)."""

    name: str
    phrases: tuple[str, ...]  # folded: its name and approved synonyms
    dataset_ids: frozenset[uuid.UUID]
    text: str


@dataclass
class Schema:
    tables: list[Tbl]
    fks: list[Fk] = field(default_factory=list)
    # Verified question → SQL pairs of the Data Space (context.example_query, active).
    examples: list[tuple[str, str]] = field(default_factory=list)
    definitions: list[Definition] = field(default_factory=list)


@dataclass
class Linked:
    tables: list[Tbl]
    fks: list[Fk]
    values: list[tuple[str, str, str]]  # table, column, the value the question names
    definitions: list[str] = field(default_factory=list)
    # S3: the question linked by its words alone, when definitions changed what is linked.
    plain: Linked | None = None
    # The tables of the definitions named (schema.table): a winner reading none of them did not
    # compute the quantity as defined (S3's check, sqlgen/run.py).
    defined_tables: list[str] = field(default_factory=list)

    def merged(self) -> Linked:
        """Both views as one, for checking a candidate written from either."""
        if self.plain is None:
            return self
        ids = {t.dataset_id for t in self.tables}
        tables = [*self.tables, *(t for t in self.plain.tables if t.dataset_id not in ids)]
        fks = [*self.fks, *(fk for fk in self.plain.fks if fk not in self.fks)]
        values = [*self.values, *(v for v in self.plain.values if v not in self.values)]
        return Linked(tables, fks, values, self.definitions, None, self.defined_tables)


def readable(schema: Schema, dialect: str | None = None) -> Schema:
    """The schema with nothing about a table it does not hold: joins with both ends among its
    tables, and only the verified examples whose SQL reads its tables alone (an example over a
    table the asker may not read would show that table to the model)."""
    ids = {t.dataset_id for t in schema.tables}
    qualified = {t.qualified for t in schema.tables}
    names = {t.name for t in schema.tables}
    examples = []
    for question, sql in schema.examples:
        try:
            tables = list(sqlglot.parse_one(sql, read=dialect).find_all(exp.Table))
        except ParseError:
            continue
        if tables and all(
            (f"{t.db}.{t.name}" in qualified) if t.db else (t.name in names) for t in tables
        ):
            examples.append((question, sql))
    return Schema(
        schema.tables,
        [fk for fk in schema.fks if fk.from_table in ids and fk.to_table in ids],
        examples,
        [d for d in schema.definitions if d.dataset_ids <= ids],
    )


def _says(question: str, phrase: str) -> bool:
    p = norm(phrase.replace("_", " "))
    return bool(p) and f" {p} " in question


# Words too common in questions to say which table is meant, folded (a label's distinctive words
# are its longer ones: "internet" in "Bán qua Internet", not "qua").
_COMMON = frozenset(
    {"nhieu", "trong", "thang", "nhung", "duoc", "khong", "theo", "tong", "cua", "nhat", "bang"}
)


def _distinctive(question: str, label: str) -> int:
    """How many of the label's distinctive words the question says (none when the label is said
    whole: that already scores)."""
    words = {w for w in norm(label.replace("_", " ")).split() if len(w) >= 5 and w not in _COMMON}
    return sum(1 for w in words if f" {w} " in question)


def named_definitions(question: str, definitions: list[Definition]) -> list[Definition]:
    """The definitions the question names, the most specific only: "doanh thu đại lý" is the
    reseller revenue's, not also the total revenue's whose name it contains."""
    q = f" {norm(question)} " if not question.startswith(" ") else question
    said: list[tuple[str, Definition]] = []
    for d in definitions:
        best = max((p for p in d.phrases if p and f" {p} " in q), key=len, default=None)
        if best is not None:
            said.append((best, d))
    return [
        d
        for phrase, d in said
        if not any(other != phrase and f" {phrase} " in f" {other} " for other, _ in said)
    ]


def link(
    question: str, schema: Schema, *, max_tables: int = MAX_TABLES, definitions: bool = True
) -> Linked:
    """`definitions` False links by the words alone, as before S2 (the plain view S3 gives half
    of the candidates, so a definition and a plain reading that disagree show in the vote)."""
    q = f" {norm(question)} "
    scored: list[tuple[int, int, Tbl]] = []
    values: list[tuple[str, str, str]] = []
    words = q.split()
    named_defs = named_definitions(q, schema.definitions) if definitions else []
    defined = frozenset().union(*(d.dataset_ids for d in named_defs)) if named_defs else frozenset()
    for order, t in enumerate(schema.tables):
        named = _says(q, t.label) or _says(q, t.name)
        score = 3 if named else _distinctive(q, t.label)
        # The quantity's own tables, from its definition, ahead of any word that matches.
        if t.dataset_id in defined:
            score += 6
        for c in t.columns:
            score += 2 * (_says(q, c.label) or _says(q, c.name))
            said = [v for v in c.samples if _says(q, v)]
            # A value said only in part ("Hồ Chí Minh" for "TP. Hồ Chí Minh"), by the value
            # index's near pass (understand/value_retrieval.py, R1-05).
            unsaid = {norm(v): v for v in c.samples if v not in said}
            said += [unsaid[label] for label, _, _ in near_matches(words, list(unsaid))]
            for v in said:
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
    kept = [d for d in named_defs if d.dataset_ids <= ids]
    by_id = {t.dataset_id: t for t in chosen}
    return Linked(
        chosen,
        fks,
        [v for v in values if v[0] in names],
        [d.text for d in kept],
        link(question, schema, max_tables=max_tables, definitions=False) if kept else None,
        sorted({by_id[i].qualified for d in kept for i in d.dataset_ids}),
    )
