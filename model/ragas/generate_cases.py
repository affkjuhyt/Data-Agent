"""
Generate 500 test cases (250 EN + 250 VI) for RAGAS evaluation.

Sources:
- English (250): rajpurkar/squad validation split — real Wikipedia QA with contexts
- Vietnamese (250): vietai/vi_squad validation split — SQuAD translated to Vietnamese

No LLM needed — both datasets already have question + answer + context.

Usage:
    pip install -r requirements.txt
    python generate_cases.py
    # Output: test_cases.json
"""

import json
import os

from datasets import load_dataset

TARGET_EN = 250
TARGET_VI = 250
OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "test_cases.json")


def load_squad_en(n: int) -> list[dict]:
    """Load English QA from SQuAD v1.1 validation split."""
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
    en_cases = load_squad_en(TARGET_EN)
    vi_cases = load_squad_vi(TARGET_VI)

    all_cases = en_cases + vi_cases
    print(f"\nTotal: {len(all_cases)} cases ({len(en_cases)} EN + {len(vi_cases)} VI)")

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(all_cases, f, ensure_ascii=False, indent=2)
    print(f"Saved → {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
