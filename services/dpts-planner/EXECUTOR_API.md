# DPTS Planner — Executor Integration Guide

The planner turns a user query into a **plan** (an ordered list of high-level
steps, each with concrete tasks) and stores it. An **executor** (built by you,
separately) reads the plan, runs each step's tasks, and — when a task breaks —
reports the broken node back so the planner can repair the plan.

Everything is scoped by **`user_id`**: the same request from two users is kept
separate (separate cache, separate stored plans, separate live search trees).

Base URL: `http://<planner-host>:8080`

---

## 1. Create / fetch a plan

`POST /api/plans`

```json
{ "user_id": "u_123", "user_input": "Build a daily sales report from Postgres orders", "use_cache": true }
```

- `use_cache` (default `true`): if this user already has a plan for a similar
  query (Elasticsearch match), it is returned immediately (`"cached": true`)
  instead of re-planning.

**Response** `201`:

```json
{
  "cached": false,
  "plan": {
    "plan_id": "plan_c05f64ee6f24",
    "user_id": "u_123",
    "user_input": "Build a daily sales report from Postgres orders",
    "status": "ready",
    "confidence": 0.81,
    "targets": [
      {
        "node_id": "n7",
        "description": "Extract data from the Postgres orders table",
        "tasks": [
          { "title": "Query orders for the day", "type": "sql", "depend_on": [] }
        ]
      },
      {
        "node_id": "n14",
        "description": "Aggregate and format the daily sales report",
        "tasks": [
          { "title": "Aggregate totals", "type": "transform", "depend_on": [0] },
          { "title": "Render report",     "type": "render",    "depend_on": [0] }
        ]
      }
    ],
    "node_path": ["n1", "n7", "n14"],
    "terminal_node_id": "n14",
    "created_at": 1782400000.0,
    "expires_at": 1782486400.0
  }
}
```

### How the executor uses a plan
- Execute `targets` **in order**. Each target is one step.
- Within a step, run `tasks`; `depend_on` lists the **indices of tasks in the
  same step** that must finish first (`[]` = no dependency).
- **`node_id` on each target is the key you report back on failure.** Keep it.
- `node_path` is the root→terminal path in the search tree (provenance);
  `node_path[0]` is the root (the user goal, no tasks).
- A plan expires at `expires_at` (epoch seconds). After that the planner's TTL
  sweeper erases it from both stores; fetch/re-create before then.

---

## 2. Access a ready plan later

`GET /api/users/{user_id}/plans/{plan_id}`

Returns the same plan document as above (read straight from the plan DB).
`404` if it does not exist for that user (wrong user_id, or expired/erased).

---

## 3. Report a broken node (failure recovery)

When a task fails during execution, announce the **node_id of the step it
belongs to** (the `node_id` field on that target):

`POST /api/users/{user_id}/plans/{plan_id}/node-failed`

```json
{ "user_id": "u_123", "node_id": "n14" }
```

**What you provide:** the `node_id` of the failed step — copy it from the
target you were executing (`plan.targets[i].node_id`).

**What the planner does:** the Planner gate trims that node's branch from the
tree, deletes its candidates from the pool, records the failed step so it is not
re-proposed, and expands again for an alternative plan.

**Response:**

```json
{
  "recovered": true,
  "trimmed": ["n14", "n21"],
  "parent_id": "n7",
  "plan": { "...": "a NEW plan document, same shape as above, with a new plan_id" }
}
```

- `recovered: true` + `plan` → execute the new plan (note its new `plan_id`).
- `recovered: false` with:
  - `"reason": "no_live_session"` → the planner has no in-memory tree for this
    `(user_id, plan_id)` (e.g. it restarted, or the plan came from cache).
    Re-create a plan via **POST /api/plans** with `use_cache: false`.
  - `"reason": "no_alternative_plan"` → the tree had no other viable branch;
    re-create with `use_cache: false`.
  - `"reason": "unknown_node"` → the `node_id` is not in this plan's tree.

---

## Notes for multi-user / multi-replica

- A live search tree is held in memory keyed by `(user_id, plan_id)` so failure
  recovery reuses the same tree. With multiple planner replicas, the
  `node-failed` call must reach the replica holding that tree (sticky routing by
  `plan_id`), otherwise it returns `no_live_session` and you re-create the plan.
- `plan_id` is globally unique; `user_id` scopes ownership, the cache, and the
  TTL sweep.
