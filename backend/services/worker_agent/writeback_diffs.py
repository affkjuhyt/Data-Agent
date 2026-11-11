"""The preview of a writeback (UC-06-04 step 2, SD-06 step 1): the `writeback.diff_requested`
handler.

POST /v1/writebacks records the request `draft` with an empty diff. Here the derived table (the
new content of the target) is read, the target table is read on the customer's source, both by
the target's key, and the request becomes `previewed` with diff_summary {inserts, updates,
deletes, unchanged, key, sample}; :submit then refuses any delete (BR-113). What cannot be
compared ends the request `failed`, diff_summary {"error": why} in Vietnamese.

The derived table is read only when the platform materialised it for this dataset in the
request's Data Space (materialised_here); any other dataset row fails with one generic reason.
Before either table is read, the requester's access is bound as the query executor binds it
(_governed): a table they may not read, or see only part of under a row policy or row scope,
refuses the preview, and every column a column or mask policy keeps from them is left out of the
sample, as is every column labelled personal data (catalog column_meta.pii_label); the api
strips the latter again when it answers.

Both reads are read-only: the target through the source's read account (secret_ref, never the
writeback account) in the drivers' read-only transaction (query.executor.driver_for), the derived
table in the Data Space workspace as the executor reads it (svc_query as the requester, READ
ONLY). Each read is capped at MAX_ROWS and the source's query timeout: a larger table fails the
preview rather than diffing part of it.

Executing the writeback (staging, totals check, the swap into the target, the snapshot for undo)
is not done anywhere yet: an approved request stays `approved`.
"""

from __future__ import annotations

import asyncio
import datetime
import decimal
import logging
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, text
from sqlglot import exp

from libs.dataagent_db import scoped_session
from libs.dataagent_db.models import catalog
from libs.dataagent_db.models import pipeline as pl
from libs.sql_guard import SqlRejected, check_read_only, dialect_of
from services.worker_agent.events import Event, Handler
from services.worker_agent.query.executor import ROLE as QUERY_ROLE
from services.worker_agent.query.executor import _plain, bound_sql, driver_for
from services.worker_agent.query.scope import active_row_scopes, default_schema, rewrite_row_scopes

logger = logging.getLogger(__name__)

EVENT_TYPE = "writeback.diff_requested"
ROLE = "svc_pipeline"
# Rows read on each side. A preview of a part of the table would show a diff that is not the
# write's, so a larger table fails, saying so.
MAX_ROWS = 50_000
SAMPLE_ROWS = 5
TIMEOUT_S = 120
_NUMERIC = {"integer", "decimal"}
# A person asks for a writeback (BR-25): the policies that apply are a person's (scope.actor_of).
CALLER_KIND = "ad_hoc"
NOT_MATERIALISED = (
    "Chỉ xem trước được từ bảng dẫn xuất mà nền tảng đã tạo trong Data Space này "
    "(kết quả pipeline hoặc bảng dẫn xuất đã lưu)."
)


class PreviewFailed(Exception):
    """Ends the request `failed` with this message, in Vietnamese, for the requester."""


@dataclass(frozen=True)
class Table:
    """A table to read, and where: `source` None is the Data Space workspace on the platform
    database."""

    source: catalog.Source | None
    schema: str | None
    name: str


@dataclass
class Diff:
    inserts: int
    updates: int
    deletes: int
    unchanged: int
    sample: dict[str, list[dict[str, Any]]]


async def preview(event: Event) -> None:
    request_id = uuid.UUID(str(event.payload["writeback_request_id"]))
    async with scoped_session(ROLE, event.org_id) as session:
        row = await session.get(pl.WritebackRequest, request_id)
        if row is None or row.status != "draft":
            return
        requester = row.requested_by
    try:
        summary = await _diff(event.org_id, requester, request_id)
        status = "previewed"
    except PreviewFailed as exc:
        summary, status = {"error": str(exc)}, "failed"
    except Exception:
        # Left `draft`, the request could never be submitted nor previewed again: end it.
        logger.exception("writeback %s: preview failed", request_id)
        summary, status = {"error": "Lỗi khi tính diff của lần ghi ngược."}, "failed"
    async with scoped_session(ROLE, event.org_id, requester) as session:
        row = await session.get(pl.WritebackRequest, request_id, with_for_update=True)
        if row is None or row.status != "draft":
            return
        # The deletes found are diff_summary's, which :submit refuses (BR-113); the `deletes`
        # column is held at 0 by a CHECK, so a request that would delete is never recorded as one.
        row.diff_summary, row.status = summary, status
        row.updated_at = datetime.datetime.now(datetime.UTC)
    logger.info("writeback %s: %s", request_id, status)


async def _diff(org_id: uuid.UUID, requester: uuid.UUID, request_id: uuid.UUID) -> dict[str, Any]:
    async with scoped_session(ROLE, org_id, requester) as session:
        row = await session.get(pl.WritebackRequest, request_id)
        assert row is not None
        space = row.data_space_id
        target_source = await session.scalar(
            select(catalog.Source).where(
                catalog.Source.id == row.source_id,
                catalog.Source.data_space_id == space,
                catalog.Source.deleted_at.is_(None),
            )
        )
        if target_source is None:
            raise PreviewFailed("Nguồn của bảng đích không còn.")
        derived = await session.scalar(
            select(catalog.Dataset).where(
                catalog.Dataset.id == row.from_dataset_id, catalog.Dataset.deleted_at.is_(None)
            )
        )
        if derived is None or not materialised_here(derived, space):
            raise PreviewFailed(NOT_MATERIALISED)
        target = await _target_dataset(session, target_source, row.target_table)
        if target is None or target.data_space_id != space:
            raise PreviewFailed(
                f"Bảng đích {row.target_table} chưa có trong danh mục của nguồn; "
                "quét lại nguồn rồi tạo lại yêu cầu."
            )
        target_columns = await _columns(session, target.id)
        derived_columns = await _columns(session, derived.id)
        key = [c.name for c in target_columns if c.is_key]
        pii = {c.name.lower() for c in [*target_columns, *derived_columns] if c.pii_label}
    if not key:
        raise PreviewFailed(
            f"Bảng đích {row.target_table} chưa có khoá chính trong danh mục; "
            "không so được từng dòng."
        )
    new_table = Table(None, derived.schema_name, derived.table_name)
    old_table = Table(target_source, target.schema_name, target.table_name)
    # Both tables as the requester may read them, before a row of either is read.
    hidden = pii | await _governed(org_id, requester, space, new_table)
    hidden |= await _governed(org_id, requester, space, old_table)
    new = await _read(org_id, requester, new_table, None)
    by_lower = {c.name.lower(): c for c in target_columns}
    extra = [name for name in new[0] if name.lower() not in by_lower]
    if extra:
        shown = [name for name in extra if name.lower() not in hidden]
        raise PreviewFailed(
            "Bảng dẫn xuất có cột không có ở bảng đích"
            + (f": {', '.join(shown)}." if shown else ".")
        )
    missing_key = [k for k in key if k.lower() not in {n.lower() for n in new[0]}]
    if missing_key:
        raise PreviewFailed(f"Bảng dẫn xuất thiếu cột khoá {', '.join(missing_key)}.")
    # The target's own spelling of each column the derived table writes.
    compared = [by_lower[name.lower()].name for name in new[0]]
    old = await _read(org_id, requester, old_table, compared)
    numeric = {c.name for c in target_columns if c.normalized_type in _NUMERIC}
    diff = compare(
        key,
        compared,
        [dict(zip(compared, r, strict=True)) for r in new[1]],
        [dict(zip(compared, r, strict=True)) for r in old[1]],
        numeric=numeric,
        hidden=hidden,
    )
    return {
        "inserts": diff.inserts,
        "updates": diff.updates,
        "deletes": diff.deletes,
        "unchanged": diff.unchanged,
        "key": [k for k in key if k.lower() not in hidden],
        "sample": diff.sample,
    }


def materialised_here(dataset: catalog.Dataset, space: uuid.UUID) -> bool:
    """The derived table the platform built for this very dataset, in this Data Space. A dataset
    row names any schema.table (POST /v1/derived-tables takes the name from its body), so the
    name alone is never trusted: pipelines and saved derived tables (pipelines.physical_name,
    derived.py) write workspace.p_<stem>_<the dataset's own id>, which no other row can claim."""
    return (
        dataset.origin == "derived"
        and dataset.data_space_id == space
        and dataset.source_id is None
        and dataset.schema_name == "workspace"
        and bool(dataset.enabled)
        and re.fullmatch(rf"p_[a-z0-9_]{{1,28}}_{dataset.id.hex}", dataset.table_name) is not None
    )


async def _governed(
    org_id: uuid.UUID, requester: uuid.UUID, space: uuid.UUID, table: Table
) -> set[str]:
    """The requester's access to `table`, through the query executor's own scope and policy
    binding (executor.bound_sql): a table they may not read refuses the preview, and so does one
    a row policy or a row scope shows them only part of (the rest would read as deletes). Returns
    the lower-case columns a column or mask policy keeps from them, left out of the sample."""
    from services.worker_agent.questions import _internal_workspace_source

    source = table.source or _internal_workspace_source(space, org_id)
    dialect = dialect_of(source.kind)
    plain = _select(table, None, dialect)
    async with scoped_session(QUERY_ROLE, org_id, requester) as session:
        try:
            bound = await bound_sql(
                session,
                sql=plain,
                source=source,
                data_space_id=space,
                principal_id=requester,
                caller_kind=CALLER_KIND,
            )
            scopes = await active_row_scopes(session, data_space_id=space, principal_id=requester)
        except SqlRejected as exc:
            logger.info(
                "writeback preview: %s is out of the requester's reach: %s", table.name, exc
            )
            raise PreviewFailed(f"Bạn không được đọc bảng {table.name}.") from exc
    if any(p.kind == "row" for p in bound.policies) or (
        rewrite_row_scopes(plain, dialect=dialect, scopes=scopes) != plain
    ):
        raise PreviewFailed(
            f"Bạn chỉ thấy một phần bảng {table.name} (chính sách giới hạn dòng); "
            "xem trước cần đọc được cả bảng."
        )
    return {
        str(c).lower()
        for p in bound.policies
        if p.kind in ("column", "mask")
        for c in p.columns or []
    }


async def _target_dataset(
    session: Any, source: catalog.Source, target_table: str
) -> catalog.Dataset | None:
    """The catalogued table `target_table` names on `source`; an unqualified name is in the
    schema the source's queries resolve it to (scope.default_schema)."""
    parts = target_table.split(".")
    schema = parts[-2] if len(parts) >= 2 else default_schema(source)
    query = select(catalog.Dataset).where(
        catalog.Dataset.source_id == source.id,
        catalog.Dataset.deleted_at.is_(None),
        catalog.Dataset.table_name == parts[-1],
    )
    if schema is not None:
        query = query.where(catalog.Dataset.schema_name == schema)
    found = list((await session.scalars(query)).all())
    return found[0] if len(found) == 1 else None


async def _columns(session: Any, dataset_id: uuid.UUID) -> list[catalog.ColumnMeta]:
    return list(
        (
            await session.scalars(
                select(catalog.ColumnMeta)
                .where(
                    catalog.ColumnMeta.dataset_id == dataset_id,
                    catalog.ColumnMeta.removed_in_version.is_(None),
                )
                .order_by(catalog.ColumnMeta.ordinal)
            )
        ).all()
    )


def _select(table: Table, columns: Sequence[str] | None, dialect: str) -> str:
    projection = [exp.column(c, quoted=True) for c in columns] if columns else [exp.Star()]
    query = (
        exp.select(*projection)
        .from_(exp.table_(table.name, db=table.schema, quoted=True))
        .limit(MAX_ROWS + 1)
    )
    return check_read_only(query.sql(dialect=dialect), dialect).sql(dialect=dialect)


async def _read(
    org_id: uuid.UUID, requester: uuid.UUID, table: Table, columns: Sequence[str] | None
) -> tuple[list[str], list[list[Any]]]:
    """Column names and rows of `table`, read-only, at most MAX_ROWS."""
    if table.source is None:
        sql = _select(table, columns, "postgres")
        async with scoped_session(QUERY_ROLE, org_id, requester) as session:
            await session.execute(text("SET TRANSACTION READ ONLY"))
            await session.execute(text(f"SET LOCAL statement_timeout = {TIMEOUT_S * 1000}"))
            result = await session.execute(text(sql))
            names = list(map(str, result.keys()))
            rows = [[_plain(v) for v in r] for r in result.fetchall()]
    else:
        from services.worker_agent.questions import secrets

        source = table.source
        timeout = int(source.query_timeout_s or TIMEOUT_S)
        sql = _select(table, columns, dialect_of(source.kind))
        try:
            credentials = await secrets().read(org_id, source.secret_ref)
            described, rows = await asyncio.wait_for(
                driver_for(source.kind)(
                    source, credentials.user, credentials.password, sql, timeout
                ),
                timeout=timeout + 5,
            )
        except TimeoutError as exc:
            raise PreviewFailed(f"Đọc bảng {table.name} quá {timeout} giây.") from exc
        except Exception as exc:
            # The driver's message may name the host or the login: the log keeps it, the
            # requester reads which table could not be read.
            logger.warning("writeback preview: reading %s failed: %s", table.name, exc)
            raise PreviewFailed(f"Không đọc được bảng {table.name} trên nguồn.") from exc
        names = [str(c["name"]) for c in described]
    if len(rows) > MAX_ROWS:
        limit = f"{MAX_ROWS:_}".replace("_", ".")
        raise PreviewFailed(
            f"Bảng {table.name} có hơn {limit} dòng, quá cỡ xem trước của một lần ghi ngược."
        )
    return names, rows


def _norm(value: Any, numeric: bool) -> Any:
    """A value as the two sides compare it: a number by its value (a source sends 1500.00 where
    the workspace has 1500), anything else as text (dates come as ISO text from both)."""
    if value is None:
        return None
    if numeric:
        try:
            return decimal.Decimal(str(value))
        except decimal.InvalidOperation:
            return str(value)
    return str(value)


def compare(
    key: Sequence[str],
    columns: Sequence[str],
    new: Sequence[dict[str, Any]],
    old: Sequence[dict[str, Any]],
    *,
    numeric: set[str],
    hidden: set[str],
) -> Diff:
    """The derived rows (`new`, the target's content after the write) against the target's rows
    (`old`), matched by `key`. A key twice on either side fails: the write could not say which
    row it means. `hidden` columns (lower-case) never reach the sample."""

    def key_of(row: dict[str, Any], side: str) -> tuple[Any, ...]:
        found = tuple(_norm(row[k], k in numeric) for k in key)
        if any(v is None for v in found):
            raise PreviewFailed(f"Bảng {side} có dòng thiếu giá trị khoá.")
        return found

    def shown(row: dict[str, Any], names: Sequence[str]) -> dict[str, Any]:
        return {n: row[n] for n in names if n.lower() not in hidden}

    before: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in old:
        k = key_of(row, "đích")
        if k in before:
            raise PreviewFailed("Khoá của bảng đích bị trùng; không so được từng dòng.")
        before[k] = row
    seen: set[tuple[Any, ...]] = set()
    sample: dict[str, list[dict[str, Any]]] = {"inserts": [], "updates": [], "deletes": []}
    inserts = updates = unchanged = 0
    for row in new:
        k = key_of(row, "dẫn xuất")
        if k in seen:
            raise PreviewFailed("Khoá của bảng dẫn xuất bị trùng; không so được từng dòng.")
        seen.add(k)
        was = before.get(k)
        if was is None:
            inserts += 1
            if len(sample["inserts"]) < SAMPLE_ROWS:
                sample["inserts"].append({"key": shown(row, key), "after": shown(row, columns)})
            continue
        changed = [
            c
            for c in columns
            if c not in key and _norm(row[c], c in numeric) != _norm(was[c], c in numeric)
        ]
        if not changed:
            unchanged += 1
            continue
        updates += 1
        if len(sample["updates"]) < SAMPLE_ROWS:
            sample["updates"].append(
                {
                    "key": shown(row, key),
                    "before": shown(was, changed),
                    "after": shown(row, changed),
                }
            )
    gone = [k for k in before if k not in seen]
    for k in gone[:SAMPLE_ROWS]:
        sample["deletes"].append(
            {"key": shown(before[k], key), "before": shown(before[k], columns)}
        )
    return Diff(inserts, updates, len(gone), unchanged, sample)


HANDLERS: dict[str, Handler] = {EVENT_TYPE: preview}
