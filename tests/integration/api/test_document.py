"""Tài liệu against PostgreSQL: collections and their sync sources, document uploads, preprocessing
progress, validity, page reads, hybrid search, the quality queue and extraction schemas
(UC-05-01 to UC-05-06).

Each test builds its own organisation with two Data Spaces; the data admin is a member of both,
so a row of space A read with X-Data-Space B proves the space filter. Object storage, the secret
store and the vector index are in-memory fakes injected over the module's ports. Preprocessing is
worker-ingest's: the tests assert the outbox event and seed what the worker would have written.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from typing import Any, BinaryIO
from urllib.parse import quote

import httpx
import pytest
from psycopg2.extras import Json

from services.api.app import create_app
from services.api.config import Settings
from services.api.modules.document import routes as document_routes
from services.api.modules.document.ports import (
    DERIVED_BUCKET,
    RAW_BUCKET,
    VectorTarget,
    object_store,
    secret_store,
    vector_search,
)

from .conftest import Seed

pytestmark = [pytest.mark.integration, pytest.mark.contract]


class FakeStore:
    """Buckets in memory; refuses to overwrite a key (originals are immutable, invariant 1)."""

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.puts = 0

    async def exists(self, bucket: str, key: str) -> bool:
        return (bucket, key) in self.objects

    async def put(
        self, bucket: str, key: str, data: BinaryIO, size: int, content_type: str
    ) -> None:
        assert (bucket, key) not in self.objects, "an original was rewritten"
        body = data.read()
        assert len(body) == size
        self.objects[(bucket, key)] = body
        self.puts += 1

    async def presign_get(self, bucket: str, key: str, ttl_seconds: int) -> str:
        return f"https://minio.test/{bucket}/{key}?ttl={ttl_seconds}"


class FakeSecrets:
    def __init__(self) -> None:
        self.written: dict[str, dict[str, str]] = {}

    async def write(self, org_id: uuid.UUID, name: str, values: dict[str, str]) -> str:
        ref = f"bao://{org_id}/{name}"
        self.written[ref] = values
        return ref


class FakeVectors:
    """Answers with the point ids it is given, best first."""

    def __init__(self) -> None:
        self.points: list[uuid.UUID] = []
        self.asked: list[list[VectorTarget]] = []

    async def search(
        self, targets: list[VectorTarget], query: str, limit: int
    ) -> list[tuple[uuid.UUID, float]]:
        self.asked.append(targets)
        return [(p, 1.0 - i / 100) for i, p in enumerate(self.points[:limit])]


@dataclass
class Space:
    org: uuid.UUID
    space: uuid.UUID
    other: uuid.UUID
    users: dict[str, uuid.UUID]
    laws: uuid.UUID
    contracts: uuid.UUID
    other_collection: uuid.UUID
    dataset: uuid.UUID
    other_dataset: uuid.UUID
    seed: Seed
    store: FakeStore = field(default_factory=FakeStore)
    secrets: FakeSecrets = field(default_factory=FakeSecrets)
    vectors: FakeVectors = field(default_factory=FakeVectors)

    def headers(self, who: str, space: uuid.UUID | None = None, **extra: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer kc-{who}",
            "X-Data-Space": str(space or self.space),
            **extra,
        }

    def events(self, aggregate_id: object) -> list[tuple[str, dict[str, Any]]]:
        with self.seed.conn.cursor() as cur:
            cur.execute(
                "SELECT event_type, payload FROM document.outbox WHERE aggregate_id = %s"
                " ORDER BY created_at, id",
                (str(aggregate_id),),
            )
            return list(cur.fetchall())

    def one(self, sql: str, *params: object) -> Any:
        with self.seed.conn.cursor() as cur:
            cur.execute(sql, [str(p) if isinstance(p, uuid.UUID) else p for p in params])
            row = cur.fetchone()
            return row[0] if row else None

    def document(self, collection: uuid.UUID | None = None, **values: Any) -> uuid.UUID:
        values.setdefault("status", "ready")
        values.setdefault("media", "pdf")
        values.setdefault("sha256", uuid.uuid4().hex * 2)
        values.setdefault("file_name", "quyet_dinh.pdf")
        return self.seed.row(
            "document.document",
            org_id=self.org,
            collection_id=collection or self.laws,
            object_key=f"{self.org}/{values['sha256']}",
            size_bytes=1000,
            **values,
        )

    def page(self, document: uuid.UUID, page_no: int, **values: Any) -> uuid.UUID:
        values.setdefault("usable", True)
        return self.seed.row(
            "document.document_page",
            org_id=self.org,
            document_id=document,
            page_no=page_no,
            page_hash=f"h{page_no}",
            **values,
        )

    def chunk(self, document: uuid.UUID, text: str, **values: Any) -> uuid.UUID:
        values.setdefault("collection_id", self.laws)
        values.setdefault("page_from", 1)
        values.setdefault("page_to", 1)
        return self.seed.row(
            "document.chunk",
            org_id=self.org,
            document_id=document,
            text=text,
            tokens=len(text.split()),
            **values,
        )

    def issue(self, document: uuid.UUID, **values: Any) -> uuid.UUID:
        values.setdefault("kind", "low_ocr")
        return self.seed.row(
            "document.quality_issue", org_id=self.org, document_id=document, **values
        )


def _user(seed: Seed, org: uuid.UUID, name: str, role: uuid.UUID, *spaces: uuid.UUID) -> uuid.UUID:
    user = seed.row(
        "org.app_user",
        org_id=org,
        external_id=f"kc-{name}",
        username=name,
        display_name=name,
        status="active",
    )
    seed.row("org.user_role", org_id=org, user_id=user, role_id=role, source="manual")
    for space in spaces:
        seed.row(
            "org.data_space_member",
            org_id=org,
            data_space_id=space,
            principal_type="user",
            principal_id=user,
            role_id=role,
        )
    return user


def _collection(
    seed: Seed, org: uuid.UUID, space: uuid.UUID, code: str, purpose: str, **values: Any
) -> uuid.UUID:
    collection = uuid.uuid4()
    return seed.row(
        "document.collection",
        id=collection,
        org_id=org,
        data_space_id=space,
        code=code,
        name=code,
        purpose=purpose,
        qdrant_collection=f"doc_{collection.hex}",
        embed_model="bge-m3",
        **values,
    )


@pytest.fixture
def ds(seed: Seed) -> Space:
    org = seed.org()

    def role(code: str, *caps: str) -> uuid.UUID:
        return seed.row("org.role", org_id=org, code=code, name=code, capabilities=list(caps))

    keeper = seed.row(
        "org.app_user",
        org_id=org,
        external_id=f"kc-keeper-{org.hex[:6]}",
        username="keeper",
        display_name="Keeper",
    )
    space = seed.row("org.data_space", org_id=org, code="DS-DOC", name="Doc", steward_id=keeper)
    other = seed.row("org.data_space", org_id=org, code="DS-DOC-B", name="B", steward_id=keeper)
    users = {
        "admin": _user(
            seed, org, "admin", role("AC04", "ask", "admin_data", "upload"), space, other
        ),
        "uploader": _user(seed, org, "uploader", role("AC01", "ask", "upload"), space),
        "analyst": _user(seed, org, "analyst", role("ASK", "ask"), space),
        "detail": _user(seed, org, "detail", role("DETAIL", "ask", "view_detail"), space),
        # AC03: a data owner who also works the queue (the route gate is admin_data).
        "steward": _user(seed, org, "steward", role("AC03", "ask", "admin_data", "steward"), space),
    }
    dataset = seed.row(
        "catalog.dataset",
        org_id=org,
        data_space_id=space,
        origin="derived",
        schema_name="silver",
        table_name="hop_dong_trich_xuat",
    )
    other_dataset = seed.row(
        "catalog.dataset",
        org_id=org,
        data_space_id=other,
        origin="derived",
        schema_name="silver",
        table_name="hop_dong_b",
    )
    return Space(
        org=org,
        space=space,
        other=other,
        users=users,
        laws=_collection(
            seed, org, space, "van-ban-phap-quy", "tra cứu văn bản pháp quy", media_kinds=["pdf"]
        ),
        contracts=_collection(
            seed,
            org,
            space,
            "hop-dong",
            "thẩm định tín dụng",
            pii_policy="keep_for_authorized",
        ),
        other_collection=_collection(seed, org, other, "phap-quy-b", "tra cứu văn bản pháp quy"),
        dataset=dataset,
        other_dataset=other_dataset,
        seed=seed,
    )


class Tokens:
    def __init__(self, ds: Space) -> None:
        self.ds = ds

    def claims(self, token: str) -> dict[str, Any]:
        # "<user>#client": an API client or agent acting for that user (token exchange).
        user, _, actor = token.partition("#")
        out: dict[str, Any] = {"sub": user, "org_id": str(self.ds.org)}
        if actor:
            out["act"] = {"sub": actor}
        return out


def client(ds: Space) -> httpx.AsyncClient:
    app = create_app(Settings(), token_verifier=Tokens(ds))
    app.dependency_overrides[object_store] = lambda: ds.store
    app.dependency_overrides[secret_store] = lambda: ds.secrets
    app.dependency_overrides[vector_search] = lambda: ds.vectors
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api")


PDF = b"%PDF-1.7 Quyet dinh 1234/QD-NHNN lai suat huy dong"


# --- collections and sources --------------------------------------------------------------------


# AC-05-06-01
async def test_admin_creates_a_collection_with_its_purpose_and_pii_policy(ds: Space) -> None:
    body = {
        "code": "email-khieu-nai",
        "name": "Email khiếu nại",
        "media_kinds": ["email", "pdf", "email"],
        "purpose": "xử lý khiếu nại",
        "retention_days": 365,
    }
    async with client(ds) as api:
        made = await api.post("/v1/collections", json=body, headers=ds.headers("admin"))
        assert made.status_code == 201, made.text
        collection = made.json()
        assert collection["status"] == "active" and collection["pii_policy"] == "mask"
        assert collection["media_kinds"] == ["email", "pdf"]
        assert collection["purpose"] == "xử lý khiếu nại"
        assert collection["qdrant_collection"] == f"doc_{uuid.UUID(collection['id']).hex}"
        assert collection["embed_model"]
        [(kind, payload)] = ds.events(collection["id"])
        assert kind == "collection.created"
        assert payload["qdrant_collection"] == collection["qdrant_collection"]
        assert payload["data_space_id"] == str(ds.space)

        rejecting = await api.post(
            "/v1/collections",
            json={**body, "code": "ghi-am", "pii_policy": "reject"},
            headers=ds.headers("admin"),
        )
        assert rejecting.json()["pii_policy"] == "reject"
        bad_policy = await api.post(
            "/v1/collections",
            json={**body, "code": "x", "pii_policy": "publish"},
            headers=ds.headers("admin"),
        )
        assert bad_policy.status_code == 422
        no_purpose = await api.post(
            "/v1/collections",
            json={**body, "code": "y", "purpose": " "},
            headers=ds.headers("admin"),
        )
        assert no_purpose.status_code == 422
        bad_media = await api.post(
            "/v1/collections",
            json={**body, "code": "z", "media_kinds": ["rar"]},
            headers=ds.headers("admin"),
        )
        assert bad_media.status_code == 422
        twice = await api.post("/v1/collections", json=body, headers=ds.headers("admin"))
        assert twice.status_code == 409
        assert "already exists" in twice.json()["detail"]
        # Codes are unique per Data Space only.
        elsewhere = await api.post(
            "/v1/collections", json=body, headers=ds.headers("admin", ds.other)
        )
        assert elsewhere.status_code == 201
        forbidden = await api.post(
            "/v1/collections", json={**body, "code": "w"}, headers=ds.headers("uploader")
        )
        assert forbidden.status_code == 403

        listed = await api.get("/v1/collections", headers=ds.headers("analyst"))
        assert listed.status_code == 200, listed.text
        codes = {c["code"] for c in listed.json()["items"]}
        assert codes == {"van-ban-phap-quy", "hop-dong", "email-khieu-nai", "ghi-am"}
        archived = await api.get(
            "/v1/collections", params={"status": "archived"}, headers=ds.headers("analyst")
        )
        assert archived.json()["items"] == []
        paged = await api.get(
            "/v1/collections", params={"page_size": 3}, headers=ds.headers("analyst")
        )
        rest = await api.get(
            "/v1/collections",
            params={"page_size": 3, "page_token": paged.json()["next_page_token"]},
            headers=ds.headers("analyst"),
        )
        assert {c["code"] for c in paged.json()["items"] + rest.json()["items"]} == codes


async def test_sync_source_keeps_credentials_in_the_secret_store(ds: Space) -> None:
    body = {
        "collection_id": str(ds.laws),
        "kind": "smb_folder",
        "location": "smb://fileserver/phap-che/quyet-dinh",
        "sync_cron": "0 2 * * *",
        "credentials": {"username": "svc_doc", "password": "s3cret!"},
    }
    async with client(ds) as api:
        made = await api.post("/v1/document-sources", json=body, headers=ds.headers("admin"))
        assert made.status_code == 201, made.text
        source = made.json()
        assert source["has_credentials"] is True and source["status"] == "active"
        assert "s3cret!" not in made.text and "secret_ref" not in source
        ref = ds.one("SELECT secret_ref FROM document.document_source WHERE id = %s", source["id"])
        assert ds.secrets.written[ref] == body["credentials"]
        [(kind, payload)] = ds.events(source["id"])
        assert kind == "document_source.created"
        assert payload["collection_id"] == str(ds.laws) and "credentials" not in payload

        bare = await api.post(
            "/v1/document-sources",
            json={**body, "credentials": None, "location": "s3://da-raw/inbox"},
            headers=ds.headers("admin"),
        )
        assert bare.status_code == 201 and bare.json()["has_credentials"] is False

        inline = await api.post(
            "/v1/document-sources",
            json={**body, "location": "smb://svc_doc:s3cret!@fileserver/share"},
            headers=ds.headers("admin"),
        )
        assert inline.status_code == 422
        bad_cron = await api.post(
            "/v1/document-sources",
            json={**body, "sync_cron": "hằng ngày"},
            headers=ds.headers("admin"),
        )
        assert bad_cron.status_code == 422
        foreign = await api.post(
            "/v1/document-sources",
            json={**body, "collection_id": str(ds.other_collection)},
            headers=ds.headers("admin"),
        )
        assert foreign.status_code == 404
        forbidden = await api.post(
            "/v1/document-sources", json=body, headers=ds.headers("uploader")
        )
        assert forbidden.status_code == 403
    assert len(ds.secrets.written) == 1


# AC-05-01-03
async def test_sync_now_asks_the_worker(ds: Space) -> None:
    source = ds.seed.row(
        "document.document_source",
        org_id=ds.org,
        collection_id=ds.laws,
        kind="smb_folder",
        location="smb://fileserver/phap-che",
        sync_cron="0 2 * * *",
    )
    paused = ds.seed.row(
        "document.document_source",
        org_id=ds.org,
        collection_id=ds.laws,
        kind="imap_mailbox",
        location="imaps://mail/khieu-nai",
        sync_cron="*/5 * * * *",
        status="paused",
    )
    async with client(ds) as api:
        accepted = await api.post(
            f"/v1/document-sources/{source}:sync", headers=ds.headers("admin")
        )
        assert accepted.status_code == 202, accepted.text
        assert accepted.json()["id"] == str(source)
        assert accepted.headers["Location"] == f"/v1/document-sources/{source}"
        [(kind, payload)] = ds.events(source)
        assert kind == "document_source.sync_requested"
        assert payload["requested_by"] == str(ds.users["admin"])

        refused = await api.post(f"/v1/document-sources/{paused}:sync", headers=ds.headers("admin"))
        assert refused.status_code == 409
        other = await api.post(
            f"/v1/document-sources/{source}:sync", headers=ds.headers("admin", ds.other)
        )
        assert other.status_code == 404
        forbidden = await api.post(
            f"/v1/document-sources/{source}:sync", headers=ds.headers("uploader")
        )
        assert forbidden.status_code == 403


# --- documents ----------------------------------------------------------------------------------


# AC-05-01-01, AC-05-01-02
async def test_upload_stores_the_original_once_and_answers_a_duplicate(ds: Space) -> None:
    sha = hashlib.sha256(PDF).hexdigest()
    async with client(ds) as api:
        accepted = await api.post(
            "/v1/documents",
            files={"file": ("QD-1234.pdf", PDF, "application/pdf")},
            data={
                "collection_id": str(ds.laws),
                "doc_no": "1234/QĐ-NHNN",
                "effective_from": "2026-06-01",
            },
            headers=ds.headers("uploader"),
        )
        assert accepted.status_code == 202, accepted.text
        document = accepted.json()["id"]
        assert accepted.json()["status"] == "received"
        assert accepted.headers["Location"] == f"/v1/documents/{document}"
        key = f"{ds.org}/{ds.laws}/{sha}"
        assert ds.store.objects[(RAW_BUCKET, key)] == PDF
        row = ds.one(
            "SELECT row(sha256, media, doc_no, effective_from::text, object_key, created_by::text)"
            " FROM document.document WHERE id = %s",
            document,
        )
        assert row == f'({sha},pdf,1234/QĐ-NHNN,2026-06-01,{key},{ds.users["uploader"]})'
        [(kind, payload)] = ds.events(document)
        assert kind == "document.received"
        assert payload["sha256"] == sha and payload["collection_id"] == str(ds.laws)

        # UC-05-01 step 2: the same file again answers the existing document, nothing new.
        again = await api.post(
            "/v1/documents",
            files={"file": ("ban-sao.pdf", PDF, "application/pdf")},
            data={"collection_id": str(ds.laws)},
            headers=ds.headers("uploader"),
        )
        assert again.status_code == 200, again.text
        assert again.json()["id"] == document
        assert again.headers["Location"] == f"/v1/documents/{document}"
        assert ds.one("SELECT count(*) FROM document.document WHERE org_id = %s", ds.org) == 1
        assert ds.store.puts == 1 and len(ds.events(document)) == 1

        # The same bytes in another collection are another document.
        elsewhere = await api.post(
            "/v1/documents",
            files={"file": ("QD-1234.pdf", PDF, "application/pdf")},
            data={"collection_id": str(ds.contracts)},
            headers=ds.headers("uploader"),
        )
        assert elsewhere.status_code == 202
        assert elsewhere.json()["id"] != document


# AC-05-01-05, AC-05-01-07
async def test_upload_refusals(ds: Space) -> None:
    async with client(ds) as api:
        rar = await api.post(
            "/v1/documents",
            files={"file": ("ho_so.rar", b"Rar!\x1a\x07", "application/x-rar")},
            data={"collection_id": str(ds.laws)},
            headers=ds.headers("uploader"),
        )
        assert rar.status_code == 422
        assert rar.json()["code"] == "validation_failed"
        # BR-107: a document lives in a collection.
        no_collection = await api.post(
            "/v1/documents",
            files={"file": ("QD.pdf", PDF, "application/pdf")},
            headers=ds.headers("uploader"),
        )
        assert no_collection.status_code == 422
        # The collection takes PDFs only.
        word = await api.post(
            "/v1/documents",
            files={"file": ("hop_dong.docx", b"PK\x03\x04", "application/octet-stream")},
            data={"collection_id": str(ds.laws)},
            headers=ds.headers("uploader"),
        )
        assert word.status_code == 422
        assert word.json()["code"] == "business_rule_violated"
        empty = await api.post(
            "/v1/documents",
            files={"file": ("rong.pdf", b"", "application/pdf")},
            data={"collection_id": str(ds.laws)},
            headers=ds.headers("uploader"),
        )
        assert empty.status_code == 422
        foreign = await api.post(
            "/v1/documents",
            files={"file": ("QD.pdf", PDF, "application/pdf")},
            data={"collection_id": str(ds.other_collection)},
            headers=ds.headers("admin"),
        )
        assert foreign.status_code == 404
        forbidden = await api.post(
            "/v1/documents",
            files={"file": ("QD.pdf", PDF, "application/pdf")},
            data={"collection_id": str(ds.laws)},
            headers=ds.headers("analyst"),
        )
        assert forbidden.status_code == 403
        ds.seed.conn.cursor().execute(
            "UPDATE document.collection SET status = 'archived' WHERE id = %s", (str(ds.laws),)
        )
        archived = await api.post(
            "/v1/documents",
            files={"file": ("QD.pdf", PDF, "application/pdf")},
            data={"collection_id": str(ds.laws)},
            headers=ds.headers("uploader"),
        )
        assert archived.status_code == 409
    assert ds.store.objects == {}
    assert ds.one("SELECT count(*) FROM document.document WHERE org_id = %s", ds.org) == 0


# AC-05-01-06
async def test_a_file_over_the_limit_is_refused(ds: Space, monkeypatch: pytest.MonkeyPatch) -> None:
    # The 200 MB check without allocating 200 MB: lower the limit, send one byte more.
    monkeypatch.setattr(document_routes, "MAX_DOCUMENT_BYTES", 1024)
    async with client(ds) as api:
        too_big = await api.post(
            "/v1/documents",
            files={"file": ("lon.pdf", b"%" * 1025, "application/pdf")},
            data={"collection_id": str(ds.laws)},
            headers=ds.headers("uploader"),
        )
        assert too_big.status_code == 413
        assert too_big.json()["code"] == "payload_too_large"
        fits = await api.post(
            "/v1/documents",
            files={"file": ("vua.pdf", b"%" * 1024, "application/pdf")},
            data={"collection_id": str(ds.laws)},
            headers=ds.headers("uploader"),
        )
        assert fits.status_code == 202
    assert ds.store.puts == 1


async def test_document_shows_preprocessing_progress_and_open_issues(ds: Space) -> None:
    document = ds.document(status="waiting_review", page_count=3)
    ds.page(document, 1, ocr_conf=0.97)
    ds.page(document, 2, usable=False, ocr_conf=0.62)
    ds.chunk(document, "Điều 1. Phạm vi", qdrant_point_id=uuid.uuid4(), embed_model="bge-m3")
    ds.chunk(document, "Điều 2. Đối tượng")
    ds.issue(document, page_no=2)
    ds.issue(document, kind="garbled_text", severity="warning")
    ds.issue(
        document,
        kind="empty_chunk",
        status="resolved",
        resolved_by=ds.users["admin"],
        resolved_at="2026-10-01T00:00:00Z",
    )
    async with client(ds) as api:
        got = await api.get(f"/v1/documents/{document}", headers=ds.headers("analyst"))
        assert got.status_code == 200, got.text
        body = got.json()
        assert body["status"] == "waiting_review"
        assert body["progress"] == {
            "pages_total": 3,
            "pages_read": 2,
            "pages_usable": 1,
            "chunks": 2,
            "chunks_indexed": 1,
        }
        assert body["open_quality_issues"] == 2 and body["blocking_quality_issues"] == 1
        # Files are reached through signed links only.
        assert "object_key" not in body
        other = await api.get(f"/v1/documents/{document}", headers=ds.headers("admin", ds.other))
        assert other.status_code == 404
        missing = await api.get(f"/v1/documents/{uuid.uuid4()}", headers=ds.headers("analyst"))
        assert missing.status_code == 404
        deleted = ds.document(deleted_at="2026-10-01T00:00:00Z")
        gone = await api.get(f"/v1/documents/{deleted}", headers=ds.headers("analyst"))
        assert gone.status_code == 404


# AC-05-05-01
async def test_validity_changes_are_proposed_not_applied(ds: Space) -> None:
    old = ds.document(doc_no="100/QĐ", effective_from="2025-01-01")
    new = ds.document(doc_no="200/QĐ", effective_from="2026-08-01")
    async with client(ds) as api:
        url = f"/v1/documents/{new}"
        titled = await api.patch(
            url, json={"title": "Quyết định lãi suất"}, headers=ds.headers("admin")
        )
        assert titled.status_code == 200, titled.text
        assert titled.json()["title"] == "Quyết định lãi suất"
        assert titled.json()["proposed_validity"] is None
        assert [e for e, _ in ds.events(new)] == ["document.updated"]

        proposed = await api.patch(
            url,
            json={"supersedes_id": str(old), "doc_no": "200/QĐ-NHNN"},
            headers=ds.headers("admin"),
        )
        assert proposed.status_code == 200, proposed.text
        assert proposed.json()["proposed_validity"]["supersedes_id"] == str(old)
        # The reviewer decides in Cần bạn; nothing changes until then.
        assert proposed.json()["supersedes_id"] is None
        assert ds.one("SELECT supersedes_id FROM document.document WHERE id = %s", new) is None
        assert ds.one("SELECT doc_no FROM document.document WHERE id = %s", new) == "200/QĐ-NHNN"
        kinds = dict(ds.events(new))
        assert kinds["document.validity_proposed"]["supersedes_id"] == str(old)
        assert kinds["document.validity_proposed"]["proposed_by"] == str(ds.users["admin"])

        backwards = await api.patch(
            url, json={"effective_to": "2026-07-01"}, headers=ds.headers("admin")
        )
        assert backwards.status_code == 422
        itself = await api.patch(url, json={"supersedes_id": str(new)}, headers=ds.headers("admin"))
        assert itself.status_code == 422
        stranger = ds.document(ds.other_collection)
        foreign = await api.patch(
            url, json={"supersedes_id": str(stranger)}, headers=ds.headers("admin")
        )
        assert foreign.status_code in (404, 422)
        unchanged = await api.patch(
            url, json={"effective_from": "2026-08-01"}, headers=ds.headers("admin")
        )
        assert unchanged.json()["proposed_validity"] is None

        processing = ds.document(status="processing")
        early = await api.patch(
            f"/v1/documents/{processing}",
            json={"effective_to": "2027-01-01"},
            headers=ds.headers("admin"),
        )
        assert early.status_code == 409
        removed = ds.document(status="removed")
        gone = await api.patch(
            f"/v1/documents/{removed}", json={"title": "x"}, headers=ds.headers("admin")
        )
        assert gone.status_code == 409
        other = await api.patch(url, json={"title": "x"}, headers=ds.headers("admin", ds.other))
        assert other.status_code == 404
        forbidden = await api.patch(url, json={"title": "x"}, headers=ds.headers("uploader"))
        assert forbidden.status_code == 403


async def test_versions_follow_the_supersedes_chain_both_ways(ds: Space) -> None:
    first = ds.document(doc_no="1/QĐ", effective_from="2024-01-01", effective_to="2024-12-31")
    second = ds.document(
        doc_no="2/QĐ", effective_from="2025-01-01", effective_to="2025-12-31", supersedes_id=first
    )
    third = ds.document(doc_no="3/QĐ", effective_from="2026-01-01", supersedes_id=second)
    ds.document(doc_no="khác")
    async with client(ds) as api:
        timeline = await api.get(f"/v1/documents/{second}/versions", headers=ds.headers("analyst"))
        assert timeline.status_code == 200, timeline.text
        assert [d["id"] for d in timeline.json()["items"]] == [str(first), str(second), str(third)]
        other = await api.get(
            f"/v1/documents/{second}/versions", headers=ds.headers("admin", ds.other)
        )
        assert other.status_code == 404


# AC-05-04-01
async def test_page_read_brings_a_signed_image_link_and_its_chunks(ds: Space) -> None:
    document = ds.document()
    ds.page(document, 1, text="Trang 1", image_ref=f"{ds.org}/p1.png")
    ds.page(document, 2, text="Trang 2")
    on_one = ds.chunk(document, "Điều 1", page_from=1, page_to=1)
    spanning = ds.chunk(document, "Điều 2 kéo sang trang 2", page_from=1, page_to=2)
    ds.chunk(document, "Điều 3", page_from=2, page_to=2)
    async with client(ds) as api:
        url = f"/v1/documents/{document}/pages/1"
        page = await api.get(url, headers=ds.headers("analyst"))
        assert page.status_code == 200, page.text
        assert page.json()["text"] == "Trang 1"
        assert page.json()["image_url"].startswith(f"https://minio.test/{DERIVED_BUCKET}/")
        assert [c["id"] for c in page.json()["chunks"]] == [str(on_one), str(spanning)]
        no_image = await api.get(f"/v1/documents/{document}/pages/2", headers=ds.headers("analyst"))
        assert no_image.json()["image_url"] is None and len(no_image.json()["chunks"]) == 2

        missing = await api.get(f"/v1/documents/{document}/pages/9", headers=ds.headers("analyst"))
        assert missing.status_code == 404
        zero = await api.get(f"/v1/documents/{document}/pages/0", headers=ds.headers("analyst"))
        assert zero.status_code == 422
        other = await api.get(url, headers=ds.headers("admin", ds.other))
        assert other.status_code == 404


# AC-05-06-06
async def test_a_reader_of_another_purpose_gets_purpose_mismatch(ds: Space) -> None:
    document = ds.document(ds.contracts)
    ds.page(document, 1, text="Hợp đồng tín dụng")
    clause = ds.chunk(document, "hợp đồng tín dụng số 15", collection_id=ds.contracts)
    ds.chunk(ds.document(), "hợp đồng mẫu trong văn bản pháp quy")
    async with client(ds) as api:
        url = f"/v1/documents/{document}/pages/1"
        # HTTP headers are latin-1: a Vietnamese purpose travels percent-encoded.
        same = await api.get(
            url, headers=ds.headers("analyst", **{"X-Purpose": quote("thẩm định tín dụng")})
        )
        assert same.status_code == 200, same.text
        raw_utf8 = await api.get(
            url,
            headers={**ds.headers("analyst"), "X-Purpose": "thẩm định tín dụng".encode()},  # type: ignore[dict-item]
        )
        assert raw_utf8.status_code == 200, raw_utf8.text
        searched = await api.post(
            "/v1/search/documents",
            json={"query": "hợp đồng"},
            headers=ds.headers("analyst", **{"X-Purpose": quote("thẩm định tín dụng")}),
        )
        assert searched.status_code == 200, searched.text
        assert [h["id"] for h in searched.json()["items"]] == [str(clause)]
        mismatch = await api.get(url, headers=ds.headers("analyst", **{"X-Purpose": "marketing"}))
        assert mismatch.status_code == 403
        assert mismatch.json()["code"] == "purpose_mismatch"


# BR-110, UC-05-03 E1: personal data the collection keeps unmasked is for view_detail only.
async def test_unmasked_personal_data_reaches_only_view_detail(ds: Space) -> None:
    document = ds.document(ds.contracts, status="ready")
    ds.page(document, 1, text="Bên vay Nguyễn Văn A, CCCD 001099000123")
    masked = ds.chunk(
        document, "Lãi suất cho vay 9,5%/năm", collection_id=ds.contracts, pii_masked=True
    )
    raw = ds.chunk(
        document,
        "Bên vay Nguyễn Văn A CCCD 001099000123 lãi suất",
        collection_id=ds.contracts,
        pii_masked=False,
    )
    async with client(ds) as api:
        url = f"/v1/documents/{document}/pages/1"
        plain = await api.get(url, headers=ds.headers("analyst"))
        assert plain.status_code == 200, plain.text
        assert [c["id"] for c in plain.json()["chunks"]] == [str(masked)]
        assert "001099000123" not in plain.text
        detail = await api.get(url, headers=ds.headers("detail"))
        assert {c["id"] for c in detail.json()["chunks"]} == {str(masked), str(raw)}
        assert "001099000123" in detail.text

        query = {"query": "lãi suất", "collection_ids": [str(ds.contracts)]}
        found = await api.post("/v1/search/documents", json=query, headers=ds.headers("analyst"))
        assert found.status_code == 200, found.text
        assert [h["id"] for h in found.json()["items"]] == [str(masked)]
        found_all = await api.post("/v1/search/documents", json=query, headers=ds.headers("detail"))
        assert {h["id"] for h in found_all.json()["items"]} == {str(masked), str(raw)}

        # The same person's rights through a client or an agent never reach unmasked data.
        delegated = {**ds.headers("detail"), "Authorization": "Bearer kc-detail#mcp-client"}
        via_client = await api.get(url, headers=delegated)
        assert [c["id"] for c in via_client.json()["chunks"]] == [str(masked)]
        assert "001099000123" not in via_client.text
        via_search = await api.post("/v1/search/documents", json=query, headers=delegated)
        assert [h["id"] for h in via_search.json()["items"]] == [str(masked)]


# --- search -------------------------------------------------------------------------------------


# AC-05-02-07, AC-05-05-04, AC-05-06-06
async def test_search_reads_ready_passages_in_force_at_as_of(ds: Space) -> None:
    law = ds.document(title="Quyết định lãi suất", doc_no="1234/QĐ", effective_from="2026-01-01")
    current = ds.chunk(law, "lãi suất huy động kỳ hạn 12 tháng là 4,8%")
    # Điều 3 stops applying on 01/08/2026 (BR-109: passage-level validity).
    article_3 = ds.chunk(
        law,
        "Điều 3 lãi suất huy động không kỳ hạn",
        effective_to="2026-07-31",
        page_from=2,
        page_to=2,
    )
    ds.page(law, 1, ocr_conf=0.97)
    ds.page(law, 2, ocr_conf=0.97)
    # A page under the OCR threshold is not used until reviewed (BR-19).
    ds.page(law, 3, usable=False, ocr_conf=0.80)
    ds.chunk(law, "lãi suất trên trang OCR kém", page_from=3, page_to=3)
    draft = ds.document(status="processing")
    ds.chunk(draft, "lãi suất chưa xử lý xong")
    contract = ds.document(ds.contracts)
    contract_chunk = ds.chunk(contract, "lãi suất cho vay", collection_id=ds.contracts)
    stranger = ds.document(ds.other_collection)
    ds.chunk(stranger, "lãi suất ở Data Space khác", collection_id=ds.other_collection)
    async with client(ds) as api:
        august = await api.post(
            "/v1/search/documents",
            json={"query": "lãi suất", "as_of": "2026-08-15", "collection_ids": [str(ds.laws)]},
            headers=ds.headers("analyst"),
        )
        assert august.status_code == 200, august.text
        hits = august.json()["items"]
        assert [h["id"] for h in hits] == [str(current)]
        assert hits[0]["document_title"] == "Quyết định lãi suất" and hits[0]["doc_no"] == "1234/QĐ"
        assert hits[0]["page_from"] == 1 and hits[0]["score"] > 0
        july = await api.post(
            "/v1/search/documents",
            json={"query": "lãi suất", "as_of": "2026-07-15", "collection_ids": [str(ds.laws)]},
            headers=ds.headers("analyst"),
        )
        assert {h["id"] for h in july.json()["items"]} == {str(current), str(article_3)}
        before = await api.post(
            "/v1/search/documents",
            json={"query": "lãi suất", "as_of": "2025-12-31", "collection_ids": [str(ds.laws)]},
            headers=ds.headers("analyst"),
        )
        assert before.json()["items"] == []

        # Without a purpose the caller reads every collection of the Data Space.
        everywhere = await api.post(
            "/v1/search/documents", json={"query": "lãi suất"}, headers=ds.headers("analyst")
        )
        assert str(contract_chunk) in {h["id"] for h in everywhere.json()["items"]}
        # BR-59: a run with another purpose sees nothing of the contract collection.
        marketing = await api.post(
            "/v1/search/documents",
            json={"query": "lãi suất"},
            headers=ds.headers("analyst", **{"X-Purpose": "marketing"}),
        )
        assert marketing.json()["items"] == []
        named = await api.post(
            "/v1/search/documents",
            json={"query": "lãi suất", "collection_ids": [str(ds.contracts)]},
            headers=ds.headers("analyst", **{"X-Purpose": "marketing"}),
        )
        assert named.status_code == 403
        assert named.json()["code"] == "purpose_mismatch"
        foreign = await api.post(
            "/v1/search/documents",
            json={"query": "lãi suất", "collection_ids": [str(ds.other_collection)]},
            headers=ds.headers("analyst"),
        )
        assert foreign.status_code == 404
        top = await api.post(
            "/v1/search/documents",
            json={"query": "lãi suất", "top_k": 1},
            headers=ds.headers("analyst"),
        )
        assert len(top.json()["items"]) == 1
        filtered = await api.post(
            "/v1/search/documents",
            json={"query": "lãi suất", "filters": {"doc_no": "1234/QĐ"}},
            headers=ds.headers("analyst"),
        )
        assert {h["document_id"] for h in filtered.json()["items"]} == {str(law)}
        unknown_filter = await api.post(
            "/v1/search/documents",
            json={"query": "lãi suất", "filters": {"owner": "x"}},
            headers=ds.headers("analyst"),
        )
        assert unknown_filter.status_code == 422


async def test_search_fuses_vector_neighbours_of_the_same_embedding_model(ds: Space) -> None:
    law = ds.document()
    keyword = ds.chunk(law, "biểu lãi suất", qdrant_point_id=uuid.uuid4(), embed_model="bge-m3")
    point = uuid.uuid4()
    semantic = ds.chunk(law, "mức chi trả tiền gửi", qdrant_point_id=point, embed_model="bge-m3")
    stale_point = uuid.uuid4()
    # BR-18: a vector of another embedding model is never mixed in.
    ds.chunk(law, "chi trả tiền gửi cũ", qdrant_point_id=stale_point, embed_model="old-model")
    ds.vectors.points = [stale_point, point, uuid.uuid4()]
    async with client(ds) as api:
        found = await api.post(
            "/v1/search/documents",
            json={"query": "lãi suất", "collection_ids": [str(ds.laws)]},
            headers=ds.headers("analyst"),
        )
    assert found.status_code == 200, found.text
    assert {h["id"] for h in found.json()["items"]} == {str(keyword), str(semantic)}
    [targets] = ds.vectors.asked
    assert [t.embed_model for t in targets] == ["bge-m3"]


# --- quality issues -----------------------------------------------------------------------------


async def test_quality_queue_puts_blocking_issues_first(ds: Space) -> None:
    document = ds.document(status="waiting_review")
    warning = ds.issue(
        document, kind="garbled_text", severity="warning", created_at="2026-10-01T00:00:00Z"
    )
    late_block = ds.issue(document, page_no=3, created_at="2026-10-03T00:00:00Z")
    early_block = ds.issue(document, page_no=2, created_at="2026-10-02T00:00:00Z")
    ds.issue(
        document,
        kind="empty_chunk",
        status="resolved",
        resolved_by=ds.users["admin"],
        resolved_at="2026-10-04T00:00:00Z",
    )
    ds.issue(ds.document(ds.other_collection))
    async with client(ds) as api:
        queue = await api.get("/v1/quality-issues", headers=ds.headers("admin"))
        assert queue.status_code == 200, queue.text
        assert [i["id"] for i in queue.json()["items"]] == [
            str(early_block),
            str(late_block),
            str(warning),
        ]
        first = await api.get(
            "/v1/quality-issues", params={"page_size": 2}, headers=ds.headers("admin")
        )
        rest = await api.get(
            "/v1/quality-issues",
            params={"page_size": 2, "page_token": first.json()["next_page_token"]},
            headers=ds.headers("admin"),
        )
        assert [i["id"] for i in rest.json()["items"]] == [str(warning)]
        resolved = await api.get(
            "/v1/quality-issues", params={"status": "resolved"}, headers=ds.headers("admin")
        )
        assert len(resolved.json()["items"]) == 1
        garbled = await api.get(
            "/v1/quality-issues",
            params={"kind": "garbled_text", "severity": "warning"},
            headers=ds.headers("admin"),
        )
        assert [i["id"] for i in garbled.json()["items"]] == [str(warning)]
        forbidden = await api.get("/v1/quality-issues", headers=ds.headers("analyst"))
        assert forbidden.status_code == 403


# AC-05-03-01
async def test_correcting_a_page_keeps_the_old_text_and_frees_the_page(ds: Space) -> None:
    document = ds.document(status="waiting_review")
    ds.page(document, 2, usable=False, ocr_conf=0.61, text="L4i sual huy d0ng")
    issue = ds.issue(document, page_no=2)
    async with client(ds) as api:
        url = f"/v1/quality-issues/{issue}:resolve"
        no_text = await api.post(url, json={"resolution": "corrected"}, headers=ds.headers("admin"))
        assert no_text.status_code == 422
        done = await api.post(
            url,
            json={"resolution": "corrected", "corrected_text": "Lãi suất huy động"},
            headers=ds.headers("admin"),
        )
        assert done.status_code == 200, done.text
        assert done.json()["status"] == "resolved" and done.json()["resolution"] == "corrected"
        # BR-108: the old value and the corrector are kept.
        assert done.json()["old_value"] == "L4i sual huy d0ng"
        assert done.json()["resolved_by"] == str(ds.users["admin"])
        page = ds.one(
            "SELECT row(text, usable, human_corrected) FROM document.document_page"
            " WHERE document_id = %s AND page_no = 2",
            document,
        )
        assert page == '("Lãi suất huy động",t,t)'
        [(kind, payload)] = ds.events(issue)
        assert kind == "quality_issue.resolved"
        assert payload["document_id"] == str(document) and payload["blocking_remaining"] == 0
        twice = await api.post(
            url, json={"resolution": "page_dropped"}, headers=ds.headers("admin")
        )
        assert twice.status_code == 409
        other = await api.post(
            f"/v1/quality-issues/{ds.issue(document, page_no=2)}:resolve",
            json={"resolution": "page_dropped"},
            headers=ds.headers("admin", ds.other),
        )
        assert other.status_code == 404


async def test_other_resolutions_write_where_they_belong(ds: Space) -> None:
    document = ds.document(status="waiting_review")
    ds.page(document, 4, usable=True, ocr_conf=0.9, text="trang trắng")
    chunk = ds.chunk(document, "Ddieu 5 khoan 2")
    schema = ds.seed.row(
        "document.extraction_schema",
        org_id=ds.org,
        collection_id=ds.laws,
        version=1,
        fields=Json([{"name": "so_tien", "type": "number", "required": True}]),
    )
    record = ds.seed.row(
        "document.extracted_record",
        org_id=ds.org,
        document_id=document,
        extraction_schema_id=schema,
        values=Json({"so_tien": "1.000.000"}),
        status="needs_review",
    )
    dropped = ds.issue(document, page_no=4, kind="empty_chunk")
    text = ds.issue(document, chunk_id=chunk, kind="garbled_text")
    value = ds.issue(
        document,
        extracted_record_id=record,
        kind="field_mismatch",
        detail=Json({"field": "so_tien"}),
    )
    nowhere = ds.issue(document, kind="garbled_text")
    async with client(ds) as api:

        def resolve(issue: uuid.UUID, body: dict[str, Any]) -> Any:
            return api.post(
                f"/v1/quality-issues/{issue}:resolve", json=body, headers=ds.headers("admin")
            )

        assert (await resolve(dropped, {"resolution": "page_dropped"})).status_code == 200
        assert (
            ds.one(
                "SELECT usable FROM document.document_page WHERE document_id = %s AND page_no = 4",
                document,
            )
            is False
        )
        fixed = await resolve(text, {"resolution": "corrected", "corrected_text": "Điều 5 khoản 2"})
        assert fixed.status_code == 200, fixed.text
        assert fixed.json()["old_value"] == "Ddieu 5 khoan 2"
        assert ds.one("SELECT human_corrected FROM document.chunk WHERE id = %s", chunk) is True
        corrected = await resolve(
            value, {"resolution": "value_fixed", "corrected_text": "50000000"}
        )
        assert corrected.status_code == 200, corrected.text
        assert corrected.json()["old_value"] == "1.000.000"
        assert ds.one(
            "SELECT values->>'so_tien' FROM document.extracted_record WHERE id = %s", record
        ) == ("50000000")
        assert (
            ds.one("SELECT status FROM document.extracted_record WHERE id = %s", record)
            == "confirmed"
        )
        # Nothing to correct: refused, and the issue stays open.
        refused = await resolve(nowhere, {"resolution": "corrected", "corrected_text": "x"})
        assert refused.status_code == 422
        assert ds.one("SELECT status FROM document.quality_issue WHERE id = %s", nowhere) == "open"


# AC-05-03-03
async def test_personal_data_issues_need_a_steward_or_compliance(ds: Space) -> None:
    document = ds.document(ds.contracts, status="waiting_review")
    pii = ds.issue(document, kind="pii_unmasked", requires_role="AC06", page_no=1)
    url = f"/v1/quality-issues/{pii}:resolve"
    async with client(ds) as api:
        refused = await api.post(
            url, json={"resolution": "confirmed_mask"}, headers=ds.headers("admin")
        )
        assert refused.status_code == 403
        assert refused.json()["code"] == "forbidden"
        assert ds.one("SELECT status FROM document.quality_issue WHERE id = %s", pii) == "open"
        done = await api.post(
            url, json={"resolution": "confirmed_mask"}, headers=ds.headers("steward")
        )
        assert done.status_code == 200, done.text
        assert done.json()["resolution"] == "confirmed_mask"


# --- extraction schemas -------------------------------------------------------------------------


FIELDS = [
    {"name": "so_hop_dong", "type": "text", "required": True, "target_column": "so_hop_dong"},
    {"name": "so_tien_vay", "type": "number", "required": True},
    {"name": "so_cccd", "type": "text", "pii": True},
]


# AC-05-06-02
async def test_each_schema_change_is_a_new_version(ds: Space) -> None:
    body = {
        "collection_id": str(ds.contracts),
        "fields": FIELDS,
        "target_dataset_id": str(ds.dataset),
        "join_key": {"so_hop_dong": "ma_khoan_vay"},
    }
    async with client(ds) as api:
        first = await api.post("/v1/extraction-schemas", json=body, headers=ds.headers("admin"))
        assert first.status_code == 201, first.text
        assert first.json()["version"] == 1 and first.json()["status"] == "draft"
        assert first.json()["fields"][2] == {
            "name": "so_cccd",
            "type": "text",
            "required": False,
            "pii": True,
        }
        [(kind, payload)] = ds.events(first.json()["id"])
        assert kind == "extraction_schema.created" and payload["version"] == 1
        second = await api.post("/v1/extraction-schemas", json=body, headers=ds.headers("admin"))
        assert second.json()["version"] == 2

        twice = await api.post(
            "/v1/extraction-schemas",
            json={**body, "fields": [FIELDS[0], FIELDS[0]]},
            headers=ds.headers("admin"),
        )
        assert twice.status_code == 422
        foreign_table = await api.post(
            "/v1/extraction-schemas",
            json={**body, "target_dataset_id": str(ds.other_dataset)},
            headers=ds.headers("admin"),
        )
        assert foreign_table.status_code == 422
        foreign = await api.post(
            "/v1/extraction-schemas",
            json={**body, "collection_id": str(ds.other_collection), "target_dataset_id": None},
            headers=ds.headers("admin"),
        )
        assert foreign.status_code == 404
        forbidden = await api.post(
            "/v1/extraction-schemas", json=body, headers=ds.headers("uploader")
        )
        assert forbidden.status_code == 403


def _schema(ds: Space, collection: uuid.UUID, version: int, **values: Any) -> uuid.UUID:
    return ds.seed.row(
        "document.extraction_schema",
        org_id=ds.org,
        collection_id=collection,
        version=version,
        fields=Json(FIELDS),
        **values,
    )


async def test_a_test_run_is_queued_on_samples_of_the_collection(ds: Space) -> None:
    schema = _schema(ds, ds.contracts, 1, sample_accuracy=Json({"so_hop_dong": 0.9}))
    samples = [ds.document(ds.contracts) for _ in range(2)]
    async with client(ds) as api:
        url = f"/v1/extraction-schemas/{schema}:test"
        queued = await api.post(
            url,
            json={"sample_document_ids": [str(s) for s in samples]},
            headers=ds.headers("admin"),
        )
        assert queued.status_code == 202, queued.text
        assert queued.json()["status"] == "testing"
        assert queued.headers["Location"] == f"/v1/extraction-schemas/{schema}"
        assert (
            ds.one("SELECT status FROM document.extraction_schema WHERE id = %s", schema)
            == "testing"
        )
        # A new run measures afresh.
        assert (
            ds.one("SELECT sample_accuracy FROM document.extraction_schema WHERE id = %s", schema)
            is None
        )
        [(kind, payload)] = ds.events(schema)
        assert kind == "extraction_schema.test_requested"
        assert payload["sample_document_ids"] == [str(s) for s in samples]

        worker_picks = await api.post(url, json={}, headers=ds.headers("admin"))
        assert worker_picks.status_code == 202
        outsider = await api.post(
            url,
            json={"sample_document_ids": [str(ds.document(ds.laws))]},
            headers=ds.headers("admin"),
        )
        assert outsider.status_code == 422
        active = _schema(ds, ds.contracts, 2, status="active", sample_accuracy=Json({}))
        refused = await api.post(
            f"/v1/extraction-schemas/{active}:test", json={}, headers=ds.headers("admin")
        )
        assert refused.status_code == 409
        other = await api.post(url, json={}, headers=ds.headers("admin", ds.other))
        assert other.status_code == 404
        forbidden = await api.post(url, json={}, headers=ds.headers("uploader"))
        assert forbidden.status_code == 403


# AC-05-06-04, AC-05-06-01
async def test_activation_needs_every_required_field_at_95_percent(ds: Space) -> None:
    previous = _schema(
        ds,
        ds.contracts,
        1,
        status="active",
        sample_accuracy=Json({"so_hop_dong": 1, "so_tien_vay": 1}),
    )
    short = _schema(
        ds,
        ds.contracts,
        2,
        status="testing",
        sample_accuracy=Json({"so_hop_dong": 0.99, "so_tien_vay": 0.94}),
    )
    unmeasured = _schema(ds, ds.contracts, 3, status="testing")
    good = _schema(
        ds,
        ds.contracts,
        4,
        status="testing",
        # The optional PII field may be anything; the required ones reach 95%.
        sample_accuracy=Json(
            {"so_hop_dong": {"accuracy": 0.97}, "so_tien_vay": "0.95", "so_cccd": 0.2}
        ),
    )
    async with client(ds) as api:
        below = await api.post(
            f"/v1/extraction-schemas/{short}:activate", headers=ds.headers("admin")
        )
        assert below.status_code == 422
        assert below.json()["code"] == "business_rule_violated"
        assert "so_tien_vay" in below.json()["detail"]
        none = await api.post(
            f"/v1/extraction-schemas/{unmeasured}:activate", headers=ds.headers("admin")
        )
        assert none.status_code == 422
        forbidden = await api.post(
            f"/v1/extraction-schemas/{good}:activate", headers=ds.headers("uploader")
        )
        assert forbidden.status_code == 403
        other = await api.post(
            f"/v1/extraction-schemas/{good}:activate", headers=ds.headers("admin", ds.other)
        )
        assert other.status_code == 404

        active = await api.post(
            f"/v1/extraction-schemas/{good}:activate", headers=ds.headers("admin")
        )
        assert active.status_code == 200, active.text
        assert active.json()["status"] == "active"
        assert (
            ds.one("SELECT status FROM document.extraction_schema WHERE id = %s", previous)
            == "superseded"
        )
        assert str(
            ds.one(
                "SELECT extraction_schema_id FROM document.collection WHERE id = %s", ds.contracts
            )
        ) == str(good)
        assert [e for e, _ in ds.events(previous)] == ["extraction_schema.superseded"]
        [(kind, payload)] = ds.events(good)
        assert kind == "extraction_schema.activated" and payload["version"] == 4
        again = await api.post(
            f"/v1/extraction-schemas/{good}:activate", headers=ds.headers("admin")
        )
        assert again.status_code == 409
        retired = await api.post(
            f"/v1/extraction-schemas/{previous}:activate", headers=ds.headers("admin")
        )
        assert retired.status_code == 409


async def test_document_rows_of_another_organisation_are_invisible(ds: Space, seed: Seed) -> None:
    document = ds.document()
    other_org = seed.org()
    with seed.conn.cursor() as cur:
        cur.execute(
            "UPDATE document.collection SET org_id = %s WHERE id = %s",
            (str(other_org), str(ds.laws)),
        )
        cur.execute(
            "UPDATE document.document SET org_id = %s WHERE id = %s",
            (str(other_org), str(document)),
        )
    async with client(ds) as api:
        got = await api.get(f"/v1/documents/{document}", headers=ds.headers("admin"))
        assert got.status_code == 404
        listed = await api.get("/v1/collections", headers=ds.headers("admin"))
        assert str(ds.laws) not in {c["id"] for c in listed.json()["items"]}
