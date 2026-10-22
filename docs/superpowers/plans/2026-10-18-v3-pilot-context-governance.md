# V3 Pilot Context & Governance Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to execute this plan task-by-task, with `superpowers:verification-before-completion` before claiming completion.

**Goal:** Đưa thiết kế V3 từ trạng thái “đủ khung API/DDL” sang một pilot có thể chứng minh end-to-end cho lớp ngữ cảnh, quyền dữ liệu, truy vấn theo thời điểm và MCP; đồng thời làm cho tài liệu phản ánh đúng phần đang làm và phần đã hoãn.

**Architecture:** Giữ API v1 và Kong/Keycloak hiện đang chạy; Postgres là nguồn sự thật cho semantic/governance, các index/graph chỉ là projection; kiểm quyền và Data Space xảy ra trước bước resolve/generate; agent chỉ thực thi plan đã được kiểm tra; câu trả lời số phải đi qua result-set/provenance/trust contract. Không mở thêm service chỉ để nâng số endpoint.

**Tech Stack:** Python 3.11 trong service images, FastAPI, SQLAlchemy/Alembic, Postgres/RLS, Redis/Kafka/outbox, worker-agent, React/TypeScript, pytest/Vitest, generated V3 design checklist.

**Scope evidence:** `docs/v3/00-product-strategy.md`, `docs/v3/01-agent-core-and-ui.md`, `docs/v3/03-r0-technical-design.md`, `docs/v3/04-backlog.md`, `docs/v3/10-design-checklist.md`, `docs/v3/checklist/design-snapshot.json`.

## Global constraints

- Không sửa trực tiếp `docs/v3/10-design-checklist.md` hoặc `design-snapshot.json`; sinh lại bằng `make design-checklist` sau khi sửa mã/test/spec.
- Mỗi test hành vi phải nhắc đúng mã `AC-*`; test schema/DDL không được tính là test hành vi nếu không kiểm tra outcome.
- Mọi đường đọc/resolve/generate phải giữ tenant/Data Space scope trước khi đưa dữ liệu hoặc schema vào prompt.
- Không dùng test giả, mock-only để đóng acceptance criteria có nhãn API/CSDL/E2E; các tiêu chí đó cần Postgres/test image hoặc deployed e2e tương ứng.
- Không triển khai các phần đã bị hoãn trong chiến lược chỉ để tăng coverage: Publisher/data product bán ngoài, lớp lãnh đạo riêng, forecast giá xăng, proactive crawl, industry package đầy đủ, Temporal migration.
- Bảo toàn dirty worktree hiện có; không reset, checkout hoặc xoá thay đổi không thuộc phạm vi.
- Deploy chỉ qua Ansible và chỉ sau khi có commit; trong giai đoạn local verification dùng test image/kind dev path được repo hỗ trợ.

## Phase 0 — Freeze the baseline and correct the design scope

### Task 0.1: Reproduce the audit from the current tree

**Files:** `tests/design_checklist.py`, `docs/v3/checklist/`, `docs/v3/10-design-checklist.md`, `Makefile`.

1. Chạy `make design-checklist` và ghi lại số liệu sinh ra từ tree hiện tại.
2. Chạy `make design-audit` để phân biệt checklist stale, thiếu test marker và mâu thuẫn status.
3. So sánh số liệu trong tài liệu người dùng đưa với file sinh hiện tại; ghi rõ chênh lệch (không sửa bằng tay).
4. Lưu baseline vào phần audit trong tài liệu triển khai hoặc log CI, không commit log lớn vào repo.

**Verification:** generated checklist không stale; audit trả về danh sách lỗi cụ thể, không chỉ một tổng số.

### Task 0.2: Chốt phạm vi pilot và ADR mâu thuẫn

**Files:** `docs/v3/00-product-strategy.md`, `docs/v3/02-market-solution.md`, `docs/v3/04-backlog.md`, `docs/v3/06-api-v1.md`, `docs/v3/08-legacy-cutover.md`, ADR source files tìm được bằng `rg -n "ADR-30|ADR-04|ADR-03|ADR-42|ADR-44" docs`.

1. Ghi pilot chính thức là 15–25 metric cho các hành trình bán hàng–biên–công nợ trên Data Space ngân hàng demo.
2. Đánh dấu Publisher, leadership, fuel forecast, proactive crawl và industry package là deferred/out-of-pilot; các endpoint giữ compatibility nhưng không nằm trong release gate.
3. Chốt gateway theo runtime hiện tại là Kong; ADR-30 phải nói rõ APISIX là phương án chưa chọn hoặc ghi lại quyết định chuyển đổi nếu có bằng chứng triển khai.
4. Chốt Postgres là source of truth, Neo4j là projection/index cho V3 hiện tại; xoá câu chữ mâu thuẫn “bỏ graph ngay” hoặc “Neo4j là nền bắt buộc”.
5. Chuyển các UC đã được chứng minh khỏi trạng thái `todo` chỉ khi checklist có evidence; không đổi status thủ công để làm đẹp dashboard.

**Verification:** tài liệu chiến lược, ADR và checklist không còn mô tả hai runtime gateway hoặc hai quyết định graph trái nhau; backlog có một pilot gate duy nhất.

## Phase 1 — Close the Context Layer (P07)

### Task 1.1: Metric lifecycle and governed formula

**Files:** `backend/services/api/modules/context/`, `backend/libs/dataagent_db/models/context.py`, Alembic context migrations, `tests/integration/api/`, `tests/unit/api/`, relevant frontend context/metric screens.

Implement and test the outcomes for `AC-07-01-01` through `AC-07-01-08`:

- metric draft → preview → approval → certified/published version;
- package-owned formula is immutable and customer override is separate;
- materiality reason is required before approval;
- compile-time column permission failure names the denied column;
- denominator-zero returns null plus warning;
- metric code is immutable, formula changes create a version with `valid_from`;
- published-report metric cannot be deleted, only retired;
- non-additive ratios compile from numerator/denominator, never sum ratios.

Add the smallest API/UI needed to exercise these outcomes; do not build the full Publisher product.

**Verification:** integration tests run against real DDL and the generated checklist shows all eight required criteria with test references.

### Task 1.2: Synonyms, relationships, examples, glossary and ambiguity

**Files:** context routes/schemas/models, worker-agent resolver/compiler, `tests/integration/api/test_context_reads.py`, `tests/integration/api/test_semantic_yaml.py`, `tests/unit/worker_agent/test_understand.py`, frontend context screens.

Implement and test `AC-07-02-01` through `AC-07-02-05` and `AC-07-09-01` through `AC-07-09-05`:

- validated synonym/example proposal with eval before activation;
- relationship and example-query checks fail closed on wrong results;
- unresolved terms accumulate evidence and produce a review item;
- multi-meaning glossary entries do not overwrite each other;
- stale examples leave active context;
- default meaning, user preference, and Data Space meaning are distinct;
- low-confidence interpretation creates clarification before query execution.

**Verification:** tests prove both isolation of meanings and “no query before clarification”; prompt/context snapshots contain only active, permitted entries.

### Task 1.3: Context evaluation and knowledge-page governance

**Files:** eval models/routes, knowledge/context routes and workers, `tests/integration/api/test_inbox_eval.py`, new `tests/integration/api/test_context_eval.py`, new worker tests, relevant frontend screens.

Implement the minimum pilot slice for `AC-07-06-01` through `AC-07-06-06` and `AC-07-10-01` through `AC-07-10-09`:

- eval set/run/result with interpretation, number and behavior scores;
- feedback-to-eval-item conversion;
- reason required for answer changes;
- publish gate when evaluation regresses;
- Data Space opening blocked below threshold and notifications on regression;
- governed knowledge-page versioning, review, stale/source-certification checks and access filtering.

Use the banking demo dataset and a small deterministic pilot eval set first; keep the 120-case acceptance shape as the release gate, not as a mock-only unit test.

**Verification:** a real eval run persists results and can block a publish/open action; unauthorized knowledge pages return indistinguishable 404.

## Phase 2 — Query time semantics and data authorization

### Task 2.1: As-of and period-close behavior

**Files:** period routes/models/services, worker-agent question/compiler/resolver, `tests/integration/api/test_period.py`, new `tests/integration/worker_agent/test_as_of_queries.py`, frontend ask/period UI.

Implement and test `AC-10-01-01` through `AC-10-01-07`:

- persist `as_of`, `knowledge_mode` and period-close reference;
- return submitted and current values side by side when mode is unspecified;
- enforce record availability cutoff for `knowledge_at`;
- fail with earliest available date and no fabricated number;
- label current-data answers as current, never as historical;
- measure the concurrent-query p95 gate separately from correctness tests.

**Verification:** a banking fixture with a retroactive record proves different answers for period-close, `at`, and current modes, with provenance visible in the answer receipt.

### Task 2.2: Permission-before-generation and Data Space isolation

**Files:** `backend/services/api/modules/governance/`, context/data-space routes, worker-agent planning/resolution path, RLS/session helpers, `tests/integration/api/`, `tests/integration/worker_agent/`.

Implement and test `AC-11-02-01` through `AC-11-02-09`:

- simulate and activate row policy with audit metadata;
- backtest impact before activation;
- column masking/redaction;
- notify affected reports before policy effect;
- deny policy changes that weaken higher-level rules;
- enforce Data Space membership and source entitlement before schema/metric retrieval;
- distinguish forbidden from no-data without leaking table/column names;
- verify worker and agent paths set tenant/Data Space scope on every transaction.

**Verification:** cross-tenant and cross-Data-Space integration tests run with `app_user`; the prompt trace proves forbidden schema never entered the model context.

## Phase 3 — MCP contract and agent execution evidence

### Task 3.1: Connect MCP allow-listed calls to the current worker-agent plan

**Files:** `backend/services/worker_agent/tools/mcp.py` or existing MCP owner, planner/validator, API integration routes/schemas, `tests/unit/worker_agent/test_mcp.py`, `tests/integration/worker_agent/`.

Complete `AC-12-02-01` through `AC-12-02-07`:

- discover tools through Streamable HTTP with a tenant-scoped registry;
- pass only allow-listed tools from a validated plan;
- resolve bearer token transiently from secret reference;
- reject unknown tools with structured error;
- persist provenance without token leakage;
- handle unavailable tool without silently falling back to an unapproved data source;
- show the tool call/result in the agent run and answer receipt.

**Verification:** in-process MCP server integration test covers discovery → validated plan → call → result-set/provenance; grep-based secret-leak test covers state, cache and logs.

### Task 3.2: Close the first question journey

**Files:** `backend/services/worker_agent/questions.py`, resolver/compiler/narrate, frontend ask/agent screens, `tests/integration/worker_agent/test_pilot_reconciliation.py`, `tests/integration/test_agent_screens_e2e.py`.

Complete the remaining required criteria for `UC-01-01` and the pilot journey:

- clarify unresolved terms before execution;
- choose the governed metric and time mode;
- enforce permissions before planning;
- execute structured result set with provenance/trust label;
- expose source, formula, time mode, warning and receipt in UI;
- support follow-up and drill-down without losing scope.

**Verification:** deployed dev e2e runs the banking reconciliation question, follow-up, denied-data case and MCP-backed case; screenshots/trace IDs are captured in the test report.

## Phase 4 — Evidence, status and release gate

### Task 4.1: Add AC markers and update generated audit

**Files:** all new/changed test files, `docs/v3/10-design-checklist.md`, `docs/v3/04-backlog.md`, `docs/v3/05-*.md` or the relevant pilot report.

1. Add exact `@pytest.mark.req` markers or frontend test-name references for every proven AC.
2. Regenerate the checklist.
3. Update backlog TT only for items with passing evidence.
4. Add a pilot acceptance report containing: metric count, eval score, unsupported-number rate, isolation result, as-of examples, MCP trace, and known deferred scope.

**Verification:** `make design-audit`; targeted service tests; `make frontend-lint`; `make frontend-test`; relevant `make test-integration`; no new ratchet findings.

### Task 4.2: Release gate and handoff

Run the gate from `docs/v3/04-backlog.md`:

- Eval Set ≥ 0.95 on VERIFIED/GOVERNED;
- `unsupported_number_rate = 0`;
- isolation errors = 0;
- 15–25 pilot metrics pass reconciliation;
- all four pilot journeys have required criteria evidence;
- deferred capabilities are visible as deferred, not falsely marked implemented.

If any gate is red, report the exact failing AC and trace rather than marking the UC done.

## Review focus before execution

1. Confirm that this is the intended first implementation slice, rather than asking to implement all 115 use cases at once.
2. Confirm whether the pilot threshold should be 15–25 metrics exactly or a smaller first demo batch (for example 5 banking metrics).
3. Confirm whether the user wants local/dev verification only in this pass; production deployment remains outside this plan until changes are committed and the Ansible deploy gate is satisfied.
