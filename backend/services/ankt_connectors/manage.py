"""Creating and editing sources, and trying one before it is saved.

The catalogue is only config-driven if a steward can actually change it. Until
this module existed the registry was read-only from the product's side: adding
the next source meant editing seed.yaml and a deploy, which is the same as
saying the people who know which sources matter cannot add one.

`preview` is the part that makes the form usable. Nobody can be asked to type
`VNM.GGXWDG.A` correctly from memory — I guessed two IMF series keys wrong in a
row while building this. So the wizard fetches with the config the steward has
entered, WITHOUT saving anything, and shows what came back. That works for
every protocol, which a per-source browse endpoint would not.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import httpx

from services.ankt_connectors.db import commit, execute, fetch_all, fetch_one, fetch_value
from services.ankt_connectors.plugins import ConfigError, FetchError, fetch

_TIMEOUT = httpx.Timeout(45.0, connect=15.0)


class SourceNotFound(LookupError):
    """No such source for this tenant. Distinct from a permission failure."""


async def create_source(session: Any, tenant_id: uuid.UUID, spec: dict[str, Any]) -> str:
    source_id = await fetch_value(
        session,
        """
        INSERT INTO ankt.source (tenant_id, code, title, publisher, connector,
                                 config, schedule, licence, classification)
        VALUES (:tenant_id, :code, :title, :publisher, :connector,
                CAST(:config AS jsonb), :schedule, :licence,
                COALESCE(:classification, 'public'))
        RETURNING source_id
        """,
        tenant_id=tenant_id, code=spec["code"], title=spec["title"],
        publisher=spec.get("publisher"), connector=spec["connector"],
        config=json.dumps(spec.get("config") or {}), schedule=spec.get("schedule"),
        licence=spec.get("licence"), classification=spec.get("classification"),
    )
    await commit(session, tenant_id)
    return str(source_id)


async def update_source(
    session: Any, tenant_id: uuid.UUID, source_id: uuid.UUID, spec: dict[str, Any],
) -> None:
    """Patch the fields the caller sent, leave the rest alone.

    Built from the keys present rather than from a full row so a form that
    edits one field cannot blank the others by omitting them — the mistake a
    naive PUT makes, and an expensive one when the blanked field is a base URL.
    """
    allowed = ("title", "publisher", "connector", "schedule", "licence",
               "classification", "enabled")
    sets, params = [], {"tenant_id": tenant_id, "source_id": source_id}
    for key in allowed:
        if key in spec:
            sets.append(f"{key} = :{key}")
            params[key] = spec[key]
    if "config" in spec:
        sets.append("config = CAST(:config AS jsonb)")
        params["config"] = json.dumps(spec["config"] or {})
    if not sets:
        return
    sets.append("updated_at = now()")
    updated = await fetch_value(
        session,
        f"""UPDATE ankt.source SET {', '.join(sets)}
             WHERE source_id = :source_id AND tenant_id = :tenant_id
         RETURNING source_id""",
        **params,
    )
    if updated is None:
        raise SourceNotFound(str(source_id))
    await commit(session, tenant_id)


async def delete_source(session: Any, tenant_id: uuid.UUID, source_id: uuid.UUID) -> None:
    """Remove a source and its bindings. Observations are NOT touched.

    Deleting a source must not delete the figures it produced: an observation
    is a published fact with its own artifact and release, and losing it because
    a steward tidied up the catalogue would break every answer that cited it.
    """
    deleted = await fetch_value(
        session,
        """DELETE FROM ankt.source
            WHERE source_id = :source_id AND tenant_id = :tenant_id
        RETURNING source_id""",
        source_id=source_id, tenant_id=tenant_id,
    )
    if deleted is None:
        raise SourceNotFound(str(source_id))
    await commit(session, tenant_id)


async def add_binding(
    session: Any, tenant_id: uuid.UUID, source_id: uuid.UUID, spec: dict[str, Any],
) -> str:
    """Bind a series key to a variable, creating the variable if it is new."""
    variable_id = await fetch_value(
        session,
        """
        INSERT INTO ankt.variable (tenant_id, code, concept, unit, unit_multiplier,
                                   currency, stock_or_flow, perimeter)
        VALUES (:tenant_id, :code, :concept, :unit, 0, :currency, :stock_or_flow,
                :perimeter)
        ON CONFLICT (tenant_id, code) DO UPDATE SET concept = EXCLUDED.concept
        RETURNING variable_id
        """,
        tenant_id=tenant_id, code=spec["variable_code"],
        concept=spec.get("concept") or spec["variable_code"], unit=spec["unit"],
        currency=spec.get("currency"), stock_or_flow=spec["stock_or_flow"],
        perimeter=spec.get("perimeter"),
    )
    binding_id = await fetch_value(
        session,
        """
        INSERT INTO ankt.source_binding (tenant_id, source_id, variable_id, series_key)
        VALUES (:tenant_id, :source_id, :variable_id, :series_key)
        ON CONFLICT (tenant_id, source_id, series_key)
            DO UPDATE SET variable_id = EXCLUDED.variable_id, enabled = true
        RETURNING binding_id
        """,
        tenant_id=tenant_id, source_id=source_id, variable_id=variable_id,
        series_key=spec["series_key"],
    )
    await commit(session, tenant_id)
    return str(binding_id)


async def set_binding_enabled(
    session: Any, tenant_id: uuid.UUID, binding_id: uuid.UUID, enabled: bool,
) -> None:
    await execute(
        session,
        """UPDATE ankt.source_binding SET enabled = :enabled
            WHERE binding_id = :binding_id AND tenant_id = :tenant_id""",
        binding_id=binding_id, tenant_id=tenant_id, enabled=enabled,
    )
    await commit(session, tenant_id)


async def remove_binding(
    session: Any, tenant_id: uuid.UUID, binding_id: uuid.UUID,
) -> None:
    await execute(
        session,
        """DELETE FROM ankt.source_binding
            WHERE binding_id = :binding_id AND tenant_id = :tenant_id""",
        binding_id=binding_id, tenant_id=tenant_id,
    )
    await commit(session, tenant_id)


def preview(connector: str, config: dict[str, Any], series_key: str) -> dict[str, Any]:
    """Fetch with these settings and report what came back. Saves nothing.

    Errors are returned rather than raised, and typed: a ConfigError is the
    steward's form to fix, an HTTP error is the publisher being unreachable.
    A wizard that shows "failed" for both sends people to debug the wrong thing.
    """
    try:
        with httpx.Client(timeout=_TIMEOUT, follow_redirects=True) as client:
            fetched = fetch(connector, config, series_key, client)
    except ConfigError as exc:
        return {"ok": False, "kind": "config", "message": str(exc)}
    except FetchError as exc:
        return {"ok": False, "kind": "response", "message": str(exc)}
    except httpx.HTTPError as exc:
        return {"ok": False, "kind": "network",
                "message": f"{type(exc).__name__}: {exc}"}

    periods = sorted(p.period for p in fetched.points)
    sample = sorted(fetched.points, key=lambda p: p.period)[-5:]
    return {
        "ok": True,
        "release_date": fetched.release_date.isoformat(),
        "vintage_label": fetched.vintage_label,
        "count": len(fetched.points),
        "first_period": periods[0] if periods else None,
        "last_period": periods[-1] if periods else None,
        "bytes": len(fetched.raw),
        "warnings": fetched.warnings[:5],
        "sample": [{"period": p.period, "value": str(p.value)} for p in sample],
    }


async def get_source(
    session: Any, tenant_id: uuid.UUID, source_id: uuid.UUID,
) -> dict[str, Any]:
    row = await fetch_one(
        session,
        """SELECT source_id, code, title, publisher, connector, config, schedule,
                  licence, classification, enabled
             FROM ankt.source
            WHERE source_id = :source_id AND tenant_id = :tenant_id""",
        source_id=source_id, tenant_id=tenant_id,
    )
    if row is None:
        raise SourceNotFound(str(source_id))
    config = row["config"]
    return {**row, "source_id": str(row["source_id"]),
            "config": config if isinstance(config, dict) else json.loads(config or "{}")}


async def list_variables(session: Any) -> list[dict[str, Any]]:
    """Variables a new binding can attach to, so the form offers a choice
    instead of asking for a code that must match something invisible."""
    return await fetch_all(
        session,
        """SELECT code, concept, unit, currency, stock_or_flow, perimeter
             FROM ankt.variable ORDER BY code""",
    )
