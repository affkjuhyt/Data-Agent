# Evaluation & release gate

Four numbers, a versioned dataset behind each of them, and a gate that exits
non-zero. Before this existed the repo had two RAGAS scripts, no dataset
(`test_cases.json` was absent and only obtainable by downloading SQuAD), and
nothing anywhere that could stop a release.

| Metric | Tier | What it answers | Bound |
|---|---|---|---|
| `policy_violation_rate` | offline | Of the inputs that MUST be refused (DDL/DML, statement smuggling, sandbox escapes, path traversal), how many got through? | max 0 |
| `false_refusal_rate` | offline | Of the ordinary inputs that must be ALLOWED, how many were refused? | max 0 |
| `sql_accuracy` | offline | Question → metric resolution → compiled SQL: how often is it the SQL the contract implies? | min 1.0 |
| `intent_accuracy` | online | How often does the real planner classify intent / analysis type correctly? | min 0.85 * |
| `unsupported_recall` | online | Of the questions asking for what this platform CANNOT do (forecast, anomaly), how many were caught? | min 0.95 * |
| `hallucination_rate` | online | RAGAS faithfulness below its floor — answers not made of their sources. | max 0.10 * |
| `unsupported_number_rate` | online | Figures in an answer that appear in no source. Deterministic. | max 0.05 * |

`*` provisional — see *Re-basing* below.

## Why two tiers

Not by subject, by what they need to run.

**offline** is pure Python against the *real* guards (`sql_guard`,
`security_visitor`, the tool-executor path check, the staged-input name rule)
and the *real* metric compiler. No model, no warehouse, no stack; `sqlparse` is
the only third-party import. That is what lets it gate **every pull request** —
a gate that needs a GPU is a gate that runs nightly and blocks nothing.

**online** needs a model behind LiteLLM and an agent to interrogate. It runs
against a deployed environment and gates **releases**
(`.github/workflows/release-gate.yml`). The RAGAS faithfulness judge defaults
to the `judge` LiteLLM alias (override with `EVAL_JUDGE_MODEL`), so the model
that wrote the answers is not the one grading them.

Nothing here re-implements what it measures. The policy scorer imports the
guards; the intent scorer calls the real planner node; the grounding scorer uses
the shipped responder prompt and the shipped analysis-type guidance. An eval
that vendored its own copy would grade the copy.

## Running it

```bash
# offline — what CI runs on every PR
pip install sqlparse
python -m model.eval.run  --tier offline --out eval-report.json
python -m model.eval.gate --report eval-report.json --tier offline

# both tiers, against a live stack
export LITELLM_BASE_URL=http://localhost:8500 AGENT_ID=<uuid>
python -m model.eval.run  --tier all --out eval-report.json
python -m model.eval.gate --report eval-report.json --tier offline --tier online

# or in the stack's own network
kubectl apply -f deploy/k8s/job/eval.yaml
```

`run.py` always exits 0 when the run completed — judging is `gate.py`'s job.
Splitting them is what lets a pipeline archive a failing report instead of
losing it when the step dies.

## The dataset

`cases/*.jsonl`, one case per line, each with an `id` the reports key on
("sql-011 regressed" is actionable; "3 of 25 failed" is not). JSONL so a diff
shows exactly which case changed and a reviewer can argue with a single line.

- `policy.jsonl` — adversarial inputs **and** benign ones. Both directions are
  required: a corpus of only-refuse cases is passed by a guard that refuses
  everything.
- `sql.jsonl` — questions plus a request, expected against the *contract*
  (required and forbidden SQL fragments), never against the compiler's own
  output. Pasting the output back in as the expectation produces a test that
  passes by construction.
- `intent.jsonl` — a case pins `analysis_type`, `intent`, or both. They are
  orthogonal, and a case with an opinion about one axis is not graded on the
  other.
- `grounding.jsonl` — question, contexts, ground truth, and `expect:
  grounded|refuse`. The refusal cases (an unpublished price, a period with no
  data, a forecast off a tidy trend) are the sharp ones: every figure in an
  answer to them is unsupported by construction.

`catalogs.json` holds the metric contracts the SQL cases are written against —
two revenue metrics differing only by refunds, a COUNT vs COUNT_DISTINCT pair,
an always-on filter. Those are the three confusions the semantic layer exists to
end, so they are what the corpus measures.

## Re-basing the provisional thresholds

The offline bounds are absolute (0 / 0 / 1.0) because those scorers are
deterministic and sit exactly there today: no sampling, no judge, no flake, so
any movement is a regression and slack would only hide it.

The online bounds are **targets, not measurements** — this repo has no recorded
baseline against a live model. After the first green run against staging:

1. Read the metrics out of that run's `eval-report.json`.
2. Set each bound at the measured value minus a deliberate margin (a judged
   metric moves run to run; a deterministic one should not).
3. Drop `"provisional": true` in `thresholds.json` and say in the `why` what
   run the number came from.

A unit test asserts the online bounds are still flagged provisional, so this
stays visible until someone does it rather than quietly becoming folklore.

## Adding a case

Append a line, run the tier, and check the case fails for the reason you expect
before you make it pass. A case added green has never demonstrated it can go
red, which is the only thing that makes it worth its runtime.
