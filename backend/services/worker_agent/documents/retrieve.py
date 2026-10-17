"""Passages for a question (UC-05-04 steps 1 and 2; tab 5, document retrieval).

1. Pre-filter, before any ranking: collections of the question's Data Space, of its declared
   purpose when it has one (BR-59); ready documents; passages in force at as_of, by the passage's
   own validity when it has one, else the document's (BR-109); no page below the OCR threshold
   (BR-19); masked text only, since an agent never reads unmasked personal data (BR-46).
2. Two lists, fused by reciprocal rank (k = 60): Qdrant neighbours of the question and Postgres
   full-text matches over the passage text. Tab 5 plans bge-m3 sparse vectors for the lexical half;
   until those are written, the full-text index of chunk.text serves it.
3. The TEI reranker, when configured, orders the fused pool and decides relevance; without it a
   passage is relevant when it shares most of the question's words or is a close neighbour.
4. Passages flagged injection_suspected never reach the model (tab 5: citable only once reviewed).

What comes back is at most ``limit`` passages, each above the relevance bar: an empty list is the
"không tìm thấy trong tài liệu được phép" of E1, never a reason to guess.
"""

from __future__ import annotations

import datetime
import os
import re
import unicodedata
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import exists, func, literal_column, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db.models import document as doc
from services.worker_agent.documents.search import SearchServices, Target

RRF_K = 60
POOL = 40
# E1's relevance bar. Tab 8 leaves the threshold open ("Cần làm rõ"), so it is configurable.
MIN_RERANK = float(os.getenv("DOCUMENT_MIN_RERANK", "0.2"))
MIN_DENSE = float(os.getenv("DOCUMENT_MIN_DENSE", "0.55"))
MIN_OVERLAP = float(os.getenv("DOCUMENT_MIN_OVERLAP", "0.6"))

# Words that carry no subject: a question shares them with any passage.
_STOP = frozenset(
    "là gì của và các những cho với theo trong tại về được có không nào bao nhiêu thế như này "
    "đó khi thì mà hay hoặc hiện nay áp dụng quy định hỏi bạn tôi ai đâu sao vậy một".split()
)
_WORD = re.compile(r"\w+", re.UNICODE)


@dataclass(frozen=True)
class Passage:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    title: str
    doc_no: str | None
    section_path: str | None
    page_from: int
    page_to: int
    text: str
    effective_from: datetime.date | None
    effective_to: datetime.date | None
    # Title and number of the document that replaced this one, when one did (BR-20).
    superseded_by: str | None
    score: float


def _words(text: str) -> list[str]:
    return [w for w in _WORD.findall(unicodedata.normalize("NFC", text).lower()) if w not in _STOP]


def overlap(question: str, text: str) -> float:
    """Share of the question's subject words the passage contains."""
    asked = set(_words(question))
    if not asked:
        return 0.0
    return len(asked & set(_words(text))) / len(asked)


def _tsquery(question: str) -> str | None:
    # OR of the subject words: the whole question ANDed would almost never match one passage.
    words = sorted({w for w in _words(question) if len(w) > 1})
    return " | ".join(words) if words else None


async def retrieve(
    session: AsyncSession,
    services: SearchServices,
    *,
    data_space_id: uuid.UUID,
    question: str,
    as_of: datetime.date,
    purpose: str | None,
    limit: int = 8,
) -> list[Passage]:
    c, d, col = doc.Chunk, doc.Document, doc.Collection
    query = select(col).where(col.data_space_id == data_space_id, col.deleted_at.is_(None))
    if purpose is not None:
        query = query.where(col.purpose == purpose)
    collections = list((await session.scalars(query)).all())
    if not collections:
        return []
    starts = func.coalesce(c.effective_from, d.effective_from)
    ends = func.coalesce(c.effective_to, d.effective_to)
    unusable_page = exists().where(
        doc.DocumentPage.document_id == c.document_id,
        doc.DocumentPage.page_no.between(c.page_from, c.page_to),
        doc.DocumentPage.usable.is_(False),
    )
    base = (
        select(c, func.coalesce(d.title, d.file_name), d.doc_no, starts, ends)
        .join(d, d.id == c.document_id)
        .where(
            c.collection_id.in_([x.id for x in collections]),
            d.status == "ready",
            d.deleted_at.is_(None),
            or_(starts.is_(None), starts <= as_of),
            or_(ends.is_(None), ends >= as_of),
            ~unusable_page,
            c.pii_masked.is_(True),
            c.injection_suspected.is_(False),
        )
    )
    keyword: list[Any] = []
    if (terms := _tsquery(question)) is not None:
        tsquery = func.to_tsquery(literal_column("'simple'"), terms)
        # The expression of chunk_text_idx, so the GIN index serves it.
        tsvector = func.to_tsvector(literal_column("'simple'"), c.text)
        keyword = list(
            (
                await session.execute(
                    base.where(tsvector.op("@@")(tsquery))
                    .order_by(func.ts_rank(tsvector, tsquery).desc(), c.id)
                    .limit(POOL)
                )
            ).all()
        )
    neighbours = await services.dense(
        [Target(x.qdrant_collection, x.embed_model) for x in collections], question, POOL
    )
    dense_score = dict(neighbours)
    semantic: list[Any] = []
    if neighbours:
        by_id = {
            r[0].id: r
            for r in (
                await session.execute(
                    base.join(col, col.id == c.collection_id).where(
                        c.id.in_(list(dense_score)), c.embed_model == col.embed_model
                    )
                )
            ).all()
        }
        semantic = [by_id[i] for i, _ in neighbours if i in by_id]

    fused: dict[uuid.UUID, float] = {}
    rows: dict[uuid.UUID, Any] = {}
    for ranked in (keyword, semantic):
        for position, r in enumerate(ranked):
            rows[r[0].id] = r
            fused[r[0].id] = fused.get(r[0].id, 0.0) + 1.0 / (RRF_K + position + 1)
    pool = sorted(fused, key=lambda i: (-fused[i], str(i)))[:POOL]
    if not pool:
        return []

    def body(i: uuid.UUID) -> str:
        chunk = rows[i][0]
        return f"{chunk.context_header}\n\n{chunk.text}" if chunk.context_header else chunk.text

    reranked = await services.rerank(question, [body(i) for i in pool])
    if reranked is not None:
        scored = sorted(
            ((i, s) for i, s in zip(pool, reranked, strict=True) if s >= MIN_RERANK),
            key=lambda x: -x[1],
        )
    else:
        scored = [
            (i, fused[i])
            for i in pool
            if overlap(question, body(i)) >= MIN_OVERLAP or dense_score.get(i, 0.0) >= MIN_DENSE
        ]
    chosen = scored[:limit]
    if not chosen:
        return []

    document_ids = {rows[i][0].document_id for i, _ in chosen}
    replacements = {
        r.supersedes_id: r.title or r.doc_no or r.file_name
        for r in (
            await session.execute(
                select(d.supersedes_id, d.title, d.doc_no, d.file_name).where(
                    d.supersedes_id.in_(document_ids), d.deleted_at.is_(None), d.status == "ready"
                )
            )
        ).all()
    }
    passages: list[Passage] = []
    for i, score in chosen:
        chunk, title, doc_no, effective_from, effective_to = rows[i]
        passages.append(
            Passage(
                chunk_id=chunk.id,
                document_id=chunk.document_id,
                title=title,
                doc_no=doc_no,
                section_path=chunk.section_path,
                page_from=chunk.page_from,
                page_to=chunk.page_to,
                text=chunk.text,
                effective_from=effective_from,
                effective_to=effective_to,
                superseded_by=replacements.get(chunk.document_id),
                score=round(score, 6),
            )
        )
    return passages
