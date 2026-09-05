"""
RAGAS evaluation for the Data Agent RAG pipeline — exploratory, NOT the gate.

The gate lives in model/eval (four metrics, versioned corpus, thresholds, a
non-zero exit). This script stays for interactive RAGAS work: a wider judge-model
sweep, a quick look at answer relevancy, comparing two responder models. It
reports; it does not decide.

Read `--mode dataset` with that in mind: it scores the ground truth AS the
answer, so it measures the JUDGE and the corpus, not this system. That is a
useful calibration run (a metric that scores the reference answer poorly is
measuring something other than correctness) and a useless quality signal.
`--mode api` is the one that asks the live agent.

test_cases.json comes from generate_cases.py, which now defaults to the
committed corpus in model/eval/cases/grounding.jsonl and needs no network.

Modes:
  --mode dataset   Use test_cases.json directly (contexts from dataset, no API call).
                   Good for testing LLM quality in isolation.
  --mode api       Call workflow_runtime for each question, then evaluate answer quality.
                   Good for end-to-end pipeline testing (no contexts available).

Usage:
    # Dataset mode (full RAGAS metrics, needs test_cases.json from generate_cases.py)
    python eval.py --mode dataset

    # API mode (answer quality only, calls live workflow_runtime)
    python eval.py --mode api --agent-id <agent_id>
"""

import argparse
import asyncio
import json
import os

import httpx
import pandas as pd
from datasets import Dataset
from langchain_openai import ChatOpenAI
from ragas import evaluate
from ragas.llms import LangchainLLMWrapper
from ragas.metrics import AnswerCorrectness, AnswerRelevancy, Faithfulness

LITELLM_URL = os.getenv("LITELLM_BASE_URL", "http://localhost:8500")
LITELLM_API_KEY = os.getenv("LITELLM_API_KEY", "anything")
RUNTIME_URL = os.getenv("WORKFLOW_RUNTIME_URL", "http://localhost:8240")
DEFAULT_AGENT_ID = os.getenv("AGENT_ID", "00000000-0000-0000-0000-000000000030")
TEST_CASES_PATH = os.path.join(os.path.dirname(__file__), "test_cases.json")


def get_ragas_llm():
    # Same judge alias as the gate (model/eval): `judge` currently maps to
    # agent-main behind LiteLLM, and is not the model that wrote the answers.
    llm = ChatOpenAI(
        model=os.getenv("EVAL_JUDGE_MODEL", "judge"),
        base_url=f"{LITELLM_URL}/v1",
        api_key=LITELLM_API_KEY,
    )
    return LangchainLLMWrapper(llm)


async def call_agent(question: str, agent_id: str) -> str:
    """Call workflow_runtime SSE and collect full answer text."""
    payload = {"query": question, "agent_id": agent_id}
    headers = {"X-Tenant-ID": "dev-tenant", "Content-Type": "application/json"}
    full_text = []

    async with httpx.AsyncClient(timeout=60) as client:
        async with client.stream("POST", f"{RUNTIME_URL}/api/conversations/run",
                                 json=payload, headers=headers) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                raw = line[6:]
                if raw in ("[DONE]", "[ERROR]") or raw.startswith("[ERROR]"):
                    break
                try:
                    chunk = json.loads(raw)
                    if isinstance(chunk, str):
                        full_text.append(chunk)
                except json.JSONDecodeError:
                    pass

    return "".join(full_text)


def _load_cases() -> list[dict]:
    """test_cases.json, or a message that says how to make one.

    It is a generated artifact (gitignored). Failing with the command that
    produces it beats a bare FileNotFoundError on a path nobody recognises.
    """
    if not os.path.exists(TEST_CASES_PATH):
        raise SystemExit(
            f"{TEST_CASES_PATH} not found — build it first:\n"
            "    python generate_cases.py            # committed corpus, no network\n"
            "    python generate_cases.py --source squad"
        )
    with open(TEST_CASES_PATH, encoding="utf-8") as f:
        return json.load(f)


def run_dataset_mode(limit: int | None):
    """Evaluate using pre-built test_cases.json (has contexts + ground_truth)."""
    print("[note] dataset mode scores the ground truth as the answer — it "
          "calibrates the judge, it does not grade this system. Use model/eval "
          "for the gated metrics.")
    cases = _load_cases()

    if limit:
        cases = cases[:limit]

    data = {
        "question":     [c["question"]     for c in cases],
        "answer":       [c["ground_truth"]  for c in cases],  # ground_truth AS answer for baseline
        "contexts":     [c.get("contexts", []) for c in cases],
        "ground_truth": [c["ground_truth"]  for c in cases],
    }
    dataset = Dataset.from_dict(data)

    ragas_llm = get_ragas_llm()
    metrics = [
        Faithfulness(llm=ragas_llm),
        AnswerRelevancy(llm=ragas_llm),
        AnswerCorrectness(llm=ragas_llm),
    ]

    print(f"Running RAGAS evaluation on {len(cases)} cases (dataset mode)...")
    result = evaluate(dataset, metrics=metrics)
    return result


async def run_api_mode(agent_id: str, limit: int | None):
    """Call live agent for each question, evaluate answer quality."""
    cases = _load_cases()

    if limit:
        cases = cases[:limit]

    print(f"Calling workflow_runtime for {len(cases)} questions...")
    sem = asyncio.Semaphore(5)

    async def call_with_sem(i, c):
        async with sem:
            ans = await call_agent(c["question"], agent_id)
            print(f"  [{i+1}/{len(cases)}] {c['question'][:60]}...")
            return ans

    answers = await asyncio.gather(*(call_with_sem(i, c) for i, c in enumerate(cases)))

    data = {
        "question":     [c["question"]     for c in cases],
        "answer":       answers,
        "ground_truth": [c["ground_truth"] for c in cases],
    }
    dataset = Dataset.from_dict(data)

    ragas_llm = get_ragas_llm()
    # No contexts available from SSE — use answer-quality metrics only
    metrics = [
        AnswerRelevancy(llm=ragas_llm),
        AnswerCorrectness(llm=ragas_llm),
    ]

    print("Running RAGAS evaluation...")
    result = evaluate(dataset, metrics=metrics)
    return result


def print_results(result):
    print("\n" + "=" * 50)
    print("RAGAS Evaluation Results")
    print("=" * 50)
    df = result.to_pandas()
    summary = df.select_dtypes(include="number").mean()
    for metric, score in summary.items():
        bar = "█" * int(score * 20)
        print(f"  {metric:<25} {score:.4f}  {bar}")
    print("=" * 50)

    output_csv = os.path.join(os.path.dirname(__file__), "results.csv")
    df.to_csv(output_csv, index=False)
    print(f"\nDetailed results saved to {output_csv}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["dataset", "api"], default="dataset")
    parser.add_argument("--agent-id", default=DEFAULT_AGENT_ID)
    parser.add_argument("--limit", type=int, default=None,
                        help="Evaluate only first N cases (for quick testing)")
    args = parser.parse_args()

    if args.mode == "dataset":
        result = run_dataset_mode(args.limit)
    else:
        result = asyncio.run(run_api_mode(args.agent_id, args.limit))

    print_results(result)


if __name__ == "__main__":
    main()
