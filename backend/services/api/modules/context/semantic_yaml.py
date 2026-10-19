"""A Data Space's semantic model as a YAML file, in and out (R1-06, D4: our model, an open format).

The file follows MetricFlow's layout, so a model written for dbt reads mostly as is and ours reads
in a MetricFlow editor: `semantic_models` (one per entity: its table, key, dimensions, measures),
then `metrics`. Where our model differs, the file says it our way:

    version: 1
    semantic_models:
      - name: khoan_vay                 # entity code
        label: Khoản vay
        model: bank.loan                # schema.table of a catalogued dataset of this Data Space
        primary_key: [loan_id]
        dimensions:
          - {name: ngay, label: Ngày, type: time, expr: as_of_date}
          - {name: san_pham, label: Sản phẩm, type: categorical, expr: product_code}
        measures:
          - {name: du_no, label: Dư nợ, agg: sum, expr: balance, additivity: semi}
    relationships:
      - {from: bank.loan, from_columns: [branch_code], to: bank.branch, to_columns: [code],
         cardinality: many_to_one}
    metrics:
      - name: NPL_RATIO
        label: Tỷ lệ nợ xấu
        expr: no_xau / du_no * 100      # over measure codes, as metric_version.expression
        period_semantics: end_of_period
        time_grain: day
        unit: "%"
        dimensions: [vung, san_pham]    # the cuts the owner allows (BR-81)
        filters: [{dimension: san_pham, op: "!=", values: [NOI_BO]}]

A measure is `agg` over `expr` (MetricFlow); an `expr` that already aggregates (`SUM(CASE …)`) is
kept whole. Importing never approves anything: a relationship is pending_review (BR-100), a new
or changed metric gets a draft version (BR-26), each with the outbox event the API's own create
emits, so review (UC-07-03) is the same as for a steward's edit. Entities, dimensions and
measures have no review of their own; they are written as read, matched by code. A metric from an
industry pack is locked (UC-07-01 2a) and left as it is.

Run where the api runs (it reads DATAAGENT_DB_URL__SVC_CONTEXT):

    python -m services.api.modules.context.semantic_yaml export --org <org id> --space DS-TD
    python -m services.api.modules.context.semantic_yaml import model.yaml --org <org id> \
        --space DS-TD --as steward [--dry-run]

(`make semantic-export` / `make semantic-import` run it in the api pod.)
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db import scoped_session, uuid7
from libs.dataagent_db.models import catalog, context
from libs.dataagent_db.models import org as org_models
from services.api.kernel.auth import Principal
from services.api.modules.context.common import ROLE, audit, emit, now

FORMAT_VERSION = 1
_CODE = r"^[A-Za-z_][A-Za-z0-9_]{0,63}$"
_AGGS = {"sum": "SUM", "count": "COUNT", "avg": "AVG", "min": "MIN", "max": "MAX"}
_AGGREGATES = re.compile(r"\b(?:SUM|COUNT|AVG|MIN|MAX)\s*\(", re.IGNORECASE)
_IDENT = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")


class _Item(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class DimensionDef(_Item):
    name: str = Field(pattern=_CODE)
    label: str | None = None
    type: Literal["categorical", "time"] = "categorical"
    expr: str = Field(min_length=1, max_length=2_000)


class MeasureDef(_Item):
    name: str = Field(pattern=_CODE)
    label: str | None = None
    agg: Literal["sum", "count", "count_distinct", "avg", "min", "max", "last"] = "sum"
    expr: str = Field(min_length=1, max_length=4_000)
    additivity: Literal["full", "semi", "none"] = "full"
    unit: str | None = None


class SemanticModelDef(_Item):
    name: str = Field(pattern=_CODE)
    label: str | None = None
    description: str | None = None
    model: str = Field(pattern=r"^[^.\s]+\.[^.\s]+$")
    primary_key: list[str] = Field(min_length=1)
    dimensions: list[DimensionDef] = Field(default_factory=list)
    measures: list[MeasureDef] = Field(default_factory=list)


class RelationshipDef(_Item):
    from_: str = Field(alias="from")
    from_columns: list[str] = Field(min_length=1)
    to: str
    to_columns: list[str] = Field(min_length=1)
    cardinality: Literal["one_to_one", "many_to_one", "one_to_many"] = "many_to_one"


class MetricDef(_Item):
    name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{1,63}$")
    label: str = Field(min_length=1, max_length=200)
    description: str | None = None
    expr: str = Field(min_length=1, max_length=8_000)
    period_semantics: Literal["end_of_period", "sum_over_period", "average_over_period"]
    time_grain: Literal["day", "month", "quarter", "year"] = "day"
    unit: str | None = None
    decimals: int = Field(default=2, ge=0, le=10)
    direction: Literal["higher_better", "lower_better", "neutral"] = "neutral"
    additivity: Literal["full", "semi", "none"] = "none"
    dimensions: list[str] = Field(default_factory=list)
    filters: list[dict[str, Any]] = Field(default_factory=list)
    legal_basis: str | None = None


class SemanticFile(_Item):
    version: Literal[1] = 1
    semantic_models: list[SemanticModelDef] = Field(default_factory=list)
    relationships: list[RelationshipDef] = Field(default_factory=list)
    metrics: list[MetricDef] = Field(default_factory=list)


class ImportProblem(ValueError):
    """The file cannot be imported as a whole; every reason, each a line a steward can act on."""

    def __init__(self, problems: Sequence[str]) -> None:
        super().__init__("\n".join(problems))
        self.problems = list(problems)


@dataclass
class ImportReport:
    created: dict[str, int] = field(default_factory=dict)
    updated: dict[str, int] = field(default_factory=dict)
    unchanged: dict[str, int] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)

    def count(self, kind: str, what: Literal["created", "updated", "unchanged"]) -> None:
        bucket = getattr(self, what)
        bucket[kind] = bucket.get(kind, 0) + 1


def parse(text: str) -> SemanticFile:
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ImportProblem([f"Tệp không phải YAML hợp lệ: {exc}"]) from exc
    try:
        return SemanticFile.model_validate(raw or {})
    except ValidationError as exc:
        raise ImportProblem(
            [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
        ) from exc


def measure_expression(m: MeasureDef) -> str:
    """The stored aggregate: `agg(expr)`, or `expr` as written when it aggregates already."""
    if _AGGREGATES.search(m.expr):
        return m.expr
    if m.agg == "count_distinct":
        return f"COUNT(DISTINCT {m.expr})"
    if m.agg == "last":
        raise ImportProblem([f"measure {m.name}: agg last needs its own expression"])
    return f"{_AGGS[m.agg]}({m.expr})"


def _measure_def(row: context.SemanticMeasure) -> dict[str, Any]:
    agg = _AGGS.get(row.aggregation)
    simple = (
        re.fullmatch(rf"\s*{agg}\s*\((.+)\)\s*", row.expression, re.IGNORECASE) if agg else None
    )
    inner = simple.group(1) if simple and not _AGGREGATES.search(simple.group(1)) else None
    out: dict[str, Any] = {
        "name": row.code,
        "label": row.name,
        "agg": row.aggregation,
        "expr": inner or row.expression,
        "additivity": row.additivity,
    }
    if row.unit:
        out["unit"] = row.unit
    return out


async def _datasets(session: AsyncSession, data_space: uuid.UUID) -> dict[str, catalog.Dataset]:
    rows = (
        await session.scalars(
            select(catalog.Dataset).where(
                catalog.Dataset.data_space_id == data_space, catalog.Dataset.deleted_at.is_(None)
            )
        )
    ).all()
    return {f"{d.schema_name}.{d.table_name}": d for d in rows}


def _chosen(versions: Sequence[context.MetricVersion], current: int | None) -> Any:
    """The version a reader uses (semantic/loader.py): the current one when certified, else the
    certified one, else the newest."""
    by_no = {v.version: v for v in versions}
    head = by_no.get(current) if current is not None else None
    certified = next(
        (v for v in sorted(versions, key=lambda v: -v.version) if v.status == "certified"), None
    )
    if head is not None and head.status == "certified":
        return head
    return certified or head or (max(versions, key=lambda v: v.version) if versions else None)


async def export_model(session: AsyncSession, data_space: uuid.UUID) -> dict[str, Any]:
    datasets = await _datasets(session, data_space)
    by_id = {d.id: name for name, d in datasets.items()}
    entities = (
        await session.scalars(
            select(context.SemanticEntity)
            .where(
                context.SemanticEntity.data_space_id == data_space,
                context.SemanticEntity.status != "retired",
            )
            .order_by(context.SemanticEntity.code)
        )
    ).all()
    dims = (
        await session.scalars(
            select(context.SemanticDimension)
            .where(context.SemanticDimension.data_space_id == data_space)
            .order_by(context.SemanticDimension.code)
        )
    ).all()
    measures = (
        await session.scalars(
            select(context.SemanticMeasure)
            .where(context.SemanticMeasure.data_space_id == data_space)
            .order_by(context.SemanticMeasure.code)
        )
    ).all()
    models = []
    for e in entities:
        item: dict[str, Any] = {"name": e.code, "label": e.name}
        if e.description:
            item["description"] = e.description
        item["model"] = by_id.get(e.primary_dataset_id, str(e.primary_dataset_id))
        item["primary_key"] = list(e.key_columns)
        item["dimensions"] = [
            {
                "name": d.code,
                "label": d.name,
                "type": "time" if d.is_time else "categorical",
                "expr": d.expression,
            }
            for d in dims
            if d.entity_id == e.id
        ]
        item["measures"] = [_measure_def(m) for m in measures if m.entity_id == e.id]
        models.append(item)
    relationships = [
        {
            "from": by_id[r.from_dataset_id],
            "from_columns": list(r.from_columns),
            "to": by_id[r.to_dataset_id],
            "to_columns": list(r.to_columns),
            "cardinality": r.cardinality,
        }
        for r in (
            await session.scalars(
                select(context.Relationship)
                .where(
                    context.Relationship.data_space_id == data_space,
                    context.Relationship.status == "active",
                )
                .order_by(context.Relationship.created_at)
            )
        ).all()
        if r.from_dataset_id in by_id and r.to_dataset_id in by_id
    ]
    dim_code = {d.id: d.code for d in dims}
    metrics = []
    for m in (
        await session.scalars(
            select(context.Metric)
            .where(context.Metric.data_space_id == data_space, context.Metric.status != "retired")
            .order_by(context.Metric.code)
        )
    ).all():
        versions = (
            await session.scalars(
                select(context.MetricVersion).where(context.MetricVersion.metric_id == m.id)
            )
        ).all()
        v = _chosen(versions, m.current_version)
        if v is None:
            continue
        item = {"name": m.code, "label": v.display_name}
        if v.description:
            item["description"] = v.description
        item.update(
            expr=v.expression,
            period_semantics=v.period_semantics,
            time_grain=v.time_grain,
            unit=v.unit,
            decimals=v.decimals,
            direction=v.direction,
            additivity=v.additivity,
            dimensions=sorted(dim_code[d] for d in (v.allowed_dimensions or ()) if d in dim_code),
            filters=list(v.filters or ()),
        )
        if v.legal_basis:
            item["legal_basis"] = v.legal_basis
        metrics.append(item)
    return {
        "version": FORMAT_VERSION,
        "semantic_models": models,
        "relationships": relationships,
        "metrics": metrics,
    }


def dump(model: dict[str, Any]) -> str:
    text: str = yaml.safe_dump(model, allow_unicode=True, sort_keys=False, width=100)
    return text


def _check(file: SemanticFile, datasets: dict[str, catalog.Dataset], known: set[str]) -> None:
    """Every reference in the file resolves, or nothing is written."""
    problems: list[str] = []
    measures = {m.name for sm in file.semantic_models for m in sm.measures} | known
    dims = {d.name for sm in file.semantic_models for d in sm.dimensions}
    for kind, names in (
        ("semantic_models", [sm.name for sm in file.semantic_models]),
        ("dimensions", [d.name for sm in file.semantic_models for d in sm.dimensions]),
        ("measures", [m.name for sm in file.semantic_models for m in sm.measures]),
        ("metrics", [m.name for m in file.metrics]),
    ):
        twice = sorted({n for n in names if names.count(n) > 1})
        if twice:
            problems.append(f"{kind}: tên lặp lại {', '.join(twice)}")
    for sm in file.semantic_models:
        if sm.model not in datasets:
            problems.append(f"semantic_models.{sm.name}: bảng {sm.model} chưa có trong catalog")
        for m in sm.measures:
            try:
                measure_expression(m)
            except ImportProblem as exc:
                problems += exc.problems
    for r in file.relationships:
        for table in (r.from_, r.to):
            if table not in datasets:
                problems.append(f"relationships: bảng {table} chưa có trong catalog")
    for metric in file.metrics:
        unknown = sorted(set(_IDENT.findall(metric.expr)) - measures)
        if unknown:
            problems.append(
                f"metrics.{metric.name}: expr nêu measure chưa định nghĩa {', '.join(unknown)}"
            )
        named = set(metric.dimensions) | {str(f.get("dimension") or "") for f in metric.filters}
        missing = sorted(named - dims - {""})
        if missing:
            problems.append(f"metrics.{metric.name}: chiều chưa định nghĩa {', '.join(missing)}")
    if problems:
        raise ImportProblem(problems)


def _version_values(m: MetricDef, dim_ids: dict[str, uuid.UUID]) -> dict[str, Any]:
    return {
        "display_name": m.label,
        "description": m.description,
        "expression": m.expr,
        "filters": m.filters,
        "unit": m.unit,
        "decimals": m.decimals,
        "direction": m.direction,
        "period_semantics": m.period_semantics,
        "time_grain": m.time_grain,
        "allowed_dimensions": sorted((dim_ids[d] for d in m.dimensions), key=str),
        "additivity": m.additivity,
        "legal_basis": m.legal_basis,
    }


def _same(row: context.MetricVersion, values: dict[str, Any]) -> bool:
    for key, value in values.items():
        have = getattr(row, key)
        if key == "allowed_dimensions":
            have, value = sorted(map(str, have or ())), sorted(map(str, value))
        elif key == "filters":
            have = list(have or ())
        if have != value:
            return False
    return True


async def import_model(
    session: AsyncSession,
    principal: Principal,
    data_space: uuid.UUID,
    file: SemanticFile,
) -> ImportReport:
    """Write the file into the Data Space in the caller's transaction; ImportProblem when any
    reference does not resolve, before anything is written."""
    datasets = await _datasets(session, data_space)
    existing_measures = set(
        (
            await session.scalars(
                select(context.SemanticMeasure.code).where(
                    context.SemanticMeasure.data_space_id == data_space
                )
            )
        ).all()
    )
    _check(file, datasets, existing_measures)
    report = ImportReport()
    who = {"updated_by": principal.user_id}

    async def by_code(model: Any, code: str) -> Any:
        return await session.scalar(
            select(model).where(model.data_space_id == data_space, model.code == code)
        )

    dim_ids: dict[str, uuid.UUID] = {}
    for sm in file.semantic_models:
        dataset = datasets[sm.model]
        entity = await by_code(context.SemanticEntity, sm.name)
        values = {
            "name": sm.label or sm.name,
            "description": sm.description,
            "primary_dataset_id": dataset.id,
            "key_columns": sm.primary_key,
        }
        if entity is None:
            entity = context.SemanticEntity(
                id=uuid7(),
                org_id=principal.org_id,
                data_space_id=data_space,
                code=sm.name,
                status="active",
                created_by=principal.user_id,
                **values,
                **who,
            )
            session.add(entity)
            report.count("semantic_model", "created")
        elif any(getattr(entity, k) != v for k, v in values.items()):
            for k, v in {**values, **who, "updated_at": now()}.items():
                setattr(entity, k, v)
            report.count("semantic_model", "updated")
        else:
            report.count("semantic_model", "unchanged")
        await session.flush()
        for d in sm.dimensions:
            row = await by_code(context.SemanticDimension, d.name)
            dvalues = {
                "entity_id": entity.id,
                "name": d.label or d.name,
                "expression": d.expr,
                "is_time": d.type == "time",
            }
            row = await _upsert(
                session,
                principal,
                data_space,
                context.SemanticDimension,
                row,
                d.name,
                dvalues,
                report,
                "dimension",
            )
            dim_ids[d.name] = row.id
        for m in sm.measures:
            row = await by_code(context.SemanticMeasure, m.name)
            mvalues = {
                "entity_id": entity.id,
                "name": m.label or m.name,
                "expression": measure_expression(m),
                "aggregation": m.agg,
                "additivity": m.additivity,
                "unit": m.unit,
            }
            await _upsert(
                session,
                principal,
                data_space,
                context.SemanticMeasure,
                row,
                m.name,
                mvalues,
                report,
                "measure",
            )
    for code, dim_id in (
        await session.execute(
            select(context.SemanticDimension.code, context.SemanticDimension.id).where(
                context.SemanticDimension.data_space_id == data_space
            )
        )
    ).all():
        dim_ids.setdefault(code, dim_id)

    for r in file.relationships:
        source, target = datasets[r.from_].id, datasets[r.to].id
        found = await session.scalar(
            select(context.Relationship).where(
                context.Relationship.data_space_id == data_space,
                context.Relationship.from_dataset_id == source,
                context.Relationship.to_dataset_id == target,
                context.Relationship.from_columns == r.from_columns,
                context.Relationship.to_columns == r.to_columns,
                context.Relationship.status != "rejected",
            )
        )
        if found is not None:
            report.count("relationship", "unchanged")
            continue
        # BR-100: a declared relationship waits for review before the compiler joins on it.
        rel = context.Relationship(
            id=uuid7(),
            org_id=principal.org_id,
            data_space_id=data_space,
            from_dataset_id=source,
            from_columns=r.from_columns,
            to_dataset_id=target,
            to_columns=r.to_columns,
            cardinality=r.cardinality,
            origin="declared",
            status="pending_review",
            created_by=principal.user_id,
            **who,
        )
        session.add(rel)
        emit(
            session,
            principal,
            "relationship",
            rel.id,
            "relationship.created",
            relationship_id=rel.id,
            data_space_id=data_space,
            status=rel.status,
        )
        report.count("relationship", "created")

    for defn in file.metrics:
        values = _version_values(defn, dim_ids)
        metric = await by_code(context.Metric, defn.name)
        if metric is not None and metric.pack_metric_id is not None:
            # UC-07-01 2a: a pack metric's formula is locked; changes are overrides (UC-09-03).
            report.skipped.append(f"{defn.name}: chỉ số của gói ngành, công thức bị khoá")
            continue
        if metric is None:
            metric = context.Metric(
                id=uuid7(),
                org_id=principal.org_id,
                data_space_id=data_space,
                code=defn.name,
                owner_id=principal.user_id,
                status="draft",
                created_by=principal.user_id,
                **who,
            )
            session.add(metric)
            await session.flush()
            version = context.MetricVersion(
                id=uuid7(),
                org_id=principal.org_id,
                metric_id=metric.id,
                version=1,
                status="draft",
                change_note="Nhập từ tệp semantic model",
                **values,
            )
            session.add(version)
            emit(
                session,
                principal,
                "metric",
                metric.id,
                "metric.created",
                metric_id=metric.id,
                data_space_id=data_space,
                code=metric.code,
                status=metric.status,
                version_id=version.id,
            )
            report.count("metric", "created")
            continue
        versions = (
            await session.scalars(
                select(context.MetricVersion).where(context.MetricVersion.metric_id == metric.id)
            )
        ).all()
        latest = max(versions, key=lambda v: v.version) if versions else None
        if latest is not None and _same(latest, values):
            report.count("metric", "unchanged")
            continue
        # BR-26: a change of formula is a new version; the certified one stays as it is.
        version = context.MetricVersion(
            id=uuid7(),
            org_id=principal.org_id,
            metric_id=metric.id,
            version=(latest.version if latest else 0) + 1,
            status="draft",
            change_note="Nhập từ tệp semantic model",
            **values,
        )
        session.add(version)
        metric.updated_by, metric.updated_at = principal.user_id, now()
        emit(
            session,
            principal,
            "metric_version",
            version.id,
            "metric_version.created",
            metric_id=metric.id,
            version_id=version.id,
            version=version.version,
            data_space_id=data_space,
            status=version.status,
        )
        report.count("metric", "updated")
    audit(
        session,
        principal,
        action="semantic_model.imported",
        object_kind="data_space",
        object_id=data_space,
        after={"created": report.created, "updated": report.updated},
    )
    await session.flush()
    return report


async def _upsert(
    session: AsyncSession,
    principal: Principal,
    data_space: uuid.UUID,
    model: Any,
    row: Any,
    code: str,
    values: dict[str, Any],
    report: ImportReport,
    kind: str,
) -> Any:
    if row is None:
        row = model(
            id=uuid7(),
            org_id=principal.org_id,
            data_space_id=data_space,
            code=code,
            created_by=principal.user_id,
            updated_by=principal.user_id,
            **values,
        )
        session.add(row)
        report.count(kind, "created")
    elif any(getattr(row, k) != v for k, v in values.items()):
        for k, v in values.items():
            setattr(row, k, v)
        row.updated_by, row.updated_at = principal.user_id, now()
        report.count(kind, "updated")
    else:
        report.count(kind, "unchanged")
    await session.flush()
    return row


# ---------------------------------------------------------------- command line


class _DryRun(Exception):
    """Raised inside the import's transaction so that it rolls back."""


async def _principal(org_id: uuid.UUID, space_code: str, username: str | None) -> Principal:
    """The Data Space and acting user named on the command line, within the organisation."""
    async with scoped_session(ROLE, org_id, None) as session:
        space = await session.scalar(
            select(org_models.DataSpace).where(
                org_models.DataSpace.org_id == org_id, org_models.DataSpace.code == space_code
            )
        )
        user = (
            await session.scalar(
                select(org_models.AppUser).where(
                    org_models.AppUser.org_id == org_id, org_models.AppUser.username == username
                )
            )
            if username
            else None
        )
    if space is None:
        raise SystemExit(f"no Data Space {space_code} in organisation {org_id}")
    if username and user is None:
        raise SystemExit(f"no user {username} in organisation {org_id}")
    return Principal(
        user_id=user.id if user else space.steward_id,
        org_id=org_id,
        external_id=user.external_id if user else "",
        data_space_id=space.id,
    )


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="semantic_yaml", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("export", "import"):
        p = sub.add_parser(name)
        if name == "import":
            p.add_argument("file", help="the YAML file; - reads it from stdin")
            p.add_argument("--as", dest="username", required=True)
            p.add_argument("--dry-run", action="store_true")
        p.add_argument("--org", required=True, type=uuid.UUID, help="the organisation's id")
        p.add_argument("--space", required=True)
    args = parser.parse_args(argv)
    if args.command == "export":
        return asyncio.run(_export(args.org, args.space))
    try:
        if args.file == "-":
            text = sys.stdin.read()
        else:
            with open(args.file, encoding="utf-8") as handle:
                text = handle.read()
        file = parse(text)
        return asyncio.run(_import(args.org, args.space, args.username, file, args.dry_run))
    except ImportProblem as exc:
        sys.stderr.write("\n".join(exc.problems) + "\n")
        return 1


async def _export(org_id: uuid.UUID, space: str) -> int:
    principal = await _principal(org_id, space, None)
    assert principal.data_space_id is not None
    async with scoped_session(ROLE, principal.org_id, principal.user_id) as session:
        sys.stdout.write(dump(await export_model(session, principal.data_space_id)))
    return 0


async def _import(
    org_id: uuid.UUID, space: str, username: str, file: SemanticFile, dry_run: bool
) -> int:
    principal = await _principal(org_id, space, username)
    assert principal.data_space_id is not None
    try:
        async with scoped_session(ROLE, principal.org_id, principal.user_id) as session:
            report = await import_model(session, principal, principal.data_space_id, file)
            if dry_run:
                raise _DryRun
    except _DryRun:
        pass
    sys.stdout.write(
        dump(
            {
                "dry_run": dry_run,
                "created": report.created,
                "updated": report.updated,
                "unchanged": report.unchanged,
                "skipped": report.skipped,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
