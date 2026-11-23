"""Who a query runs as on its source (AE-17, ASK_ENGINE_SPEC §6.1, F-29).

A source reached with its service account knows nothing of the asker: the policies of the Data
Space are written into the SQL (scope.py), the source's own grants and row security see one login.
catalog.source.role_mapping maps an asker to a role of a PostgreSQL source:

    {"users": {"<user id>": "<role>"}, "groups": {"<group id>": "<role>"}}

The asker's own entry wins; else the role of the groups they belong to. Two groups that name two
different roles are not settled by picking one: the query is refused, as an ambiguous grant should
be. An asker the mapping does not name runs as the service account, as before the mapping
(fallback: the policy rewrite). The service account must be a member of every mapped role
(GRANT <role> TO <login>), or the source refuses the SET ROLE.

The role is set with SET LOCAL ROLE inside the read-only transaction (executor._run_postgres), so
it ends with the transaction. query.query_run.executed_as records it for audit.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db.models import catalog
from libs.dataagent_db.models import org as org_models
from libs.sql_guard import SqlRejected

# A PostgreSQL role as an unquoted identifier: what an admin can type and nothing it could inject.
ROLE_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
MAPPED_KINDS = frozenset({"postgresql"})


def mapping_of(source: catalog.Source) -> tuple[dict[str, str], dict[str, str]]:
    """The source's users and groups maps, each id to its role; empty when none is set."""
    raw: Any = source.role_mapping if isinstance(source.role_mapping, dict) else {}

    def part(key: str) -> dict[str, str]:
        found = raw.get(key)
        return {str(k): str(v) for k, v in found.items()} if isinstance(found, dict) else {}

    return part("users"), part("groups")


def mapped_roles(source: catalog.Source) -> set[str]:
    users, groups = mapping_of(source)
    return set(users.values()) | set(groups.values())


async def role_for(
    session: AsyncSession, source: catalog.Source, principal_id: uuid.UUID
) -> str | None:
    """The role `principal_id`'s query runs as on `source`, or None for the service account."""
    users, groups = mapping_of(source)
    if not users and not groups:
        return None
    if source.kind not in MAPPED_KINDS:
        raise SqlRejected(f"a role mapping is only applied on PostgreSQL, not on {source.kind}")
    role = users.get(str(principal_id))
    if role is None and groups:
        member_of = (
            await session.scalars(
                select(org_models.UserGroupMember.group_id).where(
                    org_models.UserGroupMember.user_id == principal_id
                )
            )
        ).all()
        roles = {groups[str(g)] for g in member_of if str(g) in groups}
        if len(roles) > 1:
            raise SqlRejected(
                "the asker's groups map to different roles on this source; the mapping must name"
                " one for them"
            )
        role = next(iter(roles), None)
    if role is not None and not ROLE_NAME.match(role):
        raise SqlRejected("the role mapped for the asker is not a valid role name")
    return role
