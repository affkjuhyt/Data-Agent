"""Shared V2 contracts (NOP-150): wire DTOs, errors and the event envelope.

A leaf package: it imports pydantic and libs.indicator only, never a service
(.importlinter `contracts-is-a-leaf`), so platform_workers, workflow_runtime,
kb_backend and worker_agent all consume it without importing each other.

CONTRACT_VERSION is semver for the package as a whole; each model carries its own
SCHEMA_VERSION. The rules for bumping either are in COMPATIBILITY.md.
"""

from __future__ import annotations

CONTRACT_VERSION = "1.2.0"
