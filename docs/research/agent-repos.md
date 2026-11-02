# Other repos: ideas to port into a dynamic Python data agent

Scope: the planner, the graph/loop executor with replanning and reflection, persistent memory, and the typed-block UI stream.
Clones are shallow (`--depth 1`) and live under `research2/repos/`. Clone date is 2026-11-02.

**Source caveats**
- **WrenAI:** `main` no longer has `wren-ai-service`. The repo was restructured into `core/`, `sdk/` and `skills/`. The Python pipelines quoted below come from a sparse clone of the branch `dependabot/pip/wren-ai-service/requests-2.33.0`, which still carries `wren-ai-service/src`. That clone is `repos/WrenAI-legacy/`.
- **ragflow:** the backend has been ported to Go (`internal/`). The prompts are still plain `.md` files or Go string constants, so they port to Python unchanged.
- **Memori:** memory extraction (they call it "augmentation") runs as a hosted call: `api.post("cloud/augmentation", ...)`. The open-source repo has no extraction prompt. It does have the record schema, the storage layout, the recall ranking and the prompt-injection format.
- **databend:** there is no AI analyst or agent piece. It has only a `VECTOR` type, vector-index tests and distance functions (`src/common/vector`, `src/query/functions/src/scalars/vector.rs`). I skipped it.

---

## 1. Zafer-Liu/Data-Analysis-Agent (Flask + SSE, tool-calling agent)

The architecture is a single tool-calling loop over roughly 60 tools (`agent/agent.py`, 5118 lines). It is not a planner. Four pieces are still worth taking.

### 1a. Numeric provenance audit: every number in the final answer must trace to a tool result
File: `agent/number_audit.py`. This is a deterministic gate that runs before the answer is emitted. It fits our invariants ("LLM proposes, deterministic checks decide", "trust label on every answer"), and it is the most portable piece in this list.

```python
# Tools whose success marks a turn as "data-derived"; only those turns are audited.
NUMBER_EVIDENCE_TOOLS = frozenset({
    "query_data", "run_analysis", "create_analysis_table", "profile_data",
    "fetch_table", "browser_extract_tables", "generate_chart",
})

def collect_number_evidence(messages) -> tuple[list[str], list[str]]:
    """Split a message list into ``(tool_result_texts, context_texts)``.

    Context texts (user messages and the agent's own SQL/tool arguments) are
    matched as legitimate number sources: thresholds written into SQL such as
    ``AQI > 100`` and facts the user stated must not trigger the gate.
    Assistant prose is intentionally excluded — that is exactly the channel
    through which fabricated numbers would re-enter.
    """
    evidence_texts, context_texts = [], []
    for message in messages:
        role = message.get("role")
        if role == "tool":
            evidence_texts.append(str(message.get("content") or ""))
        elif role == "user":
            context_texts.append(str(message.get("content") or ""))
        elif role == "assistant":
            for call in message.get("tool_calls") or []:
                function = call.get("function") or {}
                context_texts.append(str(function.get("arguments") or ""))
    return evidence_texts, context_texts

def audit_report_numbers(report_text, evidence_texts, context_texts=()):
    texts = [*(evidence_texts or []), *(context_texts or [])]
    blob = " ".join(str(t)[:_MAX_TEXT_CHARS].replace(",", "") for t in texts if t)
    values = _parse_corpus_values(texts)
    float_values = {float(v) for v in values if v}
    unsupported = []
    for key, value, decimals in _extract_report_numbers(report_text):
        if key in blob: continue
        if value in values: continue
        if any(_quantize(e, decimals) == value for e in values): continue   # 40.7 matches 40.73
        if decimals == 0:
            # Derived integer deltas: x == a + b or x == a - b, both operands >= 100
            target = float(value); derived = False
            for a in float_values:
                if a < 100: continue
                for partner in (round(target - a, 6), round(a - target, 6)):
                    if partner >= 100 and partner in float_values:
                        derived = True; break
                if derived: break
            if derived: continue
        unsupported.append(key)
    return unsupported[:_MAX_LISTED], len(unsupported)
```
The audit skips these as noise: integers below 100, years, decimals below 3, ranges (`2013-2017`), identifier-embedded numbers (`PM2.5`), and values followed by `% ‰ 万 亿 倍`.
**Port:** run it over every `markdown`, `kpi` and `insight` block before the stream closes. When it flags numbers, either send the turn back to the writer, which is the reflection step, or downgrade the trust label. Our evidence corpus would be the query-run results instead of tool messages.

### 1b. Deterministic stage routing that narrows the tools the model can see
File: `agent/workflow_stage.py`. The stage is inferred from tools that already ran in the turn, not chosen by the LLM. Each stage then filters the tool schemas.

```python
STAGES = frozenset({"general","understand","inspect","analyze","visualize",
                    "propose_output","generate_output","verify"})

def infer_workflow_stage(*, activation, user_message, has_data_source,
                         needs_chart, needs_output, completed_tools=()):
    """Infer a conservative stage from immutable turn evidence."""
    done = frozenset(str(name) for name in completed_tools)
    if activation.internal_action.endswith("_confirm"):
        return WorkflowStageContext("generate_output", needs_chart, True)
    if done & _GENERATION_TOOLS:  return WorkflowStageContext("verify", needs_chart, needs_output)
    if done & _PROPOSAL_TOOLS:    return WorkflowStageContext("propose_output", needs_chart, True)
    if needs_chart and done & {"query_data", "run_analysis", "create_analysis_table"}:
        return WorkflowStageContext("visualize", True, needs_output)
    if done & {"get_schema", "get_table_detail", "query_data", "run_analysis"}:
        return WorkflowStageContext("analyze", needs_chart, needs_output)
    ...
```
**Port:** in a dynamic planner, the step types a replan may propose (for example `chart` before any `semantic_query` has produced data) can be constrained the same way. This works as a static guard on the plan DAG.

### 1c. Long-term memory: typed, scoped, human-readable records, with extraction and consolidation prompts
Files: `agent/memory.py` (prompts) and `data/memory_store.py` (store). Each record is a markdown file with YAML front matter. A `MEMORY.md` index is injected into the system prompt, and the full body is read on demand through a `memory_read(name)` tool.

Record schema (`data/memory_store.py`):
```python
MEMORY_TYPES = {"user", "feedback", "project", "reference"}
USER_TYPES = {"user", "feedback"}            # scope = user (global)
WORKSPACE_TYPES = MEMORY_TYPES - USER_TYPES  # scope = workspace
# front matter: name, type, title, created_at, updated_at, last_seen_at, source_session, [why], [how_to_apply]; body = markdown
def render_memory_section(*, user_id="", workspace_id="") -> str:
    ...
    return "\n\n[LONG-TERM MEMORY]\n" + content + "Use memory_read(name) only when the full record is needed.\n[END LONG-TERM MEMORY]"
```
Extraction prompt, verbatim (`agent/memory.py`, `_EXTRACT_SYSTEM`; it runs on a background thread after each turn):
```text
You extract durable memory for a local data-analysis assistant. Return JSON only.
Your first non-whitespace character must be `{`; do not emit reasoning, `<think>` tags, Markdown fences, or prose.
Remember only explicit user preferences (user), corrections (feedback), confirmed workspace facts (project), or durable resource pointers (reference). Do not store tasks, casual chat, raw query values, credentials, connection strings, secrets, or private data. Existing memory summaries are included for deduplication.
Explicit output preferences MUST be recorded: chart title/label language, number formatting, metric definitions, report style, and any rule the user says applies from now on. Example: "图表标题用中文" -> create user memory; "毛利口径=(收入-成本)/收入" -> create user memory.
Scope is mandatory: user/feedback are global user memory; project/reference are current-workspace memory. Explicit wording wins: "本项目"、"当前工作区"、project names, table/field names, dataset rules, and file paths mean project/reference. "以后"、"所有项目"、"默认" and personal output preferences mean user/feedback. When unsure, choose project rather than polluting global memory.
Output {"ops":[]} when nothing is durable. Each operation is create or update with type, name, title, body, optional why and how_to_apply. The "op" field holds the verb ("create" or "update"); the "type" field holds the memory category ("user", "feedback", "project", or "reference"). Never put the verb in "type" and never omit "type". Names must use lowercase kebab-case, for example unit-net-rate-formula. At most 5 operations.
```
Consolidation prompt (`_CONSOLIDATE_SYSTEM`; runs at most once every 24 h behind a lock file, with at most 20 operations):
```text
You curate the long-term memory of a local data-analysis assistant. Return JSON only.
Given today's date and every memory record, merge duplicates or overlapping records into the most complete one, rewrite relative dates (e.g. "last week") as absolute dates, and archive records that are stale, superseded, or merged away. Never invent new facts and never store secrets. Output {"ops":[]} when nothing needs curation. Each operation is either {"op":"update","name":...,"title":...,"body":...} (partial fields allowed) or {"op":"archive","name":...}. At most 20 operations.
```
**Port:** this is the memory model closest to what we need. A "metric definition the user stated" is a `project` memory. For us that should become a *proposed* glossary or semantic-layer change that waits for review, not a silent override. Keep the hard rule "do not store raw query values".

### 1d. Typed job events and a chart registry with role contracts
`agent/events.py` defines frozen dataclasses (`JobCreated/Started/Progress/ArtifactCreated/Done/Error/Canceled`), each carrying a `ClassVar event_type`. Only the HTTP layer turns them into SSE JSON. This is the right shape for our block stream.

`LLM/chart_selector.py` holds a chart registry with entries `dict(chart_id, name, category, required_roles=["x","y","series"], optional_roles, data_format, constraints, desc)`. The `select_chart(user_intent, available_columns)` tool returns the best matches, and the model must call it *before* `generate_chart(chart_id, field_mapping)`. That gives a two-step chart flow, "pick a type from a catalogue, then fill role→column mapping". The mapping is easy to validate deterministically.

---

## 2. assafelovic/gpt-researcher (planner → parallel sub-research → writer → review loops)

### 2a. Planner prompts
Sub-query planner (`gpt_researcher/prompts.py::generate_search_queries_prompt`):
```text
Write {max_iterations} search queries to research the following task: "{task}"

Each query must be a plain natural language phrase. Do not use search operator syntax
such as site:, filetype:, inurl:, intitle:, OR, AND, or NOT — these operators are
not universally supported and will return empty results on many search backends.

Assume the current date is {date} if required.

{context_prompt}
You must respond with a list of strings in the following format: [{dynamic_example}].
The response should contain ONLY the list.
```
`context_prompt` is filled with the results of an initial search, so the plan is grounded in a first look at the data. The equivalent for us is to run schema/metric retrieval first and then plan.

Section planner, the "editor" (`multi_agents/agents/editor.py::_format_planning_instructions`):
```text
Today's date is {today}
Research summary report: '{initial_research}'
{feedback_instruction}   # "Human feedback: {human_feedback}. You must plan the sections based on the human feedback."
Your task is to generate an outline of sections headers for the research project
based on the research summary report above.
You must generate a maximum of {max_sections} section headers.
You must focus ONLY on related research topics for subheaders and do NOT include introduction, conclusion and references.
You must return nothing but a JSON with the fields 'title' (str) and
'sections' (maximum {max_sections} section headers) with the following structure:
'{title: string research title, date: today's date,
sections: ['section header 1', 'section header 2', 'section header 3' ...]}'.
```

### 2b. Deep research: breadth × depth recursion with learnings and follow-ups
File: `gpt_researcher/skills/deep_research.py`. Every node returns learnings with citations plus follow-up questions. Children are generated from `researchGoal` and the follow-ups. Breadth halves at each level, and branches run concurrently under a semaphore.

```python
# generate_search_queries — user message
f"Given the following prompt, generate {num_queries} unique search queries to research the topic thoroughly. "
"For each query, provide a research goal.\n\n"
"Return ONLY a JSON array of objects using this exact schema:\n"
'[{"query": "<search query>", "researchGoal": "<research goal>"}]\n\n'
f"Prompt: {query}"

# process_research_results — user message
f"Given the following research results for the query '{query}', extract key learnings and suggest "
"follow-up questions. For each learning, include a citation to the source URL if available.\n\n"
"Return ONLY a JSON object using this exact schema:\n"
'{"learnings": [{"insight": "<insight>", "sourceUrl": "<url or empty string>"}], '
'"followUpQuestions": ["<question 1>", "<question 2>"]}\n\n'
f"Research results:\n{context}"
```
```python
semaphore = asyncio.Semaphore(self.concurrency_limit)
async def process_query(serp_query):
    async with semaphore:
        researcher = GPTResearcher(query=serp_query['query'], ...)   # a full sub-agent per branch
        context = await researcher.conduct_research()
        results = await self.process_research_results(query=serp_query['query'], context=context)
        return {'learnings': results['learnings'], 'followUpQuestions': results['followUpQuestions'],
                'researchGoal': serp_query['researchGoal'], 'citations': results['citations'], ...}
results = [r for r in await asyncio.gather(*[process_query(q) for q in serp_queries]) if r]
if not results:   # #1579: every branch failed -> stop instead of endlessly generating follow-ups
    return {...}
for result in results:
    if depth > 1:
        next_query = f"""
        Previous research goal: {result['researchGoal']}
        Follow-up questions: {' '.join(result['followUpQuestions'])}
        """
        deeper = await self.deep_research(query=next_query, breadth=max(2, breadth // 2), depth=depth - 1, ...)
```
**Port:** the record `{insight, sourceUrl}` maps to our `insight` block as `{text, evidence_ref}`, where `evidence_ref` is a query-run id or a chunk id. The "stop when every branch failed" rule belongs in the executor.

### 2c. The graph itself (LangGraph): bounded revise loops that force-accept at a ceiling
File: `multi_agents/agents/orchestrator.py`.
```python
workflow.add_node("browser", agents["research"].run_initial_research)
workflow.add_node("planner", agents["editor"].plan_research)
workflow.add_node("researcher", agents["editor"].run_parallel_research)
workflow.add_node("writer", agents["writer"].run)
workflow.add_node("fact_checker", agents["fact_checker"].run)
workflow.add_node("visualizer", agents["visualizer"].run)
workflow.add_node("publisher", agents["publisher"].run)
workflow.add_node("human", agents["human"].review_plan)
workflow.add_edge('browser', 'planner'); workflow.add_edge('planner', 'human')
workflow.add_edge('researcher', 'writer'); workflow.add_edge('writer', 'fact_checker')
workflow.add_edge('visualizer', 'publisher'); workflow.add_edge('publisher', END)
workflow.add_conditional_edges('human', self._route_human_feedback, {"accept": "researcher", "revise": "planner"})
workflow.add_conditional_edges('fact_checker', self._route_fact_check, {"accept": "visualizer", "revise": "writer"})
```
Each section is itself a subgraph, `researcher → reviewer ⇄ reviser`, and `_route_draft_review` returns `"accept"` once `max_draft_revisions` is exceeded. That avoids crashing on the recursion limit. Parallel section writers also receive `sibling_sections` so they stay off each other's ground (#1495).

Fact checker system prompt (`multi_agents/agents/fact_checker.py`):
```text
You are a diligent Fact Checker Agent. Your goal is to review a research report draft, identify potential factual inaccuracies, hallucinations, or inconsistencies based on the provided research context, and decide if it needs to be sent back to the writer for revision.
--- user:
Review the following draft for factual accuracy:\n\n{draft}\n\nIf you find critical errors, provide a list of them so the writer can revise the draft. If the draft is factually sound, return exactly the string 'None'. Do not return any other text if it is accepted.
```
**Port:** the shape is `plan → human approval (optional) → parallel step execution → writer → checker ⇄ writer (bounded) → publish`. Three ideas carry over:
- our "clarification ask" step is the `human` node;
- the deterministic number audit from 1a should run *before* the LLM fact-checker;
- every loop gets a ceiling with force-accept plus a trust downgrade, never an exception.

### 2d. Writer citation rule (`generate_report_prompt`)
```text
- Use in-text citation references in {report_format} format and make it with markdown hyperlink placed at the end of the sentence or paragraph that references them like this: ([in-text citation](url)).
- Every substantive claim, figure or quote MUST carry an in-text citation to the source it came from. Do NOT cite sources that do not appear in the provided information.
- You MUST determine your own concrete and valid opinion based on the given information. Do NOT defer to general and meaningless conclusions.
```

---

## 3. microsoft/RD-Agent (hypothesis → experiment → run → feedback → trace)

### 3a. The loop as named steps, with checkpointing, parallel loops and an exception channel
`rdagent/utils/workflow/loop.py` uses a `LoopMeta` metaclass that collects every public method of the subclass, in definition order, as `steps`. `LoopBase` runs them step by step and pickles the session after each one (resume / traceback). Each step receives `prev_out: dict[step_name → output]`. Exceptions land in `prev_out["_EXCEPTION"]` instead of killing the loop. `skip_loop_error` and `withdraw_loop_error` decide whether to skip ahead to a named step or to drop the loop.

The concrete loop is `rdagent/components/workflow/rd_loop.py`:
```python
class RDLoop(LoopBase, metaclass=LoopMeta):
    async def direct_exp_gen(self, prev_out):
        while True:
            if self.get_unfinished_loop_cnt(self.loop_idx) < RD_AGENT_SETTINGS.get_max_parallel():
                hypo = self._propose()               # HypothesisGen.gen(trace, plan) (+ user may edit: _interact_hypo)
                exp = self._exp_gen(hypo)            # Hypothesis2Experiment.convert(hypo, trace)
                return {"propose": hypo, "exp_gen": exp}
            await asyncio.sleep(1)
    def coding(self, prev_out):  return self.coder.develop(prev_out["direct_exp_gen"]["exp_gen"])
    def running(self, prev_out): return self.runner.develop(prev_out["coding"])
    def feedback(self, prev_out):
        e = prev_out.get(self.EXCEPTION_KEY, None)
        if e is not None:
            feedback = HypothesisFeedback(reason=str(e), decision=False, code_change_summary="", acceptable=False)
        else:
            feedback = self.summarizer.generate_feedback(prev_out["running"], self.trace)
        return self._interact_feedback(feedback)    # human can override the feedback too
    def record(self, prev_out):
        exp = prev_out.get("running") or prev_out.get("coding") or prev_out["direct_exp_gen"]["exp_gen"]
        self.trace.sync_dag_parent_and_hist((exp, prev_out["feedback"]), prev_out[self.LOOP_IDX_KEY])
```

### 3b. Trace = DAG of (experiment, feedback), with SOTA selection
File: `rdagent/core/proposal.py`.
```python
class Hypothesis:
    def __init__(self, hypothesis, reason, concise_reason, concise_observation,
                 concise_justification, concise_knowledge): ...

class HypothesisFeedback(ExperimentFeedback):
    # ExperimentFeedback: reason, decision: bool, code_change_summary, eda_improvement, exception
    def __init__(self, reason, decision, code_change_summary="", *, observations=None,
                 hypothesis_evaluation=None, new_hypothesis=None, eda_improvement=None,
                 acceptable=None, exception=None): ...

class Trace:
    NodeType = tuple[Experiment, ExperimentFeedback]
    NEW_ROOT: tuple = ()
    SEL_LATEST_SOTA: tuple = (-1,)
    def __init__(self, scen, knowledge_base=None):
        self.hist: list[Trace.NodeType] = []          # (exp, feedback) over time
        self.dag_parent: list[tuple[int, ...]] = []   # () root, (i,) one parent
        self.current_selection = self.SEL_LATEST_SOTA
    def get_sota_hypothesis_and_experiment(self):
        for experiment, feedback in self.hist[::-1]:
            if feedback.decision: return experiment.hypothesis, experiment
        return None, None
```
`CheckpointSelector.get_selection(trace)` decides where the next node branches from: the latest node, a specific node, or a new tree.

### 3c. Prompts: hypothesis generation that reflects over the trace, and structured feedback
`rdagent/components/proposal/prompts.yaml`, `hypothesis_gen.system_prompt` (verbatim excerpt):
```text
The user has already proposed several hypotheses and conducted evaluations on them. This information will be provided to you. Your task is to analyze previous experiments, reflect on the decision made in each experiment, and consider why experiments with a decision of true were successful while those with a decision of false failed. Then, think about how to improve further — either by refining the existing approach or by exploring an entirely new direction.
If one exists and you agree with it, feel free to use it. If you disagree, please generate an improved version.
```
`hypothesis_gen.user_prompt`:
```jinja
{% if hypothesis_and_feedback|length == 0 %}
It is the first round of hypothesis generation. The user has no hypothesis on this scenario yet.
{% else %}
The former hypothesis and the corresponding feedbacks are as follows:
{{ hypothesis_and_feedback }}
{% endif %}
{% if last_hypothesis_and_feedback %}
Here is the last trial's hypothesis and the corresponding feedback (The main feedback contains a new hypothesis for your reference only. You need to evaluate the complete trace chain to decide whether to adopt it or propose a more appropriate hypothesis):
{{ last_hypothesis_and_feedback }}
{% endif %}
{% if sota_hypothesis_and_feedback != "" %}
Here is the SOTA trail's hypothesis and the corresponding feedback:
{{ sota_hypothesis_and_feedback }}
{% endif %}
```
Trace rendering (`rdagent/scenarios/qlib/prompts.yaml::hypothesis_and_feedback`):
```jinja
{% for experiment, feedback in trace.hist %}
# Trial {{ loop.index }}:
## Hypothesis
{{ experiment.hypothesis }}
...
Observation: {{ feedback.observations }}
Hypothesis Evaluation: {{ feedback.hypothesis_evaluation }}
Decision (Whether the hypothesis was successful): {{ feedback.decision }}
{% endfor %}
```
Hypothesis output format:
```json
{
"hypothesis": "An exact, testable, and innovative statement derived from previous experimental trace analysis. Avoid overly general ideas and ensure precision. The hypothesis should clearly specify the exact approach and expected improvement in performance in two or three sentences.",
"reason": "Provide a clear, logical explanation for why this hypothesis was proposed, grounded in evidence (e.g., trace history, domain principles). Reason should be short with no more than two sentences."
}
```
Feedback output (`factor_feedback_generation`):
```json
{
  "Observations": "Your overall observations here",
  "Feedback for Hypothesis": "Observations related to the hypothesis",
  "New Hypothesis": "Your new hypothesis here",
  "Reasoning": "Reasoning for the new hypothesis",
  "Replace Best Result": "yes or no"
}
```
The data-science scenario (`rdagent/scenarios/data_science/proposal/exp_gen/proposal.py`) adds a hypothesis critic. `HypothesisEvaluation` scores `alignment`, `impact`, `novelty`, `feasibility` and `risk_reward_balance`, each as `{reasoning: str, score: 1-10}`, and the critic chooses among several candidate hypotheses.

**Port:** this is the template for "why did revenue drop?" analyses, i.e. diagnostic questions:
- hypothesis = "the drop is driven by segment X";
- experiment = a semantic query or Python decomposition;
- feedback = `{observations, hypothesis_evaluation, decision, new_hypothesis}`;
- trace = what the replanner reads.

`record()` as the single last step that appends to the trace is a clean invariant to copy. The exception-as-feedback path (`decision=False, reason=str(e)`) is how the replanner should see a failed SQL step.

---

## 4. Canner/WrenAI (wren-ai-service pipelines; legacy branch)

Pipeline layout: `wren-ai-service/src/pipelines/{generation,retrieval,indexing}`. The generation pipelines are `intent_classification`, `sql_generation_reasoning`, `sql_generation`, `sql_correction`, `sql_diagnosis`, `followup_sql_generation`, `sql_answer`, `chart_generation`, `chart_adjustment`, `question_recommendation`, `data_assistance`, `misleading_assistance`, `user_guide_assistance` and `relationship_recommendation`. Each pipeline is a Hamilton DAG of small functions (`prompt → generate → post_process`) and is called from FastAPI services that keep a per-query status record.

### 4a. Chart generation: the prompt plus Pydantic-typed Vega-Lite as structured output, then JSON-Schema validation
File: `pipelines/generation/chart_generation.py`. The system prompt (verbatim) is:
```text
### TASK ###

You are a data analyst great at visualizing data using vega-lite! Given the user's question, SQL, sample data and sample column values, you need to generate vega-lite schema in JSON and provide suitable chart type.
Besides, you need to give a concise and easy-to-understand reasoning to describe why you provide such vega-lite schema based on the question, SQL, sample data and sample column values.

{chart_generation_instructions}
- If the user provides a custom instruction, it should be followed strictly and you should use it to change the style of response for reasoning.

### OUTPUT FORMAT ###

Please provide your chain of thought reasoning, chart type and the vega-lite schema in JSON format.

{
    "reasoning": <REASON_TO_CHOOSE_THE_SCHEMA_IN_STRING_FORMATTED_IN_LANGUAGE_PROVIDED_BY_USER>,
    "chart_type": "line" | "multi_line" | "bar" | "pie" | "grouped_bar" | "stacked_bar" | "area" | "",
    "chart_schema": <VEGA_LITE_JSON_SCHEMA>
}
```
The user template is:
```jinja
### INPUT ###
Question: {{ query }}
SQL: {{ sql }}
Sample Data: {{ sample_data }}
Sample Column Values: {{ sample_column_values }}
Language: {{ language }}
Custom Instruction: {{ custom_instruction }}

Please think step by step
```
`chart_generation_instructions` (`pipelines/generation/utils/chart.py`, verbatim INSTRUCTIONS part; the file goes on with a "GUIDELINES TO PLOT CHART" section on data types and when to use each chart, and with seven worked examples):
```text
- Chart types: Bar chart, Line chart, Multi line chart, Area chart, Pie chart, Stacked bar chart, Grouped bar chart
- You can only use the chart types provided in the instructions
- Generated chart should answer the user's question and based on the semantics of the SQL query, and the sample data, sample column values are used to help you generate the suitable chart type
- If the sample data is not suitable for visualization, you must return an empty string for the schema and chart type
- If the sample data is empty, you must return an empty string for the schema and chart type
- The language for the chart and reasoning must be the same language provided by the user
- Please use the current time provided by the user to generate the chart
- In order to generate the grouped bar chart, you need to follow the given instructions:
    - Disable Stacking: Add "stack": null to the y-encoding.
    - Use xOffset for subcategories to group bars.
    - Don't use "transform" section.
- In order to generate the pie chart, you need to follow the given instructions:
    - Add {"type": "arc"} to the mark section.
    - Add "theta" encoding to the encoding section.
    - Add "color" encoding to the encoding section.
    - Don't add "innerRadius" to the mark section.
- If the x-axis of the chart is a temporal field, the time unit should be the same as the question user asked.
    - For yearly question, the time unit should be "year".
    - For monthly question, the time unit should be "yearmonth".
    - For weekly question, the time unit should be "yearmonthdate".
    - For daily question, the time unit should be "yearmonthdate".
    - Default time unit is "yearmonth".
- For each axis, generate the corresponding human-readable title based on the language provided by the user.
- Make sure all of the fields(x, y, xOffset, color, etc.) in the encoding section of the chart schema are present in the column names of the data.
```
Example (multi-line, using `fold`):
```json
{ "title": "<TITLE_IN_LANGUAGE_PROVIDED_BY_USER>", "mark": {"type": "line"},
  "transform": [{"fold": ["readCount", "clickCount"], "as": ["Metric", "Value"]}],
  "encoding": {
    "x": {"field": "Date", "type": "temporal", "title": "<...>"},
    "y": {"field": "Value", "type": "quantitative", "title": "<...>"},
    "color": {"field": "Metric", "type": "nominal", "title": "<...>"} } }
```
The structured-output schema is passed as `response_format={"type":"json_schema","json_schema":{"name":"chart_generation_schema","schema": ChartGenerationResults.model_json_schema()}}`:
```python
class ChartSchema(BaseModel):
    class ChartType(BaseModel):
        type: Literal["bar", "line", "area", "arc"]
    class ChartEncoding(BaseModel):
        field: str
        type: Literal["ordinal", "quantitative", "nominal"]
        title: str
    title: str
    mark: ChartType
    encoding: ChartEncoding

class TemporalChartEncoding(ChartSchema.ChartEncoding):
    type: Literal["temporal"] = Field(default="temporal")
    timeUnit: str = Field(default="yearmonth")

class LineChartSchema(ChartSchema):
    class LineChartMark(BaseModel):
        type: Literal["line"] = Field(default="line")
    class LineChartEncoding(BaseModel):
        x: TemporalChartEncoding | ChartSchema.ChartEncoding
        y: ChartSchema.ChartEncoding
        color: ChartSchema.ChartEncoding
    mark: LineChartMark
    encoding: LineChartEncoding

class MultiLineChartSchema(ChartSchema):      # + transform: list[{fold: list[str], as_: list[str] = Field(alias="as")}]
class BarChartSchema(ChartSchema):            # x, y, color
class GroupedBarChartSchema(ChartSchema):     # x, y, xOffset, color
class StackedBarChartYEncoding(ChartSchema.ChartEncoding):
    stack: Literal["zero"] = Field(default="zero")
class StackedBarChartSchema(ChartSchema):     # x, y: StackedBarChartYEncoding, color
class PieChartSchema(ChartSchema):            # mark arc; theta, color
class AreaChartSchema(ChartSchema):           # x, y

class ChartGenerationResults(BaseModel):
    reasoning: str
    chart_type: Literal["line", "multi_line", "bar", "pie", "grouped_bar", "stacked_bar", "area", ""]  # empty string for no chart
    chart_schema: (LineChartSchema | MultiLineChartSchema | BarChartSchema | PieChartSchema
                   | GroupedBarChartSchema | StackedBarChartSchema | AreaChartSchema)
```
Pre- and post-processing (`ChartDataPreprocessor`, `ChartGenerationPostProcessor`):
```python
# preprocess: 15 sampled rows + up to 5 distinct values per column go to the LLM, never the full result
sample_column_values = {col: list(df[col].unique())[:sample_column_size] for col in df.columns}
sample_data = df.sample(n=sample_data_count).to_dict(orient="records") if len(df) > sample_data_count else df.to_dict(orient="records")

# postprocess
chart_schema["$schema"] = "https://vega.github.io/schema/vega-lite/v5.json"
chart_schema["data"] = {"values": sample_data}
validate(chart_schema, schema=vega_schema)        # jsonschema against bundled utils/vega-lite-schema-v5.json
if remove_data_from_chart_schema:
    chart_schema["data"]["values"] = []           # the client re-binds real rows
# on ValidationError / JSON error -> {"chart_schema": {}, "reasoning": "", "chart_type": ""}
```
**Port as is:** our `chart` block becomes `{chart_type, vega_lite (without data), data_ref → query run, reasoning}`. The LLM sees samples only. The server validates against the bundled Vega-Lite v5 JSON Schema, and the frontend binds the rows.

### 4b. Chart adjustment: the user edits the chart, the LLM regenerates it
`pipelines/generation/chart_adjustment.py` reuses the same instructions plus `- If you think the adjustment options are not suitable for the data, you can return an empty string for the schema and chart type and give reasoning to explain why.` Its input template:
```jinja
### INPUT ###
Original Question: {{ query }}
Original SQL: {{ sql }}
Original Vega-Lite Schema: {{ chart_schema }}
Sample Data: {{ sample_data }}
Sample Column Values: {{ sample_column_values }}
Language: {{ language }}

Adjustment Options:
- Chart Type: {{ adjustment_option.chart_type }}
{% if adjustment_option.chart_type != "pie" %}{% if adjustment_option.x_axis %}- X Axis: {{ adjustment_option.x_axis }}{% endif %}
{% if adjustment_option.y_axis %}- Y Axis: {{ adjustment_option.y_axis }}{% endif %}{% endif %}
{% if adjustment_option.x_offset and adjustment_option.chart_type == "grouped_bar" %}- X Offset: {{ adjustment_option.x_offset }}{% endif %}
{% if adjustment_option.color and adjustment_option.chart_type != "area" %}- Color: {{ adjustment_option.color }}{% endif %}
{% if adjustment_option.theta and adjustment_option.chart_type == "pie" %}- Theta: {{ adjustment_option.theta }}{% endif %}
```
**Port:** a chart block can carry an "adjust" affordance (type / x / y / color / offset / theta pickers) that calls this one pipeline. No new agent turn is needed.

### 4c. SQL → natural-language answer (`pipelines/generation/sql_answer.py`)
```text
### TASK
You are a data analyst that great at answering non-technical user's questions based on the data, sql so that even non technical users can easily understand.
Please answer the user's question in concise and clear manner in Markdown format.

### INSTRUCTIONS
1. Read the user's question and understand the user's intention.
2. Read the sql and understand the data.
3. Make sure the answer is aimed for non-technical users, so don't mention any technical terms such as SQL syntax.
4. Generate a concise and clear answer in string format to answerthe user's question based on the data and sql.
5. If answer is in list format, only list top few examples, and tell users there are more results omitted.
6. Answer must be in the same language user specified.
7. Do not include ```markdown or ``` in the answer.
8. If the user provides a custom instruction, it should be followed strictly and you should use it to change the style of response.
```
Its inputs are `User's question`, `SQL`, `Data: columns / rows`, `Language`, `Current Time` and `Custom Instruction`. The answer is streamed.

### 4d. Follow-ups and question recommendation (`pipelines/generation/question_recommendation.py`)
Output is `{"questions":[{"question": "...", "category": "..."}]}`. Verbatim guidelines (excerpt):
```text
2. **Incorporate Diverse Analysis Techniques:**
   - Use a mix of the following analysis techniques for each category:
     - **Drill-down:** Delve into detailed levels of data.
     - **Roll-up:** Aggregate data to higher levels.
     - **Slice and Dice:** Analyze data from different perspectives.
     - **Trend Analysis:** Identify patterns or changes over time.
     - **Comparative Analysis:** Compare segments, groups, or time periods.
3. **If a User Question is Provided:**
   - Generate questions that are closely related to the user's previous question, ensuring that the new questions build upon or provide deeper insights into the original query.
5. **General Guidelines for All Questions:**
   - Ensure questions can be answered using the data model.
   - Mix simple and complex questions.
   - Avoid open-ended questions - each should have a definite answer.
   - Incorporate time-based analysis where relevant.
### Categories of Questions
1. Descriptive Questions  2. Segmentation Questions  3. Comparative Questions  4. Data Quality/Accuracy Questions
```
**Port:** this becomes our `follow_ups` block. Validate each suggestion against the semantic layer (it must resolve to known metrics and dimensions) before rendering it.

### 4e. Intent routing with rephrasing, and a status state machine for the progress block
The `intent_classification.py` output is `{"rephrased_question", "reasoning" (≤20 words), "results": "MISLEADING_QUERY" | "TEXT_TO_SQL" | "GENERAL" | "USER_GUIDE"}`. Key rules: **Rephrase Question:** "Rewrite follow-up questions into full standalone questions using prior conversation context". **Incomplete Queries:** "references unspecified values (e.g., 'the following', 'these', 'those') without providing them, classify as `GENERAL`". **Time-related Queries:** "Don't rephrase time-related information".

The ask service (`web/v1/services/ask.py`) writes a status record that the client polls or streams:
```python
status: Literal["understanding","searching","planning","generating","correcting","finished","failed","stopped"]
rephrased_question, intent_reasoning, sql_generation_reasoning, type: Literal["GENERAL","TEXT_TO_SQL"],
retrieved_tables, response: list[AskResult{sql, type: "llm"|"view", viewId}], invalid_sql,
error: AskError{code: Literal["NO_RELEVANT_DATA","NO_RELEVANT_SQL","OTHERS"], message}, trace_id, is_followup
```
The correction loop runs `while retries < max_sql_correction_retries` and breaks on `TIME_OUT`. It runs `sql_diagnosis`, an LLM explanation of the error in 50 words or fewer, mapped back to the original SQL, and then `sql_correction` on `{sql, error: diagnosis_reasoning}`.

The `sql_generation_reasoning` system prompt produces a user-visible numbered plan. That is a candidate `plan` block:
```text
10. Each step in the reasoning plan must start with a number, a title(in bold format in markdown), and a reasoning for the step.
12. A table name in the reasoning plan must be in this format: `table: <table_name>`.
13. A column name in the reasoning plan must be in this format: `column: <table_name>.<column_name>`.
9. Don't include SQL in the reasoning plan.
```

---

## 5. infiniflow/ragflow (now in Go; agent canvas, DeepResearcher, citations)

### 5a. Citation format and prompt: `[ID:i]` against chunks rendered as a tree
File: `internal/rag/prompts/citation_prompt.md`. Verbatim rules:
```text
## Technical Rules:
- Use format: [ID:i] or [ID:i] [ID:j] for multiple sources
- Cite each source INDIVIDUALLY: [ID:i][ID:j]. NEVER merge consecutive citations into a range such as [ID:i-j] or [ID:i~j]
- Place citations at the end of sentences, before punctuation
- Maximum 4 citations per sentence
- DO NOT cite content not from <context></context>
- DO NOT modify whitespace or original text
- STRICTLY prohibit non-standard formatting (~~, etc.)

## What MUST Be Cited:
1. **Quantitative data**: Numbers, percentages, statistics, measurements
2. **Temporal claims**: Dates, timeframes, sequences of events
3. **Causal relationships**: Claims about cause and effect
4. **Comparative statements**: Rankings, comparisons, superlatives
5. **Technical definitions**: Specialized terms, concepts, methodologies
6. **Direct attributions**: What someone said, did, or believes
7. **Predictions/forecasts**: Future projections, trend analyses
8. **Controversial claims**: Disputed facts, minority opinions
9. **List/enumeration answers**: When the answer lists several things (people, places, dates,
   events), cite EACH element with the passage that states it — one citation per element, never
   one citation for the whole list. If you cannot cite an element, say which one you cannot;
   never write that "some of the list" is supported.

## What Should NOT Be Cited:
- Common knowledge (e.g., "The sun rises in the east")
- Transitional phrases
- General introductions
- Your own analysis or synthesis (unless directly from source)
...
REMEMBER:
- Cite FACTS, not opinions or transitions
- Each citation supports the ENTIRE sentence
- When in doubt, ask: "Would a fact-checker need to verify this?"
- Format likes this is FORBIDDEN: [ID:0, ID:5, ID:...]. It MUST be separated like, [ID:0][ID:5]...
- Range citations like [ID:1-3] are FORBIDDEN too. Multiple sources MUST be spelled out individually: [ID:1][ID:2][ID:3].
```
Context rendering (`internal/service/deep_researcher.go::kbPrompt`). Chunks are cut at 97% of the token budget and each is drawn as:
```text
ID: 45
├── Title: <document name>
├── URL: <url>
├── <metadata key>: <value>        (sorted keys, newlines collapsed)
└── Content:
<chunk text>
```
Post-processing (`internal/service/citation.go`) has three parts:
1. `CitationMarkerPattern = \[(?:ID:\s*)?([0-9…]+)\]`.
2. `RepairBadCitationFormats` rewrites `(ID: 12)`, `【ID: 12】`, `ref12`, `(**ID: 12**)` and ranges `[ID:1-3]` into the canonical `[ID:n]`. A `citationStreamFilter` holds back a half-written marker at the end of a streamed delta.
3. **Fallback when the model cites nothing:** `InsertCitations` splits the answer into sentences, embeds them, takes the cosine similarity to the chunk vectors and assigns citations by threshold descent:
```go
thr := 0.63
for thr > 0.3 && len(cites) == 0 {
    for i := range sim {
        mx := maxRow(sim[i]) * 0.99
        if mx < thr { continue }
        // every chunk with s > mx, capped at 4 per sentence
    }
    thr *= 0.8
}
```
**Port:** use the same `[ID:n]` syntax for document chunks *and* for query runs, and render citations as chips in markdown/insight blocks. The repair regex plus the stream filter is what makes this robust while streaming.

### 5b. DeepResearcher: retrieve → sufficiency check → missing-info sub-queries → parallel recursion
File: `internal/service/deep_researcher.go`. Sufficiency check prompt, verbatim:
```text
You are a information retrieval evaluation expert. Please assess whether the currently retrieved content is sufficient to answer the user's question.

User question:
%s

Retrieved content:
%s

Please determine whether these content are sufficient to answer the user's question.

Output format (JSON):
{
    "is_sufficient": true/false,
    "reasoning": "Your reasoning for the judgment",
    "missing_information": ["Missing information 1", "Missing information 2"]
}

Requirements:
1. If the retrieved content contains key information needed to answer the query, judge as sufficient (true).
2. If key information is missing, judge as insufficient (false), and list the missing information.
3. The reasoning should be concise and clear.
4. The missing_information should only be filled when insufficient, otherwise empty array.
```
Multi-query generation prompt, verbatim (its inputs are original query, original question, retrieved content and missing information):
```text
Please generate 2-3 complementary queries to help find the missing information. These queries should:
1. Focus on different missing information points.
2. Use different expressions.
3. Avoid being identical to the original query.
4. Remain concise and clear.

Output format (JSON):
{
    "reasoning": "Explanation of query generation strategy",
    "questions": [
        {"question": "Improved question 1", "query": "Improved query 1"},
        ...
    ]
}
Requirements:
1. Questions array contains 1-3 questions and corresponding queries.
2. Each question length is between 5-200 characters.
3. Each query length is between 1-5 keywords.
4. Each query MUST be in the same language as the retrieved content in.
5. DO NOT generate question and query that is similar to the original query.
6. Reasoning explains the generation strategy.
```
Control flow of `_research(depth)`:
1. If `depth==0` or the request was cancelled, stop.
2. Emit `callback("Searching by `q`...")`.
3. Retrieve.
4. Merge chunks into a shared map, dedup by `chunk_id`.
5. Run the sufficiency check. If the check errors, treat the result as insufficient.
6. If sufficient, return.
7. Otherwise generate sub-queries and emit the callback `"Next step is to search for the following questions: …"`.
8. Run goroutines on `depth-1` and join the results. A failing child becomes a string result; it does not abort.

**Port:** this is the "reflection" step for our document-retrieval tool. The *same* `{is_sufficient, missing_information}` contract also works after a semantic-query step: "does this result answer the question? What is missing?" That gives a cheap, uniform replan trigger.

### 5c. Agent canvas DSL
The template (`internal/agent/templates/deep_research.json`) is a JSON DSL: `{components: {"<Type>:<Name>": {obj: {component_name, params}, downstream: [...], upstream: [...]}}, globals, graph, history, path, retrieval}`. The component set (`internal/agent/component/`) is `begin, agent (with tools = sub-agents/MCP), llm, categorize, switch, loop, parallel, retrieval, exesql, invoke, message, fillup/userfillup (ask user), variable_assigner/aggregator, data/list operations, docs_generator`. An `Agent` component carries `sys_prompt, max_rounds, max_retries, exception_method/exception_goto/exception_default_value, tools[]`, which is error routing per node.

The Deep Research lead-agent prompt classifies the query before planning, into "Depth-first", "Breadth-first" or "Straightforward". It then deploys sub-agents.

**Port:** we only need the *plan* to be a small DSL like this one: node = `{id, kind, params, depends_on, on_error: retry|skip|goto|default}`. The per-node `exception_*` fields are a good shape for "what happens when a step fails" in a statically validated plan DAG.

### 5d. Related questions (`internal/rag/prompts/related_question.md`)
"generate **5-10 related questions** … help **expand the search query scope** … each alternative should be **unique** but still **relevant** … ensure that each question **broadens** the search angle, **not narrows** it … If you cannot generate any relevant alternatives, do **not** return any questions." These are used for retrieval expansion. WrenAI's recommendation prompt (4d) is the better fit for user-facing follow-ups.

---

## 6. MemoriLabs/Memori (SQL-backed memory: facts, triples, process attributes, recall injection)

### 6a. Memory record schema (`memori/storage/migrations/_postgresql.py`)
```sql
memori_entity(id BIGSERIAL PK, uuid, external_id VARCHAR(100) UNIQUE, date_created, date_updated)   -- the user / org
memori_process(id, uuid, external_id UNIQUE, ...)                                                   -- the agent / app
memori_session(id, uuid, entity_id FK, process_id FK, ...)
memori_conversation(id, uuid, session_id FK UNIQUE, summary TEXT, ...)
memori_conversation_message(id, uuid, conversation_id FK, role, type, content TEXT, ...)
memori_entity_fact(
    id, uuid, entity_id FK, content TEXT NOT NULL, content_embedding BYTEA NOT NULL,
    num_times BIGINT NOT NULL,            -- reinforcement count
    date_last_time TIMESTAMP NOT NULL,    -- recency
    uniq CHAR(64) NOT NULL,               -- sha256 of normalized content -> dedup
    CONSTRAINT uk_memori_entity_fact_entity_id_uniq UNIQUE (entity_id, uniq))
memori_process_attribute(id, uuid, process_id FK, content, num_times, date_last_time, uniq, UNIQUE(process_id, uniq))
memori_subject(id, uuid, name, type, uniq UNIQUE) / memori_predicate(id, uuid, content, uniq UNIQUE) / memori_object(id, uuid, name, type, uniq UNIQUE)
memori_knowledge_graph(id, uuid, entity_id, subject_id, predicate_id, object_id, num_times, date_last_time,
    UNIQUE (entity_id, subject_id, predicate_id, object_id))
memori_entity_fact_mention(id, uuid, entity_id, fact_id, conversation_id,
    UNIQUE (entity_id, fact_id, conversation_id))   -- provenance: which conversation stated the fact
```
The extraction payload that comes back from the hosted augmenter, as parsed in `memori/memory/_struct.py`:
```python
{"conversation": {"summary": str},
 "entity":  {"facts": [str], "fact_embeddings": [[float]],
             "triples": [{"subject": {"name","type"}, "predicate": str, "object": {"name","type"}, "content"?: str}]},
 "process": {"attributes": [str]}}
# a triple without "content" becomes fact text f"{subject_name} {predicate} {object_name}"
```
The key design choice is the split into three subjects: the **entity** (the user), the **process** (the agent), and the conversation **summary**. Writes are idempotent upserts on `uniq` that bump `num_times` and `date_last_time`, and there is mention provenance per conversation.

### 6b. Recall: hybrid dense + BM25 with a short-query lexical boost, a threshold, then prompt injection
`memori/search/_core.py` and `_lexical.py`:
```python
def _candidate_limit(*, limit, total_embeddings, query_text):
    if query_text:
        return max(limit, min(total_embeddings, max(limit * 10, 50)))
    return int(limit)

rank_score = w_cos * cosine(fid) + w_lex * bm25_normalized(fid)     # BM25 k1=1.2, b=0.75, normalized by max
def dense_lexical_weights(*, query_text):
    """We bias toward lexical matching for very short queries where exact terms are usually high-signal."""
    w_lex = 0.15 (env MEMORI_RECALL_LEX_WEIGHT); if len(tokens) <= 2: w_lex = 0.30
    w_lex = max(0.05, min(0.40, w_lex)); return (1.0 - w_lex, w_lex)
```
Defaults (`memori/_config.py`): `recall_facts_limit = 5`, `recall_relevance_threshold = 0.1`, `recall_embeddings_limit = 1000`.

Injection (`memori/llm/pipelines/recall_injection.py`) appends to the system prompt:
```python
fact_lines = [f"- {content}. Stated at {ts}" ...]
context_body = "Relevant context about the user:\n" + "\n".join(fact_lines)
if summary_lines:
    context_body += "\n\n## Summaries\n\n" + "\n\n".join(summary_lines)
recall_context = (
    "\n\n<memori_context>\n"
    "Only use the relevant context if it is relevant to the user's query. "
    + context_body
    + "\n</memori_context>"
)
```
Injected messages are counted in `_memori_injected_count` and stripped before the conversation is persisted, so memory never re-feeds itself.

**Port:** take the tables nearly verbatim into our memory schema, using `pgvector` instead of BYTEA:
- entity = user, process = data space or agent, `fact_mention` = provenance;
- `num_times` and `date_last_time` give recency and frequency ranking;
- the `"Stated at {ts}"` suffix fits our point-in-time invariant;
- stripping injected context before persisting is a must-copy.

Combine this with Data-Analysis-Agent's typed extraction prompt (1c), because Memori's extractor is closed.

---

## 7. Not cloned (too large): one idea each

**OpenMetadata.** Its entity model makes `Table`, `Column`, `GlossaryTerm`, `Metric`, `Classification/Tag`, `DataProduct` and `Domain` first-class, versioned entities with owners. Lineage is a column-level edge graph (`fromEntity → toEntity`, with `columnsLineage` and the SQL that produced it). The ideas that matter:
- (a) glossary terms that have `synonyms`, `relatedTerms` and a reviewer-approval workflow. This maps onto our semantic-layer synonyms and "human decides";
- (b) lineage edges so an answer block can show "this metric ← these tables";
- (c) a `changeDescription {fieldsAdded, fieldsUpdated, fieldsDeleted, previousVersion}` diff on every entity version, for OCC and audit.

**Airbyte.** Each connector declares a JSON-Schema `connectionSpecification` in `spec.json`/`spec.yaml`. Secret fields are marked `airbyte_secret: true`, and the UI form is generated from that schema. The protocol is a small set of messages: `SPEC → CHECK → DISCOVER (catalog of streams with json_schema, supported sync modes, cursor/primary keys) → READ` (RECORD / STATE / LOG / TRACE). The idea that matters: describe every warehouse or document source with a JSON-Schema spec plus a `check` method, so our connector UI and secret handling (OpenBao refs instead of `airbyte_secret` values) come from one declaration. DISCOVER's catalog is a ready shape for schema introspection that feeds the semantic layer.

**databend.** Not applicable (see the caveats at the top).

---

## 8. Cross-repo synthesis for our redesign (short)

1. **Plan representation.** Use a small typed DAG, in the style of ragflow's DSL, that is statically validated:
   - nodes are `{id, kind ∈ {semantic_query, sql, python, chart, retrieve, ask_user}, params, depends_on, on_error}`;
   - which kinds are legal follows DAA-style stage rules, e.g. no `chart` without an upstream data node.
2. **Executor loop.** Use RD-Agent's step loop: `propose/plan → execute → feedback → record`.
   - Exceptions become feedback with `decision=false`.
   - Parallel branches run under a semaphore (gpt-researcher), and a level where every branch failed stops the loop.
   - Revise loops are bounded and force-accept at the ceiling, with a trust downgrade (gpt-researcher).
3. **Reflection contracts.** Use ragflow's `{is_sufficient, reasoning, missing_information}` after every data or retrieval step, and RD-Agent's `{observations, hypothesis_evaluation, decision, new_hypothesis}` for diagnostic "why" questions. The replanner reads the rendered trace (the RD-Agent template).
4. **Answer assembly.** Pick a block type per renderer:
   - `markdown`/`insight` use WrenAI's sql_answer rules plus `[ID:n]` citations (ragflow) over query runs and chunks;
   - `chart` uses WrenAI's typed Vega-Lite (Pydantic union → json_schema response_format → jsonschema validation → data stripped and bound client-side by `data_ref`), with chart adjustment as a block-level action;
   - `follow_ups` uses WrenAI's recommendation prompt, validated against the semantic layer;
   - `plan`/`progress` use WrenAI's status enum plus the numbered reasoning plan.
5. **Gates before publish.** Run DAA's numeric provenance audit first, which is deterministic, then an LLM fact-checker (gpt-researcher) only if needed.
6. **Memory.** Use Memori's SQL schema (entity/process/conversation summary, `uniq` dedup, `num_times`/`date_last_time`, mention provenance, hybrid recall with threshold, injected-context stripping) together with DAA's typed extraction and consolidation prompts (user/feedback/project/reference, scope rules, "no raw query values", daily consolidation). Metric definitions the user states become *proposals* to the semantic layer, not silent memory.
