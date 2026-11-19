"""A dataset's versions as tables, and the view readers name (DF-02, F-01).

A full run used to replace workspace.<name> in place: the rows an answer was read from were gone
after the next load, and a receipt could not be replayed. Now each full run writes
workspace.<base>__v<n> (`version_base`: unique to the name, 55 characters at most) and, in
the run's own transaction, `public.da_swap_version` points the view workspace.<name> at it: a run
that fails leaves the view and catalog.dataset.current_version on the version before. An
incremental run appends to the current version's table and refreshes the view, which shows a
column it added.

The previous versions stay as tables (`physical_table` on catalog.dataset_version). Beyond the
KEEP_VERSIONS newest, a version is marked `retained_until` its end plus KEEP_DAYS; dropping one is
for a job that first checks its Parquet snapshot exists (DF-04), never for this worker.

INGEST_VERSIONED=0 keeps the replace-in-place path for one release.
"""

from __future__ import annotations

import datetime
import hashlib
import os
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text

ENABLED = os.environ.get("INGEST_VERSIONED", "1") != "0"
KEEP_VERSIONS = 3
KEEP_DAYS = 90
BASE_CHARS = 55


# 55 + "__v" + 5 digits = 63, PostgreSQL's limit; da_swap_version refuses a sixth digit.
MAX_VERSION = 99_999


def version_base(table_name: str) -> str:
    """The stem every version of `table_name` shares, unique to it: the name itself when it fits
    in 55 characters, else its first 46, `_` and 8 hex of the sha256 of the whole name.

    Not the name cut to 55: an ingest table is `ingest_<stem, 45>_<job id, 8 hex>`, and the cut
    dropped most of the job id, so two long-named jobs, of two organisations as well, shared their
    version tables in the one workspace schema (security review of 5d59b877).
    public.da_swap_version derives the same stem and refuses any other."""
    if len(table_name) <= BASE_CHARS:
        return table_name
    digest = hashlib.sha256(table_name.encode()).hexdigest()[:8]
    return f"{table_name[: BASE_CHARS - 9]}_{digest}"


def physical_name(table_name: str, version: int) -> str:
    """The table of one version: `<version_base>__v<n>`, 63 characters at most."""
    if not 1 <= version <= MAX_VERSION:
        raise ValueError(f"dataset version {version} is out of 1..{MAX_VERSION}")
    return f"{version_base(table_name)}__v{version}"


@dataclass(frozen=True)
class Current:
    """What a run starts from: the dataset's current version and the table that holds it."""

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


async def swap(session: Any, table_name: str, physical: str, legacy: str | None = None) -> bool:
    """Point workspace.<table_name> at `physical`; True when a table stood there and became
    `legacy`, the version it was."""
    renamed = await session.scalar(
        text("SELECT public.da_swap_version(:name, :physical, :legacy)"),
        {"name": table_name, "physical": physical, "legacy": legacy},
    )
    return bool(renamed)


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
