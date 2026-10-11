"""Business rules of the Tích hợp group (tab 1 UC-12-01…04, tab 4 A3)."""

from __future__ import annotations

import datetime
import os
import re
from collections.abc import Mapping
from urllib.parse import urlsplit

from services.api.kernel.problems import FieldError, Problem

# The MCP tools of tab 1 ("Công cụ MCP"); a client is granted a subset.
MCP_TOOLS = frozenset(
    {
        "ask_question",
        "get_metric_definition",
        "search_metrics",
        "run_skill",
        "get_published_result",
        "search_documents",
    }
)
# BR-48: a client acting under its own identity only gets tools that return no detail data
# ("tra định nghĩa, số tổng hợp công khai nội bộ"). Tab 8 leaves get_published_result,
# search_documents and run_skill as "Cần làm rõ", so they stay refused until the design says.
OWN_IDENTITY_TOOLS = frozenset({"get_metric_definition", "search_metrics"})


# The webhook targets the egress proxy lets through (UC-12-03 ngoại lệ): host suffixes,
# comma-separated, from the deploy. Unset means no target is allowed.
TARGETS_ENV = "API_WEBHOOK_TARGETS"

_EVENT_TYPE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")


def invalid(field: str, message: str, code: str = "invalid") -> Problem:
    return Problem(
        422,
        "validation_failed",
        message,
        errors=[FieldError(field=field, code=code, message=message)],
    )


def rule(message: str) -> Problem:
    return Problem(422, "business_rule_violated", message)


def check_tools(tools: list[str], identity_mode: str) -> list[str]:
    unknown = sorted(set(tools) - MCP_TOOLS)
    if unknown:
        raise invalid("tools", f"Unknown tools: {', '.join(unknown)}.")
    if identity_mode == "own_identity":
        detail = sorted(set(tools) - OWN_IDENTITY_TOOLS)
        if detail:
            raise rule(
                "BR-48: a client with its own identity only gets tools without detail data; "
                f"not {', '.join(detail)}."
            )
    return sorted(set(tools))


def twelve_months_after(moment: datetime.datetime) -> datetime.datetime:
    """The same instant a year later, as PostgreSQL's `+ interval '12 months'` (29/02 → 28/02)."""
    try:
        return moment.replace(year=moment.year + 1)
    except ValueError:
        return moment.replace(year=moment.year + 1, day=28)


def check_expiry(
    expires_at: datetime.datetime,
    now: datetime.datetime,
    granted_at: datetime.datetime | None = None,
) -> datetime.datetime:
    """BR-144: at most 12 months after the grant (the table's CHECK counts from created_at)."""
    if expires_at <= now:
        raise invalid("expires_at", "expires_at must be in the future.")
    if expires_at > twelve_months_after(granted_at or now):
        raise rule("BR-144: a client expires at most 12 months after it is granted.")
    return expires_at


def check_event_types(event_types: list[str]) -> list[str]:
    bad = [e for e in event_types if not _EVENT_TYPE.match(e)]
    if bad:
        raise invalid("event_types", f"Not event type names: {', '.join(bad[:3])}.")
    return sorted(set(event_types))


def check_target(url: str, environ: Mapping[str, str] | None = None) -> str:
    """https://<allowed host>[:443]/path, refused unless every parser reads the same host.

    urlsplit and an HTTP client can disagree on a URL with userinfo, a backslash, control or
    non-ASCII characters, or an odd port (https://allowed.vn\\@evil.com/ is evil.com to some);
    such URLs are refused outright rather than reasoned about, so the host checked here is the
    host the worker connects to. The egress proxy enforces the same list again (UC-12-03).
    """
    if not url.isascii() or any(c.isspace() or ord(c) < 32 or c in "\\@" for c in url):
        raise invalid("target_url", "target_url has characters a webhook URL may not contain.")
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host or parts.username or parts.password:
        raise invalid("target_url", "target_url must be an https:// URL.")
    try:
        port = parts.port
    except ValueError as exc:
        raise invalid("target_url", "target_url has an invalid port.") from exc
    if port not in (None, 443) or parts.netloc.lower() not in (host, f"{host}:443"):
        raise invalid("target_url", "target_url must use the default https port.")
    env = os.environ if environ is None else environ
    allowed = [
        s.strip().lower().lstrip(".") for s in env.get(TARGETS_ENV, "").split(",") if s.strip()
    ]
    if not any(host == a or host.endswith("." + a) for a in allowed):
        raise rule("The target is not in the egress proxy's list of allowed destinations.")
    return url
