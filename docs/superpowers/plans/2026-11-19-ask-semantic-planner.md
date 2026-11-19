# Ask semantic planner — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A question that names no certified metric goes to the planner, and an overview question is a request with no numeric intent, so it is not asked to pick a metric.

**Architecture:** `RequestContract` says whether a question needs a metric. `router.classify` keeps fast governed only for one resolved certified metric. `UNCATALOGUED` goes to the existing agentic planner, which already answers a non-figure question from `<corpus>`. Two certified candidates that share a stem stay on governed until a later plan teaches the planner that menu.

**Tech Stack:** Python 3.11, the existing `router.classify` and `agentic` planner, pytest via `make test-unit`.

**Spec:** `docs/superpowers/specs/2026-11-19-ask-semantic-planner-design.md`

## Global Constraints

- Do not add a route, tool, or prompt branch per question wording. Overview, table list, and document list are `purpose` values, not new endpoints.
- A certified metric with its required period stays on `query_metric` / the governed compiler. An `explore_sql` result stays exploratory.
- `numeric_intent=none` never asks the user to choose a metric.
- `ACCESS_DENIED`, `NO_DATA`, and `NO_READABLE_ASSET` stay three codes. This plan does not map them onto user sentences yet.
- `AMBIGUOUS` stays `governed`. Do not send “Lãi gộp năm 2013” to the planner in this plan.
- `FORBIDDEN` and `GONE` stay `governed`. `ASK_ENGINE=agentic` and `ASK_ENGINE=governed` stay the rollback.
- Do not delete `llm_reader`. Do not rebuild `DataSpaceContext`. Do not add a plan validator.
- Tests: `make test-unit UNIT_SUITES=worker_agent:worker-agent PYTEST_ARGS="…"`. Never the host interpreter.
- No new ruff findings on touched files (`make fmt-files FILES="…"`).
- After code edits, `graphify update .`

## Review Focus

- “Các dữ liệu ở đây là về gì?” on a vocabulary with no such metric is `agentic` / `UNCATALOGUED`, and its contract says `numeric_intent=none`. Pinned in Tasks 1 and 3.
- “Doanh thu năm 2013” with one certified metric stays `governed` / `RESOLVED`. Pinned in Task 3.
- “Lãi gộp năm 2013” with two certified metrics that share the stem stays `governed` / `AMBIGUOUS`. Pinned in Task 3.
- A period with no rows is still not answered as 0. This plan does not touch `present_gate`. The existing `test_no_figure_still_blocks_an_all_zero_period` remains the pin; Task 3 must not edit `present_gate.py`.
- A principal who cannot ask the space still gets `governed` / `FORBIDDEN` and is not handed the corpus. `decide` already returns that before `classify`. Task 3 must not move that return.

---

### Task 1: Request contract

**Files:**
- Create: `backend/services/worker_agent/ask/__init__.py`
- Create: `backend/services/worker_agent/ask/contract.py`
- Test: `tests/unit/worker_agent/test_ask_contract.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `Purpose` with values `orient`, `answer`, `analyze`, `explain`, `compare`, `document`
  - `NumericIntent` with values `none`, `required`, `optional`
  - `AskProblem` with values `ACCESS_DENIED`, `NO_DATA`, `NO_READABLE_ASSET`, `AMBIGUOUS_REQUEST`, `MISSING_REQUIRED_SLOT`, `UNSUPPORTED_REQUEST`, `INSUFFICIENT_EVIDENCE`
  - `RequestContract(purpose, numeric_intent, missing_required_slots: tuple[str, ...] = ())`
  - `RequestContract.needs_metric_choice() -> bool`

- [ ] **Step 1: Write the failing test**

```python
def test_an_overview_does_not_ask_for_a_metric() -> None:
    contract = RequestContract(Purpose.ORIENT, NumericIntent.NONE)
    assert contract.needs_metric_choice() is False


def test_a_required_number_with_no_metric_asks() -> None:
    contract = RequestContract(
        Purpose.ANSWER, NumericIntent.REQUIRED, missing_required_slots=("metric",)
    )
    assert contract.needs_metric_choice() is True
```

Mark the module `pytest.mark.unit`.

- [ ] **Step 2: Run it and see it fail**

Run: `make test-unit UNIT_SUITES=worker_agent:worker-agent PYTEST_ARGS="tests/unit/worker_agent/test_ask_contract.py"`

Expected: FAIL, `RequestContract` cannot be imported.

- [ ] **Step 3: Implement the types**

`needs_metric_choice` is true only when `numeric_intent` is `NumericIntent.REQUIRED` and `"metric"` is one of `missing_required_slots`. An empty package `__init__.py` does not re-export the types.

- [ ] **Step 4: Re-run the test**

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/services/worker_agent/ask/__init__.py backend/services/worker_agent/ask/contract.py tests/unit/worker_agent/test_ask_contract.py
git commit -m "feat: a request says whether it needs a metric"
```

### Task 2: Eval cases for the planner gate

**Files:**
- Create: `model/eval/cases/ask-planner.jsonl`
- Test: `tests/unit/worker_agent/test_ask_contract.py`

**Interfaces:**
- Consumes: `Purpose`, `NumericIntent` from Task 1
- Produces: one JSON object per line with `id`, `question`, `purpose`, `numeric_intent`

- [ ] **Step 1: Extend the failing test**

Load `model/eval/cases/ask-planner.jsonl` from the repo root. Assert a row `id` `orient-what-data` has question `Các dữ liệu ở đây là về gì?`, `purpose` `orient`, `numeric_intent` `none`. Assert a row `id` `resolved-revenue` has question `Doanh thu năm 2013`, `purpose` `answer`, `numeric_intent` `required`. Assert the set of `purpose` values in the file is a subset of the `Purpose` values, and the same for `numeric_intent`.

- [ ] **Step 2: Run it and see it fail**

Same command as Task 1. Expected: FAIL, the file is missing.

- [ ] **Step 3: Write the cases**

One JSON object per line, at least: `orient-what-data`, `orient-which-tables` (question `Có những bảng nào?`, `orient`, `none`), `orient-which-docs` (question `Có tài liệu gì?`, `document`, `none`), `resolved-revenue`, `ambiguous-margin` (question `Lãi gộp năm 2013`, `answer`, `required`). Do not call a model. Do not score the rows.

- [ ] **Step 4: Re-run the test**

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add model/eval/cases/ask-planner.jsonl tests/unit/worker_agent/test_ask_contract.py
git commit -m "test: pin overview questions as requests with no numeric intent"
```

### Task 3: Uncatalogued questions go to the planner

**Files:**
- Modify: `backend/services/worker_agent/router.py` (`classify`, the final return)
- Test: `tests/unit/worker_agent/test_router.py`

**Interfaces:**
- Consumes: nothing from Task 1. The route is still `Route(path, reason)`.
- Produces: `classify` returns `Route("agentic", "UNCATALOGUED")` when no earlier branch matched.

- [ ] **Step 1: Change the existing assertion and add the overview one**

In `test_a_question_goes_where_it_can_be_answered`, the row `Giá cổ phiếu của công ty hôm nay` expects `agentic`, `UNCATALOGUED`. Add a row `Các dữ liệu ở đây là về gì?` with the same path and reason. Leave `Lãi gộp năm 2013` as `governed`, `AMBIGUOUS`, and `Doanh thu năm 2013` as `governed`, `RESOLVED`.

- [ ] **Step 2: Run it and see it fail**

Run: `make test-unit UNIT_SUITES=worker_agent:worker-agent PYTEST_ARGS="tests/unit/worker_agent/test_router.py::test_a_question_goes_where_it_can_be_answered"`

Expected: FAIL, those two texts are still `governed`.

- [ ] **Step 3: Change only the final return of `classify`**

Replace `return Route("governed", "UNCATALOGUED")` with `return Route("agentic", "UNCATALOGUED")`. Do not change the `AMBIGUOUS`, `RESOLVED`, `FORBIDDEN`, `GONE`, `WHY`, `MULTI_METRIC`, `HYBRID`, `DOC_ONLY`, or `DEEP` returns. Do not edit `present_gate.py` or `llm_reader.py`.

- [ ] **Step 4: Re-run the test**

Expected: PASS. Then run `make test-unit UNIT_SUITES=worker_agent:worker-agent PYTEST_ARGS="tests/unit/worker_agent/test_router.py tests/unit/worker_agent/test_ask_contract.py"`.

- [ ] **Step 5: Commit**

```bash
git add backend/services/worker_agent/router.py tests/unit/worker_agent/test_router.py
git commit -m "feat: a question with no certified metric goes to the planner"
```

### Task 4: Record the gate where AE-01 wrote the old rule

**Files:**
- Modify: `docs/blueprint/ASK_ENGINE_SPEC.md` (the AE-01 “Đã làm” bullet that says `UNCATALOGUED` goes to governed)

**Interfaces:**
- Consumes: the route Task 3 produces
- Produces: a reader of §2 no longer restores `UNCATALOGUED` → governed

- [ ] **Step 1: Rewrite that one bullet**

The bullet that lists reasons still says `UNCATALOGUED` → governed. Say `UNCATALOGUED` → agentic, because the planner answers a non-figure question from the corpus. `AMBIGUOUS` stays governed until the planner asks with the real certified candidates. `RESOLVED` stays governed. `FORBIDDEN` and `GONE` stay governed. Do not rewrite the rest of the section.

- [ ] **Step 2: Commit**

```bash
git add docs/blueprint/ASK_ENGINE_SPEC.md
git commit -m "docs: an uncatalogued question is planned, not read as a metric"
```

## Follow-on plans, not tasks of this one

Do not start these while this plan is open.

- One `DataSpaceContext` builder over `load_corpus`, the semantic loader, and document collections, keyed by principal and context version.
- Capability descriptors and a Plan IR shared by governed and agentic, then the static validator.
- Move `AMBIGUOUS` to the planner only after a test shows “Lãi gộp năm 2013” still asks between the two certified metrics.
- `ASK_PLANNER_SHADOW`, then delete the metric-first conclusion in `llm_reader`.
