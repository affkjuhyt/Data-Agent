# Ask chat redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every Ask turn shows a live and replayable timeline of what the agent did, each step with its detail, and every answer is drawn from its blocks in a new chat layout.

**Architecture:** The worker emits each step once through `turn_trace.stepping()`, which publishes the `activity/step` stream event and keeps the step for the turn's single `agent_run` write. The api serves the persisted steps at `GET /v1/questions/{id}/steps` and adds `caveats` and SQL text to `blocks[]`. The frontend `features/ask` is rebuilt around a step reducer, a `StepTimeline`, a block registry and a new page layout.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy async, Redis streams (worker `progress.publish`); React 18 + TypeScript, vitest + msw, the repo design system (`frontend/src/design-system`).

**Spec:** `docs/superpowers/specs/2026-11-02-ask-chat-redesign-design.md` (read it first; "Step contract" and "Calls made by the designer" C1–C8 are binding).

## Global Constraints

- Work in `/Users/thiennlinh/Dev/Projects/Data-Agent-dsbh` (branch `drop-ds-bh`); stage explicit paths only (node_modules are symlinks); never stash/reset `/Users/thiennlinh/Dev/Projects/Data-Agent`.
- Tests only through make targets (host Python is 3.10): `make test-unit UNIT_SUITES="worker_agent:worker-agent" PYTEST_ARGS="<files>"`, `make test-integration INTEGRATION_SUITES="api:api" PYTEST_ARGS="<files>"`; frontend `cd frontend && npx vitest run <paths>` and `npx tsc --noEmit -p .`.
- `make lint` must report `0 added`; `make fmt-files FILES="…"` on touched Python.
- Unit test files carry `pytestmark = [pytest.mark.unit]`, integration ones `pytest.mark.integration`, or they are deselected.
- A new route: one row in `backend/services/api/catalogue/a2_endpoints.json` (insert textually, keep format), A2 + A3 rows in `backend/services/api/catalogue/source/tab4.md`, regenerate `a3_contract.json` (`docker run --rm -v "$PWD/backend:/app" -w /app localhost:5001/dataagent/api:latest python -m services.api.catalogue.build_a3`), bump the count in `catalogue/check.py` and `tests/unit/api/test_catalogue.py` (456 → 457), `docs/v3/06-api-v1.md`, `make api-types`, `make design-checklist`.
- Step `detail` never holds prompt text, model output or provider error text; model calls carry model, tokens, latency only.
- UI copy is Vietnamese. Known baseline: 7 worker_agent integration tests are red on main (test_sql 2–3, test_verified_queries 2, test_document_answers 3); do not count them as regressions, do not fix them here.
- The peer session owns `_understand_and_plan`'s no-metric internals and `understand/analyst.py`; touch only the lines named here and tell it (SendMessage `uds:/tmp/cc-socks/7893.sock`) before committing changes to questions.py.

## Review Focus

- A turn reopened while still running (C1): the hook must replay the stream from the start, not show "Lượt này chưa ghi chi tiết các bước." — test in Task 6.
- A step that fails mid-turn (source timeout): the timeline must show it failed with its summary, and the turn's later steps absent, not "done" — test in Task 2 (`query` failed) and Task 7 (failed row).
- A turn recorded before this change (old `understand`/`narrate` rows without `_view`): `/steps` returns `steps: []` and the UI shows the muted line — test in Task 4 and Task 7.
- Another member of the Data Space opening `/steps` of a question that is not theirs: 404 — test in Task 4.
- A model call outside any open step (e.g. llm_reader before `understand` opens): it is a top-level `model_call` step, not lost — test in Task 1.

---

### Task 1: The step contract in turn_trace

**Files:**
- Modify: `backend/services/worker_agent/turn_trace.py`
- Test: `tests/unit/worker_agent/test_turn_trace.py`

**Interfaces:**
- Produces:
  - `ACTIONS: dict[str, tuple[str, str]]` — action → (agent_step kind, Vietnamese title default): understand→(plan, Hiểu câu hỏi), choose_metric→(plan, Chọn chỉ số), ask_user→(checkpoint, Hỏi lại bạn), query→(query, Truy vấn), write_sql→(tool_call, Viết SQL thăm dò), search_documents→(tool_call, Tìm trong tài liệu), use_verified→(query, Dùng truy vấn đã duyệt), use_tool→(tool_call, Dùng công cụ), validate→(plan, Kiểm tra kết quả), visualize→(plan, Chọn cách hiển thị), analyze→(skill, Phân tích), write_answer→(message, Viết câu trả lời), model_call→(llm_call, Gọi mô hình).
  - `class StepHandle: step_id: str; detail: dict[str, Any]; status: str` (status "done" | "failed" | "skipped", default "done").
  - `@asynccontextmanager async def stepping(action: str, *, title: str | None = None, target: str = "", chips: Sequence[Mapping[str, str]] = ()) -> AsyncIterator[StepHandle]`
  - `async def record_step(action: str, *, title: str | None = None, target: str = "", chips: Sequence[Mapping[str, str]] = (), detail: Mapping[str, Any] | None = None, status: str = "done", summary: str | None = None) -> None`
  - Event published via `services.worker_agent.progress.publish(str(question_id), "activity", payload)` with payload `{"kind": "step", "step_id", "seq", "action", "title", "target", "chips", "status", "started_at", "duration_ms", "detail", "parent"}` (`detail`/`duration_ms` null while running).
  - Persisted agent_step: `name = action`, `kind = ACTIONS[action][0]`, `output = {"detail": detail, "_view": {"step_id", "title", "target", "chips", "parent"}}`, `summary`.
  - `model_call(...)` unchanged signature; its step becomes action `model_call` with `_view.parent` = the step open when it ran (None if none) and `detail = {model, input_tokens, output_tokens, latency_ms}`; it is not published as an event.
  - The old `step()` is removed (Task 2 replaces its only caller).

- [ ] **Step 1: Write the failing tests** in `test_turn_trace.py` (replace `test_steps_open_and_close_with_their_kind`): 
  - `test_stepping_publishes_running_then_done_with_detail` — monkeypatch `turn_trace.publish` to collect; inside a `Trace` context, `async with stepping("query", target="AdventureWorksDW") as s: s.detail = {"row_count": 3}`; assert two events, first `status == "running"`, `detail is None`, `title == "Truy vấn"`; second `status == "done"`, `detail == {"row_count": 3}`, `duration_ms` int, same `step_id`, `seq == 1`; `trace.steps[0].output == {"detail": {"row_count": 3}, "_view": {"step_id": s.step_id, "title": "Truy vấn", "target": "AdventureWorksDW", "chips": [], "parent": None}}`.
  - `test_an_exception_fails_the_step_and_propagates` — `pytest.raises(RuntimeError)` around a stepping block; last event `status == "failed"`; `trace.steps[0].status == "error"`.
  - `test_a_model_call_nests_under_the_open_step` — inside `stepping("write_answer")`, call `model_call(...)`; the llm step `output["_view"]["parent"] == handle.step_id`; outside any step `parent is None` (Review Focus).
  - `test_stepping_outside_a_turn_publishes_nothing` — no trace set; no events.
  - `test_record_step_is_one_finished_event`.
- [ ] **Step 2: Run, expect FAIL** `make test-unit UNIT_SUITES="worker_agent:worker-agent" PYTEST_ARGS="tests/unit/worker_agent/test_turn_trace.py"` → ImportError/AttributeError on `stepping`.
- [ ] **Step 3: Implement** in `turn_trace.py`: `Trace` gains `open_step: str | None`, `seq: int`; `stepping` pushes/pops `open_step` (restore the previous on exit for nesting); `_write` maps `Step.kind` from `ACTIONS`; import `publish` from `services.worker_agent.progress` as a module attribute so tests can patch `turn_trace.publish`.
- [ ] **Step 4: Run, expect PASS** (same command), plus `tests/unit/worker_agent/test_llm_gateway.py`.
- [ ] **Step 5: Commit** `feat(ask): one step contract, published live and kept for the turn's run`.

### Task 2: Steps on the governed path

**Files:**
- Modify: `backend/services/worker_agent/questions.py` (`_analyst_step` removed; `_run`; `_answer` around `verify`/`chart_spec_of`/`narrator.narrate`)
- Test: `tests/integration/worker_agent/test_answers.py` (the turn-run assertion added in e27686cf), `tests/integration/worker_agent/test_questions.py`

**Interfaces:**
- Consumes: Task 1 `stepping`, `record_step`.
- Produces (step details, exact keys):
  - `understand`: `{"reading": [{"kind", "label", "value", "source"}...], "confidence": float | None, "unmapped": [str]}` from the plan chips (`prepared`'s plan / `u.chips`); target = the question text cut at 80 chars.
  - `choose_metric`: `{"metric": {"code", "name", "version", "certified"}, "datasets": [str], "source": str}`; chips `[{"kind": "metric", "label": name}, {"kind": "source", "label": source}]`; target = metric name.
  - `query`: `{"sql": outcome.sql or compiled.sql, "row_count": len(rows), "truncated": bool, "query_run_id": str, "columns": [str]}`; target = `f"{source.name} cho: {metric name}"`; status failed with summary = the refusal message on `not outcome.ok`.
  - `validate`: `{"checks": number_check["verification"]["checks"]}`; failed when rows are empty (summary "Không có dữ liệu hợp lệ").
  - `visualize`: `{"chart_type": spec["type"] or None, "reason": str}`; only when a chart spec is made, after `chart_spec_of`.
  - `write_answer`: `{}` (its model calls nest under it); wraps `narrator.narrate`.
- Remove the `activity/plan_step` and `activity/stage` publishes; keep `planning`, `progress`, `answer`, `done`, `cancelled` events.

- [ ] **Step 1: Write the failing test**: in `test_answers.py::test_ac_01_01_01_a_ratio_by_region_in_scope_with_a_sealed_receipt` replace the step-name assertion with: steps (excluding `llm_call`) by `name` == `["understand", "choose_metric", "query", "validate", "visualize", "write_answer"]`, all `status == "ok"`; the `query` step's `output->'detail'->>'sql'` contains `GROUP BY`; `output->'detail'->>'row_count' == '2'`; `choose_metric` detail `metric.code == "NPL_RATIO"`. Add `test_a_source_timeout_fails_the_query_step` reusing the file's timeout fixture if one exists, else assert via the existing failed-source test in that file: the `query` step `status == "error"` and no `validate` step (Review Focus).
- [ ] **Step 2: Run, expect FAIL** `make test-integration INTEGRATION_SUITES="worker_agent:worker-agent" PYTEST_ARGS="tests/integration/worker_agent/test_answers.py tests/integration/worker_agent/test_questions.py"`.
- [ ] **Step 3: Implement** the step calls at the points listed; the `understand` step opens before `_understand_and_plan` and closes with its detail after it returns (failed when it returns None with a refusal, done when a clarification was asked — the `ask_user` step is Task 3).
- [ ] **Step 4: Run, expect PASS** (same command); the 7 baseline failures are not in these files.
- [ ] **Step 5: Tell the peer session** the exact line ranges touched in `_answer`; **Commit** `feat(ask): the governed path records what each step did`.

### Task 3: Steps on every other path

**Files:**
- Modify: `backend/services/worker_agent/questions.py` (clarify branch in `_understand_and_plan`; `_answer_from_documents`; `_refuse` call for the no-metric refusal), `backend/services/worker_agent/sqlgen/answer.py` (`answer_by_sql`, `answer_verified_first`)
- Test: `tests/integration/worker_agent/test_questions.py`, `tests/integration/worker_agent/test_follow_ups.py`, `tests/integration/worker_agent/test_sql.py` (green tests only)

**Interfaces:**
- Consumes: Task 1.
- Produces:
  - clarification: `record_step("ask_user", target=<the question back>, detail={"question": text, "options": [label…]})`.
  - sqlgen: `stepping("write_sql")` detail `{"candidates": int, "agreeing": int, "repaired": int, "sql": chosen}` then `stepping("query")` as Task 2's detail.
  - documents: `stepping("search_documents")` detail `{"hits": [{"title", "page"}], "mode": "hybrid" | "keyword"}` then `stepping("write_answer")`.
  - verified: `record_step("use_verified", target=<name>, detail={"name", "query_run_id"})` then the `query` step.
  - no-metric refusal: the `understand` step ends `failed` with summary = the refusal message.

- [ ] **Step 1: Write the failing tests**: `test_questions.py::test_a_turn_is_a_run_of_the_data_spaces_ask_agent` asserts the refused turn's steps are `[("understand", "error")]` (plus any `llm_call`); `test_follow_ups.py` cut test asserts an `understand` and a `query` step exist; add to the first green test of `test_sql.py` that `write_sql` precedes `query`. For clarification use the existing clarification test in `test_follow_ups.py` or `test_questions.py` (whichever asserts `clarifying`) and assert an `ask_user` step with `detail.options` non-empty.
- [ ] **Step 2: Run, expect FAIL** `make test-integration INTEGRATION_SUITES="worker_agent:worker-agent" PYTEST_ARGS="tests/integration/worker_agent/test_questions.py tests/integration/worker_agent/test_follow_ups.py tests/integration/worker_agent/test_sql.py"`.
- [ ] **Step 3: Implement** at the listed points.
- [ ] **Step 4: Run the full worker_agent integration suite**, expect only the 7 baseline failures (`make test-integration INTEGRATION_SUITES="worker_agent:worker-agent"`), and `make test-unit UNIT_SUITES="worker_agent:worker-agent"` green.
- [ ] **Step 5: Commit** `feat(ask): every answer path records its steps`; SendMessage the peer that the contract is on drop-ds-bh/main.

### Task 4: `GET /v1/questions/{id}/steps`

**Files:**
- Modify: `backend/services/api/modules/agent/public.py` (add `turn_steps`), `backend/services/api/modules/qa/routes.py`, `backend/services/api/modules/qa/schemas.py`, catalogue files (Global Constraints), `docs/v3/06-api-v1.md`
- Test: `tests/integration/api/test_qa_query.py`, `tests/unit/api/test_catalogue.py`

**Interfaces:**
- Produces:
  - `async def turn_steps(org_id: uuid.UUID, user_id: uuid.UUID, question_id: uuid.UUID) -> TurnSteps | None` in agent public.py (svc_agent; latest `agent_run` with `trigger_ref == f"question:{question_id}"`, its steps by seq); `TurnSteps` dataclass `{run_id, status, started_at, finished_at, tokens, steps: list[dict]}`.
  - Schemas: `StepView {step_id: str, seq: int, action: str, title: str, target: str, chips: list[dict[str, str]], status: Literal["done","failed","skipped","running"], started_at: datetime, duration_ms: int | None, detail: dict | None, parent: str | None}`; `QuestionStepsView {question_id: UUID, run_id: UUID | None, status: str | None, duration_ms: int | None, tokens: int, steps: list[StepView]}`. agent_step status maps ok→done, error→failed, skipped→skipped. Rows without `output._view` are omitted (old turns → `steps: []`).
  - Route `GET /v1/questions/{id}/steps` → `QuestionStepsView`, operation_id `getQuestionSteps`, capability `ask`; 404 unless the question is in the Data Space **and** `qa.Conversation.user_id == principal.user_id`.
- [ ] **Step 1: Write the failing tests** in `test_qa_query.py`: `test_the_asker_reads_the_steps_of_their_question` (seed an agent_run with `trigger_ref` and two agent_step rows with `_view`, one legacy row without; expect 200, 2 steps in seq order with `title`/`detail`, legacy omitted); `test_another_member_cannot_read_the_steps` → 404; `test_a_question_without_a_run_has_no_steps` → 200 `{"run_id": null, "steps": []}`. Bump the catalogue test count to 457.
- [ ] **Step 2: Run, expect FAIL** `make test-integration INTEGRATION_SUITES="api:api" PYTEST_ARGS="tests/integration/api/test_qa_query.py"` and `make test-unit UNIT_SUITES="api:api" PYTEST_ARGS="tests/unit/api/test_catalogue.py"`.
- [ ] **Step 3: Implement**, then the catalogue/api-types/checklist chores (Global Constraints).
- [ ] **Step 4: Run, expect PASS**; `make design-audit` clean.
- [ ] **Step 5: Commit** `feat(api): a question's steps can be read back after its turn`.

### Task 5: Caveats and SQL text in answer blocks

**Files:**
- Modify: `backend/services/api/modules/qa/answer_blocks.py`, `backend/services/api/modules/qa/routes.py` (pass the executed SQL into `blocks_of`)
- Test: `tests/unit/api/test_qa_answer_blocks.py`

**Interfaces:**
- Produces: `blocks_of(answer: Any, *, sql_by_run: Mapping[str, str] | None = None) -> list[dict]`.
  - `sql` block gains `"sql": str | None` (from `sql_by_run[query_run_id]`, the run's `sql_executed`).
  - New `{"type": "caveats", "id": f"{answer_id}-caveats", "items": [str]}` appended last when non-empty, from: each failed verification check → its `detail`; each distinct figure `trust` other than `"Theo chỉ số chuẩn"` → `f"Số liệu {trust}."`; `reconcile_badge == "differs_from_filed"` → `"Số hiện tại khác số đã nộp của kỳ."`; `product_scope == "out_of_scope"` → `"Câu trả lời nằm ngoài mọi sản phẩm dữ liệu, không có biên nhận."`.
- [ ] **Step 1: Failing tests**: `test_caveats_list_failed_checks_and_non_standard_trust`, `test_no_caveats_block_when_nothing_to_say`, `test_the_sql_block_carries_the_executed_sql`.
- [ ] **Step 2: Run, expect FAIL** `make test-unit UNIT_SUITES="api:api" PYTEST_ARGS="tests/unit/api/test_qa_answer_blocks.py"`.
- [ ] **Step 3: Implement**; in the `GET /v1/answers/{id}` handler read `query.QueryRun.sql_executed` for the figures' run ids (the asker can already read it via `/v1/query-runs/{id}/sql`); `make api-types`.
- [ ] **Step 4: Run, expect PASS**.
- [ ] **Step 5: Commit** `feat(api): an answer's blocks say what to watch out for and the SQL that ran`.

### Task 6: Frontend data layer — steps and the stream hook

**Files:**
- Create: `frontend/src/features/ask/steps.ts`, `frontend/src/features/ask/steps.test.ts`
- Modify: `frontend/src/lib/v1/questions.ts` (add `getQuestionSteps`), `frontend/src/features/ask/types.ts`, `frontend/src/features/ask/hooks/useQuestionStream.ts`, `frontend/src/features/ask/hooks/useQuestionStream.test.tsx`, `frontend/src/features/ask/answerMessage.ts`, `frontend/src/mocks/questions.ts`

**Interfaces:**
- Produces:
  - `type StepStatus = 'running' | 'done' | 'failed' | 'skipped'`; `interface TurnStep { stepId: string; seq: number; action: string; title: string; target: string; chips: { kind: string; label: string }[]; status: StepStatus; startedAt: string; durationMs: number | null; detail: Record<string, unknown> | null; parent: string | null; children: TurnStep[] }`.
  - `applyStepEvent(steps: TurnStep[], data: Record<string, unknown>): TurnStep[]` (insert by `step_id`, update on end; keep order by `seq`).
  - `stepsFromView(view: QuestionStepsView): TurnStep[]` (nest `parent` into `children`).
  - `getQuestionSteps(id: string): Promise<QuestionStepsView>` (`api.GET('/v1/questions/{id}/steps')`).
  - `AskMessage` gains `steps?: TurnStep[]`, `stepsRecorded?: boolean` (false → the C5 line), `blocks?: AnswerBlockView[]`, `startedAt?: string`, `durationMs?: number`, `questionText?: string`; old `progress`, `understanding`, `evidence` panel fields are removed when Task 10 deletes their users.
  - `fromAnswer(answer)` sets `blocks = answer.blocks`, keeps `suggestions = answer.follow_ups`, refusal, clarification, citations, trust, answerId, productScope.
  - Hook: on `activity` with `kind === 'step'` → `applyStepEvent`; on reopen (`hydrate`) a finished turn loads `getQuestionSteps`, a running turn follows the stream from no Last-Event-ID (C1); `retry(messageId)` re-sends `questionText` (C6).
- [ ] **Step 1: Failing tests**: `steps.test.ts` (running then done updates in place; out-of-order end before start creates the step; nesting by parent); hook tests: live turn collects steps from `activity` events and ends with `blocks` from the answer; `hydrate` of a finished turn fills `steps` from `/steps`; `hydrate` of a running turn replays the stream (Review Focus); old turn with `steps: []` → `stepsRecorded === false`.
- [ ] **Step 2: Run, expect FAIL** `cd frontend && npx vitest run src/features/ask/steps.test.ts src/features/ask/hooks`.
- [ ] **Step 3: Implement**; extend `src/mocks/questions.ts` with step events and a `/v1/questions/:id/steps` handler.
- [ ] **Step 4: Run, expect PASS**; `npx tsc --noEmit -p .` may fail only in files Task 10 deletes.
- [ ] **Step 5: Commit** `feat(ask): the chat follows each step live and reads them back on reopen`.

### Task 7: StepTimeline

**Files:**
- Create: `frontend/src/features/ask/chat/StepTimeline.tsx`, `frontend/src/features/ask/chat/StepDetail.tsx`, `frontend/src/features/ask/chat/StepTimeline.test.tsx`

**Interfaces:**
- Produces: `StepTimeline({ steps, running, durationMs, recorded }: { steps: TurnStep[]; running: boolean; durationMs?: number; recorded: boolean })`; `StepDetail({ step }: { step: TurnStep })`.
  - Header button: running → "Đang làm… {n} bước", done → `Đã làm trong ${seconds} giây` (vi-VN, 1 decimal under 10 s), `aria-expanded`; open while running, closed once done; toggles.
  - Row: status mark (spinner/check/x), **title** + target text + chips; a chevron button "Xem chi tiết" that toggles `StepDetail`; `model_call` children listed inside the detail as "Mô hình {model} · {in}+{out} token · {ms} ms".
  - `StepDetail` per action: understand → reading chips; choose_metric → metric name, version, "Đã chứng nhận"/"Chưa chứng nhận", datasets, source; query/write_sql/use_verified → SQL `<pre>` with a copy button, row count, "Đã cắt bớt" when truncated; validate → checks list with pass/fail; visualize → chart type; search_documents → hits list; ask_user → question + options; use_tool → tool, args, result; failed → summary in an error Notice; unknown action → key/value list.
  - `recorded === false` → only the muted text "Lượt này chưa ghi chi tiết các bước." (C5).
- [ ] **Step 1: Failing tests**: header text and toggle; a failed `query` row shows its summary; expanding a `query` row shows the SQL and copy; model call nested line; `recorded=false` line (Review Focus).
- [ ] **Step 2: Run, expect FAIL** `npx vitest run src/features/ask/chat/StepTimeline.test.tsx`.
- [ ] **Step 3: Implement** with design-system `Icon`, `Badge`, `Notice`, `Button`.
- [ ] **Step 4: Run, expect PASS**.
- [ ] **Step 5: Commit** `feat(ask): a step timeline whose every step opens to what it did`.

### Task 8: Answer blocks

**Files:**
- Create: `frontend/src/features/ask/chat/blocks/AnswerBlocks.tsx`, `ResultCard.tsx`, `MetricCards.tsx`, `SqlBlock.tsx`, `CaveatsBlock.tsx`, `AnswerBlocks.test.tsx` (all under `chat/blocks/`)
- Modify: `frontend/src/features/ask/answerBlocks.ts` (types follow the server's block union: markdown, metric, chart, table, sql, citations, caveats)

**Interfaces:**
- Consumes: Task 6 `AskMessage.blocks`; existing `MarkdownContent`, `ChartRenderer`, `DataGrid`, `CitationPanel`/citations rendering, `ExportDialog`, `exportCsv`, `SaveToSessionDialog`.
- Produces: `AnswerBlocks({ message, layout, onOpenCitation, onExplore, onSaveToSession }: …)`: renders blocks in server order, reordered by `responseOrder(layout, sectionForBlock(b))` when a `/` command set a layout; all metric blocks of a turn form one `MetricCards` row (max 4 cards, then "+n"); chart and table blocks share one `ResultCard` with a Biểu đồ/Bảng toggle and toolbar: "Khám phá" (C8, only with a query run), "Xem" (full-size Dialog), "Bộ lọc {n}" (read-only popover of the applied filters from the plan chips, C7), "…" Menu (Tải CSV, Lưu vào phiên); `SqlBlock` collapsed "SQL đã chạy" with copy; `CaveatsBlock` titled "Lưu ý" as a bullet list.
- [ ] **Step 1: Failing tests**: a data answer renders text, metric cards, a result card that toggles chart/table, caveats; `/sql` layout puts the SQL block first; "Khám phá" hidden without a query run; filters popover lists filters and has no input.
- [ ] **Step 2–4:** run `npx vitest run src/features/ask/chat/blocks` FAIL → implement → PASS.
- [ ] **Step 5: Commit** `feat(ask): an answer is drawn block by block`.

### Task 9: The assistant turn and its footer

**Files:**
- Create: `frontend/src/features/ask/chat/AssistantTurn.tsx`, `TurnFooter.tsx`, `UserMessage.tsx`, `AssistantTurn.test.tsx`

**Interfaces:**
- Consumes: Tasks 6–8; existing `RefusalCard`, `ClarificationCard`, `DecompositionCard`, `VerifierBadge`/trust label, `ReportDialog`, receipt drawer, `SaveToKnowledgeDialog`, `hasReceipt`.
- Produces: `AssistantTurn({ message, busy, onRetry, onReport, onReceipt, onSaveKnowledge, onSaveSession, onAsk, onAnswerClarification, onRunProposal, onOpenCitation })`: order = StepTimeline → refusal | clarification | AnswerBlocks → decomposition proposal → trust label line → follow-up chips (`message.suggestions`, click = `onAsk(text)`) → `TurnFooter`. `TurnFooter`: time (HH:mm, dd/MM), buttons "Hỏi lại" (retry, C6), "Báo sai" (thumbs-down icon → `onReport`), "Sao chép" (copies markdown text), "Biên nhận" when `hasReceipt`, "…" menu: Lưu vào tri thức, Lưu vào phiên.
- [ ] **Step 1: Failing tests** covering every G5 action: retry calls `onRetry`; Báo sai opens; copy writes clipboard; receipt only when `hasReceipt`; follow-up chip asks; refusal card shows its next action; clarification card answers; decomposition card runs; no thumbs-up button exists.
- [ ] **Step 2–4:** `npx vitest run src/features/ask/chat/AssistantTurn.test.tsx` FAIL → implement → PASS.
- [ ] **Step 5: Commit** `feat(ask): each turn shows its work, its answer and what you can do with it`.

### Task 10: The page — panel, empty chat, thread, composer — and removal of the old screen

**Files:**
- Create: `frontend/src/features/ask/chat/ConversationPanel.tsx`, `EmptyChat.tsx`, `ChatComposer.tsx`, `Thread.tsx`, and tests `ChatComposer.test.tsx`
- Modify (rewrite): `frontend/src/features/ask/AskDataPage.tsx`, `frontend/src/features/ask/AskDataPage.test.tsx`
- Delete (after `rg` shows no other importer): `components/AnswerBlock.tsx` (+ test), `components/AgentProgress.tsx`, `components/EvidencePanel.tsx`, `components/EvidenceStrip.tsx`, `components/InterpretationCheck.tsx`, and any helper left unreferenced (`make deadcode` / knip)
- Modify: `docs/v3/05-ui-v3.md` (Ask section rewritten to this design)

**Interfaces:**
- `ConversationPanel({ activeId, onNew, onOpen })`: "Cuộc trò chuyện mới", a search box filtering titles client-side, "Gần đây" from `listConversations(30)`; collapsible.
- `EmptyChat({ greeting, starters, recent, onAsk, composer })`: "Xin chào {tên}, bạn muốn khám phá dữ liệu nào?", subline, the composer, `starterQuestions(model)` as "→ …" rows, recent chats with "Xem tất cả".
- `ChatComposer({ sending, onSend, onStop, model })`: textarea, placeholder "Hỏi về dữ liệu, gõ @ để thêm ngữ cảnh và / để dùng lệnh"; `@` opens a list of the explore model's metrics and dimensions, picking one adds a chip above the field; `/` opens `CHAT_COMMANDS`; send = chips' labels + text joined into the question (C3), then the existing `parseChatInput`/`commandPrompt`; Enter sends, Shift+Enter breaks; "Dừng" while sending.
- `AskDataPage`: header with the conversation title, "Cuộc trò chuyện mới"; left `ConversationPanel`; centre `EmptyChat` or `Thread` + `ChatComposer`; dialogs (report, receipt, knowledge, session, export) wired as today; route `/analysis/ask/<conversation_id>` reopens.
- [ ] **Step 1: Failing tests**: composer `@` adds a chip and the sent text contains it; `/` lists commands; page renders the empty chat then a thread after asking; reopening by conversation id hydrates; the panel search filters.
- [ ] **Step 2–4:** run `npx vitest run src/features/ask` FAIL → implement → PASS; then `npx tsc --noEmit -p .`, `make frontend-lint`, `make deadcode` clean for removed files.
- [ ] **Step 5: Commit** `feat(ask): a new chat layout with a conversation panel and an @ and / composer`.

### Task 11: Deploy and verify on the dev cluster

- [ ] **Step 1:** fast-forward main (`git -C /Users/thiennlinh/Dev/Projects/Data-Agent merge --ff-only drop-ds-bh`), deploy api, worker-agent and frontend from the worktree with the Ansible command used before (allowlist `["api","worker-agent","frontend"]`).
- [ ] **Step 2:** ask "Doanh thu Internet năm 2013 là bao nhiêu?" as helios-engineer through the API; expect `/steps` to list understand → choose_metric → query → validate → (visualize) → write_answer with details, and the answer's `blocks` to include markdown and metric.
- [ ] **Step 3:** report to the user with what to click.
