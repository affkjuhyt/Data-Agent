# Policies — API Contract

Two policy types managed in this tab: **Filter Policies** and the **Extraction Policy**.

---

## Filter Policies

### GET /api/knowledge/policies/filtering

Called on mount. Returns the full list of filter policies. If the server returns an empty list or fails, `INITIAL_FILTER_POLICIES` (4 seed items) is used as fallback.

#### Response

```json
[
  {
    "id": "FP-001",
    "name": "Exclude PII Data",
    "type": "natural_language",
    "content": "Remove any personally identifiable information...",
    "added_by": "platform-admin",
    "added_when": "2026-05-20",
    "active": true
  },
  {
    "id": "FP-002",
    "name": "Remove Duplicate Sections",
    "type": "natural_language",
    "content": "Detect and strip semantically redundant paragraphs...",
    "added_by": "ai-engineer",
    "added_when": "2026-05-21",
    "active": true
  },
  {
    "id": "FP-003",
    "name": "Compliance Keywords Block",
    "type": "exact_word",
    "content": "[\"DRAFT\",\"INTERNAL USE ONLY\",\"NOT FOR DISTRIBUTION\"]",
    "added_by": "platform-admin",
    "added_when": "2026-05-22",
    "active": true
  }
]
```

### Policy type reference

| `type` | UI label | `content` format |
|---|---|---|
| `natural_language` | Natural Language | Plain prose instruction for the AI to follow |
| `exact_word` | Exact Word | JSON-stringified `string[]` of blocked keywords |

---

### POST /api/knowledge/policies/filtering

Creates a new filter policy. Fired when the user saves the "Add Policy" drawer.

#### Request

```json
{
  "name": "Block Financial Data",
  "type": "exact_word",
  "content": "[\"REVENUE\",\"SALARY\",\"BUDGET\"]",
  "added_by": "platform-admin",
  "added_when": "2026-05-25",
  "active": true
}
```

#### Response

The created policy object with a server-assigned `id`.

---

### PUT /api/knowledge/policies/filtering/:id

Updates an existing filter policy. Fired when the user saves the "Edit Policy" drawer.

#### Request

Same shape as POST, omit `id`.

#### Response

The updated policy object.

---

### DELETE /api/knowledge/policies/filtering/:id

Deletes a filter policy. Fired when the user confirms deletion.

#### Response

```json
{ "ok": true }
```

---

## Extraction Policy

### GET /api/knowledge/policies/extraction

Called on mount. Returns the current extraction policy. If empty/error, `BASE_EXTRACTION_POLICY` (a static multi-line string) is used as fallback.

#### Response

```json
{
  "content": "Extract all named entities including:\n• Organizations (ORG)...",
  "last_modified_by": "platform-admin",
  "last_modified_at": "2026-05-20 09:00 UTC"
}
```

The UI shows this as a read-only code block by default. Clicking "Edit" opens an editable textarea.

---

### PUT /api/knowledge/policies/extraction/custom

Saves a custom extraction policy. Fired when the user clicks "Save" in the edit mode.

#### Request

```json
{
  "content": "Extract all named entities including:\n• Organizations (ORG)...\n[custom additions]"
}
```

#### Response

```json
{ "ok": true }
```
