# OpenBB-Inspired Provider Registry and Result Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a typed provider boundary and unified result envelope around the existing AdventureWorks/Data Space query path without weakening permissions, SQL safety, or query-run provenance.

**Architecture:** The provider contract lives in shared V1 contracts and describes a request plus a serializable result envelope. Worker-agent owns the registry and a relational provider adapter; the adapter delegates execution to the existing `run_query` path, so RLS, row scopes, read-only checks, secrets, caching, and audit remain authoritative. The first registry is explicit and built-in, leaving package entry-point discovery for a later phase.

**Tech Stack:** Python 3.11 target, Pydantic, SQLAlchemy/asyncpg, existing `run_query`, pytest in the worker-agent service image.

**Spec:** `docs/v3/06-api-v1.md`, `docs/v3/01-agent-core-and-ui.md`, and the OpenBB-inspired direction agreed in chat.

## Global Constraints

- Do not import or add OpenBB as a runtime dependency; copy the boundary pattern, not the platform.
- Every provider request remains scoped to one Data Space, principal, source, and read-only SQL execution.
- Existing `run_query` remains the only path that reaches a source and records `query_run` provenance.
- Result numbers must retain `query_run_id` and trust/provenance metadata; no answer path may bypass verification.
- No new shell orchestration; use existing Make targets and the worker-agent test image.
- Do not reformat unrelated files or claim whole-tree lint cleanliness.

## Review Focus

- A provider cannot execute SQL for another Data Space or principal; test the request scope and delegation arguments.
- A failed or timed-out source produces a typed failure/warning envelope and preserves the existing query-run status; test both outcomes.
- Empty and truncated result sets remain distinguishable; test `row_count`, `truncated`, and warnings.
- Decimal, date, UUID, and NULL values remain JSON-serializable in the envelope; test representative AdventureWorks-shaped rows.
- A registered provider name that is missing or duplicated fails deterministically before execution; test registry validation.

### Task 1: Shared provider request and result contracts

**Files:**
- Create: `backend/libs/contracts/v1/provider.py`
- Create: `backend/libs/contracts/v1/result.py`
- Modify: `backend/libs/contracts/v1/__init__.py`
- Test: `tests/unit/worker_agent/test_provider_contracts.py`

**Interfaces:**
- `ProviderRequest`: `provider: str`, `data_space_id: UUID`, `principal_id: UUID | None`, `source_id: UUID`, `sql: str`, `caller_kind: str`, `caller_id: UUID`, `as_of: date | None`.
- `ResultColumn`: `name: str`, `type: str`.
- `ResultWarning`: `code: str`, `message: str`.
- `ResultProvenance`: `query_run_id: UUID`, `source_id: UUID`, `trust: str`, `sql_sha256: str | None`.
- `ResultEnvelope`: `columns: list[ResultColumn]`, `rows: list[dict[str, Any]]`, `row_count: int`, `truncated: bool`, `warnings: list[ResultWarning]`, `provenance: ResultProvenance | None`, `error: str | None`.
- Pydantic models serialize dates, UUIDs, decimals, and NULLs to JSON-compatible values through one helper.

- [ ] **Step 1: Write failing contract tests** for JSON serialization, empty rows, truncation, warnings, and provenance.
- [ ] **Step 2: Run the focused test** with `make test-unit UNIT_SUITES='worker_agent:worker-agent' PYTEST_ARGS='tests/unit/worker_agent/test_provider_contracts.py -q'`; verify collection fails because the contract module does not exist.
- [ ] **Step 3: Implement the Pydantic contracts** with strict required identity fields and optional provenance/error fields.
- [ ] **Step 4: Run the focused test** and verify it passes.
- [ ] **Step 5: Export the public contract names** from `backend/libs/contracts/v1/__init__.py` and rerun the focused test.

### Task 2: Provider protocol and deterministic registry

**Files:**
- Create: `backend/services/worker_agent/providers/__init__.py`
- Create: `backend/services/worker_agent/providers/base.py`
- Create: `backend/services/worker_agent/providers/registry.py`
- Test: `tests/unit/worker_agent/test_provider_registry.py`

**Interfaces:**
- `class DataProvider(Protocol): name: str; async def execute(self, request: ProviderRequest) -> ResultEnvelope`.
- `class ProviderRegistry`: `register(provider: DataProvider) -> None`, `get(name: str) -> DataProvider`, `names() -> tuple[str, ...]`, and `build_default() -> ProviderRegistry`.
- Registration rejects empty names and duplicate names; lookup raises a typed `ProviderNotFound` rather than `KeyError`.

- [ ] **Step 1: Write failing registry tests** for registration, duplicate rejection, deterministic lookup, and unknown-provider errors.
- [ ] **Step 2: Run the registry tests** through the worker-agent suite and verify the expected import/behavior failures.
- [ ] **Step 3: Implement the protocol, exception, and registry** with no dynamic import or global mutable singleton.
- [ ] **Step 4: Run the registry tests** and verify they pass.

### Task 3: Relational provider adapter over `run_query`

**Files:**
- Create: `backend/services/worker_agent/providers/relational.py`
- Modify: `backend/services/worker_agent/query/executor.py` only if a small result-normalization helper is needed
- Test: `tests/unit/worker_agent/test_relational_provider.py`

**Interfaces:**
- `RelationalProvider(name: str, secrets: SecretReader)` implements `DataProvider`.
- `execute(request: ProviderRequest) -> ResultEnvelope` calls the existing `run_query` with the request’s exact `org_id`/Data Space/principal/source/caller fields and maps `QueryOutcome` to the envelope.
- Success maps columns, rows, row count, truncation, and `query_run_id`; timeout/failed outcomes map `error` and a stable warning code without fabricating rows.
- The adapter does not accept arbitrary credentials, source URLs, or a caller-supplied trust label.

- [ ] **Step 1: Write failing adapter tests** using a small fake `run_query` boundary to assert exact scope propagation, successful envelope mapping, empty results, truncation, timeout, and failure mapping.
- [ ] **Step 2: Run the adapter tests** and verify they fail before the adapter exists.
- [ ] **Step 3: Implement the adapter** with a single normalization function and provider-owned defaults.
- [ ] **Step 4: Run the adapter tests** and verify they pass.
- [ ] **Step 5: Run the worker-agent unit suite** and verify no existing query executor behavior regresses.

### Task 4: Register and use the provider for the AdventureWorks question path

**Files:**
- Modify: `backend/services/worker_agent/questions.py` at the exploratory SQL execution branch
- Modify: `backend/services/worker_agent/sqlgen/answer.py` if needed to consume the envelope without changing answer semantics
- Modify: `backend/services/worker_agent/main.py` to build the default registry once per worker process
- Test: `tests/unit/worker_agent/test_question_provider_path.py`

**Interfaces:**
- The default registry contains one provider named `relational`.
- The exploratory SQL path requests `relational`; it still runs only Data Space-scoped SQL and still writes the existing answer figures from the existing query outcome/provenance.
- The provider result is the internal boundary for future providers; the current public API and SSE event shapes remain unchanged.

- [ ] **Step 1: Write a failing integration-at-the-function-boundary test** proving an exploratory AdventureWorks-style request resolves the registered provider and preserves `query_run_id`, source, and trust metadata.
- [ ] **Step 2: Run the focused test** and verify the provider registry is not yet connected to the question path.
- [ ] **Step 3: Wire the default registry and adapter** into worker-agent initialization and the SQL answer path without duplicating permission or query execution logic.
- [ ] **Step 4: Run the focused test and existing SQL answer tests** and verify they pass.
- [ ] **Step 5: Run `make test-unit UNIT_SUITES='worker_agent:worker-agent'`** and verify the complete worker-agent suite passes.

### Task 5: Documentation and final verification

**Files:**
- Modify: `docs/v3/06-api-v1.md`
- Modify: `docs/v3/01-agent-core-and-ui.md` only where the provider/result boundary changes the documented agent/tool contract
- Test: existing repository gates

- [ ] **Step 1: Document the provider contract**, registry ownership, relational provider, and future extension seam; explicitly state that permissions and `run_query` remain authoritative.
- [ ] **Step 2: Run `make lint`** and verify the ratchet reports no added findings.
- [ ] **Step 3: Run `make test-unit`** and verify all unit suites pass.
- [ ] **Step 4: Run `git diff --check`** and inspect the final diff for unrelated changes.

