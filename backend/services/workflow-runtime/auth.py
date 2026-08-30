"""Caller authentication for workflow-runtime.

Thin binding over backend/libs/shared/auth so the trust decision is identical across
services. Kong already routes /api/conversations, /api/catalog, /api/traces …
here with the aeroflow-jwks plugin attached (deploy/docker/kong/kong-setup.sh §3a) —
what was missing is that this service never verified anything, so any caller
reaching port 8001 could run an agent turn as any tenant.

RUNTIME_ALLOWED_SERVICES defaults to "" (nothing): the orchestrator, which used
to present a signed assertion when calling POST /api/conversations/run on a
user's behalf, now runs inside this process (orchestrator/ package) and calls
the runtime directly. Every route is reached by a browser through Kong; the
assertion mechanism stays for a future service caller — name it in the env var
to open that path.
"""
from libs.shared.auth import make_get_current_user

get_current_user = make_get_current_user("RUNTIME_ALLOWED_SERVICES")
