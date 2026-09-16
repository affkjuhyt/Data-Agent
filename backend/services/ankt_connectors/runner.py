"""Running a registered source: read its bindings, fetch, load, record the run.

Everything this needs comes from `ankt.source` and `ankt.source_binding`, so the
same code runs a source a steward added through the wizard five minutes ago and
one that has been scheduled for a year. Nothing is hard-coded per publisher and
nothing is read from the repository.

Every run leaves a row in `ankt.connector_run` whether it worked or not. That
row is what the Nguồn dữ liệu screen reads — last run, records, state — and a
failure with no row would show as a source that simply never ran.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

import httpx

from services.ankt_connectors import artifact
from services.ankt_connectors.db import (
    commit,
    execute,
    fetch_all,
    fetch_one,
    fetch_value,
    rollback,
)
from services.ankt_connectors.plugins import ConfigError, FetchError, fetch

_TIMEOUT = httpx.Timeout(60.0, connect=15.0)


@dataclass(frozen=True)
class RunSummary:
    run_id: str
    source_code: str
    status: str
    fetched: int
    inserted: int
    skipped: int
    series_ok: int
    series_failed: int
    warnings: list[str]
    error: str | None = None


_SOURCE = """
SELECT s.source_id, s.code, s.connector, s.config
  FROM ankt.source s
 WHERE s.source_id = :source_id AND s.enabled
"""

_BINDINGS = """
SELECT b.series_key, v.variable_id, v.code AS variable_code, v.unit,
       v.unit_multiplier, v.currency
  FROM ankt.source_binding b
  JOIN ankt.variable v ON v.variable_id = b.variable_id
 WHERE b.source_id = :source_id AND b.enabled
 ORDER BY v.code
"""


async def run_source(
    session: Any,
    tenant_id: uuid.UUID,
    source_id: uuid.UUID,
    triggered_by: str = "manual",
    client: httpx.Client | None = None,
    minio: Any | None = None,
    postgres: Any | None = None,
) -> RunSummary:
    source = await fetch_one(session, _SOURCE, source_id=source_id)
    if source is None:
        raise LookupError(f"no enabled source {source_id}")
    config = _as_dict(source["config"])
    bindings = await fetch_all(session, _BINDINGS, source_id=source_id)

    run_id = await fetch_value(
        session,
        """INSERT INTO ankt.connector_run (tenant_id, source_id, triggered_by)
           VALUES (:tenant_id, :source_id, :triggered_by) RETURNING run_id""",
        tenant_id=tenant_id, source_id=source_id, triggered_by=triggered_by,
    )
    # Committed before any network call: the row is what the screen polls, and
    # a run that is only visible after it finishes cannot be watched.
    await commit(session, tenant_id)

    totals = {"fetched": 0, "inserted": 0, "skipped": 0}
    warnings: list[str] = []
    ok = failed = 0
    owned = client is None
    http = client or httpx.Client(timeout=_TIMEOUT, follow_redirects=True)
    try:
        for binding in bindings:
            try:
                fetched = fetch(source["connector"], config, binding["series_key"], http)
            except (FetchError, ConfigError, httpx.HTTPError) as exc:
                # One unreadable series must not abandon the rest: a run that
                # loads four of five and says so is more useful than one that
                # loads none, and `partial` below is how the screen shows it.
                failed += 1
                warnings.append(f"{binding['series_key']}: {type(exc).__name__}: {exc}")
                continue

            # Artifact BEFORE extraction, always. The response is the only
            # evidence of what the publisher said; parsing it first and
            # storing nothing leaves a figure no one can check against its
            # source once that source revises (README section 3.3).
            stored = None
            if minio is not None and postgres is not None:
                stored = await artifact.store(
                    session, minio, postgres, tenant_id, source["code"],
                    binding["series_key"], fetched)
                # upload_to_bronze owns its own session and commits there; this
                # only releases the runner's transaction so the tenant setting
                # is restored before the observations are written.
                await commit(session, tenant_id)
                if stored.already_present:
                    warnings.append(
                        f"{binding['series_key']}: byte-identical to a stored "
                        f"artifact ({stored.sha256[:12]}…), nothing new to extract")

            written = await _write(session, tenant_id, binding, fetched, stored)
            totals["fetched"] += len(fetched.points)
            totals["inserted"] += written
            totals["skipped"] += len(fetched.points) - written
            warnings.extend(fetched.warnings)
            ok += 1
    except Exception as exc:
        # rollback() first: the failing statement poisoned the transaction, so
        # the UPDATE recording the failure would itself fail and the run would
        # sit on `running` with nothing explaining it.
        await rollback(session, tenant_id)
        await _finish(session, tenant_id, run_id, "failed", totals, warnings, str(exc))
        raise
    finally:
        if owned:
            http.close()

    status = "failed" if ok == 0 and failed else "partial" if failed else "ok"
    await _finish(session, tenant_id, run_id, status, totals, warnings, None)
    return RunSummary(str(run_id), source["code"], status, totals["fetched"],
                      totals["inserted"], totals["skipped"], ok, failed, warnings)


async def _write(session: Any, tenant_id: uuid.UUID, binding: Any, fetched: Any,
                 stored: Any = None) -> int:
    """One release per vintage, shared by every series of that vintage.

    Sharing it is what makes `vintage_consistent` downstream mean something:
    debt and GDP read from the same WEO edition carry the same release, so a
    ratio built from them is reproducible from one published document.
    """
    release_id = await fetch_value(
        session,
        """SELECT release_id FROM ankt.release
            WHERE tenant_id = :tenant_id AND release_date = :release_date
              AND vintage_label = :vintage_label LIMIT 1""",
        tenant_id=tenant_id, release_date=fetched.release_date,
        vintage_label=fetched.vintage_label,
    ) or await fetch_value(
        session,
        """INSERT INTO ankt.release (tenant_id, release_date, vintage_label, revision_status)
           VALUES (:tenant_id, :release_date, :vintage_label, 'official')
           RETURNING release_id""",
        tenant_id=tenant_id, release_date=fetched.release_date,
        vintage_label=fetched.vintage_label,
    )

    inserted = 0
    for point in fetched.points:
        # Idempotency is the natural key's job, not this loop's: a rerun over an
        # unchanged source produces the same key and is dropped, so the runner
        # needs no memory of what it did last time.
        written = await fetch_value(
            session,
            """
            INSERT INTO ankt.observation
                (tenant_id, variable_id, release_id, reference_area, period, value,
                 unit, unit_multiplier, currency, source_ref, extracted_by, status)
            VALUES (:tenant_id, :variable_id, :release_id, 'VN', :period, :value,
                    :unit, :unit_multiplier, :currency, CAST(:source_ref AS jsonb),
                    'api', 'approved')
            ON CONFLICT ON CONSTRAINT observation_natural_key DO NOTHING
            RETURNING obs_id
            """,
            tenant_id=tenant_id, variable_id=binding["variable_id"],
            release_id=release_id, period=point.period, value=point.value,
            unit=binding["unit"], unit_multiplier=binding["unit_multiplier"],
            currency=binding["currency"],
            source_ref=json.dumps(
                {**point.source_ref, **_artifact_ref(stored)}, ensure_ascii=False),
        )
        inserted += 1 if written else 0
    await commit(session, tenant_id)
    return inserted


async def _finish(session: Any, tenant_id: uuid.UUID, run_id: Any, status: str,
                  totals: dict[str, int], warnings: list[str],
                  error: str | None) -> None:
    await execute(
        session,
        """UPDATE ankt.connector_run
              SET finished_at = now(), status = :status, fetched = :fetched,
                  inserted = :inserted, skipped = :skipped,
                  warnings = CAST(:warnings AS jsonb), error = :error
            WHERE run_id = :run_id""",
        run_id=run_id, status=status, fetched=totals["fetched"],
        inserted=totals["inserted"], skipped=totals["skipped"],
        warnings=json.dumps(warnings[:50], ensure_ascii=False), error=error,
    )
    await commit(session, tenant_id)


def _artifact_ref(stored: Any) -> dict[str, Any]:
    """The stored response this figure was read out of.

    Carried on every observation so a citation resolves to bytes, not just to
    a URL that may answer differently today.
    """
    if stored is None:
        return {}
    return {"artifact_data_id": stored.data_id, "artifact_sha256": stored.sha256,
            "artifact_object": stored.object_key}


def _as_dict(raw: Any) -> dict[str, Any]:
    """asyncpg hands JSONB back as text unless a codec is registered."""
    if isinstance(raw, dict):
        return raw
    try:
        decoded = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}
