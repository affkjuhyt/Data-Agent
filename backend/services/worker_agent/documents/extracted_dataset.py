"""An extraction schema's records as a dataset of the Data Space (DI-17, RG-08).

A question that counts or adds up across a whole collection ("bao nhiêu hợp đồng có điều khoản
phạt > 5%", "tổng giá trị các hoá đơn quý 1") cannot be answered from the few passages a search
returns: it is answered from rows. Each document of the collection whose fields its active schema
read (extraction.py, KB-07) is one row of workspace.<table>, a normal dataset of the Data Space
(catalog.dataset, origin `derived`), so the agent's count tool (agentic/tools/extracted.py) and the
exploratory SQL read it like any other table.

What reaches a row is decided here, by rule, never by the model:
- the document is ready, not deleted, in force today (effective_from/effective_to) and not replaced
  by a ready document that names it in supersedes_id; its newest record of the active schema is
  not waiting for review (needs_review);
- a field's value is kept when its quote was found in the document (confidence ≥ 0.8) or a person
  fixed it (a resolved `value_fixed` quality issue), and it reads as the field's type (`typed`,
  with the collection's decimal mark and date order); anything else is NULL, the issue that says
  why stays in the quality queue. A personal-data field holds its masked form, as a text column
  labelled personal data, so the SQL path never shows it to a model;
- every row carries where it came from: the document, its number and title, the record, the
  schema version, how it was extracted, the lowest field confidence and each field's page,
  passage, element, box and quote (source_spans), so an answer can cite the document.

A load is idempotent: the rows are built from the records alone, one per document. Nothing new →
nothing written. Only new or corrected records → they are upserted into the current version on
document_id (no new version for each document a sync brings). A document that left (deleted,
replaced, expired, sent back to review) or another schema → a whole new version (F-01), the one
before kept by versioning. loaded_at marks the records the dataset holds.

An expired document leaves at the next load (any event of its collection); until then the count
tool filters rows by their validity at the asked date, so it is never counted.
"""

from __future__ import annotations

import datetime
import decimal
import json
import logging
import re
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import and_, exists, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from libs import tabular
from libs.dataagent_db import scoped_session, uuid7, versioning
from libs.dataagent_db.models import catalog
from libs.dataagent_db.models import document as doc
from libs.tabular import profile as ingest_profile
from services.worker_agent.events import Event, Handler

log = logging.getLogger(__name__)

DOCUMENT_ROLE = "svc_document"
INGEST_ROLE = "svc_ingest"
CATALOG_ROLE = "svc_catalog"

ACTIVATED = "extraction_schema.activated"
REMOVED = "document.removed"
LOAD_REQUESTED = "extracted_dataset.load_requested"

# How a value was read: the model proposes value + quote, the quote check decides (extraction.py).
EXTRACTION_VERSION = "kb07-quote-check-1"
# A field read at this confidence or above is checked against its quote (extraction.LOW_CONFIDENCE).
CHECKED = 0.8
# A run over a collection reads at most this many documents with the model in one event; the
# rest wait for the next activation or come one by one as they are indexed.
MAX_RUN_DOCUMENTS = 500

NUMBER_TYPES = frozenset(
    {"number", "numeric", "decimal", "integer", "int", "float", "money", "amount", "currency"}
    | {"percent", "percentage", "so", "số", "tien", "tiền"}
)
DATE_TYPES = frozenset({"date", "ngay", "ngày"})
BOOLEAN_TYPES = frozenset({"boolean", "bool", "yes_no", "co_khong"})
# A figure may end with its unit; anything else after it is not a number of this field.
_UNIT = re.compile(r"\s*(%|phần trăm|đồng|dong|đ|vnđ|vnd|usd|\$|eur|€)\s*$", re.IGNORECASE)
_CURRENCY_FIRST = re.compile(r"^\s*(\$|usd|vnd|vnđ|€|eur)\s*", re.IGNORECASE)

# The columns every extracted dataset holds before the schema's fields: what a citation needs.
PROVENANCE: tuple[tuple[str, str, str], ...] = (
    ("document_id", "text", "Mã tài liệu"),
    ("doc_no", "text", "Số hiệu tài liệu"),
    ("document_title", "text", "Tên tài liệu"),
    ("record_id", "text", "Mã bản ghi trích"),
    ("schema_version", "numeric", "Phiên bản lược đồ trích"),
    ("extractor_version", "text", "Cách trích (kiểm câu trích / bộ đọc tài liệu)"),
    ("confidence", "numeric", "Độ tin thấp nhất trong các trường đã nạp"),
    ("source_spans", "jsonb", "Nguồn từng trường: trang, đoạn, phần tử, khung, câu trích"),
    ("effective_from", "date", "Hiệu lực từ"),
    ("effective_to", "date", "Hiệu lực đến"),
)
_NORMALIZED = {
    "text": "string",
    "numeric": "decimal",
    "boolean": "boolean",
    "date": "date",
    "jsonb": "json",
}


# --- typing --------------------------------------------------------------------------------------


def column_type(f: dict[str, Any]) -> str:
    """The column type of a schema field. A personal-data field is stored masked, so as text."""
    if f.get("pii"):
        return "text"
    kind = str(f.get("type") or "").strip().lower()
    if kind in NUMBER_TYPES:
        return "numeric"
    if kind in DATE_TYPES:
        return "date"
    if kind in BOOLEAN_TYPES:
        return "boolean"
    return "text"


def typed(f: dict[str, Any], raw: Any, profile: ingest_profile.Profile) -> Any:
    """The value `raw` writes as the field's type; raises ValueError when it does not read as one.
    No guessing: "khoảng 5 tỷ" or "05 tháng 3" are not a number or a date, they fail."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    kind = column_type(f)
    if kind == "text":
        return str(raw).strip()
    if kind == "numeric":
        figure = _CURRENCY_FIRST.sub("", _UNIT.sub("", str(raw).strip()))
        mark = tabular.decimal_mark([figure]) or profile.decimal_mark
        return tabular.to_number(figure, mark)
    if kind == "date":
        order = tabular.date_order([raw]) or profile.date_order
        return tabular.to_date(raw, order)
    return tabular.to_boolean(raw)


def invalid_fields(
    fields: Sequence[dict[str, Any]], values: dict[str, Any], profile: ingest_profile.Profile
) -> list[str]:
    """The fields whose value is there but does not read as the field's type."""
    bad = []
    for f in fields:
        try:
            typed(f, values.get(f["name"]), profile)
        except (ValueError, decimal.InvalidOperation, ArithmeticError):
            bad.append(str(f["name"]))
    return bad


def _plain(value: Any) -> Any:
    if isinstance(value, decimal.Decimal):
        return str(value)
    if isinstance(value, datetime.date):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


# --- columns and rows ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Column:
    name: str
    type: str
    label: str
    field: str | None = None  # the schema field it holds, None for provenance
    pii: bool = False


def columns_of(fields: Sequence[dict[str, Any]]) -> list[Column]:
    """Provenance first, then one column per field, named [a-z][a-z0-9_]* from its target column
    or name; a name provenance already holds is prefixed f_."""
    out = [Column(n, t, label) for n, t, label in PROVENANCE]
    taken = {c.name for c in out}
    for index, f in enumerate(fields):
        base = tabular.physical_name(f.get("target_column") or f["name"], f"field_{index + 1}")
        name = base if base not in taken else f"f_{base}"[:63]
        if name in taken:
            name = f"{name}_{index + 1}"[:63]
        taken.add(name)
        out.append(
            Column(
                name,
                column_type(f),
                str(f.get("description") or f["name"])[:200],
                str(f["name"]),
                bool(f.get("pii")),
            )
        )
    return out


@dataclass
class Source:
    """One record with its document, as the loader reads them."""

    record_id: uuid.UUID
    document_id: uuid.UUID
    doc_no: str | None
    title: str | None
    values: dict[str, Any]
    confidence: dict[str, float]
    spans: list[dict[str, Any]]
    effective_from: datetime.date | None
    effective_to: datetime.date | None
    # Fields a person fixed (a resolved value_fixed issue): kept whatever the model's confidence.
    confirmed: frozenset[str] = frozenset()


def row_of(
    source: Source,
    columns: Sequence[Column],
    fields: Sequence[dict[str, Any]],
    schema_version: int,
    profile: ingest_profile.Profile,
) -> list[Any]:
    by_name = {str(f["name"]): f for f in fields}
    kept: dict[str, Any] = {}
    confidences: list[float] = []
    for name, f in by_name.items():
        conf = 1.0 if name in source.confirmed else float(source.confidence.get(name) or 0.0)
        if conf < CHECKED:
            continue
        try:
            value = typed(f, source.values.get(name), profile)
        except (ValueError, decimal.InvalidOperation, ArithmeticError):
            continue
        if value is not None:
            kept[name] = value
            confidences.append(conf)
    spans = [
        {k: v for k, v in s.items() if v is not None}
        for s in source.spans
        if isinstance(s, dict) and s.get("field") in kept
    ]
    readers = sorted({str(s["extractor_version"]) for s in spans if s.get("extractor_version")})
    provenance = {
        "document_id": str(source.document_id),
        "doc_no": source.doc_no,
        "document_title": source.title,
        "record_id": str(source.record_id),
        "schema_version": schema_version,
        "extractor_version": "/".join([EXTRACTION_VERSION, *readers]),
        "confidence": min(confidences) if confidences else None,
        "source_spans": spans,
        "effective_from": source.effective_from,
        "effective_to": source.effective_to,
    }
    return [
        _plain(provenance[c.name]) if c.field is None else _plain(kept.get(c.field))
        for c in columns
    ]


def table_name(collection_code: str, dataset_id: uuid.UUID) -> str:
    stem = tabular.physical_name(collection_code, "bo_suu_tap")[:30].rstrip("_")
    return f"trich_{stem}_{dataset_id.hex[-8:]}"


# --- what the dataset should hold ----------------------------------------------------------------


@dataclass
class Wanted:
    org_id: uuid.UUID
    collection_id: uuid.UUID
    data_space_id: uuid.UUID
    collection_code: str
    collection_name: str
    schema_id: uuid.UUID
    schema_version: int
    fields: list[dict[str, Any]]
    dataset_id: uuid.UUID
    sources: list[Source]
    # Records of this collection's schemas the dataset holds now: id → (schema id, still current).
    loaded: dict[uuid.UUID, tuple[uuid.UUID, bool]] = field(default_factory=dict)
    needs_review: int = 0
    profile: ingest_profile.Profile | None = None
    # Other datasets this collection's earlier schemas wrote: no longer the collection's.
    stale_datasets: set[uuid.UUID] = field(default_factory=set)


def in_force(day: datetime.date) -> Any:
    d = doc.Document
    return and_(
        or_(d.effective_from.is_(None), d.effective_from <= day),
        or_(d.effective_to.is_(None), d.effective_to >= day),
    )


def not_replaced() -> Any:
    newer = aliased(doc.Document)
    return ~exists().where(
        newer.supersedes_id == doc.Document.id,
        newer.status == "ready",
        newer.deleted_at.is_(None),
    )


def documents_in_scope(collection_id: uuid.UUID, day: datetime.date) -> Any:
    """The documents of the collection a count is over at `day`."""
    d = doc.Document
    return and_(
        d.collection_id == collection_id,
        d.status == "ready",
        d.deleted_at.is_(None),
        in_force(day),
        not_replaced(),
    )


async def _confirmed(
    session: AsyncSession, record_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, set[str]]:
    ids = list(record_ids)
    out: dict[uuid.UUID, set[str]] = {}
    if not ids:
        return out
    rows = await session.execute(
        select(doc.QualityIssue.extracted_record_id, doc.QualityIssue.detail).where(
            doc.QualityIssue.extracted_record_id.in_(ids),
            doc.QualityIssue.status == "resolved",
            doc.QualityIssue.resolution == "value_fixed",
        )
    )
    for record_id, detail in rows.all():
        if record_id is not None and isinstance(detail, dict) and detail.get("field"):
            out.setdefault(record_id, set()).add(str(detail["field"]))
    return out


async def wanted(
    session: AsyncSession, collection_id: uuid.UUID, today: datetime.date
) -> Wanted | None:
    """What the collection's dataset should hold today (svc_document's session). None when the
    collection has no active schema or is gone; the caller then disables its datasets."""
    collection = await session.get(doc.Collection, collection_id)
    if collection is None or collection.deleted_at is not None:
        return None
    schema = (
        await session.get(doc.ExtractionSchema, collection.extraction_schema_id)
        if collection.extraction_schema_id
        else None
    )
    if schema is None or schema.status != "active" or not schema.fields:
        return None
    if schema.target_dataset_id is None:
        # The schema names no table: the first load makes one, and the schema keeps pointing at it
        # so its later loads (and the next version of the schema, if it is given the same id)
        # write the same dataset.
        schema.target_dataset_id = uuid7()
        await session.flush()
    d, r = doc.Document, doc.ExtractedRecord
    newest = (
        select(r, d)
        .join(d, d.id == r.document_id)
        .where(r.extraction_schema_id == schema.id, documents_in_scope(collection.id, today))
        .order_by(r.document_id, r.created_at.desc(), r.id.desc())
        .distinct(r.document_id)
    )
    found = (await session.execute(newest)).all()
    confirmed = await _confirmed(session, [rec.id for rec, _ in found])
    sources: list[Source] = []
    review = 0
    for rec, document in found:
        if rec.status == "needs_review":
            review += 1
            continue
        sources.append(
            Source(
                record_id=rec.id,
                document_id=document.id,
                doc_no=document.doc_no,
                title=document.title or document.file_name,
                values=dict(rec.values or {}),
                confidence={k: float(v) for k, v in (rec.confidence or {}).items()},
                spans=list(rec.source_spans or []) if isinstance(rec.source_spans, list) else [],
                effective_from=document.effective_from,
                effective_to=document.effective_to,
                confirmed=frozenset(confirmed.get(rec.id, set())),
            )
        )
    schemas = {
        s.id: s.target_dataset_id
        for s in (
            await session.scalars(
                select(doc.ExtractionSchema).where(
                    doc.ExtractionSchema.collection_id == collection.id
                )
            )
        ).all()
    }
    same_target = [sid for sid, target in schemas.items() if target == schema.target_dataset_id]
    loaded = {
        rid: (sid, loaded_at is not None and updated_at <= loaded_at)
        for rid, sid, loaded_at, updated_at in (
            await session.execute(
                select(r.id, r.extraction_schema_id, r.loaded_at, r.updated_at).where(
                    r.extraction_schema_id.in_(same_target), r.loaded_at.is_not(None)
                )
            )
        ).all()
    }
    profile = await ingest_profile.load(
        session,
        collection.data_space_id,
        collection.ingest_profile if isinstance(collection.ingest_profile, dict) else None,
    )
    return Wanted(
        org_id=collection.org_id,
        collection_id=collection.id,
        data_space_id=collection.data_space_id,
        collection_code=collection.code,
        collection_name=collection.name,
        schema_id=schema.id,
        schema_version=schema.version,
        fields=[f for f in schema.fields if isinstance(f, dict) and f.get("name")],
        dataset_id=schema.target_dataset_id,
        sources=sources,
        loaded=loaded,
        needs_review=review,
        profile=profile,
        stale_datasets={
            t for t in schemas.values() if t is not None and t != schema.target_dataset_id
        },
    )


def plan(w: Wanted, exists_now: bool) -> tuple[str, list[Source]]:
    """("rebuild", every row) | ("upsert", new or corrected rows) | ("unchanged", [])."""
    current = {s.record_id for s in w.sources}
    left = [rid for rid, (sid, _) in w.loaded.items() if rid not in current or sid != w.schema_id]
    if not exists_now or left:
        return "rebuild", w.sources
    fresh = [s for s in w.sources if not w.loaded.get(s.record_id, (None, False))[1]]
    return ("upsert", fresh) if fresh else ("unchanged", [])


# --- writing -------------------------------------------------------------------------------------


async def _disable(org_id: uuid.UUID, dataset_ids: Iterable[uuid.UUID]) -> int:
    ids = [i for i in dataset_ids if i is not None]
    if not ids:
        return 0
    async with scoped_session(CATALOG_ROLE, org_id) as session:
        done = await session.execute(
            update(catalog.Dataset)
            .where(
                catalog.Dataset.id.in_(ids),
                catalog.Dataset.origin == "derived",
                catalog.Dataset.schema_name == "workspace",
                catalog.Dataset.enabled.is_(True),
            )
            .values(enabled=False, updated_at=datetime.datetime.now(datetime.UTC))
        )
        return int(getattr(done, "rowcount", 0) or 0)


async def load(
    org_id: uuid.UUID,
    collection_id: uuid.UUID,
    *,
    today: datetime.date | None = None,
    actor: uuid.UUID | None = None,
) -> str | None:
    """Bring the collection's extracted dataset to what its records say. Returns rebuilt,
    upserted, unchanged, disabled or None (nothing to load, or a target it may not write)."""
    day = today or datetime.date.today()
    async with scoped_session(DOCUMENT_ROLE, org_id) as session:
        w = await wanted(session, collection_id, day)
        if w is None:
            targets = (
                await session.scalars(
                    select(doc.ExtractionSchema.target_dataset_id).where(
                        doc.ExtractionSchema.collection_id == collection_id,
                        doc.ExtractionSchema.target_dataset_id.is_not(None),
                    )
                )
            ).all()
        else:
            targets = []
    if w is None:
        # No active schema, or the collection is gone: who may read the collection decided who
        # may read its rows, so the rows go with it.
        return "disabled" if await _disable(org_id, [t for t in targets if t]) else None
    await _disable(org_id, w.stale_datasets)
    assert w.profile is not None
    columns = columns_of(w.fields)
    async with scoped_session(CATALOG_ROLE, org_id) as session:
        dataset = await session.get(catalog.Dataset, w.dataset_id)
        if dataset is not None and (
            dataset.origin != "derived"
            or dataset.schema_name != "workspace"
            or dataset.data_space_id != w.data_space_id
        ):
            # A schema pointed at a table someone else owns (a source table, an upload): never
            # overwritten by extraction.
            log.warning(
                "extracted_dataset | schema %s targets dataset %s, not an extraction table",
                w.schema_id,
                w.dataset_id,
            )
            return None
        found = dataset is not None
        enabled = bool(dataset.enabled) if dataset is not None else False
        name = (
            dataset.table_name
            if dataset is not None
            else table_name(w.collection_code, w.dataset_id)
        )
        known = await active_columns(session, w.dataset_id) if found else set()
    # A schema version with other columns rewrites the table whole.
    mode, sources = plan(w, found and {c.name for c in columns} == known)
    if mode == "unchanged":
        if not enabled:
            await _enable(org_id, w.dataset_id)
        return mode
    payload_columns = [{"name": c.name, "type": c.type} for c in columns]
    rows = [row_of(s, columns, w.fields, w.schema_version, w.profile) for s in sources]
    start: versioning.Current | None = None
    version: int | None = None
    write_to = name
    legacy: str | None = None
    async with scoped_session(INGEST_ROLE, org_id, actor) as session:
        if mode == "upsert":
            current = await versioning.current(session, w.dataset_id)
            # Into the version the view shows: a new document is no new version (F-01 appends).
            await _write(session, current.physical or name, payload_columns, rows, upsert=True)
        else:
            if versioning.ENABLED:
                start = await versioning.current(session, w.dataset_id)
                version = await versioning.next_version(session, name, start)
                write_to = versioning.physical_name(name, version)
            await _write(session, write_to, payload_columns, rows, upsert=False)
            if start is not None and write_to != name:
                legacy = await versioning.publish(session, name, write_to, start)
    async with scoped_session(CATALOG_ROLE, org_id, actor) as session:
        await _catalogue(session, w, name, columns, len(w.sources), actor)
        if version is not None and start is not None:
            await versioning.record(
                session,
                org_id=org_id,
                dataset_id=w.dataset_id,
                version=version,
                physical=write_to,
                columns=payload_columns,
                start=start,
                legacy=legacy,
                actor=actor,
                kind="derive",
                run_id=w.schema_id,
            )
        await _columns(session, w, columns, actor)
    now = datetime.datetime.now(datetime.UTC)
    async with scoped_session(DOCUMENT_ROLE, org_id, actor) as session:
        r = doc.ExtractedRecord
        written = [s.record_id for s in sources]
        if written:
            await session.execute(update(r).where(r.id.in_(written)).values(loaded_at=now))
        if mode == "rebuild":
            gone = [rid for rid in w.loaded if rid not in set(written)]
            if gone:
                await session.execute(update(r).where(r.id.in_(gone)).values(loaded_at=None))
    return "rebuilt" if mode == "rebuild" else "upserted"


async def _write(
    session: AsyncSession,
    table: str,
    columns: list[dict[str, str]],
    rows: list[list[Any]],
    *,
    upsert: bool,
) -> None:
    if upsert:
        for start in range(0, len(rows), 1_000):
            await session.execute(
                text(
                    "SELECT public.da_materialize_upsert(:t, CAST(:c AS jsonb), CAST(:r AS jsonb),"
                    " CAST(:k AS text[]))"
                ),
                {
                    "t": table,
                    "c": json.dumps(columns),
                    "r": json.dumps(rows[start : start + 1_000]),
                    "k": ["document_id"],
                },
            )
        return
    # The first batch creates the table, even with no row: a collection where nothing is extracted
    # yet still has its (empty) table, so a count says 0 rather than failing.
    for start in range(0, max(len(rows), 1), 1_000):
        await session.execute(
            text(
                "SELECT public.da_materialize_upload(:t, CAST(:c AS jsonb), CAST(:r AS jsonb), :x)"
            ),
            {
                "t": table,
                "c": json.dumps(columns),
                "r": json.dumps(rows[start : start + 1_000]),
                "x": start == 0,
            },
        )


async def _enable(org_id: uuid.UUID, dataset_id: uuid.UUID) -> None:
    async with scoped_session(CATALOG_ROLE, org_id) as session:
        await session.execute(
            update(catalog.Dataset).where(catalog.Dataset.id == dataset_id).values(enabled=True)
        )


async def _catalogue(
    session: AsyncSession,
    w: Wanted,
    name: str,
    columns: list[Column],
    row_count: int,
    actor: uuid.UUID | None,
) -> None:
    """The dataset row (svc_catalog), made on the first load."""
    now = datetime.datetime.now(datetime.UTC)
    description = (
        f"Trích từ tài liệu của bộ sưu tập “{w.collection_name}” theo lược đồ trích phiên bản "
        f"{w.schema_version}: mỗi dòng một tài liệu đang hiệu lực, giá trị đã kiểm câu trích hoặc "
        "đã được người sửa; dòng có mã tài liệu, trang và câu trích để dẫn nguồn."
    )
    dataset = await session.get(catalog.Dataset, w.dataset_id)
    if dataset is None:
        dataset = catalog.Dataset(
            id=w.dataset_id,
            org_id=w.org_id,
            source_id=None,
            data_space_id=w.data_space_id,
            origin="derived",
            schema_name="workspace",
            table_name=name,
            object_kind="table",
            display_name=f"Trích từ tài liệu: {w.collection_name}"[:200],
            # Read by everyone of the Data Space, as the collection is (BR-59); a question of
            # another purpose does not see it (readable, hidden below).
            scope="data_space",
            current_version=1,
            created_by=actor,
        )
        session.add(dataset)
    dataset.row_estimate = row_count
    dataset.description = description
    dataset.enabled = True
    # Documents waiting for review are not counted: the table is known to be partial.
    dataset.quality_state = "warning" if w.needs_review else "ok"
    dataset.updated_by, dataset.updated_at = actor, now
    await session.flush()


async def _columns(
    session: AsyncSession, w: Wanted, columns: list[Column], actor: uuid.UUID | None
) -> None:
    """The columns as written: new ones catalogued at the dataset's version, a field the schema
    no longer has marked removed, so no reader is shown a column the table lost."""
    dataset = await session.get(catalog.Dataset, w.dataset_id)
    assert dataset is not None
    version = dataset.current_version or 1
    metas = list(
        (
            await session.scalars(
                select(catalog.ColumnMeta).where(
                    catalog.ColumnMeta.dataset_id == w.dataset_id,
                    catalog.ColumnMeta.removed_in_version.is_(None),
                )
            )
        ).all()
    )
    wanted_names = {c.name for c in columns}
    for meta in metas:
        if meta.name not in wanted_names:
            meta.removed_in_version = version
    known = {m.name for m in metas}
    taken = set(
        (
            await session.scalars(
                select(catalog.ColumnMeta.name).where(
                    catalog.ColumnMeta.dataset_id == w.dataset_id,
                    catalog.ColumnMeta.dataset_version == version,
                )
            )
        ).all()
    )
    session.add_all(
        catalog.ColumnMeta(
            id=uuid.uuid4(),
            org_id=w.org_id,
            dataset_id=w.dataset_id,
            dataset_version=version,
            name=c.name,
            ordinal=ordinal,
            data_type=c.type,
            normalized_type=_NORMALIZED[c.type],
            is_key=c.name == "document_id",
            display_name=c.label,
            pii_label="other" if c.pii else None,
            pii_confirmed=c.pii,
            created_by=actor,
            updated_by=actor,
        )
        for ordinal, c in enumerate(columns)
        if c.name not in known and c.name not in taken
    )


async def active_columns(session: AsyncSession, dataset_id: uuid.UUID) -> set[str]:
    return set(
        (
            await session.scalars(
                select(catalog.ColumnMeta.name).where(
                    catalog.ColumnMeta.dataset_id == dataset_id,
                    catalog.ColumnMeta.removed_in_version.is_(None),
                )
            )
        ).all()
    )


# --- reading: who may, and what a count is over ----------------------------------------------


@dataclass(frozen=True)
class Target:
    """An extracted dataset a question may read."""

    dataset_id: uuid.UUID
    table: str
    title: str
    collection_id: uuid.UUID
    collection_code: str
    collection_name: str
    schema_id: uuid.UUID
    schema_version: int
    data_space_id: uuid.UUID
    fields: tuple[dict[str, Any], ...]
    columns: tuple[Column, ...]
    ingest_profile: dict[str, Any] | None = None


def _readable_query(space_id: uuid.UUID) -> Any:
    es, col, ds = doc.ExtractionSchema, doc.Collection, catalog.Dataset
    return (
        select(es, col, ds)
        .join(col, col.extraction_schema_id == es.id)
        .join(ds, ds.id == es.target_dataset_id)
        .where(
            col.data_space_id == space_id,
            col.deleted_at.is_(None),
            es.status == "active",
            ds.data_space_id == space_id,
            ds.origin == "derived",
            ds.schema_name == "workspace",
            ds.enabled.is_(True),
            ds.deleted_at.is_(None),
        )
    )


async def readable(session: AsyncSession, space_id: uuid.UUID, purpose: str | None) -> list[Target]:
    """The extracted datasets of the Data Space the asker may read: those of the collections a
    question of its purpose may read (BR-59, as documents/retrieve.py filters passages), so the
    rows of a document are read by whoever may read the document, and no one else."""
    query = _readable_query(space_id)
    if purpose is not None:
        query = query.where(doc.Collection.purpose == purpose)
    out = []
    for schema, collection, dataset in (await session.execute(query)).all():
        fields = [f for f in schema.fields or [] if isinstance(f, dict) and f.get("name")]
        written = await active_columns(session, dataset.id)
        # A field the table does not hold yet (a schema activated, its load not run) is not
        # offered: a count over it would fail at the source.
        columns = tuple(c for c in columns_of(fields) if c.name in written)
        out.append(
            Target(
                dataset_id=dataset.id,
                table=dataset.table_name,
                title=dataset.display_name or dataset.table_name,
                collection_id=collection.id,
                collection_code=collection.code,
                collection_name=collection.name,
                schema_id=schema.id,
                schema_version=schema.version,
                data_space_id=space_id,
                fields=tuple(fields),
                columns=columns,
                ingest_profile=collection.ingest_profile
                if isinstance(collection.ingest_profile, dict)
                else None,
            )
        )
    return out


async def hidden(session: AsyncSession, space_id: uuid.UUID, purpose: str | None) -> set[uuid.UUID]:
    """The extracted datasets of the Data Space a question of `purpose` may not read: of a deleted
    collection, of a schema no longer active, or of a collection of another purpose. The SQL path
    (sqlgen/space.load_space) leaves them out as it leaves out a personal table."""
    every = set(
        (
            await session.scalars(
                select(doc.ExtractionSchema.target_dataset_id)
                .join(doc.Collection, doc.Collection.id == doc.ExtractionSchema.collection_id)
                .where(
                    doc.Collection.data_space_id == space_id,
                    doc.ExtractionSchema.target_dataset_id.is_not(None),
                )
            )
        ).all()
    )
    if not every:
        return set()
    allowed = {t.dataset_id for t in await readable(session, space_id, purpose)}
    return {d for d in every if d is not None and d not in allowed}


async def coverage(session: AsyncSession, target: Target, day: datetime.date) -> dict[str, int]:
    """What a count over the dataset stands on (RG-08): the collection's documents in force at
    `day`, how many of them the dataset counts, how many wait for review (not counted), how many
    were not read (no model, nothing found, not run yet)."""
    d, r = doc.Document, doc.ExtractedRecord
    scope = documents_in_scope(target.collection_id, day)
    in_scope = int(await session.scalar(select(func.count()).select_from(d).where(scope)) or 0)
    newest = (
        select(r.document_id, r.status)
        .join(d, d.id == r.document_id)
        .where(r.extraction_schema_id == target.schema_id, scope)
        .order_by(r.document_id, r.created_at.desc(), r.id.desc())
        .distinct(r.document_id)
    ).subquery()
    by_status: dict[str, int] = {
        str(status): int(n)
        for status, n in (
            await session.execute(select(newest.c.status, func.count()).group_by(newest.c.status))
        ).all()
    }
    review = int(by_status.pop("needs_review", 0) or 0)
    extracted = int(sum(by_status.values()) or 0)
    return {
        "documents_in_scope": in_scope,
        "extracted": extracted,
        "needs_review": review,
        "not_extracted": max(in_scope - extracted - review, 0),
    }


# --- events --------------------------------------------------------------------------------------


async def collection_of(org_id: uuid.UUID, document_id: uuid.UUID) -> uuid.UUID | None:
    async with scoped_session(DOCUMENT_ROLE, org_id) as session:
        found: uuid.UUID | None = await session.scalar(
            select(doc.Document.collection_id).where(doc.Document.id == document_id)
        )
        return found


def _id(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value)) if value else None
    except ValueError:
        return None


async def schema_activated(event: Event) -> None:
    """An approved (activated) schema is run over every ready document of its collection not yet
    read with it, then the collection's dataset is rebuilt from the records."""
    from services.worker_agent import questions
    from services.worker_agent.documents import extraction

    schema_id = _id(event.payload.get("extraction_schema_id")) or event.aggregate_id
    async with scoped_session(DOCUMENT_ROLE, event.org_id) as session:
        schema = await session.get(doc.ExtractionSchema, schema_id)
        if schema is None or schema.status != "active":
            return
        collection_id = schema.collection_id
        done = select(doc.ExtractedRecord.document_id).where(
            doc.ExtractedRecord.extraction_schema_id == schema.id
        )
        pending = list(
            (
                await session.scalars(
                    select(doc.Document.id)
                    .where(
                        doc.Document.collection_id == collection_id,
                        doc.Document.status == "ready",
                        doc.Document.deleted_at.is_(None),
                        doc.Document.id.not_in(done),
                    )
                    .order_by(doc.Document.created_at, doc.Document.id)
                    .limit(MAX_RUN_DOCUMENTS)
                )
            ).all()
        )
    gateway = questions.gateway()
    for document_id in pending:
        # Each document is its own record (and its own issues), committed as it is read: a run
        # cut short keeps what it read, and the next one skips it.
        await extraction.extract_document(gateway, event.org_id, document_id)
    await load(event.org_id, collection_id, actor=_id(event.payload.get("actor_id")))


async def collection_changed(event: Event) -> None:
    """A document left (deleted, replaced, its validity changed) or a value was fixed."""
    collection_id = _id(event.payload.get("collection_id"))
    if collection_id is None:
        document_id = _id(event.payload.get("document_id")) or event.aggregate_id
        collection_id = await collection_of(event.org_id, document_id)
    if collection_id is not None:
        await load(event.org_id, collection_id)


HANDLERS: dict[str, Handler] = {
    ACTIVATED: schema_activated,
    REMOVED: collection_changed,
    LOAD_REQUESTED: collection_changed,
}


def outbox(
    org_id: uuid.UUID, document_id: uuid.UUID, collection_id: uuid.UUID, event_type: str
) -> doc.Outbox:
    """The event that asks for a reload, written in the transaction that changed the document."""
    return doc.Outbox(
        id=uuid7(),
        org_id=org_id,
        aggregate_type="document",
        aggregate_id=document_id,
        event_type=event_type,
        payload={"document_id": str(document_id), "collection_id": str(collection_id)},
    )
