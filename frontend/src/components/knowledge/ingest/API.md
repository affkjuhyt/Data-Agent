# Ingest (Add Data) — API Contract

The Ingestion Wizard adds new Bronze-layer documents to the knowledge pipeline.

---

## No direct HTTP calls

The IngestionWizard does **not** call the server directly. On completion it calls `addDocument()` from `AppStateContext`, which adds the document to global in-memory state.

The backend should handle persistence when the global state is synced (e.g., via a POST on the context provider level), or you may choose to add a direct POST here in future iterations.

---

## addDocument payload (sent to AppStateContext)

```typescript
addDocument({
  name: "Policy_Framework_v2.pdf",
  layer: "BRONZE",
  author: "platform-admin",
  metadata: {
    tenant: "GlobalCorp",
    type: "Doc/pdf",        // see type table below
    language: "English",
  }
});
```

### Source type → metadata.type mapping

| Wizard choice | `metadata.type` value |
|---|---|
| Document (PDF) | `Doc/pdf` |
| Document (Word) | `Doc/docx` |
| Document (Markdown) | `Doc/md` |
| Image | `Image/png` or `Image/jpeg` |
| Video | `Video/mp4` |
| Web URL | `web` |

### Wizard steps

1. **Select Source Type** — choose video / image / document / web
2. **Configure Properties** — fill in name, author, date, language, upload file or enter URL

On finish the document is inserted at Bronze layer with status `PENDING`.

---

## Future: POST /api/data/documents (suggested)

If you want server-side persistence on ingest, add this endpoint:

### Request

```json
{
  "name": "Policy_Framework_v2.pdf",
  "layer": "BRONZE",
  "author": "platform-admin",
  "metadata": {
    "tenant": "GlobalCorp",
    "type": "Doc/pdf",
    "language": "English"
  }
}
```

### Response

```json
{
  "id": "DOC-1748200000000",
  "name": "Policy_Framework_v2.pdf",
  "layer": "BRONZE",
  "status": "PENDING",
  "version": "v1.0",
  "lastUpdated": "2026-05-25",
  "author": "platform-admin",
  "metadata": {
    "tenant": "GlobalCorp",
    "type": "Doc/pdf",
    "language": "English"
  }
}
```
