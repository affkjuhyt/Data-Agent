"""What worker-agent does when worker-document marks a document ready (document.indexed).

Two model readings of the same passages, each idempotent on its own rows so a redelivered event is
harmless: the fields of the collection's extraction schema (extraction.py, KB-07) and the claims
that may contradict other documents (claims.py, KB-05). Neither is needed to search or cite the
document; without a gateway both are skipped.
"""

from __future__ import annotations

import uuid

from services.worker_agent.documents import claims, extraction
from services.worker_agent.events import Event, Handler

INDEXED = "document.indexed"


async def document_indexed(event: Event) -> None:
    # The gateway the questions use; read through the module so tests can replace it there.
    from services.worker_agent import questions

    gateway = questions.gateway()
    document_id = uuid.UUID(str(event.payload.get("document_id") or event.aggregate_id))
    await extraction.extract_document(gateway, event.org_id, document_id)
    await claims.read_document(gateway, event.org_id, document_id)


HANDLERS: dict[str, Handler] = {INDEXED: document_indexed}
