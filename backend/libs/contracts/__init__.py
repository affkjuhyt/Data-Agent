"""Shared wire contracts: the business errors and the agent's verification frame (v1).

A leaf package: it imports pydantic only, never a service (.importlinter `contracts-is-a-leaf`),
so any service consumes it without importing another. The V2 observation, ingest and event
shapes, their published schemas and the compatibility gate were removed with the V2 numeric core
(GEN-42, 01/11/2026); they are in git history.
"""

from __future__ import annotations
