"""Hallucination rate — online tier, needs a model to produce the answers.

Two measurements over one corpus, because they fail differently and a single
number would hide the one that matters here.

**unsupported_number_rate** (deterministic). Every numeric token in the answer
must appear in the contexts the answer was given, or be derivable from them —
and derivations are declared per case, not guessed. This is narrow on purpose:
it cannot judge prose, it judges figures, and a figure nobody can point to in a
source is exactly the failure this product cannot afford. It needs no judge
model, so it is reproducible and cannot be argued with.

**hallucination_rate** (RAGAS faithfulness). The broader claim-level check: a
case counts as hallucinated when faithfulness falls under FAITHFULNESS_FLOOR.
Needs ragas + a judge model; when neither is present the metric is reported as
absent rather than as zero, and the gate treats a required-but-absent metric as
a failure. A missing measurement is not a passing one.

`refuse` cases carry no supported figures at all: the source does not contain
the answer (an unpublished price, a period with no data) or the question needs a
capability the platform does not have (forecast, anomaly). Any figure in the
answer is therefore unsupported by construction, which is what makes them the
sharpest cases in the corpus.
"""
from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any

# Below this, RAGAS considers the answer's claims unsupported by its contexts.
# 0.7 rather than 0.5: a "half-supported" answer about a number is a wrong
# answer, not a partially right one.
FAITHFULNESS_FLOOR = 0.7

# Digit groups, tolerating both separators used across the corpus (1.240 and
# 12,400). Percentages and years are numbers too — a wrong year is as
# fabricated as a wrong total.
_NUMBER_RE = re.compile(r"\d[\d.,]*")

# The shipped responder prompt cites sources as "[1]", "[2]"… (see
# nodes/model_harness's system prompt). That index is structural markup, not a
# claimed figure — without stripping it first, every correctly-cited answer
# ("... 1.240. Trích dẫn: [1]") flags its own citation as an unsupported "1".
_CITATION_RE = re.compile(r"\[\d+\]")

Answerer = Callable[[dict[str, Any]], Awaitable[str]]


def _numbers(text: str) -> list[str]:
    """Digit groups, normalised so 128,4 and 128.4 compare equal.

    Separators are stripped rather than parsed: the question is whether the
    figure was taken from the source, and 1.240 in a Vietnamese source is the
    same figure as 1240 in an answer that dropped the separator.
    """
    out = []
    text = _CITATION_RE.sub(" ", text)
    for raw in _NUMBER_RE.findall(text):
        cleaned = raw.strip(".,").replace(".", "").replace(",", "")
        if cleaned:
            out.append(cleaned)
    return out


def unsupported_numbers(answer: str, contexts: list[str], allowed: list[str] | None = None) -> list[str]:
    """Figures in `answer` that are in neither the contexts nor `allowed`.

    `allowed` is the case's declared derivations — a percentage computed from
    two figures that ARE in the source is grounded, and refusing to say so would
    push the metric to reward answers that never compute anything.
    """
    supported = set()
    for context in contexts:
        supported.update(_numbers(context))
    supported.update(_numbers(" ".join(allowed or [])))
    return [n for n in _numbers(answer) if n not in supported]


async def score(
    cases: list[dict[str, Any]],
    answer: Answerer,
    faithfulness: Callable[[list[dict[str, Any]]], list[float]] | None = None,
) -> dict[str, Any]:
    """Answer every case, then score the answers.

    `answer` takes the whole case (question + contexts) and returns the agent's
    answer. `faithfulness` scores a batch and returns one score per case; None
    means RAGAS was not available, and hallucination_rate is then absent from
    the metrics rather than defaulted.
    """
    answers: list[str] = []
    results: list[dict[str, Any]] = []

    for case in cases:
        try:
            text = await answer(case)
        # Any failure to get an answer counts against the rate below, so the
        # catch is deliberately as wide as the ways a model call can fail.
        except Exception as e:  # noqa: BLE001
            text = ""
            results.append({"id": case["id"], "error": f"{type(e).__name__}: {e}"})
            answers.append(text)
            continue
        answers.append(text)
        results.append({"id": case["id"], "error": ""})

    unsupported_cases = 0
    for case, text, result in zip(cases, answers, results, strict=True):
        stray = unsupported_numbers(text, case.get("contexts", []), case.get("allowed_numbers"))
        # An errored answer counts as a failure, not as a skip: a corpus that
        # drops the cases it could not run reports a rate for the easy half.
        failed = bool(stray) or bool(result["error"])
        unsupported_cases += 1 if failed else 0
        result.update({
            "tag": case.get("tag", ""),
            "expect": case.get("expect", "grounded"),
            "answer": text,
            "unsupported_numbers": stray,
            "passed": not failed,
        })

    metrics: dict[str, float] = {
        "unsupported_number_rate": (unsupported_cases / len(cases)) if cases else 0.0,
    }

    if faithfulness is not None:
        # Faithfulness is undefined for a correct refusal: "Không có trong
        # nguồn" makes zero factual claims, so there is nothing for RAGAS to
        # verify against the contexts — judged anyway it scores near 0 by
        # construction (0 supported claims / 0 total), which is a scorer
        # artifact, not a hallucination. Whether the refusal itself was
        # correct is intent.unsupported_recall's job; unsupported_number_rate
        # above already catches a refuse-case answer that smuggles a figure.
        graded = [i for i, c in enumerate(cases) if c.get("expect", "grounded") != "refuse"]
        rows = [
            {"question": cases[i]["question"], "answer": answers[i], "contexts": cases[i].get("contexts", []),
             "ground_truth": cases[i].get("ground_truth", "")}
            for i in graded
        ]
        scores = faithfulness(rows) if rows else []
        hallucinated = 0
        for i, value in zip(graded, scores, strict=True):
            results[i]["faithfulness"] = value
            if value < FAITHFULNESS_FLOOR:
                hallucinated += 1
        metrics["hallucination_rate"] = (hallucinated / len(graded)) if graded else 0.0

    return {
        "metrics": metrics,
        "counts": {"total": len(cases), "with_unsupported_numbers": unsupported_cases},
        "results": results,
    }


def ragas_faithfulness(model: str | None = None) -> Callable[[list[dict[str, Any]]], list[float]] | None:
    """A RAGAS faithfulness scorer, or None when ragas/the judge is unavailable.

    The judge model is `EVAL_JUDGE_MODEL` (default: the `judge` LiteLLM alias),
    overridable with the `model` argument. Defaulting to a dedicated judge alias
    rather than `responder` keeps the model that wrote the answers from grading
    them.

    Returning None rather than raising keeps "we could not measure this" a
    reportable state. The gate is what decides that an unmeasured required
    metric fails the release — this module does not get to make a missing number
    look like a good one.
    """
    try:
        import os

        from datasets import Dataset
        from langchain_openai import ChatOpenAI
        from ragas import evaluate
        from ragas.llms import LangchainLLMWrapper
        from ragas.metrics import Faithfulness
    except ImportError:
        return None

    judge_model = model or os.getenv("EVAL_JUDGE_MODEL", "judge")

    def _score(rows: list[dict[str, Any]]) -> list[float]:
        judge = LangchainLLMWrapper(ChatOpenAI(
            model=judge_model,
            base_url=f"{os.getenv('LITELLM_BASE_URL', 'http://localhost:4000')}/v1",
            api_key=os.getenv("LITELLM_API_KEY", "anything"),
        ))
        dataset = Dataset.from_dict({
            "question": [r["question"] for r in rows],
            "answer": [r["answer"] for r in rows],
            "contexts": [r["contexts"] for r in rows],
            "ground_truth": [r["ground_truth"] for r in rows],
        })
        frame = evaluate(dataset, metrics=[Faithfulness(llm=judge)]).to_pandas()
        column = "faithfulness" if "faithfulness" in frame else frame.select_dtypes("number").columns[0]
        return [float(v) for v in frame[column].fillna(0.0)]

    return _score
