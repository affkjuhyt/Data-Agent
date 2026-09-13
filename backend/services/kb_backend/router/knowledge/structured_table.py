from __future__ import annotations

from typing import Any, Optional
import logging
import os
import uuid
import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select as sa_select, update as sa_update, text

from libs.shared.row_version import with_new_version
from libs.basemodel.services_databaseconnector.postgres_model import KBStructuredImportJobInsert, Tier
from libs.basemodel.services_databaseconnector.postgres_orm.knowledge_base_orm import (
    KBStructuredImportJobORM, KBDataORM, KBTableORM,
)
from libs.shared.sql_identifiers import safe_identifier, quote_identifier, UnsafeIdentifier
from services.kb_backend.dependencies.services import svc
from services.kb_backend.dependencies.auth import require_perm
from services.kb_backend.pipeline.ingestion import config as cfg
from services.kb_backend.router.knowledge.ingestion import _fetch_kbdata_or_404

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/pipeline/structured", tags=["StructuredTable"])

_INSERT_BATCH_SIZE = int(os.getenv("KB_STRUCTURED_INSERT_BATCH", "1000"))
_MAX_IMPORT_ROWS = int(os.getenv("KB_STRUCTURED_MAX_ROWS", "200000"))

# Off until the agent's configured reader role is pinned down: RLS on a role
# that never sets app.tenant_id empties these tables instead of scoping them.
_ENFORCE_ROW_LEVEL_SECURITY = os.getenv("KB_STRUCTURED_RLS", "").lower() in ("1", "true", "yes")

# Closed vocabulary — matches kb_ingestion_worker's "table-schema-proposal"
# LLM schema and the columns() TYPE_MAP below one-for-one. Never let a
# caller-supplied type string reach SQL directly.
_TYPE_MAP: dict[str, str] = {
    "text": "TEXT",
    "integer": "BIGINT",
    "numeric": "NUMERIC",
    "boolean": "BOOLEAN",
    "date": "DATE",
    "timestamp": "TIMESTAMPTZ",
}


class ColumnSchema(BaseModel):
    name: str
    type: str
    nullable: bool = True
    # The ORIGINAL spreadsheet header this column's values come from — used
    # to map staged rows to columns at insert time. `name` is whatever the
    # human renamed it to for the review screen; it does not necessarily
    # match any header in the parsed file. None for a column the human
    # added by hand (no source data — every row gets NULL for it).
    source_header: Optional[str] = None


class TableSchema(BaseModel):
    table_name: str
    columns: list[ColumnSchema]


class ApproveRequest(BaseModel):
    # Field is named `schema_` on the Python side only because `schema` is a
    # reserved BaseModel method name in Pydantic v2 — the wire field (and
    # what the frontend sends) is plain `schema`.
    schema_: TableSchema = Field(alias="schema")
    model_config = {"populate_by_name": True}


async def _latest_job(postgres: Any, data_id: str) -> KBStructuredImportJobORM | None:
    async with postgres.get_client() as session:
        return (await session.execute(
            sa_select(KBStructuredImportJobORM)
            .where(KBStructuredImportJobORM.owner_id == uuid.UUID(data_id))
            .order_by(KBStructuredImportJobORM.created_at.desc())
            .limit(1)
        )).scalars().first()


def _job_to_dict(job: KBStructuredImportJobORM) -> dict[str, Any]:
    return {
        "job_id": str(job.job_id),
        "status": job.status,
        "llm_proposed_schema": job.llm_proposed_schema,
        "human_edited_schema": job.human_edited_schema,
        "sample_rows": job.sample_rows,
        "created_table_name": job.created_table_name,
        "error_message": job.error_message,
    }


@router.post("/{data_id}/propose")
async def propose_structured_table(
        data_id: str,
        request: Request,
        ctx: dict[str, Any] = Depends(require_perm("processLayer")),
) -> dict[str, Any]:
    postgres = svc(request, "postgres")
    kafka_producer = svc(request, "kafka_producer")
    mongo = svc(request, "mongo")

    record = await _fetch_kbdata_or_404(postgres, ctx["tenant_id"], ctx["role_id"], data_id)
    if record.get("ingestion_mode") != "structured_table":
        raise HTTPException(status_code=409, detail="Document was not uploaded in structured_table mode")
    if record.get("extension", "").lower() not in ("xlsx", "xls", "csv"):
        raise HTTPException(status_code=409, detail="Only .xlsx, .xls, .csv documents support this")

    job_insert = KBStructuredImportJobInsert(owner_id=data_id, status="proposing", created_by=ctx["user_name"])
    job_res = await postgres.insert(job_insert)
    if job_res.code != 200:
        raise HTTPException(status_code=500, detail="Could not create import job")
    job_id: str = job_res.data["job_id"]

    try:
        await kafka_producer.produce(
            topic=cfg.KAFKA_TOPIC_STRUCTURED_PROPOSE,
            value={
                "event": "propose_structured",
                "data_id": data_id,
                "job_id": job_id,
                "tenant_id": ctx["tenant_id"],
                "extension": record.get("extension", ""),
                "minio_path": record.get("path", ""),
            },
            key=data_id,
        )
    except Exception as e:
        log.exception("Kafka produce error (structured propose): %s", e)
        raise HTTPException(status_code=500, detail="Schema-proposal queuing failed")

    now = datetime.datetime.now(datetime.timezone.utc)
    await mongo.get_client()["kb_ingestion_logs"].update_one(
        {"data_id": data_id},
        {
            "$set": {"status": "PENDING", "stage": "schema_proposal", "updated_at": now},
            "$push": {"logs": {"timestamp": now, "level": "INFO", "message": "Schema proposal queued"}},
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )

    log.info("POST /structured/%s/propose job=%s tenant=%s", data_id, job_id, ctx["tenant_id"])
    return {"job_id": job_id, "status": "proposing"}


@router.get("/{data_id}")
async def get_structured_job(
        data_id: str,
        request: Request,
        ctx: dict[str, Any] = Depends(require_perm("viewDocuments")),
) -> dict[str, Any]:
    postgres = svc(request, "postgres")
    await _fetch_kbdata_or_404(postgres, ctx["tenant_id"], ctx["role_id"], data_id)

    job = await _latest_job(postgres, data_id)
    if job is None:
        raise HTTPException(status_code=404, detail="No structured-import job for this document")
    return _job_to_dict(job)


Columns = list[tuple[str, str, bool, Optional[str]]]


def _validated_columns(final_schema: TableSchema) -> Columns:
    """(name, type, nullable, source_header) — source_header is the ORIGINAL
    spreadsheet column, which the reviewer may have renamed."""
    if not final_schema.columns:
        raise HTTPException(status_code=422, detail="Schema must have at least one column")
    try:
        columns = [
            (safe_identifier(column.name, "column name"), column.type, column.nullable, column.source_header)
            for column in final_schema.columns
        ]
    except UnsafeIdentifier as e:
        raise HTTPException(status_code=422, detail=str(e))
    for _, column_type, _, _ in columns:
        if column_type not in _TYPE_MAP:
            raise HTTPException(status_code=422, detail=f"Unsupported column type: {column_type!r}")
    return columns


async def _staged_rows(mongo: Any, data_id: str) -> tuple[list[str], list[list[Any]]]:
    staging = await mongo.get_client()["kb_structured_staging"].find_one({"data_id": data_id})
    if not staging or not staging.get("rows"):
        raise HTTPException(
            status_code=409,
            detail="Parsed data for this document is no longer available — re-run propose",
        )
    rows = staging["rows"]
    if len(rows) > _MAX_IMPORT_ROWS:
        raise HTTPException(
            status_code=422,
            detail=f"Import has {len(rows)} rows; the limit is {_MAX_IMPORT_ROWS}",
        )
    return staging.get("headers", []), rows


def _coerced_rows(rows: list[list[Any]], headers: list[str], columns: Columns,
                  tenant_id: str) -> tuple[list[dict[str, Any]], int]:
    """Parameter sets for executemany, plus how many values would not convert."""
    header_index = {header: index for index, header in enumerate(headers)}
    parameter_sets: list[dict[str, Any]] = []
    coercion_failures = 0
    for row in rows:
        params: dict[str, Any] = {"tenant_id": tenant_id}
        for position, (_, column_type, _, source_header) in enumerate(columns):
            source_index = header_index.get(source_header) if source_header else None
            raw_value = row[source_index] if source_index is not None and source_index < len(row) else None
            params[f"c{position}"] = _coerce(raw_value, column_type)
            if raw_value not in (None, "") and params[f"c{position}"] is None:
                coercion_failures += 1
        parameter_sets.append(params)
    return parameter_sets, coercion_failures


async def _unused_table_name(session: Any, preferred_name: str) -> str:
    """Collisions are only ever against other imports — kb_structured holds nothing else."""
    candidate = preferred_name
    suffix = 1
    while (await session.execute(
        text("SELECT 1 FROM information_schema.tables "
             "WHERE table_schema = 'kb_structured' AND table_name = :name"),
        {"name": candidate},
    )).first() is not None:
        suffix += 1
        candidate = f"{preferred_name}_{suffix}"
    return candidate


async def _create_import_table(session: Any, table_name: str, columns: Columns) -> None:
    qualified_name = f"kb_structured.{quote_identifier(table_name)}"
    column_definitions = ", ".join(
        f"{quote_identifier(name)} {_TYPE_MAP[column_type]}" + ("" if nullable else " NOT NULL")
        for name, column_type, nullable, _ in columns
    )
    await session.execute(text(
        f"CREATE TABLE {qualified_name} "
        f"(id BIGSERIAL PRIMARY KEY, tenant_id UUID NOT NULL, {column_definitions})"
    ))
    # Migration d4e5f6a7b8c9 covers this by default privileges; the guard keeps
    # an environment that has not run it yet from failing the approval.
    await session.execute(text(
        "DO $$ BEGIN "
        f"  GRANT SELECT ON {qualified_name} TO kb_structured_reader; "
        "EXCEPTION WHEN undefined_object THEN NULL; END $$;"
    ))
    if _ENFORCE_ROW_LEVEL_SECURITY:
        await session.execute(text(f"ALTER TABLE {qualified_name} ENABLE ROW LEVEL SECURITY"))
        await session.execute(text(f"ALTER TABLE {qualified_name} FORCE ROW LEVEL SECURITY"))
        await session.execute(text(
            f"CREATE POLICY tenant_isolation ON {qualified_name} "
            "USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid) "
            "WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
        ))


async def _insert_rows(session: Any, table_name: str, columns: Columns,
                       parameter_sets: list[dict[str, Any]]) -> None:
    insert_columns = ", ".join(quote_identifier(name) for name, _, _, _ in columns)
    placeholders = ", ".join(f":c{position}" for position in range(len(columns)))
    statement = text(
        f"INSERT INTO kb_structured.{quote_identifier(table_name)} (tenant_id, {insert_columns}) "
        f"VALUES (:tenant_id, {placeholders})"
    )
    # executemany per batch — one round-trip per row starved the pool.
    for start in range(0, len(parameter_sets), _INSERT_BATCH_SIZE):
        await session.execute(statement, parameter_sets[start:start + _INSERT_BATCH_SIZE])


async def _record_approval(session: Any, job: Any, data_id: str, table_name: str,
                           columns: Columns, final_schema: TableSchema, reviewer: str) -> None:
    qualified_name = f"kb_structured.{table_name}"
    session.add(KBTableORM(
        owner_id=uuid.UUID(data_id),
        table_name=qualified_name,
        description=(job.llm_proposed_schema or {}).get("description") if job.llm_proposed_schema else None,
        table_schema={"columns": [{"name": n, "type": t, "nullable": nl} for n, t, nl, _ in columns]},
        created_by=reviewer,
    ))
    await session.execute(
        sa_update(KBStructuredImportJobORM)
        .where(KBStructuredImportJobORM.job_id == job.job_id)
        .values(
            status="approved", human_edited_schema=final_schema.model_dump(),
            created_table_name=qualified_name, reviewed_by=reviewer,
            reviewed_at=datetime.datetime.now(datetime.timezone.utc),
        )
    )
    await session.execute(
        sa_update(KBDataORM).where(KBDataORM.data_id == uuid.UUID(data_id)).values(**with_new_version(current_tier=Tier.GOLD.value))
    )


@router.post("/{data_id}/approve")
async def approve_structured_table(
        data_id: str,
        body: ApproveRequest,
        request: Request,
        ctx: dict[str, Any] = Depends(require_perm("processLayer")),
) -> dict[str, Any]:
    postgres = svc(request, "postgres")

    await _fetch_kbdata_or_404(postgres, ctx["tenant_id"], ctx["role_id"], data_id)
    job = await _latest_job(postgres, data_id)
    if job is None or job.status != "proposed":
        raise HTTPException(status_code=409, detail="No proposal is ready for review on this document")

    final_schema = body.schema_
    columns = _validated_columns(final_schema)
    headers, rows = await _staged_rows(svc(request, "mongo"), data_id)
    parameter_sets, coercion_failures = _coerced_rows(rows, headers, columns, ctx["tenant_id"])

    # DDL, rows and the catalogue row share one transaction: the whole import
    # lands or none of it does.
    async with postgres.get_client() as session:
        preferred_name = safe_identifier(final_schema.table_name, "table_name")
        table_name = await _unused_table_name(session, preferred_name)
        await _create_import_table(session, table_name, columns)
        await _insert_rows(session, table_name, columns, parameter_sets)
        await _record_approval(session, job, data_id, table_name, columns, final_schema, ctx["user_name"])
        await session.commit()

    log.info("Structured table approved data_id=%s table=kb_structured.%s rows=%d", data_id, table_name, len(rows))
    return {"table_name": f"kb_structured.{table_name}", "row_count": len(rows), "coercion_warnings": coercion_failures}


@router.post("/{data_id}/reject")
async def reject_structured_table(
        data_id: str,
        request: Request,
        ctx: dict[str, Any] = Depends(require_perm("processLayer")),
) -> dict[str, Any]:
    postgres = svc(request, "postgres")
    await _fetch_kbdata_or_404(postgres, ctx["tenant_id"], ctx["role_id"], data_id)

    job = await _latest_job(postgres, data_id)
    if job is None:
        raise HTTPException(status_code=404, detail="No structured-import job for this document")

    now = datetime.datetime.now(datetime.timezone.utc)
    async with postgres.get_client() as session:
        await session.execute(
            sa_update(KBStructuredImportJobORM)
            .where(KBStructuredImportJobORM.job_id == job.job_id)
            .values(status="rejected", reviewed_by=ctx["user_name"], reviewed_at=now)
        )
        await session.commit()
    return {"status": "rejected"}


def _coerce(value: Any, col_type: str) -> Any:
    """Best-effort cast of a stringified spreadsheet cell to the approved
    column type. Never raises — a bad cell becomes NULL, counted by the
    caller as a coercion warning, rather than aborting the whole import."""
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return None
    try:
        if col_type == "integer":
            return int(str(value).replace(",", "").strip())
        if col_type == "numeric":
            return float(str(value).replace(",", "").strip())
        if col_type == "boolean":
            s = str(value).strip().lower()
            if s in ("true", "1", "yes", "y", "có", "co"):
                return True
            if s in ("false", "0", "no", "n", "không", "khong"):
                return False
            return None
        if col_type in ("date", "timestamp"):
            from dateutil import parser as _dtparser
            parsed = _dtparser.parse(str(value).strip(), dayfirst=False)
            return parsed.date() if col_type == "date" else parsed
        return str(value)
    except (ValueError, TypeError, OverflowError):
        return None
    except Exception:
        # dateutil raises its own ParserError subclassing ValueError in
        # recent versions but not always — never let a bad cell abort the
        # whole import regardless of the exact exception type it raises.
        return None
