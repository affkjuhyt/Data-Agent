"""Storing what the publisher actually returned, through the bronze entry.

An API response is an artifact. The first version of this connector went from
HTTP straight to `ankt.observation` — correct numbers, nothing else: no bytes to
re-check, no hash, nothing in Data Layers. README section 3.3 puts Artifact
between Release and Extraction for exactly that reason.

The second version stored the bytes but wrote `KBData` with its own INSERT, and
that was still wrong in a quieter way. `upload_to_bronze` does four things, not
two: it uploads, inserts the document, inserts a `KBLifecycleHistory` row in the
SAME transaction, and deletes the MinIO object if either insert fails. Its own
comment records why the history is not optional — "a document with no history
was previously possible whenever the second insert failed" — so a parallel
implementation reintroduced a bug the repo had already fixed, and left orphaned
objects behind on failure.

So this calls that function. A connector's artifact and an uploaded file are
the same kind of thing and take the same door; anything else is a second bronze
path that will drift from the first.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import uuid
from dataclasses import dataclass
from typing import Any

from services.ankt_connectors.db import fetch_one

# The role a connector acts as. It has no request context, so it cannot inherit
# the caller's; PLATFORM_ADMIN matches what the ingestion routes resolve for an
# administrator and keeps the KBData foreign key satisfied. When connectors get
# their own service identity this becomes that identity.
CONNECTOR_ROLE = "PLATFORM_ADMIN"


@dataclass(frozen=True)
class StoredArtifact:
    data_id: str
    sha256: str
    object_key: str
    already_present: bool
    """True when this exact response was stored before. The connector then has
    a decision `lastupdated` alone could not support: nothing changed, so
    nothing downstream needs recomputing."""


async def store(
    session: Any,
    minio: Any,
    postgres: Any,
    tenant_id: uuid.UUID,
    source_code: str,
    series_key: str,
    fetched: Any,
    added_by: str = "ankt-connector",
) -> StoredArtifact:
    """Register the response as a bronze document, exactly as an upload would.

    The hash is the stronger idempotency key. A publisher's `lastupdated` is a
    claim; SHA-256 of the response is a fact, and the two disagree when a source
    re-stamps a file it did not change.
    """
    from libs.basemodel.services_databaseconnector.postgres_model import (
        Language,
        SourceType,
    )
    from services.kb_backend.config import resolve_role_id
    from services.kb_backend.pipeline.ingestion.bronze import upload_to_bronze

    digest = hashlib.sha256(fetched.raw).hexdigest()
    existing = await fetch_one(
        session,
        """SELECT data_id, path FROM "KBData"
            WHERE tenant_id = :tenant_id
              AND metadata->>'sha256' = :sha256
              AND is_deleted = false
            LIMIT 1""",
        tenant_id=tenant_id, sha256=digest,
    )
    if existing:
        return StoredArtifact(str(existing["data_id"]), digest, existing["path"], True)

    extension = "csv" if "csv" in fetched.content_type else "json"
    filename = (f"{source_code}_{series_key.replace('/', '_')}"
                f"_{fetched.release_date.isoformat()}.{extension}")

    result = await upload_to_bronze(
        minio=minio,
        postgres=postgres,
        file_data=fetched.raw,
        name=filename,
        filename=filename,
        extension=extension,
        source_type=SourceType.API,
        tenant_id=str(tenant_id),
        role_id=resolve_role_id(CONNECTOR_ROLE),
        added_by=added_by,
        abstract=f"{source_code} · {series_key} · {fetched.vintage_label}",
        language=Language.EN,
        doc_metadata={
            "source_type": SourceType.API.value,
            "sha256": digest,
            "ankt_source": source_code,
            "series_key": series_key,
            "vintage_label": fetched.vintage_label,
            "release_date": fetched.release_date.isoformat(),
            "fetched_at": dt.datetime.now(dt.UTC).isoformat(),
            "observations": len(fetched.points),
            "bytes": len(fetched.raw),
        },
        # Rows, not prose. The figures are already structured in the response,
        # so promoting this through the document chunker would produce chunks
        # nobody should retrieve.
        ingestion_mode="structured_table",
    )
    return StoredArtifact(result["data_id"], digest, result["path"], False)
