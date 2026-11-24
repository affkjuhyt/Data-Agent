"""What worker-agent does when worker-document marks a document ready (document.indexed).

Two model readings of the same passages, each idempotent on its own rows so a redelivered event is
harmless: the fields of the collection's extraction schema (extraction.py, KB-07; then loaded into
the collection's extracted dataset, extracted_dataset.py, DI-17) and the claims
that may contradict other documents (claims.py, KB-05). Neither is needed to search or cite the
document; without a gateway both are skipped.
"""

from __future__ import annotations

import uuid

from services.worker_agent.documents import claims, extracted_dataset, extraction
from services.worker_agent.events import Event, Handler

INDEXED = "document.indexed"


async def document_indexed(event: Event) -> None:
    # The gateway the questions use; read through the module so tests can replace it there.
    from services.worker_agent import questions

    gateway = questions.gateway()
    document_id = uuid.UUID(str(event.payload.get("document_id") or event.aggregate_id))
    if await extraction.extract_document(gateway, event.org_id, document_id) is not None:
        # DI-17: the new record reaches the collection's extracted dataset (an upsert into its
        # current version; nothing is written for a record waiting for review).
        collection_id = await extracted_dataset.collection_of(event.org_id, document_id)
        if collection_id is not None:
            await extracted_dataset.load(event.org_id, collection_id)
    await claims.read_document(gateway, event.org_id, document_id)


HANDLERS: dict[str, Handler] = {INDEXED: document_indexed}
