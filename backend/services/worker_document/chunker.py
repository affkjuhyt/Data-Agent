"""Chunks cut on a document's headings, parent and child, and one summary per table (DI-08, §5.3, §5.4).

Over the elements (docling_adapter, re-levelled and without furniture by legal_headings), each
heading down to level 4 (Điều) opens a section: its text and the elements after it, up to the next
such heading. A Khoản or Điểm (levels 5 and 6) stays inside its Điều: the Điều is the unit a
citation names. The `section_path` is the headings above ("Chương I > Điều 5. Lãi suất"). A
section longer than MAX_CHARS is a parent chunk (cited at Điều level, found by full text only)
with child chunks cut between its elements, so at a Khoản, whose label joins the child's path
("… > Khoản 2"; vectors at Khoản level, RES-13); a shorter one is one chunk, searched and embedded.
A heading with deeper headings under it and no text of its own is only a step of the path; a leaf
heading with no body ("Điều 3. Quy chế có hiệu lực từ ngày ký.") is a chunk of its own text.

Rule 1 of §5.3: no table cell is ever inside a section chunk. A table is one `table` chunk of its
caption, column headers and row labels, never its figures (§5.4; the DoD's "no chunk holds a
cell's text" for the numbers): a figure is read from table_cell by SQL (table_query), so a chunk
can find the table but cannot be the source of its numbers.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from services.worker_document.docling_adapter import Element
from services.worker_document.processing import context_header
from services.worker_document.tables import Table

# To be measured by RG-02 on the 156 retrieval cases before it is fixed (§5.3 rule 4).
MAX_CHARS = 1600
MIN_CHARS = 200
# A heading is named in a path by its text, cut here (a long Docling heading is a sentence).
PATH_LABEL_CHARS = 120
SUMMARY_ROWS = 3
# Headings deeper than an Điều (Khoản 5, Điểm 6) are cut points inside it, not sections.
SECTION_LEVELS = 4
_KHOAN = re.compile(r"^(\d+)\.\s")
_DIEM = re.compile(r"^([a-zđ])\)\s")

# Body text of a section; a table is its own chunk, a picture has no text, a caption of a table
# goes with the table's summary.
_BODY_KINDS = frozenset({"text", "list", "caption", "footnote", "formula", "code"})


@dataclass
class Chunk:
    kind: str  # section | table
    text: str
    page_from: int
    page_to: int
    section_path: str | None
    context_header: str
    element_refs: list[str]
    # Index in chunks_of's result of this chunk's parent (a child of a long section).
    parent: int | None = None
    # Whether the chunk gets a vector: a parent is found by full text only (§5.3 rule 2).
    embed: bool = True
    # Index in the tables given of the table a `table` chunk sums up.
    table: int | None = None
    children: list[int] = field(default_factory=list)


def chunks_of(
    elements: Sequence[Element],
    tables: Sequence[Table] = (),
    *,
    title: str | None = None,
    doc_no: str | None = None,
    max_chars: int = MAX_CHARS,
    min_chars: int = MIN_CHARS,
) -> list[Chunk]:
    captions = {e.ref for e in elements if e.kind == "caption" and e.parent_ref}
    out: list[Chunk] = []
    stack: list[tuple[int, str]] = []  # (level, label) of the headings above
    section: list[Element] = []
    for element in elements:
        if element.kind == "heading" and (element.level or 1) <= SECTION_LEVELS:
            level = element.level or 1
            # The open section is a leaf when this heading is not under it.
            leaf = not section or section[0].kind != "heading" or level <= (section[0].level or 1)
            _close(out, section, stack, title, doc_no, max_chars, min_chars, leaf=leaf)
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, element.text[:PATH_LABEL_CHARS].strip()))
            section = [element]
        elif (
            (element.kind in _BODY_KINDS or element.kind == "heading")
            and element.ref not in captions
            and element.text
        ):
            section.append(element)
    _close(out, section, stack, title, doc_no, max_chars, min_chars, leaf=True)
    for index, table in enumerate(tables):
        out.append(_table_chunk(table, index, elements, title, doc_no))
    return out


def _close(
    out: list[Chunk],
    section: list[Element],
    stack: list[tuple[int, str]],
    title: str | None,
    doc_no: str | None,
    max_chars: int,
    min_chars: int,
    *,
    leaf: bool,
) -> None:
    if not section:
        return
    opener = section[0] if section[0].kind == "heading" else None
    body = section[1:] if opener is not None else section
    if not body and not leaf:
        return  # a heading with deeper headings under it is a step of the path
    path = " > ".join(label for _, label in stack) if opener is not None else None
    path = path or None
    header = context_header(title, doc_no, path)
    whole = _chunk(section, path, header)
    if len(whole.text) <= max_chars:
        out.append(whole)
        return
    parent = len(out)
    whole.embed = False
    out.append(whole)
    for piece in _pieces(body, max_chars, min_chars):
        members = [opener, *piece] if opener is not None else piece
        child_path = " > ".join(x for x in (path, _cut_label(piece)) if x) or None
        child = _chunk(members, child_path, context_header(title, doc_no, child_path))
        child.parent = parent
        whole.children.append(len(out))
        out.append(child)


def _cut_label(piece: Sequence[Element]) -> str | None:
    """A child's place in its Điều: the Khoản it holds ("Khoản 2", "Khoản 1-2" as the legacy
    chunker wrote it), else the Điểm or heading it starts at."""
    clauses = [m.group(1) for e in piece if e.kind == "heading" and (m := _KHOAN.match(e.text))]
    if clauses:
        return f"Khoản {clauses[0]}" if len(clauses) == 1 else f"Khoản {clauses[0]}-{clauses[-1]}"
    first = piece[0]
    if first.kind != "heading":
        return None
    if m := _DIEM.match(first.text):
        return f"Điểm {m.group(1)}"
    return first.text[:PATH_LABEL_CHARS].strip()


def _pieces(body: Sequence[Element], max_chars: int, min_chars: int) -> list[list[Element]]:
    """The body cut between elements, each piece under max_chars when its elements allow, and
    preferably at a Khoản: a piece over half full is closed before the next Khoản begins. A last
    piece under min_chars joins the one before it."""
    pieces: list[list[Element]] = [[]]
    size = 0
    for e in body:
        at_clause = e.kind == "heading" and size > max_chars // 2
        if pieces[-1] and (size + len(e.text) + 2 > max_chars or at_clause):
            pieces.append([])
            size = 0
        pieces[-1].append(e)
        size += len(e.text) + 2
    if len(pieces) > 1 and sum(len(e.text) for e in pieces[-1]) < min_chars:
        pieces[-2].extend(pieces.pop())
    return [p for p in pieces if p]


def _chunk(members: Sequence[Element], path: str | None, header: str) -> Chunk:
    pages = [e.page_no for e in members if e.page_no is not None]
    return Chunk(
        kind="section",
        text="\n\n".join(e.text for e in members if e.text),
        # A format without pages (DOCX, HTML) is cited as one page.
        page_from=min(pages) if pages else 1,
        page_to=max(pages) if pages else 1,
        section_path=path,
        context_header=header,
        element_refs=[e.ref for e in members],
    )


def _table_chunk(
    table: Table, index: int, elements: Sequence[Element], title: str | None, doc_no: str | None
) -> Chunk:
    """§5.4 without the figures: caption, column headers, the first rows' labels, the size."""
    headers = [
        c.text for c in table.cells if c.is_header and c.row_idx < table.header_rows and c.text
    ]
    labels = [
        c.text
        for c in table.cells
        if c.col_idx == 0 and c.row_idx >= table.header_rows and c.text and c.numeric_value is None
    ][:SUMMARY_ROWS]
    pages = (
        f", trang {table.page_from}"
        + (f"-{table.page_to}" if table.page_to and table.page_to != table.page_from else "")
        if table.page_from is not None
        else ""
    )
    lines = [
        table.caption or "",
        " | ".join(dict.fromkeys(headers)),
        "; ".join(labels),
        f"bảng {table.n_rows}x{table.n_cols}{pages}",
    ]
    path = _path_before(table.element_ref, elements)
    return Chunk(
        kind="table",
        text="\n".join(line for line in lines if line),
        page_from=table.page_from or 1,
        page_to=table.page_to or table.page_from or 1,
        section_path=path,
        context_header=context_header(title, doc_no, path),
        element_refs=[table.element_ref],
        table=index,
    )


def _path_before(ref: str, elements: Sequence[Element]) -> str | None:
    """The headings above the table's element: the section it stands in."""
    stack: list[tuple[int, str]] = []
    for e in elements:
        if e.ref == ref:
            break
        if e.kind == "heading":
            level = e.level or 1
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, e.text[:PATH_LABEL_CHARS].strip()))
    return " > ".join(label for _, label in stack) or None
