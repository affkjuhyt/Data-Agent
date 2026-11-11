"""Fields out of an indexed document by its collection's extraction schema (KB-07, UC-05-06), on
the V3 DDL as svc_document. The model is a script; under test are the Verifier, masking, the
review status and one record per document and schema version."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from psycopg2.extras import Json

from services.worker_agent import questions
from services.worker_agent.documents import extraction
from services.worker_agent.events import Event
from services.worker_agent.llm import Reply

from .conftest import Seed
from .test_document_answers import Library, add_document, library

pytestmark = [pytest.mark.integration]

CONTRACT = (
    "Hợp đồng tín dụng số HD-2026-001 ký ngày 05/03/2026. Bên vay: Nguyễn Văn A, "
    "CCCD 001203004567. Số tiền vay: 500.000.000 đồng."
)
FIELDS = [
    {"name": "contract_no", "type": "text", "required": True},
    {"name": "national_id", "type": "text", "required": False, "pii": True},
    {"name": "amount", "type": "number", "required": True},
]


class Script:
    def __init__(self, values: list[dict[str, Any]]) -> None:
        self.values = values
        self.calls = 0

    async def json_chat(
        self, role: str, system: str, user: str, output: type[Any], **_: Any
    ) -> Any:
        self.calls += 1
        return Reply(output.model_validate({"values": self.values}), "responder", 40, 3)


def schema(seed: Seed, lib: Library, *, status: str = "active") -> Any:
    found = seed.row(
        "document.extraction_schema",
        org_id=lib.org,
        collection_id=lib.collection,
        version=1,
        fields=Json(FIELDS),
        sample_accuracy=Json({"contract_no": 1.0, "amount": 0.97}),
        status=status,
    )
    with seed.conn.cursor() as cur:
        cur.execute(
            "UPDATE document.collection SET extraction_schema_id = %s WHERE id = %s",
            (str(found), str(lib.collection)),
        )
    return found


def _record(seed: Seed, document: Any) -> tuple[Any, ...]:
    with seed.conn.cursor() as cur:
        cur.execute(
            "SELECT values, confidence, source_spans, status FROM document.extracted_record"
            " WHERE document_id = %s",
            (str(document),),
        )
        rows = cur.fetchall()
    assert len(rows) == 1
    return rows[0]


async def test_checked_values_are_kept_with_their_spans_and_pii_is_masked(seed: Seed) -> None:
    lib = library(seed, purpose="thẩm định tín dụng")
    schema(seed, lib)
    doc, chunk = add_document(
        seed, lib, CONTRACT, title="Hợp đồng HD-2026-001", doc_no="HD-2026-001"
    )
    model = Script(
        [
            {
                "field": "contract_no",
                "value": "HD-2026-001",
                "passage": 1,
                "quote": "Hợp đồng tín dụng số HD-2026-001",
            },
            {
                "field": "national_id",
                "value": "001203004567",
                "passage": 1,
                "quote": "CCCD 001203004567",
            },
            {
                "field": "amount",
                "value": "500.000.000 đồng",
                "passage": 1,
                "quote": "Số tiền vay: 500.000.000 đồng",
            },
        ]
    )
    assert await extraction.extract_document(model, lib.org, doc) == "extracted"
    values, confidence, spans, status = _record(seed, doc)
    assert values == {
        "contract_no": "HD-2026-001",
        "national_id": "•••••••••567",
        "amount": "500.000.000 đồng",
    }
    assert confidence == {"contract_no": 1.0, "national_id": 1.0, "amount": 1.0}
    assert {s["field"]: s["chunk_id"] for s in spans} == {
        "contract_no": str(chunk),
        "national_id": str(chunk),
        "amount": str(chunk),
    }
    assert next(s for s in spans if s["field"] == "national_id")["quote"] is None
    # Read once per document and schema version: a redelivered event asks the model nothing.
    assert await extraction.extract_document(model, lib.org, doc) is None
    assert model.calls == 1


async def test_a_required_value_it_cannot_point_to_sends_the_record_to_review(seed: Seed) -> None:
    lib = library(seed)
    schema(seed, lib)
    doc, _ = add_document(seed, lib, CONTRACT)
    model = Script(
        [
            {
                "field": "contract_no",
                "value": "HD-2026-001",
                "passage": 1,
                "quote": "Hợp đồng tín dụng số HD-2026-001",
            },
            # Not in the document.
            {
                "field": "amount",
                "value": "700.000.000 đồng",
                "passage": 1,
                "quote": "Số tiền vay: 700.000.000 đồng",
            },
        ]
    )
    assert await extraction.extract_document(model, lib.org, doc) == "needs_review"
    values, confidence, _, _ = _record(seed, doc)
    assert values["amount"] == "700.000.000 đồng" and confidence["amount"] == 0.5
    assert values["national_id"] is None and confidence["national_id"] == 0.0


async def test_no_active_schema_means_no_reading(seed: Seed) -> None:
    lib = library(seed)
    schema(seed, lib, status="draft")
    doc, _ = add_document(seed, lib, CONTRACT)
    model = Script([])
    assert await extraction.extract_document(model, lib.org, doc) is None
    assert model.calls == 0


# --- a schema's test on sample documents (POST /v1/extraction-schemas/{id}:test, BR-110) ---------

COPY = "Hợp đồng tín dụng số HD-2026-001 (bản sao). Số tiền vay: 700.000.000 đồng."
READING = [
    {
        "field": "contract_no",
        "value": "HD-2026-001",
        "passage": 1,
        "quote": "Hợp đồng tín dụng số HD-2026-001",
    },
    {
        "field": "national_id",
        "value": "001203004567",
        "passage": 1,
        "quote": "CCCD 001203004567",
    },
    {
        "field": "amount",
        "value": "500.000.000 đồng",
        "passage": 1,
        "quote": "Số tiền vay: 500.000.000 đồng",
    },
]


def _schema_under_test(seed: Seed, lib: Library, *, status: str = "testing") -> Any:
    """A schema under test: not the collection's, no accuracy yet (the api cleared it)."""
    return seed.row(
        "document.extraction_schema",
        org_id=lib.org,
        collection_id=lib.collection,
        version=1,
        fields=Json(FIELDS),
        status=status,
    )


def _test_event(lib: Library, schema_id: Any, samples: list[Any]) -> Event:
    return Event(
        uuid.uuid4(),
        lib.org,
        extraction.TEST_REQUESTED,
        "extraction_schema",
        schema_id,
        {
            "extraction_schema_id": str(schema_id),
            "collection_id": str(lib.collection),
            "data_space_id": str(lib.space),
            "sample_document_ids": [str(s) for s in samples],
            "status": "testing",
        },
    )


def _accuracy(seed: Seed, schema_id: Any) -> tuple[Any, ...]:
    return seed.one(  # type: ignore[return-value]
        "SELECT status, sample_accuracy FROM document.extraction_schema WHERE id = %s", schema_id
    )


async def test_a_schema_test_measures_each_field_on_the_collections_documents(
    seed: Seed, monkeypatch: pytest.MonkeyPatch
) -> None:
    lib = library(seed)
    found = _schema_under_test(seed, lib)
    add_document(seed, lib, CONTRACT, doc_no="HD-2026-001")
    add_document(seed, lib, COPY, doc_no="HD-2026-001-BS")
    model = Script(READING)
    monkeypatch.setattr(questions, "gateway", lambda: model)

    await extraction.test_requested(_test_event(lib, found, []))

    # The copy holds the contract number but neither the id nor that amount: those values cannot
    # be pointed to there.
    assert _accuracy(seed, found) == (
        "testing",
        {
            "contract_no": {"accuracy": 1.0, "documents": 2},
            "national_id": {"accuracy": 0.5, "documents": 2},
            "amount": {"accuracy": 0.5, "documents": 2},
        },
    )
    assert model.calls == 2
    # A test keeps no record: the schema is not the collection's yet.
    assert (
        seed.one(
            "SELECT count(*) FROM document.extracted_record WHERE extraction_schema_id = %s", found
        )
        == 0
    )


async def test_a_schema_test_reads_only_the_named_samples(
    seed: Seed, monkeypatch: pytest.MonkeyPatch
) -> None:
    lib = library(seed)
    found = _schema_under_test(seed, lib)
    first, _ = add_document(seed, lib, CONTRACT, doc_no="HD-2026-001")
    add_document(seed, lib, COPY, doc_no="HD-2026-001-BS")
    model = Script(READING)
    monkeypatch.setattr(questions, "gateway", lambda: model)

    await extraction.test_requested(_test_event(lib, found, [first]))

    assert model.calls == 1
    assert _accuracy(seed, found)[1]["amount"] == {"accuracy": 1.0, "documents": 1}


async def test_a_schema_no_longer_testing_is_not_measured(
    seed: Seed, monkeypatch: pytest.MonkeyPatch
) -> None:
    lib = library(seed)
    found = _schema_under_test(seed, lib, status="draft")
    add_document(seed, lib, CONTRACT)
    model = Script(READING)
    monkeypatch.setattr(questions, "gateway", lambda: model)

    await extraction.test_requested(_test_event(lib, found, []))

    assert model.calls == 0
    assert _accuracy(seed, found) == ("draft", None)
