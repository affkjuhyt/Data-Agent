"""A semantic model's metrics against SQL written by hand, on the source's own data (EV-03).

    kubectl exec -i deploy/worker-agent -- python -m services.worker_agent.semantic.reconcile \
        --org <id> --space DS-TLC - < {"model": …, "reference": …}

`make tlc-reconcile` sends deploy/docker/semantic/ds-tlc.yaml and ds-tlc-reference.yaml, read
into one JSON document by the dev-tools image (the worker carries no YAML reader). Each
reference check names a metric, a cut or none, and the SQL an analyst writes for it; the metric is
compiled from the model with the real compiler (semantic/compiler.py) over the reference's period,
both queries run on the Data Space's source in a read-only transaction, and the values are
compared member by member. The model is the file, not the catalogue's copy, so a model can be
reconciled before its metrics are certified: a difference is the compiler's or the model's.

The DS-AW model is reconciled in CI (its data is small: tests/integration/worker_agent/
test_adventureworks_reconciliation.py); TLC's 9.5 million trips are reconciled here, on the load.
Reads only: nothing is written, not even a query run.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import decimal
import json
import math
import sys
import uuid
from collections.abc import Sequence
from typing import Any

import asyncpg
from sqlalchemy import select

from libs.dataagent_db import scoped_session
from libs.dataagent_db.models import catalog, org
from services.worker_agent.semantic import compiler as c

_AGGS = {"sum": "SUM", "count": "COUNT", "avg": "AVG", "min": "MIN", "max": "MAX"}
# Equal to the cent on a sum of millions, and to 1e-9 of a ratio.
REL_TOL = 1e-9
ABS_TOL = 1e-6


def layer_of(model: dict[str, Any]) -> tuple[c.Layer, dict[str, c.MetricVersion]]:
    """The compiler's layer and metric versions of a MetricFlow-shaped model (semantic YAML)."""
    tables: dict[uuid.UUID, c.Table] = {}
    datasets: dict[str, uuid.UUID] = {}
    measures: dict[str, c.Measure] = {}
    dims: dict[str, c.Dimension] = {}
    for sm in model["semantic_models"]:
        entity = uuid.uuid4()
        schema, table = sm["model"].split(".")
        datasets[sm["model"]] = uuid.uuid4()
        tables[entity] = c.Table(datasets[sm["model"]], schema, table)
        for m in sm.get("measures", ()):
            expr = f"{_AGGS[m.get('agg', 'sum')]}({m['expr']})"
            measures[m["name"]] = c.Measure(m["name"], expr, entity, m.get("additivity", "full"))
        for d in sm.get("dimensions", ()):
            dims[d["name"]] = c.Dimension(
                uuid.uuid4(), d["name"], d["label"], d["expr"], entity, d["type"] == "time"
            )
    joins = tuple(
        c.Join(
            datasets[r["from"]], tuple(r["from_columns"]), datasets[r["to"]], tuple(r["to_columns"])
        )
        for r in model.get("relationships", ())
    )
    metrics = {
        m["name"]: c.MetricVersion(
            id=uuid.uuid4(),
            metric_code=m["name"],
            display_name=m["label"],
            expression=m["expr"],
            filters=tuple(m.get("filters", ())),
            unit=m.get("unit"),
            decimals=m.get("decimals", 2),
            period_semantics=m["period_semantics"],
            allowed_dimension_ids=frozenset(dims[d].id for d in m.get("dimensions", ())),
            certified=True,
            time_grain=m.get("time_grain", "day"),
        )
        for m in model["metrics"]
    }
    return c.Layer(measures, dims, tables, joins), metrics


def _number(value: Any) -> float | None:
    if value is None:
        return None
    return float(value) if isinstance(value, decimal.Decimal | int | float) else None


def _values(rows: Sequence[Sequence[Any]], grouped: bool) -> dict[str | None, float | None]:
    if not grouped:
        return {None: _number(rows[0][0]) if rows else None}
    return {(None if r[0] is None else str(r[0])): _number(r[-1]) for r in rows}


def _same(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return a is b
    return math.isclose(a, b, rel_tol=REL_TOL, abs_tol=ABS_TOL)


def differences(
    compiled: dict[str | None, float | None], reference: dict[str | None, float | None]
) -> list[str]:
    """Each member whose value differs, or that one side has and the other does not."""
    out = []
    for member in sorted(set(compiled) | set(reference), key=lambda m: (m is None, str(m))):
        a, b = compiled.get(member, "absent"), reference.get(member, "absent")
        if a == "absent" or b == "absent" or not _same(a, b):  # type: ignore[arg-type]
            out.append(f"{member}: compiled {a}, by hand {b}")
    return out


async def _connection(org_id: uuid.UUID, space_code: str) -> Any:
    from services.worker_agent.questions import secrets
    from services.worker_agent.source_tests import _ssl_mode

    async with scoped_session("svc_catalog", org_id) as session:
        source = await session.scalar(
            select(catalog.Source)
            .join(org.DataSpace, org.DataSpace.id == catalog.Source.data_space_id)
            .where(
                catalog.Source.org_id == org_id,
                org.DataSpace.code == space_code,
                catalog.Source.kind == "postgresql",
                catalog.Source.deleted_at.is_(None),
            )
        )
    if source is None:
        raise SystemExit(f"{space_code} has no PostgreSQL source")
    credentials = await secrets().read(source.org_id, source.secret_ref)
    conn = await asyncpg.connect(
        host=source.host,
        port=source.port or 5432,
        database=source.database_name,
        user=credentials.user,
        password=credentials.password,
        ssl=_ssl_mode(source),
        timeout=30,
        statement_cache_size=0,
        server_settings={"application_name": "dataagent-reconcile"},
    )
    await conn.execute("SET default_transaction_read_only = on")
    await conn.execute("SET statement_timeout = 600000")
    return conn


async def reconcile(
    model: dict[str, Any], reference: dict[str, Any], conn: Any
) -> list[dict[str, Any]]:
    layer, metrics = layer_of(model)
    period = c.Period(
        datetime.date.fromisoformat(reference["period"]["from"]),
        datetime.date.fromisoformat(reference["period"]["to"]),
    )
    results = []
    for check in reference["checks"]:
        cut = [check["cut"]] if check.get("cut") else []
        compiled = c.compile_metric(
            c.Request(metrics[check["metric"]], group_by=cut, period=period),
            layer,
            dialect="postgres",
        )
        got = _values(await conn.fetch(compiled.sql), bool(cut))
        want = _values(await conn.fetch(check["sql"]), bool(cut))
        wrong = differences(got, want)
        results.append(
            {
                "metric": check["metric"],
                "cut": check.get("cut"),
                "members": len(want),
                "equal": not wrong,
                "differences": wrong[:10],
            }
        )
    return results


async def _main(args: argparse.Namespace) -> int:
    document = json.loads(sys.stdin.read())
    model, reference = document["model"], document["reference"]
    conn = await _connection(uuid.UUID(args.org), args.space)
    try:
        results = await reconcile(model, reference, conn)
    finally:
        await conn.close()
    checked = {r["metric"] for r in results}
    missing = sorted({m["name"] for m in model["metrics"]} - checked)
    for r in results:
        mark = "ok  " if r["equal"] else "DIFF"
        print(f"{mark} {r['metric']:<22} {r['cut'] or '(tổng)':<18} {r['members']:>4} nhóm")
        for line in r["differences"]:
            print(f"       {line}")
    print(
        f"{sum(r['equal'] for r in results)}/{len(results)} checks equal;"
        f" {len(checked)} of {len(model['metrics'])} metrics reconciled"
        + (f"; not reconciled: {', '.join(missing)}" if missing else "")
    )
    return 0 if all(r["equal"] for r in results) and not missing else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--org", required=True)
    parser.add_argument("--space", default="DS-TLC")
    parser.add_argument("stdin", nargs="?", default="-")
    return asyncio.run(_main(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
