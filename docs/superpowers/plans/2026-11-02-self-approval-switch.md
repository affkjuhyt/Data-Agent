# Self-approval switch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** an org admin can turn on "self-approval" for the organisation. Once it is on, the person
who made something may approve it, at every place that requires two different people today, in
the API and in the database. It is off by default, and every self-approval is recorded.

**Architecture:**
- One boolean on `org.organization`, carried on the `Principal`.
- One kernel helper replaces each `if x == principal.user_id: raise …` and writes an audit row when
  it lets a self-approval through.
- In the database, the `x <> y` CHECKs move into one generic trigger function, `org.two_people()`.
  It reads the org's flag, so an org with the switch off is still refused by the database itself.

**Tech Stack:** FastAPI, SQLAlchemy async, PostgreSQL (plpgsql triggers), alembic, React + vitest.

**Spec:** `docs/superpowers/specs/2026-11-02-three-roles-self-approval-history-design.md` (goal
G2, section "Self-approval switch"). This plan is part 1 of 5. The later parts (vault removal,
three roles, history, chat) get their own plans.

## Global Constraints

- The switch is **off by default**: `allow_self_approval boolean NOT NULL DEFAULT false`.
- Only a holder of the `admin` capability may change it. The change is audited with its `before`
  and `after` values.
- **One person may do every step of a chain** when it is on: review and certify, step 1 and step 2
  of BR-119, approve and enable. Each step a person takes on their own work writes its own audit row:
  `action = "self_approval"`, `after = {"step": "<step name>"}`.
- With the switch on, the "two different people" constraints that are not approvals are relaxed
  too: `owner_id <> steward_id`, `legal_reviewed_by <> owner_id`.
- The `exec` schema is out of scope: plan 2 deletes it.
- The schema is changed through `backend/schema/csdl/source/tab3b.md` and `routines/*.sql`. Then run
  `python3 -m csdl.parse_d4 && python3 -m csdl.gen_ddl && python3 -m csdl.gen_orm` (from
  `backend/schema`), plus an idempotent alembic revision (`SET LOCAL ROLE da_owner … RESET ROLE`,
  `sa.text(SQL.replace(":", "\\:"))`).
  - Next free revision: check `ls backend/alembic/versions`. It is `v3_0023` at the time of writing.
- The API contract is changed through `tab4.md`, then `build_a3.py` and `make api-types`. Never edit
  `openapi.json` or `schema.ts` by hand.
- Messages the user sees are Vietnamese. Code, comments and test names are English.
- Run tests only through `make test-unit` and `make test-integration` with `PYTEST_ARGS`. The host
  Python is 3.10, so it is never used.

## Review Focus

- Switch **off**, approving one's own item → refused by the API (403, unchanged text) **and**, when
  written directly in SQL, by the database. Owner: Task 1, `test_two_people_refused_when_off`.
- Switch turned **off again** after self-approvals → rows already approved stay valid. Only new writes
  are refused. Owner: Task 1, `test_turning_it_off_keeps_existing_rows`.
- A **different** person approving while the switch is on → no `self_approval` audit row. Owner:
  Task 2, `test_other_person_is_not_a_self_approval`.
- Approving with a **null** creator (a seeded or system row) → never counted as a self-approval, and
  never refused. Owner: Task 1 and Task 2, null cases.
- A non-admin calling the settings endpoint → 403, and the flag is unchanged. Owner: Task 5,
  `test_only_an_admin_turns_it_on`.

---

### Task 1: The flag and the database guard

**Files:**
- Modify: `backend/schema/csdl/source/tab3b.md`. Add the `org.organization` column
  `allow_self_approval`. Remove these person-inequality CHECKs:
  - `catalog.dataset_version`: `certified_by <> created_by`
  - `context.knowledge_page_version`: `reviewed_by <> created_by`
  - `context.data_product`: `owner_id <> steward_id`, `activated_by <> created_by`
  - `pipeline.writeback_request`: `approved_by <> requested_by`
  - `acquire.crawl_source`: `legal_reviewed_by <> owner_id`
  - `forecast.model_version`: `approved_by <> created_by`
  - `forecast.formula_component`: `certified_by <> created_by`
  - `governance.model_provider`: in the frontier CHECK, drop only the `approved_by <> enabled_by`
    conjunct and keep the rest.
- Modify: `backend/schema/csdl/routines/context.sql:94-100`. Keep `(certified_by IS NULL) =
  (certified_at IS NULL)` and the certified-needs-certifier rule. Drop the two `IS DISTINCT FROM`
  conjuncts; the trigger now holds them.
- Create: `backend/schema/csdl/routines/org.sql`, holding `org.self_approval_allowed(uuid)`,
  `org.two_people()` and the `CREATE TRIGGER` statements. If the generator does not pick up a new
  routines file by itself, register it the same way `context.sql` is registered.
- Modify: `backend/alembic/sql/dataagent_audit.sql` through its source. `audit.export_job` gets
  `self_approved boolean NOT NULL DEFAULT false`, and its CHECK becomes `approved_by IS NULL OR
  approved_by <> requested_by OR self_approved`. The audit database cannot read `org.organization`,
  so the API is its guard.
- Create: `backend/alembic/versions/v3_0023_self_approval.py`.
- Regenerate: `backend/alembic/sql/*.sql`, `backend/libs/dataagent_db/models/*`.
- Test: `tests/integration/migrate/test_two_people.py`

**Interfaces:**
- Produces:
  - column `org.organization.allow_self_approval boolean NOT NULL DEFAULT false`;
  - SQL `org.self_approval_allowed(p_org uuid) RETURNS boolean`, `STABLE SECURITY DEFINER`, which
    returns false when the org is not found;
  - trigger function `org.two_people()`, configured per table through `TG_ARGV` as column-name
    pairs `a, b, a, b, …`;
  - ORM `org.Organization.allow_self_approval: bool`;
  - ORM `audit.ExportJob.self_approved: bool`.

- [ ] **Step 1: Write the failing tests** in `tests/integration/migrate/test_two_people.py`. Use the
  fixtures `test_core.py` uses to insert rows as `da_owner`. Use `catalog.dataset_version`
  (`certified_by`/`created_by`) as the representative table, plus one parametrised check per guarded
  pair:

```python
def test_two_people_refused_when_off(db): # same user as created_by and certified_by
    with pytest.raises(psycopg2.errors.CheckViolation, match="hai người"):
        certify(db, version, by=creator)

def test_two_people_allowed_when_on(db):
    set_flag(db, org, True); certify(db, version, by=creator)  # no error

def test_turning_it_off_keeps_existing_rows(db):
    set_flag(db, org, True); certify(db, v1, by=creator); set_flag(db, org, False)
    touch(db, v1)  # an UPDATE of an unrelated column still succeeds
    with pytest.raises(psycopg2.errors.CheckViolation): certify(db, v2, by=creator)

def test_a_null_side_is_never_refused(db): ...  # created_by NULL, certified_by set

@pytest.mark.parametrize("table, a, b", GUARDED)  # the 11 pairs listed under Files
def test_every_guarded_pair_has_the_trigger(db, table, a, b):
    # pg_trigger has a two_people trigger on the table whose tgargs contain a and b
```

- [ ] **Step 2: Run them and see them fail.**
  `make test-integration INTEGRATION_SUITES=migrate:migrate PYTEST_ARGS="tests/integration/migrate/test_two_people.py -q"`
  (use the suite name `make help` lists for migrate). Expected: FAIL, because the column and the
  function do not exist yet.

- [ ] **Step 3: Implement.** `org.two_people()` reads `to_jsonb(NEW) ->> TG_ARGV[i]` for each pair.
  On an INSERT, or on an UPDATE where either column of a pair changed (compare with `OLD`), it
  raises `check_violation` when both values are non-null, equal, and
  `NOT org.self_approval_allowed(NEW.org_id)`. The message is `'Cần hai người khác nhau (%s và %s):
  tổ chức chưa bật tự duyệt'`. The "changed" test is what lets existing rows stay valid. Write the
  revision with the same DDL, idempotent: `ADD COLUMN IF NOT EXISTS`, `DROP CONSTRAINT IF EXISTS`
  (by the generated constraint names), `CREATE OR REPLACE FUNCTION`, `DROP TRIGGER IF EXISTS` and
  then `CREATE TRIGGER`.

- [ ] **Step 4: Run them and see them pass.** Same command. Then run the whole migrate suite
  (`test_structure.py` checks that the generated DDL and the alembic path agree). Expected: PASS.

- [ ] **Step 5: Commit**, with message `feat(org): self-approval is an org switch, and the database
  guards two people only while it is off`.

### Task 2: Principal carries the switch; one helper decides

**Files:**
- Modify: `backend/services/api/kernel/auth.py:58-76`. `Principal` and `Identity` gain
  `self_approval: bool = False`, and the code that builds `Principal` (near `:163`) copies it.
- Modify: `backend/services/api/modules/org/identity.py:124-153`. Select `status` and
  `allow_self_approval` in the one query that already reads the organisation.
- Create: `backend/services/api/kernel/approval.py`
- Test: `tests/unit/api/test_approval.py`

**Interfaces:**
- Consumes: `org.Organization.allow_self_approval` (Task 1).
- Produces:

```python
def two_people(principal: Principal, *others: uuid.UUID | None, message: str,
               status: int = 403, code: str = "forbidden") -> bool:
    """True when the caller is one of `others` and the org allows it (a self-approval);
    False when the caller is none of them; raises Problem(status, code, message) otherwise.
    None in `others` is ignored."""

async def record_self_approval(principal: Principal, *, step: str, object_kind: str,
                               object_id: uuid.UUID) -> None:
    """audit.public.append(principal.org_id, actor_kind="user", actor_id=principal.user_id,
    on_behalf_of=None, data_space_id=principal.data_space_id, action="self_approval",
    object_kind=object_kind, object_id=object_id, after={"step": step})"""
```

  Call sites call `record_self_approval` **inside** their transaction block, after their own writes,
  so a failed audit write rolls the approval back ("not done unaudited", `audit/public.py`).

- [ ] **Step 1: Write the failing tests** in `tests/unit/api/test_approval.py`:
  - `test_someone_else_passes`: returns False.
  - `test_self_refused_when_off`: raises Problem 403 with the given message.
  - `test_self_allowed_when_on`: returns True.
  - `test_none_is_ignored`: `two_people(p, None, message="m")` is False.
  - `test_other_person_is_not_a_self_approval`: switch on, other id → False.
  - `test_custom_status_and_code`: 422 with `business_rule_violated` is passed through.
  - `test_record_writes_one_audit_row`: monkeypatch `audit.public.append` and assert its kwargs.
- [ ] **Step 2: Run them and see them fail.**
  `make test-unit UNIT_SUITES=api:api PYTEST_ARGS="tests/unit/api/test_approval.py -q"`.
- [ ] **Step 3: Implement** `kernel/approval.py` and the two dataclass fields as specified.
- [ ] **Step 4: Run them and see them pass**, plus `tests/unit/api/test_auth*.py`. Expected: PASS.
- [ ] **Step 5: Commit**, with message `feat(api): the principal carries the self-approval switch;
  two_people decides`.

### Task 3: Proposals, datasets, knowledge, products, writeback, reports

**Files (each `if … == principal.user_id: raise Problem(...)` becomes a `two_people(...)` call
keeping its exact message and status; when it returns True, call `record_self_approval` with the
step name in parentheses):**
- `backend/services/api/modules/inbox/routes.py:589` (`proposal.review`), `:624`
  (`proposal.accept_edit`), `:635` (`proposal.second_step`; the `compliance` capability check above
  it stays, and plan 3 changes it), `:438` (`hitl.approve`)
- `backend/services/api/modules/catalog/routes.py:1493` (`dataset_version.certify`)
- `backend/services/api/modules/context/routes_knowledge.py:716` (`knowledge_page.certify`; both
  author and reviewer are among `others`)
- `backend/services/api/modules/context/routes_products.py:172` (`data_product.owner`)
- `backend/services/api/modules/pipeline/routes.py:1358` and `pipeline/public.py:80`
  (`data_product.publish`)
- `backend/services/api/modules/workspace/routes.py:1566` (`session_report.approve`). This site does
  not raise; it routes the report to `pending_approval`. With a self-approval it publishes as if
  another person had approved.
- Tests: next to each module's existing four-eyes test in `tests/integration/api/` (find it by its
  error text, e.g. `grep -rn "cannot review their own" tests/integration/api`).

**Interfaces:**
- Consumes: `two_people`, `record_self_approval` (Task 2); the database trigger (Task 1).

- [ ] **Step 1: Write the failing tests.** For every site, next to its existing "refused" test (which
  must stay green unchanged), add `test_<site>_self_approved_when_the_org_allows_it`. It sets
  `allow_self_approval = true` on the test org through the fixture's SQL seed, performs the action as
  the creator, asserts 2xx and the new state, and asserts one `audit.audit_log` row with
  `action = 'self_approval'` and `after->>'step'` equal to the step name. For `:635`, the test has
  one compliance holder do steps 1 and 2 and expects two `self_approval` rows.
- [ ] **Step 2: Run them and see them fail.**
  `make test-integration INTEGRATION_SUITES=api:api PYTEST_ARGS="<the changed test files> -q"`.
- [ ] **Step 3: Implement** the replacements.
- [ ] **Step 4: Run them and see them pass**, then `make test-related`. Expected: only the known red
  tests. Compare with `main` in a baseline worktree if any red test is not on the list in `CLAUDE.md`
  §1.
- [ ] **Step 5: Commit**, with message `feat(api): proposals, certifications, products and reports
  accept a self-approval when the org allows it`.

### Task 4: Forecasts, periods, access requests, providers, crawl legal review, audit export

**Files (same replacement rule as Task 3):**
- `backend/services/api/modules/forecast/models.py:273` (`forecast_model.decide`)
- `backend/services/api/modules/forecast/formula.py:514` (`formula_value.certify`)
- `backend/services/api/modules/period/routes.py:776` (`period_number.approve`; the `compliance`
  requirement stays)
- `backend/services/api/modules/org/admin/access.py:237` (`access_request.decide`)
- `backend/services/api/modules/governance/routes.py:480` and `:533` (`model_provider.approve`,
  `model_provider.enable`)
- `backend/services/api/modules/acquire/routes.py:790` (`crawl_source.legal_review`)
- `backend/services/api/modules/audit/routes.py`, at the code that sets `audit.ExportJob.approved_by`
  (find it from the `waiting_approval` status). Use `two_people` there and set
  `self_approved = True` on the job when it returns True. If no approve route exists, nothing
  changes here; note that in the commit message.
- Tests: next to each existing four-eyes test, same pattern as Task 3.

- [ ] **Step 1: Write the failing tests**: one `…_self_approved_when_the_org_allows_it` per site, same
  assertions as Task 3.
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run them and see them pass**, plus `make test-related`.
- [ ] **Step 5: Commit**, with message `feat(api): forecasts, periods, access, providers and crawl
  review accept a self-approval when the org allows it`.

### Task 5: Two people where nothing checks today

**Files:**
- `backend/services/api/modules/governance/policies.py:787` (`activate_policy`): compare the
  activator with `row.created_by` (step `policy.activate`).
- `backend/services/api/modules/context/routes_semantic.py:1137` (`update_example`, when
  `body.status == "active"`): compare with `row.created_by` (step `example.confirm`).
- The message for both is `"Người tạo không tự duyệt được khi tổ chức chưa bật tự duyệt."` (403
  `forbidden`).
- Synonyms and relationships are already reviewed through inbox proposals (Task 3, `:589`). Nothing
  to add.
- Tests: the modules' existing activation and confirm tests. Any test that activates its own policy
  or confirms its own example now needs a second user, or the org switch on. Fix the fixtures, not
  the rule.

- [ ] **Step 1: Write the failing tests**: `test_policy_activation_needs_another_person`,
  `test_policy_self_activation_when_the_org_allows_it`, and the same two for examples.
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement**, then run the governance and context integration tests and fix the
  fixtures the new rule breaks.
- [ ] **Step 4: Run them and see them pass.**
- [ ] **Step 5: Commit**, with message `feat(api): activating a policy and confirming an example need
  two people unless the org allows self-approval`.

### Task 6: The switch itself — endpoint, screen, seed, docs

**Files:**
- `backend/services/api/modules/org/admin/` (a new `organization.py`, or the module's routes file,
  following how `roles` or `users` is laid out):
  - `GET /v1/admin/organization` returns `{id, code, name, allow_self_approval}`.
  - `PATCH /v1/admin/organization` takes `{allow_self_approval: bool}`. It needs the `admin`
    capability and audits through `org/admin/common.py` with `before` and `after`.
- `tab4.md`, then `build_a3.py` and the A2 catalogue entry for both operations, then `make api-types`.
- `frontend/src/lib/v1/admin.ts` (or the file where the other `/v1/admin/*` calls live):
  `getOrganization()`, `setSelfApproval(on: boolean)`.
- Create `frontend/src/features/admin-access/SelfApprovalSwitch.tsx`, rendered at the top of
  `UsersPage.tsx`:
  - label "Cho phép tự duyệt", with the help text "Khi bật, người tạo được tự duyệt việc của mình. Mỗi
    lần tự duyệt được ghi vào nhật ký kiểm toán.";
  - the toggle is disabled without the `admin` capability.
- `deploy/docker/sql/v3-adventureworks-seed.sql`: `UPDATE org.organization SET allow_self_approval =
  true WHERE id = 'a0000000-0000-0000-0000-000000000001';` with a comment saying it is the test org.
- `docs/v3/04-backlog.md`: a new item for this plan (status `done <commit>` in the last commit).
- The BR wording in the design source and `docs/v3`: BR-100, 119, 133, 156, 158, 164, 174, 182 read
  "two people unless the org allows self-approval". Then `make design-checklist`.
- `docs/RUNBOOK.md`: one line on where the switch is.
- Tests: `tests/integration/api/test_admin_organization.py`;
  `frontend/src/features/admin-access/SelfApprovalSwitch.test.tsx`.

- [ ] **Step 1: Write the failing tests**:
  - `test_only_an_admin_turns_it_on`: a non-admin gets 403 and the flag is unchanged.
  - `test_an_admin_turns_it_on_and_off_and_each_change_is_audited`: two `audit_log` rows with
    `before`/`after` `{"allow_self_approval": …}`.
  - `test_the_next_request_sees_the_new_value`: after PATCH, a self-approval site from Task 3 accepts.
  - The vitest test checks that the toggle reflects `getOrganization()`, calls `setSelfApproval`, and
    is disabled without `admin`.
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Verify the whole change**:
  - `make lint` (0 added);
  - `make type-v3` (no new errors in touched files; compare to `main`);
  - `make frontend-lint`;
  - `make frontend-test`;
  - `make test-unit`;
  - `make test-integration` (only the known red tests);
  - `make design-checklist` (no diff left).
- [ ] **Step 5: Commit**, with message `feat(admin): an admin turns self-approval on for the
  organisation; the test org has it on`. Mark the backlog item `done`.
