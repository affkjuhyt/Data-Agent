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
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "score": {"type": "number"},
                },
                "required": ["name", "description", "score"],
                "additionalProperties": False,
            },
        },
        "intents": {"type": "array", "items": {"type": "string"}},
        "skip": {"type": "boolean"},
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
                    "from": {"type": "string"},
                    "to": {"type": "string"},
                    "type": {"type": "string"},
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

# Reasoning-pivot conflict pipeline, stage 1 (single prompt): every distinct
# question a chunk answers, each with its own intent/entities/pivot chain.
_PIVOT_QUESTION_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "question": {"type": "string"},
        "question_intent": {"type": "string"},
        "question_entities": {"type": "array", "items": {"type": "string"}},
        "pivots": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["question", "question_intent", "question_entities", "pivots"],
    "additionalProperties": False,
}

_PIVOT_QUESTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"questions": {"type": "array", "items": _PIVOT_QUESTION_ITEM_SCHEMA}},
    "required": ["questions"],
    "additionalProperties": False,
}

# Stage 3a: the pivot chain a candidate chunk offers toward a fixed question.
_PIVOT_CHUNK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"pivots": {"type": "array", "items": {"type": "string"}}},
    "required": ["pivots"],
    "additionalProperties": False,
}

# Stage 3b: score the incoming vs. candidate pivot chains across the twelve
# conflict criteria (see utils.prompts for definitions).
_PIVOT_COMPARE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "semantic_ambiguity_score": {"type": "number"},
        "semantic_multilingual_score": {"type": "number"},
        "temporal_time_score": {"type": "number"},
        "temporal_version_score": {"type": "number"},
        "factual_entities_score": {"type": "number"},
        "factual_relationships_score": {"type": "number"},
        "factual_quantity_score": {"type": "number"},
        "reasoning_logic_score": {"type": "number"},
        "constraint_policy_score": {"type": "number"},
        "missing_information_score": {"type": "number"},
        "duplicate_score": {"type": "number"},
        "irrelevant_score": {"type": "number"},
    },
    "required": [
        "semantic_ambiguity_score", "semantic_multilingual_score",
        "temporal_time_score", "temporal_version_score",
        "factual_entities_score", "factual_relationships_score", "factual_quantity_score",
        "reasoning_logic_score", "constraint_policy_score", "missing_information_score",
        "duplicate_score", "irrelevant_score",
    ],
    "additionalProperties": False,
}

_MODERATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "blocked": {"type": "boolean"},
        "reason": {"type": "string"},
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
                    "name": {"type": "string"},
                    "type": {"type": "string"},
                    "description": {"type": "string"},
                    "score": {"type": "number"},
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
                    "from": {"type": "string"},
                    "to": {"type": "string"},
                    "type": {"type": "string"},
                    "score": {"type": "number"},
                },
                "required": ["from", "to", "type", "score"],
                "additionalProperties": False,
            },
        },
        "intents": {"type": "array", "items": {"type": "string"}},
        "skip": {"type": "boolean"},
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
                    "name": {"type": "string"},
                    "type": {"type": "string", "enum": ENTITY_TYPES},
                    "aliases": {"type": "array", "items": {"type": "string"}},
                    "description": {"type": "string"},
                    "score": {"type": "number"},
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
                    "name": {"type": "string"},
                    "alias": {"type": "string"},
                    "same": {"type": "boolean"},
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
    "relationship": _RELATIONSHIP_SCHEMA,
    "pivot-question": _PIVOT_QUESTION_SCHEMA,
    "pivot-chunk": _PIVOT_CHUNK_SCHEMA,
    "pivot-compare": _PIVOT_COMPARE_SCHEMA,
    "moderate": _MODERATE_SCHEMA,
    "chunk-summary": _CHUNK_SUMMARY_SCHEMA,
    "doc-reduce": _DOC_REDUCE_SCHEMA,
    "doc-entity": _DOC_ENTITY_SCHEMA,
    "alias-verify": _ALIAS_VERIFY_SCHEMA,
    "merge-verify": _MERGE_VERIFY_SCHEMA,
}
