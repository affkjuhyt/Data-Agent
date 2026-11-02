"""Every number the reader sees comes from a dataset (invariants 5 and 6), as the narrator does it
(answer/narrator.py): the reporter writes a figure only as a tag {{F12}} naming a fact the server
listed; the server fills the tag with the value in the organisation's format, then checks every
number left in the text. A sentence holding a number that is neither a filled fact nor a number
the question, a dataset's labels or a citation already contains is dropped."""

from __future__ import annotations

import re
from dataclasses import dataclass

from libs import language
from services.worker_agent.agentic.workspace import Workspace, number, plain

_TAG = re.compile(r"\{\{\s*(F\d+)\s*\}\}")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_CITE = re.compile(r"\[\d{1,2}\]")
_SENTENCE = re.compile(r"(?<=[.!?:])\s+|\n+")

# The facts listed for the reporter: enough to write about, bounded for the prompt.
ROWS_PER_DATASET = 40
MAX_FACTS = 400


@dataclass(frozen=True)
class Fact:
    tag: str
    value: float
    shown: str
    label: str


def _decimals(value: float) -> int:
    if value == int(value) and abs(value) >= 1:
        return 0
    return 2 if abs(value) >= 1 else 4


def show(value: float, locale: str | None = None) -> str:
    return language.number(value, _decimals(value), locale)


def facts(ws: Workspace, locale: str | None = None) -> dict[str, Fact]:
    out: dict[str, Fact] = {}

    def add(value: float, label: str) -> None:
        if len(out) >= MAX_FACTS:
            return
        tag = f"F{len(out) + 1}"
        out[tag] = Fact(tag, value, show(value, locale), label)

    for ds in ws.datasets.values():
        numeric = [
            i
            for i, _ in enumerate(ds.columns)
            if ds.rows
            and all(number(r[i]) is not None for r in ds.rows if r[i] is not None)
            and any(r[i] is not None for r in ds.rows)
        ]
        keys = [i for i in range(len(ds.columns)) if i not in numeric]
        for row in ds.rows[:ROWS_PER_DATASET]:
            where = ", ".join(f"{ds.columns[k]}={plain(row[k])}" for k in keys)
            for i in numeric:
                value = number(row[i])
                if value is not None:
                    add(value, f"{ds.id} · {where or 'tổng'} · {ds.columns[i]}")
        if len(ds.rows) > 1:
            for i in numeric:
                values = [n for n in (number(r[i]) for r in ds.rows) if n is not None]
                add(sum(values), f"{ds.id} · tổng các dòng · {ds.columns[i]}")
    return out


def allowed_numbers(question: str, ws: Workspace, extra: list[str]) -> set[str]:
    """Numbers a sentence may hold without a tag: in the question, in a dataset's labels and
    non-numeric cells (years, codes), in titles and column names."""
    texts = [question, *extra]
    for ds in ws.datasets.values():
        texts += [ds.title, *ds.columns]
        for row in ds.rows[:ROWS_PER_DATASET]:
            texts += [str(plain(v)) for v in row if number(v) is None and v is not None]
    return {n for t in texts for n in _NUMBER.findall(t)}


@dataclass
class Checked:
    text: str
    numbers: int
    dropped: int


# Citation markers a model writes off the format ([ID:2], 【2】, [^2], (ref 2)): repaired to [2].
_LOOSE_CITE = re.compile(r"\[(?:ID|id|ref|\^)\s*:?\s*(\d{1,2})\]|【(\d{1,2})】")


def repair_citations(text: str, known_indexes: set[int]) -> str:
    """Off-format markers rewritten as [n]; a marker for a passage the turn never found removed."""
    text = _LOOSE_CITE.sub(lambda m: f"[{m.group(1) or m.group(2)}]", text)
    return _CITE.sub(lambda m: m.group(0) if int(m.group(0)[1:-1]) in known_indexes else "", text)


def fill(text: str, known: dict[str, Fact], allowed: set[str]) -> Checked:
    numbers = 0
    kept: list[str] = []
    dropped = 0
    for sentence in [s for s in _SENTENCE.split(text) if s and s.strip()]:
        tags = _TAG.findall(sentence)
        if any(t not in known for t in tags):
            dropped += 1
            continue
        numbers += len(tags)
        filled = _TAG.sub(lambda m: known[m.group(1)].shown, sentence)
        bare = _CITE.sub("", _TAG.sub("", sentence))
        if any(n not in allowed for n in _NUMBER.findall(bare)):
            dropped += 1
            continue
        kept.append(filled)
    # Lines are kept as lines (markdown lists and headings), sentences of a paragraph joined.
    return Checked(_rejoin(text, kept), numbers, dropped)


def _rejoin(original: str, kept: list[str]) -> str:
    if "\n" not in original:
        return " ".join(kept)
    return "\n".join(kept)
