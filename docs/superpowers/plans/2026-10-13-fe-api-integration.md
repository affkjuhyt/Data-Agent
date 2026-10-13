# FE/API Integration Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Remove the remaining frontend/backend integration gaps for screens that still call proposal or mock-only APIs.

**Architecture:** Treat the FastAPI A2/A3 catalogue as the source of truth. Each group adds the backend route and schema, registers the endpoint in A2/A3, updates generated OpenAPI/types, then removes the FE proposal fallback only after a real route exists. Data reads must use the existing policy/data-space transaction helpers; long-running work must use existing job/worker patterns.

**Tech Stack:** FastAPI, SQLAlchemy async, PostgreSQL models, React/TypeScript, Vitest, OpenAPI Typescript generation, Docker-backed Make checks.

**Spec:** `docs/v3/README.md`, `docs/v3/01-agent-core-and-ui.md`, `backend/services/api/catalogue/source/tab4.md`.

## Global Constraints

- Preserve existing untracked agent metadata and unrelated user changes.
- Do not store secrets; REST credentials remain `secret_ref` references.
- Every new route must be present in A2/A3 and pass `make api-types`.
- Use Data Space and capability checks already used by the owning module.
- Run a focused frontend test and relevant backend checks for each group; run the full available frontend suite before completion.

## Review Focus

- Dataset preview/statistics must enforce Data Space access and never return unmasked protected values.
- Derivation must not execute arbitrary SQL from the browser; validate the existing pipeline/filter contract.
- REST try must not persist credentials or configuration and must apply timeout/response-size limits.
- Proposal comments that describe already implemented routes must be removed or corrected rather than counted as missing work.
- Generated OpenAPI/schema must match route decorators and A2/A3.

### Task 1: Source preview, column statistics, derive, and REST try

**Files:**
- Modify: `backend/services/api/modules/catalog/routes.py`, `backend/services/api/modules/catalog/schemas.py`
- Modify: `backend/services/api/catalogue/a2_endpoints.json`, `backend/services/api/catalogue/a3_contract.json`, `backend/services/api/catalogue/source/tab4.md`
- Modify: `frontend/src/lib/v1/sources.ts`, source screen tests and mocks as required
- Test: focused catalog/API and `frontend/src/features/data-sources` tests

- [ ] Add failing contract tests for the four missing routes.
- [ ] Implement the smallest policy-safe backend behavior using existing dataset/catalogue helpers.
- [ ] Update A2/A3, regenerate OpenAPI/types, and switch FE calls from proposal/mock-only behavior.
- [ ] Run focused tests and commit.

### Task 2: Data Prep pipeline preview, run, output draft, schedule, and lineage

**Files:**
- Modify: `backend/services/api/modules/data_prep` (owning routes/schemas/models)
- Modify: `backend/services/api/catalogue/*`
- Modify: `frontend/src/lib/v1/dataPrep.ts` and related screens/tests

- [ ] Inventory existing pipeline models and routes before adding only absent operations.
- [ ] Add failing route/FE tests, implement through existing pipeline/job abstractions, update contract and generated types.
- [ ] Commit after focused verification.

### Task 3: Sessions, reports, skills, and admin/access gaps

**Files:**
- Modify owning backend modules and their catalogue entries.
- Modify `frontend/src/lib/v1/{sessions,reports,skillAuthoring,adminAccess,adminPlatform}.ts` and tests.

- [ ] Compare each proposal call to generated OpenAPI; skip calls already served by a route.
- [ ] Implement missing CRUD/action routes with existing authorization and state-transition rules.
- [ ] Generate types, run focused tests, and commit.

### Task 4: Final inventory and verification

**Files:** generated OpenAPI/schema, stale FE comments/docs as needed.

- [ ] Re-run proposal inventory and classify remaining comments as real API gaps or documentation-only fields.
- [ ] Run `make api-types`, `make frontend-lint`, `make frontend-test`, `git diff --check`, and relevant backend checks.
- [ ] Commit documentation-only cleanup and report any genuinely blocked external integrations.
