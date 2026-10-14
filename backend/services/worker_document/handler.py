from __future__ import annotations

import datetime
import logging
import os
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import delete, select, update

from libs.dataagent_db import scoped_session, uuid7
from libs.dataagent_db.models import document
from libs.database_connector.minio_connector import MinIOClient
from services.worker_document.processing import extract_pages, page_chunks, page_hash

log = logging.getLogger(__name__)
DOCUMENT_RECEIVED = "document.received"
ROLE = "svc_document"
RAW_BUCKET = os.environ.get("DOCUMENT_RAW_BUCKET", "da-raw-docs")


async def process_document(event: Any) -> None:
    document_id = uuid.UUID(str(event.aggregate_id))
    org_id = event.org_id
    minio = MinIOClient()
    minio.set_url(os.environ["MINIO_URL"])
    await minio.open()
    try:
        async with scoped_session(ROLE, org_id) as session:
            row = await session.scalar(
                select(document.Document).where(document.Document.id == document_id)
            )
            if row is None or row.status in {"removed", "ready", "failed", "waiting_review"}:
                return
            await session.execute(
                update(document.Document)
                .where(document.Document.id == document_id)
                .values(status="processing", error=None, updated_at=datetime.datetime.now(datetime.UTC))
            )
            object_key, media = row.object_key, row.media

        try:
            content = await minio.download(RAW_BUCKET, object_key)
            pages = extract_pages(content, media)
            if not pages:
                raise ValueError("The document has no readable pages")
        except Exception as exc:
            async with scoped_session(ROLE, org_id) as session:
                await session.execute(
                    update(document.Document)
                    .where(document.Document.id == document_id)
                    .values(status="failed", error=str(exc)[:1_000], updated_at=datetime.datetime.now(datetime.UTC))
                )
            log.exception("document processing failed id=%s", document_id)
            return

        async with scoped_session(ROLE, org_id) as session:
            row = await session.scalar(
                select(document.Document).where(document.Document.id == document_id).with_for_update()
            )
            if row is None or row.status == "removed":
                return
            await session.execute(delete(document.Chunk).where(document.Chunk.document_id == document_id))
            await session.execute(delete(document.DocumentPage).where(document.DocumentPage.document_id == document_id))
            for page_no, extracted in enumerate(pages, start=1):
                text = extracted.text
                image_ref = None
                if extracted.image:
                    image_ref = f"{org_id}/{document_id}/page-{page_no}.png"
                    await minio.upload("da-doc-derived", image_ref, extracted.image, "image/png")
                page = document.DocumentPage(
                    id=uuid7(), org_id=org_id, document_id=document_id, page_no=page_no,
                    page_hash=page_hash(text), text_layer=extracted.text_layer, ocr_conf=extracted.ocr_conf,
                    image_ref=image_ref, text=text or None,
                    usable=bool(text), created_by=row.created_by, updated_at=datetime.datetime.now(datetime.UTC),
                )
                session.add(page)
                for chunk_text in page_chunks(text):
                    session.add(document.Chunk(
                        id=uuid7(), org_id=org_id, document_id=document_id,
                        collection_id=row.collection_id, page_from=page_no, page_to=page_no,
                        text=chunk_text, tokens=len(chunk_text.split()),
                        effective_from=row.effective_from, pii_masked=True,
                    ))
            await session.execute(
                update(document.Document)
                .where(document.Document.id == document_id)
                .values(
                    page_count=len(pages), status="ready", error=None,
                    updated_at=datetime.datetime.now(datetime.UTC),
                )
            )
            log.info("document processed id=%s pages=%d", document_id, len(pages))
    finally:
        await minio.close()


HANDLERS: dict[str, Callable[[Any], Awaitable[None]]] = {DOCUMENT_RECEIVED: process_document}
