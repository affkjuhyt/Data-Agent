"""indicator-api business logic, one package per bounded context.

Plain Python and Pydantic only: no FastAPI, no database / Kafka / object-store
client. `.importlinter` (`indicator-domains-are-pure`) fails CI otherwise —
routes live in `api/`, I/O in `adapters/`.
"""
