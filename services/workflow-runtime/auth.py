"""Caller authentication for workflow-runtime.

Thin binding over libs/shared/auth so the trust decision is identical across
services. Kong already routes /api/conversations, /api/catalog, /api/traces …
here with the aeroflow-jwks plugin attached (infra/kong/kong-setup.sh §3a) —
what was missing is that this service never verified anything, so any caller
reaching port 8001 could run an agent turn as any tenant.

RUNTIME_ALLOWED_SERVICES defaults to "orchestrator": the orchestrator calls
POST /api/conversations/run on a user's behalf and cannot carry their Keycloak
token, so it presents a signed assertion instead. Every other route is reached
by a browser through Kong.
"""
from libs.shared.auth import make_get_current_user

get_current_user = make_get_current_user("RUNTIME_ALLOWED_SERVICES", "orchestrator")
