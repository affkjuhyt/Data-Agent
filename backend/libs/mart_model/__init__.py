"""Build a data mart's dimensions and relations from active catalog relationships (UC-06-06).

The author names a fact table; active many_to_one relationships of that fact become either
accepted dimensions (when the create body already names them) or suggested_dimensions the
agent offers on the star. Relations are the FK edges those dimensions need for the worker's
check. Pure helpers stay here so the api and the worker share one shape.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping, Sequence
from typing import Any


def _name_of(dataset: Mapping[str, Any]) -> str:
    table = str(dataset.get("table_name") or "").strip()
    schema = str(dataset.get("schema_name") or "").strip()
    display = str(dataset.get("display_name") or "").strip()
    if display:
        return display
    if schema and table:
        return f"{schema}.{table}"
    return table or str(dataset.get("id") or "dimension")


def _dim_key(from_columns: Sequence[str]) -> str:
    return from_columns[0] if from_columns else "key"


def _prefers(a: Sequence[str], b: Sequence[str]) -> bool:
    """Prefer order_* keys when several FKs point at the same dimension table (dim_date)."""
    a0 = (a[0] if a else "").lower()
    b0 = (b[0] if b else "").lower()
    if "order" in a0 and "order" not in b0:
        return True
    if "order" in b0 and "order" not in a0:
        return False
    return a0 < b0


def _taken_names(dimensions: Iterable[Any]) -> set[str]:
    out: set[str] = set()
    for item in dimensions:
        if isinstance(item, Mapping):
            for key in ("name", "label", "table"):
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    out.add(value.strip().lower())
                    # "public.dim_date" and "dim_date" both match the same table.
                    if "." in value:
                        out.add(value.rsplit(".", 1)[-1].lower())
                    break
    return out


def propose_from_relationships(
    *,
    fact_dataset_id: uuid.UUID,
    relationships: Sequence[Mapping[str, Any]],
    datasets: Mapping[uuid.UUID, Mapping[str, Any]],
    dimensions: Sequence[Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (dimensions_kept_or_empty, suggested_dimensions, relations).

    `relationships` are active edges whose from_dataset_id is the fact. When `dimensions` already
    names a target table, that edge becomes a kept relation; otherwise it is suggested.
    """
    best: dict[uuid.UUID, Mapping[str, Any]] = {}
    for raw in relationships:
        if raw.get("from_dataset_id") != fact_dataset_id:
            continue
        if raw.get("status") not in (None, "active"):
            continue
        to_id = raw.get("to_dataset_id")
        if not isinstance(to_id, uuid.UUID):
            continue
        if to_id == fact_dataset_id:
            continue
        prev = best.get(to_id)
        cols = list(raw.get("from_columns") or [])
        if prev is None or _prefers(cols, list(prev.get("from_columns") or [])):
            best[to_id] = raw

    # No dimensions in the body: bootstrap the mart from every active FK of the fact (real
    # catalog data, not a seed). Named dimensions keep theirs; the rest become suggestions.
    bootstrap = not list(dimensions or ())
    taken = _taken_names(dimensions or ())
    kept: list[dict[str, Any]] = [dict(d) for d in dimensions or [] if isinstance(d, Mapping)]
    suggested: list[dict[str, Any]] = []
    relations: list[dict[str, Any]] = []

    for to_id, rel in sorted(best.items(), key=lambda item: _name_of(datasets.get(item[0], {}))):
        target = datasets.get(to_id) or {}
        name = _name_of(target)
        key = _dim_key(list(rel.get("from_columns") or []))
        to_cols = list(rel.get("to_columns") or [])
        to_col = to_cols[0] if to_cols else "id"
        table = str(target.get("table_name") or name)
        edge = {"from": key, "to": f"{table}.{to_col}"}
        row = {
            "name": table,
            "key": key,
            "dataset_id": str(to_id),
            "columns": [key, *(to_cols[:1] or [])],
            "label": name,
        }
        aliases = {name.lower(), table.lower()}
        if bootstrap or aliases & taken:
            if bootstrap:
                kept.append(row)
            relations.append(edge)
            continue
        suggested.append({**row, "suggested_by": "relationship"})

    if kept and not relations:
        for to_id, rel in best.items():
            target = datasets.get(to_id) or {}
            name = _name_of(target)
            table = str(target.get("table_name") or name)
            if {name.lower(), table.lower()} & taken:
                key = _dim_key(list(rel.get("from_columns") or []))
                to_cols = list(rel.get("to_columns") or [])
                to_col = to_cols[0] if to_cols else "id"
                relations.append({"from": key, "to": f"{table}.{to_col}"})

    return kept, suggested, relations


def relations_match_active(
    *,
    fact_dataset_id: uuid.UUID,
    relations: Sequence[Any],
    relationships: Sequence[Mapping[str, Any]],
    datasets: Mapping[uuid.UUID, Mapping[str, Any]],
) -> tuple[bool, list[str]]:
    """True when every declared mart relation matches an active FK of the fact."""
    if not relations:
        return True, []
    allowed: set[tuple[str, str]] = set()
    for raw in relationships:
        if raw.get("from_dataset_id") != fact_dataset_id:
            continue
        if raw.get("status") not in (None, "active"):
            continue
        to_id = raw.get("to_dataset_id")
        if not isinstance(to_id, uuid.UUID):
            continue
        target = datasets.get(to_id) or {}
        table = str(target.get("table_name") or "")
        from_cols = [str(c) for c in (raw.get("from_columns") or [])]
        to_cols = [str(c) for c in (raw.get("to_columns") or [])]
        if from_cols and to_cols and table:
            allowed.add((from_cols[0], f"{table}.{to_cols[0]}"))
    failures: list[str] = []
    for item in relations:
        if not isinstance(item, Mapping):
            failures.append("relation is an object")
            continue
        frm = str(item.get("from") or "")
        to = str(item.get("to") or "")
        if (frm, to) not in allowed:
            failures.append(f"{frm} → {to} is not an active relationship of the fact")
    return not failures, failures
