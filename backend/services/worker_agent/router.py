"""Governed-first router (AE-01, ADR-50, docs/blueprint/ASK_ENGINE_SPEC.md §2): which engine
answers a question, decided by rules before any model is called.

Measured on DS-AW (12 and 16/11/2026): the governed path answered the first 20 questions 20/20 in
2.3 s and 536 tokens, the dynamic agent 14 to 17/20 in 16 s and 22.6k tokens, and the misses were
the agent over-answering single-metric questions. So a question the rule pass places on one
certified metric goes to the governed path, which also asks back when the reading is ambiguous
and falls back on the model reader, the analyst pass, exploratory SQL and the documents when it
places none. The agent gets what that path does not do: "why" a metric moved, two metrics at
once, a question that wants the documents too, a documents question, and a turn asked to go deep.

The reading is the governed path's own (understand/asked.py, then the rule resolver), so the
router never routes on a reading the answer does not use. No model, no write: a route is a log
line and a count (`dataagent_ask_route_total{path,reason}`).

ASK_ENGINE chooses the policy: `router` (default), `agentic` (the agent first for every question,
as before AE-01, the rollback) or `governed` (never the agent).
"""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass
from typing import Literal

from libs import clock, metrics
from libs.dataagent_db import scoped_session
from libs.dataagent_db.models import qa
from services.worker_agent.agentic import deep_skill
from services.worker_agent.events import Event
from services.worker_agent.query.scope import can_ask_data_space
from services.worker_agent.semantic.loader import load
from services.worker_agent.understand.asked import asked_of
from services.worker_agent.understand.packs import PACKS
from services.worker_agent.understand.resolver import Understanding, understand
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
    # HYBRID, DOC_ONLY, DEEP, FORBIDDEN, GONE, or the ENGINE_* of a fixed policy.
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
    return Route("governed", "UNCATALOGUED")


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
        today = question.as_of or clock.today(await clock.space_zone(session, space_id))
        layer = await load(session, space_id)
        text, purpose = question.text, question.purpose
    u = understand(asked_of(text, layer).text, layer.vocabulary, today=today)
    return classify(text, u, deep=deep_skill.requested(event.payload, purpose))


def count(question_id: uuid.UUID, route: Route) -> None:
    metrics.ASK_ROUTES.labels(route.path, route.reason).inc()
    logger.info("question %s: routed to %s (%s)", question_id, route.path, route.reason)
