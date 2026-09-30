"""Structured-table ingestion mode: propose a normalized schema from an
uploaded spreadsheet's largest detected table, for human review before any
real Postgres table gets created (see backend/services/kb_backend/router/
knowledge/structured_table.py, which owns the review/approve/reject API and
the actual CREATE TABLE).

v1 scope, documented as a known limitation in the feature plan: one proposed
table per file — the largest sheet/table Docling detects. A multi-sheet
spreadsheet with several independent tables needs a follow-up (a
table-selection review step), not built here."""
from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any, Optional, cast

from sqlalchemy import update as sa_update

from libs.basemodel.services_databaseconnector.postgres_orm.knowledge_base_orm import (
    KBStructuredImportJobORM,
)
from libs.database_connector.minio_connector import MinIOClient
from libs.database_connector.mongo_connector import MongoClient
from libs.database_connector.postgres_connector import PostgresClient
from libs.ingestion import config as cfg
from libs.ingestion import llm_client
from libs.ingestion.silver.persistence import _load_kb_data
from libs.ingestion.storage_boundary import is_v2_structured_document
from libs.shared.storage_ownership import V2_STRUCTURED_SOURCE_ERROR

log = logging.getLogger(__name__)

_JSON_FENCE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)
_JSON_OBJ = re.compile(r"\{[\s\S]*\}", re.DOTALL)
_SAMPLE_ROWS_FOR_LLM = 15
_SAMPLE_ROWS_FOR_REVIEW = 20

_SYSTEM_PROMPT = (
    "You are a data engineer. Given a spreadsheet's column headers and a small "
    "sample of rows, propose a normalized table schema for storing this data in "
    "Postgres. Pick a short snake_case table_name. For each column, pick a "
    "snake_case name (even if the source header is messy, e.g. a merged "
    "multi-level header), a type from exactly this set: "
    "text, integer, numeric, boolean, date, timestamp — and whether it should "
    "be nullable. Set source_header to the EXACT original header text this "
    "column comes from, unchanged, so the caller can map values back to it. "
    "If a header looks like a number, date, or a wide-format year, choose the "
    "type that actually matches the sample values, not just the header text."
)


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "_", name).strip("_").lower()
    return s or "imported_table"


async def _update_job(postgres: PostgresClient, job_id: str, **values: Any) -> None:
    async with postgres.get_client() as session:
        await session.execute(
            sa_update(KBStructuredImportJobORM)
            .where(KBStructuredImportJobORM.job_id == uuid.UUID(job_id))
            .values(**values)
        )
        await session.commit()


def _fallback_proposal(filename: str, headers: list[str]) -> dict[str, Any]:
    return {
        "table_name": _slugify(filename.rsplit(".", 1)[0] if "." in filename else filename),
        "description": f"Imported from {filename} (schema proposal unavailable — literal headers used)",
        "columns": [
            {"name": _slugify(h) or f"column_{i}", "type": "text", "nullable": True, "source_header": h}
            for i, h in enumerate(headers)
        ],
    }


def _extract_json(raw: str) -> Optional[dict[str, Any]]:
    match = _JSON_FENCE.search(raw)
    cleaned = match.group(1).strip() if match else raw.strip()
    try:
        return cast(dict[str, Any], json.loads(cleaned))
    except json.JSONDecodeError:
        obj_match = _JSON_OBJ.search(cleaned)
        if obj_match:
            try:
                return cast(dict[str, Any], json.loads(obj_match.group(0)))
            except json.JSONDecodeError:
                return None
        return None


def _validate_proposal(parsed: dict[str, Any] | None, headers: list[str]) -> dict[str, Any] | None:
    """Defensive field-by-field check — never trust the LLM's shape blindly,
    mirroring platform_workers' llm_describe.py precedent. Returns None (the
    caller falls back to the deterministic proposal) if anything is off."""
    if not isinstance(parsed, dict):
        return None
    table_name = parsed.get("table_name")
    columns = parsed.get("columns")
    if not isinstance(table_name, str) or not table_name.strip() or not isinstance(columns, list) or not columns:
        return None
    valid_types = {"text", "integer", "numeric", "boolean", "date", "timestamp"}
    header_set = set(headers)
    cleaned_columns = []
    for c in columns:
        if not isinstance(c, dict):
            continue
        name, ctype, source = c.get("name"), c.get("type"), c.get("source_header")
        if not isinstance(name, str) or not name.strip() or ctype not in valid_types:
            continue
        cleaned_columns.append({
            "name": _slugify(name),
            "type": ctype,
            "nullable": bool(c.get("nullable", True)),
            "source_header": source if source in header_set else "",
        })
    if not cleaned_columns:
        return None
    return {
        "table_name": _slugify(table_name),
        "description": str(parsed.get("description") or "")[:1000],
        "columns": cleaned_columns,
    }


async def propose_structured_table(
    minio: MinIOClient,
    postgres: PostgresClient,
    docling: Any,
    mongo: MongoClient,
    data_id: str,
    job_id: str,
    tenant_id: str,
) -> None:
    if await is_v2_structured_document(postgres, data_id, tenant_id):
        # kb_backend already answers 409; this catches a replayed or stale
        # event. A V2 structured source's numbers never become a
        # `kb_structured` table (NOP-163) — fail the job so the review screen
        # shows why instead of a proposal that stays "proposing".
        log.info("structured-propose | skipped data_id=%s: V2 structured source", data_id)
        await _update_job(
            postgres, job_id, status="failed",
            error_message=f"Source has an extraction profile; its numbers use the structured pipeline "
                          f"[{V2_STRUCTURED_SOURCE_ERROR}]",
        )
        return
    try:
        kb_data = await _load_kb_data(postgres, tenant_id=tenant_id, data_id=data_id)
        path: str = kb_data["path"]
        extension: str = kb_data["extension"]
        filename = kb_data.get("name") or path.rsplit("/", 1)[-1]

        result = await docling.parse(bucket=cfg.MINIO_BUCKET, object_key=path, extension=extension)
        table_chunks = [c for c in result.chunks if c.table_involved and c.table]
        if not table_chunks:
            await _update_job(
                postgres, job_id, status="failed",
                error_message="No table detected in this file by the document parser.",
            )
            return

        largest = max(table_chunks, key=lambda c: len(c.table.data.get("rows", [])))
        headers: list[str] = largest.table.data.get("headers", [])
        rows: list[list[Any]] = largest.table.data.get("rows", [])

        await mongo.get_client()["kb_structured_staging"].replace_one(
            {"data_id": data_id},
            {"data_id": data_id, "job_id": job_id, "tenant_id": tenant_id, "headers": headers, "rows": rows},
            upsert=True,
        )

        sample = rows[:_SAMPLE_ROWS_FOR_LLM]
        prompt = (
            f"Headers: {json.dumps(headers, ensure_ascii=False)}\n\n"
            f"Sample rows ({len(sample)} of {len(rows)} total):\n"
            f"{json.dumps(sample, ensure_ascii=False)}"
        )

        proposal: dict[str, Any] | None = None
        try:
            raw = await llm_client.generate_json(
                prompt, system_prompt=_SYSTEM_PROMPT, system_type="table-schema-proposal",
                max_tokens=1024, temperature=0.1,
            )
            proposal = _validate_proposal(_extract_json(raw), headers)
        except Exception as e:
            log.warning("structured-propose | LLM call failed data_id=%s: %s", data_id, e)

        if proposal is None:
            proposal = _fallback_proposal(filename, headers)

        sample_rows_for_review = [
            dict(zip(headers, r)) for r in rows[:_SAMPLE_ROWS_FOR_REVIEW]
        ]

        await _update_job(
            postgres, job_id, status="proposed",
            llm_proposed_schema=proposal, sample_rows={"rows": sample_rows_for_review},
        )
        log.info("structured-propose | proposed data_id=%s job=%s columns=%d rows=%d",
                  data_id, job_id, len(proposal["columns"]), len(rows))
    except Exception as exc:
        log.exception("structured-propose | failed data_id=%s job=%s: %s", data_id, job_id, exc)
        try:
            await _update_job(postgres, job_id, status="failed", error_message=str(exc)[:500])
        except Exception:
            pass
