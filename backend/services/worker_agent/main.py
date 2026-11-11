"""worker-agent: the agent-execution block of ADR-28.

Run: `python -m services.worker_agent.main` with backend/ on PYTHONPATH.

Polls its event source, hands each event to the handler of its type, and acknowledges the
batch only when every handler returned; a failing event is logged and left for the next
claim (handlers are idempotent).

It handles `question.received` from qa.outbox, `hitl.resolved` from inbox.outbox,
`writeback.submitted` from pipeline.outbox, and `hitl.requested` from the outbox of every
schema that has one, in the main database and in the audit database (whose outbox is read
through its own connection key, svc_outbox_relay_audit).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import time
import uuid
from collections.abc import Mapping

from sqlalchemy import select, text

from libs import language
from libs.dataagent_db import scoped_session, unscoped_session
from libs.dataagent_db.models import org
from services.worker_agent import (
    agent_evals,
    audit_exports,
    derived,
    eval_runs,
    exports,
    forecasting,
    hitl,
    liked,
    metric_changes,
    monitor_backtests,
    periods,
    pipelines,
    policy_backtests,
    prep,
    private,
    product_use,
    proposals,
    quality,
    query_runs,
    questions,
    replay,
    report_runs,
    resolutions,
    rest_tries,
    reviews,
    runs,
    schema_scans,
    semantic_review,
    session_reports,
    skill_evals,
    skill_runs,
    source_tests,
    tiles,
    uploads,
    writeback_diffs,
)
from services.worker_agent.channels import gateway as channels
from services.worker_agent.documents import extraction, indexed
from services.worker_agent.documents import sync as document_sync
from services.worker_agent.events import EventSource, Handler, OutboxTableSource
from services.worker_agent.providers.registry import ProviderRegistry
from services.worker_agent.schedules import clock

logger = logging.getLogger(__name__)

POLL_SECONDS = float(os.environ.get("WORKER_AGENT_POLL_SECONDS", "0.5"))
BATCH = int(os.environ.get("WORKER_AGENT_BATCH", "10"))


# The organisations' locales, read once per process lifetime and kept LOCALE_TTL_S: a locale
# changes seldom, and an event should not pay a query for it.
LOCALE_TTL_S = 600.0
_locales: dict[uuid.UUID, tuple[float, str | None]] = {}


async def org_locale(org_id: uuid.UUID | None) -> str | None:
    if org_id is None:
        return None
    cached = _locales.get(org_id)
    if cached is not None and time.monotonic() - cached[0] < LOCALE_TTL_S:
        return cached[1]
    try:
        async with scoped_session("svc_qa", org_id) as session:
            locale = await session.scalar(
                select(org.Organization.locale).where(org.Organization.id == org_id)
            )
    except Exception:  # noqa: BLE001 - presentation only: the default locale, never a lost event
        logger.warning("worker-agent | locale of org %s unread, using the default", org_id)
        return None
    _locales[org_id] = (time.monotonic(), locale)
    return locale


async def run_once(source: EventSource, handlers: Mapping[str, Handler], batch: int = BATCH) -> int:
    """Claim one batch and handle it; returns how many events were handled."""
    async with source.claim(batch) as events:
        for event in events:
            # What the handler writes for a person (a number, a chip, a refusal's sentence) is in
            # the organisation's locale (GEN-41).
            with language.use_locale(await org_locale(event.org_id)):
                # Within one claim a failure aborts the batch: nothing is acknowledged and the
                # events come back on the next claim, already-handled ones as no-ops.
                await handlers[event.event_type](event)
        return len(events)


HANDLERS: dict[str, Handler] = {
    **channels.HANDLERS,
    **questions.HANDLERS,
    **replay.HANDLERS,
    **report_runs.HANDLERS,
    **hitl.HANDLERS,
    **resolutions.HANDLERS,
    **source_tests.HANDLERS,
    **schema_scans.HANDLERS,
    **query_runs.HANDLERS,
    **tiles.HANDLERS,
    **session_reports.HANDLERS,
    **uploads.HANDLERS,
    **proposals.HANDLERS,
    **exports.HANDLERS,
    **liked.HANDLERS,
    **runs.HANDLERS,
    **agent_evals.HANDLERS,
    **skill_runs.HANDLERS,
    **policy_backtests.HANDLERS,
    **eval_runs.HANDLERS,
    **metric_changes.HANDLERS,
    **derived.HANDLERS,
    **semantic_review.HANDLERS,
    **reviews.HANDLERS,
    **pipelines.HANDLERS,
    **prep.HANDLERS,
    **quality.HANDLERS,
    **periods.HANDLERS,
    **forecasting.HANDLERS,
    **indexed.HANDLERS,
    **extraction.HANDLERS,
    **document_sync.HANDLERS,
    **product_use.HANDLERS,
    **monitor_backtests.HANDLERS,
    **audit_exports.HANDLERS,
    **skill_evals.HANDLERS,
    **writeback_diffs.HANDLERS,
}
AUDIT_RELAY = "svc_outbox_relay_audit"


async def _outbox_schemas(role: str) -> list[str]:
    """The schemas whose outbox this connection may claim from (its grants decide)."""
    async with unscoped_session(role) as session:
        rows = await session.execute(
            text(
                "SELECT table_schema FROM information_schema.tables "
                "WHERE table_name = 'outbox' ORDER BY table_schema"
            )
        )
        return [r[0] for r in rows]


async def sources() -> list[EventSource]:
    found: list[EventSource] = []
    for schema in await _outbox_schemas("svc_outbox_relay"):
        types = {hitl.EVENT_TYPE}
        if schema == "qa":
            types |= set(questions.HANDLERS) | set(replay.HANDLERS) | set(exports.HANDLERS)
            # A liked exploratory answer proposed as a verified query (liked.py, R1-08).
            types |= set(liked.HANDLERS)
        if schema == "inbox":
            # Claiming stamps published_at, so this worker is the one consumer of hitl.resolved
            # until Debezium gives each consumer its own group (ADR-06).
            types |= set(resolutions.HANDLERS) | {proposals.DECIDED}
            # The channel gateway tells a request's assignee outside the app (channels/).
            types |= channels.INBOX_EVENTS
        if schema == "notify":
            types |= channels.NOTIFY_EVENTS
        if schema == "catalog":
            types |= set(source_tests.HANDLERS) | set(schema_scans.HANDLERS) | set(derived.HANDLERS)
        if schema == "query":
            types |= set(query_runs.HANDLERS)
        if schema == "governance":
            types |= set(policy_backtests.HANDLERS)
        if schema == "eval":
            types |= set(eval_runs.HANDLERS)
        if schema == "workspace":
            # session_report.status_changed is claimed whatever its status (publish, approval):
            # nothing else consumes it yet, and session_reports acts on `checking` alone.
            types |= set(tiles.HANDLERS) | set(session_reports.HANDLERS)
        if schema == "skill":
            types |= {proposals.PUBLISH, proposals.SHARE} | set(skill_runs.HANDLERS)
            # A version's :test (skill_evals.py, UC-03-04).
            types |= set(skill_evals.HANDLERS)
        if schema == "pipeline":
            types |= {proposals.WRITEBACK} | set(pipelines.HANDLERS) | set(prep.HANDLERS)
            # A writeback's preview diff (writeback_diffs.py, UC-06-04 step 2).
            types |= set(writeback_diffs.HANDLERS)
        if schema == "ingest":
            types |= set(uploads.HANDLERS)
        if schema == "context":
            # One handler per event: metric_changes previews a metric version (with the eval run
            # semantic_review's overlay makes), semantic_review the other drafts, reviews a
            # submitted data product.
            types |= set(metric_changes.HANDLERS) | set(semantic_review.HANDLERS)
            types |= {reviews.PRODUCT, proposals.KNOWLEDGE_SUBMITTED}
            # The access request a data product's use asks for, opened as svc_org (product_use.py).
            types |= set(product_use.HANDLERS)
        if schema == "document":
            # worker-document claims its own types from the same outbox; this is the ready one.
            types |= set(indexed.HANDLERS)
            # A schema's test on sample documents (documents/extraction.py, BR-110).
            types |= set(extraction.HANDLERS)
            # A document source's sync (documents/sync.py, UC-05-01 1a): object stores only.
            types |= set(document_sync.HANDLERS)
        if schema == "agent":
            # The one consumer of run.status_changed: the run executor (runs.py, ADR-37 stand-in).
            types |= set(runs.HANDLERS)
            # An agent version's eval run, the gate before it is deployed (agent_evals.py).
            types |= set(agent_evals.HANDLERS)
            types |= set(uploads.HANDLERS) | set(quality.HANDLERS)
        if schema == "monitor":
            # Run now and the scheduler both request a report run here (report_runs.py).
            types |= set(report_runs.HANDLERS)
            # A rule's backtest (monitor_backtests.py): this worker is the one consumer of
            # monitor.created/updated, whose pending backtest it runs.
            types |= set(monitor_backtests.HANDLERS)
        if schema == "period":
            types |= set(periods.HANDLERS)
        if schema == "forecast":
            # A queued job is trained here; a submitted version opens the owner's proposal.
            types |= set(forecasting.HANDLERS)
        if schema == "conflict":
            types |= {reviews.AUTHORITY}
        if schema == "document":
            types |= {reviews.VALIDITY}
        found.append(OutboxTableSource(schema=schema, event_types=frozenset(types)))
    try:
        audit = await _outbox_schemas(AUDIT_RELAY)
    except RuntimeError:
        # No URL for the audit database: requests raised by the audit module wait until it is set.
        logger.warning("worker-agent | DATAAGENT_DB_URL__%s is not set", AUDIT_RELAY.upper())
        audit = []
    # The audit database's requests, and its exports to write (audit_exports.py).
    audit_types = frozenset({hitl.EVENT_TYPE} | set(audit_exports.HANDLERS))
    found += [OutboxTableSource(schema=s, event_types=audit_types, role=AUDIT_RELAY) for s in audit]
    return found


async def run(stop: asyncio.Event) -> None:
    claimed_from = await sources()
    # UC-16-02's private questions come from Redis, not an outbox (BR-193: never in a table).
    redis = private.redis_from_env()
    private_loop = asyncio.create_task(private.run(redis, stop)) if redis is not None else None
    # The REST connector's try waits on Redis too, answered while its form waits (rest_tries.py).
    tries_loop = asyncio.create_task(rest_tries.run(redis, stop)) if redis is not None else None
    # Every schedule fires from Temporal (schedules/clock.py), not from this loop.
    schedules = asyncio.create_task(clock.serve(stop))
    while not stop.is_set():
        handled = 0
        for source in claimed_from:
            try:
                handled += await run_once(source, HANDLERS)
            except Exception:
                logger.exception("worker-agent | batch failed; retrying after a pause")
        if not handled:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=POLL_SECONDS)
    if private_loop is not None:
        await private_loop
    if tries_loop is not None:
        await tries_loop
    await schedules


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    questions.set_provider_registry(
        ProviderRegistry.build_default(
            secrets=questions.secrets(), source_loader=questions.source_for_provider
        )
    )
    stop = asyncio.Event()
    loop = asyncio.new_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    loop.run_until_complete(run(stop))


if __name__ == "__main__":
    main()
