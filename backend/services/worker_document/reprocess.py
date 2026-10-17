"""Queue documents to be processed again (document.reprocess on document.outbox).

For documents read by an earlier extractor (an HTML page stripped of its tags, a page OCR'd with
a made-up confidence) or indexed while the embedding service was down. The worker reconciles, so
what did not change keeps its pages, corrections and vectors.

    kubectl -n dataagent-service exec deploy/worker-document -- \\
        python -m services.worker_document.reprocess --org <org id> --media html
    ... --collection <collection id>        every document of one collection
    ... --document <id> [--document <id>]   named documents
    ... --unindexed                         only documents with a chunk not in Qdrant yet
"""

from __future__ import annotations

import argparse
import asyncio
import uuid

from sqlalchemy import exists, select

from libs.dataagent_db import scoped_session, uuid7
from libs.dataagent_db.models import document
from services.worker_document.handler import DOCUMENT_REPROCESS, ROLE


async def queue(
    org_id: uuid.UUID,
    *,
    documents: list[uuid.UUID],
    collection_id: uuid.UUID | None,
    media: str | None,
    unindexed: bool,
) -> int:
    d = document.Document
    query = select(d.id).where(d.deleted_at.is_(None), d.status != "removed")
    if documents:
        query = query.where(d.id.in_(documents))
    if collection_id is not None:
        query = query.where(d.collection_id == collection_id)
    if media is not None:
        query = query.where(d.media == media)
    if unindexed:
        query = query.where(
            exists().where(
                document.Chunk.document_id == d.id, document.Chunk.qdrant_point_id.is_(None)
            )
        )
    async with scoped_session(ROLE, org_id) as session:
        ids = list((await session.scalars(query)).all())
        for document_id in ids:
            session.add(
                document.Outbox(
                    id=uuid7(),
                    org_id=org_id,
                    aggregate_type="document",
                    aggregate_id=document_id,
                    event_type=DOCUMENT_REPROCESS,
                    payload={"document_id": str(document_id)},
                )
            )
    return len(ids)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--org", type=uuid.UUID, required=True)
    parser.add_argument("--document", type=uuid.UUID, action="append", default=[])
    parser.add_argument("--collection", type=uuid.UUID)
    parser.add_argument("--media", choices=["pdf", "image", "docx", "email", "audio", "html"])
    parser.add_argument("--unindexed", action="store_true")
    args = parser.parse_args()
    if not (args.document or args.collection or args.media or args.unindexed):
        parser.error("name what to reprocess: --document, --collection, --media or --unindexed")
    count = asyncio.run(
        queue(
            args.org,
            documents=args.document,
            collection_id=args.collection,
            media=args.media,
            unindexed=args.unindexed,
        )
    )
    print(f"queued {count} document(s) for reprocessing")


if __name__ == "__main__":
    main()
