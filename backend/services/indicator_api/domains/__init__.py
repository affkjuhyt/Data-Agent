"""Business definitions shared by every part of V2 (P0-09).

Import rule, enforced by .importlinter: nothing here imports FastAPI, a storage
client or `services.indicator_api.api` / `.adapters` — the definitions must not
depend on how they are stored or transported (P0-09 BR-5).
"""
