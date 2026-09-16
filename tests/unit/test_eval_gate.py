"""The evaluation harness and the release gate (model/eval).

Two different things are pinned here, and the second is the point:

  * the corpus loads, and the offline scorers agree with the guards and the
    metric compiler they measure — so a green gate means something;
  * the gate FAILS when it should. A gate that only ever passes is
    indistinguishable from no gate, and the way that happens in practice is not
    a wrong threshold, it is a metric that silently did not get measured.
"""
from __future__ import annotations

import json
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.unit


# ── the corpus ─────────────────────────────────────────────────────────────

def test_every_case_file_loads_and_has_unique_ids():
    from model.eval import corpus

    for name in ("policy", "sql", "intent", "grounding"):
        cases = corpus.load_cases(name)
        assert cases, f"{name} corpus is empty"
        assert len({c["id"] for c in cases}) == len(cases)


def test_a_malformed_case_file_is_fatal(tmp_path, monkeypatch):
    """A corpus that quietly shrinks makes every rate look better than it is."""
    from model.eval import corpus

    monkeypatch.setattr(corpus, "CASES_DIR", tmp_path)
    (tmp_path / "broken.jsonl").write_text('{"id": "a"}\nnot json\n', encoding="utf-8")

    with pytest.raises(corpus.CorpusError):
        corpus.load_cases("broken")


def test_duplicate_ids_are_fatal(tmp_path, monkeypatch):
    from model.eval import corpus

    monkeypatch.setattr(corpus, "CASES_DIR", tmp_path)
    (tmp_path / "dupe.jsonl").write_text('{"id": "a"}\n{"id": "a"}\n', encoding="utf-8")

    with pytest.raises(corpus.CorpusError):
        corpus.load_cases("dupe")


def test_policy_corpus_covers_both_directions():
    """Must-allow cases are what stop the gate rewarding a guard that refuses
    everything, so their absence is itself a defect in the dataset."""
    from model.eval import corpus

    cases = corpus.load_cases("policy")
    assert any(c["expect"] == "refuse" for c in cases)
    assert any(c["expect"] == "allow" for c in cases)

    surfaces = {c["surface"] for c in cases}
    from model.eval.scorers import policy

    assert surfaces <= set(policy.SURFACES), f"unknown surfaces in corpus: {surfaces - set(policy.SURFACES)}"


# ── offline scorers ────────────────────────────────────────────────────────

def test_policy_scorer_runs_the_whole_corpus_clean():
    """Not a tautology: these call the real sql_guard / AST validator / path
    check. If a defence regresses, this is where it shows up as a rate."""
    pytest.importorskip("sqlparse", reason="sql_guard needs sqlparse")
    from model.eval import corpus
    from model.eval.scorers import policy

    report = policy.score(corpus.load_cases("policy"))

    assert report["metrics"]["policy_violation_rate"] == 0.0, [
        r for r in report["results"] if not r["passed"]
    ]
    assert report["metrics"]["false_refusal_rate"] == 0.0, [
        r for r in report["results"] if not r["passed"]
    ]


def test_policy_scorer_counts_a_violation_when_a_guard_lets_one_through(monkeypatch):
    """The rate has to be able to move, or a passing gate proves nothing."""
    from model.eval.scorers import policy

    monkeypatch.setitem(policy.SURFACES, "sql_guard", lambda case: None)  # a guard that allows everything
    cases = [
        {"id": "x1", "surface": "sql_guard", "expect": "refuse", "input": "DROP TABLE t"},
        {"id": "x2", "surface": "sql_guard", "expect": "allow", "input": "SELECT 1"},
    ]

    report = policy.score(cases)

    assert report["metrics"]["policy_violation_rate"] == 1.0
    assert report["metrics"]["false_refusal_rate"] == 0.0


def test_sql_scorer_runs_the_whole_corpus_clean():
    pytest.importorskip("sqlparse", reason="one case asserts the compiled SQL passes sql_guard")
    from model.eval import corpus
    from model.eval.scorers import sql

    report = sql.score(corpus.load_cases("sql"), corpus.load_catalogs())

    assert report["metrics"]["sql_accuracy"] == 1.0, [
        r for r in report["results"] if not r["passed"]
    ]


def test_sql_scorer_fails_a_case_whose_expectation_is_not_met():
    from model.eval import corpus
    from model.eval.scorers import sql

    case = {
        "id": "x1", "catalog": "retail", "question": "doanh thu theo tháng",
        "request": {"grain": "month"},
        "expect": {"metric": "revenue", "sql_contains": ["SUM(net_amount)"]},
    }

    report = sql.score([case], corpus.load_catalogs())

    assert report["metrics"]["sql_accuracy"] == 0.0
    assert "missing from SQL" in report["results"][0]["detail"]


# ── online scorers (metric arithmetic, no model) ────────────────────────────

async def test_intent_scorer_only_grades_the_axes_a_case_states():
    """intent and analysis_type are orthogonal. A case pinning one must not be
    marked wrong for the other, or the corpus would need an opinion it does not
    have."""
    from model.eval.scorers import intent

    async def classify(question):
        return {"intent": "task", "analysis_type": "comparison"}

    cases = [
        {"id": "a", "question": "q", "expect": {"analysis_type": "comparison"}},
        {"id": "b", "question": "q", "expect": {"intent": "task"}},
        {"id": "c", "question": "q", "expect": {"intent": "question"}},
    ]

    report = await intent.score(cases, classify)

    assert report["metrics"]["intent_accuracy"] == pytest.approx(2 / 3)


async def test_intent_scorer_reports_unsupported_recall_separately():
    """A forecast misclassified is what lets an invented number through, and it
    is rare enough to vanish inside overall accuracy."""
    from model.eval.scorers import intent

    async def classify(question):
        return {"intent": "question", "analysis_type": "analytical"}

    cases = [
        {"id": "a", "question": "q", "expect": {"analysis_type": "analytical"}},
        {"id": "b", "question": "q", "expect": {"analysis_type": "analytical"}},
        {"id": "c", "question": "q", "expect": {"analysis_type": "forecast"}},
    ]

    report = await intent.score(cases, classify)

    assert report["metrics"]["intent_accuracy"] == pytest.approx(2 / 3)
    assert report["metrics"]["unsupported_recall"] == 0.0


async def test_a_classifier_error_counts_as_wrong_not_as_skipped():
    from model.eval.scorers import intent

    async def classify(question):
        raise RuntimeError("litellm unreachable")

    report = await intent.score([{"id": "a", "question": "q", "expect": {"intent": "question"}}], classify)

    assert report["metrics"]["intent_accuracy"] == 0.0
    assert "litellm unreachable" in report["results"][0]["error"]


@pytest.mark.parametrize(
    ("answer", "contexts", "expected"),
    [
        ("Doanh thu là 128,4 tỷ đồng", ["Doanh thu đạt 128.4 tỷ"], []),
        ("Doanh thu là 200 tỷ đồng", ["Doanh thu đạt 128,4 tỷ"], ["200"]),
        ("Quý sau sẽ đạt 155 tỷ", ["110, 118, 128, 141 tỷ"], ["155"]),
        ("Không có trong nguồn", ["Giá Enterprise báo giá theo yêu cầu"], []),
    ],
)
def test_unsupported_numbers_finds_figures_that_are_in_no_source(answer, contexts, expected):
    """Convention-insensitive, not separator-insensitive: 128,4 and 128.4 are
    the same figure read under two conventions, but 1284 is a different figure.
    Deleting the separator instead of parsing it is the E2 defect — see
    tests/unit/test_eval_numeric.py."""
    from model.eval.scorers import grounding

    assert grounding.unsupported_numbers(answer, contexts) == expected


def test_declared_derivations_count_as_grounded():
    """A percentage computed from two figures that ARE in the source is
    grounded. Without this the metric would reward answers that never compute."""
    from model.eval.scorers import grounding

    stray = grounding.unsupported_numbers(
        "khoảng 12%", ["doanh thu 128,4 tỷ; chi phí marketing 15,4 tỷ"], allowed=["12"],
    )

    assert stray == []


async def test_grounding_scorer_counts_an_errored_answer_as_a_failure():
    """A corpus that drops the cases it could not run reports a rate for the
    easy half of itself."""
    from model.eval.scorers import grounding

    async def answer(case):
        raise RuntimeError("model timeout")

    report = await grounding.score([{"id": "a", "question": "q", "contexts": ["x"]}], answer)

    assert report["metrics"]["unsupported_number_rate"] == 1.0


async def test_hallucination_rate_is_absent_when_no_judge_ran():
    """Absent, not zero. The gate turns an absent required metric into a
    failure; a zero would sail through as a perfect score."""
    from model.eval.scorers import grounding

    async def answer(case):
        return "Doanh thu đạt 128,4 tỷ"

    report = await grounding.score(
        [{"id": "a", "question": "q", "contexts": ["Doanh thu đạt 128,4 tỷ"]}], answer,
    )

    assert "hallucination_rate" not in report["metrics"]
    assert report["metrics"]["unsupported_number_rate"] == 0.0


# ── the gate ───────────────────────────────────────────────────────────────

def _thresholds():
    from model.eval import gate

    return gate.load_thresholds()


def test_gate_passes_a_report_inside_every_bound():
    from model.eval import gate

    report = {"tiers": ["offline"], "metrics": {
        "policy_violation_rate": 0.0, "false_refusal_rate": 0.0, "sql_accuracy": 1.0,
    }}

    assert gate.check(report, _thresholds(), ["offline"]) == []


def test_gate_fails_a_metric_over_its_maximum():
    from model.eval import gate

    report = {"tiers": ["offline"], "metrics": {
        "policy_violation_rate": 0.02, "false_refusal_rate": 0.0, "sql_accuracy": 1.0,
    }}

    failures = gate.check(report, _thresholds(), ["offline"])

    assert len(failures) == 1
    assert "policy_violation_rate" in failures[0]


def test_gate_fails_a_metric_under_its_minimum():
    from model.eval import gate

    report = {"tiers": ["offline"], "metrics": {
        "policy_violation_rate": 0.0, "false_refusal_rate": 0.0, "sql_accuracy": 0.96,
    }}

    failures = gate.check(report, _thresholds(), ["offline"])

    assert len(failures) == 1
    assert "sql_accuracy" in failures[0]


def test_gate_fails_a_metric_that_was_never_measured():
    """The failure mode this file exists for: a release certified by an eval
    that silently did not run the half that mattered."""
    from model.eval import gate

    report = {"tiers": ["offline"], "metrics": {"policy_violation_rate": 0.0}}

    failures = gate.check(report, _thresholds(), ["offline"])

    assert any("sql_accuracy" in f and "not present" in f for f in failures)
    assert any("false_refusal_rate" in f and "not present" in f for f in failures)


def test_gate_fails_when_the_report_never_ran_the_tier_being_enforced():
    from model.eval import gate

    report = {"tiers": ["offline"], "metrics": {
        "policy_violation_rate": 0.0, "false_refusal_rate": 0.0, "sql_accuracy": 1.0,
    }}

    failures = gate.check(report, _thresholds(), ["online"])

    assert failures and "was not run" in failures[0]


def test_online_thresholds_name_every_metric_the_online_tier_produces():
    """A metric that no threshold mentions is measured and then ignored, which
    reads as coverage without being it."""
    from model.eval import gate

    online = set(gate.load_thresholds()["online"])

    assert {"intent_accuracy", "unsupported_recall", "hallucination_rate", "unsupported_number_rate"} <= online


def test_provisional_online_thresholds_are_declared_as_such():
    """They are engineering targets, not measurements — this repo has no
    recorded baseline against a live model. Marking them keeps that honest until
    someone re-bases them from a real run."""
    from model.eval import gate

    online = gate.load_thresholds()["online"]

    assert all(bound.get("provisional") for bound in online.values())


def test_thresholds_file_is_valid_json_with_a_bound_per_metric():
    raw = json.loads((REPO_ROOT / "model" / "eval" / "thresholds.json").read_text(encoding="utf-8"))

    for tier, metrics in raw.items():
        if tier.startswith("_"):
            continue
        for name, bound in metrics.items():
            assert ("min" in bound) or ("max" in bound), f"{tier}.{name} has no bound"
            assert bound.get("why"), f"{tier}.{name} does not say why"
