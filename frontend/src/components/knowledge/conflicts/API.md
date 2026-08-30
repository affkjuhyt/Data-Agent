# Conflicts — API Contract

---

## GET /api/knowledge/conflicts

Called on mount. Returns all three status buckets in one response; each bucket is
independently paginated. `pending` groups conflicts by their batch (a batch is
anchored on one existing text block); `awaiting` and `resolved` are flat lists.

### Query params

| Param | Values | Default |
|---|---|---|
| `pending_page` | integer ≥ 1 | 1 |
| `awaiting_page` | integer ≥ 1 | 1 |
| `resolved_page` | integer ≥ 1 | 1 |

### Response

Each bucket is a page: `{ "items": [...], "page": 1, "page_size": 20, "has_next": false }`.

```json
{
  "pending": {
    "items": [
      {
        "batch_id": "b1a2…",
        "batch_name": "Gold pipeline · policy.pdf · block 7f3c1a9b",
        "extracted_date": "2026-05-20 14:32:00+00:00",
        "number_pending_conflict": 2,
        "conflicts": [
          { "conflict_id": "CFT-001", "conflict_type": "content_conflict", "severity": "medium", "detected_at": "2026-05-20 14:32:00+00:00" },
          { "conflict_id": "CFT-002", "conflict_type": "content_update",   "severity": "low",    "detected_at": "2026-05-20 14:33:10+00:00" }
        ]
      }
    ],
    "page": 1, "page_size": 20, "has_next": false
  },
  "awaiting": {
    "items": [ { "conflict_id": "CFT-010", "conflict_type": "content_duplicate", "severity": "low", "detected_at": "…" } ],
    "page": 1, "page_size": 20, "has_next": false
  },
  "resolved": {
    "items": [ { "conflict_id": "CFT-020", "conflict_type": "content_conflict", "severity": "medium", "detected_at": "…" } ],
    "page": 1, "page_size": 20, "has_next": false
  }
}
```

The UI shows `pending` batches as a folder list; selecting a batch reveals its conflicts.

### Conflict type reference

Resolution methods offered per type (the drawer offers `keep_existing`, `keep_incoming`,
`merge`; `merge` is hidden for types where it does not apply):

| `conflict_type` | Title | Offered methods |
|---|---|---|
| `table_schema` | Schema Exception | `keep_existing`, `keep_incoming` |
| `content_contradiction` | Content Contradiction | `keep_existing`, `keep_incoming` |
| `content_conflict` | Content Conflict | `keep_existing`, `keep_incoming`, `merge` |
| `content_duplicate` | Content Duplication | `keep_existing`, `keep_incoming`, `merge` |
| `content_update` | Content Update | `keep_existing`, `keep_incoming` |

---

## GET /api/knowledge/conflicts/:conflictId

Loads one conflict's full detail on row click.

### Response

```json
{
  "conflict_id": "CFT-001",
  "conflict_type": "content_conflict",
  "severity": "medium",
  "detected_at": "2026-05-20 14:32:00+00:00",
  "where_happens": "batch/b1a2…",
  "status": "pending",
  "detailed_explanation": "factual_quantity_score (0.82) — how to travel to Toronto",
  "existing_snapshot": { "block_id": "7f3c…", "summary": "The bus runs once at 4pm; trip ~5h." },
  "incoming_snapshot": {
    "summary": "Wait an hour for the bus; trip ~5h.",
    "content": "Wait an hour at the stop to catch the bus; trip ~5h."
  },
  "affected_location": "CFT-001",
  "batch_id": "b1a2…",
  "resolution_instruction": null,
  "selected_resolution_method": null,
  "resolved_at": "",
  "resolved_by": null,
  "existing_db_content": "…full active-version text of the existing block…"
}
```

Both snapshots are assembled from Mongo; Postgres stores only the conflict record
(type, severity, status, resolution choice). `existing_snapshot` comes from the batch's
working document and `incoming_snapshot` from the conflicting chunk's payload, keyed by
`(tenant_id, conflict_id)`.

`existing_db_content` is the existing side's full text. While the batch is unresolved it
is the **progressively merged working text** the user is actually judging against;
Postgres only holds the stable final version, so it is read from there once the conflict
is `resolved`.

Once a batch resolves, the payload's replay fields (embedding, entities, relationships,
table) are dropped — the chunk is already committed or discarded. `summary` and
`content` stay, so the resolved archive view still renders.

---

## POST /api/knowledge/conflicts/batches/:batchId/resolve

Resolves a whole batch at once. A batch is anchored on one existing text block and
holds every incoming chunk that conflicts with it, so the user assigns a method to
each conflict and submits them together.

### Request

```json
{
  "resolutions": [
    { "conflict_id": "CFT-001", "selected_resolution_method": "keep_existing" },
    { "conflict_id": "CFT-002", "selected_resolution_method": "merge",
      "resolution_instruction": "Prefer the newer compliance review dated 2026-05-20." }
  ]
}
```

Every open (non-resolved) conflict in the batch must appear in `resolutions`.
`resolution_instruction` is optional (used by `merge`).

### Batch rules (validated server-side, mirrored in the UI)

| Combination | Allowed |
|---|---|
| `keep_existing` | Only when **all** conflicts choose it |
| `keep_incoming` | Only when **exactly one** chooses it and the rest are `keep_existing` |
| `merge` | Any mix of `merge` + `keep_existing`, with **no** `keep_incoming` |

An invalid combination returns `400`.

### Response

```json
{
  "batch_id": "BATCH-001",
  "status": "awaiting"
}
```

The batch moves to `awaiting`; the worker executes the resolution (merges run one by
one, and a merge that still conflicts raises a new pending conflict), then flips the
batch to `resolved`.
