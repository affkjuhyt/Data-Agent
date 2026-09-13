from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, NamedTuple

from libs.database_connector.model_connector import ModelServiceClient
from libs.database_connector.postgres_connector import PostgresClient
from qdrant_client.async_qdrant_client import AsyncQdrantClient

from libs.ingestion import config as cfg
from libs.ingestion import entity_registry, llm_client
from libs.ingestion.entity_types import (
    entities_by_type_fields,
    to_entity_type as _to_entity_type,
)
from libs.ingestion.prompts import GOLD_SYSTEM_PROMPTS as SYSTEM_PROMPTS

log = logging.getLogger(__name__)

_JSON_FENCE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)
_JSON_OBJECT = re.compile(r"\{[\s\S]*\}", re.DOTALL)


class QuestionPivots(NamedTuple):
    question: str
    intent: str
    entities: list[str]
    pivots: list[str]


class QuestionPipelineResult(NamedTuple):
    question_pivots: QuestionPivots
    candidate_hits: list
    qualifying: list[tuple[Any, dict]]


@dataclass(frozen=True, slots=True)
class ChunkEntities:
    canonicals: list[str]
    type_by_canonical: dict[str, str]
    description_by_canonical: dict[str, str]
    display_by_canonical: dict[str, str]
    raw_to_canonical: dict[str, str]

    def graph_nodes(self) -> list[dict]:
        return [
            {
                "canonical": canonical,
                "display": self.display_by_canonical.get(canonical, canonical),
                "description": self.description_by_canonical.get(canonical, ""),
                "type": self.type_by_canonical.get(canonical, "concept"),
            }
            for canonical in self.canonicals
        ]

    def qdrant_type_fields(self) -> dict:
        return entities_by_type_fields(self.type_by_canonical, self.canonicals) if self.canonicals else {}


def _fold(value: str) -> str:
    return unicodedata.normalize("NFC", value).casefold().strip()


def _json_hint(raw: str, exc: json.JSONDecodeError) -> str:
    message = str(exc).lower()
    if "unterminated" in message or (exc.pos and exc.pos >= len(raw) - 5):
        return (
            f"your response was cut off because it was too long — return FEWER items: "
            f"at most {cfg.MAX_DOC_ENTITIES} entities and {cfg.MAX_DOC_RELATIONSHIPS} "
            f"relationships, each with a description under 12 words, so the JSON closes properly"
        )
    if "expecting" in message or "invalid" in message:
        return f"syntax error near position {exc.pos} — return only a valid JSON object, no extra text"
    return "response is not valid JSON — return only the JSON object with no markdown or extra text"


async def _llm_generate_json(
    system_prompt: str,
    user: str,
    max_tokens: int = 1024,
    temperature: float = 0.1,
    max_retries: int = 2,
    system_type: str = "",
) -> tuple[dict[str, Any], str, int]:
    current_user = user
    last_hint = ""

    for attempt in range(max_retries):
        raw = await llm_client.generate_json(
            current_user,
            system_prompt=system_prompt,
            system_type=system_type,
            max_tokens=max_tokens,
            temperature=temperature,
        )

        fenced = _JSON_FENCE.search(raw)
        if fenced:
            candidate = fenced.group(1).strip()
        else:
            obj_match = _JSON_OBJECT.search(raw)
            candidate = obj_match.group(0) if obj_match else raw.strip()

        candidate = candidate.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")

        try:
            return json.loads(candidate), raw, attempt + 1
        except json.JSONDecodeError as exc:
            last_hint = _json_hint(raw, exc)
            current_user = f"{user}\n\nNote: {last_hint}."

    raise ValueError(f"LLM failed to return valid JSON after {max_retries} attempts: {last_hint}")


async def _retry_while_empty(produce_result, max_attempts: int) -> list:
    result: list = []
    for _ in range(max_attempts):
        result = await produce_result()
        if result:
            return result
    return result


async def verify_merge_pair(name_a: str, desc_a: str, name_b: str, desc_b: str) -> bool:
    user = f'A: "{name_a}" — {desc_a or "(không có mô tả)"}\nB: "{name_b}" — {desc_b or "(không có mô tả)"}'
    try:
        result, _, _ = await _llm_generate_json(
            SYSTEM_PROMPTS["merge-verify"],
            user,
            max_tokens=16,
            system_type="merge-verify",
        )
        return bool(result.get("same", False))
    except Exception:
        return False


def _to_score(value: Any, default: float = 0.5) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return default
    if score != score:
        return default
    return max(0.0, min(1.0, score))


def _to_entity_list(raw_value: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_value, list):
        return []

    parsed_entities: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for item in raw_value:
        if isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            entity_type = _to_entity_type(item.get("type"))
            description = str(item.get("description") or "").strip()
            score = _to_score(item.get("score"))
            aliases = [
                alias.strip()
                for alias in (item.get("aliases") or [])
                if isinstance(alias, str) and alias.strip()
            ]
        elif isinstance(item, str):
            name, entity_type, description, score, aliases = item.strip(), "concept", "", 0.5, []
        else:
            continue

        if not name or len(name) < 2:
            continue

        aliases = [alias for alias in aliases if _fold(alias) not in _fold(alias) != _fold(name)]
        folded_name = _fold(name)
        if folded_name in seen_names:
            continue
        seen_names.add(folded_name)
        parsed_entities.append(
            {
                "name": name,
                "type": entity_type,
                "aliases": aliases,
                "description": description,
                "score": score,
            }
        )

    all_folded_names = {_fold(entity["name"]) for entity in parsed_entities}
    for entity in parsed_entities:
        entity["aliases"] = [alias for alias in entity["aliases"] if _fold(alias) not in all_folded_names]
    return parsed_entities


async def canonicalize_chunk_entities(
    qdrant: AsyncQdrantClient,
    postgres: PostgresClient,
    model: ModelServiceClient,
    tenant_id: str,
    raw_entities: list[dict[str, Any]],
    *,
    knowledge_base: str = "",
    embed_model_id: str = "",
) -> ChunkEntities:
    raw_entities_parsed = _to_entity_list(raw_entities or [])
    if not raw_entities_parsed:
        return ChunkEntities([], {}, {}, {}, {})

    canonicals, details = await entity_registry.normalize_entities_detailed(
        qdrant,
        postgres,
        model,
        tenant_id,
        raw_entities_parsed,
        verify_fn=verify_merge_pair,
        knowledge_base=knowledge_base,
        embed_model_id=embed_model_id,
    )

    raw_to_canonical: dict[str, str] = {}
    display_by_canonical: dict[str, str] = {}
    for detail in details:
        canonical = detail["canonical"]
        for surface_form in [detail["raw"], *detail.get("aliases", [])]:
            raw_to_canonical[_fold(surface_form)] = canonical
        display_by_canonical.setdefault(canonical, detail["raw"])

    type_by_canonical: dict[str, str] = {}
    description_by_canonical: dict[str, str] = {}
    for raw_entity in raw_entities_parsed:
        canonical = raw_to_canonical.get(_fold(raw_entity["name"]))
        if not canonical:
            continue
        type_by_canonical.setdefault(canonical, raw_entity.get("type", "concept"))
        description_by_canonical.setdefault(canonical, raw_entity.get("description", ""))

    return ChunkEntities(
        canonicals=canonicals,
        type_by_canonical=type_by_canonical,
        description_by_canonical=description_by_canonical,
        display_by_canonical=display_by_canonical,
        raw_to_canonical=raw_to_canonical,
    )


def remap_chunk_relationships(
    relationships: list[dict[str, Any]] | None,
    raw_to_canonical: dict[str, str],
) -> list[dict[str, Any]]:
    remapped: list[dict[str, Any]] = []
    for relationship in relationships or []:
        canonical_from = raw_to_canonical.get(_fold(str(relationship.get("from", ""))))
        canonical_to = raw_to_canonical.get(_fold(str(relationship.get("to", ""))))
        relationship_type = str(relationship.get("type", "")).strip()
        if not (canonical_from and canonical_to and relationship_type) or canonical_from == canonical_to:
            continue
        remapped.append(
            {
                "from": canonical_from,
                "to": canonical_to,
                "type": relationship_type,
                "score": _to_score(relationship.get("score")),
            }
        )
    return remapped


def _heuristic_summary(content: str) -> str:
    first_sentence = re.split(r"[.!?…]", content, maxsplit=1)[0].strip()
    return " ".join((first_sentence or content).split()[:40])
