"""Filtering rules applied to parsed silver chunks."""
from __future__ import annotations

from typing import Any

from libs.basemodel.services_databaseconnector.postgres_model import (
    PolicyFilteringType,
    ReadJoinRequest,
    WhereFilter,
)
from libs.database_connector.postgres_connector import PostgresClient
from libs.ingestion.silver.shared import _llm_generate_json
from libs.ingestion.pipeline_events import NULL_EMITTER, PipelineEventEmitter


def _exact_match_blocked(content: str, banned_phrases: list[str]) -> str | None:
    lower = content.lower()
    for phrase in banned_phrases:
        if phrase.lower() in lower:
            return phrase
    return None


async def _llm_policy_blocked(content: str, nlp_rules: list[str]) -> bool:
    if not nlp_rules:
        return False
    rules_text = "\n".join(f"- {rule}" for rule in nlp_rules)
    result = await _llm_generate_json(
        system_type="moderate",
        user=f"Rules:\n{rules_text}\n\nChunk:\n{content[:2000]}",
        max_tokens=128,
    )
    return bool(result.get("blocked", False))


async def _filter_chunks(
    postgres: PostgresClient,
    tenant_id: str,
    chunks: list[dict[str, Any]],
    emitter: PipelineEventEmitter = NULL_EMITTER,
) -> list[dict[str, Any]]:
    policies_response = await postgres.read(ReadJoinRequest(
        tenant_id=tenant_id,
        joins_table=["KBFilterPolicy"],
        filters=[
            WhereFilter(
                table_name="KBFilterPolicy",
                column_name="is_active",
                value=True,
            )
        ],
        limit=100,
    ))

    exact_phrases: list[str] = []
    if policies_response.code == 200:
        for policy in policies_response.data or []:
            policy_config = policy.get("config") or {}
            raw_rules = policy_config.get("rules") or []
            rule_values = [
                rule["value"] if isinstance(rule, dict) and "value" in rule else str(rule)
                for rule in raw_rules
            ]
            if policy.get("configformat") == PolicyFilteringType.EXACT_MATCH.value:
                exact_phrases.extend(rule_values)

    accepted_chunks: list[dict[str, Any]] = []
    for chunk in chunks:
        content = chunk["content"]
        block_index = chunk["block_index"]
        matched_phrase = _exact_match_blocked(content, exact_phrases) if exact_phrases else None
        if matched_phrase:
            await emitter.emit("silver.filter.blocked", {
                "index": block_index,
                "method": "exact_match",
                "reason": f"Matched banned phrase: '{matched_phrase}'",
                "phrase": matched_phrase,
                "preview": content[:200],
                "length": len(content),
                "table_involved": chunk.get("table_involved", False),
            })
            continue

        await emitter.emit("silver.filter.accepted", {"index": block_index})
        accepted_chunks.append(chunk)
    return accepted_chunks
