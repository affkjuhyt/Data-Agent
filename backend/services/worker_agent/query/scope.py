"""What a query may read, checked before the source is touched: tables of the Data Space only.

Every table the SQL reads must be a dataset of the run's Data Space on the run's source, enabled
(BR-102), not deleted, and not someone else's personal table (BR-111). A table the catalogue does
not know is refused: the source login may see more than the Data Space does, and the Data Space
is the boundary (invariant 13, permissions before generation).

Access policies (UC-11-02, BR-45) are not applied yet: the row, column and mask rewrite needs the
asker's attributes and is its own piece. Until it exists a query that reads a dataset with an
active policy is refused, never run unfiltered.
"""

from __future__ import annotations

import uuid

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db.models import catalog, governance
from services.worker_agent.query.guard import SqlRejected


class OutOfScope(SqlRejected):
    """The query reads a table outside the Data Space, or one an access policy covers."""


def default_schema(source: catalog.Source) -> str | None:
    """The one schema an unqualified name resolves to, None when names must be qualified.

    The executor makes the source agree: on PostgreSQL it sets search_path to exactly this schema
    (else the login's own default, "$user" first, would decide); on MySQL it is the database the
    connection USEs. A SQL Server login has its own default schema the worker cannot see, so
    names there are always written with their schema."""
    options = source.conn_options if isinstance(source.conn_options, dict) else {}
    configured = str(options.get("schema") or "") or None
    if source.kind == "postgresql":
        return configured or "public"
    if source.kind == "mysql":
        return configured or source.database_name
    return None


def resolve(tables: set[tuple[str | None, str]], source: catalog.Source) -> set[tuple[str, str]]:
    schema = default_schema(source)
    resolved: set[tuple[str, str]] = set()
    for table_schema, name in tables:
        if table_schema is None and schema is None:
            raise OutOfScope(f"write the schema of table {name}")
        resolved.add((table_schema or schema or "", name))
    return resolved


def _same(a: str, b: str, source: catalog.Source) -> bool:
    return a == b if source.kind == "postgresql" else a.lower() == b.lower()


async def datasets_read(
    session: AsyncSession,
    *,
    data_space_id: uuid.UUID,
    principal_id: uuid.UUID,
    source: catalog.Source,
    tables: set[tuple[str | None, str]],
) -> list[catalog.Dataset]:
    """The datasets the query reads; OutOfScope when one table is not a readable dataset or an
    active access policy covers one."""
    wanted = resolve(tables, source)
    if not wanted:
        return []
    ds = catalog.Dataset
    local = source.secret_ref == "internal://workspace"
    candidates = (
        await session.scalars(
            select(ds).where(
                ds.data_space_id == data_space_id,
                ds.source_id.is_(None) if local else ds.source_id == source.id,
                ds.origin == "upload" if local else ds.origin != "upload",
                ds.deleted_at.is_(None),
                ds.enabled.is_(True),
                or_(ds.scope != "personal", ds.created_by == principal_id),
            )
        )
    ).all()
    found: list[catalog.Dataset] = []
    for schema, name in sorted(wanted):
        match = [
            d
            for d in candidates
            if _same(d.schema_name, schema, source) and _same(d.table_name, name, source)
        ]
        if not match:
            raise OutOfScope(f"table {schema}.{name} is not a dataset of this Data Space")
        found += match
    policy = await session.scalar(
        select(governance.AccessPolicy).where(
            governance.AccessPolicy.dataset_id.in_([d.id for d in found]),
            governance.AccessPolicy.status == "active",
        )
    )
    if policy is not None:
        raise OutOfScope(
            f"access policy {policy.name} covers a table of this query; policies are not applied "
            "by the query worker yet, so it does not run (BR-45)"
        )
    return found
