"""The api's business modules (ADR-28): one package per schema, each exposing a
`router`. A module talks to another only through that module's package
`__init__` (its public interface), never its internals; .importlinter holds
the line. Register a module by adding its router here.
"""

from __future__ import annotations

from fastapi import APIRouter

from services.api.modules import org, qa, workspace

ROUTERS: list[APIRouter] = [org.router, qa.router, workspace.router]
