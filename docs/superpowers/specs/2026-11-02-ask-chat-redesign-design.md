# Ask chat redesign: a visible step timeline and multi-block answers

Date: 2026-11-02. Status: approved in chat by the user ("Yes, please refactor all code for chat ui
and feature in this chat"), after a playback of the Holistics AI screens they supplied.

## Why

Using the Ask screen (Phân tích › Hỏi đáp), the user found three things wrong:

1. Every answer comes back in one shape: a paragraph, then a chart or table when there is one.
2. The plan the agent made is not shown.
3. What each step of that plan actually did is not shown.

They want the screen to work like Holistics AI (demo4.holistics.io, screenshots in the
conversation). This is a big update that does not have to follow the current Ask design or the
Figma frame "P01 · Hỏi đáp"; the current screen is replaced, not extended.

What the Holistics screens show, and what we take:

- An empty chat is a centred greeting, the composer, a few starter questions and the recent chats.
- Each answer opens with a collapsible line, "Brewed for 224s ▾" (ours: "Đã làm trong 224 giây").
  Under it is a timeline: one line per step, a check mark, a bold action verb and its object
  ("**Write query on** [Aql Ecommerce Ds] **for:** Monthly sales and cost"), and a `>` that opens
  the step's detail.
- The answer is several blocks in a row: formatted text, a chart/map/table card with a toolbar
  (Explore, View, Filters n, …), and a "Watchout:" note on the data's limits.
- Under the answer: the time and the actions retry, like, dislike, copy.
- The composer takes `@` to pin context (a dataset chip in the box) and `/` for skills.
- A left panel with New conversation, Search chat and Recents.

## Goals

- G1. Every turn shows its steps, live while it runs and the same after reload, each step with
  its detail (see "Step contract").
- G2. Every path the worker answers by emits steps: governed metric, exploratory SQL, documents,
  approved (verified) query, clarification, refusal. A path that emits none is a defect.
- G3. An answer is drawn from its blocks (`GET /v1/answers/{id}` `blocks[]`), so one answer can be
  text + KPI cards + chart + table + SQL + citations + caveats, in the order the server gives.
- G4. The new layout: in-page conversation panel, centred empty state, message thread, composer
  with `@` context and `/` commands.
- G5. Nothing the current screen does for trust is lost: trust label, receipt (Biên nhận), report
  (Báo sai), save to knowledge, save to session, export, clarification card, refusal card with
  its next action, the decomposition proposal ("why"), follow-up questions, cancel (Dừng),
  reopening a conversation by URL `/analysis/ask/<conversation_id>`.

## Non-goals

- No new agent behaviour. The peer session's planned tool loop (search_metric / query_metric /
  compare / decompose / ask_user) is not part of this; it will emit steps in the contract below.
- No dashboard building from chat, no file or image attachments, no map charts.
- No change to how answers are computed, to permissions or to the trust rules.

## Anti-goals (a failure even if it works)

- A step detail that shows data the asker may not see: masked columns, rows outside their row
  scope, another person's question. Step details follow the same rules as the answer.
- A step detail that shows a prompt, a model's raw output or a provider's error text. Model calls
  show model, tokens, latency only (the turn trace's rule).
- Steps that exist only while the page is open: a reload must show the same timeline.
- A timeline that claims work that did not happen (e.g. "visualize: done" published before the
  chart is chosen, as today).

## Step contract (shared with the peer session)

One shape, used live (stream event) and persisted (agent_step):

```
activity event, kind "step":
{
  "kind": "step",
  "step_id": "s3",            // unique in the turn, stable between "running" and its end
  "seq": 3,                    // order in the turn
  "action": "query",           // vocabulary below
  "title": "Truy vấn",         // the bold verb, Vietnamese
  "target": "AdventureWorksDW cho: Doanh thu Internet năm 2013",  // the object, plain text
  "chips": [{"kind": "source", "label": "AdventureWorksDW"}],    // optional, drawn as chips
  "status": "running" | "done" | "failed" | "skipped",
  "started_at": "…ISO…", "duration_ms": 123 | null,
  "detail": { … per action … } | null   // only on the end event
}
```

Action vocabulary and its `detail`:

| action | title | detail |
|---|---|---|
| `understand` | Hiểu câu hỏi | `reading`: chips (metric, period, dimensions, filters, each with source rule/llm/inherited), `confidence`, `unmapped` phrases |
| `choose_metric` | Chọn chỉ số | `metric` {code, name, version, certified}, `datasets` [names], `source` name, `why` (rule / synonym / model) |
| `ask_user` | Hỏi lại bạn | `question`, `options`; the user's reply when there is one |
| `query` | Truy vấn | `sql` (as executed), `row_count`, `truncated`, `query_run_id`, `columns` |
| `write_sql` | Viết SQL thăm dò | `candidates`, `agreeing`, `repaired` (sqlgen vote), `sql` chosen |
| `search_documents` | Tìm trong tài liệu | `collections`, `hits` [{title, page}], `mode` hybrid/keyword |
| `use_verified` | Dùng truy vấn đã duyệt | `name`, `query_run_id` |
| `validate` | Kiểm tra kết quả | `checks` [{name, passed, detail}] from number_check |
| `visualize` | Chọn cách hiển thị | `chart_type`, `reason` |
| `analyze` | Phân tích | `skill`, `drivers` (why/decomposition) |
| `write_answer` | Viết câu trả lời | `model`, `input_tokens`, `output_tokens`, `latency_ms` (no text) |
| `model_call` | Gọi mô hình | as write_answer; drawn nested under the step it belongs to, not as a top-level line |

Permissions: `detail` is built by the worker from what the answer itself is allowed to show; SQL is
the executed SQL the asker can already open under "Cách tính" today; table previews are not put in
step details (the answer's table block carries rows).

Persistence: the worker writes each step to `agent.agent_step` (turn_trace) with
`output = detail` and `name = action`, plus `title`, `target`, `chips` in `output._view`.
Replay: a new read `GET /v1/questions/{id}/steps` returns the persisted steps of the question's
latest turn, for the asker only (the same ownership rule as the question).

## Backend changes

- B1. turn_trace: `step()` takes `action`, `title`, `target`, `chips`, `detail`; publishes the
  `activity/step` event itself (one place, live and persisted from the same call).
- B2. questions.py: `_analyst_step` call sites replaced by step calls with real detail at the
  moment the work is done (understand after `_understand_and_plan`, choose_metric with the
  compiled metric, query with the outcome, validate with number_check after `verify`, visualize
  after `chart_spec_of`, write_answer around the narrator). Steps for the other paths: sqlgen
  (`write_sql`, `query`), documents (`search_documents`, `write_answer`), verified
  (`use_verified`, `query`), clarification (`ask_user`), refusal (the step that refused, failed).
- B3. `GET /v1/questions/{id}/steps` in the qa module, reading agent_run/agent_step through an
  agent-module public function; catalogue A2/A3 rows, tab4, counts, api-types.
- B4. The answer's `blocks[]`: add `caveats` (best-effort notes, trust warnings, withheld figures)
  and put the executed SQL text in the `sql` block (it is already readable by the asker).

## Frontend changes

- F1. Replace `features/ask` UI: `AskDataPage` (layout), `ConversationPanel` (new conversation,
  search, recents from `GET /v1/conversations`), `EmptyChat`, `Thread`, `UserMessage`,
  `AssistantTurn` (= `StepTimeline` + `AnswerBlocks` + `TurnFooter`), `Composer` with `@`/`/`.
- F2. `StepTimeline`: the "Đã làm trong …" header (open while running, closed when done), one row
  per step, row expands to a detail view per action (SQL in a code block with copy, reading as
  chips, checks as a list, model calls nested).
- F3. `AnswerBlocks`: a registry by block type; chart/table card with toolbar (Khám phá → Explore
  page with the query, Xem → full-size dialog, Bộ lọc n, … → export CSV, save to session).
- F4. `useQuestionStream` rewritten around the step contract; hydrate from `/steps` on reopen.
- F5. Keep and restyle: RefusalCard, ClarificationCard, DecompositionCard, ChartRenderer,
  DataGrid, MarkdownContent, ReportDialog, ExportDialog, receipt drawer, save dialogs. Remove what
  the timeline replaces: AgentProgress, EvidencePanel side panel, InterpretationCheck, EvidenceStrip,
  answerLayout (if blocks cover it).
- F6. Tests: vitest for the timeline (live + reopen), each block, composer `@`/`/`, and every G5
  action; the old tests are rewritten, not deleted without a replacement.

## Constraints

- Repo rules (CLAUDE.md): no new `.sh`, catalogue gate for the new route, `make api-types`,
  `make frontend-lint` clean, `make test-unit`/`test-integration` for touched suites, docs in the
  same commit (docs/v3/05-ui-v3.md Ask section, 06-api-v1.md).
- Another session works on the worker (`_understand_and_plan` no-metric path, suggestions, why):
  they do not edit `features/ask/**`, `_run` or `_analyst_step`; coordinate before touching their
  areas.
- Vietnamese UI copy, the existing design-system tokens and components.

## Calls made by the designer (not asked of the user)

- C1. Persistence stays one write at the turn's end. A turn reopened while it runs replays the
  stream from its start (Redis `sse:<id>` from `0-0`, which the stream route already does); a
  finished turn reads `/steps`. A step still open when the turn ends is stored as it ended
  (turn_trace already does this).
- C2. Model calls nest under the step open when they were made: turn_trace keeps the current
  step and records its `step_id` as `parent` in the model call's `output._view`. No migration.
- C3. `@` pins a Data Space metric or dimension from the explore model as a chip; on send it is
  written into the question text (the resolver reads names), like today's `/` commands. No new
  request field, no new agent behaviour. `/` lists today's chat commands (`/chart`, `/sql`,
  `/data`, `/report`, `/new`, `/help`).
- C4. `questions.py`: this work adds the step calls in `_run`, and in the sqlgen, documents and
  verified paths at the point each returns (the peer session is told before those lines are
  touched). The peer's tool loop emits steps through `turn_trace.step()` in this contract.
- C5. Turns recorded before this change, or with no steps, show one muted line "Lượt này chưa ghi
  chi tiết các bước." instead of a timeline.
- C6. Footer: retry asks the same text again as a new question in the conversation; thumbs-down
  opens Báo sai (the existing feedback route); thumbs-up is not drawn (no backend for it); copy
  copies the answer text.
- C7. "Bộ lọc n" shows the filters the answer applied, read-only; changing them is a follow-up
  question, so no un-governed result appears inside an answer.
- C8. "Khám phá" opens the Explore page with the answer's executed SQL (the query run) when
  Explore can take it as input; if it cannot, the button opens the SQL and its run in a dialog
  with "Mở trong SQL editor" left out. When the answer has no query run the button is not drawn.

## Left to the builder

- Exact visual styling within the design system; icons from the existing set.
- Whether `/steps` returns only the latest turn or all turns of a question (latest is enough).
- Splitting the work into commits (backend contract first, then frontend).
