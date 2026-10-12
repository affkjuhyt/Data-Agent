"""The api's business modules (ADR-28): one package per schema, each exposing a
`router`. A module talks to another only through that module's package
`__init__` (its public interface), never its internals; .importlinter holds
the line. Register a module by adding its router here.
"""

from __future__ import annotations

from fastapi import APIRouter

from services.api.modules import (
    acquire,
    agent,
    audit,
    catalog,
    conflict,
    context,
    document,
    eval,
    exec,
    forecast,
    governance,
    inbox,
    ingest,
    integration,
    monitor,
    notify,
    org,
    pack,
    period,
    pipeline,
    qa,
    skill,
    workspace,
)

ROUTERS: list[APIRouter] = [
    org.router,
    qa.router,
    workspace.router,
    inbox.router,
    eval.router,
    agent.router,
    skill.router,
    catalog.router,
    ingest.router,
    document.router,
    pipeline.router,
    context.router,
    conflict.router,
    governance.router,
    monitor.router,
    notify.router,
    pack.router,
    period.router,
    audit.router,
    integration.router,
    acquire.router,
    forecast.router,
    exec.router,
]
