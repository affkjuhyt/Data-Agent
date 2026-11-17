# Ask engine audit — Data-Agent (main @ 25b5482e, 2026-11-12)

Paths are under `backend/services/worker_agent/` unless stated. Measured baseline (docs/v3/eval/ds-aw-agent-eval.md): agentic 3/60 passed (14 values right), governed 3/60 (15 right); after metrics certified + RES-05, first-20: 17/20 passed, 7.2 steps, 10.9k tokens, 7.2 s/turn. Remaining misses: wrong table in exploratory SQL, dropped filter, breakdown counted as extra figures.

## 1. Pipeline trace (one question) — **exists**

| # | Module / function | Carries |
|---|---|---|
| 1 | `api/modules/qa/routes.py:161 create_question` → quota `within_quota("questions")`, FOR UPDATE on conversation, `insert_question` | body.text, conversation_id, parent_question_id, as_of, knowledge_mode, purpose |
| 2 | `api/modules/qa/questions.py:34 insert_question` → `refuse_instructions` (AGC-03), seq, `qa.Question(status=received)`, `scope.emit(qa.Outbox, event_type="question.received")` same tx | payload: question_id, conversation_id, data_space_id, user_id |
| 3 | `worker_agent/events.py:50 OutboxTableSource.claim` (FOR UPDATE SKIP LOCKED, role svc_outbox_relay) → `main.py:106 run_once` → `questions.HANDLERS["question.received"]` | `Event(event_id, org_id, payload)` |
| 4 | `questions.py:785 answer_question` → `turn_trace.turn()` → `_run` :799 | — |
| 5 | `_run`: (a) `answer_verified_first` (sqlgen/verified.py, VERIFIED) → (b) `agentic.engine.run` (if `ASK_ENGINE=agentic`, engine.py:86) → (c) governed `_understand_and_plan` :1280 → `_find_values` → `run_query` → `_answer` :2129 / `_refuse` | — |
| 6 | `engine.py:1354 _graph`: prepare → plan → act ⟲ (replan/review) → compose → persist; `GRAPH.run(max_steps=64)` | `State` (:101): event, question_id, model, ctx:`ToolContext`, conversation_id, history, plan, replans, reviews, step_runs, failed, clarify, refused, handed_back, composition, headline, dropped_sentences, numbers, memories, signals, coverage, feedback, gate_retries, audience, prior_clarify_rounds, deep, answered, cancelled |
| 7 | `engine.py:1161 persist`: `qa.Plan(steps={engine:"agentic", datasets, operator})`, `qa.Answer(kind, text, figures, blocks, citations, number_check=verify_answer(...))`, status→answered | figures carry `trust` (string), `query_run_id` |
| 8 | SSE: worker `progress.publish` → Redis stream `sse:{qid}` (events `planning/plan/progress/activity/block/clarification/answer/done`, `stream_contract.py`); api `routes.py:391 stream_question` reads Redis (`qa/progress.py:read`) with DB fallback | `final_payload` has headline, blocks, text, trust_summary |
| 9 | Trace: `turn_trace._write` :428 writes `agent_run` + `agent_step` **once at turn end** (docstring :8–10) | tokens, cost per step |

## 2. Intent / entity / context resolution — **exists (rule-first, LLM fallback)**

- Rule pass `understand/resolver.py:155 understand`: period removed first (`text.find_period`, packs vi/en, ISO, fiscal via `clock.Calendar`), then `_ranking` (top-N/superlative), longest-phrase exact match over synonyms (0.9) → value_index (`value_retrieval.sem_value_retrieval`, trigram near-match ≥0.8 with 0.15 margin) → metric/dimension names (1.0). `PROCEED_AT=0.8`, two equal metrics → clarify ≤3 options; `unplaced_cut` ("theo khu vực" naming no dim) → clarify.
- Comparison `understand/comparison.py` (compare_to previous_period/same_period_last_year), grain `grain.py` (fiscal_*), numeric conditions `condition.py` → `compiler.Filter`. All regex from `understand/packs/{vi,en}.py` (`Pack` fields: periods, grains, comparisons, rank, superlative, by, filler, quantity, why, knowledge).
- No match: `questions.py:1401` `follow_up()` inherits previous chips → `:1414 llm_reader.read` (fast "planner" alias, confidence 0.8, codes only, `grounding()` full/partial/none; partial → `to_confirm`) → `analyst.resolve` (tool-using reader: search_metrics/describe/search_values/propose) → `answer_by_sql` (exploratory) → `_answer_from_documents` → `UNRESOLVED_ENTITY` refusal :1951. Unmapped phrases recorded for stewards (`_record_unmapped` :1241).
- Agentic path: no slot resolver — the planner LLM reads `<catalog>` (`engine._catalog` :287, max 60 metrics, unit/period_semantics/cuts/description) and `_situation` (:341: history, memories, `<coverage>`, rule-read period). Values come via `search_values` tool; compose cross-checks with `_named_members`/`_dropped_members` (:726–766).

## 3. Schema linking & text-to-SQL — **exists**

| Piece | Evidence |
|---|---|
| Linking | `sqlgen/schema.py:103 link`: table/column/value-name scoring, MAX_TABLES=4, FK middle-table closure; `_keep_one_source` (answer.py:113) one source per query |
| Variants | `sqlgen/generate.py`: METHODS skeleton/examples/divide × TEMPERATURES (0.2, 0.7) = 6 candidates, role `text_to_sql` (Qwen3-14B); examples from active `context.example_query` with literals masked |
| Static check | `sqlgen/check.py:40 problems`: parse, `check_read_only`, no other catalog, tables ⊆ linked, no `SELECT *`, column existence via sqlglot `qualify`; one `repair`, one `diagnose_and_repair` on source error (run.py:74–96) |
| Vote | `sqlgen/vote.py`: signature of rows → ≥4/6 answer, 3/6 review, ≤2 ask |
| Audit | `agentic/tools/query.py:263 _audit` (prompt `AUDIT_SQL`, planner alias, 5 sample rows); refused → `fingerprint` to `ToolContext.refused_sql` (base.py:56), banned in `run.explore` :63 and listed in prompt; one retry per step (:369). **Only on agentic path**; governed `answer_by_sql` has no audit |
| Guard | `libs/sql_guard/__init__.py:139 check_read_only`: one statement, exp.Query only, write nodes, denied/unmodelled functions, no comments, no locks/hints; `capped()` LIMIT wrap re-checked; `tables_of` scope-aware |
| Limits/identity | `query/executor.py`: MAX_ROWS 10 000, STORED_ROWS 1 000, `statement_timeout = source.query_timeout_s` (120 s default), READ ONLY tx (PG/MySQL; not SQL Server), `max_concurrency` semaphore. **Identity = the source's `secret_ref` service credential** (:517), asker expressed only via row-scope/column-policy rewrite (`query/scope.py`); internal workspace runs as asker's scoped session |
| Dialect | `dialect_of(source.kind)` map (postgres, mysql, tsql, clickhouse, …); compiler emits per dialect |

## 4. Semantic layer — **exists; several shapes rejected**

- Model: `semantic/compiler.py` — entities→`Table`, `Measure(additivity full/semi/none)`, `Dimension(is_time)`, `Join` (active many-to-one), `MetricVersion(period_semantics sum/end/average_over_period, time_grain, allowed_dimension_ids, certified)`. Fiscal via `fiscal_shift` :372. Multi-entity measures aggregated per side then UNION ALL (fan/chasm safe); `NULLIF` division; semi-additive read on last day.
- Compiles: group_by (allowed dims only, BR-81), typed filters/conditions, period, grain, top_n, compare_to (`semantic/compare.py`), share_of_total/running_total (`semantic/analysis.py`), knowledge_at. Rejects (`CompileError` codes): `NO_JOIN_PATH` (>MAX_HOPS=3 or two shortest paths), `NO_DATA_FOR_PERIOD` (no time dim), `CONTRACT_MISMATCH`, `UNRESOLVED_ENTITY`. **Not modelled**: required dimensions, account-type sign/balance rules (AGC-12 todo), multi-metric in one request, ratio-of-two-metrics ad hoc, non-time window functions.
- Loader `semantic/loader.py:53`: active rows only, certified version in force else newest certified else current (label "chưa có định nghĩa chuẩn"), column/mask policies drop datasets, `profile_supplements` from `column_profile.top_values` when value_index empty (RES-03), `blocked_metric_ids`.
- YAML: `api/modules/context/semantic_yaml.py` MetricFlow-shaped export/import (`make semantic-export/-import`, dry-run default, relationships `pending_review`, metrics draft).
- Verified queries `sqlgen/verified.py:67 match`: exact normalised text match (period words removed when SQL has `:period_start/:period_end`); no fuzzy/semantic matching; runs first in `_run` (VERIFIED label).

## 5. Multi-turn & memory — **partial**

- Tables: `qa.conversation`, `qa.question(seq, parent_question_id)`, `qa.plan(chips)`, `qa.answer`, `qa.memory_fact`.
- Governed: `resolver.follow_up` :337 inherits metric + period + filters from previous chips when the new turn only states cut/filter/period (new metric or `_asks_quantity` → new question); cut replaces cut; `llm_reader` gets 10 verbatim turns + `compaction.summarize_older`.
- Agentic: `engine._history` :143 = last 5 turns as prose (`Người dùng/Kế hoạch/Trả lời[:400]`); **no structured slot carry-over** — period/filter/source of the previous turn reach the planner only as text; `ctx.period` is the current text only. Lost: previous `reading` (metric/group_by/filters), previous dataset ids, source.
- RG-07 (follow-up rewrite before retrieval): **todo** — governed `_answer_from_documents` :2022 retrieves with `question.text` verbatim; agentic `search_documents` uses model-written `args.query` (tools/documents.py:17), an implicit rewrite with no test.
- Memory `agentic/memory.py`: kinds preference/definition/fact/feedback, scope (org, space, user); extract only when `worth_remembering` (lexical trigger list `_LASTING`), ≤5 ops, secrets/fetched numbers dropped server-side; recall BM25+recency+times, RECALL_LIMIT 5. Correction memory AGC-21: `correction_gate.py` opens `feedback_fix` proposal at `times ≥ 3`; never auto-applies. RES-10 (consolidation, conflict marks, UI) todo.

## 6. Clarification policy — **partial**

- Agentic: planner `mode=ask` → `clarify_policy.apply` (engine `_bind_clarify` :203): executive 1 round/3 options, analyst 3/5; `prior_clarify_rounds` counted from earlier `kind=clarification` answers in the conversation; over budget → assume (thought note), analyze/answer. Audience from `event.payload.audience` only — API never sets it (defaults analyst).
- **Verified gap**: the `ask_user` tool (tools/__init__.py:29) returns `Clarify` directly; `act` :495 copies it to `s.clarify` with no policy applied — a plan step `ask_user` bypasses round/option budget (options[:5] only). Same via tool results from replan.
- Governed: `MAX_OPTIONS=3`, no round budget; `to_confirm` tries exploratory SQL before asking (:1862).

## 7. Routing SQL / documents / Python / hybrid — **exists**

- Decider: planner prompt rules 2–7b (`agentic/prompts.py` PLANNER) + specialist cards (`specialists.py:NUMBERS/DOCUMENTS.card`). Hybrid = exactly two lead steps `investigate_numbers` ∥ `investigate_documents` (`depends_on=[]`), run concurrently by `p.ready(MAX_PARALLEL=3)` + `asyncio.gather` (engine `act` :479). Specialists: own transcript, allowlist, `DEFAULT_INNER_STEPS=4`, only summary back; datasets/citations land on shared `Workspace`.
- Python: `analyze` only when `AGENT_PYTHON_SANDBOX=service|local` (tools/analyze.py:14); trust `derived`.
- Merge: `compose` :793 gives the reporter `<facts>` (every numeric cell tagged F<n>, `verify.facts`, ≤400) and `<citations>`; one Composition of blocks; no cross-source consistency check (a document number vs a queried number is never reconciled).
- Governed: documents only when no metric resolves — no hybrid.

## 8. Grounding, verification, trust, trace — **partial**

- Figures: `verify.fill` :107 drops any sentence with an unknown `{{F}}` or a bare number not in `allowed_numbers`; `repair_citations`. Chart/table/kpi blocks materialised from datasets (`blocks.materialize`). Count in `number_check` (persist :1241).
- Gates: `present_gate.before_write` (out_of_time → immediate handover BR-178; nothing fetched; `_dropped_members`; `no_figure`; `_unnamed_quantity`) → `_gate` one replan (`MAX_GATE_RETRIES=1`) then handover to governed; `after_drafts` column contract.
- Signals `agentic/signals.py`: quality_state, freshness overdue/missing, structure changed after certification → caveats; **no trust downgrade** (RES-07 "còn").
- Trust labels: set as **Vietnamese strings** — `workspace.TRUST_LABEL` (certified/exploratory/derived/document), governed `questions.py:2175` STANDARD/NOT_STANDARD/OUT_OF_SCOPE, `sqlgen/answer.py TRUST`, `verified.TRUST_VERIFIED`; stored only inside `figures[].trust` / block `trust_label`. Codes VERIFIED/GOVERNED/EXPLORATORY/INSUFFICIENT exist **only in the frontend** (`frontend/src/features/ask/dataAnswer.ts:66–125`, `trust.ts:11`); unknown string → EXPLORATORY; INSUFFICIENT derived client-side; `dataAnswer.ts` has level `DOCUMENTS` that `trust.ts TrustLevel` does not declare. AUD-05 todo confirmed.
- Receipts: governed `_seal_receipt` :2516 (sql hash, compiler version); agentic answers have `product_scope="not_applicable"` and no receipt row.
- Trace AGC-05 todo confirmed: `turn_trace._write` at end of turn; `GET /questions/{id}/steps` (routes.py:337) reads `agent_step` → empty while running; live view only via Redis `activity` events (100-entry window).

## 9. Determinism — **partial**

- LLM: `llm.py` `json_chat` default `temperature=0.0`, no seed; `responder`/`orchestrator-planner` have Qwen thinking on. sqlgen samples at 0.2/0.7 by design → exploratory answers vary; vote absorbs some.
- Query cache: `executor.cache_ttl` 600 s keyed by executed SQL + policies + as_of → same governed SQL within 10 min is identical rows. Verified-query reuse R1-08 exists; AN-10 (tile/dashboard reuse) todo.
- Variance sources: memory recall (`State.memories`), `_history`, planner JSON, replan path, reviewer rewrite (`_reviewed`), period_moves. Same question twice on agentic path is **not guaranteed** the same plan or blocks; figures from `query_metric` are.

## 10. Zero vs no-data — **partial**

- Governed `_no_value` :1232: NULL value → `NO_DATA_FOR_PERIOD` after `_explore_period`/`_widen_filter`; a real 0 is answered as 0.
- Agentic `present_gate.no_figure` :31 treats **all-zero as no figure** → replan → handover; `query_metric` says "0 dòng" only on empty rows. AGC-10 `semantic/coverage.py` + `period_moves` move out-of-range periods to the latest full one (labelled `period_moved`). So a genuine zero (e.g. cancellations = 0) on the agentic path costs a replan and lands in governed; if governed cannot resolve the metric it refuses `UNRESOLVED_ENTITY`. Design gap, not a bug.

## 11. Vietnamese — **exists, with hard-coding**

- `libs/language/__init__.py`: `norm` (NFC, lower, strip accents, đ→d, punct→space) shared by api/worker; `number()` via Babel on org locale (`use_locale`, DEFAULT `vi-VN`); packs vi/en for periods/grains/comparisons/rank.
- Accent stripping means "lãi/lại", "nợ/nở" collide in matching (by design, no disambiguation).
- Hard-coded Vietnamese: all prompts (`agentic/prompts.py`, `llm_reader.SYSTEM`, memory `EXTRACT`), `stream_contract.progress_message`, `_history` labels, refusal/clarification texts in `questions.py`, trust labels, caveats, `TRUST_LABEL`. Reporter is told "write in the question's language" but harness text stays Vietnamese.

## 12. Cost / latency controls — **partial**

- Engine: `MAX_STEPS=8` per plan, `MAX_STEP_RUNS=14`, `MAX_REPLANS=2`, `MAX_REVIEWS=1`, `MAX_GATE_RETRIES=1`, `MAX_PARALLEL=3`, `STEP_ATTEMPTS=3`, graph `max_steps=64`; tool timeouts 180 s (explore 300, investigate 600); `simple()` skips reviews (RES-05). **No token or wall-clock budget** (AGC-04 todo; `turn_trace` only records tokens/cost). Cancel polled each second (`CANCEL_POLL_SECONDS`).
- LiteLLM aliases (`deploy/docker/litellm/config.yaml`): main tier Qwen3-14B-AWQ (`LLM_MAIN_BASE_URL`): `responder` (thinking on), `text_to_sql`, `orchestrator-planner` (thinking on), `orchestrator-judge`; fast tier Qwen3-4B-AWQ: `planner`, `judge`, `guardrail`, `rewriter`, `suggester`, `visualize`, `orchestrator-summary`, `local-qwen`, `local`; `embedder` = BAAI/bge-m3 (TEI); reranker bge-reranker-v2-m3 called directly. Worker uses `planner` (plan, executor, review, audit, specialists, memory, llm_reader, analyst), `responder` (compose), `text_to_sql`.

## 13. Tests — none hit a real LLM

| File | Proves |
|---|---|
| unit `test_agentic_core.py` (40) | plan DAG validation, blocks from datasets, number tracing, graph termination; fake `json_chat` |
| unit `test_agentic_coverage.py` (4), `test_present_gate.py` (5), `test_clarify_policy.py`, `test_specialists.py` (4), `test_agentic_memory.py` (5), `test_correction_gate.py`, `test_stream_contract.py`, `test_operator_view.py`, `test_deep_skill.py`, `test_turn_trace.py` | AGC-09..11, OP-1..8 units |
| unit `test_understand.py` (23), `test_language_packs.py` (9), `test_value_retrieval.py`, `test_analyst_plan.py` | rule pass vi/en, near match, analyst tool loop with stub |
| unit `test_sqlgen.py` (16), `test_sqlgen_ablation.py`, `test_guard.py` (18), `test_executor_dialects.py` | linking, static check, vote, guard |
| unit `test_compiler*.py` (12+), `test_comparison_answer.py`, `test_narrator.py`, `test_document_answer.py` | compiler shapes, narration |
| integration `test_answers.py` (31), `test_questions.py` (8), `test_analyst_answers.py` (3), `test_verified_queries.py` (6), `test_answer_verification.py` (5), `test_document_answers.py` (6), `test_adventureworks_reconciliation.py` (9), `test_agent_evals.py` (6), `test_query_runs.py` | governed end-to-end on PG with stub gateway; DS-AW metrics reconcile with hand SQL |
| e2e `tests/integration/test_v3_question_e2e.py` (`make e2e`) | deployed cluster, real LLM — the only real-model test; plus `make agent-eval` / `make sqlgen-ablation` (not in CI) |

Not covered by tests: `ask_user` bypass, agentic path end-to-end with DB (integration tests run governed), hybrid merge, AUDIT_SQL effect.

## 14. Top 10 gaps vs "reliable enterprise Ask"

| # | Gap | Kind | Sev | Fix location |
|---|---|---|---|---|
| 1 | Trust label is a Vietnamese string on figures, mapped client-side; no stored code, `INSUFFICIENT` never set server-side, `DOCUMENTS` level undeclared in `trust.ts` (AUD-05) | design | P1 | `qa.answer` column + `engine.persist`, `questions._answer`, `sqlgen/answer._store_answer`; `frontend/src/features/ask/trust.ts` |
| 2 | Exploratory SQL still wins with the wrong table (eval: "Doanh thu 2013" → 4 057 065); audit is LLM-only, governed `answer_by_sql` has no audit at all | measured | P1 | `sqlgen/answer.py:155`, `agentic/tools/query.py:_audit` (add rule checks: measure column ∈ metric's measures when a catalogued metric exists) |
| 3 | `ask_user` tool bypasses `clarify_policy` (rounds/options); audience never passed by API | verified bug | P2 | `agentic/engine.py:act` (route `result.clarify` through `clarify_policy.apply`), `api/modules/qa/questions.py` payload |
| 4 | No token/time budget per turn (AGC-04); 14 step runs × 600 s specialists possible | design | P1 | `agentic/engine.py` (budget on `State`, check in `after_act`), `turn_trace` |
| 5 | Trace written only at turn end; `/steps` empty while running, lost on pod death (AGC-05) | design | P2 | `turn_trace.py:_write` → per-step upsert |
| 6 | Agentic multi-turn carries only prose history; no inherited slots; document retrieval uses verbatim text (RG-07) | design | P2 | `engine._situation` (add previous `reading` chips), `questions._answer_from_documents` |
| 7 | All-zero result treated as "no data" on agentic path → replan + handover | design | P2 | `agentic/present_gate.no_figure` (distinguish NULL from 0, use coverage) |
| 8 | Semantic layer lacks required dimensions / account sign rules (AGC-12); descriptions carry rules as prose the planner may ignore | design | P1 | `semantic/compiler.py`, `deploy/docker/semantic/ds-aw.yaml`, `semantic_yaml.py` |
| 9 | Execution identity is the source's service credential; asker enforced only by policy rewrite; SQL Server has no READ ONLY tx | design | P2 | `query/executor.py` (per-role credentials / SET ROLE), `query/scope.py` |
| 10 | Verified-query match is exact-text only; no reuse of tiles (AN-10); agentic answers get no receipt (`product_scope=not_applicable`) | design | P2 | `sqlgen/verified.py:match`, `agentic/engine.persist` → `_seal_receipt` |

Also noted: backlog §3i marks AGC-14..21 "chờ commit" but the files are committed (ab51e4cd/0ce8e6df) — backlog stale; signals never downgrade trust (RES-07 open); harness text hard-coded Vietnamese (§11); no real-LLM test in CI (§13).
