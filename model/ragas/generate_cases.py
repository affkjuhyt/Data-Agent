"""Build test_cases.json for eval.py.

Two sources, and the default changed:

  * **local** (default) — the committed grounding corpus,
    model/eval/cases/grounding.jsonl. Product questions, product contexts, and
    the refusal cases (an unpublished price, a period with no data, a forecast)
    that are the whole reason this platform measures grounding. Needs no
    network, so test_cases.json is reproducible from a checkout — it used to be
    absent from the repo and only obtainable by downloading two datasets from
    HuggingFace, which meant eval.py did not run at all.

  * **squad** — the original: 250 English SQuAD + 250 Vietnamese vi_squad rows.
    Kept because it is a useful breadth check on the judge model itself, but it
    is Wikipedia trivia: it says nothing about whether this system answers
    questions about a warehouse without inventing numbers, so it is no longer
    what the gate reads.

Usage:
    python generate_cases.py                  # local corpus (default)
    python generate_cases.py --source squad   # 500 SQuAD rows, needs network
"""

import argparse
import json
import os

TARGET_EN = 250
TARGET_VI = 250
OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "test_cases.json")
GROUNDING_PATH = os.path.join(
    os.path.dirname(__file__), "..", "eval", "cases", "grounding.jsonl",
)


def load_local() -> list[dict]:
    """The committed grounding corpus, in eval.py's case shape."""
    cases = []
    with open(GROUNDING_PATH, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)
            cases.append({
                "question": row["question"],
                "ground_truth": row["ground_truth"],
                "contexts": row.get("contexts", []),
                # Kept so a run can be split by outcome: a refusal case scoring
                # well on answer-correctness means something different from a
                # grounded one doing so.
                "expect": row.get("expect", "grounded"),
                "language": "en" if row["id"].endswith("-en") else "vi",
                "id": row["id"],
            })
    print(f"Loaded {len(cases)} cases from the committed grounding corpus")
    return cases


def load_squad_en(n: int) -> list[dict]:
    """Load English QA from SQuAD v1.1 validation split."""
    from datasets import load_dataset

    print(f"Loading SQuAD (EN) from HuggingFace — taking {n} rows...")
    ds = load_dataset("rajpurkar/squad", split=f"validation[:{n}]")
    cases = []
    for row in ds:
        answer = row["answers"]["text"]
        if not answer:
            continue
        cases.append({
            "question": row["question"],
            "ground_truth": answer[0],
            "contexts": [row["context"]],
            "language": "en",
        })
        if len(cases) >= n:
            break
    print(f"  Loaded {len(cases)} EN cases")
    return cases


def load_squad_vi(n: int) -> list[dict]:
    """Load Vietnamese QA from vietai/vi_squad validation split."""
    from datasets import load_dataset

    print(f"Loading vi_squad (VI) from HuggingFace — taking {n} rows...")
    try:
        ds = load_dataset("vietai/vi_squad", split=f"validation[:{n}]")
        cases = []
        for row in ds:
            answer = row["answers"]["text"]
            if not answer:
                continue
            cases.append({
                "question": row["question"],
                "ground_truth": answer[0],
                "contexts": [row["context"]],
                "language": "vi",
            })
            if len(cases) >= n:
                break
        print(f"  Loaded {len(cases)} VI cases")
        return cases
    except Exception as e:
        print(f"  [warn] vietai/vi_squad failed ({e}), falling back to uitnlp/ViQuAD...")
        return load_viquad(n)


def load_viquad(n: int) -> list[dict]:
    """Fallback: uitnlp/ViQuAD — Vietnamese Wikipedia QA."""
    from datasets import load_dataset

    ds = load_dataset("uitnlp/ViQuAD", split=f"validation[:{n}]")
    cases = []
    for row in ds:
        answer = row["answers"]["text"]
        if not answer:
            continue
        cases.append({
            "question": row["question"],
            "ground_truth": answer[0],
            "contexts": [row["context"]],
            "language": "vi",
        })
        if len(cases) >= n:
            break
    print(f"  Loaded {len(cases)} VI cases from ViQuAD")
    return cases


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", choices=["local", "squad"], default="local")
    args = parser.parse_args()

    if args.source == "local":
        all_cases = load_local()
    else:
        en_cases = load_squad_en(TARGET_EN)
        vi_cases = load_squad_vi(TARGET_VI)
        all_cases = en_cases + vi_cases
        print(f"\nTotal: {len(all_cases)} cases ({len(en_cases)} EN + {len(vi_cases)} VI)")

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(all_cases, f, ensure_ascii=False, indent=2)
    print(f"Saved -> {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
