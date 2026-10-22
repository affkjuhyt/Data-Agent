"""Finding "thay thế Quyết định số …" in a processed document (UC-05-05 step 1, AC-05-05-01).

A sentence that says the new document replaces another names it by number. When a ready document
of the same Data Space carries that number, the worker proposes the link: a
document.validity_proposed event with the replaced document, the new one's start date and the
sentence that says so. worker-agent turns it into a Cần bạn proposal; nothing is written to either
document until a person accepts it, and two documents claiming to replace the same one are left to
that person (AC-05-05-03), never resolved here.
"""

from __future__ import annotations

import datetime
import re
import uuid
from dataclasses import dataclass

from sqlalchemy import func, select

from libs.dataagent_db import uuid7
from libs.dataagent_db.models import document

# "thay thế Quyết định số 123/QĐ-NH", "thay thế cho Thông tư 39/2016/TT-NHNN".
_REPLACES = re.compile(
    r"thay\s+thế(?:\s+cho)?\s+(?:các\s+)?(?:quyết\s+định|thông\s+tư|nghị\s+định|công\s+văn|văn\s+bản|quy\s+định)?"
    r"\s*(?:số\s*)?(?P<no>\d+(?:/\d{4})?/[A-ZĐa-zđ0-9][A-ZĐa-zđ0-9\-]*)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Replacement:
    doc_no: str
    excerpt: str


def _norm(doc_no: str) -> str:
    return re.sub(r"\s+", "", doc_no).upper().rstrip(".,;:")


def find_replacements(texts: list[str]) -> list[Replacement]:
    seen: dict[str, Replacement] = {}
    for text in texts:
        for m in _REPLACES.finditer(text):
            number = _norm(m.group("no"))
            start = max(0, m.start() - 120)
            excerpt = " ".join(text[start : m.end() + 60].split())
            seen.setdefault(number, Replacement(number, excerpt))
    return list(seen.values())


async def propose(
    session,
    *,
    org_id: uuid.UUID,
    data_space_id: uuid.UUID,
    document_id: uuid.UUID,
    own_doc_no: str | None,
    effective_from: datetime.date | None,
    proposed_by: uuid.UUID | None,
    texts: list[str],
) -> int:
    """Queue one validity proposal per replaced document found; returns how many."""
    if proposed_by is None:
        return 0
    found = [r for r in find_replacements(texts) if r.doc_no != _norm(own_doc_no or "")]
    queued = 0
    for r in found:
        old = await session.scalar(
            select(document.Document.id)
            .join(document.Collection, document.Collection.id == document.Document.collection_id)
            .where(
                document.Collection.data_space_id == data_space_id,
                func.upper(func.replace(document.Document.doc_no, " ", "")) == r.doc_no,
                document.Document.id != document_id,
                document.Document.deleted_at.is_(None),
                document.Document.status == "ready",
            )
            .limit(1)
        )
        if old is None:
            continue
        session.add(
            document.Outbox(
                id=uuid7(),
                org_id=org_id,
                aggregate_type="document",
                aggregate_id=document_id,
                event_type="document.validity_proposed",
                payload={
                    "document_id": str(document_id),
                    "data_space_id": str(data_space_id),
                    "supersedes_id": str(old),
                    "effective_from": effective_from.isoformat() if effective_from else None,
                    "proposed_by": str(proposed_by),
                    "detected": True,
                    "excerpt": r.excerpt,
                },
            )
        )
        queued += 1
    return queued
