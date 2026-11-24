"""Calibrating a producer's confidence (EV-19, docs/blueprint/CONFIDENCE_SPEC.md): the arithmetic
of backend/libs/calibration and the report model/eval/calibrate.py makes of an eval's turns.

The library is what AE-24 will decide with, so its conservative choices are pinned: a sparse bin
is not trusted as much as a full one, a value between two bins takes the weaker, and a bar is
only set where the evidence keeps the error within the accepted risk."""

from __future__ import annotations

import json
import pathlib

import pytest

from libs import calibration as cal
from model.eval import calibrate

pytestmark = [pytest.mark.unit]


def test_wilson_bounds_a_proportion_and_says_nothing_without_turns() -> None:
    low, high = cal.wilson(0, 10)
    assert low == 0.0 and high == pytest.approx(0.2130, abs=1e-4)
    low, high = cal.wilson(10, 10)
    assert high == 1.0 and low == pytest.approx(0.7870, abs=1e-4)
    assert cal.wilson(0, 0) == (0.0, 1.0)


def test_pava_pools_what_goes_down_into_a_non_decreasing_fit() -> None:
    blocks = cal.pava([0.5, 0.3, 0.7, 0.6], [1, 1, 1, 1])
    assert [(first, last) for first, last, _, _ in blocks] == [(0, 1), (2, 3)]
    assert [round(v, 4) for _, _, v, _ in blocks] == [0.4, 0.65]
    assert [w for *_, w in blocks] == [2, 2]


def test_edges_keep_ties_together_and_give_few_values_a_bin_each() -> None:
    assert cal.edges([1.0, 0.9, 0.9, 0.8], 5) == [(0.8, 0.8), (0.9, 0.9), (1.0, 1.0)]
    xs = [0.1] * 4 + [0.2, 0.3, 0.4, 0.5, 0.6, 0.7] + [0.9] * 4
    # About 14/3 turns a bin; the four 0.9s stay in one bin although that makes it the fullest.
    assert cal.edges(xs, 3) == [(0.1, 0.2), (0.3, 0.7), (0.9, 0.9)]


def test_a_map_is_monotone_conservative_and_never_more_sure_than_its_turns() -> None:
    xs = [1.0] * 30 + [0.9] * 10 + [0.8] * 3
    ys = [True] * 29 + [False] + [True] * 7 + [False] * 3 + [True, True, True]
    m = cal.fit(xs, ys, "understanding")
    ps = [b.p for b in m.bins]
    assert ps == sorted(ps), "p never falls as x rises"
    assert all(b.p_low <= b.p for b in m.bins)
    # Three right turns at 0.8 are pooled with the 0.9 bin they outrank, not trusted alone.
    assert m.apply(0.8) == m.apply(0.9)
    assert m.apply(1.0)[1] > m.apply(0.9)[1]
    # Unseen values: between two bins the lower, under the lowest the lowest.
    assert m.apply(0.95) == m.apply(0.9) and m.apply(0.5) == m.apply(0.8)
    assert cal.CalibrationMap.from_dict(json.loads(json.dumps(m.to_dict()))) == m


def test_no_wrong_turn_does_not_make_the_map_certain() -> None:
    m = cal.fit([1.0] * 5, [True] * 5, "understanding")
    p, low = m.apply(1.0)
    assert p < 1.0 and low < 0.8


def test_ece_brier_and_aurc_on_known_cases() -> None:
    assert cal.ece([1.0, 1.0, 0.0, 0.0], [True, True, False, False]) == 0.0
    assert cal.ece([0.5] * 4, [True, False, True, False], n_bins=2) == 0.0
    assert cal.ece([0.9] * 10, [True] * 5 + [False] * 5, n_bins=1) == pytest.approx(0.4)
    assert cal.brier([0.8, 0.2], [True, False]) == pytest.approx(0.04)
    # Two sure right turns, then two unsure turns of which one is wrong.
    ps, ys = [0.9, 0.9, 0.4, 0.4], [True, True, True, False]
    points = cal.risk_coverage(ps, ys)
    assert [(p.threshold, p.coverage, p.risk) for p in points] == [
        (0.9, 0.5, 0.0),
        (0.4, 1.0, 0.25),
    ]
    assert cal.aurc(ps, ys) == pytest.approx(0.125)


def test_bars_hold_the_accepted_risk_on_the_upper_bound_of_the_error() -> None:
    scores = [0.95] * 100 + [0.6] * 20 + [0.3] * 20
    ys = [True] * 100 + [True] * 18 + [False] * 2 + [True] * 8 + [False] * 12
    tau_answer, tau_review = cal.choose_thresholds(scores, ys, r_answer=0.03, r_review=0.30)
    assert (tau_answer, tau_review) == (0.95, 0.6)
    assert cal.choose_thresholds([0.9] * 5, [True] * 5, 0.03, 0.2) == (None, None)


def test_cross_fit_is_deterministic_and_predicts_every_turn() -> None:
    xs = [i / 20 for i in range(20)] * 3
    ys = [i % 3 != 0 for i in range(60)]
    first = cal.cross_fit(xs, ys, "sqlgen_vote", seed=7)
    assert first == cal.cross_fit(xs, ys, "sqlgen_vote", seed=7)
    assert len(first[0]) == len(first[1]) == 60
    assert all(0.0 < p < 1.0 and low <= p for p, low in zip(*first, strict=True))
    assert cal.bootstrap(cal.ece, first[0], ys) == cal.bootstrap(cal.ece, first[0], ys)


def _ablation(outcomes: list[dict]) -> list[dict]:
    return [{"variant": "all", "outcomes": outcomes}, {"variant": "skeleton", "outcomes": []}]


def test_the_vote_turns_are_the_items_with_a_winner() -> None:
    report = _ablation(
        [
            {"item_id": "a", "confidence": 1.0, "winner_correct": True},
            {"item_id": "b", "confidence": 0.3333, "winner_correct": False},
            {"item_id": "c", "confidence": 0.0, "winner_correct": None},
        ]
    )
    turns = calibrate.turns_of(report, "sqlgen_vote")
    assert turns == [calibrate.Turn("a", 1.0, True), calibrate.Turn("b", 0.3333, False)]
    with pytest.raises(SystemExit, match="predates EV-19"):
        calibrate.turns_of(_ablation([{"item_id": "a", "confidence": 1.0}]), "sqlgen_vote")


def test_the_reading_turns_are_the_items_that_expect_a_metric_and_were_read_as_one() -> None:
    report = [
        {
            "engine": "governed",
            "outcomes": [
                {"item_id": "a", "expected_metric": "M", "understood": "M", "confidence": 1.0},
                {"item_id": "b", "expected_metric": "M", "understood": "N", "confidence": 0.8},
                {"item_id": "c", "expected_metric": None, "understood": "M", "confidence": 1.0},
                {"item_id": "d", "expected_metric": "M", "understood": None, "confidence": None},
            ],
        }
    ]
    turns = calibrate.turns_of(report, "understanding")
    assert turns == [calibrate.Turn("a", 1.0, True), calibrate.Turn("b", 0.8, False)]


def test_the_report_measures_the_bars_in_the_code_today() -> None:
    turns = (
        [calibrate.Turn(f"a{i}", 1.0, True) for i in range(8)]
        + [calibrate.Turn(f"b{i}", 0.6667, i < 3) for i in range(4)]
        + [calibrate.Turn(f"c{i}", 0.5, i < 2) for i in range(4)]
        + [calibrate.Turn(f"d{i}", 0.3333, False) for i in range(4)]
    )
    r = calibrate.report(turns, "sqlgen_vote")
    answer, review = r["current_bars"]
    # The answer bar is 6/6 since 24/11; review takes 3/6 to 5/6 (4/6 stored as 0.6667).
    assert (answer["threshold"], answer["answered"], answer["errors"]) == (1.0, 8, 0)
    assert (review["threshold"], review["answered"], review["errors"]) == (0.5, 8, 3)
    assert r["turns"] == 20 and r["right"] == 13
    assert set(r["calibrated_out_of_fold"]) == {"ece", "ece_ci95", "brier", "aurc"}
    assert "map fitted on every turn" in calibrate._text(r)


def test_the_cli_writes_the_map_only_when_asked(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "ablation.json"
    source.write_text(
        json.dumps(
            _ablation(
                [
                    {"item_id": str(i), "confidence": c, "winner_correct": c > 0.5 or i % 2 == 0}
                    for i, c in enumerate([1.0, 0.8333, 0.6667, 0.5, 0.3333] * 6)
                ]
            )
        )
    )
    out = tmp_path / "map.json"
    assert calibrate.main(["--producer", "sqlgen_vote", "--from", str(source), "--json"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["turns"] == 30 and not out.exists()
    calibrate.main(
        ["--producer", "sqlgen_vote", "--from", str(source), "--write", str(out), "--commit", "abc"]
    )
    written = json.loads(out.read_text())
    assert written["producer"] == "sqlgen_vote" and written["commit"] == "abc"
    assert set(written["thresholds"]) == {"answer", "review"} and written["metrics"]["n"] == 30
    assert cal.CalibrationMap.from_dict(written).bins


def _cand(method: str, group: int | None, correct: bool) -> dict[str, object]:
    return {"method": method, "status": "ran" if group is not None else "rejected",
            "group": group, "value": None, "correct": correct}  # fmt: skip


def _item(item_id: str, cands: list[dict[str, object]], decision: str = "answer") -> dict:
    return {"item_id": item_id, "decision": decision, "candidates": cands,
            "winner_correct": None, "confidence": 0.0}  # fmt: skip


def test_the_vote_is_counted_again_by_way_of_writing_offline() -> None:
    # Items where skeleton is right alone and the other two agree on a wrong figure: the count
    # vote takes the wrong group (4 of 6); weighted by skeleton's accuracy alone, it does not.
    def trap(i: int) -> dict:
        return _item(f"t{i}", [
            _cand("skeleton", 0, True), _cand("skeleton", 0, True),
            _cand("examples", 1, False), _cand("examples", 1, False),
            _cand("divide", 1, False), _cand("divide", 1, False),
        ])  # fmt: skip

    def easy(i: int) -> dict:
        return _item(f"e{i}", [_cand(m, 0, True) for m in ("skeleton",) * 2 + ("examples",) * 2
                               + ("divide",) * 2])  # fmt: skip

    ids = [f"t{i}" for i in range(5)] + [f"e{i}" for i in range(5)]
    # Alone, skeleton is right on every item, the other two only on the easy ones.
    alone = {
        m: [
            {"item_id": i, "decision": "answer", "winner_correct": m == "skeleton" or i[0] == "e"}
            for i in ids
        ]
        for m in ("skeleton", "examples", "divide")
    }
    report = [
        {"variant": "all", "outcomes": [trap(i) for i in range(5)] + [easy(i) for i in range(5)]}
    ]
    report += [{"variant": m, "outcomes": outs} for m, outs in alone.items()]
    accuracy = calibrate.method_accuracy(report)
    assert accuracy == {"divide": (5, 10), "examples": (5, 10), "skeleton": (10, 10)}
    weights = calibrate.weights_of(accuracy)
    assert weights["examples"] == cal.MIN_WEIGHT and weights["skeleton"] > 2
    rows = calibrate.revote(report, folds=2)
    assert len(rows) == 10
    assert sum(r.share_right for r in rows) == 5 and sum(r.margin_right for r in rows) == 10
    out = calibrate.revote_report(rows, rounds=50)
    assert out["winner_changed"] == {"fixed": 5, "broken": 0}
    assert out["aurc"]["margin"] <= out["aurc"]["share"] and out["passes"] is True


def test_without_variants_alone_the_ways_are_weighed_by_their_candidates() -> None:
    report = [{"variant": "all", "outcomes": [_item("a", [
        _cand("skeleton", 0, True), _cand("skeleton", None, False),
        _cand("divide", 1, False), _cand("divide", 1, False),
    ])]}]  # fmt: skip
    assert calibrate.method_accuracy(report) == {"divide": (0, 2), "skeleton": (1, 2)}


def test_the_weights_file_is_read_when_it_holds_weights(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "sqlgen_vote.json"
    monkeypatch.setattr(cal, "WEIGHTS_FILE", path)
    assert cal.method_weights() is None
    path.write_text(json.dumps({"method_weights": {"skeleton": 0.4}}))
    assert cal.method_weights() == {"skeleton": 0.4}
