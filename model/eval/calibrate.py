"""How well a producer's confidence says how often it is right (EV-19, CONFIDENCE_SPEC §2-3, §6).

Reads one report and scores one producer on it:

* `sqlgen_vote`: `make sqlgen-ablation ABLATION_ARGS="--variants all --json"`; a turn is an item
  whose vote had a winner, x is the vote's share (k of the candidates written), and it is right
  when the winner's value is (`winner_correct`, whatever the vote decided).
* `understanding`: `make agent-eval AGENT_EVAL_ARGS="--engines governed --json"`; a turn is an
  item that expects a metric and was read as one, x is the reading's raw score (1.0 a name or
  code, 0.9 a synonym, 0.8 a model's reading), and it is right when the metric is the expected one.

The report gives the raw score taken as a probability (is it one today?), the bars production
uses now (4/6 and 3/6 of the vote, 0.8 of the reading) as coverage and error, and the calibrated
map: out-of-fold ECE (with a bootstrap interval), Brier and AURC, the bars an accepted risk would
set, and the map fitted on every turn. `--write PATH` also writes that map with its thresholds.

    make calibrate CALIBRATE_PRODUCER=sqlgen_vote CALIBRATE_FROM=<ablation.json>
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any

from model.eval import _paths

_paths.ensure()

from libs import calibration as cal  # noqa: E402

PRODUCERS = ("sqlgen_vote", "understanding")
# The bars in the code today (sqlgen/vote.py ANSWER_AT, REVIEW_AT of CANDIDATES;
# understand/resolver.py PROCEED_AT): what the baseline measures.
CURRENT: dict[str, list[tuple[str, float]]] = {
    "sqlgen_vote": [("answer", 4 / 6), ("review", 3 / 6)],
    "understanding": [("proceed", 0.8)],
}
# Accepted risks, provisional (CONFIDENCE_SPEC §2.4): a misread metric is wrong in a way the
# reader cannot see, so its bar is stricter.
R_ANSWER = {"sqlgen_vote": 0.05, "understanding": 0.03}
R_REVIEW = 0.20
# A share stored rounded to four places (ablation.py) must still meet the bar it equals.
EPSILON = 1e-4


@dataclass(frozen=True)
class Turn:
    item_id: str
    x: float
    y: bool


def turns_of(
    report: Any, producer: str, *, variant: str = "all", engine: str = "governed"
) -> list[Turn]:
    """The producer's turns in a report of the ablation or of agent-eval."""
    if not isinstance(report, list):
        raise SystemExit(
            "a report is a JSON list of summaries (--json of the ablation or agent-eval)"
        )
    if producer == "sqlgen_vote":
        summary = next((s for s in report if s.get("variant") == variant), None)
        if summary is None:
            raise SystemExit(f"no variant {variant!r} in the report: is it an ablation --json?")
        out = []
        for o in summary["outcomes"]:
            if "winner_correct" not in o:
                raise SystemExit(
                    "the ablation report predates EV-19 (no winner_correct): run it again"
                )
            if o["winner_correct"] is not None:
                out.append(
                    Turn(str(o["item_id"]), float(o["confidence"]), bool(o["winner_correct"]))
                )
        return out
    if producer == "understanding":
        summary = next((s for s in report if s.get("engine") == engine), None)
        if summary is None:
            raise SystemExit(f"no engine {engine!r} in the report: is it an agent-eval --json?")
        return [
            Turn(str(o["item_id"]), float(o["confidence"]), o["understood"] == o["expected_metric"])
            for o in summary["outcomes"]
            if o.get("expected_metric") and o.get("understood") and o.get("confidence") is not None
        ]
    raise SystemExit(f"unknown producer {producer!r}; known: {', '.join(PRODUCERS)}")


def _quality(ps: Sequence[float], ys: Sequence[bool]) -> dict[str, Any]:
    low, high = cal.bootstrap(cal.ece, ps, ys)
    return {
        "ece": round(cal.ece(ps, ys), 4),
        "ece_ci95": [round(low, 4), round(high, 4)],
        "brier": round(cal.brier(ps, ys), 4),
        "aurc": round(cal.aurc(ps, ys), 4),
    }


def _point(name: str, p: cal.Operating) -> dict[str, Any]:
    return {
        "decision": name,
        **{k: round(v, 4) if isinstance(v, float) else v for k, v in asdict(p).items()},
    }


def report(
    turns: Sequence[Turn],
    producer: str,
    *,
    folds: int = 5,
    r_answer: float | None = None,
    r_review: float = R_REVIEW,
) -> dict[str, Any]:
    xs = [t.x for t in turns]
    ys = [t.y for t in turns]
    n = len(turns)
    out: dict[str, Any] = {"producer": producer, "turns": n, "right": sum(ys)}
    if not n:
        return out
    out["accuracy"] = round(sum(ys) / n, 4)
    raw = [min(max(x, 0.0), 1.0) for x in xs]
    out["raw_as_probability"] = _quality(raw, ys)
    bars = CURRENT[producer]
    current = []
    for i, (name, bar) in enumerate(bars):
        ceiling = bars[i - 1][1] - EPSILON if i else float("inf")
        current.append(
            {**_point(name, cal.band(xs, ys, bar - EPSILON, ceiling)), "threshold": round(bar, 4)}
        )
    out["current_bars"] = current
    ps, lows = cal.cross_fit(xs, ys, producer, folds=folds)
    out["calibrated_out_of_fold"] = _quality(ps, ys)
    r_answer = R_ANSWER[producer] if r_answer is None else r_answer
    tau_answer, tau_review = cal.choose_thresholds(lows, ys, r_answer, r_review)
    chosen: dict[str, Any] = {
        "r_answer": r_answer,
        "r_review": r_review,
        "answer": tau_answer,
        "review": tau_review,
    }
    top = tau_answer if tau_answer is not None else float("inf")
    if tau_answer is not None:
        chosen["answer_band"] = _point("answer", cal.band(lows, ys, tau_answer))
    if tau_review is not None:
        chosen["review_band"] = _point("review", cal.band(lows, ys, tau_review, top))
    out["thresholds_out_of_fold"] = chosen
    out["risk_coverage_out_of_fold"] = [_point("", p) for p in cal.risk_coverage(ps, ys)]
    fitted = cal.fit(xs, ys, producer)
    out["map"] = fitted.to_dict()
    return out


def _bar(value: float | None, missing: str) -> str:
    return missing if value is None else f"{value:.4f}"


def _text(r: dict[str, Any]) -> str:
    if not r.get("turns"):
        return f"{r['producer']}: no turn to score"
    lines = [
        f"{r['producer']}: {r['turns']} turns, {r['right']} right ({r['accuracy']:.1%})",
        "",
        "                       ECE    ECE 95% CI        Brier   AURC",
    ]
    for name, key in (
        ("raw as probability", "raw_as_probability"),
        ("calibrated (oof)", "calibrated_out_of_fold"),
    ):
        q = r[key]
        lines.append(
            f"  {name:<19} {q['ece']:>6.4f}  [{q['ece_ci95'][0]:.4f}, {q['ece_ci95'][1]:.4f}]"
            f"  {q['brier']:>6.4f}  {q['aurc']:>6.4f}"
        )
    lines += ["", "bars in the code today       answered  coverage  errors  risk    risk ≤ (95%)"]
    for p in r["current_bars"]:
        lines.append(
            f"  {p['decision']:<8} x ≥ {p['threshold']:<10.4f} {p['answered']:>8}  {p['coverage']:>8.1%}"
            f"  {p['errors']:>6}  {p['risk']:>5.1%}   {p['risk_high']:>6.1%}"
        )
    t = r["thresholds_out_of_fold"]
    lines += [
        "",
        f"bars from accepted risk (p_low, out of fold; r_answer {t['r_answer']}, r_review {t['r_review']}):",
        f"  answer at p_low ≥ {_bar(t['answer'], 'none: no bar keeps the risk')}",
        f"  review at p_low ≥ {_bar(t['review'], 'none')}",
    ]
    for key in ("answer_band", "review_band"):
        if key in t:
            p = t[key]
            lines.append(
                f"    {p['decision']:<7} answered {p['answered']}, coverage {p['coverage']:.1%},"
                f" risk {p['risk']:.1%} (≤ {p['risk_high']:.1%})"
            )
    lines += ["", "map fitted on every turn      n  right  raw acc   p       p_low"]
    for b in r["map"]["bins"]:
        span = f"{b['lo']:.4f}" if b["lo"] == b["hi"] else f"{b['lo']:.4f}-{b['hi']:.4f}"
        lines.append(
            f"  x {span:<24} {b['n']:>3}  {b['correct']:>5}  {b['correct'] / b['n'] if b['n'] else 0:>7.1%}"
            f"  {b['p']:.4f}  {b['p_low']:.4f}"
        )
    return "\n".join(lines)


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--producer", required=True, choices=PRODUCERS)
    parser.add_argument("--from", dest="source", default="-", help="the report; - reads stdin")
    parser.add_argument("--variant", default="all", help="the ablation's variant (sqlgen_vote)")
    parser.add_argument("--engine", default="governed", help="agent-eval's engine (understanding)")
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--r-answer", type=float, default=None)
    parser.add_argument("--r-review", type=float, default=R_REVIEW)
    parser.add_argument("--commit", default=None, help="the commit the report was run at")
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--write", default=None, help="write the fitted map and its thresholds here"
    )
    args = parser.parse_args(argv)
    if args.source == "-":
        raw = sys.stdin.read()
    else:
        with open(args.source, encoding="utf-8") as f:
            raw = f.read()
    turns = turns_of(json.loads(raw), args.producer, variant=args.variant, engine=args.engine)
    r = report(
        turns, args.producer, folds=args.folds, r_answer=args.r_answer, r_review=args.r_review
    )
    r["source"] = args.source
    r["commit"] = args.commit
    if args.write and r.get("turns"):
        t = r["thresholds_out_of_fold"]
        with open(args.write, "w", encoding="utf-8") as f:
            json.dump(
                {
                    **r["map"],
                    "source": args.source,
                    "commit": args.commit,
                    "thresholds": {"answer": t["answer"], "review": t["review"]},
                    "metrics": {**r["calibrated_out_of_fold"], "n": r["turns"]},
                },
                f,
                ensure_ascii=False,
                indent=1,
            )
            f.write("\n")
    print(json.dumps(r, ensure_ascii=False, indent=1) if args.json else _text(r))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
