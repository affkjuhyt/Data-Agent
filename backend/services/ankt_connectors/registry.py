"""Reading and seeding the source registry.

Separate from `runner` because the API needs these without needing to run
anything: the Nguồn dữ liệu screen lists sources and their last run on every
page load, and fetching from the IMF to render a table would be absurd.
"""
from __future__ import annotations

import json
import pathlib
import uuid
from typing import Any

from services.ankt_connectors.db import commit, execute, fetch_all, fetch_value

SEED_FILE = pathlib.Path(__file__).with_name("seed.yaml")

_LIST = """
SELECT s.source_id, s.code, s.title, s.publisher, s.connector, s.schedule,
       s.licence, s.classification, s.enabled, s.config,
       (SELECT count(*) FROM ankt.source_binding b
         WHERE b.source_id = s.source_id AND b.enabled)      AS series_count,
       r.run_id, r.started_at, r.finished_at, r.status,
       r.fetched, r.inserted, r.skipped, r.error
  FROM ankt.source s
  LEFT JOIN LATERAL (
       SELECT * FROM ankt.connector_run cr
        WHERE cr.source_id = s.source_id
        ORDER BY cr.started_at DESC LIMIT 1
  ) r ON true
 ORDER BY s.code
"""

_RUNS = """
SELECT run_id, started_at, finished_at, status, fetched, inserted, skipped,
       error, warnings, triggered_by
  FROM ankt.connector_run
 WHERE source_id = :source_id
 ORDER BY started_at DESC
 LIMIT :limit
"""

_BINDINGS = """
SELECT b.binding_id, b.series_key, b.enabled, v.code AS variable_code,
       v.concept, v.unit, v.currency, v.stock_or_flow, v.perimeter,
       (SELECT count(*) FROM ankt.observation o
         WHERE o.variable_id = b.variable_id)  AS observation_count,
       (SELECT max(o.period) FROM ankt.observation o
         WHERE o.variable_id = b.variable_id)  AS latest_period
  FROM ankt.source_binding b
  JOIN ankt.variable v ON v.variable_id = b.variable_id
 WHERE b.source_id = :source_id
 ORDER BY v.code
"""


async def list_sources(session: Any) -> list[dict[str, Any]]:
    """Every source with the last run folded in — one query, not N+1.

    The screen shows both together on every row, so fetching runs separately
    per source would make a twenty-source page twenty-one round trips.
    """
    rows = await fetch_all(session, _LIST)
    return [
        {
            "source_id": str(row["source_id"]),
            "code": row["code"],
            "title": row["title"],
            "publisher": row["publisher"],
            "connector": row["connector"],
            "schedule": row["schedule"],
            "licence": row["licence"],
            "classification": row["classification"],
            "enabled": row["enabled"],
            "series_count": row["series_count"],
            "last_run": None if row["run_id"] is None else {
                "run_id": str(row["run_id"]),
                "started_at": _iso(row["started_at"]),
                "finished_at": _iso(row["finished_at"]),
                "status": row["status"],
                "fetched": row["fetched"],
                "inserted": row["inserted"],
                "skipped": row["skipped"],
                "error": row["error"],
            },
        }
        for row in rows
    ]


async def list_runs(session: Any, source_id: uuid.UUID, limit: int = 20) -> list[dict[str, Any]]:
    rows = await fetch_all(session, _RUNS, source_id=source_id, limit=limit)
    return [
        {
            "run_id": str(row["run_id"]),
            "started_at": _iso(row["started_at"]),
            "finished_at": _iso(row["finished_at"]),
            "status": row["status"],
            "fetched": row["fetched"],
            "inserted": row["inserted"],
            "skipped": row["skipped"],
            "error": row["error"],
            "warnings": _json_list(row["warnings"]),
            "triggered_by": row["triggered_by"],
        }
        for row in rows
    ]


async def list_bindings(session: Any, source_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = await fetch_all(session, _BINDINGS, source_id=source_id)
    return [
        {
            "binding_id": str(row["binding_id"]),
            "series_key": row["series_key"],
            "enabled": row["enabled"],
            "variable_code": row["variable_code"],
            "concept": row["concept"],
            "unit": row["unit"],
            "currency": row["currency"],
            "stock_or_flow": row["stock_or_flow"],
            "perimeter": row["perimeter"],
            "observation_count": row["observation_count"],
            "latest_period": row["latest_period"],
        }
        for row in rows
    ]


async def seed(session: Any, tenant_id: uuid.UUID) -> dict[str, int]:
    """Upsert the bootstrap catalogue. Safe to re-run.

    Updates the config of a source that already exists rather than skipping it,
    so a corrected base URL in the seed reaches an environment that was set up
    before the correction — but never touches observations, which belong to the
    append-only store.
    """
    import yaml

    raw = yaml.safe_load(SEED_FILE.read_text(encoding="utf-8"))
    variable_ids: dict[str, uuid.UUID] = {}
    for spec in raw["variables"]:
        variable_ids[spec["code"]] = await fetch_value(
            session,
            """
            INSERT INTO ankt.variable (tenant_id, code, concept, unit, unit_multiplier,
                                       currency, stock_or_flow, perimeter)
            VALUES (:tenant_id, :code, :concept, :unit, 0, :currency, :stock_or_flow,
                    :perimeter)
            ON CONFLICT (tenant_id, code) DO UPDATE SET concept = EXCLUDED.concept
            RETURNING variable_id
            """,
            tenant_id=tenant_id, code=spec["code"], concept=spec["concept"],
            unit=spec["unit"], currency=spec.get("currency"),
            stock_or_flow=spec["stock_or_flow"], perimeter=spec.get("perimeter"),
        )

    sources = bindings = 0
    for spec in raw["sources"]:
        source_id = await fetch_value(
            session,
            """
            INSERT INTO ankt.source (tenant_id, code, title, publisher, connector,
                                     config, schedule, licence)
            VALUES (:tenant_id, :code, :title, :publisher, :connector,
                    CAST(:config AS jsonb), :schedule, :licence)
            ON CONFLICT (tenant_id, code) DO UPDATE
                SET title = EXCLUDED.title, publisher = EXCLUDED.publisher,
                    connector = EXCLUDED.connector, config = EXCLUDED.config,
                    schedule = EXCLUDED.schedule, licence = EXCLUDED.licence,
                    updated_at = now()
            RETURNING source_id
            """,
            tenant_id=tenant_id, code=spec["code"], title=spec["title"],
            publisher=spec.get("publisher"), connector=spec["connector"],
            config=json.dumps(spec.get("config") or {}),
            schedule=spec.get("schedule"), licence=spec.get("licence"),
        )
        sources += 1
        for binding in spec.get("bindings") or []:
            await execute(
                session,
                """
                INSERT INTO ankt.source_binding (tenant_id, source_id, variable_id, series_key)
                VALUES (:tenant_id, :source_id, :variable_id, :series_key)
                ON CONFLICT (tenant_id, source_id, series_key) DO NOTHING
                """,
                tenant_id=tenant_id, source_id=source_id,
                variable_id=variable_ids[binding["variable"]],
                series_key=binding["series_key"],
            )
            bindings += 1

    await commit(session, tenant_id)
    return {"variables": len(variable_ids), "sources": sources, "bindings": bindings}


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _json_list(raw: Any) -> list[Any]:
    if isinstance(raw, list):
        return raw
    try:
        decoded = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return decoded if isinstance(decoded, list) else []
