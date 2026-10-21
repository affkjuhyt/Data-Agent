"""Fields out of an indexed document by its collection's extraction schema (KB-07, UC-05-06), on
the V3 DDL as svc_document. The model is a script; under test are the Verifier, masking, the
review status and one record per document and schema version."""

from __future__ import annotations

from typing import Any

import pytest
from psycopg2.extras import Json

from services.worker_agent.documents import extraction
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
