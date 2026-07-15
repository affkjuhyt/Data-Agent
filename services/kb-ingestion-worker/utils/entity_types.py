"""Closed entity-type vocabulary shared by the gold stage and the graph store.

Type is metadata, never node identity — a wrong LLM type must not split an entity
into two nodes. Each type maps to a Neo4j secondary label and to a per-type Qdrant
payload field (ent_person, ent_location, …) for typed filtered search.
"""
from __future__ import annotations

ENTITY_TYPE_LABELS: dict[str, str] = {
    "person":         "Person",
    "organization":   "Organization",
    "location":       "Location",
    "law_document":   "LawDocument",
    "system_product": "SystemProduct",
    "event":          "Event",
    "time_period":    "TimePeriod",
    "concept":        "Concept",
}


def to_entity_type(v: object) -> str:
    s = str(v or "").strip().lower()
    return s if s in ENTITY_TYPE_LABELS else "concept"


def entities_by_type_fields(
    type_by_canonical: dict[str, str],
    canonical_names: list[str],
) -> dict[str, list[str]]:
    fields: dict[str, list[str]] = {f"ent_{t}": [] for t in ENTITY_TYPE_LABELS}
    for can in canonical_names:
        fields[f"ent_{to_entity_type(type_by_canonical.get(can))}"].append(can)
    return fields
