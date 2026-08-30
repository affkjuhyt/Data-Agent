"""Caller authentication for the orchestrator.

Thin binding over backend/libs/shared/auth so the trust decision is the same one every
other service makes. Kong already has a route and the aeroflow-jwks plugin for
this service (deploy/docker/kong/kong-setup.sh §3d) — what was missing is that the
service never checked anything: `/api/orchestrate/stream` read the tenant from
a raw header and fell back to DEV_TENANT_ID, so any caller reaching port 8003
could act as any tenant.

ORCHESTRATOR_ALLOWED_SERVICES is empty by default: nothing calls the
orchestrator service-to-service, so the assertion path is closed here and only
the gateway route is open. Set it only if that changes.
"""
from libs.shared.auth import make_get_current_user

get_current_user = make_get_current_user("ORCHESTRATOR_ALLOWED_SERVICES")
