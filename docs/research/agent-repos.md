# Other repos: ideas to port into a dynamic Python data agent

> Licences: Data-Analysis-Agent is CC BY-NC 4.0 with a commercial-use restriction and WrenAI's legacy `wren-ai-service` is AGPL-3.0: both are described in our words only. Quoted material from gpt-researcher (Apache-2.0), RD-Agent (MIT), ragflow (Apache-2.0) and Memori (Apache-2.0) is © its authors under those licences, from the commits named below.

Scope: the planner, the graph/loop executor with replanning and reflection, persistent memory, and the typed-block UI stream.
Clones are shallow (`--depth 1`) and live under `research2/repos/`. Clone date is 2026-11-02.

**Source caveats**
- **WrenAI:** `main` no longer has `wren-ai-service`. The repo was restructured into `core/`, `sdk/` and `skills/`. The Python pipelines quoted below come from a sparse clone of the branch `dependabot/pip/wren-ai-service/requests-2.33.0`, which still carries `wren-ai-service/src`. That clone is `repos/WrenAI-legacy/`.
- **ragflow:** the backend has been ported to Go (`internal/`). The prompts are still plain `.md` files or Go string constants, so they port to Python unchanged.
- **Memori:** memory extraction (they call it "augmentation") runs as a hosted call: `api.post("cloud/augmentation", ...)`. The open-source repo has no extraction prompt. It does have the record schema, the storage layout, the recall ranking and the prompt-injection format.
- **databend:** there is no AI analyst or agent piece. It has only a `VECTOR` type, vector-index tests and distance functions (`src/common/vector`, `src/query/functions/src/scalars/vector.rs`). I skipped it.

---

## 1. Zafer-Liu/Data-Analysis-Agent (Flask + SSE, tool-calling agent)

**Licence: CC BY-NC 4.0, and the author requires a written licence for any commercial use. Study
only: nothing of its code or prompts is quoted here or used in the product.** Upstream:
https://github.com/Zafer-Liu/Data-Analysis-Agent. The ideas, in our own words:

- **A numeric provenance gate.** Before an answer that rests on tool results goes out, every number
  in it is matched against the numbers those tools returned; an answer with an unmatched number is
  not sent as is. (Our own form: `agentic/verify.py`, tags the server fills, sentences with a stray
  number dropped.)
- **Tools narrowed by stage.** The stage of the analysis is worked out from which tools have
  already run, and only the tools that fit the next stage are offered to the model.
- **Long-term memory as typed, scoped records.** Records of a few kinds (the person's own
  preferences and corrections, facts about a workspace, pointers to resources), each scoped to the
  person or to the workspace, extracted from the conversation after a turn with a short list of
  things never to store, and merged now and then into fewer, dated records. (Our own form:
  `agentic/memory.py`, written independently.)
- **Typed job events** for the progress stream, and **a chart registry** in which each chart type
  declares which column roles it needs, so a model first picks a type and then maps columns to
  roles, which a program can check. (Our own form: `agentic/blocks.py` CHART_ROLES.)

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

**Licence: the legacy branch that holds `wren-ai-service` is AGPL-3.0. Study only: nothing of its
prompts, schemas or code is quoted here or used in the product.** (WrenAI's current `core/`,
`sdk/` and `skills/` are Apache-2.0, its `docs/` CC BY 4.0.) Upstream:
https://github.com/Canner/WrenAI, `wren-ai-service/src/pipelines/` on the legacy branch. The ideas,
in our own words:

- **Chart generation as a constrained spec.** The model sees a small sample of the result (a few
  rows, a few distinct values per column) and the question, and returns a chart type, the columns
  it maps to each channel and a reason; the spec is validated against the charting library's
  schema, and the real rows are bound by the client, never written by the model. On a validation
  failure the chart is simply left out.
- **Choosing the chart type** follows common visualisation practice (a line for change over time,
  bars to compare categories, a pie only for a few parts of one whole).
- **Chart adjustment.** The person changes the chart (type, axes), and the chart is regenerated
  from the same result instead of re-asking the question.
- **The SQL answer** is written from the question, the SQL and its result rows, in the person's
  language.
- **Follow-up questions** grouped by the kind of exploration they make (go deeper into a cut,
  roll up, filter to a member, look at the trend, compare).
- **Intent routing** that also rewrites a follow-up as a standalone question, and a named status
  sequence for the progress of a turn.
- **SQL correction** in two passes: the model first says why a statement failed, then rewrites it
  with that reason. (Our own form: `sqlgen/generate.diagnose_and_repair`, written independently.)

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
"generate **5-10 related questions** … help **expand the search query scope** … each alternative should be **unique** but still **relevant** … ensure that each question **broadens** the search angle, **not narrows** it … If you cannot generate any relevant alternatives, do **not** return any questions." These are used for retrieval expansion. The idea of follow-ups grouped by kind of exploration (4d, AGPL: idea only, our own wording) fits user-facing follow-ups better.

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

Combine this with typed, scoped extraction written by us (the idea in 1c; that repo's licence forbids commercial use), because Memori's extractor is closed.

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
   - which kinds are legal follows stage rules (an idea in 1b, written by us), e.g. no `chart` without an upstream data node.
2. **Executor loop.** Use RD-Agent's step loop: `propose/plan → execute → feedback → record`.
   - Exceptions become feedback with `decision=false`.
   - Parallel branches run under a semaphore (gpt-researcher), and a level where every branch failed stops the loop.
   - Revise loops are bounded and force-accept at the ceiling, with a trust downgrade (gpt-researcher).
3. **Reflection contracts.** Use ragflow's `{is_sufficient, reasoning, missing_information}` after every data or retrieval step, and RD-Agent's `{observations, hypothesis_evaluation, decision, new_hypothesis}` for diagnostic "why" questions. The replanner reads the rendered trace (the RD-Agent template).
4. **Answer assembly.** Pick a block type per renderer:
   - `markdown`/`insight` follow our own answer rules (the idea of 4c) plus `[ID:n]` citations (ragflow) over query runs and chunks;
   - `chart` is a typed chart spec of our own, validated, with the data bound by the server or client, never written by the model (the idea of 4a), with chart adjustment as a block-level action;
   - `follow_ups` are written by our own prompt (the idea of 4d), validated against the semantic layer;
   - `plan`/`progress` use a status sequence of our own plus the numbered reasoning plan.
5. **Gates before publish.** Run a numeric provenance check first (the idea of 1a, our own code), which is deterministic, then an LLM fact-checker (gpt-researcher) only if needed.
6. **Memory.** Use Memori's SQL schema (entity/process/conversation summary, `uniq` dedup, `num_times`/`date_last_time`, mention provenance, hybrid recall with threshold, injected-context stripping) together with typed extraction and consolidation of our own (the ideas of 1c: kinds, scope, no raw query values, periodic merging). Metric definitions the user states become *proposals* to the semantic layer, not silent memory.
