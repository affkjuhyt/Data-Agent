"""The document reads added 12/10/2026 against PostgreSQL: GET /v1/documents with each document's
current preprocessing step, GET /v1/extraction-schemas and /{id}, GET /v1/document-sources
(UC-05-01, UC-05-02, UC-05-05, UC-05-06).

Organisation, collections and users come from the document suite's fixture: the data admin is a
member of both Data Spaces, so a row of space A read with X-Data-Space B proves the space filter.
Preprocessing is worker-ingest's; the rows it would write are seeded.
"""

from __future__ import annotations

import uuid

import pytest
from psycopg2.extras import Json

from . import test_document
from .test_document import Space, _user, client

pytestmark = [pytest.mark.integration, pytest.mark.contract]
# The document suite's fixture, reused: the same organisation, users and rows.
ds = test_document.ds


def _no_ask(ds: Space) -> str:
    """A user of the Data Space whose role does not grant ask, the capability of these reads."""
    role = ds.seed.row(
        "org.role", org_id=ds.org, code="UPLOAD", name="UPLOAD", capabilities=["upload"]
    )
    _user(ds.seed, ds.org, "noask", role, ds.space)
    return "noask"


def _schema(ds: Space, collection: uuid.UUID, version: int, **values: object) -> uuid.UUID:
    values.setdefault("status", "draft")
    return ds.seed.row(
        "document.extraction_schema",
        org_id=ds.org,
        collection_id=collection,
        version=version,
        fields=Json([{"name": "so_hop_dong", "type": "text", "required": True}]),
        **values,
    )


# --- documents ----------------------------------------------------------------------------------


async def test_documents_list_with_their_preprocessing_step(ds: Space) -> None:
    queued = ds.document(status="received", file_name="moi_nhan.pdf")
    reading = ds.document(status="processing", page_count=3, file_name="dang_doc.pdf")
    ds.page(reading, 1)
    indexing = ds.document(status="processing", page_count=1, file_name="dang_lap_chi_muc.pdf")
    ds.page(indexing, 1)
    ds.chunk(indexing, "Điều 1. Phạm vi điều chỉnh", qdrant_point_id=uuid.uuid4())
    ds.chunk(indexing, "Điều 2. Đối tượng áp dụng")
    review = ds.document(status="waiting_review", page_count=1, file_name="cho_duyet.pdf")
    ds.issue(review, severity="blocking")
    done = ds.document(title="Thông tư 39/2016", doc_no="39/2016/TT-NHNN", page_count=1)
    ds.page(done, 1)
    ds.chunk(done, "Điều 1.", qdrant_point_id=uuid.uuid4())
    contract = ds.document(ds.contracts, status="failed", error="Tệp hỏng")
    ds.document(status="removed", file_name="da_xoa.pdf", deleted_at="2026-10-01T00:00:00Z")
    async with client(ds) as api:
        everything = await api.get("/v1/documents", headers=ds.headers("analyst"))
        assert everything.status_code == 200, everything.text
        rows = {r["id"]: r for r in everything.json()["items"]}
        steps = {doc_id: rows[str(doc_id)]["step"] for doc_id in (queued, reading, indexing)}
        assert steps == {queued: "queued", reading: "reading_pages", indexing: "indexing"}
        assert rows[str(review)]["step"] == "quality_review"
        assert rows[str(done)]["step"] == "done"
        assert rows[str(contract)]["step"] == "failed"
        # A soft-deleted document is not listed.
        assert len(rows) == 6
        assert rows[str(indexing)]["progress"] == {
            "pages_total": 1,
            "pages_read": 1,
            "pages_usable": 1,
            "chunks": 2,
            "chunks_indexed": 1,
        }
        # No storage reference leaves the api (A1.1).
        assert "object_key" not in rows[str(done)] and "workflow_id" not in rows[str(done)]

        by_collection = await api.get(
            "/v1/documents",
            params={"collection_id": str(ds.contracts)},
            headers=ds.headers("analyst"),
        )
        assert [r["id"] for r in by_collection.json()["items"]] == [str(contract)]
        processing = await api.get(
            "/v1/documents", params={"status": "processing"}, headers=ds.headers("analyst")
        )
        assert {r["id"] for r in processing.json()["items"]} == {str(reading), str(indexing)}
        by_number = await api.get(
            "/v1/documents", params={"q": "39/2016"}, headers=ds.headers("analyst")
        )
        assert [r["id"] for r in by_number.json()["items"]] == [str(done)]
        # q is a literal substring: "%" matches only a "%" in a name.
        wildcard = await api.get("/v1/documents", params={"q": "%"}, headers=ds.headers("analyst"))
        assert wildcard.json()["items"] == []

        first = await api.get(
            "/v1/documents",
            params={"page_size": 4, "order_by": "file_name asc"},
            headers=ds.headers("analyst"),
        )
        rest = await api.get(
            "/v1/documents",
            params={
                "page_size": 4,
                "order_by": "file_name asc",
                "page_token": first.json()["next_page_token"],
            },
            headers=ds.headers("analyst"),
        )
        names = [r["file_name"] for r in first.json()["items"] + rest.json()["items"]]
        assert len(names) == 6 and names == sorted(names)


async def test_documents_list_is_scoped_and_gated(ds: Space) -> None:
    mine = ds.document()
    theirs = ds.document(ds.other_collection)
    async with client(ds) as api:
        here = await api.get("/v1/documents", headers=ds.headers("admin"))
        there = await api.get("/v1/documents", headers=ds.headers("admin", ds.other))
        assert [r["id"] for r in here.json()["items"]] == [str(mine)]
        assert [r["id"] for r in there.json()["items"]] == [str(theirs)]
        # A collection of another Data Space lists nothing, rather than its documents.
        foreign = await api.get(
            "/v1/documents",
            params={"collection_id": str(ds.other_collection)},
            headers=ds.headers("admin"),
        )
        assert foreign.json()["items"] == []
        no_ask = await api.get("/v1/documents", headers=ds.headers(_no_ask(ds)))
        assert no_ask.status_code == 403
        not_member = await api.get("/v1/documents", headers=ds.headers("analyst", ds.other))
        assert not_member.status_code == 404
        no_space = await api.get("/v1/documents", headers={"Authorization": "Bearer kc-analyst"})
        assert no_space.status_code == 400
        bad_order = await api.get(
            "/v1/documents", params={"order_by": "sha256 asc"}, headers=ds.headers("analyst")
        )
        assert bad_order.status_code == 422


# --- extraction schemas -------------------------------------------------------------------------


async def test_extraction_schemas_list_and_read(ds: Space) -> None:
    v1 = _schema(ds, ds.contracts, 1, status="superseded")
    v2 = _schema(
        ds,
        ds.contracts,
        2,
        status="testing",
        target_dataset_id=ds.dataset,
        sample_accuracy=Json({"so_hop_dong": 0.97}),
    )
    laws = _schema(ds, ds.laws, 1)
    other = _schema(ds, ds.other_collection, 1)
    async with client(ds) as api:
        listed = await api.get("/v1/extraction-schemas", headers=ds.headers("analyst"))
        assert listed.status_code == 200, listed.text
        assert {r["id"] for r in listed.json()["items"]} == {str(v1), str(v2), str(laws)}
        of_contracts = await api.get(
            "/v1/extraction-schemas",
            params={"collection_id": str(ds.contracts), "order_by": "version desc"},
            headers=ds.headers("analyst"),
        )
        assert [r["id"] for r in of_contracts.json()["items"]] == [str(v2), str(v1)]

        one = await api.get(f"/v1/extraction-schemas/{v2}", headers=ds.headers("analyst"))
        assert one.status_code == 200, one.text
        body = one.json()
        assert body["version"] == 2 and body["status"] == "testing"
        assert body["target_dataset_id"] == str(ds.dataset)
        assert body["sample_accuracy"] == {"so_hop_dong": 0.97}
        assert body["fields"][0]["name"] == "so_hop_dong" and body["fields"][0]["required"]
        assert one.headers["ETag"]

        # :test's Location is this read.
        tested = await api.post(
            f"/v1/extraction-schemas/{v2}:test", json={}, headers=ds.headers("admin")
        )
        assert tested.status_code == 202, tested.text
        followed = await api.get(tested.headers["Location"], headers=ds.headers("admin"))
        assert followed.status_code == 200 and followed.json()["sample_accuracy"] is None

        other_space = await api.get(f"/v1/extraction-schemas/{other}", headers=ds.headers("admin"))
        assert other_space.status_code == 404
        unknown = await api.get(
            f"/v1/extraction-schemas/{uuid.uuid4()}", headers=ds.headers("analyst")
        )
        assert unknown.status_code == 404
        no_ask = _no_ask(ds)
        assert (
            await api.get("/v1/extraction-schemas", headers=ds.headers(no_ask))
        ).status_code == 403
        gated = await api.get(f"/v1/extraction-schemas/{v2}", headers=ds.headers(no_ask))
        assert gated.status_code == 403


# --- document sources ---------------------------------------------------------------------------


async def test_document_sources_list_without_secrets(ds: Space) -> None:
    async with client(ds) as api:
        made = await api.post(
            "/v1/document-sources",
            json={
                "collection_id": str(ds.contracts),
                "kind": "sharepoint",
                "location": "https://sharepoint.example.vn/sites/tin-dung",
                "sync_cron": "0 * * * *",
                "credentials": {"username": "svc", "password": "mat-khau-bi-mat"},
            },
            headers=ds.headers("admin"),
        )
        assert made.status_code == 201, made.text
        plain = await api.post(
            "/v1/document-sources",
            json={
                "collection_id": str(ds.laws),
                "kind": "smb_folder",
                "location": "//fileserver/phap-quy",
                "sync_cron": "0 6 * * *",
            },
            headers=ds.headers("admin"),
        )
        abroad = await api.post(
            "/v1/document-sources",
            json={
                "collection_id": str(ds.other_collection),
                "kind": "smb_folder",
                "location": "//fileserver/b",
                "sync_cron": "0 6 * * *",
            },
            headers=ds.headers("admin", ds.other),
        )
        assert abroad.status_code == 201, abroad.text

        listed = await api.get("/v1/document-sources", headers=ds.headers("analyst"))
        assert listed.status_code == 200, listed.text
        rows = {r["id"]: r for r in listed.json()["items"]}
        assert set(rows) == {made.json()["id"], plain.json()["id"]}
        sharepoint = rows[made.json()["id"]]
        assert (
            sharepoint["has_credentials"] is True
            and rows[plain.json()["id"]]["has_credentials"] is False
        )
        assert "secret_ref" not in sharepoint and "credentials" not in sharepoint
        assert "mat-khau-bi-mat" not in listed.text and "bao://" not in listed.text

        of_laws = await api.get(
            "/v1/document-sources",
            params={"collection_id": str(ds.laws)},
            headers=ds.headers("analyst"),
        )
        assert [r["id"] for r in of_laws.json()["items"]] == [plain.json()["id"]]
        there = await api.get("/v1/document-sources", headers=ds.headers("admin", ds.other))
        assert [r["id"] for r in there.json()["items"]] == [abroad.json()["id"]]
        no_ask = await api.get("/v1/document-sources", headers=ds.headers(_no_ask(ds)))
        assert no_ask.status_code == 403
        not_member = await api.get("/v1/document-sources", headers=ds.headers("analyst", ds.other))
        assert not_member.status_code == 404
