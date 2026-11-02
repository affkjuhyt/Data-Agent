# Dynamic analyst agent: a planned, replanned tool graph that composes its own answer

Date: 2026-11-02. Status: asked for by the user ("phá vỡ thiết kế hiện tại… nó đang rất gò bó",
"cứ tự chạy đi"), researched against spring-ai-alibaba/DataAgent, WrenAI, gpt-researcher, RD-Agent,
ragflow, Memori, Data-Analysis-Agent (notes in the session scratchpad, summarised in §9).

## 1. What is rigid today

- **Planner**: `analyst_plan.build_analysis_plan` writes the same six steps for every question
  (understand → resolve_scope → query → validate → visualize → narrate). The plan describes what
  the code will do; nothing reads it to decide what to do.
- **Workflow**: `questions._run` is a hand-written sequence with branches for each special case
  (verified query, period explore, widen filter, sqlgen, documents). One question = one metric =
  one SQL. "Compare revenue and margin by region, then tell me which product drives the drop"
  cannot be answered: it needs two queries, a derived computation and a ranking.
- **Answer**: the stored answer has one text, one figures list, one chart. `answer_blocks.blocks_of`
  projects it to blocks in a fixed order (markdown, metric cards, chart, table, sql, citations,
  caveats). The frontend groups them back into the same layout. Whatever the question, the answer
  looks the same.

## 2. Goals

- G1. The **planner decides** the steps: which tools, how many, in what order, with dependencies.
  A plan is a DAG of steps, each a tool call or an instruction to the executor; it is validated
  statically (tools exist, deps acyclic, budget) before anything runs (invariant 17).
- G2. The **workflow is a graph** (`agentic/graph.py`): nodes with conditional edges, a shared
  state, a step budget. Nodes: `prepare → plan → act ⟲ reflect → (replan | ask | compose) →
  verify → persist`. Failure of a step goes back to the planner with what was observed
  (DataAgent's PlanExecutor/Replan, RD-Agent's feedback loop), at most `MAX_REPLANS` times.
- G3. **Open tool registry** (`agentic/tools/`): adding a capability is one class with a name, a
  description, a Pydantic args model and `run()`. First set: `search_catalog`, `describe_metric`,
  `search_values`, `query_metric` (governed: compiler + executor), `explore_sql` (sqlgen N-version
  vote), `analyze` (pandas in a sandbox over the turn's datasets), `search_documents`,
  `ask_user`. Tools write **datasets** to a workspace (blackboard), never prose.
- G4. The **answer is composed by the agent** as a list of typed blocks it chooses
  (`agentic/blocks.py`): `markdown`, `kpi`, `chart`, `table`, `insights`, `callout`, `plan`,
  `code` (python), `sql` (by query run), `citations`, `follow_ups`. Data blocks name a dataset and
  columns; the server fills the data from the dataset (the model never writes a number into a
  chart or table). Blocks are stored on the answer (`qa.answer.blocks`, v3_0023) and **streamed
  live** (`block` event), so the screen fills as the agent works.
- G5. The UI renders **whatever blocks arrive**, in the order the agent chose, with a generic
  fallback for an unknown block type. The step timeline shows the plan and each executed step.
- G6. **Memory across turns**: the previous turns' questions, the datasets they produced (name,
  columns, a few rows) and their conclusions are the planner's context, so "còn theo vùng thì
  sao?" or "so với cái trước" works without rule-based follow-up reading.

## 3. Non-negotiables kept (docs/v3/00 §5)

- Permissions before generation (13): every catalogue tool sees only `SpaceLayer` metrics the asker
  is not denied; `run_query` applies row/column policies; a denied metric is not found.
- LLM proposes, deterministic checks decide (5/6): the model never states a figure. Every number
  in a `markdown`/`kpi`/`insights` block must be traced to a dataset cell (`agentic/verify.py`,
  tolerant to rounding and unit scaling); an untraced sentence is dropped, an untraced KPI is
  removed, and the count lands in `number_check` (`untraceable_final = 0` holds).
- A trust label on every answer (14): each dataset carries its trust (`certified` for a governed
  metric, `exploratory` for sqlgen, `derived` inherits the weakest input); the answer's figures
  carry it; caveats name non-certified figures.
- Query in place (15): tools run through the existing executor / provider registry only.
- A statically validated plan DAG (17): `agentic/plan.py` validates before `act`.
- No SQL text in steps or blocks (ask chat amendment): `sql` blocks name the query run.

## 4. Routing

`ASK_ENGINE` (worker env): `agentic` (default when a model gateway is configured) or `governed`.
The agentic engine takes a question first; it hands it back to the governed path (`_run`) when
there is no gateway, when the model is unavailable before anything was shown, and for an edited
plan (chips edit is the governed path's contract). An approved (verified) query still answers
first, as today.

## 5. State and graph

```
AgentState: question, today, conversation memory, layer (permission-filtered), workspace
  {datasets: id → Dataset(columns, rows, provenance, trust)}, plan, cursor, observations[],
  replans, blocks[], citations[], models[], status.

prepare  → plan     : load question, conversation memory, layer, sources
plan     → act      : planner LLM → Plan (validated); plan block streamed; or ask/answer direct
act      → reflect  : executor runs the next ready step (tool call, args by the executor LLM
                      when the step is an instruction); observation recorded; step on timeline
reflect  → act      : step ok and steps left
reflect  → replan   : step failed / observation says the plan is wrong (≤ MAX_REPLANS)
reflect  → compose  : all steps done
any      → ask      : ask_user tool → clarification answer
compose  → verify   : reporter LLM → blocks (layout is its choice)
verify   → persist  : number grounding, data materialised into blocks, caveats, trust
```

## 6. Blocks (stored and streamed)

```json
{"type": "markdown", "content": "…"}
{"type": "kpi", "items": [{"label": "…", "dataset": "d1", "column": "value", "row": 0,
                          "compare_column": "previous", "unit": "VND"}]}
{"type": "chart", "dataset": "d2", "chart_type": "bar|line|area|pie|scatter|composed",
 "x": "region", "y": ["value"], "title": "…"}      → server adds spec {chart_type,x_key,y_keys,data}
{"type": "table", "dataset": "d2", "columns": ["…"], "title": "…"} → server adds rows (≤ 200)
{"type": "insights", "items": [{"text": "…", "tone": "positive|negative|neutral"}]}
{"type": "callout", "tone": "info|warning", "content": "…"}
{"type": "code", "language": "python", "content": "…", "dataset": "d3"}
{"type": "sql", "query_run_id": "…"}
{"type": "plan", "steps": [{"id": "s1", "title": "…", "status": "…"}]}
{"type": "follow_ups", "items": ["…"]}
{"type": "citations", "items": [...]}
```

`GET /v1/answers/{id}` returns the stored blocks when there are any (plus server caveats),
else the projection of the old answer shape (answers made before this change).

## 7. What is removed / replaced

- `analyst_plan.build_analysis_plan`'s fixed DAG is no longer what an agentic answer stores; the
  plan stored is the one the planner made and the executor walked (`plan.steps.engine = agentic`).
- The frontend's fixed `answerLayout` grouping is bypassed for agentic answers: blocks are drawn
  one by one.
- The governed path stays as the fallback until the agentic engine has its eval baseline; it is
  deleted in its own commit once `model/eval` passes on the agentic engine.

## 8. Phases

1. Engine core: graph, state, plan validation, tool registry, blocks, workspace, verify, persist,
   routing, migration, api `blocks_of`.
2. Tools: catalogue, governed query, exploratory SQL, python sandbox, documents, ask_user.
3. Frontend: dynamic block renderer, live `block` events, plan block on the timeline.
4. Memory across turns; reflection quality; eval cases on DS-AW.

## 9. Borrowed ideas (where from)

- DataAgent (spring-ai-alibaba): graph of nodes with dispatchers; planner emits a JSON plan of
  steps each tagged with the tool; PlanExecutor walks steps and validates; Python code generated,
  run in a sandbox, its output fed back; report generator writes the final report from all step
  results; human feedback on the plan.
- gpt-researcher: plan sub-questions, research them, write one report from the context.
- RD-Agent: hypothesis → experiment → feedback loop; the feedback decides the next hypothesis.
- WrenAI: chart generation as a constrained spec over the result's columns, never data.
- ragflow: citations by chunk id, inserted by the server.
- Memori: memories extracted per turn and recalled as context.
