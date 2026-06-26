"""Agent catalog — loads the executor agents (name + description) from the Postgres
`Agents` table via the shared postgres connector. These are fed to the planner so the
model sets each task's `type` to a real registered agent. Best-effort: with no Postgres
configured the catalog is empty and task `type` stays free-form.
"""

from __future__ import annotations

import logging

from config import AGENT_TENANT_ID, POSTGRES_DSN
from services.database_connector.postgres_connector import client

logger = logging.getLogger(__name__)
_connected = False


async def init_agents_db() -> None:
    global _connected
    if not POSTGRES_DSN:
        logger.info("agents | POSTGRES_DSN not set — task 'type' stays free-form")
        return
    client.set_url(POSTGRES_DSN)
    try:
        await client.open()
        _connected = True
        logger.info("agents | postgres connected")
    except Exception as e:  # noqa: BLE001 — planner still serves without the catalog
        logger.warning("agents | postgres unavailable: %s", e)
        _connected = False


async def close_agents_db() -> None:
    if _connected:
        await client.close()


# Agents table -> [{name, description}] (live agents only). Query logic lives in the connector.
async def load_agents(limit: int = 200) -> list[dict]:
    if not _connected:
        return []
    from basemodel.services_databaseconnector.postgres_model import (
        FilterOperator, ReadJoinRequest, SelectedColumn, WhereFilter,
    )
    res = await client.read(ReadJoinRequest(
        joins_table=["Agents"],
        selected_columns=[
            SelectedColumn(table_name="Agents", column_name="name"),
            SelectedColumn(table_name="Agents", column_name="description"),
        ],
        filters=[WhereFilter(table_name="Agents", column_name="deleted_at",
                             value=None, operator=FilterOperator.EQ)],  # deleted_at IS NULL
        tenant_id=AGENT_TENANT_ID or None,
        limit=limit,
    ))
    if res.code != 200 or not res.data:
        return []
    out, seen = [], set()
    for row in res.data:
        name = (row.get("name") or "").strip()
        if name and name not in seen:
            seen.add(name)
            out.append({"name": name, "description": (row.get("description") or "").strip()})
    logger.info("agents | loaded %d agents", len(out))
    return out
