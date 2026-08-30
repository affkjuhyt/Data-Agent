# kb-compute (Go)

CPU-bound, deterministic KB kernels — the Go side of the Go/Python split
(see [`../../M3-CAPACITY.md`](../../M3-CAPACITY.md) §5). No ML dependencies, so
it's Go: 10–50× faster than the Python equivalents and ~5 MB RSS.

## Owns
- **RRF fusion** (`/rrf-fuse`) — ported 1:1 from
  `backend/services/kb_backend/pipeline/light_rag/pipeline.py:rrf_merge`. ✅ done
- _next:_ BM25 scoring, entity canonicalization + de-dup
  (`kb-ingestion-worker/utils/entity_registry.py`), graph-edge building.

## Boundary
Python calls this over HTTP+JSON (scaffold) — target gRPC contract in
[`backend/libs/proto/kbcompute.proto`](../../libs/proto/kbcompute.proto). **Anti-ambiguity
rule:** kb-compute never writes KB tables; it takes inputs, returns ranked/derived
outputs, and the caller persists.

## Run / test
```bash
cd backend/services/kb-compute
go test ./...                 # kernel unit tests (mirror the Python semantics)
go run .                      # serves :8600
curl -s localhost:8600/health
curl -s -X POST localhost:8600/rrf-fuse -d '{
  "lists":[[{"point_id":"a"},{"point_id":"b"}],[{"point_id":"b"},{"point_id":"c"}]],
  "top_n":10}' | jq
```

## Cutover plan
1. A/B: call `/rrf-fuse` from `light_rag/pipeline.py` behind a flag; diff against
   the Python `rrf_merge` output on the same inputs (the Python fn is unit-tested,
   so parity is checkable).
2. Flip the flag once outputs match; delete the Python fusion.
