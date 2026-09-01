"""Shared LLM and chunk-normalization helpers for the silver pipeline."""
from __future__ import annotations

import json
import re
from typing import Any, cast

from services.kb_ingestion_worker.utils import llm_client
from services.kb_ingestion_worker.utils.prompts import SILVER_SYSTEM_PROMPTS as SYSTEM_PROMPTS

_JSON_FENCE = re.compile(r"```(?:json)?\\s*([\\s\\S]*?)```", re.IGNORECASE)
_JSON_OBJ = re.compile(r"\\{[\\s\\S]*\\}", re.DOTALL)


async def _llm_generate_json(system_type: str, user: str, max_tokens: int = 512) -> dict[str, Any]:
    raw = await llm_client.generate_json(
        user,
        system_prompt=SYSTEM_PROMPTS[system_type],
        system_type=system_type,
        max_tokens=max_tokens,
        temperature=0.1,
    )
    match = _JSON_FENCE.search(raw)
    cleaned = match.group(1).strip() if match else raw.strip()
    try:
        return cast(dict[str, Any], json.loads(cleaned))
    except json.JSONDecodeError:
        object_match = _JSON_OBJ.search(cleaned)
        if object_match:
            try:
                return cast(dict[str, Any], json.loads(object_match.group(0)))
            except json.JSONDecodeError:
                pass
        return {}


async def _vlm_describe(image_bytes: bytes, content_type: str = "image/jpeg") -> str:
    raise NotImplementedError(
        "VLM describe requires USE_VLM=true and a dedicated VLM endpoint. "
        "Set USE_VLM=false to skip image/video processing."
    )


def _strip_surrogates(text: str) -> str:
    if not text:
        return text
    return "".join(char for char in text if not 0xD800 <= ord(char) <= 0xDFFF)


def _clean_chunk_entities(raw_entities: Any) -> list[dict[str, Any]]:
    cleaned_entities: list[dict[str, Any]] = []
    for entity in raw_entities or []:
        if not isinstance(entity, dict):
            continue
        name = _strip_surrogates(str(entity.get("name") or "").strip())
        if len(name) < 2:
            continue
        cleaned_entities.append({
            "name": name,
            "type": (str(entity.get("type") or "concept").strip() or "concept"),
            "description": _strip_surrogates(str(entity.get("description") or "").strip()),
            "aliases": [],
            "score": entity.get("score", 0.5),
        })
    return cleaned_entities


def _clean_chunk_relationships(raw_relationships: Any, entity_names: list[str]) -> list[dict[str, Any]]:
    allowed_names = {_strip_surrogates(name).strip().lower() for name in entity_names}
    cleaned_relationships: list[dict[str, Any]] = []
    for relationship in raw_relationships or []:
        if not isinstance(relationship, dict):
            continue
        source = _strip_surrogates(str(relationship.get("from") or "").strip())
        target = _strip_surrogates(str(relationship.get("to") or "").strip())
        rel_type = str(relationship.get("type") or "").strip()
        if not (source and target and rel_type) or source == target:
            continue
        if source.lower() not in allowed_names or target.lower() not in allowed_names:
            continue
        cleaned_relationships.append({
            "from": source,
            "to": target,
            "type": rel_type,
            "score": relationship.get("score", 0.5),
        })
    return cleaned_relationships


def _fallback_chunk_summary(content: str) -> dict[str, Any]:
    first_sentence = re.split(r"[.!?…]", content, maxsplit=1)[0].strip()
    summary = " ".join((first_sentence or content).split()[:40])
    return {
        "summary": summary,
        "intents": ["document_content"],
        "entities": [],
        "relationships": [],
        "skip": False,
    }


async def summarize_chunk(content: str) -> dict[str, Any]:
    for _ in range(2):
        try:
            summary_result = await _llm_generate_json(
                system_type="chunk-summary",
                user=content[:3000],
                max_tokens=512,
            )
            summary = _strip_surrogates(str(summary_result.get("summary") or "").strip())
            intents = [
                intent for intent in summary_result.get("intents") or []
                if isinstance(intent, str) and intent.strip()
            ]
            if summary:
                entities = _clean_chunk_entities(summary_result.get("entities"))
                return {
                    "summary": summary,
                    "intents": intents or ["document_content"],
                    "entities": entities,
                    "relationships": _clean_chunk_relationships(
                        summary_result.get("relationships"),
                        [entity["name"] for entity in entities],
                    ),
                    "skip": bool(summary_result.get("skip", False)),
                }
        except Exception:
            pass
    return _fallback_chunk_summary(content)
