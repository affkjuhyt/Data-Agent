# Contract v1 mock API (NOP-151)

A stateless HTTP mock that answers with Contract v1 models (`backend/libs/contracts/v1`) read from
`tests/fixtures/contracts/mock/`. FE, Data and Agent develop and test against it until indicator-api
(NOP-162) serves the same models. It simulates **no** backend logic: no as-of, no windows, no
permissions, no storage.

## Run it

```bash
make mock-api                          # kb-backend image, http://localhost:8090
python -m tests.contract_mock          # Python 3.11+ with PYTHONPATH=backend:. (repo root)
python -m tests.contract_mock --port 9000
```

In a Python test, no server is needed:

```python
from fastapi.testclient import TestClient
from tests.contract_mock import create_app

client = TestClient(create_app())
```

The frontend dev server may call it straight from the browser (CORS is open for `localhost`). Point the
base URL at the mock; there is no path prefix, so switching to the real API changes only the base URL.

## Routes

Paths are the mock's own; Contract v1 fixes shapes, not paths. NOP-162 publishes the real ones.

| Route | Request | Answer (Contract v1 model) |
| --- | --- | --- |
| `GET /entities`, `GET /entities/{entity_id}` | — | `list[EntityRef]` / `EntityRef` |
| `GET /series`, `GET /series/{series_key}` | — | `list[SeriesRef]` / `SeriesRef` |
| `GET /series/{series_key}/observations` | — | `list[Observation]`: every version of every period in the fixture |
| `GET /query?series_key=A&series_key=B` | at least one `series_key` | `list[Observation]` of those series |
| `GET /review/cases`, `GET /review/cases/{case_id}` | — | `list[ReviewCaseRef]` / `ReviewCaseRef` |
| `POST /review/cases/{case_id}/approve`, `…/reject` | `VersionedWrite` | `ReviewCaseRef` with `version + 1`, or 409 |
| `GET /capabilities` | — | `list[CapabilityRef]` |

- **Unknown id** on any route → 404 `UNRESOLVED_ENTITY`. Contract v1 has no "not found" code; resolving
  a series is the same step as resolving its entity (12-numeric-core NC-T6).
- **Version conflict**: `expected_version` other than the case's `version` → 409 `VERSION_CONFLICT` with
  `current_version`. Nothing is stored, so the same request always gets the same answer.
- **Query** returns observations only; operator results (`compare_period`, …) wait for a QuerySpec
  contract.
- A request the routes cannot parse (missing `expected_version`, no `series_key`) → FastAPI's 422.

## Standard errors: `X-Mock-Error`

Send `X-Mock-Error: <ErrorCode>` on any route to get `errors/<ErrorCode>.json` with the status
`HTTP_STATUS` fixes for it, e.g. `STALE_SOURCE` → 400, `ACCESS_DENIED` → 403, `NO_DATA_FOR_PERIOD` → 404.
This is the only way to get the codes that need backend logic. An unknown code → 422.

```bash
curl -H "X-Mock-Error: SERIES_NOT_APPROVED" "localhost:8090/query?series_key=EIA:RBRTE:D"
```

## Fixtures (`tests/fixtures/contracts/mock/`)

| Path | Use |
| --- | --- |
| `happy/*.json` | What the routes answer: Brent `EIA:RBRTE:D` (22/09 revised, `effective_version` 1 and 2), WTI `EIA:RWTC:D`, gold `SJC:SJC_BAR:HCM:SELL:D`; two review cases; four capabilities |
| `errors/<ErrorCode>.json` | One `ErrorBody` per code, answered on `X-Mock-Error` |
| `version_conflict/request.json`, `response.409.json` | A stale `VersionedWrite` on case `2e3f4a5b-…a06` (version 3) and the 409 it gets |
| `events/<topic>.v1.json` | One valid `EventEnvelope` per topic that has a payload model in v1: `artifact.stored`, `extraction.completed`, `mapping.proposed`, `source.sla.breached`. Read them with `libs.contracts.v1.parse_envelope` |

Invalid envelopes and DTOs live in `tests/fixtures/contracts/invalid/` (NOP-150).

The fixtures are validated against the contract by `tests/unit/contracts/test_mock_fixtures.py`, and the
mock refuses to start on one that drifted. When Contract v1 changes, fix the fixture the test names.
