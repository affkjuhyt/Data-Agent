"""CLI for the ANKT source connectors.

A Python CLI under the service that owns it, per CLAUDE.md section 5, and what
a K8s CronJob will invoke once the schedule is wired. It drives the SAME
registry and the SAME runner the API does — the screen and the schedule are two
front doors onto one implementation, not two implementations.

    python -m services.ankt_connectors seed --dsn ... --tenant ...
    python -m services.ankt_connectors list --dsn ... --tenant ...
    python -m services.ankt_connectors run  --dsn ... --tenant ... [--source CODE]
    python -m services.ankt_connectors promote --dsn ... --tenant ...
"""
from __future__ import annotations

import argparse
import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from services.ankt_connectors.registry import list_sources, seed
from services.ankt_connectors.runner import run_source


@asynccontextmanager
async def _session(dsn: str, tenant_id: uuid.UUID) -> AsyncIterator[Any]:
    """The same PostgresClient the services use, not a raw driver connection.

    Two clients would be two behaviours: the API once shipped with asyncpg-style
    SQL that returned HTTP 500 the moment a real request reached it, because it
    had only ever been exercised from a CLI holding a different connection.
    """
    from libs.database_connector.postgres_connector import PostgresClient

    client = PostgresClient()
    client.set_url(dsn)
    await client.open()
    try:
        async with client.get_client(str(tenant_id)) as session:
            yield session
    finally:
        await client.close()


async def command_seed(dsn: str, tenant_id: uuid.UUID) -> int:
    async with _session(dsn, tenant_id) as session:
        counts = await seed(session, tenant_id)
        print(f"  seeded: {counts['sources']} nguồn · {counts['variables']} biến "
              f"· {counts['bindings']} series")
    return 0


async def command_list(dsn: str, tenant_id: uuid.UUID) -> int:
    async with _session(dsn, tenant_id) as session:
        rows = await list_sources(session)
        if not rows:
            print("  (registry rỗng — chạy `seed` trước)")
            return 0
        print(f"  {'code':22} {'connector':11} {'series':>6}  {'lần chạy gần nhất':24} trạng thái")
        for row in rows:
            run = row["last_run"]
            when = (run["started_at"][:19].replace("T", " ") if run else "chưa chạy")
            status = (f"{run['status']}  +{run['inserted']} mới/{run['fetched']}"
                      if run else "-")
            print(f"  {row['code']:22} {row['connector']:11} {row['series_count']:>6}  "
                  f"{when:24} {status}")
    return 0


async def command_run(dsn: str, tenant_id: uuid.UUID, only: str | None) -> int:
    from libs.database_connector.minio_connector import MinIOClient
    from libs.database_connector.postgres_connector import PostgresClient

    # The artifact store is not optional: a run that writes observations with
    # no stored response leaves figures nothing can be checked against.
    minio_url = os.environ.get("MINIO_URL")
    if not minio_url:
        raise SystemExit(
            "MINIO_URL is required: the artifact store is not optional, and a run "
            "that wrote observations with no stored response would leave figures "
            "nothing can be checked against.")
    client = MinIOClient()
    client.set_url(minio_url)
    minio = await client.open()
    # upload_to_bronze manages its own session (document and lifecycle row in
    # one transaction), so it needs the client rather than the runner's session.
    bronze_pg = PostgresClient()
    bronze_pg.set_url(dsn)
    await bronze_pg.open()
    failures = 0
    async with _session(dsn, tenant_id) as session:
        sources = await list_sources(session)
        if only:
            sources = [s for s in sources if s["code"] == only]
            if not sources:
                print(f"  không có nguồn nào mã {only!r}")
                return 2
        for source in sources:
            if not source["enabled"]:
                continue
            summary = await run_source(session, tenant_id, uuid.UUID(source["source_id"]),
                                       minio=minio, postgres=bronze_pg)
            failures += 1 if summary.status == "failed" else 0
            print(f"  {summary.source_code:22} {summary.status:8} "
                  f"series {summary.series_ok}/{summary.series_ok + summary.series_failed}  "
                  f"fetched={summary.fetched:3} mới={summary.inserted:3} "
                  f"đã_có={summary.skipped:3}")
            for warning in summary.warnings[:3]:
                print(f"        ! {warning[:100]}")
    await bronze_pg.close()
    return 1 if failures else 0


async def command_promote(dsn: str, tenant_id: uuid.UUID) -> int:
    """One-shot for artifacts stored before promotion existed."""
    from libs.database_connector.postgres_connector import PostgresClient
    from services.ankt_connectors.promote import backfill

    tier_pg = PostgresClient()
    tier_pg.set_url(dsn)
    await tier_pg.open()
    try:
        async with _session(dsn, tenant_id) as session:
            done = await backfill(session, tier_pg, tenant_id)
    finally:
        await tier_pg.close()
    if not done:
        print("  không có artifact nào còn ở bronze")
        return 0
    for row in done:
        print(f"  {row['variable_code']:28} {row['series_key']:24} "
              f"{row['observations']:>4} quan sát  → gold")
    print(f"  {len(done)} artifact đã đi hết bronze → silver → gold")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="ankt-connectors")
    parser.add_argument("command", choices=("seed", "list", "run", "promote"))
    parser.add_argument("--dsn", default=os.environ.get("ANKT_DSN"))
    parser.add_argument("--tenant", default=os.environ.get("ANKT_TENANT_ID"))
    parser.add_argument("--source", help="chỉ chạy một nguồn theo mã")
    args = parser.parse_args()

    if not (args.dsn and args.tenant):
        parser.error("--dsn và --tenant là bắt buộc (hoặc ANKT_DSN / ANKT_TENANT_ID)")
    tenant = uuid.UUID(args.tenant)

    if args.command == "seed":
        return asyncio.run(command_seed(args.dsn, tenant))
    if args.command == "list":
        return asyncio.run(command_list(args.dsn, tenant))
    if args.command == "promote":
        return asyncio.run(command_promote(args.dsn, tenant))
    return asyncio.run(command_run(args.dsn, tenant, args.source))


if __name__ == "__main__":
    raise SystemExit(main())
