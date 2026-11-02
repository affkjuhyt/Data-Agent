# Three roles, a self-approval switch, and history on everything

Date: 2026-11-02. Status: agreed in chat by the user ("Oke tôi đồng ý với điều đó", "oke chốt vậy
đi"). Calls marked *(my call)* were not discussed and can be changed.

## Why

The user works alone and tests the product end to end on the dev cluster (DS-AW). Two things stop
them:

1. **Account switching.** The product has 11 database roles built from 15 capabilities, and about
   16 "the creator cannot approve their own work" rules. Those rules live in the API *and* in
   database CHECK constraints (`x_by <> created_by`). No switch relaxes them. Even
   `helios-engineer` with `dev-full-access` (every capability) cannot approve its own import: the
   user has to log out, log in as `helios-admin`, approve, and switch back. Testing one flow means
   several logins.
2. **No history.** The user wants to look back at what changed, when and by whom, on everything,
   above all chats and data sources. Today:
   - `audit.audit_log` records every /v1 call, but only "who called what", with no before/after.
   - Only knowledge pages, documents and skills show version history in the UI.
   - The "Lịch sử" buttons on the metric, policy and agent pages are disabled.
   - Synonyms, relationships, verified queries, sources, role grants, reports and dashboards have
     no history at all.
   - Chats are saved, but untitled ("Hội thoại không tiêu đề"). There is no list beyond the 8 most
     recent, they can't be renamed or deleted, and a follow-up question does not see earlier
     answers' numbers.

What the user wants, in their words: "không phân role quá nhiều, mọi thứ đều là auto, người dùng chỉ
mang tính chất duyệt", "các tính năng đều nên có lịch sử", "cả các chat cũng phải được lưu lại, để
tiếp tục phân tích sau này", "xem lịch sử chat, lịch sử nguồn đều cần cả".

## Goals

- G1. **Three roles** everywhere (org-wide and per Data Space), replacing the 11:
  - **Người hỏi** (`asker`): asks questions; reads answers, reports and dashboards; keeps chats and
    analysis sessions.
  - **Người dựng** (`builder`): everything an asker does, plus connecting sources, scans, the
    semantic model, skills and agents, and proposing and approving changes.
  - **Quản trị** (`admin`): everything a builder does, plus users, access policies, compliance and
    the audit log.
- G2. A per-organisation **self-approval switch**, off by default.
  - When on, the person who made something may approve it at all ~16 places that forbid it today,
    database constraints included.
  - Every self-approval is marked as such in history.
  - Only an admin can flip the switch, and flipping it is itself in history.
- G3. **History on every feature.** Every detail page gets a "Lịch sử" tab: who, when, what (created,
  edited, proposed, approved, self-approved, disabled, deleted, restored), and a before/after diff.
- G4. **Source history**:
  - connection setting changes (a secret appears only as "đã đổi", never its value);
  - every scan (when; tables added or gone; columns whose type changed);
  - each table enabled or disabled, and every re-profile;
  - the per-table schema versions that `catalog.dataset_version` already holds.
- G6. **Remove the executive vault.** The user no longer needs it ("không cần dữ liệu kho của lãnh
  đạo đâu, giờ không cần chức năng đó nữa"). Remove all of it:
  - the `exec` schema (11 tables);
  - `backend/services/api/modules/exec/`;
  - the `exec_vault` capability;
  - the VAULT menu item and its pages;
  - AC02's vault rights;
  - its business rules (BR-195 shred handover and the rest), docs and tests.

  Deletion follows `CLAUDE.md` §2, with a repo-wide grep for each symbol and its string form. Data
  in the dev cluster's `exec` tables is dropped by the migration.
- G5. **Chat history**:
  - a page listing every one of the user's conversations, searchable and filterable by Data Space;
  - a title set automatically from the first question, which the user can rename;
  - delete means hide: the chat can be restored and stays in history;
  - reopening shows the chat as it was (this already works) and continues it;
  - a follow-up question sees the earlier answers' figures, so "so với con số vừa rồi" works.

## Non-goals

- No change to what the agent may do on its own. Invariants 5 and 12 stay: the LLM proposes,
  deterministic checks and a person decide, and nothing proposed is live until someone approves it.
  The switch changes *who* may approve, not *whether* approval happens.
- No change to permission checks before generation (invariant 13), column masking, or row policies.
- No new history store per entity type, and no event-sourcing rewrite.
- No sharing chats with other people. Admins do not read other users' chats *(my call)*.

## Anti-goals (a failure even if it works)

- The self-approval switch is on by default, or on in an org without anyone having turned it on.
- A self-approval that history can't tell apart from a two-person approval.
- A secret, password or token value appearing in any history row or diff.
- History that can be edited or deleted. The audit chain's hash must keep verifying.
- Losing data on the role migration: a user who could do something yesterday finds it locked
  today. The opposite is acceptable (merging roles widens some people's rights) and is said in the
  release note.

## Decisions

### Roles

- *(my call)* Old roles map to the three new ones:

  | Old role | New role |
  | --- | --- |
  | AC01 Business user, AC02 Executive (its vault rights go with G6) | `asker` |
  | AC03 Steward, AC04 Data engineer, AC08 Analyst, AC09 Agent builder, `data-engineer` | `builder` |
  | AC05 System admin, AC06 Compliance, AC10 Internal audit, `dev-full-access` | `admin` |

  A user holding several old roles gets the highest new one.
- The executive vault and its `exec_vault` grant are removed (G6), so the three roles are the
  whole model.
- The capability strings in code (`kernel/auth.py` `CAPABILITIES`) may stay as the internal unit of
  checks; the three roles are bundles of them. The builder decides whether to keep or collapse
  them. What the user sees is three roles.
- The two-step chain that needs `compliance` on step 2 (BR-119) becomes "an admin" on step 2.

### Self-approval switch

- One org setting, e.g. `org.organization.allow_self_approval boolean NOT NULL DEFAULT false`.
- The API checks it at each of the ~16 sites. The CHECK constraints become conditional, so the
  database still refuses a self-approval in an org where the switch is off. The guard must stay in
  the database, not only in the API.
- *(my call)* The DS-AW test org on the dev cluster is seeded with the switch on.
- *(my call)* With the switch on, **one person may do every step of a chain**: review and
  certify, step 1 and step 2 of BR-119, approve and enable. Each step they take on their own
  work is its own history row marked `self_approved`. The same switch relaxes the other "two
  different people" constraints: `owner_id <> steward_id`, `successor_id <> owner_id`,
  `legal_reviewed_by <> owner_id`.
- The user confirmed the switch also covers the duties the code separates on purpose.
  - Enabling an external model provider: the models in use are offline, served in-house.
  - Exporting the whole audit log ("Oke").
  - The vault shred handover goes away with G6.
- The places that check nothing today (policy activation, verified-query confirm, synonym review)
  get the same rule: two people unless the switch is on.

### History

- Built on `audit.audit_log`, which already has `before`, `after` and a hash chain. Each module's
  write records the object's before and after state in the same transaction (the outbox path that
  already carries `audit.requested`).
  - `audit/public.py` gains a `before` parameter.
  - A self-approval writes `action = …approve` with `self_approved: true` in `after`.
- Secret fields are redacted before the row is written ("đã đổi"), never after.
- *(my call)* **A history row holds metadata, never data values.** Each entity declares which of
  its fields its snapshot keeps (names, expressions, settings, status, counts). Profile samples,
  query results, answer figures and question text are not copied into `audit_log`. A reader's
  masking and row policies therefore have nothing to leak there, and an admin on the audit page
  sees that a chat was renamed or deleted, never what was asked in it. The chat's own history
  is the chat itself (`qa.question`, `qa.answer`), which only its owner reads.
- *(my call)* **Automatic changes are in history too.** A change made by a worker, an agent
  job or a database trigger writes a history row whose actor is the system or the agent
  principal. Examples: a scan, a profile, a knowledge version superseded, a crawl source
  auto-paused when its legal approval expires. The user's "mọi thứ đều là auto" means these are
  most of the changes. The rows come from the worker's own transaction through the same outbox,
  and triggers write theirs in the database. This is larger than API routes alone and is
  planned as its own step.
- One read endpoint for any object, e.g. `GET /v1/history?object_kind=…&object_id=…`, scoped to what
  the caller may see. One shared frontend "Lịch sử" tab component used by every detail page, which
  also re-enables the three disabled buttons (metric, policy, agent).
- Existing version tables (`metric_version`, `dataset_version`, `skill_version`, `agent_config`,
  `knowledge_page_version`, …) stay. The history tab links each row to the version it produced.
- Who may read: whoever may open the object reads its history. The org-wide audit page stays
  admin-only.

### Source history

- Same mechanism as above, with source-specific events:
  - every scan run is a history row, summarising tables added or removed and columns retyped (from
    the scan's diff against `dataset_version`);
  - each table enable or disable, and each re-profile, is its own row.
- The source page and the table page each get the tab.

### Chat history

- `qa.conversation.title`: set from the first question (trimmed, no LLM needed); renamable.
- `deleted_at`: set by delete, cleared by restore. Lists hide deleted chats; a "Đã xoá" filter shows
  them.
- A full chat list page with search (title and question text) and a Data Space filter.
- The worker's conversation context (`questions.py` `_conversation`) adds each earlier answer's
  figures (label, value, unit, period) to the turns it passes to the readers and the analyst. The
  rule path keeps using the previous chips. Figures are passed as data the reader may refer to, and
  are never re-used as computed numbers: a new number still comes from SQL (invariant 6).
  *(my call)* Before they are passed again, figures of a metric the asker may no longer use are
  dropped (`blocked_metric_ids`, invariant 13).
- *(my call)* This project owns `_conversation` and the `llm_reader.Turn` type. The prompt
  wording that uses the figures, in `llm.py` and `understand/`, is agreed with the other session
  first and changed by whichever of the two it agrees on.
- **Coordination:** the Ask screen redesign (`docs/superpowers/specs/2026-11-02-ask-chat-redesign-design.md`,
  another session) owns `frontend/src/features/ask/**`, including its in-page conversation panel
  with search and recents. This project delivers:
  - the API (title, rename, delete and restore, list with search and filter);
  - the full list page outside `features/ask`;
  - the worker context change.

  The Ask panel adopts the new API fields in its own work.

## Constraints

- Repo rules in `CLAUDE.md`:
  - schema changes go through `tab3b.md` → generator → an idempotent alembic migration (next free
    number after v3_0022 at the time of writing; check again, other sessions add migrations);
  - API contract changes go through `tab4.md` → `make api-types`;
  - the backlog gets items in `docs/v3/04-backlog.md` in the same commits;
  - tests are tagged with the acceptance criterion they prove.
- The design checklist (`make design-checklist`) must not go stale: the business rules that name
  four-eyes (BR-100, BR-119, BR-133, BR-156, BR-158, BR-164, BR-174, BR-182, BR-195) change wording to
  "two people unless the org allows self-approval".
- Another session works in `features/ask/**`, `questions.py`'s `_run`, `_answer`,
  `answer_question`, `answer_edited_plan`, `llm.py` and `turn_trace.py`. Changes there are
  coordinated, not made unilaterally. `_conversation` is outside that list but in the same file.

## Left to the builder

- The order of delivery. Suggested *(my call)*:
  1. the self-approval switch, which unblocks testing at once;
  1b. removing the executive vault (G6), before the role migration so AC02 maps cleanly;
  2. the three roles and the migration of existing grants;
  3. history: the audit before/after write, the read endpoint and the shared tab; sources and chats
     first, then the rest;
  4. the chat list page and the worker context.
- Whether capabilities collapse in code or stay as internal bundles.
- The exact diff format of a history row and how the tab renders it.
- How the frontend explains the switch when it is on (e.g. a badge "Tự duyệt đang bật").
