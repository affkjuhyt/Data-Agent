"""doc_ingest (UC-05-02) and its re-run after a review (UC-05-03 step 4), on document.outbox.

document.received: the original is read from MinIO, its pages extracted (text layer, else OCR with
its real confidence), masked under the collection's PII policy, cut into structured chunks,
embedded into Qdrant and written. A page below the OCR threshold is not usable and opens a
blocking low_ocr quality issue; the document is then waiting_review instead of ready (BR-19).

quality_issue.resolved: the person's correction is already in Postgres (the api wrote it); the
later steps run again from the stored pages, and the document becomes ready once nothing blocks.

Both paths reconcile instead of starting over (BR-108, AC-05-02-05, AC-05-03-04): a page whose
source hash is unchanged keeps its row, its corrected text included; a chunk whose text and pages
are unchanged keeps its id and its vector, so only new chunks are embedded; a corrected chunk
stays as the person left it while its pages are unchanged.
"""

from __future__ import annotations

import datetime
import hashlib
import logging
import os
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
from sqlalchemy import delete, func, select, update

from libs.dataagent_db import scoped_session, uuid7
from libs.dataagent_db.models import audit, document
from libs.database_connector.minio_connector import MinIOClient
from libs.shared import pii_detector
from services.worker_document.indexing import Point, VectorIndex
from services.worker_document.processing import (
    OCR_THRESHOLD,
    ChunkDraft,
    ExtractedPage,
    context_header,
    document_chunks,
    extract_pages,
    injection_suspected,
    layout_json,
    source_hash,
)

log = logging.getLogger(__name__)
DOCUMENT_RECEIVED = "document.received"
# Queued by `python -m services.worker_document.reprocess`: extract a ready (or failed) document
# again, e.g. after an extractor improved; the reconciliation keeps what did not change.
DOCUMENT_REPROCESS = "document.reprocess"
ISSUE_RESOLVED = "quality_issue.resolved"
ROLE = "svc_document"
AUDIT_ROLE = "svc_audit"
RAW_BUCKET = os.environ.get("DOCUMENT_RAW_BUCKET", "da-raw-docs")
DERIVED_BUCKET = "da-doc-derived"
SKIP = {"removed", "ready", "failed", "waiting_review"}


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class Meta:
    org_id: uuid.UUID
    document_id: uuid.UUID
    collection_id: uuid.UUID
    data_space_id: uuid.UUID
    title: str
    doc_no: str | None
    effective_from: datetime.date | None
    created_by: uuid.UUID | None
    object_key: str
    media: str
    file_name: str
    qdrant_collection: str
    embed_model: str
    pii_policy: str


@dataclass
class NewChunk:
    id: uuid.UUID
    draft: ChunkDraft
    text: str
    header: str
    pii_masked: bool
    injection: bool


@dataclass
class ChunkPlan:
    new: list[NewChunk] = field(default_factory=list)
    kept: list[Any] = field(default_factory=list)
    reindex: list[Any] = field(default_factory=list)
    removed: list[uuid.UUID] = field(default_factory=list)


async def _meta(session: Any, org_id: uuid.UUID, row: document.Document) -> Meta | None:
    collection = await session.scalar(
        select(document.Collection).where(document.Collection.id == row.collection_id)
    )
    if collection is None:
        return None
    return Meta(
        org_id=org_id,
        document_id=row.id,
        collection_id=row.collection_id,
        data_space_id=collection.data_space_id,
        title=row.title or row.file_name,
        doc_no=row.doc_no,
        effective_from=row.effective_from,
        created_by=row.created_by,
        object_key=row.object_key,
        media=row.media,
        file_name=row.file_name,
        qdrant_collection=collection.qdrant_collection,
        embed_model=collection.embed_model,
        pii_policy=collection.pii_policy,
    )


def mask_for(policy: str, text: str) -> tuple[str, bool]:
    """(text to store, pii_masked) under the collection's policy (BR-110). `mask` redacts;
    `keep_for_authorized` keeps the text and marks it unmasked when it holds personal data, so
    only a person with view_detail reads it and an agent never does (BR-46)."""
    if policy == "keep_for_authorized":
        return text, pii_detector.scan(text)["total"] == 0
    masked, _ = pii_detector.mask(text)
    return masked, True


def plan_chunks(
    meta: Meta,
    page_texts: list[str],
    unchanged_pages: set[int],
    existing: list[Any],
) -> ChunkPlan:
    """What to insert, keep, re-embed and delete so the chunks match the pages."""
    plan = ChunkPlan()

    def same_pages(chunk: Any) -> bool:
        return all(n in unchanged_pages for n in range(chunk.page_from, chunk.page_to + 1))

    corrected = [c for c in existing if c.human_corrected and same_pages(c)]
    corrected_spans = {(c.page_from, c.page_to, c.section_path) for c in corrected}
    reusable = {
        (c.page_from, c.page_to, _sha(c.text)): c for c in existing if not c.human_corrected
    }
    plan.kept.extend(corrected)
    for draft in document_chunks(page_texts):
        if (draft.page_from, draft.page_to, draft.section_path) in corrected_spans:
            continue  # the person's text for this passage stands (BR-108)
        text, pii_masked = mask_for(meta.pii_policy, draft.text)
        old = reusable.pop((draft.page_from, draft.page_to, _sha(text)), None)
        if old is not None:
            plan.kept.append(old)
            continue
        plan.new.append(
            NewChunk(
                id=uuid7(),
                draft=draft,
                text=text,
                header=context_header(meta.title, meta.doc_no, draft.section_path),
                pii_masked=pii_masked,
                injection=injection_suspected(text),
            )
        )
    kept_ids = {c.id for c in plan.kept}
    plan.removed = [c.id for c in existing if c.id not in kept_ids]
    # A kept chunk without a vector of the collection's current model (an earlier indexing
    # failure, or a model change) is embedded again.
    plan.reindex = [
        c for c in plan.kept if c.qdrant_point_id is None or c.embed_model != meta.embed_model
    ]
    return plan


def _body(header: str | None, text: str) -> str:
    return f"{header}\n\n{text}" if header else text


async def _index(meta: Meta, plan: ChunkPlan) -> bool:
    """Writes the vectors before Postgres commits the chunks, so a chunk only claims a point that
    exists; a point Postgres never got is harmless (search joins back on chunk). Without Qdrant or
    an embeddings endpoint configured the document is searchable by keyword only."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        index = VectorIndex.from_env(client)
        if index is None:
            log.warning(
                "vector index not configured; document %s is keyword-only", meta.document_id
            )
            return False
        await index.sync(
            meta.qdrant_collection,
            org_id=meta.org_id,
            collection_id=meta.collection_id,
            document_id=meta.document_id,
            embed_model=meta.embed_model,
            upserts=[Point(c.id, _body(c.header, c.text)) for c in plan.new]
            + [Point(c.id, _body(c.context_header, c.text)) for c in plan.reindex],
            deletes=plan.removed,
        )
    return True


async def _index_or_keyword_only(meta: Meta, plan: ChunkPlan) -> bool:
    if not plan.new and not plan.reindex and not plan.removed:
        return True  # nothing to write: a redelivered event costs no call to Qdrant
    try:
        return await _index(meta, plan)
    except (httpx.HTTPError, ValueError):
        # The chunks are still correct text and full-text search serves them; a NULL
        # qdrant_point_id is what "not indexed yet" means, so the next run embeds them.
        log.exception("vector indexing failed id=%s; document is keyword-only", meta.document_id)
        return False


async def _write_chunks(session: Any, meta: Meta, plan: ChunkPlan, indexed: bool) -> None:
    if plan.removed:
        await session.execute(delete(document.Chunk).where(document.Chunk.id.in_(plan.removed)))
    if indexed:
        for chunk in plan.reindex:
            await session.execute(
                update(document.Chunk)
                .where(document.Chunk.id == chunk.id)
                .values(qdrant_point_id=chunk.id, embed_model=meta.embed_model)
            )
    for c in plan.new:
        session.add(
            document.Chunk(
                id=c.id,
                org_id=meta.org_id,
                document_id=meta.document_id,
                collection_id=meta.collection_id,
                page_from=c.draft.page_from,
                page_to=c.draft.page_to,
                section_path=c.draft.section_path,
                context_header=c.header or None,
                text=c.text,
                tokens=len(c.text.split()),
                qdrant_point_id=c.id if indexed else None,
                embed_model=meta.embed_model if indexed else None,
                effective_from=meta.effective_from,
                pii_masked=c.pii_masked,
                injection_suspected=c.injection,
            )
        )


async def _finish(session: Any, meta: Meta, page_count: int | None = None) -> str:
    """ready, or waiting_review while a blocking quality issue is open (UC-05-03)."""
    blocking = await session.scalar(
        select(func.count())
        .select_from(document.QualityIssue)
        .where(
            document.QualityIssue.document_id == meta.document_id,
            document.QualityIssue.status == "open",
            document.QualityIssue.severity == "blocking",
        )
    )
    status = "waiting_review" if blocking else "ready"
    values: dict[str, Any] = {"status": status, "error": None, "updated_at": _now()}
    if page_count is not None:
        values["page_count"] = page_count
    await session.execute(
        update(document.Document).where(document.Document.id == meta.document_id).values(**values)
    )
    return status


async def _report_injections(meta: Meta, plan: ChunkPlan) -> None:
    """UC-05-02 E2: one security_event per passage flagged. Audit is its own database (D1.1), so
    this is written after the chunks commit; a failure is logged and leaves the flag in place."""
    flagged = [c for c in plan.new if c.injection]
    if not flagged:
        return
    try:
        async with scoped_session(AUDIT_ROLE, meta.org_id) as session:
            for c in flagged:
                session.add(
                    audit.SecurityEvent(
                        org_id=meta.org_id,
                        kind="prompt_injection",
                        severity="medium",
                        data_space_id=meta.data_space_id,
                        source_ref=f"document.chunk/{c.id}",
                        detail={
                            "document_id": str(meta.document_id),
                            "chunk_id": str(c.id),
                            "page_from": c.draft.page_from,
                            "page_to": c.draft.page_to,
                            "section_path": c.draft.section_path,
                        },
                    )
                )
    except Exception:  # the audit database being down must not fail the document
        log.exception("security_event not written for document %s", meta.document_id)


async def _fail(org_id: uuid.UUID, document_id: uuid.UUID, error: str) -> None:
    async with scoped_session(ROLE, org_id) as session:
        await session.execute(
            update(document.Document)
            .where(document.Document.id == document_id)
            .values(status="failed", error=error[:1_000], updated_at=_now())
        )


async def reprocess_document(event: Any) -> None:
    await process_document(event, again=True)


async def process_document(event: Any, *, again: bool = False) -> None:
    document_id = uuid.UUID(str(event.aggregate_id))
    org_id = event.org_id
    async with scoped_session(ROLE, org_id) as session:
        row = await session.scalar(
            select(document.Document).where(document.Document.id == document_id)
        )
        if row is None or row.status in ({"removed"} if again else SKIP):
            return
        meta = await _meta(session, org_id, row)
        if meta is None:
            return
        await session.execute(
            update(document.Document)
            .where(document.Document.id == document_id)
            .values(status="processing", error=None, updated_at=_now())
        )
        existing_pages = {
            p.page_no: p
            for p in (
                await session.scalars(
                    select(document.DocumentPage).where(
                        document.DocumentPage.document_id == document_id
                    )
                )
            ).all()
        }
        existing_chunks = list(
            (
                await session.scalars(
                    select(document.Chunk).where(document.Chunk.document_id == document_id)
                )
            ).all()
        )

    minio = MinIOClient()
    minio.set_url(os.environ["MINIO_URL"])
    await minio.open()
    try:
        try:
            content = await minio.download(RAW_BUCKET, meta.object_key)
            pages = extract_pages(content, meta.media, meta.file_name)
            if not pages:
                raise ValueError("The document has no readable pages")
        except Exception as exc:  # any extraction failure ends in `failed`, said
            await _fail(org_id, document_id, str(exc))
            log.exception("document processing failed id=%s", document_id)
            return
        if meta.pii_policy == "reject" and any(pii_detector.scan(p.text)["total"] for p in pages):
            await _fail(
                org_id, document_id, "The collection rejects documents holding personal data."
            )
            return

        hashes = [source_hash(p) for p in pages]
        unchanged = {
            n
            for n, h in enumerate(hashes, start=1)
            if n in existing_pages and existing_pages[n].page_hash == h
        }
        page_texts = [
            existing_pages[n].text or "" if n in unchanged else mask_for(meta.pii_policy, p.text)[0]
            for n, p in enumerate(pages, start=1)
        ]
        plan = plan_chunks(meta, page_texts, unchanged, existing_chunks)
        indexed = await _index_or_keyword_only(meta, plan)
        derived = {
            n: await _upload_derived(minio, meta, n, p)
            for n, p in enumerate(pages, start=1)
            if n not in unchanged
        }

        async with scoped_session(ROLE, org_id) as session:
            row = await session.scalar(
                select(document.Document)
                .where(document.Document.id == document_id)
                .with_for_update()
            )
            if row is None or row.status == "removed":
                return
            await session.execute(
                delete(document.DocumentPage).where(
                    document.DocumentPage.document_id == document_id,
                    document.DocumentPage.page_no > len(pages),
                )
            )
            for n, page in enumerate(pages, start=1):
                if n in unchanged:
                    continue
                await _write_page(
                    session, meta, n, page, hashes[n - 1], page_texts[n - 1], derived[n]
                )
            await _write_chunks(session, meta, plan, indexed)
            status = await _finish(session, meta, page_count=len(pages))
        log.info(
            "document processed id=%s status=%s pages=%d (unchanged %d) chunks new=%d kept=%d removed=%d indexed=%s",
            document_id,
            status,
            len(pages),
            len(unchanged),
            len(plan.new),
            len(plan.kept),
            len(plan.removed),
            indexed,
        )
        await _report_injections(meta, plan)
    finally:
        await minio.close()


async def _upload_derived(
    minio: MinIOClient, meta: Meta, page_no: int, page: ExtractedPage
) -> tuple[str | None, str]:
    base = f"{meta.org_id}/{meta.document_id}/page-{page_no}"
    image_ref = None
    if page.image:
        image_ref = f"{base}.png"
        await minio.upload(DERIVED_BUCKET, image_ref, page.image, "image/png")
    embedded_refs: list[str] = []
    for image_no, embedded in enumerate(page.images, start=1):
        ref = f"{base}-image-{image_no}.{embedded.extension}"
        await minio.upload(DERIVED_BUCKET, ref, embedded.data, f"image/{embedded.extension}")
        embedded_refs.append(ref)
    layout_ref = f"{base}.layout.json"
    await minio.upload(
        DERIVED_BUCKET,
        layout_ref,
        layout_json(page, embedded_refs, page.width, page.height),
        "application/json",
    )
    return image_ref, layout_ref


async def _write_page(
    session: Any,
    meta: Meta,
    page_no: int,
    page: ExtractedPage,
    hash_: str,
    text: str,
    refs: tuple[str | None, str],
) -> None:
    """The page as extracted now; a changed page loses an earlier manual correction (its source
    is no longer what the person corrected). Low OCR opens a blocking issue unless one is open;
    a page with no text at all opens a warning, since a blank page is often meant to be blank."""
    low_ocr = page.ocr_conf is not None and page.ocr_conf < OCR_THRESHOLD
    image_ref, layout_ref = refs
    values = {
        "page_hash": hash_,
        "text_layer": page.text_layer,
        "ocr_conf": page.ocr_conf,
        "layout_ref": layout_ref,
        "image_ref": image_ref,
        "text": text or None,
        "human_corrected": False,
        "usable": bool(text) and not low_ocr,
        "updated_at": _now(),
    }
    found = await session.scalar(
        select(document.DocumentPage).where(
            document.DocumentPage.document_id == meta.document_id,
            document.DocumentPage.page_no == page_no,
        )
    )
    if found is None:
        session.add(
            document.DocumentPage(
                id=uuid7(),
                org_id=meta.org_id,
                document_id=meta.document_id,
                page_no=page_no,
                created_by=meta.created_by,
                **values,
            )
        )
    else:
        for key, value in values.items():
            setattr(found, key, value)
    if not low_ocr and text:
        return
    already = await session.scalar(
        select(document.QualityIssue.id).where(
            document.QualityIssue.document_id == meta.document_id,
            document.QualityIssue.page_no == page_no,
            document.QualityIssue.kind == "low_ocr",
            document.QualityIssue.status == "open",
        )
    )
    if already is None:
        session.add(
            document.QualityIssue(
                id=uuid7(),
                org_id=meta.org_id,
                document_id=meta.document_id,
                page_no=page_no,
                kind="low_ocr",
                severity="blocking" if text else "warning",
                detail={"ocr_conf": page.ocr_conf, "threshold": OCR_THRESHOLD, "empty": not text},
                created_by=meta.created_by,
            )
        )


async def process_resolution(event: Any) -> None:
    """UC-05-03 step 4: the later steps again, from the stored (corrected) pages."""
    document_id = uuid.UUID(str(event.payload["document_id"]))
    org_id = event.org_id
    async with scoped_session(ROLE, org_id) as session:
        row = await session.scalar(
            select(document.Document).where(document.Document.id == document_id)
        )
        if row is None or row.status not in {"waiting_review", "ready"}:
            return
        meta = await _meta(session, org_id, row)
        if meta is None:
            return
        pages = list(
            (
                await session.scalars(
                    select(document.DocumentPage)
                    .where(document.DocumentPage.document_id == document_id)
                    .order_by(document.DocumentPage.page_no)
                )
            ).all()
        )
        existing_chunks = list(
            (
                await session.scalars(
                    select(document.Chunk).where(document.Chunk.document_id == document_id)
                )
            ).all()
        )
    texts = {p.page_no: p.text or "" for p in pages}
    page_texts = [texts.get(n, "") for n in range(1, max(texts, default=0) + 1)]
    plan = plan_chunks(meta, page_texts, set(texts), existing_chunks)
    indexed = await _index_or_keyword_only(meta, plan)
    async with scoped_session(ROLE, org_id) as session:
        row = await session.scalar(
            select(document.Document).where(document.Document.id == document_id).with_for_update()
        )
        if row is None or row.status not in {"waiting_review", "ready"}:
            return
        await _write_chunks(session, meta, plan, indexed)
        status = await _finish(session, meta)
    log.info(
        "document re-run after review id=%s status=%s chunks new=%d removed=%d",
        document_id,
        status,
        len(plan.new),
        len(plan.removed),
    )
    await _report_injections(meta, plan)


HANDLERS: dict[str, Callable[[Any], Awaitable[None]]] = {
    DOCUMENT_RECEIVED: process_document,
    DOCUMENT_REPROCESS: reprocess_document,
    ISSUE_RESOLVED: process_resolution,
}
