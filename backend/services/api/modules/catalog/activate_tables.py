"""A source's scanned tables put into its Data Space by the route that does it on the screen.

Only an enabled table enters the context and can be queried (BR-102): the exploratory SQL path
(worker_agent/sqlgen/space.load_space) shows the model the enabled tables only. A Data Space
registered by a seed (DS-TLC, deploy/docker/sql/v3-tlc-seed.sql) has its tables scanned but none
enabled, so asking it found no table. This calls `activate_source` itself as the named user, so
the checks hold as on the screen (each personal-data column's masking confirmed, the owner an
active user, the caller holding the route's A2 capability, admin_data) and source.activated is announced. The source's
scan and profile schedules are kept as they are; a table not named is disabled, as the route does.

    python -m services.api.modules.catalog.activate_tables --org <id> --space <code> \\
        --as <user> [--owner <user>] [--table schema.table …]

With no --table, every table the source's scan found in the Data Space. (`make tlc-tables`.)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db import scoped_session
from libs.dataagent_db.models import catalog
from libs.dataagent_db.models import org as org_models
from services.api.catalogue import allowed
from services.api.kernel.auth import Principal
from services.api.modules.catalog.routes import ROLE, activate_source
from services.api.modules.catalog.schemas import ActivateRequest, ActivateTable
from services.api.modules.org import capabilities_of


async def _user(session: AsyncSession, org_id: uuid.UUID, username: str) -> org_models.AppUser:
    user = await session.scalar(
        select(org_models.AppUser).where(
            org_models.AppUser.org_id == org_id, org_models.AppUser.username == username
        )
    )
    if user is None:
        raise SystemExit(f"no user {username} in organisation {org_id}")
    return user


async def run(args: argparse.Namespace) -> dict[str, Any]:
    org_id = uuid.UUID(args.org)
    async with scoped_session(ROLE, org_id, None) as session:
        space = await session.scalar(
            select(org_models.DataSpace).where(
                org_models.DataSpace.org_id == org_id, org_models.DataSpace.code == args.space
            )
        )
        if space is None:
            raise SystemExit(f"no Data Space {args.space} in organisation {org_id}")
        user = await _user(session, org_id, args.username)
        owner = await _user(session, org_id, args.owner or args.username)
        sources = (
            await session.scalars(
                select(catalog.Source).where(
                    catalog.Source.org_id == org_id,
                    catalog.Source.data_space_id == space.id,
                    catalog.Source.deleted_at.is_(None),
                )
            )
        ).all()
        if len(sources) != 1:
            raise SystemExit(f"{args.space} has {len(sources)} sources; this takes exactly one")
        source = sources[0]
        found = (
            await session.execute(
                select(catalog.Dataset.schema_name, catalog.Dataset.table_name).where(
                    catalog.Dataset.source_id == source.id,
                    catalog.Dataset.data_space_id == space.id,
                    catalog.Dataset.deleted_at.is_(None),
                )
            )
        ).all()
    scanned = sorted(f"{s}.{t}" for s, t in found)
    names = args.tables or scanned
    unknown = sorted(set(names) - set(scanned))
    if unknown:
        raise SystemExit(f"not scanned in {args.space}: {', '.join(unknown)}")
    held = frozenset(await capabilities_of(org_id, user.id, space.id) or ())
    # The route's A2 gate (admin_data) is a dependency of its HTTP request, which a direct call
    # does not make: held here, or this would be a way round it.
    if not allowed("POST", "/v1/sources/{id}:activate", held):
        raise PermissionError(f"{args.username} does not administer the data of {args.space}")
    principal = Principal(
        user_id=user.id,
        org_id=org_id,
        external_id=user.external_id or "",
        capabilities=held,
        data_space_id=space.id,
    )
    view = await activate_source(
        ActivateRequest(
            tables=[
                ActivateTable(name=n, data_space_id=space.id, owner_id=owner.id) for n in names
            ],
            # "" keeps no schedule: a seeded source is scanned when someone asks for it.
            schema_scan=source.scan_cron or "",
            profiling=source.profile_cron or "",
        ),
        source.id,
        principal,
        space.id,
    )
    return {"source_id": str(view.id), "status": view.status, "enabled": names}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--org", required=True)
    parser.add_argument("--space", required=True)
    parser.add_argument("--as", dest="username", required=True)
    parser.add_argument("--owner", help="the tables' steward (default: the --as user)")
    parser.add_argument("--table", dest="tables", action="append", default=[])
    print(json.dumps(asyncio.run(run(parser.parse_args(argv))), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
