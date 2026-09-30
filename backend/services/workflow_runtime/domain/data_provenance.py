"""Governance facts for the tables one answer was built from.

The read half of the crawler's governance write (schema-crawler-worker/
governance.py + main.py). Given the SQL that actually ran, it returns what the
catalog knows about the tables in it — owner, classification, lineage, and how
old the data is — so the answer can carry an "as of" and, when the data is past
its agreed window, say so.

Reads Postgres rather than the Qdrant payload on purpose. The payload is only
refreshed when a table is crawled *and* retrieved, and the semantic-layer path
never retrieves at all; `SchemaCatalog` is the row the crawler writes every
pass, so it is the current one. It is also the only source available when the
question was answered from a compiled metric rather than a vector search.

Not cached. One indexed SELECT per answered question is invisible next to the
model call in the same turn, and caching the freshness data would add a second,
invisible staleness on top of the one this module exists to report.
"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any
from uuid import UUID

from libs.shared import audit
from libs.shared.freshness import Provenance, TableGovernance, summarize

logger = logging.getLogger(__name__)

# Tenant scoping is RLS (the session sets app.tenant_id — backend/libs/database_connector
# get_client), same as semantic_layer.py; warehouse_id is an explicit predicate
# because one tenant can register several warehouses and the tables in a
# question belong to exactly one of them.
#
# lower(table_name) because extract_tables() lowercases what it finds while
# Snowflake stores identifiers upper-cased — matching literally would return
# nothing for every Snowflake warehouse.
_GOVERNANCE_SQL = """
SELECT schema_name, table_name, owner, classification, upstream, freshness_column,
       data_as_of, freshness_checked_at, freshness_sla_seconds, last_crawled_at
FROM "SchemaCatalog"
WHERE warehouse_id = :warehouse_id
  AND lower(table_name) IN :names
"""


def _bare_names(tables: Sequence[str]) -> dict[str, str]:
    """{bare lowercase table name: as written in the SQL}.

    A reference may arrive qualified (`public.orders`, `db.public.orders`); the
    catalog keys on the table name with the schema in its own column, so the
    last segment is what matches.
    """
    out: dict[str, str] = {}
    for name in tables:
        bare = name.rsplit(".", 1)[-1].strip().lower()
        if bare:
            out.setdefault(bare, name)
    return out


async def governance_for_tables(
    tenant_id: str, warehouse_id: str, tables: Sequence[str],
) -> list[TableGovernance]:
    """Catalog governance rows for these tables. Empty list on anything missing.

    Empty is the honest answer for an uncrawled or unlinked warehouse: with no
    catalog entry there is no evidence about freshness, and `summarize` reports
    that as UNKNOWN. Inventing a row would turn "we never checked" into "it's
    fine".
    """
    if not (tenant_id and warehouse_id and tables):
        return []

    lookup = _bare_names(tables)
    if not lookup:
        return []

    try:
        from sqlalchemy import bindparam, text

        from libs.database_connector.postgres_connector import client

        if not client.is_connected():
            return []

        stmt = text(_GOVERNANCE_SQL).bindparams(bindparam("names", expanding=True))
        async with client.get_client(tenant_id=tenant_id) as session:
            rows = (
                await session.execute(
                    stmt, {"warehouse_id": UUID(str(warehouse_id)), "names": list(lookup)},
                )
            ).fetchall()
    except Exception as e:
        # Governance is annotation on an answer that has already been computed.
        # Losing it must degrade the disclosure, never the answer.
        logger.warning("provenance | governance lookup failed warehouse=%s: %s", warehouse_id, e)
        return []

    return [TableGovernance.from_row(row._mapping) for row in rows]


async def provenance_for_sql(
    tenant_id: str, conn_cfg: dict[str, Any], sql: str,
) -> dict[str, Any] | None:
    """Provenance for one executed statement, or None when there is nothing to say.

    Table extraction is `libs.shared.audit.extract_tables` — the same heuristic
    the audit trail annotates with, reused rather than reimplemented so the two
    records of one query name the same tables. Its known limitation carries over
    unchanged: it reads FROM/JOIN, not a parse tree, so a table reached through
    a view or a CTE alias is not seen. That direction is safe — a missed table
    can only leave provenance narrower than reality, and a table nobody can name
    cannot be reported stale either way.

    Requires the connection to be linked to a crawled warehouse
    (`catalog_warehouse_id`). Without that link there is no way to tell which
    catalog entries describe this database, and freshness read off the wrong
    warehouse would be worse than none.
    """
    warehouse_id = conn_cfg.get("catalog_warehouse_id") or ""
    if not warehouse_id:
        return None

    tables = audit.extract_tables(sql)
    if not tables:
        return None

    governance = await governance_for_tables(tenant_id, warehouse_id, tables)
    if not governance:
        return None

    prov: Provenance = summarize(governance)
    logger.info(
        "provenance | warehouse=%s tables=%d status=%s as_of=%s",
        warehouse_id, len(governance), prov.status, prov.as_of,
    )
    return prov.to_dict()
