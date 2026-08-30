# Warehouse Wizard — API Contract

The Warehouse Wizard connects a Snowflake or Databricks data warehouse to the knowledge pipeline and registers it as a Gold-layer document.

---

## Wizard flow (4 steps)

| Step | Action | Network |
|---|---|---|
| 1 | Choose warehouse type (Snowflake / Databricks) | None |
| 2 | Enter credentials + connection name | Simulated connection test (UI-only delay) |
| 3 | Select tables to sync | Simulated table discovery (UI-only delay) |
| 4 | Review and confirm | `POST /api/knowledge/documents/:id/configs` |

The wizard does **not** POST credentials to the server mid-flow. All data is held in local state until Step 4.

---

## Step 4 — Create connection

### addDocument (AppStateContext)

Before the POST, the wizard calls `addDocument()` to register the warehouse as a Gold-layer document in global state:

```typescript
addDocument({
  name: "Snowflake Production Analytics DW",
  layer: "GOLD",
  author: "platform-admin",
  metadata: {
    tenant: "GlobalCorp",
    type: "Warehouse/snowflake",   // or "Warehouse/databricks"
    language: "N/A",
    warehouseType: "snowflake",
  }
});
```

---

### POST /api/knowledge/documents/:id/configs

Persists the initial config version for the new warehouse connection.

`:id` is a client-generated ID (`wh-<timestamp>`).

#### Request

```json
{
  "version_number": "v1.0",
  "connection": {
    "account": "myorg.us-east-1.snowflakecomputing.com",
    "username": "data_engineer",
    "database": "ANALYTICS_DB",
    "schema": "PUBLIC",
    "warehouse": "COMPUTE_WH",
    "role": "DATA_ENGINEER"
  },
  "tables": [
    {
      "name": "ORDERS",
      "schema": "PUBLIC",
      "rowCount": "142,000",
      "description": "All customer orders with line items"
    },
    {
      "name": "CUSTOMERS",
      "schema": "PUBLIC",
      "rowCount": "45,000",
      "description": "Customer master data"
    }
  ]
}
```

For **Databricks**, the `connection` object uses:

```json
{
  "host": "adb-123456.azuredatabricks.net",
  "httpPath": "/sql/1.0/warehouses/abc123",
  "catalog": "ml_features",
  "schema": "feature_store"
}
```

#### Response

```json
{ "ok": true }
```

On success the wizard closes and the user is navigated to the Knowledge Hub tab where the new warehouse document is visible.

---

## Credential field reference

### Snowflake

| Field key | Required | Secret |
|---|---|---|
| `name` | Yes | No |
| `account` | Yes | No |
| `username` | Yes | No |
| `password` | Yes | Yes (masked) |
| `database` | Yes | No |
| `schema` | No | No |
| `warehouse` | Yes | No |
| `role` | No | No |

### Databricks

| Field key | Required | Secret |
|---|---|---|
| `name` | Yes | No |
| `host` | Yes | No |
| `httpPath` | Yes | No |
| `accessToken` | Yes | Yes (masked) |
| `catalog` | Yes | No |
| `schema` | No | No |

Secret fields (`password`, `accessToken`) are sent to the backend but never displayed in the UI after entry.
