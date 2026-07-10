"""JSON-output schemas the ingestion pipeline asks the LLM for, keyed by system_type.

This is DOMAIN knowledge (the shape each gold/silver/conflict call wants back), not
transport — it stays in the worker. `llm_client` looks a schema up here and forwards
it to the model-service /generate gateway as `json_schema`; the gateway applies it as
the served model's response_format. Adding a new structured call = add a schema + key.
"""
from __future__ import annotations

from typing import Any

_ENTITY_INTENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "entities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name":        {"type": "string"},
                    "description": {"type": "string"},
                    "score":       {"type": "number"},
                },
                "required": ["name", "description", "score"],
                "additionalProperties": False,
            },
        },
        "intents": {"type": "array", "items": {"type": "string"}},
        "skip":    {"type": "boolean"},
    },
    "required": ["summary", "entities", "intents", "skip"],
    "additionalProperties": False,
}

_RELATIONSHIP_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "relationships": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "from":  {"type": "string"},
                    "to":    {"type": "string"},
                    "type":  {"type": "string"},
                    "score": {"type": "number"},
                },
                "required": ["from", "to", "type", "score"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["relationships"],
    "additionalProperties": False,
}

_CONFLICT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "has_conflict":  {"type": "boolean"},
        "conflict_type": {"type": "string"},
        "severity":      {"type": "string"},
        "explanation":   {"type": "string"},
    },
    "required": ["has_conflict", "conflict_type", "severity", "explanation"],
    "additionalProperties": False,
}

_MODERATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "blocked": {"type": "boolean"},
        "reason":  {"type": "string"},
    },
    "required": ["blocked", "reason"],
    "additionalProperties": False,
}

# Silver per-chunk call: summary + per-chunk entities (in the chunk's own
# language) + intents + skip flag. Entities are extracted PER CHUNK here so each
# chunk's Qdrant point carries its own entity set (gold canonicalizes + stamps
# them per chunk; the document graph is the union across chunks).
_CHUNK_SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "entities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name":        {"type": "string"},
                    "type":        {"type": "string"},
                    "description": {"type": "string"},
                    "score":       {"type": "number"},
                },
                "required": ["name", "type", "description", "score"],
                "additionalProperties": False,
            },
        },
        # Relationships BETWEEN this chunk's entities, grounded in the chunk text
        # (the evidence lives here, not in the lossy doc summary). from/to must be
        # names from `entities`. Gold remaps them to canonical names and a final
        # doc-level pass reconciles the union across chunks.
        "relationships": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "from":  {"type": "string"},
                    "to":    {"type": "string"},
                    "type":  {"type": "string"},
                    "score": {"type": "number"},
                },
                "required": ["from", "to", "type", "score"],
                "additionalProperties": False,
            },
        },
        "intents": {"type": "array", "items": {"type": "string"}},
        "skip":    {"type": "boolean"},
    },
    "required": ["summary", "entities", "relationships", "intents", "skip"],
    "additionalProperties": False,
}

# Gold map-reduce pass: one batch of chunk summaries → one combined summary.
_DOC_REDUCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"summary": {"type": "string"}},
    "required": ["summary"],
    "additionalProperties": False,
}

# Gold document-level entity extraction. `aliases` carries other surface forms of
# the SAME entity (critical for Vietnamese synonyms: "TP.HCM" / "Sài Gòn" /
# "Thành phố Hồ Chí Minh") so the entity registry can learn them as exact lookups.
# `type` is a closed vocabulary — it becomes the Neo4j secondary label, but NEVER
# part of node identity (LLM typing is noisy; identity must not depend on it).
ENTITY_TYPES = [
    "person", "organization", "location", "law_document",
    "system_product", "event", "time_period", "concept",
]

_DOC_ENTITY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "entities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name":        {"type": "string"},
                    "type":        {"type": "string", "enum": ENTITY_TYPES},
                    "aliases":     {"type": "array", "items": {"type": "string"}},
                    "description": {"type": "string"},
                    "score":       {"type": "number"},
                },
                "required": ["name", "type", "aliases", "description", "score"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["entities"],
    "additionalProperties": False,
}

# Registry merge verification: single same-entity judgment for gray-band
# embedding matches (cosine alone cannot separate e.g. Bangladesh from Ấn Độ).
_MERGE_VERIFY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"same": {"type": "boolean"}},
    "required": ["same"],
    "additionalProperties": False,
}

# Alias verification: pair-by-pair binary judgments (far more reliable than
# asking the model to re-generate a cleaned list).
_ALIAS_VERIFY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "pairs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name":  {"type": "string"},
                    "alias": {"type": "string"},
                    "same":  {"type": "boolean"},
                },
                "required": ["name", "alias", "same"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["pairs"],
    "additionalProperties": False,
}

SCHEMAS_BY_SYSTEM_TYPE: dict[str, dict[str, Any]] = {
    "entity-intent": _ENTITY_INTENT_SCHEMA,
    "relationship":  _RELATIONSHIP_SCHEMA,
    "conflict":      _CONFLICT_SCHEMA,
    "moderate":      _MODERATE_SCHEMA,
    "chunk-summary": _CHUNK_SUMMARY_SCHEMA,
    "doc-reduce":    _DOC_REDUCE_SCHEMA,
    "doc-entity":    _DOC_ENTITY_SCHEMA,
    "alias-verify":  _ALIAS_VERIFY_SCHEMA,
    "merge-verify":  _MERGE_VERIFY_SCHEMA,
}
