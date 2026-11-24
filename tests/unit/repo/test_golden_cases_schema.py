"""EV-05: the golden TLC cases (model/eval/cases/golden/tlc/*.jsonl) are well formed, as many as the
plan says per group, a third and more of them negative, every one run on the snapshot; and the
YAML the product loads (deploy/docker/eval/ds-tlc-cases.yaml) is the one they generate."""

from __future__ import annotations

import collections
import importlib.util
import pathlib

import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.req("EV-05")]

REPO = pathlib.Path(__file__).resolve().parents[3]
_SPEC = importlib.util.spec_from_file_location(
    "tlc_cases", REPO / "tests" / "e2e" / "tlc" / "cases.py"
)
assert _SPEC and _SPEC.loader
cases = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cases)

PLAN = {"structured": 24, "multiturn": 16, "analytical": 15, "hybrid": 5}


def _all() -> list[dict]:
    return [c for group in cases.load().values() for c in group]


def test_each_group_holds_the_planned_cases_with_unique_ids() -> None:
    groups = cases.load()
    assert {g: len(v) for g, v in groups.items()} == PLAN
    ids = [c["id"] for c in _all()]
    assert len(ids) == len(set(ids)) == 60
    assert all(c["group"] == g for g, group in groups.items() for c in group)


def test_a_third_and_more_are_negative_and_each_says_what_it_expects() -> None:
    negative = [c for c in _all() if c["expected_action"] != "answer"]
    assert len(negative) / 60 >= 0.35
    for c in negative:
        assert c["failure_category"] != "none" or c["expected_action"] == "flag_conflict", c["id"]
        assert c["expected"].get("kind") in ("refusal", "clarify", "data"), c["id"]
        if c["expected_action"] == "clarify":
            assert 2 <= len(c["expected"]["options"]) <= 3, c["id"]
    groups_with_negatives = {c["group"] for c in negative}
    assert groups_with_negatives == set(PLAN)  # inside every group, not a group of their own


def test_every_case_was_run_on_the_snapshot() -> None:
    for c in _all():
        assert c["verification_status"] == "sql_run_on_snapshot", c["id"]
        assert c["gold_sql"] not in ("", "n/a"), c["id"]
        assert len(c["expected"]["result_sha256"]) == 64, c["id"]
        if c["expected_action"] == "answer":
            assert c["expected"]["values"], c["id"]
        assert c["as_of"] == "2024-03-31"


def test_the_loaded_yaml_is_the_one_the_cases_generate_and_the_loader_accepts() -> None:
    from services.api.modules.eval.cases_yaml import CasesFile

    path = REPO / "deploy" / "docker" / "eval" / "ds-tlc-cases.yaml"
    text = path.read_text(encoding="utf-8")
    assert text == cases.to_yaml(), "regenerate: make tlc-cases (or tests/e2e/tlc/cases.py yaml)"
    loaded = CasesFile.model_validate(yaml.safe_load(text))
    assert len(loaded.cases) == 60
    counts = collections.Counter(c.expected_action for c in loaded.cases)
    assert counts["answer"] == 39


def test_the_ds_aw_conversation_cases_are_well_formed_and_scored() -> None:
    # The conversation of 24/11 (five turns) as golden cases of DS-AW, replayed with their history.
    from model.eval import corpus
    from model.eval.scorers import golden

    aw = corpus.load_golden("aw")
    assert [c["id"] for c in aw] == [f"AW-CV-00{n}" for n in range(1, 6)]
    for n, case in enumerate(aw):
        assert len(case["history"]) == 2 * n
        assert case["verification_status"] in golden.SCORED_STATUSES
        golden.rubric_of(case["rubric"])


def test_a_text_evidence_is_what_the_answer_says() -> None:
    from model.eval.scorers import golden

    answer = {"text": "Chỉ số đã chứng nhận: Doanh thu.", "citations": [], "figures": []}
    assert golden.evidence([{"source": "text", "contains": "chi so da chung nhan"}], answer)[0] == 1
    assert golden.evidence([{"source": "text", "contains": "Lãi gộp"}], answer)[0] == 0
