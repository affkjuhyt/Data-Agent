# Knowledge Hub — API Contract

Three inner tabs: **DATABASE**, **QDRANT**, **NEO4J**.

---

## DATABASE tab

### GET /api/knowledge/documents/:id/chunks

Called when the user selects a Gold-layer document (non-Warehouse) to inspect its chunks.

#### Response

```json
[
  {
    "id": "CHUNK_01",
    "title": "Deployment & Failover Architecture",
    "text": "Short preview text...",
    "versions": [
      {
        "version_number": "v1.2",
        "create_at": "2026-05-23 15:30:10 UTC",
        "status": "Active",
        "embedding_models": "text-embedding-3-small (1536d)",
        "entities": ["GlobalCorp", "Failover Cluster"],
        "intent": "Explain multi-cluster failover latency parameters",
        "text": "Full chunk text content..."
      }
    ]
  }
]
```

The UI renders a left panel (chunk list) and a right panel (selected version detail with entities, intent word map, and version text).

---

### PATCH /api/knowledge/documents/:id/chunks/:chunkId/activate

Sets one version as Active, demotes all others. Fired when user clicks "Set Active".

#### Request

```json
{ "version_number": "v1.1" }
```

#### Response

```json
{ "ok": true }
```

---

### POST /api/knowledge/documents/:id/chunks/:chunkId/versions

Creates a new chunk version. Fired from the "New Version" drawer.

#### Request

```json
{
  "version_number": "v1.3",
  "text": "Updated chunk text content..."
}
```

#### Response

The new version object (same shape as a `versions[]` item).

New version is created as `Inactive`. User must click "Set Active" to promote it.

---

### DELETE /api/data/documents/:id

Deletes a Gold-layer document from the database list.

#### Request / Response

No body. Returns `{ "ok": true }`.

---

### DELETE /api/knowledge/documents/:id/chunks/:chunkId

Deletes a specific chunk.

---

### DELETE /api/knowledge/documents/:id/chunks/:chunkId/versions/:versionNumber

Deletes a specific chunk version. Only allowed on `Inactive` versions.

---

## DATABASE tab — Warehouse documents

### GET /api/knowledge/warehouses/:id/configs

Called when the user selects a Warehouse document. Returns the list of config versions.

#### Response

```json
[
  {
    "id": "cfg-001",
    "name": "Analytics Core",
    "version": "v2.1",
    "createdAt": "2026-05-20 09:00 UTC",
    "status": "Active",
    "syncSchedule": "Every 6 hours",
    "tables": [
      {
        "id": "at-001",
        "schema": "PUBLIC",
        "tableName": "CUSTOMER_EVENTS",
        "rowCount": 2400000,
        "columns": 24
      }
    ]
  }
]
```

Left panel: config list (name, version, status, table count).  
Right panel: connection details + table list for the selected config.

---

### PATCH /api/knowledge/warehouses/:id/configs/:configId/activate

Promotes a Draft config to Active. Fired when user clicks "Set Active".

#### Request

```json
{}
```

#### Response

```json
{ "ok": true }
```

---

### POST /api/knowledge/warehouses/:id/configs

Creates a new config. Fired from the "New Config" drawer.

#### Request

```json
{
  "name": "Analytics Core v3",
  "version": "v3.0",
  "syncSchedule": "Every 6 hours",
  "status": "Draft",
  "tables": [
    { "id": "at-001", "schema": "PUBLIC", "tableName": "CUSTOMER_EVENTS", "rowCount": 2400000, "columns": 24 }
  ]
}
```

#### Response

The new config object (same shape as a GET item). Created as Draft.

---

### DELETE /api/knowledge/warehouses/:id/configs/:configId

Deletes a config. Only allowed on non-Active configs.

---

### DELETE /api/knowledge/warehouses/:id/configs/:configId/tables/:tableId

Removes a single table from a config's table set.

---

## QDRANT tab

### GET /api/knowledge/qdrant/collections

Called on component mount. Returns all Qdrant vector collections.

#### Response

```json
[
  {
    "id": "qd-001",
    "name": "aeroflow_enterprise_v2",
    "points": 892541,
    "active": true,
    "dimensions": 1024,
    "distance": "Cosine",
    "indexed": 100,
    "embedding_model": "text-embedding-3-small"
  }
]
```

Collections are shown as cards. Inactive collections are visually muted.

---

### PATCH /api/knowledge/qdrant/collections/:id

Toggles the `active` field. Fired when user clicks "Set Active" / "Set Inactive" inside the detail drawer.

#### Request

```json
{ "active": false }
```

#### Response

The updated collection object (same full shape as the GET item).

---

### POST /api/knowledge/qdrant/collections/:id/search

Runs semantic search on the collection. Fired when user submits a natural-language query.

#### Request

```json
{ "query": "How does the failover cluster handle latency spikes?" }
```

#### Response — top 5 matching points

```json
[
  {
    "point_id": "CHUNK_01_v1.2",
    "score": 0.94,
    "summary": "Multi-cluster failover strategy with 400ms latency threshold...",
    "entities": ["GlobalCorp", "Failover Cluster"],
    "intent": ["failover", "latency", "retrieval"]
  }
]
```

Only available when the collection `active === true`. Inactive collections show a disabled state.

---

## NEO4J tab

No HTTP calls. The query builder constructs a Cypher string client-side using `buildCypher(startNode, steps)` and displays it as read-only output. The graph visualization is rendered from `FALLBACK_NODES`, `FALLBACK_EDGES`, and `FALLBACK_REL_LABELS` static data.
