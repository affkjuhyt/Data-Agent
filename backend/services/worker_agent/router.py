"""Governed-first router (AE-01, ADR-50, docs/blueprint/ASK_ENGINE_SPEC.md §2): which engine
answers a question, decided by rules before any model is called.

Measured on DS-AW (12 and 16/11/2026): the governed path answered the first 20 questions 20/20 in
2.3 s and 536 tokens, the dynamic agent 14 to 17/20 in 16 s and 22.6k tokens, and the misses were
the agent over-answering single-metric questions. A question the rules place on one certified
metric stays governed; an ambiguous reading stays governed; a question the rules do not place
goes to the planner (agentic, UNCATALOGUED).

The reading is the governed path's own (understand/asked.py, the rule resolver, then follow_up
against the turn before: understand/slots.py), so the router never routes on a reading the answer
does not use. A follow-up ("còn năm 2012 thì sao?", "so với cùng kỳ năm trước") names no metric
of its own; read alone it was UNCATALOGUED and went to the agent at about 40 times the governed
path's tokens, while the governed path answers it from the conversation. No model, no write: a route is a log
line and a count (`dataagent_ask_route_total{path,reason}`).

ASK_ENGINE chooses the policy: `router` (default), `agentic` (the agent first for every question,
as before AE-01, the rollback) or `governed` (never the agent).
"""

from __future__ import annotations

import datetime
import logging
import os
import uuid
from dataclasses import dataclass
from typing import Literal

from libs import clock, metrics
from libs.dataagent_db import scoped_session
from libs.dataagent_db.models import qa
from services.worker_agent.agentic import deep_skill, investigation
from services.worker_agent.events import Event
from services.worker_agent.query.scope import can_ask_data_space
from services.worker_agent.semantic import uncertified
from services.worker_agent.semantic.loader import load
from services.worker_agent.understand.asked import asked_of
from services.worker_agent.understand.packs import PACKS
from services.worker_agent.understand.resolver import (
    Understanding,
    Vocabulary,
    follow_up,
    understand,
)
from services.worker_agent.understand.slots import ConversationSlots, previous_slots
from services.worker_agent.understand.text import norm

logger = logging.getLogger(__name__)

ROLE = "svc_qa"
ENGINES = ("router", "agentic", "governed")

Path = Literal["governed", "agentic"]


def engine() -> str:
    value = os.environ.get("ASK_ENGINE", "router").strip().lower()
    return value if value in ENGINES else "router"


@dataclass(frozen=True)
class Route:
    path: Path
    # Short and bounded (a metric label): RESOLVED, AMBIGUOUS, UNCATALOGUED, WHY, MULTI_METRIC,
    # HYBRID, DOC_ONLY, DEEP, FORBIDDEN, GONE, UNCERTIFIED, INVESTIGATION, or the ENGINE_* of a
    # fixed policy.
    reason: str


def classify(question: str, u: Understanding, *, deep: bool) -> Route:
    """The route of a question the rule pass has read as `u`."""
    text = norm(question)
    why = any(p.why.search(text) for p in PACKS)
    documents = any(p.documents.search(text) for p in PACKS)
    if deep:
        return Route("agentic", "DEEP")
    # Two metrics the question joins ("doanh thu và lợi nhuận", "so sánh A với B"): the rule pass
    # cannot take one and asks which, where the question wants both. Two it names without joining
    # them ("doanh số Trade Sales theo ngân sách") ask one, the second qualifying the first: on
    # DS-AW the agent missed both such items, the governed path answers one and refuses the other.
    joined = any(p.joins.search(text) for p in PACKS)
    if u.named_metrics >= 2 and u.decision == "clarify" and joined:
        return Route("agentic", "MULTI_METRIC")
    # A metric and the documents: whatever else the rule pass made of the words around them
    # ("theo quy định …" reads as a cut it cannot place), the answer needs both.
    if documents and u.metric is not None:
        return Route("agentic", "HYBRID")
    if u.decision == "proceed" and u.metric is not None:
        if why:
            return Route("agentic", "WHY")
        return Route("governed", "RESOLVED")
    if documents:
        return Route("agentic", "DOC_ONLY")
    if u.decision == "clarify":
        return Route("governed", "AMBIGUOUS")
    return Route("agentic", "UNCATALOGUED")


def reading(
    asked: str,
    vocab: Vocabulary,
    *,
    today: datetime.date,
    slots: ConversationSlots | None = None,
    compare_to: str | None = None,
) -> Understanding:
    """The rule reading of what was asked, against the turn before as the governed path reads it
    (questions._understand_and_plan): follow_up when the rules placed no metric or no period."""
    u = understand(asked, vocab, today=today)
    if slots is not None and (u.decision == "unknown" or u.period is None):
        u = follow_up(
            u,
            slots.chips,
            vocab,
            question=asked,
            slots=slots,
            compare_to=compare_to,
            today=today,
        )
    return u


async def decide(event: Event, question_id: uuid.UUID) -> Route:
    policy = engine()
    if policy != "router":
        return Route("agentic" if policy == "agentic" else "governed", f"ENGINE_{policy.upper()}")
    async with scoped_session(ROLE, event.org_id, event.payload.get("user_id")) as session:
        question = await session.get(qa.Question, question_id)
        if question is None:
            return Route("governed", "GONE")
        conversation = await session.get(qa.Conversation, question.conversation_id)
        if conversation is None:
            return Route("governed", "GONE")
        space_id = conversation.data_space_id
        # Permissions before anything (invariant 13): the governed path refuses with the reason.
        if not await can_ask_data_space(
            session, data_space_id=space_id, principal_id=conversation.user_id
        ):
            return Route("governed", "FORBIDDEN")
        # An investigation of an alert has its plan already (AL-11): the agent runs it.
        if await investigation.prepared(event.org_id, conversation, question) is not None:
            return Route("agentic", "INVESTIGATION")
        today = question.as_of or clock.today(await clock.space_zone(session, space_id))
        layer = await load(session, space_id)
        text, purpose = question.text, question.purpose
        slots = await previous_slots(session, question, layer.vocabulary)
        parts = asked_of(text, layer)
        u = reading(
            parts.text,
            layer.vocabulary,
            today=today,
            slots=slots,
            compare_to=parts.comparison.compare_to if parts.comparison is not None else None,
        )
        # A quantity the layer defines without a certified metric (a draft, a measure) is the
        # governed path's to compile by its definition (semantic/uncertified.py, S1), not the
        # agent's to write SQL for.
        if u.decision == "unknown" and not deep_skill.requested(event.payload, purpose):
            found = await uncertified.load_uncertified(session, space_id, layer)
            if uncertified.read(parts.text, layer, found, today=today) is not None:
                return Route("governed", "UNCERTIFIED")
    return classify(text, u, deep=deep_skill.requested(event.payload, purpose))


def count(question_id: uuid.UUID, route: Route) -> None:
    metrics.ASK_ROUTES.labels(route.path, route.reason).inc()
    logger.info("question %s: routed to %s (%s)", question_id, route.path, route.reason)
