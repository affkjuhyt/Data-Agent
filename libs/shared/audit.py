"""Data-access audit — who read what, when, and what came back.

The audit pipeline before this covered *logins only*: audit-bridge polls
Keycloak auth events into Kafka and audit-consumer writes them to `audit_logs`.
Nothing recorded that a user ran a query, which table it touched, or how many
rows it returned. After an incident there was no answer to "who read this data",
and the SQL existed only inside Langfuse traces — an observability store with
sampling and retention, not an audit log.

Two rules shape everything here.

**It must never fail the turn.** An audit sink being down is an operational
problem, not a reason to refuse a user's question. Every function below
swallows its own errors and logs; `emit` is fire-and-forget. The trade-off is
explicit: this records access, it does not gate it. A control that must *block*
on an unavailable audit log belongs at the policy layer, not here.

**It must not become a PII store.** A WHERE clause routinely contains exactly
the personal data the audit exists to protect — `WHERE email = 'a@b.com'`.
Storing raw SQL would turn the audit table into the largest unmasked copy of
that data in the platform. So the statement is redacted to its shape and a hash
of the original is kept alongside, which is enough to correlate repeat queries
and to prove which statement ran without reproducing its arguments.

Pure: no Kafka import, no database driver. `libs/` stays a leaf (.importlinter);
the service installs a transport via `set_sink`.
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

TOPIC = "audit.data.access"
EVENT_TYPE = "data.access"

# Actions worth distinguishing when reading the trail back. Kept small and
# closed — an open string field turns into six spellings of the same thing.
ACTION_SQL_QUERY = "sql_query"
ACTION_SCHEMA_INTROSPECT = "schema_introspect"
ACTION_KB_RETRIEVE = "kb_retrieve"
ACTION_CODE_EXEC = "code_exec"

DECISION_ALLOW = "allow"
DECISION_DENY = "deny"
DECISION_ERROR = "error"


# ── redaction ──────────────────────────────────────────────────────────────
# Order matters: strings first, so digits inside a quoted literal are already
# gone before the numeric pass runs.
_SQL_STRING_RE = re.compile(r"'(?:[^']|'')*'")
_SQL_NUMBER_RE = re.compile(r"(?<![A-Za-z_0-9.])-?\d+(?:\.\d+)?(?![A-Za-z_0-9.])")
_DOLLAR_QUOTED_RE = re.compile(r"\$([A-Za-z_]*)\$.*?\$\1\$", re.DOTALL)
_LINE_COMMENT_RE = re.compile(r"--[^\n]*")
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_WS_RE = re.compile(r"\s+")

_MAX_REDACTED_CHARS = 4000


def redact_sql(sql: str) -> str:
    """Reduce a statement to its shape: literals become `?`.

    Keeps enough to answer "which tables, which columns, which predicates" —
    the questions an audit is for — while removing the values, which are the
    part most likely to *be* the personal data under review.

    Comments go too. They carry no structure worth auditing and are a favourite
    place for a prompt-injection payload to end up, which is not something to
    replay into a log viewer.
    """
    if not sql:
        return ""
    text = _DOLLAR_QUOTED_RE.sub("?", sql)
    text = _BLOCK_COMMENT_RE.sub(" ", text)
    text = _LINE_COMMENT_RE.sub(" ", text)
    text = _SQL_STRING_RE.sub("?", text)
    text = _SQL_NUMBER_RE.sub("?", text)
    text = _WS_RE.sub(" ", text).strip()
    if len(text) > _MAX_REDACTED_CHARS:
        text = text[:_MAX_REDACTED_CHARS] + " …[truncated]"
    return text


def statement_hash(sql: str) -> str:
    """SHA-256 of the ORIGINAL statement.

    Lets two runs of the same query be correlated, and lets a specific
    statement be proven to have run, without the audit table holding the text.
    Hashing the redacted form instead would collide every query that differs
    only by its arguments — which is exactly the distinction an investigation
    needs.
    """
    return hashlib.sha256((sql or "").encode("utf-8")).hexdigest()


# ── event ──────────────────────────────────────────────────────────────────

@dataclass
class DataAccessEvent:
    """One record of one access. Fields are flat and pre-typed so the consumer
    is a straight INSERT with no interpretation of its own."""

    tenant_id: str
    user_id: str
    action: str
    decision: str

    user_roles: list[str] = field(default_factory=list)
    # "gateway" | "service:<name>" — a request a service made on a user's behalf
    # is not the same as one the user made directly, and an investigation needs
    # to tell them apart.
    auth_source: str = ""
    # Logical target: connection name, knowledge base, warehouse.
    target: str = ""
    # Physical objects touched, when known (parsed table names, doc ids).
    target_objects: list[str] = field(default_factory=list)
    statement_redacted: str = ""
    statement_sha256: str = ""
    # Set when the Semantic Layer produced the SQL — "revenue_net@3".
    metric_ref: str = ""
    row_count: int | None = None
    latency_ms: int | None = None
    reason: str = ""
    # Ties the record to the agent turn and its trace.
    session_id: str = ""
    agent_id: str = ""
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    occurred_at: float = field(default_factory=time.time)
    event_type: str = EVENT_TYPE

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


Sink = Callable[[str, str, dict[str, Any]], None]

_sink: Sink | None = None


def set_sink(sink: Sink | None) -> None:
    """Install the transport (topic, key, value) -> None.

    workflow-runtime wires this to its existing Kafka producer at startup. Left
    unset — in a test, or a service with no broker — `emit` logs and drops,
    which keeps this module usable everywhere without a Kafka dependency.
    """
    global _sink
    _sink = sink


def emit(event: DataAccessEvent) -> None:
    """Publish one event. Never raises."""
    if _sink is None:
        logger.debug("audit | no sink configured, dropping %s event", event.action)
        return
    try:
        # Keyed by tenant so one tenant's events stay ordered within a
        # partition — an investigation reads a single tenant's trail and wants
        # it in the order it happened.
        _sink(TOPIC, event.tenant_id or "unknown", event.to_dict())
    except Exception as e:
        logger.warning("audit | failed to emit %s event: %s", event.action, e)


def record_sql_access(
    *,
    tenant_id: str,
    user_id: str,
    user_roles: list[str] | None = None,
    auth_source: str = "",
    target: str = "",
    sql: str = "",
    # Reading information_schema discloses the shape of a tenant's warehouse —
    # a real access — but filing it as a business query would drown the trail,
    # so the caller says which it is.
    action: str = ACTION_SQL_QUERY,
    decision: str = DECISION_ALLOW,
    row_count: int | None = None,
    latency_ms: int | None = None,
    reason: str = "",
    metric_ref: str = "",
    session_id: str = "",
    agent_id: str = "",
) -> None:
    """Record one SQL access. Convenience wrapper — redacts and hashes for you
    so no call site has to remember to."""
    emit(DataAccessEvent(
        tenant_id=tenant_id,
        user_id=user_id or "anonymous",
        user_roles=list(user_roles or []),
        auth_source=auth_source,
        action=action,
        decision=decision,
        target=target,
        target_objects=extract_tables(sql),
        statement_redacted=redact_sql(sql),
        statement_sha256=statement_hash(sql),
        metric_ref=metric_ref,
        row_count=row_count,
        latency_ms=latency_ms,
        reason=reason,
        session_id=session_id,
        agent_id=agent_id,
    ))


# Table names after FROM / JOIN. Deliberately a heuristic, not a parser: this
# annotates the record so "which queries touched `salaries`?" is answerable
# without a full scan, and a miss degrades the search, not the audit — the
# redacted statement is still there. A real parser (sqlparse/sqlglot) belongs
# here once one is a dependency of every emitting service.
_TABLE_RE = re.compile(r"\b(?:from|join)\s+([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)", re.I)


def extract_tables(sql: str) -> list[str]:
    if not sql:
        return []
    seen: dict[str, None] = {}
    for match in _TABLE_RE.finditer(sql):
        name = match.group(1).lower()
        # sandbox-executor wraps every statement as `SELECT * FROM (...) AS
        # _sandboxed_query`; that alias is ours, not the tenant's data.
        if name == "_sandboxed_query":
            continue
        seen.setdefault(name, None)
    return list(seen)
