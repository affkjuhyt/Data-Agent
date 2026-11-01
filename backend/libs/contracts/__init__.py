"""Shared V2 contracts (NOP-150): wire DTOs, errors and the event envelope.

A leaf package: it imports pydantic and libs.indicator only, never a service
(.importlinter `contracts-is-a-leaf`), so any service consumes it without importing
another (worker_agent does today).

CONTRACT_VERSION is semver for the package as a whole; each model carries its own
SCHEMA_VERSION. The rules for bumping either are in COMPATIBILITY.md.
"""

from __future__ import annotations

CONTRACT_VERSION = "1.2.0"
