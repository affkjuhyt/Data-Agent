"""JSON Schemas for vLLM structured outputs.

Functional equivalents of the GBNF grammars in
infra/kb-ingestion-worker/llama_client.py (ENTITY_INTENT_GBNF / RELATIONSHIP_GBNF).

With llama.cpp the JSON shape was enforced by a hand-written GBNF grammar; with
vLLM the same token-masking enforcement (xgrammar) is driven by a standard JSON
Schema passed as `response_format={"type": "json_schema", ...}` on
/v1/chat/completions. The model output is guaranteed to parse and to match the
schema — no retry-on-bad-JSON needed.
"""
from __future__ import annotations

from typing import Any

# Stage 1: summary, entities (with descriptions), intents — no relationships.
# Entity descriptions are embedded and stored on Neo4j entity nodes, same as
# the GBNF version.
ENTITY_INTENT_SCHEMA: dict[str, Any] = {
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
                },
                "required": ["name", "description"],
                "additionalProperties": False,
            },
        },
        "intents": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["summary", "entities", "intents"],
    "additionalProperties": False,
}

# Stage 2: relationships only — the prompt supplies the stage-1 entity list so
# the model can only connect entities it was explicitly given.
RELATIONSHIP_SCHEMA: dict[str, Any] = {
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
                },
                "required": ["from", "to", "type"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["relationships"],
    "additionalProperties": False,
}


def response_format(name: str, schema: dict[str, Any]) -> dict[str, Any]:
    """Wrap a schema in the OpenAI `response_format` envelope vLLM expects."""
    return {
        "type": "json_schema",
        "json_schema": {"name": name, "strict": True, "schema": schema},
    }
