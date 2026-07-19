# kb-gateway (Go)

The high-CCU **edge / BFF** for the KB user-facing tier — the Go side of the
Go/Python split (see [`../../M3-CAPACITY.md`](../../M3-CAPACITY.md) §5). Sits
behind Kong (which does JWT/JWKS verification) and in front of the Python
business services.

## Why Go here
Goroutines = thousands of cheap concurrent connections at ~10× less RAM/request
than Python workers. This is the tier that determines CCU, so it's the one worth
moving off Python.

## Owns
- identity **passthrough** (`X-Kong-Verified` / `X-User-*` / `Authorization`)
- request-id injection + access logging
- reverse-proxy to `kb-backend`
- **fan-out**: composite endpoints that call kb-backend + kb-compute concurrently
  and merge — the reason a BFF exists (scaffold marks the seam; ✅ proxy done)

## Does NOT own
Business logic, ORM, validation, RLS — those stay in Python `kb-backend`. The
gateway forwards; it never talks to the DB directly (anti-ambiguity rule).

## Run
```bash
cd services/kb-gateway
KB_BACKEND_URL=http://localhost:8050 KB_GATEWAY_ADDR=:8090 go run .
curl -s localhost:8090/health
# proxied (send the identity Kong would inject):
curl -s localhost:8090/api/knowledge/knowledge-bases \
  -H 'X-Kong-Verified: true' -H 'X-Tenant-Id: a0000000-0000-0000-0000-000000000001' \
  -H 'X-User-Roles: platform-admin'
```

Env: `KB_GATEWAY_ADDR` (`:8000`), `KB_BACKEND_URL` (`http://kb-backend:8000`),
`KB_COMPUTE_URL` (fan-out target).
