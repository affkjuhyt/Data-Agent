"""DI-17: a collection's extracted records as a dataset of the Data Space, and a count over it.

On the V3 DDL with the roles worker-agent uses: the model is a script (it proposes, the quote and
type checks decide), the rows land in workspace through the SECURITY DEFINER boundary, the count
runs through the executor as the asker. Under test: typed values and provenance, an idempotent
reload, rows leaving with their document (deleted, replaced, expired, sent to review), who may
read the rows, and a count that says what it stands on."""

from __future__ import annotations

import datetime
import json
import types
import uuid
from typing import Any

import pytest
from psycopg2.extras import Json

from services.worker_agent import questions
from services.worker_agent.agentic.tools.base import ToolContext
from services.worker_agent.agentic.tools.extracted import (
    Condition,
    CountDocuments,
    CountDocumentsArgs,
)
from services.worker_agent.agentic.workspace import Workspace
from services.worker_agent.documents import extracted_dataset as ed
from services.worker_agent.documents import extraction
from services.worker_agent.events import Event
from services.worker_agent.llm import Reply
from services.worker_agent.sqlgen.space import load_space

from .conftest import Seed
from .test_document_answers import Library, add_document, library

pytestmark = [pytest.mark.integration, pytest.mark.req("DI-17")]

PURPOSE = "thẩm định tín dụng"
FIELDS = [
    {"name": "contract_no", "type": "text", "required": True},
    {"name": "penalty_rate", "type": "percent", "description": "Mức phạt vi phạm"},
    {"name": "amount", "type": "number", "required": True},
    {"name": "signed_on", "type": "date"},
]
TEXTS = {
    "HD-01": "Hợp đồng số HD-01 ký ngày 05/03/2026. Mức phạt vi phạm: 8%. "
    "Giá trị hợp đồng: 500.000.000 đồng.",
    "HD-02": "Hợp đồng số HD-02 ký ngày 10/04/2026. Mức phạt vi phạm: 3%. "
    "Giá trị hợp đồng: 200.000.000 đồng.",
    "HD-03": "Hợp đồng số HD-03 ký ngày mồng năm tháng sáu. Mức phạt vi phạm: 12%. "
    "Giá trị hợp đồng: 1.000.000.000 đồng.",
    "HD-04": "Hợp đồng số HD-04. Mức phạt vi phạm: 6%.",
    "HD-05": "Hợp đồng số HD-05 ký ngày 01/07/2026. Mức phạt vi phạm: 9%. "
    "Giá trị hợp đồng: 300.000.000 đồng.",
}


def _reading(no: str) -> list[dict[str, Any]]:
    text = TEXTS[no]
    sentences = {s.split(":")[0].strip(): s.strip() for s in text.split(". ") if s}
    out = [{"field": "contract_no", "value": no, "passage": 1, "quote": f"Hợp đồng số {no}"}]
    penalty = next(s for k, s in sentences.items() if k.startswith("Mức phạt"))
    out.append(
        {
            "field": "penalty_rate",
            "value": penalty.split(": ")[1].rstrip("."),
            "passage": 1,
            "quote": penalty,
        }
    )
    value = next((s for k, s in sentences.items() if k.startswith("Giá trị")), None)
    out.append(
        {
            "field": "amount",
            # HD-04 says no amount: the model's figure cannot be pointed to → review.
            "value": value.split(": ")[1].rstrip(".") if value else "700.000.000 đồng",
            "passage": 1,
            "quote": value or "Giá trị hợp đồng: 700.000.000 đồng",
        }
    )
    if "ký ngày" in text:
        signed = text.split("ký ngày ")[1].split(".")[0]
        out.append(
            {"field": "signed_on", "value": signed, "passage": 1, "quote": f"ký ngày {signed}"}
        )
    return out


class Model:
    """Reads whichever contract the prompt holds."""

    def __init__(self) -> None:
        self.calls = 0

    async def json_chat(
        self, role: str, system: str, user: str, output: type[Any], **_: Any
    ) -> Any:
        self.calls += 1
        no = next(n for n in TEXTS if n in user)
        return Reply(output.model_validate({"values": _reading(no)}), "responder", 40, 3)


def _schema(seed: Seed, lib: Library, *, target: uuid.UUID | None = None) -> uuid.UUID:
    found = seed.row(
        "document.extraction_schema",
        org_id=lib.org,
        collection_id=lib.collection,
        version=1,
        fields=Json(FIELDS),
        sample_accuracy=Json({"contract_no": 1.0, "amount": 0.97}),
        status="active",
        target_dataset_id=target,
    )
    with seed.conn.cursor() as cur:
        cur.execute(
            "UPDATE document.collection SET extraction_schema_id = %s WHERE id = %s",
            (str(found), str(lib.collection)),
        )
    return found


def _contract(seed: Seed, lib: Library, no: str, **kw: Any) -> uuid.UUID:
    document, _ = add_document(seed, lib, TEXTS[no], title=f"Hợp đồng {no}", doc_no=no, **kw)
    return document


async def _extracted(seed: Seed, lib: Library, model: Model, *numbers: str) -> dict[str, Any]:
    made = {}
    for no in numbers:
        made[no] = _contract(seed, lib, no)
        await extraction.extract_document(model, lib.org, made[no])
    return made


def _dataset(seed: Seed, schema: uuid.UUID) -> tuple[Any, ...]:
    return seed.one(  # type: ignore[return-value]
        "SELECT d.id, d.table_name, d.origin, d.enabled, d.current_version, d.quality_state"
        " FROM document.extraction_schema s JOIN catalog.dataset d ON d.id = s.target_dataset_id"
        " WHERE s.id = %s",
        schema,
    )


def _rows(seed: Seed, table: str) -> dict[str, dict[str, Any]]:
    with seed.conn.cursor() as cur:
        cur.execute(f'SELECT * FROM workspace."{table}"')
        names = [c.name for c in cur.description]
        return {r[names.index("doc_no")]: dict(zip(names, r, strict=False)) for r in cur.fetchall()}


@pytest.mark.req("AC-05-06-01")
async def test_records_become_typed_rows_with_provenance_and_a_reload_is_idempotent(
    seed: Seed,
) -> None:
    lib = library(seed, purpose=PURPOSE)
    schema = _schema(seed, lib)
    model = Model()
    made = await _extracted(seed, lib, model, "HD-01", "HD-02", "HD-03", "HD-04")
    # HD-03's date is quoted but is not a date: a field_mismatch, never a guess.
    assert (
        seed.one(
            "SELECT detail->>'field' FROM document.quality_issue WHERE document_id = %s"
            " AND kind = 'field_mismatch'",
            made["HD-03"],
        )
        == "signed_on"
    )

    assert await ed.load(lib.org, lib.collection) == "rebuilt"
    dataset, table, origin, enabled, version, quality = _dataset(seed, schema)
    assert (origin, enabled, version) == ("derived", True, 1)
    # HD-04 waits for review: the table is known to be partial.
    assert quality == "warning"
    rows = _rows(seed, table)
    assert set(rows) == {"HD-01", "HD-02", "HD-03"}
    first = rows["HD-01"]
    assert first["document_id"] == str(made["HD-01"])
    assert first["penalty_rate"] == 8 and first["amount"] == 500_000_000
    assert first["signed_on"] == datetime.date(2026, 3, 5)
    assert first["schema_version"] == 1 and first["confidence"] == 1
    assert {s["field"] for s in first["source_spans"]} == {
        "contract_no",
        "penalty_rate",
        "amount",
        "signed_on",
    }
    assert all(s["page_from"] == 1 and s["chunk_id"] for s in first["source_spans"])
    assert rows["HD-03"]["signed_on"] is None and rows["HD-03"]["amount"] == 1_000_000_000
    assert (
        seed.one(
            "SELECT count(*) FROM document.extracted_record WHERE extraction_schema_id = %s"
            " AND loaded_at IS NOT NULL",
            schema,
        )
        == 3
    )
    assert seed.one(
        "SELECT pii_label IS NULL AND is_key FROM catalog.column_meta"
        " WHERE dataset_id = %s AND name = 'document_id'",
        dataset,
    )

    # Nothing new: nothing written, no new version.
    assert await ed.load(lib.org, lib.collection) == "unchanged"
    # A new document: upserted into the version the view shows, still version 1.
    await _extracted(seed, lib, model, "HD-05")
    assert await ed.load(lib.org, lib.collection) == "upserted"
    assert _dataset(seed, schema)[4] == 1
    assert set(_rows(seed, table)) == {"HD-01", "HD-02", "HD-03", "HD-05"}
    # A redelivered event reads no document twice and loads nothing twice.
    calls = model.calls
    assert await extraction.extract_document(model, lib.org, made["HD-01"]) is None
    assert model.calls == calls
    assert await ed.load(lib.org, lib.collection) == "unchanged"


async def test_a_document_that_leaves_takes_its_row_with_it(seed: Seed) -> None:
    lib = library(seed, purpose=PURPOSE)
    schema = _schema(seed, lib)
    made = await _extracted(seed, lib, Model(), "HD-01", "HD-02", "HD-03", "HD-05")
    assert await ed.load(lib.org, lib.collection) == "rebuilt"
    table = _dataset(seed, schema)[1]

    # Deleted (the api's DELETE writes document.removed; the handler reloads).
    with seed.conn.cursor() as cur:
        cur.execute(
            "UPDATE document.document SET status = 'removed', deleted_at = now() WHERE id = %s",
            (str(made["HD-02"]),),
        )
    await ed.collection_changed(
        Event(
            uuid.uuid4(),
            lib.org,
            ed.REMOVED,
            "document",
            made["HD-02"],
            {"document_id": str(made["HD-02"]), "collection_id": str(lib.collection)},
        )
    )
    assert set(_rows(seed, table)) == {"HD-01", "HD-03", "HD-05"}
    # A row leaving is a new version (F-01): the one before is kept by versioning.
    assert _dataset(seed, schema)[4] == 2
    assert (
        seed.one(
            "SELECT loaded_at FROM document.extracted_record WHERE document_id = %s", made["HD-02"]
        )
        is None
    )

    # Replaced by a ready document that names it, and expired.
    _contract(seed, lib, "HD-04", supersedes=made["HD-01"])
    with seed.conn.cursor() as cur:
        cur.execute(
            "UPDATE document.document SET effective_to = %s WHERE id = %s",
            (datetime.date.today() - datetime.timedelta(days=1), str(made["HD-03"])),
        )
    assert await ed.load(lib.org, lib.collection) == "rebuilt"
    assert set(_rows(seed, table)) == {"HD-05"}


async def _count(lib: Library, args: CountDocumentsArgs, *, purpose: str | None) -> Any:
    ws = Workspace()
    ctx = ToolContext(
        org_id=lib.org,
        space_id=lib.space,
        user_id=lib.user,
        question_id=uuid.uuid4(),
        question="bao nhiêu hợp đồng có mức phạt trên 5%",
        today=datetime.date.today(),
        as_of=None,
        purpose=purpose,
        layer=types.SimpleNamespace(),  # type: ignore[arg-type]
        model=types.SimpleNamespace(),  # type: ignore[arg-type]
        ws=ws,
    )
    return await CountDocuments().run(ctx, args), ws


@pytest.mark.req("AC-05-06-06")
async def test_a_count_over_the_collection_is_answered_from_rows_with_its_coverage(
    seed: Seed,
) -> None:
    lib = library(seed, purpose=PURPOSE)
    _schema(seed, lib)
    await _extracted(seed, lib, Model(), "HD-01", "HD-02", "HD-03", "HD-04")
    # One document of the collection no model read yet.
    _contract(seed, lib, "HD-05")
    assert await ed.load(lib.org, lib.collection) == "rebuilt"

    over = CountDocumentsArgs(where=[Condition(field="penalty_rate", op=">", value="5%")])
    result, ws = await _count(lib, over, purpose=PURPOSE)
    assert result.ok, result.observation
    ds = ws.get(result.datasets[0])
    assert ds is not None and ds.trust == "document" and ds.tool == "count_documents"
    # HD-01 (8%) and HD-03 (12%); HD-04 (6%) waits for review, HD-05 was not read.
    assert [int(r[0]) for r in ds.rows] == [2]
    assert result.detail["coverage"] == {
        "documents_in_scope": 5,
        "extracted": 3,
        "needs_review": 1,
        "not_extracted": 1,
    }
    assert ds.coverage is not None and "3/5" in ds.coverage
    assert "chờ người duyệt" in result.observation
    cited = {c["source"] for c in ws.citations}
    assert cited == {"Hợp đồng HD-01", "Hợp đồng HD-03"}
    assert all(c["type"] == "document" and c["page"] == 1 for c in ws.citations)

    total, ws = await _count(
        lib, CountDocumentsArgs(measure="sum", field="amount"), purpose=PURPOSE
    )
    assert total.ok, total.observation
    row = ws.get(total.datasets[0]).rows[0]  # type: ignore[union-attr]
    assert [int(float(v)) for v in row] == [3, 1_700_000_000, 3]

    # A question of another purpose may not read the collection, so not its rows either.
    refused, _ = await _count(lib, over, purpose="marketing")
    assert not refused.ok and refused.failure == "unavailable"


async def test_the_rows_are_read_as_the_collection_is(seed: Seed) -> None:
    lib = library(seed, purpose=PURPOSE)
    schema = _schema(seed, lib)
    await _extracted(seed, lib, Model(), "HD-01")
    assert await ed.load(lib.org, lib.collection) == "rebuilt"
    dataset = _dataset(seed, schema)[0]
    from libs.dataagent_db import scoped_session

    async with scoped_session("svc_qa", lib.org, lib.user) as session:
        assert {t.dataset_id for t in await ed.readable(session, lib.space, PURPOSE)} == {dataset}
        assert await ed.hidden(session, lib.space, "marketing") == {dataset}
        shown = await load_space(session, lib.space, lib.user, purpose=PURPOSE)
        assert dataset in {t.dataset_id for t in shown.schema.tables}
        other = await load_space(session, lib.space, lib.user, purpose="marketing")
        assert dataset not in {t.dataset_id for t in other.schema.tables}

    # The collection deleted: its rows go with it.
    with seed.conn.cursor() as cur:
        cur.execute(
            "UPDATE document.collection SET deleted_at = now() WHERE id = %s",
            (str(lib.collection),),
        )
    assert await ed.load(lib.org, lib.collection) == "disabled"
    assert _dataset(seed, schema)[3] is False


async def test_a_schema_pointed_at_someone_elses_table_writes_nothing(seed: Seed) -> None:
    lib = library(seed, purpose=PURPOSE)
    upload = seed.row(
        "catalog.dataset",
        org_id=lib.org,
        data_space_id=lib.space,
        origin="upload",
        schema_name="workspace",
        table_name=f"upload_x_{uuid.uuid4().hex[:8]}",
        expires_at="2027-01-01T00:00:00Z",
        enabled=True,
    )
    _schema(seed, lib, target=upload)
    await _extracted(seed, lib, Model(), "HD-01")
    assert await ed.load(lib.org, lib.collection) is None
    assert seed.one("SELECT current_version FROM catalog.dataset WHERE id = %s", upload) == 1


@pytest.mark.req("AC-05-06-01")
async def test_an_activated_schema_runs_over_every_ready_document_once(
    seed: Seed, monkeypatch: pytest.MonkeyPatch
) -> None:
    lib = library(seed, purpose=PURPOSE)
    schema = _schema(seed, lib)
    _contract(seed, lib, "HD-01")
    _contract(seed, lib, "HD-02")
    model = Model()
    monkeypatch.setattr(questions, "gateway", lambda: model)
    event = Event(
        uuid.uuid4(),
        lib.org,
        ed.ACTIVATED,
        "extraction_schema",
        schema,
        json.loads(json.dumps({"extraction_schema_id": str(schema)})),
    )
    await ed.schema_activated(event)
    assert model.calls == 2
    assert set(_rows(seed, _dataset(seed, schema)[1])) == {"HD-01", "HD-02"}
    # Redelivered: no document read again, nothing rewritten.
    await ed.schema_activated(event)
    assert model.calls == 2
    assert _dataset(seed, schema)[4] == 1
