"""Caller authentication for flow-builder.

Thin binding over backend/libs/shared/auth, so the trust decision is the same one every
other service makes.

Why it was missing here after orchestrator and workflow-runtime were fixed: the
audit named the *agent API* and those two services answer to that name, so the
port survey stopped there. flow-builder was published on 8002 with no
authentication of any kind and every route deriving its tenant from
`x_tenant_id or DEV_TENANT_ID` — a raw client header, defaulting to a real
tenant when absent. Anyone who could reach the host could list, edit, delete and
publish any tenant's agents and workflows by setting one header.

That is a strictly worse position than the read paths already closed: the
objects behind these routes are the agent definitions themselves, so writing
them changes what every subsequent conversation does — which prompts run, which
tools are granted, which databases are reached.

FLOW_BUILDER_ALLOWED_SERVICES is empty by default: nothing calls flow-builder
service-to-service, so only the gateway route is open. Set it only if that
changes.
"""
from libs.shared.auth import make_get_current_user

get_current_user = make_get_current_user("FLOW_BUILDER_ALLOWED_SERVICES")
