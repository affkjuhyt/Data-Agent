"""Shared LLM and chunk-normalization helpers for the silver pipeline."""
from __future__ import annotations

import io
import json
import logging
import re
from typing import Any, cast

from libs.ingestion import llm_client
from libs.ingestion.prompts import SILVER_SYSTEM_PROMPTS as SYSTEM_PROMPTS

log = logging.getLogger(__name__)

_JSON_FENCE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)
_JSON_OBJ = re.compile(r"\{[\s\S]*\}", re.DOTALL)


async def _llm_generate_json(
    system_type: str, user: str, max_tokens: int = 512, extra_instructions: str = "",
) -> dict[str, Any]:
    """`extra_instructions`, when set, is the tenant's Policy Center
    "Custom Extraction Addendum" (`KBExtractionPolicy.custom_override`) —
    appended after the base system prompt so a steward's rule augments, never
    replaces, the built-in extraction behavior."""
    system_prompt = SYSTEM_PROMPTS[system_type]
    if extra_instructions:
        system_prompt = (
            f"{system_prompt}\n\nAdditional instructions from this tenant's "
            f"extraction policy:\n{extra_instructions}"
        )
    raw = await llm_client.generate_json(
        user,
        system_prompt=system_prompt,
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


def _ocr_image_bytes(image_bytes: bytes) -> str:
    """Extract embedded/printed text from an image via Tesseract — the same
    engine `docling_parser.py` uses for scanned PDFs (vie+eng), so a photo of
    a Vietnamese document reads the same whether it's uploaded as a PDF or as
    a standalone image. Sync (pytesseract shells out to the `tesseract`
    binary and blocks) — callers run this through `asyncio.to_thread`.

    This recognizes TEXT in the image, not general scene/object content —
    there is no VLM captioning model in this deployment (`VLM_MODEL`/
    `agent-main` is a text-only LLM, not vision-capable). An image with no
    readable text returns "", which callers treat as a legitimate zero-chunk
    result, not an error.
    """
    import pytesseract
    from PIL import Image

    image = Image.open(io.BytesIO(image_bytes))
    return cast(str, pytesseract.image_to_string(image, lang="vie+eng")).strip()


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


def _fallback_table_summary(table: dict[str, Any]) -> str:
    """A table-aware fallback: naive first-sentence truncation (below) cuts a
    markdown table at its first literal '.', which for a row label like
    "1.Nợ công..." lands right after the header — the summary that then
    drives reranking (see light_rag/pipeline.py's rerank()) ends up as just
    the table's own caption, indistinguishable from every other table's.
    Built from the table's own row labels instead, so it says what the table
    is actually about even when the LLM call below failed."""
    name = str(table.get("table_name") or "").strip()
    data = table.get("data") or {}
    headers = [str(h).strip() for h in (data.get("headers") or []) if str(h).strip()]
    rows = data.get("rows") or []
    row_labels = [str(row[0]).strip() for row in rows[:3] if row and str(row[0]).strip()]
    parts = [f"Bảng: {name}"] if name else ["Bảng dữ liệu"]
    if row_labels:
        parts.append("gồm " + "; ".join(row_labels))
    if headers:
        parts.append(f"({len(headers)} cột: {', '.join(headers[:6])})")
    return " ".join(parts)


def _fallback_chunk_summary(content: str, table: dict[str, Any] | None = None) -> dict[str, Any]:
    if table:
        summary = _fallback_table_summary(table)
    else:
        first_sentence = re.split(r"[.!?…]", content, maxsplit=1)[0].strip()
        summary = " ".join((first_sentence or content).split()[:40])
    return {
        "summary": summary,
        "intents": ["document_content"],
        "entities": [],
        "relationships": [],
        "skip": False,
    }


async def summarize_chunk(
    content: str, extraction_addendum: str = "", table: dict[str, Any] | None = None,
) -> dict[str, Any]:
    last_exc: Exception | None = None
    for _ in range(2):
        try:
            summary_result = await _llm_generate_json(
                system_type="chunk-summary",
                user=content[:3000],
                max_tokens=512,
                extra_instructions=extraction_addendum,
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
        except Exception as exc:  # noqa: BLE001 — any failure here falls through to the fallback below
            last_exc = exc
    # Previously silent (`except Exception: pass`): a chunk's rerank-driving
    # summary silently degraded to noise with no trace anywhere. 480 of 1504
    # chunks in one tenant's KB were found carrying this fallback (2026-09-27)
    # — retrying right after fixed nearly all of them, so this is a real,
    # frequent failure mode (summarization concurrency saturating the LLM
    # gateway), not a one-off.
    log.warning(
        "summarize_chunk | LLM summarization failed twice, using the fallback — last error: %s", last_exc,
    )
    return _fallback_chunk_summary(content, table)
