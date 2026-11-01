"""The DS-AW eval set the seed loads (R1-12) is deploy/docker/eval/ds-aw-cases.yaml: the same
questions and expected answers, at least the set's min_items, a reference SQL behind every number,
and no question an approved verified query answers word for word (that would test R1-08, not the
reading of the question). The blind sheet has every question and no answer."""

from __future__ import annotations

import json
import pathlib
import re

import pytest
import yaml

pytestmark = [pytest.mark.unit]

ROOT = pathlib.Path(__file__).resolve().parents[3]
SEED = (ROOT / "deploy" / "docker" / "sql" / "v3-adventureworks-context.sql").read_text(
    encoding="utf-8"
)
CASES = yaml.safe_load((ROOT / "deploy" / "docker" / "eval" / "ds-aw-cases.yaml").read_text())
SHEET = (ROOT / "docs" / "v3" / "eval" / "ds-aw-blind-sheet.md").read_text(encoding="utf-8")
CONFIGMAPS = (ROOT / "deploy" / "k8s" / "create-configmaps.sh").read_text(encoding="utf-8")
DS_AW = "a0000000-0000-0000-0000-000000000a01"


def _seeded() -> dict[str, tuple[str, dict]]:
    block = SEED[SEED.index("INSERT INTO eval.eval_item") :]
    rows = re.findall(r"\('[0-9a-f-]+', '(\w+)',\s*'(\{.*?\})',\s*'(\{.*?\})'\)", block, flags=re.S)
    return {json.loads(i)["question"]: (category, json.loads(e)) for category, i, e in rows}


def test_the_seed_loads_the_cases_as_written() -> None:
    seeded = _seeded()
    [min_items] = re.findall(r"'Câu hỏi AdventureWorks', [0-9.]+, (\d+), 'active'", SEED)
    assert CASES["eval_set"] == "Câu hỏi AdventureWorks"
    assert len(seeded) == len(CASES["cases"]) >= int(min_items)
    for case in CASES["cases"]:
        assert seeded[case["question"]] == (case["category"], case["expected"]), case["id"]


def test_the_seed_is_ds_aws_and_applied_after_the_data_space() -> None:
    assert f"\\set space '''{DS_AW}'''" in SEED
    order = re.findall(r"--from-file=(\d\d)-[\w-]+\.sql=\$K/sql/([\w-]+)\.sql", CONFIGMAPS)
    prefix = {name: number for number, name in order}
    assert prefix["v3-adventureworks-context"] > prefix["v3-adventureworks-seed"]


def test_every_number_has_its_reference_sql() -> None:
    for case in CASES["cases"]:
        expected = case["expected"]
        if expected.get("kind") == "data":
            sql = case["reference_sql"]
            assert sql.startswith("SELECT") and expected["metric"], case["id"]
            assert expected["values"], case["id"]
            # Several figures are compared in order: the reference has to fix it.
            assert len(expected["values"]) == 1 or "ORDER BY" in sql, case["id"]
        else:
            assert expected["behavior"] in {"refuse", "clarify"}, case["id"]


def test_the_set_covers_numbers_clarifications_and_refusals() -> None:
    categories = [case["category"] for case in CASES["cases"]]
    assert categories.count("correct_result") >= 40
    assert categories.count("ask_at_right_time") >= 5
    assert categories.count("refusal") >= 5


def test_no_case_is_a_verified_query_word_for_word() -> None:
    verified = set(re.findall(r":space::uuid,\s*'([^']+)',\s*\$vq\$", SEED))
    assert len(verified) >= 20 and not verified & {c["question"] for c in CASES["cases"]}


def test_the_blind_sheet_has_every_question_and_no_answer() -> None:
    for case in CASES["cases"]:
        assert f"| {case['id']} | {case['question']} | |" in SHEET
    assert "reference_sql" not in SHEET and "values" not in SHEET
