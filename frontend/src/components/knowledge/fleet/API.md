# Fleet Overview — API Contract

## GET /api/fleet/stats

Loads the Fleet Overview dashboard. Called once on component mount.

### Request

No body. No query params.

```
GET /api/fleet/stats
```

### Response

```json
{
  "content": {
    "documents": 1240,
    "web": 87,
    "media": 34,
    "warehouses": 3
  },
  "qdrant_collections": 4,
  "neo4j_nodes": 48291,
  "neo4j_relationships": 102847,
  "unresolved_conflict_batches": 12
}
```

### How the UI uses it

| Field | Displayed in |
|---|---|
| `content.documents` | "Documents" stat card |
| `content.web` | "Web Sources" stat card |
| `content.media` | "Media" stat card |
| `content.warehouses` | "Warehouses" stat card |
| `qdrant_collections` | "Qdrant Collections" stat card |
| `neo4j_nodes` | "Neo4j Nodes" stat card |
| `neo4j_relationships` | "Relationships" stat card |
| `unresolved_conflict_batches` | "Unresolved Conflicts" stat card (turns red when > 0) |

All stat cards show a spinner while loading, then replace it with the number once data arrives.
