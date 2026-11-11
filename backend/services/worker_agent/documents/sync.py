"""A document source synced from an object store (UC-05-01 1a, AC-05-01-03).

`POST /v1/document-sources/{id}:sync` (and, later, the source's schedule) writes
document_source.sync_requested; this handler lists the source's bucket under its prefix and makes
every new or changed file a document the way an upload does (services/api/modules/document,
upload_document): the original content-addressed in da-raw-docs, a document row, and
document.received in the same transaction for worker-document to preprocess. A document whose
file is gone from the source, or that a changed file replaces, is removed (soft-deleted).

It runs here rather than in worker-document because worker-agent is the worker with an OpenBao
AppRole (deploy/ansible/roles/k8s_openbao): the source's account is read with the same reader,
and the same org check, as a database source's. Only object stores (S3, MinIO) are wired; the api
refuses a sync of any other kind at once (501 not_available).

A file is identified by its key under the prefix, kept as the document's file_name. One that is
no newer than the last sync and of the same size is not read again; one that is read is hashed,
and the same hash is the same document (UC-05-01 step 2). Each run is bounded: at most
MAX_FILES_PER_SYNC files read (the rest wait for the next run), MAX_LISTED keys listed (a listing
cut short removes nothing, since a key past the cut is not gone), files the upload would refuse
(type, size) skipped.
"""

from __future__ import annotations

import asyncio
import datetime
import hashlib
import ipaddress
import logging
import os
import re
import socket
import tempfile
import uuid
from dataclasses import dataclass
from typing import IO, Any, Protocol
from urllib.parse import urlsplit

from aiohttp.abc import AbstractResolver, ResolveResult
from aiohttp.resolver import DefaultResolver
from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy import select

from libs.dataagent_db import scoped_session, uuid7
from libs.dataagent_db.models import document as doc
from services.worker_agent import questions
from services.worker_agent.events import Event, Handler
from services.worker_agent.query.secrets import Credentials, SecretUnavailable

log = logging.getLogger(__name__)

SYNC_REQUESTED = "document_source.sync_requested"
ROLE = "svc_document"
RAW_BUCKET = os.environ.get("DOCUMENT_RAW_BUCKET", "da-raw-docs")
MAX_FILES_PER_SYNC = int(os.environ.get("DOCUMENT_SYNC_MAX_FILES", "200"))
MAX_LISTED = int(os.environ.get("DOCUMENT_SYNC_MAX_LISTED", "5000"))
# The upload's limit (UC-05-01 E2, the CHECK on document.size_bytes).
MAX_FILE_BYTES = 200 * 1024 * 1024
# The extensions an upload takes, to the same media (api document/routes.py MEDIA_BY_EXTENSION;
# the two images share no module, so the table is repeated, not imported).
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
# S3 bucket naming rules: 3 to 63 lower-case letters, digits, dots and hyphens.
_BUCKET = re.compile(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]")
# Names that resolve inside the cluster or the host (crawler/fetch.py keeps the same list).
_INTERNAL_SUFFIXES = (".localhost", ".svc", ".cluster.local")
_SPOOL_BYTES = 8 * 1024 * 1024


class SourceRefused(Exception):
    """The source cannot be read as configured; the message names no credential."""


class StoreUnavailable(Exception):
    """The platform's raw store is not configured or refused the original."""


@dataclass(frozen=True)
class Location:
    """Where the files are: `endpoint` None is AWS S3 itself, else an S3-compatible server."""

    endpoint: str | None
    bucket: str
    prefix: str


@dataclass(frozen=True)
class SourceObject:
    key: str
    size: int
    last_modified: datetime.datetime


class Bucket(Protocol):
    async def objects(self, limit: int) -> tuple[list[SourceObject], bool]:
        """Up to `limit` objects under the prefix, and whether that is all of them."""
        ...

    async def fetch(self, key: str, into: IO[bytes], max_bytes: int) -> None: ...


class RawStore(Protocol):
    async def exists(self, bucket: str, key: str) -> bool: ...

    async def put(self, bucket: str, key: str, data: IO[bytes], size: int) -> None: ...


@dataclass(frozen=True)
class _Fetched:
    name: str
    sha256: str
    size: int
    media: str


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def parse_location(text: str) -> Location:
    """`bucket/prefix`, `s3://bucket/prefix`, or `http(s)://host[:port]/bucket/prefix` for an
    S3-compatible server (path-style). Credentials never ride in the location (the api refuses
    them there too); they are the source's secret_ref."""
    text = (text or "").strip()
    endpoint: str | None = None
    if "://" in text:
        parts = urlsplit(text)
        if parts.username or parts.password:
            raise SourceRefused("the location carries credentials")
        if parts.scheme == "s3":
            path = f"{parts.netloc}/{parts.path.lstrip('/')}"
        elif parts.scheme in ("http", "https") and parts.hostname:
            endpoint = f"{parts.scheme}://{parts.netloc}"
            path = parts.path.lstrip("/")
        else:
            raise SourceRefused("the location is not an object store address")
    else:
        path = text.lstrip("/")
    bucket, _, prefix = path.partition("/")
    if not _BUCKET.fullmatch(bucket):
        raise SourceRefused("the location names no valid bucket")
    segments = [s for s in prefix.split("/") if s]
    if any(s in (".", "..") for s in segments):
        raise SourceRefused("the location's prefix is not a plain path")
    return Location(endpoint, bucket, "".join(f"{s}/" for s in segments))


# The cluster's own pod and service networks (k3s defaults; DOCUMENT_SYNC_REFUSED_NETWORKS when
# the cluster uses others): an address there is a workload of the platform, not a store.
REFUSED_NETWORKS = tuple(
    ipaddress.ip_network(n.strip())
    for n in os.environ.get("DOCUMENT_SYNC_REFUSED_NETWORKS", "10.42.0.0/16,10.43.0.0/16").split(
        ","
    )
    if n.strip()
)


def _refused_address(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    # Private ranges stay allowed: an on-premises MinIO on the bank's network is the usual store.
    # Loopback and link-local (the cloud metadata endpoint) would turn the sync into a way in.
    return (
        ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_unspecified
        or ip.is_reserved
        or any(ip in network for network in REFUSED_NETWORKS)
    )


async def _addresses(host: str, port: int) -> set[str]:
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        return set()
    return {str(info[4][0]) for info in infos}


async def check_endpoint(endpoint: str | None) -> None:
    """Refuse an endpoint inside the platform: this host, the cluster, or the platform's own
    MinIO, where the source's account would read another organisation's originals."""
    if endpoint is None:
        return
    parts = urlsplit(endpoint)
    host = (parts.hostname or "").lower().rstrip(".")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    if not host or host == "localhost" or host.endswith(_INTERNAL_SUFFIXES):
        raise SourceRefused("the endpoint is inside the platform")
    addresses = await _addresses(host, port)
    if not addresses:
        raise SourceRefused("the endpoint does not resolve")
    if any(_refused_address(a) for a in addresses):
        raise SourceRefused("the endpoint is inside the platform")
    platform = urlsplit(os.environ.get("MINIO_URL", "").strip())
    if platform.hostname:
        own = platform.hostname.lower().rstrip(".")
        own_port = platform.port or (443 if platform.scheme == "https" else 80)
        if own == host or addresses & await _addresses(own, own_port):
            raise SourceRefused("the endpoint is the platform's own store")


async def _platform_addresses() -> frozenset[str]:
    platform = urlsplit(os.environ.get("MINIO_URL", "").strip())
    if not platform.hostname:
        return frozenset()
    port = platform.port or (443 if platform.scheme == "https" else 80)
    return frozenset(await _addresses(platform.hostname, port))


class CheckedResolver(AbstractResolver):
    """The source client's DNS: the same rule as check_endpoint, on the answer the connection then
    uses. check_endpoint resolving a name and the client resolving it again would leave a window
    (DNS rebinding: public for the check, 127.0.0.1 for the connection); here there is one
    answer, checked and connected to. The URL keeps the host name, so Host, SigV4 and TLS
    (SNI and certificate) are unchanged. An address literal never reaches a resolver; it is
    checked by check_endpoint and cannot change."""

    def __init__(self, inner: Any = None, platform: frozenset[str] | None = None) -> None:
        self._inner = inner if inner is not None else DefaultResolver()
        self._platform = platform

    async def resolve(
        self, host: str, port: int = 0, family: socket.AddressFamily = socket.AF_INET
    ) -> list[ResolveResult]:
        answers = await self._inner.resolve(host, port, family)
        if self._platform is None:
            self._platform = await _platform_addresses()
        found = {str(a["host"]) for a in answers}
        if not found or any(_refused_address(a) for a in found) or found & self._platform:
            raise OSError(f"{host} resolves inside the platform; not connected")
        return list(answers)

    async def close(self) -> None:
        await self._inner.close()


def _s3_client(
    endpoint: str | None,
    access_key: str | None,
    secret_key: str | None,
    *,
    resolver: AbstractResolver | None = None,
    **extra: Any,
) -> Any:
    import aioboto3
    from aiobotocore.config import AioConfig

    return aioboto3.Session().client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=extra.get("region") or "us-east-1",
        config=AioConfig(
            signature_version="s3v4",
            # MinIO and most on-premises stores serve buckets by path, not by host name.
            s3={"addressing_style": "path"} if endpoint else {},
            connect_timeout=10,
            read_timeout=60,
            retries={"max_attempts": 3, "mode": "standard"},
            connector_args={"resolver": resolver} if resolver is not None else None,
        ),
    )


class S3Bucket:
    def __init__(
        self,
        location: Location,
        credentials: Credentials,
        *,
        resolver: AbstractResolver | None = None,
    ) -> None:
        self._location = location
        self._credentials = credentials
        self._resolver = resolver

    def _client(self) -> Any:
        c = self._credentials
        extra = {k: v for k, v in c.extra.items() if k == "region"}
        # A resolver per client: aiohttp closes it with the client's connector.
        resolver = self._resolver or CheckedResolver()
        return _s3_client(self._location.endpoint, c.user, c.password, resolver=resolver, **extra)

    async def objects(self, limit: int) -> tuple[list[SourceObject], bool]:
        found: list[SourceObject] = []
        async with self._client() as client:
            pages = client.get_paginator("list_objects_v2").paginate(
                Bucket=self._location.bucket, Prefix=self._location.prefix
            )
            async for page in pages:
                for item in page.get("Contents", []):
                    if len(found) == limit:
                        return found, False
                    found.append(SourceObject(item["Key"], int(item["Size"]), item["LastModified"]))
        return found, True

    async def fetch(self, key: str, into: IO[bytes], max_bytes: int) -> None:
        size = 0
        async with self._client() as client:
            answer = await client.get_object(Bucket=self._location.bucket, Key=key)
            async with answer["Body"] as body:
                while chunk := await body.read(1 << 20):
                    size += len(chunk)
                    if size > max_bytes:
                        raise SourceRefused("a file grew past the size limit while read")
                    into.write(chunk)


class MinioRawStore:
    """The platform's MinIO (MINIO_URL = http://key:secret@host:port), as the api writes to it."""

    def __init__(self, url: str) -> None:
        parsed = urlsplit(url)
        port = f":{parsed.port}" if parsed.port else ""
        self._endpoint = f"{parsed.scheme}://{parsed.hostname}{port}"
        self._key, self._secret = parsed.username, parsed.password

    async def exists(self, bucket: str, key: str) -> bool:
        async with _s3_client(self._endpoint, self._key, self._secret) as client:
            try:
                await client.head_object(Bucket=bucket, Key=key)
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
                    return False
                raise
            return True

    async def put(self, bucket: str, key: str, data: IO[bytes], size: int) -> None:
        async with _s3_client(self._endpoint, self._key, self._secret) as client:
            await client.put_object(Bucket=bucket, Key=key, Body=data, ContentLength=size)


class _NoRawStore:
    async def exists(self, bucket: str, key: str) -> bool:
        raise StoreUnavailable("MINIO_URL is not set")

    async def put(self, bucket: str, key: str, data: IO[bytes], size: int) -> None:
        raise StoreUnavailable("MINIO_URL is not set")


def open_bucket(location: Location, credentials: Credentials) -> Bucket:
    return S3Bucket(location, credentials)


def raw_store() -> RawStore:
    url = os.environ.get("MINIO_URL", "").strip()
    return MinioRawStore(url) if url else _NoRawStore()


def _sha256(data: IO[bytes]) -> str:
    digest = hashlib.sha256()
    data.seek(0)
    while chunk := data.read(1 << 20):
        digest.update(chunk)
    data.seek(0)
    return digest.hexdigest()


@dataclass(frozen=True)
class _Source:
    location: str
    secret_ref: str | None
    last_synced_at: datetime.datetime | None
    collection_id: uuid.UUID
    data_space_id: uuid.UUID
    media_kinds: tuple[str, ...]
    # file_name -> (size, sha256) of the documents the source made and still has.
    existing: dict[str, tuple[int, str]]


async def _load(org_id: uuid.UUID, actor: uuid.UUID | None, source_id: uuid.UUID) -> _Source | None:
    async with scoped_session(ROLE, org_id, actor) as session:
        found = (
            await session.execute(
                select(doc.DocumentSource, doc.Collection)
                .join(doc.Collection, doc.Collection.id == doc.DocumentSource.collection_id)
                .where(doc.DocumentSource.id == source_id)
            )
        ).first()
        if found is None:
            return None
        source, collection = found
        if (
            source.status == "paused"
            or source.kind != "object_store"
            or collection.status == "archived"
            or collection.deleted_at is not None
        ):
            # A stale request: paused since, or a kind the api no longer queues.
            log.info(
                "document source %s not synced (%s, %s)", source_id, source.kind, source.status
            )
            return None
        rows = (
            await session.execute(
                select(doc.Document.file_name, doc.Document.size_bytes, doc.Document.sha256).where(
                    doc.Document.document_source_id == source_id,
                    doc.Document.deleted_at.is_(None),
                )
            )
        ).all()
        return _Source(
            location=source.location,
            secret_ref=source.secret_ref,
            last_synced_at=source.last_synced_at,
            collection_id=collection.id,
            data_space_id=collection.data_space_id,
            media_kinds=tuple(collection.media_kinds or ()),
            existing={name: (size, sha) for name, size, sha in rows},
        )


async def _changes(org_id: uuid.UUID, source: _Source) -> tuple[list[_Fetched], set[str] | None]:
    """The files to make documents of, with their originals already in the raw store, and every
    name the source holds (None when the listing was cut short)."""
    location = parse_location(source.location)
    await check_endpoint(location.endpoint)
    if not source.secret_ref:
        raise SourceRefused("the source has no credentials")
    credentials = await questions.secrets().read(org_id, source.secret_ref)
    bucket = open_bucket(location, credentials)
    listed, complete = await bucket.objects(MAX_LISTED)
    store = raw_store()
    seen: set[str] = set()
    fresh: list[_Fetched] = []
    reads = 0
    for item in listed:
        name = item.key[len(location.prefix) :]
        if not name or item.key.endswith("/"):
            continue  # the prefix itself, or a folder marker
        seen.add(name)
        media = MEDIA_BY_EXTENSION.get(os.path.splitext(name)[1].lower())
        if media is None or (source.media_kinds and media not in source.media_kinds):
            continue
        if not 0 < item.size <= MAX_FILE_BYTES or len(name) > 1_000:
            continue
        old = source.existing.get(name)
        if (
            old is not None
            and old[0] == item.size
            and source.last_synced_at is not None
            and item.last_modified <= source.last_synced_at
        ):
            continue
        if reads == MAX_FILES_PER_SYNC:
            continue  # keep listing names, so a file not read yet is not taken for gone
        reads += 1
        with tempfile.SpooledTemporaryFile(max_size=_SPOOL_BYTES) as data:
            await bucket.fetch(item.key, data, MAX_FILE_BYTES)
            size = data.tell()
            sha256 = _sha256(data)
            if size == 0 or (old is not None and old[1] == sha256):
                continue
            key = f"{org_id}/{source.collection_id}/{sha256}"
            # Content-addressed like an upload's: a retry writes the same object, never another.
            if not await store.exists(RAW_BUCKET, key):
                await store.put(RAW_BUCKET, key, data, size)
        fresh.append(_Fetched(name, sha256, size, media))
    return fresh, seen if complete else None


def _remove(row: doc.Document, now: datetime.datetime, actor: uuid.UUID | None) -> None:
    row.status = "removed"
    row.deleted_at = now
    row.updated_at = now
    row.updated_by = actor


async def _apply(
    org_id: uuid.UUID,
    actor: uuid.UUID | None,
    source_id: uuid.UUID,
    source: _Source,
    fresh: list[_Fetched],
    seen: set[str] | None,
) -> tuple[int, int]:
    added = removed = 0
    async with scoped_session(ROLE, org_id, actor) as session:
        row = await session.scalar(
            select(doc.DocumentSource).where(doc.DocumentSource.id == source_id).with_for_update()
        )
        if row is None or row.status == "paused":
            return 0, 0
        now = _now()
        current = {
            d.file_name: d
            for d in (
                await session.scalars(
                    select(doc.Document)
                    .where(
                        doc.Document.document_source_id == source_id,
                        doc.Document.deleted_at.is_(None),
                    )
                    .with_for_update()
                )
            ).all()
        }
        for f in fresh:
            same = await session.scalar(
                select(doc.Document.id).where(
                    doc.Document.collection_id == source.collection_id,
                    doc.Document.sha256 == f.sha256,
                    doc.Document.deleted_at.is_(None),
                )
            )
            if same is not None:
                continue  # UC-05-01 step 2: the same file is not ingested twice
            document_id = uuid7()
            session.add(
                doc.Document(
                    id=document_id,
                    org_id=org_id,
                    collection_id=source.collection_id,
                    document_source_id=source_id,
                    sha256=f.sha256,
                    media=f.media,
                    file_name=f.name,
                    object_key=f"{org_id}/{source.collection_id}/{f.sha256}",
                    size_bytes=f.size,
                    created_by=actor,
                    updated_by=actor,
                )
            )
            # worker-document claims this event and runs doc_ingest (UC-05-02), as for an upload.
            session.add(
                doc.Outbox(
                    id=uuid7(),
                    org_id=org_id,
                    aggregate_type="document",
                    aggregate_id=document_id,
                    event_type="document.received",
                    payload={
                        "document_id": str(document_id),
                        "collection_id": str(source.collection_id),
                        "data_space_id": str(source.data_space_id),
                        "media": f.media,
                        "sha256": f.sha256,
                        "status": "received",
                    },
                )
            )
            added += 1
            replaced = current.pop(f.name, None)
            if replaced is not None:
                _remove(replaced, now, actor)
                removed += 1
        if seen is not None:
            for name, gone in current.items():
                if name not in seen:
                    _remove(gone, now, actor)
                    removed += 1
        row.last_synced_at = now
        row.status = "active"
        row.updated_at = now
        row.updated_by = actor
    return added, removed


async def _failed(org_id: uuid.UUID, actor: uuid.UUID | None, source_id: uuid.UUID) -> None:
    async with scoped_session(ROLE, org_id, actor) as session:
        row = await session.scalar(
            select(doc.DocumentSource).where(doc.DocumentSource.id == source_id).with_for_update()
        )
        if row is not None and row.status != "paused":
            row.status = "error"
            row.updated_at = _now()


def _uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value)) if value else None
    except ValueError:
        return None


async def sync_source(event: Event) -> None:
    source_id = _uuid(event.payload.get("document_source_id")) or event.aggregate_id
    actor = _uuid(event.payload.get("requested_by"))
    source = await _load(event.org_id, actor, source_id)
    if source is None:
        return
    try:
        fresh, seen = await _changes(event.org_id, source)
    except (SourceRefused, SecretUnavailable, StoreUnavailable) as exc:
        # These messages name no credential (SecretUnavailable's never carries one).
        log.warning("document source %s sync failed: %s", source_id, exc)
        await _failed(event.org_id, actor, source_id)
        return
    except (BotoCoreError, ClientError, OSError) as exc:
        # A store's own message can quote the request; the type is enough to act on.
        log.warning("document source %s sync failed (%s)", source_id, type(exc).__name__)
        await _failed(event.org_id, actor, source_id)
        return
    added, removed = await _apply(event.org_id, actor, source_id, source, fresh, seen)
    log.info("document source %s synced: %d added, %d removed", source_id, added, removed)


HANDLERS: dict[str, Handler] = {SYNC_REQUESTED: sync_source}
