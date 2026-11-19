"""A dataset's versions as tables, and the view readers name (DF-02, DF-03, F-01).

A full load used to replace workspace.<name> in place: the rows an answer was read from were gone
after the next load, and a receipt could not be replayed. Now every writer of the workspace (an
ingest run, an upload, a pipeline's or a query's saved table) writes workspace.<base>__v<n>
(`version_base`: unique to the name, 55 characters at most) and, in the transaction that wrote it,
`public.da_swap_version` points the view workspace.<name> at it: a write that fails leaves the view
and catalog.dataset.current_version on the version before. An append (an incremental run, an
upload that keeps the rows) writes the current version's table and refreshes the view, which shows
a column it added.

The previous versions stay as tables (`physical_table` on catalog.dataset_version). Beyond the
KEEP_VERSIONS newest, a version is marked `retained_until` its end plus KEEP_DAYS; dropping one is
for a job that first checks its Parquet snapshot exists (DF-04), never for a writer.

A version written whole (a full run, an upload that replaces, a saved pipeline or query) is
announced on catalog.outbox as `dataset.version_created`, in the transaction that catalogues it:
worker-agent snapshots it to Parquet (DF-05, services/worker_agent/snapshots.py). An append is
not announced; the snapshot of its version is the one taken when the version was written.

INGEST_VERSIONED=0 keeps the replace-in-place path for one release.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, text, update

from .base import uuid7
from .models import catalog

ENABLED = os.environ.get("INGEST_VERSIONED", "1") != "0"
KEEP_VERSIONS = 3
KEEP_DAYS = 90
BASE_CHARS = 55


# 55 + "__v" + 5 digits = 63, PostgreSQL's limit; da_swap_version refuses a sixth digit.
MAX_VERSION = 99_999


DIGEST_CHARS = 16

VERSION_CREATED = "dataset.version_created"


def version_base(table_name: str) -> str:
    """The stem every version of `table_name` shares, unique to it: the name itself when it has 54
    characters or fewer, else its first 38, `_` and 16 hex of the sha256 of the whole name, always
    55 characters, so a plain stem never equals a hashed one.

    Not the name cut to 55: an ingest table is `ingest_<stem, 45>_<job id, 8 hex>`, and the cut
    dropped most of the job id, so two long-named jobs, of two organisations as well, shared their
    version tables in the one workspace schema (security reviews of 5d59b877 and ba8fee4c).
    public.da_swap_version derives the same stem and refuses any other."""
    if len(table_name) < BASE_CHARS:
        return table_name
    digest = hashlib.sha256(table_name.encode()).hexdigest()[:DIGEST_CHARS]
    return f"{table_name[: BASE_CHARS - DIGEST_CHARS - 1]}_{digest}"


def physical_name(table_name: str, version: int) -> str:
    """The table of one version: `<version_base>__v<n>`, 63 characters at most."""
    if not 1 <= version <= MAX_VERSION:
        raise ValueError(f"dataset version {version} is out of 1..{MAX_VERSION}")
    return f"{version_base(table_name)}__v{version}"


def schema_hash(columns: Sequence[dict[str, Any]]) -> str:
    """The same columns, the same hash, in any process (`hash()` of a str is salted per process)."""
    payload = json.dumps(
        sorted((str(c["name"]), str(c["type"])) for c in columns), separators=(",", ":")
    )
    return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True)
class Current:
    """What a write starts from: the dataset's current version and the table that holds it."""

    version: int | None
    physical: str | None
    latest: int  # the highest version number written so far, 0 for a new dataset


async def current(session: Any, dataset_id: uuid.UUID) -> Current:
    row = (
        await session.execute(
            text(
                "SELECT d.current_version, v.physical_table,"
                " (SELECT max(version) FROM catalog.dataset_version WHERE dataset_id = d.id)"
                " FROM catalog.dataset d LEFT JOIN catalog.dataset_version v"
                " ON v.dataset_id = d.id AND v.version = d.current_version"
                " WHERE d.id = :id"
            ),
            {"id": dataset_id},
        )
    ).one_or_none()
    if row is None:
        return Current(version=None, physical=None, latest=0)
    return Current(version=row[0], physical=row[1], latest=int(row[2] or 0))


async def next_version(session: Any, table_name: str, start: Current) -> int:
    """The number of the version a write makes: past every version the catalog knows, the current
    one (a dataset made before versioning counted them without rows) and every version table that
    exists. The data and the catalog are written in two transactions: when the second failed, the
    table of the version it did not record stands, and the next write must not take its name."""
    standing = await session.scalar(
        text(
            "SELECT max(substring(c.relname FROM '__v([0-9]+)$')::int) FROM pg_class c"
            " JOIN pg_namespace n ON n.oid = c.relnamespace"
            " WHERE n.nspname = 'workspace' AND c.relname ~ ('^' || :base || '__v[0-9]+$')"
        ),
        # A base is [a-z0-9_] only, so it is safe inside the pattern.
        {"base": version_base(table_name)},
    )
    return max(start.latest, start.version or 0, int(standing or 0)) + 1


async def swap(session: Any, table_name: str, physical: str, legacy: str | None = None) -> bool:
    """Point workspace.<table_name> at `physical`; True when a table stood there and became
    `legacy`, the version it was."""
    renamed = await session.scalar(
        text("SELECT public.da_swap_version(:name, :physical, :legacy)"),
        {"name": table_name, "physical": physical, "legacy": legacy},
    )
    return bool(renamed)


async def publish(session: Any, table_name: str, physical: str, start: Current) -> str | None:
    """Point the view at `physical`. A table written before versioning that stood where the view
    goes becomes the version it held; returns its new name, else None."""
    legacy = physical_name(table_name, start.version or max(start.latest, 1))
    return legacy if await swap(session, table_name, physical, legacy) else None


def change_kind(previous: set[str], columns: list[str]) -> tuple[str, dict[str, list[str]]]:
    """How a full run's version differs from the one before: `initial` for the first, `additive`
    when it only gained columns, `reload` otherwise (rows replaced, columns kept or changed; a
    `breaking` change needs a proposal, which a load does not have)."""
    added = [c for c in columns if c not in previous]
    removed = sorted(previous - set(columns))
    if not previous:
        return "initial", {"added": added}
    if added and not removed:
        return "additive", {"added": added}
    return "reload", {"added": added, "removed": removed}


async def record(
    session: Any,
    *,
    org_id: uuid.UUID,
    dataset_id: uuid.UUID,
    version: int,
    physical: str,
    columns: Sequence[dict[str, Any]],
    start: Current,
    legacy: str | None,
    actor: uuid.UUID | None,
    kind: str,
    run_id: uuid.UUID | None,
) -> None:
    """Catalogue a version a write made (svc_catalog's session, the dataset row already there):
    the version before ends, the table that stood where the view now is keeps it, the dataset's
    current version is this one, and the version is announced for its snapshot."""
    known = {
        str(name): str(kind)
        for name, kind in (
            await session.execute(
                select(catalog.ColumnMeta.name, catalog.ColumnMeta.data_type).where(
                    catalog.ColumnMeta.dataset_id == dataset_id
                )
            )
        ).all()
    }
    moment = now()
    if legacy is not None and start.version is not None:
        kept = await session.execute(
            update(catalog.DatasetVersion)
            .where(
                catalog.DatasetVersion.dataset_id == dataset_id,
                catalog.DatasetVersion.version == start.version,
            )
            .values(physical_table=legacy)
        )
        if not kept.rowcount:
            # A dataset written before versioning counted its versions without rows: the table it
            # held gets the row of the version it was.
            dataset = await session.get(catalog.Dataset, dataset_id)
            session.add(
                catalog.DatasetVersion(
                    id=uuid.uuid4(),
                    org_id=org_id,
                    dataset_id=dataset_id,
                    version=start.version,
                    schema_hash=schema_hash([{"name": n, "type": t} for n, t in known.items()]),
                    diff={"added": list(known)},
                    change_kind="initial",
                    valid_from=dataset.created_at if dataset is not None else moment,
                    physical_table=legacy,
                    created_by=actor,
                )
            )
            await session.flush()
    await session.execute(
        update(catalog.DatasetVersion)
        .where(
            catalog.DatasetVersion.dataset_id == dataset_id,
            catalog.DatasetVersion.valid_to.is_(None),
            catalog.DatasetVersion.valid_from < moment,
        )
        .values(valid_to=moment)
    )
    change, diff = change_kind(set(known), [str(c["name"]) for c in columns])
    if change == "initial" and (start.latest or legacy is not None):
        change = "reload"
    session.add(
        catalog.DatasetVersion(
            id=uuid.uuid4(),
            org_id=org_id,
            dataset_id=dataset_id,
            version=version,
            schema_hash=schema_hash(columns),
            diff=diff,
            change_kind=change,
            valid_from=moment,
            physical_table=physical,
            created_by=actor,
        )
    )
    dataset = await session.get(catalog.Dataset, dataset_id)
    if dataset is not None:
        dataset.current_version = version
    await session.flush()
    await mark_retention(session, dataset_id, version)
    announce(
        session,
        org_id=org_id,
        dataset_id=dataset_id,
        version=version,
        physical=physical,
        kind=kind,
        run_id=run_id,
        at=moment,
    )


def announce(
    session: Any,
    *,
    org_id: uuid.UUID,
    dataset_id: uuid.UUID,
    version: int,
    physical: str,
    kind: str,
    run_id: uuid.UUID | None,
    at: datetime.datetime,
) -> None:
    """`dataset.version_created` on catalog.outbox, in the caller's catalog transaction. `kind` is
    what wrote it, as catalog.dataset_snapshot.created_by_kind names it (job_run, pipeline_run,
    upload, derive), `run_id` that run, `at` when its rows became the dataset's."""
    session.add(
        catalog.Outbox(
            id=uuid7(),
            org_id=org_id,
            aggregate_type="dataset",
            aggregate_id=dataset_id,
            event_type=VERSION_CREATED,
            payload={
                "dataset_id": str(dataset_id),
                "version": version,
                "physical_table": physical,
                "created_by_kind": kind,
                "run_id": str(run_id) if run_id else None,
                "available_at": at.isoformat(),
            },
        )
    )


async def mark_retention(session: Any, dataset_id: uuid.UUID, newest: int) -> None:
    """The versions past the KEEP_VERSIONS newest are kept KEEP_DAYS after they ended."""
    await session.execute(
        text(
            "UPDATE catalog.dataset_version SET retained_until = valid_to + make_interval(days => :days)"
            " WHERE dataset_id = :id AND version <= :oldest_kept - 1 AND physical_table IS NOT NULL"
            " AND retained_until IS NULL AND valid_to IS NOT NULL"
        ),
        {"id": dataset_id, "oldest_kept": newest - KEEP_VERSIONS + 1, "days": KEEP_DAYS},
    )


def now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)
