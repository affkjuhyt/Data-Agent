"""The pilot eval set the seed loads (R1-12) is deploy/docker/eval/ds-bh-pilot-cases.yaml: the
same questions and expected answers, at least the set's 100 items, a reference SQL behind every
number, and no question an approved verified query answers word for word (that would test R1-08,
not the reading of the question)."""

from __future__ import annotations

import json
import pathlib
import re

import pytest
import yaml

pytestmark = [pytest.mark.unit]

ROOT = pathlib.Path(__file__).resolve().parents[3]
SEED = (ROOT / "deploy" / "docker" / "sql" / "v3-pilot-seed.sql").read_text(encoding="utf-8")
CASES = yaml.safe_load((ROOT / "deploy" / "docker" / "eval" / "ds-bh-pilot-cases.yaml").read_text())
SHEET = (ROOT / "docs" / "v3" / "eval" / "ds-bh-blind-sheet.md").read_text(encoding="utf-8")


def _seeded() -> dict[str, tuple[str, dict]]:
    block = SEED[SEED.index("INSERT INTO eval.eval_item") :]
    rows = re.findall(r"\('[0-9a-f-]+', '(\w+)',\s*'(\{.*?\})',\s*'(\{.*?\})'\)", block, flags=re.S)
    return {json.loads(i)["question"]: (category, json.loads(e)) for category, i, e in rows}


def test_the_seed_loads_the_cases_as_written() -> None:
    seeded = _seeded()
    assert len(seeded) == len(CASES["cases"]) >= 100
    for case in CASES["cases"]:
        assert seeded[case["question"]] == (case["category"], case["expected"]), case["id"]


def test_every_number_has_its_reference_sql_and_one_value() -> None:
    for case in CASES["cases"]:
        expected = case["expected"]
        if expected.get("kind") == "data":
            assert case["reference_sql"].startswith("SELECT"), case["id"]
            assert len(expected["values"]) == 1 and expected["metric"], case["id"]
        else:
            assert expected["behavior"] in {"refuse", "clarify"}, case["id"]


def test_no_case_is_a_verified_query_word_for_word() -> None:
    verified = set(re.findall(r":space::uuid,\s*'([^']+)',\s*\$vq\$", SEED))
    assert verified and not verified & {c["question"] for c in CASES["cases"]}


def test_the_blind_sheet_has_every_question_and_no_answer() -> None:
    for case in CASES["cases"]:
        assert f"| {case['id']} | {case['question']} | |" in SHEET
    assert "reference_sql" not in SHEET and "values" not in SHEET
