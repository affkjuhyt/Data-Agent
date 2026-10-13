"""Intent accuracy for the V3 model gateway."""

from __future__ import annotations

import json
import os
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

UNSUPPORTED = ("forecast", "anomaly")
Classifier = Callable[[str], Awaitable[dict[str, Any]]]


async def planner_classifier(question: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Classify one question using the V3 planner taxonomy at LiteLLM.

    The worker planner HTTP contract is not exposed yet, so the release scorer talks to the
    same model gateway with a closed JSON contract; it does not import a legacy service.
    """
    del config
    base_url = os.getenv("LITELLM_BASE_URL")
    if not base_url:
        raise RuntimeError("LITELLM_BASE_URL is required for the intent eval")
    system = (
        "Classify the question as JSON only with keys intent and analysis_type. "
        "intent is question or task. analysis_type is one of lookup, analytical, comparison, "
        "forecast, anomaly, other. Forecast and anomaly are unsupported by the current V3 worker."
    )
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            base_url.rstrip("/") + "/v1/chat/completions",
            headers={"Authorization": f"Bearer {os.getenv('LITELLM_API_KEY', '')}"},
            json={
                "model": os.getenv("EVAL_PLANNER_MODEL", "planner"),
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": question},
                ],
                "temperature": 0,
                "response_format": {"type": "json_object"},
            },
        )
        response.raise_for_status()
        result = json.loads(response.json()["choices"][0]["message"]["content"])
    if not isinstance(result, dict):
        raise ValueError("the V3 planner did not return an object")
    return {
        "intent": result.get("intent", ""),
        "analysis_type": result.get("analysis_type", ""),
        "clarifications": result.get("clarifications", []),
    }


async def score(cases: list[dict[str, Any]], classify: Classifier) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    correct = 0
    unsupported_total = unsupported_hit = 0

    for case in cases:
        expect = case["expect"]
        try:
            actual = await classify(case["question"])
            error = ""
        except Exception as exc:  # noqa: BLE001 - a classifier outage is a failed case
            actual = {}
            error = f"{type(exc).__name__}: {exc}"
        checked = {k: v for k, v in expect.items() if k in ("intent", "analysis_type")}
        passed = not error and all(actual.get(k) == v for k, v in checked.items())
        correct += int(passed)
        if expect.get("analysis_type") in UNSUPPORTED:
            unsupported_total += 1
            unsupported_hit += int(actual.get("analysis_type") == expect["analysis_type"])
        results.append(
            {
                "id": case["id"],
                "tag": case.get("tag", ""),
                "question": case["question"],
                "expected": checked,
                "actual": {k: actual.get(k, "") for k in checked},
                "passed": passed,
                "error": error,
            }
        )

    total = len(cases)
    return {
        "metrics": {
            "intent_accuracy": (correct / total) if total else 0.0,
            "unsupported_recall": (unsupported_hit / unsupported_total)
            if unsupported_total
            else 1.0,
        },
        "counts": {
            "total": total,
            "correct": correct,
            "unsupported_total": unsupported_total,
            "unsupported_hit": unsupported_hit,
        },
        "results": results,
    }
