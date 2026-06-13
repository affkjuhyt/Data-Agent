"""Standalone smoke test for vllm_client — verifies the worker can reach the vLLM
server configured via env and that every system_type schema is enforced.

Usage (VLLM_BASE_URL / VLLM_API_KEY from env, same as the worker):

    set VLLM_BASE_URL=https://xxx.trycloudflare.com
    set VLLM_API_KEY=kb-...
    py infra/kb-ingestion-worker/test_vllm_client.py

Only needs httpx — none of the worker's DB/Kafka dependencies. Exit 0 = all passed.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
if _here not in sys.path:
    sys.path.insert(0, _here)

import vllm_client  # noqa: E402

SAMPLE_CHUNK = (
    "RAG combines BART with DPR to retrieve passages from a Wikipedia dense index. "
    "The system is fine-tuned end-to-end on Natural Questions and TriviaQA."
)

# Compact stand-ins for gold.SYSTEM_PROMPTS (importing gold pulls DB deps) — same
# field contract: entities carry score, top-level skip, relationships carry score.
_ENTITY_SYS = (
    "Extract from the text: a one-sentence summary; up to 5 named entities, each as "
    '{"name","description","score"} where score 0.0-1.0 is centrality; 2-4 snake_case '
    'intents; and "skip": true only if the chunk is boilerplate. Respond in JSON.'
)
_REL_SYS = (
    "Given ENTITIES and TEXT, output relationships only between listed entities as "
    '{"relationships":[{"from","to","type","score"}]} — type is UPPER_SNAKE_CASE, '
    "score 0.0-1.0. Respond in JSON."
)
_CONFLICT_SYS = (
    "Compare two knowledge summaries. Output JSON with keys: has_conflict (bool), "
    "conflict_type (content_duplicate|content_contradiction|content_conflict|content_update), "
    "severity (low|medium|high), explanation (max 20 words)."
)


async def test_entity_intent() -> None:
    raw = await vllm_client.generate_json(
        SAMPLE_CHUNK, system_prompt=_ENTITY_SYS, system_type="entity-intent",
        max_tokens=1024, temperature=0.1,
    )
    doc = json.loads(raw)
    assert isinstance(doc["summary"], str) and doc["summary"]
    assert isinstance(doc["skip"], bool)
    assert isinstance(doc["intents"], list) and all(isinstance(i, str) for i in doc["intents"])
    for ent in doc["entities"]:
        assert isinstance(ent["name"], str) and isinstance(ent["description"], str)
        assert isinstance(ent["score"], (int, float)) and 0.0 <= ent["score"] <= 1.0
    print(f"   entities: {[e['name'] for e in doc['entities']]}")
    print(f"   intents : {doc['intents']}  skip={doc['skip']}")


async def test_relationships() -> None:
    entities = ["RAG", "BART", "DPR", "Natural Questions", "TriviaQA"]
    raw = await vllm_client.generate_json(
        f"ENTITIES: {json.dumps(entities)}\n\nTEXT:\n{SAMPLE_CHUNK}",
        system_prompt=_REL_SYS, system_type="relationship",
        max_tokens=1024, temperature=0.1,
    )
    doc = json.loads(raw)
    assert isinstance(doc["relationships"], list)
    for rel in doc["relationships"]:
        assert {"from", "to", "type", "score"} <= rel.keys()
        assert isinstance(rel["score"], (int, float))
    for rel in doc["relationships"][:5]:
        print(f"   ({rel['from']}) -[{rel['type']}]-> ({rel['to']})  score={rel['score']}")


async def test_conflict() -> None:
    raw = await vllm_client.generate_json(
        "Existing chunk summary:\nRAG fine-tunes BART and DPR on Wikipedia for QA.\n\n"
        "Incoming chunk summary:\nRAG jointly trains BART and DPR on Wikipedia retrieval for QA.",
        system_prompt=_CONFLICT_SYS, system_type="conflict",
        max_tokens=128, temperature=0.1,
    )
    doc = json.loads(raw)
    assert isinstance(doc["has_conflict"], bool)
    assert isinstance(doc["conflict_type"], str)
    assert isinstance(doc["severity"], str)
    print(f"   has_conflict={doc['has_conflict']} type={doc['conflict_type']} severity={doc['severity']}")


async def test_chat_plain() -> None:
    text = await vllm_client.chat_plain(
        messages=[
            {"role": "system", "content": "Merge the two text chunks into one concise paragraph."},
            {"role": "user", "content": f"Chunk A:\n{SAMPLE_CHUNK}\n\nChunk B:\nRAG was proposed by Lewis et al. in 2020."},
        ],
        max_tokens=200, temperature=0.2,
    )
    assert text.strip()
    print(f"   merged: {text.strip()[:100]}...")


async def main() -> int:
    import config as cfg
    if not cfg.VLLM_BASE_URL:
        print("VLLM_BASE_URL is not set — aborting")
        return 1
    print(f"target : {cfg.VLLM_BASE_URL}")
    print(f"backend: {cfg.LLM_BACKEND}")

    failures = 0
    for label, fn in [
        ("entity-intent schema (gold stage 1)", test_entity_intent),
        ("relationship schema (gold stage 2)", test_relationships),
        ("conflict schema (gold step D)", test_conflict),
        ("chat_plain (conflict merge)", test_chat_plain),
    ]:
        print(f"\n=> {label}")
        try:
            await fn()
            print("   PASS")
        except Exception as exc:
            failures += 1
            print(f"   FAIL: {type(exc).__name__}: {exc}")

    print(f"\n{'ALL PASSED' if failures == 0 else f'{failures} FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
