"""Smoke-test a remote vLLM + Qwen endpoint exposed through a Cloudflare tunnel.

Run the Colab notebook (vllm_qwen_colab.ipynb) first; it prints TUNNEL_URL and
API_KEY. Then from the repo root:

    python model/vllm/test_endpoint.py https://xxx.trycloudflare.com --api-key kb-...

Stdlib only (urllib) — no extra dependencies. Exit code 0 = all tests passed.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from schemas import ENTITY_INTENT_SCHEMA, RELATIONSHIP_SCHEMA, response_format  # noqa: E402

SAMPLE_CHUNK = (
    "AeroFlow's Q3 revenue grew 18% year-over-year after the new Singapore hub "
    "came online in July, CFO Mira Chen told analysts. The hub, operated jointly "
    "with Changi Logistics, now handles 40% of APAC cargo volume."
)


def _request(base: str, path: str, api_key: str, payload: dict[str, Any] | None = None,
             timeout: float = 180.0) -> dict[str, Any]:
    req = urllib.request.Request(
        base.rstrip("/") + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST" if payload is not None else "GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _chat(base: str, api_key: str, model: str, system: str, user: str,
          fmt: dict[str, Any] | None, max_tokens: int = 512) -> str:
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.1,
    }
    if fmt is not None:
        payload["response_format"] = fmt
    data = _request(base, "/v1/chat/completions", api_key, payload)
    content: str = data["choices"][0]["message"]["content"]
    if not content:
        raise RuntimeError("empty content in response")
    return content


def test_models(base: str, api_key: str) -> str:
    data = _request(base, "/v1/models", api_key)
    models = [m["id"] for m in data.get("data", [])]
    assert models, "no models served"
    print(f"   served models: {models}")
    return models[0]


def test_json_object(base: str, api_key: str, model: str) -> None:
    content = _chat(
        base, api_key, model,
        "Respond with a JSON object containing a single key 'answer'.",
        "What is 2 + 2? JSON only.",
        {"type": "json_object"},
        max_tokens=50,
    )
    doc = json.loads(content)
    assert isinstance(doc, dict), f"expected object, got {type(doc)}"
    print(f"   {content.strip()}")


def test_entity_intent(base: str, api_key: str, model: str) -> None:
    content = _chat(
        base, api_key, model,
        "Extract a one-sentence summary, named entities (each with a short "
        "description) and user intents from the text. Respond in JSON.",
        SAMPLE_CHUNK,
        response_format("entity_intent", ENTITY_INTENT_SCHEMA),
    )
    doc = json.loads(content)
    assert isinstance(doc.get("summary"), str) and doc["summary"], "bad summary"
    assert isinstance(doc.get("entities"), list), "bad entities"
    for ent in doc["entities"]:
        assert isinstance(ent.get("name"), str) and isinstance(ent.get("description"), str), \
            f"bad entity: {ent}"
    assert isinstance(doc.get("intents"), list), "bad intents"
    assert all(isinstance(i, str) for i in doc["intents"]), "bad intent item"
    names = [e["name"] for e in doc["entities"]]
    print(f"   summary : {doc['summary'][:80]}")
    print(f"   entities: {names}")
    print(f"   intents : {doc['intents']}")


def test_relationships(base: str, api_key: str, model: str) -> None:
    entities = ["AeroFlow", "Singapore hub", "Mira Chen", "Changi Logistics"]
    content = _chat(
        base, api_key, model,
        "Given the text and the entity list, extract relationships ONLY between "
        "the listed entities. Respond in JSON.",
        f"Text: {SAMPLE_CHUNK}\n\nEntities: {json.dumps(entities)}",
        response_format("relationships", RELATIONSHIP_SCHEMA),
    )
    doc = json.loads(content)
    rels = doc.get("relationships")
    assert isinstance(rels, list), "bad relationships"
    for rel in rels:
        assert {"from", "to", "type"} <= rel.keys(), f"bad relationship: {rel}"
    for rel in rels[:5]:
        print(f"   ({rel['from']}) -[{rel['type']}]-> ({rel['to']})")
    if not rels:
        print("   (empty list — schema-valid, but model found no relationships)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base_url", help="tunnel URL, e.g. https://xxx.trycloudflare.com")
    parser.add_argument("--api-key", required=True, help="API key printed by the notebook")
    args = parser.parse_args()

    model = ""
    tests = [
        ("GET /v1/models", lambda: test_models(args.base_url, args.api_key)),
        ("json_object mode", lambda: test_json_object(args.base_url, args.api_key, model)),
        ("entity/intent schema", lambda: test_entity_intent(args.base_url, args.api_key, model)),
        ("relationship schema", lambda: test_relationships(args.base_url, args.api_key, model)),
    ]

    failures = 0
    for label, fn in tests:
        print(f"\n=> {label}")
        start = time.time()
        try:
            result = fn()
            if label == "GET /v1/models":
                model = result  # served model id feeds the chat tests
            print(f"   PASS ({time.time() - start:.1f}s)")
        except (AssertionError, RuntimeError, json.JSONDecodeError) as exc:
            failures += 1
            print(f"   FAIL: {exc}")
        except urllib.error.HTTPError as exc:
            failures += 1
            print(f"   FAIL: HTTP {exc.code} — {exc.read().decode()[:500]}")
        except urllib.error.URLError as exc:
            failures += 1
            print(f"   FAIL: unreachable — {exc.reason}")

    print(f"\n{'ALL PASSED' if failures == 0 else f'{failures} FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
