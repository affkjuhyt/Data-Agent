"""indicator-api: the V2 Observation / Knowledge / Governance API (ADR-V2-22, ADR-V2-36).

One deployable, split by domain so it can be cut into several after the POC:
`domains/` holds the shared business definitions (plain Pydantic, no FastAPI, no
storage client), `api/` the FastAPI routers, `adapters/` the storage and messaging
clients. P0-09 in docs/v2/05 is the contract: these definitions are the single
place every worker and screen reads a number's shape from.
"""
