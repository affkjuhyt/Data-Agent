# Data agent đọc được bộ dữ liệu — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The ask agent answers from the Data Space's tables, draft and certified metrics, dimensions, document collections and knowledge pages, and may write exploratory SQL when no certified metric matches.

**Architecture:** A `CorpusBrief` is rendered into `<corpus>` on the planner prompt. Certified-metric questions stay on `query_metric`. Unmatched quantity questions go to `explore_sql` instead of the RES-04 refusal. Orientation questions answer from the corpus in `mode=answer`.

**Tech Stack:** Python 3.11, the existing `load_space` schema, worker-agent agentic planner, pytest via `make test-unit`.

**Spec:** `docs/superpowers/specs/2026-11-19-data-agent-corpus-design.md`

## Global Constraints

- Tests run with `make test-unit UNIT_SUITES=worker_agent:worker-agent PYTEST_ARGS="…"`. Never the host interpreter.
- No new ruff findings on touched files (`make fmt-files FILES="…"`).
- Numbers in an answer stay `{{F<n>}}` facts from a tool result. A certified-metric hit stays governed. An `explore_sql` result stays exploratory.
- Columns with `pii_label` and other people's personal datasets never appear in `<corpus>` (`load_space` already drops them; do not add a second copy of those rows).
- Draft metrics are listed as chưa chứng nhận and are not added to `analyst._visible`.
- After code edits, `graphify update .`

## Review Focus

- “dữ liệu hiện có nói về gì” on a space with tables and zero certified metrics describes the tables and does not `ask_user` with an empty option list. Pinned in Task 4.
- “doanh thu yellow tháng 1/2024” with no certified metric is not refused before SQL with “không tự tính”. Pinned in Task 4.
- One certified metric still named by the question still goes through `query_metric`; `audit_rules.context_for` still binds that metric. Pinned in Task 4.
- A period whose rows are all empty or zero still hits `present_gate.no_figure` and does not answer 0. Pinned in Task 4.
- A space with no document collection, asked what documents it has, renders `Tài liệu:\nkhông có` and does not name an AdventureWorks collection. Pinned in Task 1.

---

### Task 1: Corpus brief and its text

**Files:**
- Create: `backend/services/worker_agent/agentic/corpus.py`
- Test: `tests/unit/worker_agent/test_corpus.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `CorpusTable(qualified: str, label: str, description: str | None, columns: tuple[str, ...])`
  - `CorpusMetric(code: str, name: str, certified: bool)`
  - `CorpusDoc(name: str, media_kinds: tuple[str, ...])`
  - `CorpusPage(title: str, kind: str, summary: str)`
  - `CorpusBrief(tables, joins: tuple[str, ...], metrics, dimensions: tuple[str, ...], documents, pages)` with those tuples frozen
  - `render_corpus(brief: CorpusBrief) -> str`

- [ ] **Step 1: Write the failing test**

```python
def test_render_names_tables_drafts_and_an_empty_document_shelf() -> None:
    from services.worker_agent.agentic.corpus import (
        CorpusBrief, CorpusDoc, CorpusMetric, CorpusTable, render_corpus,
    )
    text = render_corpus(CorpusBrief(
        tables=(CorpusTable("tlc.yellow_trip", "Chuyến taxi vàng", "chuyến vàng 2024", ("fare_amount (number): tiền cước",)),),
        joins=("tlc.yellow_trip.pu_location_id = tlc.zone.location_id",),
        metrics=(CorpusMetric("DOANH_THU_VANG", "Doanh thu taxi vàng", False),),
        dimensions=("thoi_diem_don: Thời điểm đón",),
        documents=(),
        pages=(),
    ))
    assert "tlc.yellow_trip" in text
    assert "fare_amount" in text
    assert "DOANH_THU_VANG" in text and "chưa chứng nhận" in text
    assert "Tài liệu:\nkhông có" in text
    assert "DOANH_THU_INTERNET" not in text
```

- [ ] **Step 2: Run the test and see it fail**

Run: `make test-unit UNIT_SUITES=worker_agent:worker-agent PYTEST_ARGS="tests/unit/worker_agent/test_corpus.py::test_render_names_tables_drafts_and_an_empty_document_shelf"`

Expected: FAIL, `corpus` cannot be imported.

- [ ] **Step 3: Implement `render_corpus`**

Headings, in this order, each followed by lines or the single word `không có`: `Bảng`, `Quan hệ`, `Chỉ số đã chứng nhận`, `Chỉ số nháp`, `Chiều`, `Tài liệu`, `Trang tri thức`. A draft metric line is `{code}: {name} (chưa chứng nhận)`. A document line is `{name} ({", ".join(media_kinds)})`. A page line is `{title} [{kind}]: {summary}`. Cap at 30 tables, 40 columns, 20 documents, 20 pages; do not render the rest.

- [ ] **Step 4: Re-run the test**

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/services/worker_agent/agentic/corpus.py tests/unit/worker_agent/test_corpus.py
git commit -m "feat: render a Data Space corpus for the planner"
```

### Task 2: Build the brief from the space the asker may read

**Files:**
- Modify: `backend/services/worker_agent/agentic/corpus.py`
- Test: `tests/unit/worker_agent/test_corpus.py`

**Interfaces:**
- Consumes: `sqlgen.space.Space` (`schema.tables`, `schema.fks`), `semantic.loader.SpaceLayer`
- Produces:
  - `build_corpus(space: Space, *, certified: tuple[CorpusMetric, ...], drafts: tuple[CorpusMetric, ...], dimensions: tuple[str, ...], documents: tuple[CorpusDoc, ...], pages: tuple[CorpusPage, ...]) -> CorpusBrief`
  - `async def load_corpus(session, space_id: uuid.UUID, principal_id: uuid.UUID, layer: SpaceLayer) -> CorpusBrief`

- [ ] **Step 1: Write the failing test**

A `Space` with one `Tbl` `tlc.yellow_trip` and one `Fk` builds a brief whose table qualified name is `tlc.yellow_trip` and whose join string contains both column lists. Certified metrics stay certified. Drafts passed in are `certified=False`. Documents and pages are copied through.

- [ ] **Step 2: Run it and see it fail**

Expected: FAIL, `build_corpus` is missing.

- [ ] **Step 3: Implement `build_corpus` and `load_corpus`**

`build_corpus` maps `Tbl.qualified`, `Tbl.label`, `Tbl.description`, and each `Col` as `"{name} ({kind}): {label}"`. A join line is `"{from_qualified}.{from_columns} = {to_qualified}.{to_columns}"`.

`load_corpus` calls `load_space(session, space_id, principal_id)`. Certified metrics come from `analyst._visible(layer)` as `CorpusMetric(code, name, True)`. Drafts are `context.Metric` in this space with `status == "draft"`, display name from the highest `metric_version.version`, as `CorpusMetric(code, display_name, False)`. Dimensions are `f"{d.code}: {d.name}"` from `layer.vocabulary.dimensions`. Documents are `document.Collection` of this space with `status == "active"` and `deleted_at is None`, as `CorpusDoc(name, tuple(media_kinds))`. Pages are `context.KnowledgePage` of this space with `status == "active"`, as `CorpusPage(title, kind, index_summary)`. Then `build_corpus`.

- [ ] **Step 4: Re-run `tests/unit/worker_agent/test_corpus.py`**

Expected: PASS. `load_corpus` is not executed in the unit test.

- [ ] **Step 5: Commit**

```bash
git add backend/services/worker_agent/agentic/corpus.py tests/unit/worker_agent/test_corpus.py
git commit -m "feat: build the planner corpus from tables, drafts and documents"
```

### Task 3: Show the corpus to the planner

**Files:**
- Modify: `backend/services/worker_agent/agentic/prompts.py` (rules 2, 5 and 7; the `DOANH_THU_INTERNET` example)
- Modify: `backend/services/worker_agent/agentic/engine.py` `_planner_system` (around the `{catalog}` replace)
- Modify: `backend/services/worker_agent/agentic/deep_skill.py` (the line that forbids `explore_sql` without a matching metric)
- Modify: `backend/services/worker_agent/agentic/tools/query.py` `ExploreSql.description`
- Test: `tests/unit/worker_agent/test_corpus.py`

**Interfaces:**
- Consumes: `render_corpus`, `load_corpus`
- Produces: `_planner_system` text contains `<corpus>` from `s.ctx.corpus`

`ToolContext` and `State.ctx` gain `corpus: CorpusBrief | None = None`. The prepare step that already loads the layer awaits `load_corpus` once and stores it. A missing corpus renders as an empty brief, not an exception.

- [ ] **Step 1: Write the failing test**

`prompts.PLANNER` does not contain `DOANH_THU_INTERNET`. It contains the sentence: a question that does not ask for a figure is `mode=answer` from `<corpus>`. `ExploreSql.description` does not contain `Cấm tự tính`.

- [ ] **Step 2: Run it and see it fail**

Expected: FAIL, the example and the ban are still in the strings.

- [ ] **Step 3: Change the prompt and inject the corpus**

Replace rule 5 with: a question that does not ask for a figure (`dữ liệu có gì`, bảng nào, tài liệu nào) is `mode="answer"` from `<corpus>`, with no `search_catalog` and no `ask_user`. A figure question whose `<corpus>` has one matching certified metric uses `query_metric`. Otherwise `explore_sql` on tables named in `<corpus>`. No related table: say there is no column to compute it. Delete the `DOANH_THU_INTERNET` example in rule 2. Narrow rule 7 so “nói gì” routes to documents only when `<corpus>` lists a collection or the question names a document. `deep_skill.py` drops the same ban. `ExploreSql.description` says it writes SQL on the corpus tables when no certified metric matches, and the result is exploratory. In `_planner_system`, append `\n<corpus>\n{render_corpus(corpus)}\n</corpus>` after the catalog.

- [ ] **Step 4: Re-run the test**

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/services/worker_agent/agentic/prompts.py backend/services/worker_agent/agentic/engine.py backend/services/worker_agent/agentic/deep_skill.py backend/services/worker_agent/agentic/tools/query.py tests/unit/worker_agent/test_corpus.py
git commit -m "feat: plan from the Data Space corpus instead of a metric example"
```

### Task 4: Let exploratory SQL run, and stop the empty ask

**Files:**
- Modify: `backend/services/worker_agent/agentic/tools/query.py` (`ExploreSql.run` catalogue refusal; the `query_metric` branch that returns “không tự tính bằng SQL thăm dò”)
- Modify: `backend/services/worker_agent/agentic/present_gate.py` (the `unnamed` hit whose feedback says không tự tính)
- Modify: `backend/services/worker_agent/agentic/engine.py` `_ask_for_unnamed_question` and `_unnamed_quantity`
- Test: `tests/unit/worker_agent/test_agentic_core.py` (the RES-04 tests around `test_explore_sql_refuses_a_quantity_no_metric_in_the_catalogue_names` and `test_explore_sql_answer_for_an_uncatalogued_quantity_hands_the_question_back`)

**Interfaces:**
- Consumes: the corpus on the context. Does not read it to decide the refusal; the refusal goes away.
- Produces: `ExploreSql.run` no longer returns `detail["unnamed"]` for a quantity the catalogue does not name. `present_gate.before_write` no longer returns a hit solely because `unnamed` is non-empty. `_ask_for_unnamed_question` returns False when the catalogue names no metric for the question.

- [ ] **Step 1: Rewrite the two tests first**

Delete the body of `test_explore_sql_refuses_a_quantity_no_metric_in_the_catalogue_names` that calls `ExploreSql.run`. That call used to stop before the database; after this task it would open a session. Replace it with a `present_gate.before_write` call whose `unnamed` is `["ty", "le"]` and whose workspace holds one `explore_sql` dataset with a row: the result is `None`.

`test_explore_sql_answer_for_an_uncatalogued_quantity_hands_the_question_back`: `_unnamed_quantity` on an `explore_sql` dataset is `[]`, and `_ask_for_unnamed_question` is False.

Add `test_no_figure_still_blocks_an_all_zero_period`: `present_gate.before_write` with `no_figure` true still returns the hit whose target starts with `Không có số liệu`.

Add `test_certified_metric_still_binds_the_audit`: `audit_rules.context_for` on a question the resolver reads as one metric still returns that metric. Do not change `audit_rules.py`.

- [ ] **Step 2: Run those tests and see the new assertions fail**

Expected: FAIL on `không tự tính` still present, and on `_unnamed_quantity` still non-empty.

- [ ] **Step 3: Remove the three refusals**

Delete the block in `ExploreSql.run` that returns “không tự tính bằng SQL thăm dò”. Delete the `query_metric` branch that returns the same sentence for leftover words; keep the branch that rewrites a missed cut as `failure="arguments"`. Delete the `if unnamed:` hit in `present_gate.before_write`. `_unnamed_quantity` returns `[]` when the only readings are `explore_sql`. `_ask_for_unnamed_question` returns False when `unnamed_quantity(...)` would have been the empty-catalogue ask. Leave `no_figure`, `out_of_time`, and `dropped_members` in place.

- [ ] **Step 4: Re-run**

Run: `make test-unit UNIT_SUITES=worker_agent:worker-agent PYTEST_ARGS="tests/unit/worker_agent/test_corpus.py tests/unit/worker_agent/test_agentic_core.py tests/unit/worker_agent/test_language_packs.py"`

Expected: PASS. `make fmt-files FILES="backend/services/worker_agent/agentic/corpus.py backend/services/worker_agent/agentic/tools/query.py backend/services/worker_agent/agentic/present_gate.py backend/services/worker_agent/agentic/engine.py backend/services/worker_agent/agentic/prompts.py tests/unit/worker_agent/test_corpus.py tests/unit/worker_agent/test_agentic_core.py"` is clean.

- [ ] **Step 5: Commit**

```bash
git add backend/services/worker_agent/agentic/tools/query.py backend/services/worker_agent/agentic/present_gate.py backend/services/worker_agent/agentic/engine.py tests/unit/worker_agent/test_agentic_core.py
git commit -m "feat: answer uncatalogued questions from the space's tables"
```

### Task 5: Record the decision where the old ban is written as done

**Files:**
- Modify: `docs/v3/04-backlog.md` RES-04 row
- Modify: `docs/v3/eval/ds-aw-agent-eval.md` the paragraph that says stock items were refused because “cuối” was an unnamed quantity

- [ ] **Step 1: Update the two paragraphs**

RES-04 keeps the half that is still true: a period with no rows is not answered as 0, and prose does not invent a figure. The half that forbade exploratory SQL for a quantity no metric names is withdrawn; that question is exploratory SQL on the space's tables, or “không có cột để tính” when `link` finds none. The eval note says the “refused correctly” threshold has to be re-scored and is not re-run in this change.

- [ ] **Step 2: Commit**

```bash
git add docs/v3/04-backlog.md docs/v3/eval/ds-aw-agent-eval.md
git commit -m "docs: exploratory SQL is allowed when no certified metric matches"
```
