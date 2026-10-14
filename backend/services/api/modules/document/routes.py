"""A2 group Tài liệu: collections, documents, search, quality queue, extraction schemas.

UC-05-01 to UC-05-06 (schema document). Preprocessing (the eight steps of UC-05-02), source
syncs and extraction test runs are worker-ingest's: this module stores the original, writes the
row and the event to document.outbox in the same transaction, and answers 202 (A1.6). A change
of validity (UC-05-05) is proposed, never applied here: the reviewer decides in Cần bạn.
"""

from __future__ import annotations

import contextlib
import datetime
import decimal
import hashlib
import json
import logging
import mimetypes
import os
import uuid
from collections.abc import Callable, Sequence
from typing import Annotated, Any, TypeVar
from urllib.parse import unquote, urlsplit

from fastapi import APIRouter, Depends, File, Form, Header, Path, Query, Response, UploadFile
from sqlalchemy import ColumnElement, and_, case, exists, func, literal_column, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db import uuid7
from libs.dataagent_db.models import catalog
from libs.dataagent_db.models import document as doc
from services.api.catalogue import endpoint
from services.api.kernel.auth import CurrentDataSpace, CurrentPrincipal, Principal
from services.api.kernel.db import transaction
from services.api.kernel.pagination import (
    Order,
    Page,
    PageRequest,
    encode_page_token,
    invalid_page_token,
    keyset,
    next_page,
    order_by,
    page_request,
)
from services.api.kernel.problems import Problem
from services.api.modules.document.ports import (
    DERIVED_BUCKET,
    RAW_BUCKET,
    ObjectStore,
    SecretStore,
    VectorSearch,
    VectorTarget,
    object_store,
    secret_store,
    vector_search,
)
from services.api.modules.document.schemas import (
    Accepted,
    ChunkHit,
    ChunkView,
    CollectionCreate,
    CollectionView,
    DocumentBlockView,
    DocumentDetail,
    DocumentListItem,
    DocumentPageView,
    DocumentPatch,
    DocumentSourceCreate,
    DocumentSourceView,
    DocumentUpdated,
    DocumentView,
    ExtractionSchemaCreate,
    ExtractionSchemaView,
    ExtractionTest,
    PreprocessingStep,
    Progress,
    QualityIssueView,
    ResolveIssue,
    SearchRequest,
    ValidityChange,
)

logger = logging.getLogger(__name__)

router = APIRouter()
ROLE = "svc_document"
IdPath = Annotated[uuid.UUID, Path(alias="id")]
# BR-59: the purpose an agent run (or a caller) reads under; a collection of another purpose is
# out of reach. Without it the caller reads as themselves, within their Data Space rights.
Purpose = Annotated[str | None, Header(alias="X-Purpose", max_length=500)]

# UC-05-01 E2 and the CHECK on document.size_bytes.
MAX_DOCUMENT_BYTES = 200 * 1024 * 1024
MEDIA_BY_EXTENSION = {
    ".pdf": "pdf",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".tif": "image",
    ".tiff": "image",
    ".webp": "image",
    ".docx": "docx",
    ".eml": "email",
    ".msg": "email",
    ".mp3": "audio",
    ".wav": "audio",
    ".m4a": "audio",
    ".ogg": "audio",
    ".html": "html",
    ".htm": "html",
}
# The embedding model of new collections (BR-18: recorded per collection and per vector).
EMBED_MODEL = os.environ.get("DOCUMENT_EMBED_MODEL", "bge-m3")
IMAGE_URL_TTL_SECONDS = 300
# BR-110 / NFR-35: every required field at least 95% accurate on the sample.
ACTIVATION_ACCURACY = decimal.Decimal("0.95")
# Reciprocal rank fusion constant for the vector and keyword lists (UC-05-04 step 1).
RRF_K = 60
# Resolving a personal-data issue: AC03 (steward) or AC06 (compliance), UC-05-03 E1.
PII_CAPABILITIES = frozenset({"steward", "compliance"})

R = TypeVar("R")


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _plain(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime.date | datetime.datetime):
        return value.isoformat()
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def _event(
    principal: Principal,
    aggregate_type: str,
    aggregate_id: uuid.UUID,
    event_type: str,
    **payload: Any,
) -> doc.Outbox:
    return doc.Outbox(
        id=uuid7(),
        org_id=principal.org_id,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        event_type=event_type,
        payload={k: _plain(v) for k, v in payload.items()},
    )


def _cut(
    rows: Sequence[R], page: PageRequest, key: Callable[[R], dict[str, Any]]
) -> tuple[list[R], str]:
    kept = list(rows[: page.page_size])
    return kept, encode_page_token(key(kept[-1]) if len(rows) > page.page_size else None)


def _in_space(principal: Principal, data_space: uuid.UUID) -> ColumnElement[bool]:
    return and_(
        doc.Collection.org_id == principal.org_id,
        doc.Collection.data_space_id == data_space,
        doc.Collection.deleted_at.is_(None),
    )


async def _collection(
    session: AsyncSession,
    collection_id: uuid.UUID,
    principal: Principal,
    data_space: uuid.UUID,
    *,
    lock: bool = False,
) -> doc.Collection:
    query = select(doc.Collection).where(
        doc.Collection.id == collection_id, _in_space(principal, data_space)
    )
    if lock:
        query = query.with_for_update()
    row = await session.scalar(query)
    if row is None:
        raise Problem(404, "not_found", "Collection not found.")
    return row


def _purpose(header: str | None) -> str | None:
    """The X-Purpose value as text. Purposes are Vietnamese and HTTP headers are latin-1, so a
    client sends it percent-encoded (UTF-8); raw UTF-8 bytes, which arrive decoded as latin-1,
    are accepted too. Without this no Vietnamese purpose could ever match its collection."""
    if header is None:
        return None
    with contextlib.suppress(UnicodeEncodeError, UnicodeDecodeError):
        header = header.encode("latin-1").decode("utf-8")
    return unquote(header).strip()


def _check_purpose(collection: doc.Collection, purpose: str | None) -> None:
    if purpose is not None and purpose != collection.purpose:
        raise Problem(403, "purpose_mismatch", "The collection serves another purpose (BR-59).")


def _sees_personal_data(principal: Principal) -> bool:
    # BR-110 and UC-05-03 E1: personal data a keep_for_authorized collection keeps unmasked is
    # for a person with view_detail. A client or agent acting for that person (client_id set)
    # reads masked text only: an agent never sees unmasked personal data (BR-46).
    return "view_detail" in principal.capabilities and principal.client_id is None


# --- collections --------------------------------------------------------------------------------


@router.get(
    "/v1/collections",
    response_model=Page[CollectionView],
    **endpoint("GET", "/v1/collections", operation_id="listCollections"),
)
async def list_collections(
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    page: Annotated[PageRequest, Depends(page_request)],
    order: Annotated[
        Order,
        Depends(
            order_by(
                "created_at", "updated_at", "name", "code", "status", default="created_at desc"
            )
        ),
    ],
    status: str | None = None,
) -> Page[CollectionView]:
    col = doc.Collection
    query = select(col).where(_in_space(principal, data_space))
    if status:
        query = query.where(col.status == status)
    query = keyset(
        query,
        columns={
            "created_at": col.created_at,
            "updated_at": col.updated_at,
            "name": col.name,
            "code": col.code,
            "status": col.status,
        },
        tiebreak=col.id,
        order=order,
        page=page,
    )
    async with transaction(ROLE, principal) as session:
        rows, token = next_page(list((await session.scalars(query)).all()), order=order, page=page)
    return Page(
        items=[CollectionView.model_validate(r, from_attributes=True) for r in rows],
        next_page_token=token,
    )


@router.post(
    "/v1/collections",
    response_model=CollectionView,
    status_code=201,
    **endpoint("POST", "/v1/collections", operation_id="createCollection"),
)
async def create_collection(
    body: CollectionCreate, principal: CurrentPrincipal, data_space: CurrentDataSpace
) -> CollectionView:
    collection_id = uuid7()
    async with transaction(ROLE, principal) as session:
        existing = await session.scalar(
            select(doc.Collection.id).where(
                doc.Collection.org_id == principal.org_id,
                doc.Collection.data_space_id == data_space,
                doc.Collection.code == body.code,
            )
        )
        if existing is not None:
            raise Problem(
                409,
                "conflict",
                f"A collection with code {body.code!r} already exists in this Data Space.",
            )
        row = doc.Collection(
            id=collection_id,
            org_id=principal.org_id,
            data_space_id=data_space,
            code=body.code,
            name=body.name,
            purpose=body.purpose,
            retention_days=body.retention_days,
            # One Qdrant collection per document collection, so purposes never share an index.
            qdrant_collection=f"doc_{collection_id.hex}",
            embed_model=EMBED_MODEL,
            created_by=principal.user_id,
            updated_by=principal.user_id,
        )
        if body.media_kinds is not None:
            row.media_kinds = list(dict.fromkeys(body.media_kinds))
        if body.pii_policy is not None:
            row.pii_policy = body.pii_policy
        session.add(row)
        await session.flush()
        await session.refresh(row)
        # worker-ingest creates the vector collection.
        session.add(
            _event(
                principal,
                "collection",
                row.id,
                "collection.created",
                collection_id=row.id,
                data_space_id=data_space,
                qdrant_collection=row.qdrant_collection,
                embed_model=row.embed_model,
                status=row.status,
            )
        )
        return CollectionView.model_validate(row, from_attributes=True)


def _source_view(row: doc.DocumentSource) -> DocumentSourceView:
    return DocumentSourceView(
        id=row.id,
        collection_id=row.collection_id,
        kind=row.kind,
        location=row.location,
        sync_cron=row.sync_cron,
        last_synced_at=row.last_synced_at,
        status=row.status,
        has_credentials=row.secret_ref is not None,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.post(
    "/v1/document-sources",
    response_model=DocumentSourceView,
    status_code=201,
    **endpoint("POST", "/v1/document-sources", operation_id="createDocumentSource"),
)
async def create_document_source(
    body: DocumentSourceCreate,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    secrets: Annotated[SecretStore, Depends(secret_store)],
) -> DocumentSourceView:
    try:
        password = urlsplit(body.location).password
    except ValueError:
        password = None
    if password:
        # Credentials go to the secret store, never into a column the API returns.
        raise Problem(
            422, "validation_failed", "Put credentials in `credentials`, not in the location."
        )
    source_id = uuid7()
    async with transaction(ROLE, principal) as session:
        collection = await _collection(session, body.collection_id, principal, data_space)
        secret_ref = None
        if body.credentials:
            secret_ref = await secrets.write(
                principal.org_id, f"document-source/{source_id}", body.credentials
            )
        row = doc.DocumentSource(
            id=source_id,
            org_id=principal.org_id,
            collection_id=collection.id,
            kind=body.kind,
            location=body.location,
            secret_ref=secret_ref,
            sync_cron=body.sync_cron,
            created_by=principal.user_id,
            updated_by=principal.user_id,
        )
        session.add(row)
        await session.flush()
        await session.refresh(row)
        session.add(
            _event(
                principal,
                "document_source",
                row.id,
                "document_source.created",
                document_source_id=row.id,
                collection_id=collection.id,
                data_space_id=data_space,
                kind=row.kind,
                status=row.status,
            )
        )
        return _source_view(row)


@router.get(
    "/v1/document-sources",
    response_model=Page[DocumentSourceView],
    **endpoint("GET", "/v1/document-sources", operation_id="listDocumentSources"),
)
async def list_document_sources(
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    page: Annotated[PageRequest, Depends(page_request)],
    order: Annotated[
        Order,
        Depends(order_by("created_at", "updated_at", "kind", "status", default="created_at desc")),
    ],
    collection_id: uuid.UUID | None = None,
) -> Page[DocumentSourceView]:
    src = doc.DocumentSource
    query = (
        select(src)
        .join(doc.Collection, doc.Collection.id == src.collection_id)
        .where(_in_space(principal, data_space))
    )
    if collection_id is not None:
        query = query.where(src.collection_id == collection_id)
    query = keyset(
        query,
        columns={
            "created_at": src.created_at,
            "updated_at": src.updated_at,
            "kind": src.kind,
            "status": src.status,
        },
        tiebreak=src.id,
        order=order,
        page=page,
    )
    async with transaction(ROLE, principal) as session:
        rows, token = next_page(list((await session.scalars(query)).all()), order=order, page=page)
    # _source_view leaves secret_ref out: only whether credentials are stored.
    return Page(items=[_source_view(r) for r in rows], next_page_token=token)


@router.post(
    "/v1/document-sources/{id}:sync",
    response_model=Accepted,
    status_code=202,
    **endpoint("POST", "/v1/document-sources/{id}:sync", operation_id="syncDocumentSource"),
)
async def sync_document_source(
    source_id: IdPath,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    response: Response,
) -> Accepted:
    async with transaction(ROLE, principal) as session:
        row = await session.scalar(
            select(doc.DocumentSource)
            .join(doc.Collection, doc.Collection.id == doc.DocumentSource.collection_id)
            .where(doc.DocumentSource.id == source_id, _in_space(principal, data_space))
        )
        if row is None:
            raise Problem(404, "not_found", "Document source not found.")
        if row.status == "paused":
            raise Problem(409, "invalid_state", "The source is paused.")
        # UC-05-01 1a: new or changed files are ingested, files gone at the source are removed.
        session.add(
            _event(
                principal,
                "document_source",
                row.id,
                "document_source.sync_requested",
                document_source_id=row.id,
                collection_id=row.collection_id,
                data_space_id=data_space,
                requested_by=principal.user_id,
                status=row.status,
            )
        )
    response.headers["Location"] = f"/v1/document-sources/{row.id}"
    return Accepted(id=row.id, status="queued")


# --- documents ----------------------------------------------------------------------------------


async def _hash(upload: UploadFile) -> tuple[str, int]:
    digest, size = hashlib.sha256(), 0
    while chunk := await upload.read(1 << 20):
        size += len(chunk)
        if size > MAX_DOCUMENT_BYTES:
            raise Problem(
                413, "payload_too_large", "The file is larger than 200 MB; split it first."
            )
        digest.update(chunk)
    await upload.seek(0)
    return digest.hexdigest(), size


@router.post(
    "/v1/documents",
    response_model=Accepted,
    status_code=202,
    **endpoint("POST", "/v1/documents", operation_id="uploadDocument"),
)
async def upload_document(
    file: Annotated[UploadFile, File()],
    collection_id: Annotated[uuid.UUID, Form()],
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    response: Response,
    store: Annotated[ObjectStore, Depends(object_store)],
    doc_no: Annotated[str | None, Form(max_length=200)] = None,
    effective_from: Annotated[datetime.date | None, Form()] = None,
) -> Accepted:
    name = os.path.basename(file.filename or "").strip()
    media = MEDIA_BY_EXTENSION.get(os.path.splitext(name)[1].lower())
    if media is None:
        # UC-05-01 E1: say why instead of failing later in preprocessing.
        raise Problem(422, "validation_failed", "This file type is not supported.")
    sha256, size = await _hash(file)
    if size == 0:
        raise Problem(422, "validation_failed", "The file is empty.")
    async with transaction(ROLE, principal) as session:
        # BR-107: a document lives in a collection and inherits its rights.
        collection = await _collection(session, collection_id, principal, data_space)
        if collection.status == "archived":
            raise Problem(409, "invalid_state", "The collection is archived.")
        if collection.media_kinds and media not in collection.media_kinds:
            raise Problem(
                422, "business_rule_violated", f"The collection does not take {media} files."
            )
        same = await session.scalar(
            select(doc.Document).where(
                doc.Document.collection_id == collection.id, doc.Document.sha256 == sha256
            )
        )
    if same is not None:
        # UC-05-01 step 2: the same file is not ingested twice; the existing document answers.
        response.status_code = 200
        response.headers["Location"] = f"/v1/documents/{same.id}"
        return Accepted(id=same.id, status=same.status)
    # Content-addressed, so a retry or a race writes the same object, never another version.
    key = f"{principal.org_id}/{collection.id}/{sha256}"
    if not await store.exists(RAW_BUCKET, key):
        await store.put(RAW_BUCKET, key, file.file, size, file.content_type or "")
    async with transaction(ROLE, principal) as session:
        row = doc.Document(
            id=uuid7(),
            org_id=principal.org_id,
            collection_id=collection.id,
            sha256=sha256,
            media=media,
            file_name=name,
            object_key=key,
            size_bytes=size,
            doc_no=doc_no,
            effective_from=effective_from,
            created_by=principal.user_id,
            updated_by=principal.user_id,
        )
        session.add(row)
        await session.flush()
        await session.refresh(row)
        # worker-ingest starts doc_ingest (UC-05-02) on this event.
        session.add(
            _event(
                principal,
                "document",
                row.id,
                "document.received",
                document_id=row.id,
                collection_id=collection.id,
                data_space_id=data_space,
                media=media,
                sha256=sha256,
                status=row.status,
            )
        )
    response.headers["Location"] = f"/v1/documents/{row.id}"
    return Accepted(id=row.id, status=row.status)


async def _document(
    session: AsyncSession,
    document_id: uuid.UUID,
    principal: Principal,
    data_space: uuid.UUID,
    *,
    lock: bool = False,
) -> tuple[doc.Document, doc.Collection]:
    query = (
        select(doc.Document, doc.Collection)
        .join(doc.Collection, doc.Collection.id == doc.Document.collection_id)
        .where(
            doc.Document.id == document_id,
            doc.Document.deleted_at.is_(None),
            _in_space(principal, data_space),
        )
    )
    if lock:
        query = query.with_for_update(of=doc.Document)
    found = (await session.execute(query)).first()
    if found is None:
        raise Problem(404, "not_found", "Document not found.")
    return found[0], found[1]


async def _count(session: AsyncSession, *where: ColumnElement[bool]) -> int:
    return int(await session.scalar(select(func.count()).where(*where)) or 0)


async def _progress(
    session: AsyncSession, document_ids: list[uuid.UUID]
) -> dict[uuid.UUID, tuple[int, int, int, int, bool]]:
    """(pages read, usable, chunks, chunks indexed, blocking issue open) per document, in three
    grouped reads for the whole page rather than five counts per row."""
    out: dict[uuid.UUID, list[Any]] = {d: [0, 0, 0, 0, False] for d in document_ids}
    if not document_ids:
        return {}
    page, chunk, issue = doc.DocumentPage, doc.Chunk, doc.QualityIssue
    for document_id, read, usable in await session.execute(
        select(
            page.document_id,
            func.count(),
            func.count().filter(page.usable.is_(True)),
        )
        .where(page.document_id.in_(document_ids))
        .group_by(page.document_id)
    ):
        out[document_id][0:2] = [read, usable]
    for document_id, chunks, indexed in await session.execute(
        select(
            chunk.document_id,
            func.count(),
            func.count().filter(chunk.qdrant_point_id.is_not(None)),
        )
        .where(chunk.document_id.in_(document_ids))
        .group_by(chunk.document_id)
    ):
        out[document_id][2:4] = [chunks, indexed]
    for document_id in await session.scalars(
        select(issue.document_id)
        .where(
            issue.document_id.in_(document_ids),
            issue.status == "open",
            issue.severity == "blocking",
        )
        .distinct()
    ):
        out[document_id][4] = True
    return {d: (v[0], v[1], v[2], v[3], v[4]) for d, v in out.items()}


def _step(row: doc.Document, read: int, chunks: int, blocking: bool) -> PreprocessingStep:
    if row.status == "ready":
        return "done"
    if row.status == "failed":
        return "failed"
    if row.status == "removed":
        return "removed"
    if row.status == "received":
        return "queued"
    if row.status == "waiting_review" or blocking:
        return "quality_review"
    if row.page_count is None or read < row.page_count:
        return "reading_pages"
    # Every page read: passages next, then vectors until the worker marks the document ready.
    return "chunking" if chunks == 0 else "indexing"


@router.get(
    "/v1/documents",
    response_model=Page[DocumentListItem],
    **endpoint("GET", "/v1/documents", operation_id="listDocuments"),
)
async def list_documents(
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    page: Annotated[PageRequest, Depends(page_request)],
    order: Annotated[
        Order,
        Depends(
            order_by(
                "created_at",
                "updated_at",
                "file_name",
                "status",
                "size_bytes",
                default="created_at desc",
            )
        ),
    ],
    collection_id: uuid.UUID | None = None,
    status: str | None = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
) -> Page[DocumentListItem]:
    d = doc.Document
    query = (
        select(d)
        .join(doc.Collection, doc.Collection.id == d.collection_id)
        .where(_in_space(principal, data_space), d.deleted_at.is_(None))
    )
    if collection_id is not None:
        query = query.where(d.collection_id == collection_id)
    if status == "processing":
        # The public filter represents unfinished preprocessing, while the persisted state moves
        # through received, processing and waiting_review before reaching a terminal state.
        query = query.where(d.status.not_in(("ready", "failed", "removed")))
    elif status:
        query = query.where(d.status == status)
    if q and q.strip():
        # A literal substring of the name, title or document number, not a pattern.
        needle = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{needle}%"
        query = query.where(
            or_(
                d.file_name.ilike(like, escape="\\"),
                d.title.ilike(like, escape="\\"),
                d.doc_no.ilike(like, escape="\\"),
            )
        )
    query = keyset(
        query,
        columns={
            "created_at": d.created_at,
            "updated_at": d.updated_at,
            "file_name": d.file_name,
            "status": d.status,
            "size_bytes": d.size_bytes,
        },
        tiebreak=d.id,
        order=order,
        page=page,
    )
    async with transaction(ROLE, principal) as session:
        rows, token = next_page(list((await session.scalars(query)).all()), order=order, page=page)
        counts = await _progress(session, [r.id for r in rows])
    items = []
    for row in rows:
        read, usable, chunks, indexed, blocking = counts[row.id]
        items.append(
            DocumentListItem(
                **DocumentView.model_validate(row, from_attributes=True).model_dump(),
                step=_step(row, read, chunks, blocking),
                progress=Progress(
                    pages_total=row.page_count,
                    pages_read=read,
                    pages_usable=usable,
                    chunks=chunks,
                    chunks_indexed=indexed,
                ),
            )
        )
    return Page(items=items, next_page_token=token)


@router.get(
    "/v1/documents/{id}",
    response_model=DocumentDetail,
    **endpoint("GET", "/v1/documents/{id}", operation_id="getDocument"),
)
async def get_document(
    document_id: IdPath,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    store: Annotated[ObjectStore, Depends(object_store)],
) -> DocumentDetail:
    page, chunk, issue = doc.DocumentPage, doc.Chunk, doc.QualityIssue
    async with transaction(ROLE, principal) as session:
        row, _ = await _document(session, document_id, principal, data_space)
        progress = Progress(
            pages_total=row.page_count,
            pages_read=await _count(session, page.document_id == row.id),
            pages_usable=await _count(session, page.document_id == row.id, page.usable.is_(True)),
            chunks=await _count(session, chunk.document_id == row.id),
            chunks_indexed=await _count(
                session, chunk.document_id == row.id, chunk.qdrant_point_id.is_not(None)
            ),
        )
        open_issues = await _count(session, issue.document_id == row.id, issue.status == "open")
        blocking = await _count(
            session,
            issue.document_id == row.id,
            issue.status == "open",
            issue.severity == "blocking",
        )
    return DocumentDetail(
        **DocumentView.model_validate(row, from_attributes=True).model_dump(),
        progress=progress,
        open_quality_issues=open_issues,
        blocking_quality_issues=blocking,
        original_url=f"/v1/documents/{row.id}/file",
    )


@router.get(
    "/v1/documents/{id}/file",
    **endpoint("GET", "/v1/documents/{id}/file", operation_id="downloadDocumentOriginal"),
)
async def download_document_original(
    document_id: IdPath,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    store: Annotated[ObjectStore, Depends(object_store)],
) -> Response:
    async with transaction(ROLE, principal) as session:
        row, _ = await _document(session, document_id, principal, data_space)
    content = await store.download(RAW_BUCKET, row.object_key)
    content_type = mimetypes.guess_type(row.file_name)[0] or "application/octet-stream"
    return Response(content=content, media_type=content_type)


@router.get(
    "/v1/documents/{id}/chunks",
    response_model=Page[ChunkView],
    **endpoint("GET", "/v1/documents/{id}/chunks", operation_id="listDocumentChunks"),
)
async def list_document_chunks(
    document_id: IdPath,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    page: Annotated[PageRequest, Depends(page_request)],
    purpose: Purpose = None,
) -> Page[ChunkView]:
    async with transaction(ROLE, principal) as session:
        row, collection = await _document(session, document_id, principal, data_space)
        _check_purpose(collection, _purpose(purpose))
        query = (
            select(doc.Chunk)
            .where(doc.Chunk.document_id == row.id, doc.Chunk.pii_masked.is_(True))
            .order_by(doc.Chunk.page_from, doc.Chunk.created_at, doc.Chunk.id)
            .limit(page.page_size + 1)
        )
        rows = list((await session.scalars(query)).all())
    kept = rows[: page.page_size]
    return Page(
        items=[ChunkView.model_validate(chunk, from_attributes=True) for chunk in kept],
        next_page_token="" if len(rows) <= page.page_size else str(page.page_size),
    )


VALIDITY_FIELDS = ("effective_from", "effective_to", "supersedes_id")


@router.patch(
    "/v1/documents/{id}",
    response_model=DocumentUpdated,
    **endpoint("PATCH", "/v1/documents/{id}", operation_id="updateDocument"),
)
async def update_document(
    body: DocumentPatch,
    document_id: IdPath,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
) -> DocumentUpdated:
    changes = body.model_dump(exclude_unset=True)
    async with transaction(ROLE, principal) as session:
        row, _ = await _document(session, document_id, principal, data_space, lock=True)
        if row.status == "removed":
            raise Problem(409, "invalid_state", "The document is removed.")
        validity = {
            k: v for k, v in changes.items() if k in VALIDITY_FIELDS and getattr(row, k) != v
        }
        direct = {k: v for k, v in changes.items() if k not in VALIDITY_FIELDS}
        proposed = None
        if validity:
            if row.status != "ready":
                # UC-05-05 precondition: validity is set on a document that is ready.
                raise Problem(409, "invalid_state", f"The document is {row.status}.")
            start = validity.get("effective_from", row.effective_from)
            end = validity.get("effective_to", row.effective_to)
            if start and end and end < start:
                raise Problem(422, "validation_failed", "effective_to is before effective_from.")
            target = validity.get("supersedes_id")
            if target is not None:
                if target == row.id:
                    raise Problem(422, "validation_failed", "A document cannot supersede itself.")
                await _document(session, target, principal, data_space)
            proposed = ValidityChange(**validity)
            # The inbox turns this into a document_validity proposal; the change is applied when
            # it is accepted (proposal.decided), and document.superseded follows from there.
            session.add(
                _event(
                    principal,
                    "document",
                    row.id,
                    "document.validity_proposed",
                    document_id=row.id,
                    collection_id=row.collection_id,
                    data_space_id=data_space,
                    proposed_by=principal.user_id,
                    **validity,
                )
            )
        if direct:
            for field, value in direct.items():
                setattr(row, field, value)
            row.updated_by, row.updated_at = principal.user_id, _now()
            session.add(
                _event(
                    principal,
                    "document",
                    row.id,
                    "document.updated",
                    document_id=row.id,
                    collection_id=row.collection_id,
                    data_space_id=data_space,
                    fields=sorted(direct),
                    status=row.status,
                )
            )
        await session.flush()
        return DocumentUpdated(
            **DocumentView.model_validate(row, from_attributes=True).model_dump(),
            proposed_validity=proposed,
        )


@router.get(
    "/v1/documents/{id}/pages/{n}",
    response_model=DocumentPageView,
    **endpoint("GET", "/v1/documents/{id}/pages/{n}", operation_id="getDocumentPage"),
)
async def get_document_page(
    document_id: IdPath,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    store: Annotated[ObjectStore, Depends(object_store)],
    n: Annotated[int, Path(ge=1)],
    purpose: Purpose = None,
) -> DocumentPageView:
    sees_personal = _sees_personal_data(principal)
    async with transaction(ROLE, principal) as session:
        row, collection = await _document(session, document_id, principal, data_space)
        _check_purpose(collection, _purpose(purpose))
        page = await session.scalar(
            select(doc.DocumentPage).where(
                doc.DocumentPage.document_id == row.id, doc.DocumentPage.page_no == n
            )
        )
        if page is None:
            raise Problem(404, "not_found", "Page not found.")
        on_page = [
            doc.Chunk.document_id == row.id,
            doc.Chunk.page_from <= n,
            doc.Chunk.page_to >= n,
        ]
        if not sees_personal:
            on_page.append(doc.Chunk.pii_masked.is_(True))
        chunks = (
            await session.scalars(
                select(doc.Chunk)
                .where(*on_page)
                .order_by(doc.Chunk.page_from, doc.Chunk.created_at, doc.Chunk.id)
            )
        ).all()
    image_url = None
    blocks: list[DocumentBlockView] = []
    layout_width = None
    layout_height = None
    if page.image_ref:
        try:
            image_url = await store.presign_get(
                DERIVED_BUCKET, page.image_ref, IMAGE_URL_TTL_SECONDS
            )
        except Problem:
            # The text and chunks are still worth serving without the image.
            logger.warning("document | page image link unavailable: storage not configured")
    if page.layout_ref:
        try:
            layout = json.loads((await store.download(DERIVED_BUCKET, page.layout_ref)).decode("utf-8"))
            layout_width = layout.get("width")
            layout_height = layout.get("height")
            for raw_block in layout.get("blocks", []):
                image_link = None
                if raw_block.get("image_ref"):
                    try:
                        image_link = await store.presign_get(
                            DERIVED_BUCKET, raw_block["image_ref"], IMAGE_URL_TTL_SECONDS
                        )
                    except Problem:
                        logger.warning("document | embedded image link unavailable")
                blocks.append(
                    DocumentBlockView(
                        kind=raw_block.get("kind", "paragraph"),
                        text=raw_block.get("text", ""),
                        box=(
                            [
                                raw_block["box"][0],
                                raw_block["box"][1],
                                raw_block["box"][2] - raw_block["box"][0],
                                raw_block["box"][3] - raw_block["box"][1],
                            ]
                            if len(raw_block.get("box", [])) == 4
                            else []
                        ),
                        level=raw_block.get("level"),
                        image_url=image_link,
                    )
                )
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            logger.warning("document | page layout unavailable")
    view = DocumentPageView.model_validate(page, from_attributes=True)
    # The page text of a keep_for_authorized collection is not masked; there is no masked copy
    # to serve instead, so a caller without view_detail gets the image and masked chunks only.
    withheld = collection.pii_policy == "keep_for_authorized" and not sees_personal
    return view.model_copy(
        update={
            "text": None if withheld else view.text,
            "image_url": image_url,
            "width": layout_width,
            "height": layout_height,
            "blocks": blocks,
            "chunks": [ChunkView.model_validate(c, from_attributes=True) for c in chunks],
        }
    )


@router.get(
    "/v1/documents/{id}/versions",
    response_model=Page[DocumentView],
    **endpoint("GET", "/v1/documents/{id}/versions", operation_id="listDocumentVersions"),
)
async def list_document_versions(
    document_id: IdPath, principal: CurrentPrincipal, data_space: CurrentDataSpace
) -> Page[DocumentView]:
    """The supersedes chain both ways from this document: what it replaced and what replaced it."""
    d = doc.Document
    async with transaction(ROLE, principal) as session:
        start, _ = await _document(session, document_id, principal, data_space)
        found = {start.id: start}
        frontier = {start.id} | ({start.supersedes_id} if start.supersedes_id else set())
        # A chain is short; the bound only guards against a cycle written by hand.
        for _ in range(50):
            rows = (
                await session.scalars(
                    select(d)
                    .join(doc.Collection, doc.Collection.id == d.collection_id)
                    .where(
                        or_(d.id.in_(frontier), d.supersedes_id.in_(frontier)),
                        d.deleted_at.is_(None),
                        _in_space(principal, data_space),
                    )
                )
            ).all()
            new = [r for r in rows if r.id not in found]
            if not new:
                break
            found.update({r.id: r for r in new})
            frontier = {r.id for r in new} | {r.supersedes_id for r in new if r.supersedes_id}
    timeline = sorted(
        found.values(),
        key=lambda r: (r.effective_from or datetime.date.max, r.created_at),
    )
    return Page(items=[DocumentView.model_validate(r, from_attributes=True) for r in timeline])


# --- search -------------------------------------------------------------------------------------


@router.post(
    "/v1/search/documents",
    response_model=Page[ChunkHit],
    **endpoint("POST", "/v1/search/documents", operation_id="searchDocuments"),
)
async def search_documents(
    body: SearchRequest,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    vectors: Annotated[VectorSearch, Depends(vector_search)],
    purpose: Purpose = None,
) -> Page[ChunkHit]:
    """Hybrid retrieval (UC-05-04 step 1): Qdrant neighbours and Postgres full-text matches,
    fused by reciprocal rank, over ready documents the caller may read, in force at as_of."""
    c, d, col = doc.Chunk, doc.Document, doc.Collection
    as_of = body.as_of or _now().date()
    purpose = _purpose(purpose)
    async with transaction(ROLE, principal) as session:
        query = select(col).where(_in_space(principal, data_space))
        if body.collection_ids:
            query = query.where(col.id.in_(body.collection_ids))
        collections = list((await session.scalars(query)).all())
        if body.collection_ids and len(collections) != len(set(body.collection_ids)):
            raise Problem(404, "not_found", "Collection not found.")
        if purpose is not None:
            if body.collection_ids:
                for collection in collections:
                    _check_purpose(collection, purpose)
            # BR-59: collections of another purpose are not searched at all.
            collections = [x for x in collections if x.purpose == purpose]
        if not collections:
            return Page(items=[])
        starts = func.coalesce(c.effective_from, d.effective_from)
        ends = func.coalesce(c.effective_to, d.effective_to)
        unusable_page = exists().where(
            doc.DocumentPage.document_id == c.document_id,
            doc.DocumentPage.page_no.between(c.page_from, c.page_to),
            doc.DocumentPage.usable.is_(False),
        )
        allowed = [
            c.collection_id.in_([x.id for x in collections]),
            d.status == "ready",
            d.deleted_at.is_(None),
            # BR-109: validity of the passage when it has its own, else of the document.
            or_(starts.is_(None), starts <= as_of),
            or_(ends.is_(None), ends >= as_of),
            # BR-19: a page below the OCR threshold is not used until reviewed.
            ~unusable_page,
        ]
        if not _sees_personal_data(principal):
            allowed.append(c.pii_masked.is_(True))
        if body.filters:
            if body.filters.document_ids:
                allowed.append(c.document_id.in_(body.filters.document_ids))
            if body.filters.media:
                allowed.append(d.media.in_(body.filters.media))
            if body.filters.doc_no:
                allowed.append(d.doc_no == body.filters.doc_no)
        base = select(c, d.title, d.doc_no).join(d, d.id == c.document_id).where(*allowed)
        pool = body.top_k * 3
        tsquery = func.websearch_to_tsquery(literal_column("'simple'"), body.query)
        # The same expression as chunk_text_idx (routines/document.sql), so the index serves it.
        tsvector = func.to_tsvector(literal_column("'simple'"), c.text)
        rank = func.ts_rank(tsvector, tsquery)
        keyword = (
            await session.execute(
                base.where(tsvector.op("@@")(tsquery)).order_by(rank.desc(), c.id).limit(pool)
            )
        ).all()
        neighbours = await vectors.search(
            [VectorTarget(x.qdrant_collection, x.embed_model) for x in collections],
            body.query,
            pool,
        )
        semantic: list[Any] = []
        if neighbours:
            by_point = {
                r[0].qdrant_point_id: r
                for r in (
                    await session.execute(
                        base.join(col, col.id == c.collection_id).where(
                            c.qdrant_point_id.in_([p for p, _ in neighbours]),
                            # BR-18: never mix vectors of two embedding models.
                            c.embed_model == col.embed_model,
                        )
                    )
                ).all()
            }
            semantic = [by_point[p] for p, _ in neighbours if p in by_point]
    scores: dict[uuid.UUID, float] = {}
    rows: dict[uuid.UUID, Any] = {}
    for ranked in (keyword, semantic):
        for position, r in enumerate(ranked):
            rows[r[0].id] = r
            scores[r[0].id] = scores.get(r[0].id, 0.0) + 1.0 / (RRF_K + position + 1)
    best = sorted(scores, key=lambda i: (-scores[i], str(i)))[: body.top_k]
    return Page(
        items=[
            ChunkHit(
                **ChunkView.model_validate(rows[i][0], from_attributes=True).model_dump(),
                score=round(scores[i], 6),
                document_title=rows[i][1],
                doc_no=rows[i][2],
            )
            for i in best
        ]
    )


# --- quality issues -----------------------------------------------------------------------------


def _blocking_first() -> ColumnElement[int]:
    return case((doc.QualityIssue.severity == "blocking", 0), else_=1)


@router.get(
    "/v1/quality-issues",
    response_model=Page[QualityIssueView],
    **endpoint("GET", "/v1/quality-issues", operation_id="listQualityIssues"),
)
async def list_quality_issues(
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    page: Annotated[PageRequest, Depends(page_request)],
    kind: str | None = None,
    severity: str | None = None,
    status: Annotated[str | None, Query(description="Default: open")] = None,
    document_id: uuid.UUID | None = None,
) -> Page[QualityIssueView]:
    """UC-05-03 step 1: blocking issues first, then oldest first."""
    issue = doc.QualityIssue
    rank = _blocking_first()
    query = (
        select(issue)
        .join(doc.Document, doc.Document.id == issue.document_id)
        .join(doc.Collection, doc.Collection.id == doc.Document.collection_id)
        .where(_in_space(principal, data_space), issue.status == (status or "open"))
    )
    if kind:
        query = query.where(issue.kind == kind)
    if severity:
        query = query.where(issue.severity == severity)
    if document_id:
        query = query.where(issue.document_id == document_id)
    if page.cursor:
        try:
            after = (
                int(page.cursor["r"]),
                datetime.datetime.fromisoformat(page.cursor["t"]),
                uuid.UUID(page.cursor["id"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise invalid_page_token() from exc
        query = query.where(tuple_(rank, issue.created_at, issue.id) > after)
    query = query.order_by(rank, issue.created_at, issue.id).limit(page.page_size + 1)
    async with transaction(ROLE, principal) as session:
        rows, token = _cut(
            list((await session.scalars(query)).all()),
            page,
            lambda r: {
                "r": 0 if r.severity == "blocking" else 1,
                "t": r.created_at.isoformat(),
                "id": str(r.id),
            },
        )
    return Page(
        items=[QualityIssueView.model_validate(r, from_attributes=True) for r in rows],
        next_page_token=token,
    )


async def _apply_resolution(
    session: AsyncSession, row: doc.QualityIssue, body: ResolveIssue, principal: Principal
) -> None:
    """Writes the correction where it belongs (BR-108: the old value and the corrector kept)."""
    now = _now()
    if body.resolution == "corrected":
        text = (body.corrected_text or "").strip()
        if row.chunk_id is not None:
            chunk = await session.scalar(
                select(doc.Chunk)
                .where(doc.Chunk.id == row.chunk_id, doc.Chunk.document_id == row.document_id)
                .with_for_update()
            )
            if chunk is None:
                raise Problem(422, "validation_failed", "The issue's passage no longer exists.")
            row.old_value, chunk.text, chunk.human_corrected = chunk.text, text, True
            return
        page = await _issue_page(session, row)
        row.old_value, page.text = page.text, text
        # BR-19: a page a person has corrected may be used again.
        page.human_corrected, page.usable = True, True
        page.updated_by, page.updated_at = principal.user_id, now
    elif body.resolution == "page_dropped":
        page = await _issue_page(session, row)
        page.usable = False
        page.updated_by, page.updated_at = principal.user_id, now
    elif body.resolution == "value_fixed":
        field = row.detail.get("field") if isinstance(row.detail, dict) else None
        record = (
            await session.scalar(
                select(doc.ExtractedRecord)
                .where(
                    doc.ExtractedRecord.id == row.extracted_record_id,
                    doc.ExtractedRecord.document_id == row.document_id,
                )
                .with_for_update()
            )
            if row.extracted_record_id is not None
            else None
        )
        if record is None or not field:
            raise Problem(422, "validation_failed", "The issue names no extracted field to fix.")
        values = dict(record.values or {})
        old = values.get(field)
        row.old_value = None if old is None else str(old)
        values[field] = body.corrected_text
        record.values = values
        record.status = "confirmed"
        record.updated_by, record.updated_at = principal.user_id, now


async def _issue_page(session: AsyncSession, row: doc.QualityIssue) -> doc.DocumentPage:
    page: doc.DocumentPage | None = (
        await session.scalar(
            select(doc.DocumentPage)
            .where(
                doc.DocumentPage.document_id == row.document_id,
                doc.DocumentPage.page_no == row.page_no,
            )
            .with_for_update()
        )
        if row.page_no is not None
        else None
    )
    if page is None:
        raise Problem(422, "validation_failed", "The issue names no page.")
    return page


@router.post(
    "/v1/quality-issues/{id}:resolve",
    response_model=QualityIssueView,
    **endpoint("POST", "/v1/quality-issues/{id}:resolve", operation_id="resolveQualityIssue"),
)
async def resolve_quality_issue(
    body: ResolveIssue,
    issue_id: IdPath,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
) -> QualityIssueView:
    issue = doc.QualityIssue
    async with transaction(ROLE, principal) as session:
        row = await session.scalar(
            select(issue)
            .join(doc.Document, doc.Document.id == issue.document_id)
            .join(doc.Collection, doc.Collection.id == doc.Document.collection_id)
            .where(issue.id == issue_id, _in_space(principal, data_space))
            .with_for_update(of=issue)
        )
        if row is None:
            raise Problem(404, "not_found", "Quality issue not found.")
        personal = row.kind == "pii_unmasked" or row.requires_role in ("AC03", "AC06")
        if personal and not PII_CAPABILITIES & principal.capabilities:
            # UC-05-03 E1: personal-data issues are for the data owner or compliance.
            raise Problem(403, "forbidden", "Personal-data issues need a steward or compliance.")
        if row.status != "open":
            raise Problem(409, "invalid_state", f"The issue is already {row.status}.")
        await _apply_resolution(session, row, body, principal)
        now = _now()
        row.status, row.resolution = "resolved", body.resolution
        row.resolved_by, row.resolved_at = principal.user_id, now
        row.updated_by, row.updated_at = principal.user_id, now
        await session.flush()
        remaining = await _count(
            session,
            issue.document_id == row.document_id,
            issue.status == "open",
            issue.severity == "blocking",
        )
        # UC-05-03 step 4: the worker re-runs the later preprocessing steps for what changed and
        # moves the document to ready once nothing blocks it.
        session.add(
            _event(
                principal,
                "quality_issue",
                row.id,
                "quality_issue.resolved",
                quality_issue_id=row.id,
                document_id=row.document_id,
                data_space_id=data_space,
                kind=row.kind,
                resolution=row.resolution,
                page_no=row.page_no,
                chunk_id=row.chunk_id,
                extracted_record_id=row.extracted_record_id,
                blocking_remaining=remaining,
                status=row.status,
            )
        )
        return QualityIssueView.model_validate(row, from_attributes=True)


# --- extraction schemas -------------------------------------------------------------------------


def _schema_view(row: doc.ExtractionSchema) -> ExtractionSchemaView:
    return ExtractionSchemaView.model_validate(row, from_attributes=True)


async def _schema(
    session: AsyncSession,
    schema_id: uuid.UUID,
    principal: Principal,
    data_space: uuid.UUID,
    *,
    lock: bool = True,
) -> doc.ExtractionSchema:
    query = (
        select(doc.ExtractionSchema)
        .join(doc.Collection, doc.Collection.id == doc.ExtractionSchema.collection_id)
        .where(doc.ExtractionSchema.id == schema_id, _in_space(principal, data_space))
    )
    if lock:
        query = query.with_for_update(of=doc.ExtractionSchema)
    row = await session.scalar(query)
    if row is None:
        raise Problem(404, "not_found", "Extraction schema not found.")
    return row


@router.get(
    "/v1/extraction-schemas",
    response_model=Page[ExtractionSchemaView],
    **endpoint("GET", "/v1/extraction-schemas", operation_id="listExtractionSchemas"),
)
async def list_extraction_schemas(
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    page: Annotated[PageRequest, Depends(page_request)],
    order: Annotated[
        Order,
        Depends(
            order_by("created_at", "updated_at", "version", "status", default="created_at desc")
        ),
    ],
    collection_id: uuid.UUID | None = None,
) -> Page[ExtractionSchemaView]:
    es = doc.ExtractionSchema
    query = (
        select(es)
        .join(doc.Collection, doc.Collection.id == es.collection_id)
        .where(_in_space(principal, data_space))
    )
    if collection_id is not None:
        query = query.where(es.collection_id == collection_id)
    query = keyset(
        query,
        columns={
            "created_at": es.created_at,
            "updated_at": es.updated_at,
            "version": es.version,
            "status": es.status,
        },
        tiebreak=es.id,
        order=order,
        page=page,
    )
    async with transaction(ROLE, principal) as session:
        rows, token = next_page(list((await session.scalars(query)).all()), order=order, page=page)
    return Page(items=[_schema_view(r) for r in rows], next_page_token=token)


@router.get(
    "/v1/extraction-schemas/{id}",
    response_model=ExtractionSchemaView,
    **endpoint("GET", "/v1/extraction-schemas/{id}", operation_id="getExtractionSchema"),
)
async def get_extraction_schema(
    schema_id: IdPath, principal: CurrentPrincipal, data_space: CurrentDataSpace
) -> ExtractionSchemaView:
    """Where :test's Location points: the fields and, once the run is done, sample_accuracy."""
    async with transaction(ROLE, principal) as session:
        return _schema_view(await _schema(session, schema_id, principal, data_space, lock=False))


@router.post(
    "/v1/extraction-schemas",
    response_model=ExtractionSchemaView,
    status_code=201,
    **endpoint("POST", "/v1/extraction-schemas", operation_id="createExtractionSchema"),
)
async def create_extraction_schema(
    body: ExtractionSchemaCreate, principal: CurrentPrincipal, data_space: CurrentDataSpace
) -> ExtractionSchemaView:
    async with transaction(ROLE, principal) as session:
        # The collection row lock serialises version numbers of concurrent creates.
        collection = await _collection(
            session, body.collection_id, principal, data_space, lock=True
        )
        if body.target_dataset_id is not None:
            dataset = await session.scalar(
                select(catalog.Dataset.id).where(
                    catalog.Dataset.id == body.target_dataset_id,
                    catalog.Dataset.org_id == principal.org_id,
                    catalog.Dataset.data_space_id == data_space,
                    catalog.Dataset.deleted_at.is_(None),
                )
            )
            if dataset is None:
                raise Problem(422, "validation_failed", "target_dataset_id is not a dataset here.")
        latest = await session.scalar(
            select(func.max(doc.ExtractionSchema.version)).where(
                doc.ExtractionSchema.collection_id == collection.id
            )
        )
        row = doc.ExtractionSchema(
            id=uuid7(),
            org_id=principal.org_id,
            collection_id=collection.id,
            # UC-05-06 5a: a change is a new version; records keep the version they came from.
            version=(latest or 0) + 1,
            fields=[f.model_dump(exclude_none=True) for f in body.fields],
            target_dataset_id=body.target_dataset_id,
            join_key=body.join_key,
            created_by=principal.user_id,
            updated_by=principal.user_id,
        )
        session.add(row)
        await session.flush()
        await session.refresh(row)
        session.add(
            _event(
                principal,
                "extraction_schema",
                row.id,
                "extraction_schema.created",
                extraction_schema_id=row.id,
                collection_id=collection.id,
                data_space_id=data_space,
                version=row.version,
                status=row.status,
            )
        )
        return _schema_view(row)


@router.post(
    "/v1/extraction-schemas/{id}:test",
    response_model=Accepted,
    status_code=202,
    **endpoint("POST", "/v1/extraction-schemas/{id}:test", operation_id="testExtractionSchema"),
)
async def test_extraction_schema(
    body: ExtractionTest,
    schema_id: IdPath,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    response: Response,
) -> Accepted:
    samples = list(dict.fromkeys(body.sample_document_ids or []))
    async with transaction(ROLE, principal) as session:
        row = await _schema(session, schema_id, principal, data_space)
        if row.status not in ("draft", "testing"):
            raise Problem(409, "invalid_state", f"The schema is {row.status}.")
        if samples:
            found = await _count(
                session,
                doc.Document.id.in_(samples),
                doc.Document.collection_id == row.collection_id,
                doc.Document.deleted_at.is_(None),
            )
            if found != len(samples):
                raise Problem(
                    422, "validation_failed", "Every sample must be a document of the collection."
                )
        row.status = "testing"
        # A new run measures afresh; activation waits for its accuracy.
        row.sample_accuracy = None
        row.updated_by, row.updated_at = principal.user_id, _now()
        session.add(
            _event(
                principal,
                "extraction_schema",
                row.id,
                "extraction_schema.test_requested",
                extraction_schema_id=row.id,
                collection_id=row.collection_id,
                data_space_id=data_space,
                sample_document_ids=samples,
                status=row.status,
            )
        )
    response.headers["Location"] = f"/v1/extraction-schemas/{row.id}"
    return Accepted(id=row.id, status="testing")


def _accuracy(value: Any) -> decimal.Decimal | None:
    if isinstance(value, dict):
        value = value.get("accuracy")
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    try:
        return decimal.Decimal(str(value))
    except decimal.InvalidOperation:
        return None


def below_threshold(fields: Any, sample_accuracy: Any) -> list[str]:
    """BR-110: the required fields whose sample accuracy is missing or under 95%."""
    measured = sample_accuracy if isinstance(sample_accuracy, dict) else {}
    short = []
    for field in fields if isinstance(fields, list) else []:
        if not isinstance(field, dict) or not field.get("required"):
            continue
        accuracy = _accuracy(measured.get(field.get("name")))
        if accuracy is None or accuracy < ACTIVATION_ACCURACY:
            short.append(str(field.get("name")))
    return short


@router.post(
    "/v1/extraction-schemas/{id}:activate",
    response_model=ExtractionSchemaView,
    **endpoint(
        "POST", "/v1/extraction-schemas/{id}:activate", operation_id="activateExtractionSchema"
    ),
)
async def activate_extraction_schema(
    schema_id: IdPath, principal: CurrentPrincipal, data_space: CurrentDataSpace
) -> ExtractionSchemaView:
    async with transaction(ROLE, principal) as session:
        row = await _schema(session, schema_id, principal, data_space)
        if row.status in ("active", "superseded"):
            raise Problem(409, "invalid_state", f"The schema is {row.status}.")
        if row.sample_accuracy is None:
            raise Problem(
                422, "business_rule_violated", "The schema has no sample accuracy yet (BR-110)."
            )
        short = below_threshold(row.fields, row.sample_accuracy)
        if short:
            raise Problem(
                422,
                "business_rule_violated",
                f"Required fields under 95% accuracy on the sample: {', '.join(short)} (BR-110).",
            )
        collection = await _collection(session, row.collection_id, principal, data_space, lock=True)
        now = _now()
        previous = list(
            (
                await session.scalars(
                    select(doc.ExtractionSchema)
                    .where(
                        doc.ExtractionSchema.collection_id == row.collection_id,
                        doc.ExtractionSchema.status == "active",
                    )
                    .with_for_update()
                )
            ).all()
        )
        for old in previous:
            old.status = "superseded"
            old.updated_by, old.updated_at = principal.user_id, now
            session.add(
                _event(
                    principal,
                    "extraction_schema",
                    old.id,
                    "extraction_schema.superseded",
                    extraction_schema_id=old.id,
                    collection_id=old.collection_id,
                    data_space_id=data_space,
                    superseded_by=row.id,
                    status=old.status,
                )
            )
        # One active schema per collection (partial unique index): retire the old one first.
        await session.flush()
        row.status = "active"
        row.updated_by, row.updated_at = principal.user_id, now
        collection.extraction_schema_id = row.id
        collection.updated_by, collection.updated_at = principal.user_id, now
        session.add(
            _event(
                principal,
                "extraction_schema",
                row.id,
                "extraction_schema.activated",
                extraction_schema_id=row.id,
                collection_id=row.collection_id,
                data_space_id=data_space,
                version=row.version,
                target_dataset_id=row.target_dataset_id,
                status=row.status,
            )
        )
        await session.flush()
        return _schema_view(row)
