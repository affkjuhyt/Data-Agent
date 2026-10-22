"""Claims out of an indexed document and the conflicts they open (KB-05, UC-07-04), on the V3 DDL as
svc_conflict. The model is a script; what is under test is the Verifier, the matching across
documents and what lands in conflict.claim, conflict.conflict and conflict.outbox."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from pydantic import ValidationError

from services.worker_agent import questions
from services.worker_agent.documents import claims, indexed
from services.worker_agent.events import Event
from services.worker_agent.llm import ModelUnavailable, Reply

from .conftest import Seed
from .test_document_answers import Library, add_document, library

pytestmark = [pytest.mark.integration]

OLD = "Phí rút trước hạn bằng 0,1% số tiền rút, áp dụng từ ngày 01/01/2026."
NEW = "Phí rút trước hạn bằng 0,2% số tiền rút, áp dụng từ ngày 01/01/2026."


class Script:
    """Answers each call with the next list of claims, validated as the real gateway does."""

    def __init__(self, *answers: list[dict[str, Any]]) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []

    async def json_chat(
        self, role: str, system: str, user: str, output: type[Any], **_: Any
    ) -> Any:
        self.prompts.append(user)
        try:
            content = output.model_validate({"claims": self.answers.pop(0)})
        except ValidationError as exc:
            raise ModelUnavailable("does not fit") from exc
        return Reply(content, model="responder", tokens=30, latency_ms=2)


def fee(rate: str, quote: str, **extra: Any) -> dict[str, Any]:
    return {
        "subject": "Phí rút trước hạn",
        "attribute": "Mức phí",
        "value_num": rate,
        "unit": "% số tiền rút",
        "passage": 1,
        "quote": quote,
        **extra,
    }


def _claims(seed: Seed, lib: Library) -> list[tuple[Any, ...]]:
    with seed.conn.cursor() as cur:
        cur.execute(
            "SELECT subject_key, attribute, value_num::text, unit, valid_from::text FROM conflict.claim"
            " WHERE data_space_id = %s ORDER BY value_num",
            (str(lib.space),),
        )
        return list(cur.fetchall())


async def test_two_documents_stating_different_fees_open_one_conflict(seed: Seed) -> None:
    lib = library(seed)
    first, _ = add_document(seed, lib, OLD, doc_no="1/QĐ")
    second, _ = add_document(seed, lib, NEW, doc_no="2/QĐ")
    model = Script(
        [fee("0.1", "Phí rút trước hạn bằng 0,1% số tiền rút")],
        [fee("0.2", "Phí rút trước hạn bằng 0,2% số tiền rút")],
    )

    assert await claims.read_document(model, lib.org, first) == (1, 0)
    assert await claims.read_document(model, lib.org, second) == (1, 1)

    # Names are matched normalised; validity falls back to the document's.
    assert _claims(seed, lib) == [
        ("phí rút trước hạn", "mức phí", "0.1", "% số tiền rút", "2026-01-01"),
        ("phí rút trước hạn", "mức phí", "0.2", "% số tiền rút", "2026-01-01"),
    ]
    # The second read was shown what the first one named, so it can reuse the same key.
    assert '"subject": "phí rút trước hạn"' in model.prompts[1]
    kind, stage, delta, pct, material, status = seed.one(
        "SELECT kind, detected_at_stage, delta::text, delta_pct::text, material, status"
        " FROM conflict.conflict WHERE data_space_id = %s",
        lib.space,
    )
    assert (kind, stage, delta, pct, material, status) == (
        "value",
        "ingest",
        "0.1",
        "100.0000",
        True,
        "open",
    )
    roles = seed.one(
        "SELECT array_agg(cc.role ORDER BY cc.role) FROM conflict.conflict_claim cc"
        " JOIN conflict.conflict c ON c.id = cc.conflict_id WHERE c.data_space_id = %s",
        lib.space,
    )
    assert roles == ["competing", "primary"]
    assert (
        seed.one(
            "SELECT payload->>'stage' FROM conflict.outbox WHERE event_type = 'conflict.detected'"
            " AND payload->>'data_space_id' = %s",
            str(lib.space),
        )
        == "ingest"
    )


async def test_a_claim_the_passage_does_not_hold_is_dropped(seed: Seed) -> None:
    lib = library(seed)
    doc, _ = add_document(seed, lib, OLD)
    model = Script(
        [
            # The quote is not in the passage.
            fee("0.1", "Phí rút trước hạn được miễn hoàn toàn"),
            # The number is not in the quote.
            fee("0.5", "Phí rút trước hạn bằng 0,1% số tiền rút"),
            # A quote one digit away from the passage matches approximately; its number does not.
            fee("0.7", "Phí rút trước hạn bằng 0,7% số tiền rút"),
            # A passage it was not shown.
            fee("0.1", "Phí rút trước hạn bằng 0,1% số tiền rút", passage=4),
        ]
    )
    assert await claims.read_document(model, lib.org, doc) == (0, 0)


async def test_same_value_or_another_period_is_no_conflict_and_a_document_is_read_once(
    seed: Seed,
) -> None:
    lib = library(seed)
    a, _ = add_document(seed, lib, OLD, doc_no="1/QĐ")
    b, _ = add_document(seed, lib, OLD, doc_no="1b/QĐ")
    c, _ = add_document(seed, lib, NEW, doc_no="2/QĐ", effective_from="2027-01-01")
    model = Script(
        [fee("0.1", "Phí rút trước hạn bằng 0,1% số tiền rút", valid_to="2026-12-31")],
        [fee("0,1", "Phí rút trước hạn bằng 0,1% số tiền rút")],
        [fee("0.2", "Phí rút trước hạn bằng 0,2% số tiền rút")],
    )
    for doc in (a, b, c):
        await claims.read_document(model, lib.org, doc)
    assert (
        seed.one("SELECT count(*) FROM conflict.conflict WHERE data_space_id = %s", lib.space) == 0
    )
    # A second document.indexed for a document already read asks the model nothing.
    assert await claims.read_document(model, lib.org, a) == (0, 0)
    assert len(model.prompts) == 3


async def test_the_event_handler_uses_the_questions_gateway_and_none_means_no_read(
    seed: Seed, monkeypatch: pytest.MonkeyPatch
) -> None:
    lib = library(seed)
    doc, _ = add_document(seed, lib, OLD)
    monkeypatch.setattr(questions, "_gateway", None)
    monkeypatch.setattr(questions, "_gateway_read", True)
    event = Event(
        uuid.uuid4(), lib.org, indexed.INDEXED, "document", doc, {"document_id": str(doc)}
    )
    await indexed.document_indexed(event)
    assert _claims(seed, lib) == []
    model = Script([fee("0.1", "Phí rút trước hạn bằng 0,1% số tiền rút")])
    monkeypatch.setattr(questions, "_gateway", model)
    await indexed.document_indexed(event)
    assert len(_claims(seed, lib)) == 1
