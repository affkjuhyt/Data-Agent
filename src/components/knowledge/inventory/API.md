# Inventory (Data Layers) — API Contract

All endpoints are relative to `/api`.

---

## AssetInventory — document list

The document list is **not fetched directly** — it is read from `AppStateContext.documents` (global state populated at app boot). The following mutation endpoints are called inline from this component.

### PATCH /api/data/documents/:id — promote layer

Triggered when the user clicks "Process to Silver" or "Process to Gold", or runs a batch process.

#### Request

```json
{
  "layer": "SILVER",
  "status": "EMBEDDING"
}
```

Or for Bronze → Gold (direct):

```json
{
  "layer": "GOLD",
  "status": "PUBLISHED"
}
```

#### Response

```json
{ "ok": true }
```

After success the UI calls `updateDocument(id, { layer, status })` to update global state and moves the row to the new layer tab.

---

### DELETE /api/data/documents/:id — delete document

Triggered when the user confirms the inline delete prompt on a row.

#### Request

No body.

#### Response

```json
{ "ok": true }
```

After success the UI calls `deleteDocument(id)` to remove it from global state.

---

## AssetDetailWorkspace — asset detail drawer

Opened when the user clicks "Inspect" on any row. The active tab determines which endpoint is called.

---

### GET /api/knowledge/documents/:id/chunks — CHUNKS tab

Called when the CHUNKS tab is active and the document is SILVER or GOLD layer.

#### Response

```json
[
  {
    "id": "CHUNK_01",
    "title": "Deployment & Failover Architecture",
    "text": "Short preview text of the chunk...",
    "versions": [
      {
        "version_number": "v1.2",
        "create_at": "2026-05-23 15:30:10 UTC",
        "status": "Active",
        "embedding_models": "text-embedding-3-small (1024)",
        "entities": ["GlobalCorp", "Failover Cluster"],
        "intent": "Explain multi-cluster failover latency-target parameters",
        "text": "Full text of this version..."
      }
    ]
  }
]
```

The UI renders a left-panel chunk list and a right-panel detail view showing the active version's parameters and text.

---

### PATCH /api/knowledge/documents/:id/configs/:configId/activate — activate a version

Called when the user clicks "Set Active" on an inactive chunk version or warehouse config.

#### Request

```json
{}
```

#### Response

```json
{ "ok": true }
```

The UI optimistically flips all versions' `status` fields (active one becomes `"Active"`, others become `"Inactive"`).

---

### GET /api/knowledge/documents/:id/tables — TABLES tab

Called when the TABLES tab is active (Doc/Web documents only).

#### Response

```json
[
  {
    "id": "tbl-001",
    "name": "Revenue Summary",
    "description": "Quarterly revenue grouped by region",
    "columns": [
      { "name": "region",  "type": "VARCHAR", "nullable": false },
      { "name": "revenue", "type": "NUMERIC", "nullable": true  }
    ],
    "rows": [
      { "region": "EMEA", "revenue": "4200000" },
      { "region": "APAC", "revenue": "3100000" }
    ]
  }
]
```

---

### PATCH /api/knowledge/documents/:id/tables/:tableId/rows/:rowIndex — edit a cell

Called when the user saves an inline cell edit in the TABLES tab.

#### Request

```json
{ "column": "revenue", "value": "4800000" }
```

#### Response

```json
{ "ok": true }
```

---

### GET /api/knowledge/documents/:id/configs — CONFIGS tab

Called when the CONFIGS tab is active (Warehouse documents only).

#### Response

```json
[
  {
    "id": "cfg-001",
    "version_number": "v2.1",
    "status": "Active",
    "created_at": "2026-05-20 09:00 UTC",
    "connection": {
      "platform": "Snowflake",
      "host": "globalcorp-prod.snowflakecomputing.com",
      "database": "ANALYTICS_DB"
    },
    "tables": [
      {
        "name": "CUSTOMER_EVENTS",
        "schema": "PUBLIC",
        "rowCount": "2,400,000",
        "description": "Click and purchase events per customer session"
      }
    ]
  }
]
```

---

### POST /api/knowledge/documents/:id/configs — create new config version

Called when the user completes the "New Config" flow inside the CONFIGS tab.

#### Request

```json
{
  "version_number": "v3.0",
  "connection": {
    "platform": "Snowflake",
    "host": "globalcorp-prod.snowflakecomputing.com"
  },
  "tables": [
    {
      "name": "ORDERS",
      "schema": "PUBLIC",
      "rowCount": "142,000",
      "description": "All orders with line items"
    }
  ]
}
```

#### Response

Returns the created config object (same shape as the GET item).
