"""Answer an overview from the Data Space the asker may see.

A question that only asks what is here has no numeric intent, so it is not a
metric choice and it is not handed to the model reader when planning fails.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db import scoped_session, uuid7
from libs.dataagent_db.models import catalog, governance, qa
from services.worker_agent.agentic.corpus import CorpusBrief
from services.worker_agent.ask.contract import NumericIntent, Purpose, RequestContract
from services.worker_agent.events import Event
from services.worker_agent.understand.text import norm

# Words that name the space itself. Anything else is a business question.
_SUBJECTS = frozenset(
    {
        "data",
        "dataset",
        "datasets",
        "bang",
        "table",
        "tables",
        "catalog",
        "document",
        "documents",
        "nguon",
    }
)
_BIGRAMS = frozenset({("du", "lieu"), ("tai", "lieu")})
_ALLOWED = _SUBJECTS | frozenset(
    {
        "a",
        "about",
        "all",
        "and",
        "any",
        "are",
        "available",
        "bao",
        "cac",
        "can",
        "co",
        "current",
        "dang",
        "day",
        "do",
        "du",
        "duoc",
        "gi",
        "have",
        "here",
        "hien",
        "how",
        "i",
        "in",
        "is",
        "khong",
        "la",
        "lieu",
        "list",
        "many",
        "me",
        "mot",
        "nay",
        "nhieu",
        "nhung",
        "noi",
        "o",
        "of",
        "on",
        "or",
        "our",
        "please",
        "see",
        "show",
        "so",
        "space",
        "tai",
        "tell",
        "the",
        "there",
        "these",
        "this",
        "those",
        "va",
        "ve",
        "we",
        "what",
        "which",
        "you",
        "your",
    }
)
_OVERVIEW_REASONS = frozenset({"UNCATALOGUED", "DOC_ONLY"})
_ROLE = "svc_qa"
_MAX_DISABLED = 30


@dataclass(frozen=True)
class SpaceOverview:
    readable: tuple[str, ...]
    disabled: tuple[str, ...]
    certified: tuple[str, ...]
    drafts: tuple[str, ...]
    documents: tuple[str, ...]


def answers_from_context(reason: str, text: str) -> RequestContract | None:
    """An orient contract when this route is an overview. None leaves the route alone."""
    if reason not in _OVERVIEW_REASONS or not _is_overview(text):
        return None
    return RequestContract(Purpose.ORIENT, NumericIntent.NONE)


def describe_space(space: SpaceOverview) -> str:
    """Prose from the assets the asker may know about. No metric is offered."""
    if not (space.readable or space.disabled or space.certified or space.drafts or space.documents):
        return "Data Space này không có bảng, chỉ số hay tài liệu nào để đọc."
    sentences: list[str] = []
    if space.readable:
        sentences.append("Có thể truy vấn các bảng: " + _join(space.readable) + ".")
    if space.disabled:
        sentences.append(
            "Catalog còn các bảng đang tắt, không truy vấn được: " + _join(space.disabled) + "."
        )
    if space.certified:
        sentences.append("Chỉ số đã chứng nhận: " + _join(space.certified) + ".")
    if space.drafts:
        sentences.append("Chỉ số chưa chứng nhận: " + _join(space.drafts) + ".")
    if space.documents:
        sentences.append("Tài liệu: " + _join(space.documents) + ".")
    return " ".join(sentences)


def overview_of(brief: CorpusBrief, disabled: tuple[str, ...]) -> SpaceOverview:
    documents = tuple(doc.name for doc in brief.documents) or tuple(
        page.title for page in brief.pages
    )
    return SpaceOverview(
        readable=tuple(table.qualified for table in brief.tables),
        disabled=disabled,
        certified=tuple(metric.name for metric in brief.metrics if metric.certified),
        drafts=tuple(metric.name for metric in brief.metrics if not metric.certified),
        documents=documents,
    )


def _is_overview(text: str) -> bool:
    tokens = tuple(norm(text).split())
    if not tokens or any(token not in _ALLOWED for token in tokens):
        return False
    if any(token in _SUBJECTS for token in tokens):
        return True
    return any(pair in _BIGRAMS for pair in zip(tokens, tokens[1:], strict=False))


def _join(names: tuple[str, ...]) -> str:
    return ", ".join(names)


async def disabled_table_names(
    session: AsyncSession, space_id: uuid.UUID, principal_id: uuid.UUID
) -> tuple[str, ...]:
    """Tables in the catalog the asker may know about, which load_space leaves out because
    they are disabled. The same privacy filter: not someone else's personal table, and not
    one an active access policy covers."""
    covered = select(governance.AccessPolicy.dataset_id).where(
        governance.AccessPolicy.status == "active"
    )
    rows = (
        await session.scalars(
            select(catalog.Dataset).where(
                catalog.Dataset.data_space_id == space_id,
                catalog.Dataset.enabled.is_(False),
                catalog.Dataset.deleted_at.is_(None),
                or_(
                    catalog.Dataset.scope != "personal",
                    catalog.Dataset.created_by == principal_id,
                ),
                catalog.Dataset.id.not_in(covered),
            )
        )
    ).all()
    return tuple(
        row.display_name or f"{row.schema_name}.{row.table_name}" for row in rows[:_MAX_DISABLED]
    )


def _citations(space: SpaceOverview) -> list[dict[str, str]]:
    cited: list[dict[str, str]] = []
    cited.extend({"title": name, "kind": "table"} for name in space.readable)
    cited.extend({"title": name, "kind": "table"} for name in space.disabled)
    cited.extend({"title": name, "kind": "metric"} for name in (*space.certified, *space.drafts))
    cited.extend({"title": name, "kind": "document"} for name in space.documents)
    return cited


async def answer_overview(event: Event, question_id: uuid.UUID) -> bool:
    """Store the overview and return True. False when the question is not an overview, so the
    caller keeps its route. A question already past received is left alone."""
    from services.worker_agent.agentic.corpus import load_corpus
    from services.worker_agent.agentic.stream_contract import final_payload
    from services.worker_agent.answer import envelope as answer_trust
    from services.worker_agent.progress import publish
    from services.worker_agent.semantic.loader import load

    user_id = _user_of(event)
    async with scoped_session(_ROLE, event.org_id, user_id) as session:
        question = await session.get(qa.Question, question_id)
        if question is None:
            return False
        if question.status != "received" or not _is_overview(question.text):
            return question.status != "received"
        conversation = await session.get(qa.Conversation, question.conversation_id)
        if conversation is None:
            return False
        space_id = conversation.data_space_id
        principal = conversation.user_id
        layer = await load(session, space_id)
        brief = await load_corpus(session, space_id, principal, layer)
        disabled = await disabled_table_names(session, space_id, principal)
        space = overview_of(brief, disabled)
        text = describe_space(space)
        citations = _citations(space)
        code, envelope = answer_trust.trust("document", "document", cited=bool(citations))
        locked = await session.get(qa.Question, question_id, with_for_update=True)
        if locked is None or locked.status != "received":
            return True
        version = (
            await session.scalar(
                select(func.max(qa.Plan.version)).where(qa.Plan.question_id == question_id)
            )
            or 0
        ) + 1
        plan_id = uuid7()
        session.add(
            qa.Plan(
                id=plan_id,
                org_id=event.org_id,
                question_id=question_id,
                version=version,
                chips=[],
                steps=[{"tool": "describe_space"}],
            )
        )
        await session.flush()
        answer_id = uuid7()
        session.add(
            qa.Answer(
                id=answer_id,
                org_id=event.org_id,
                question_id=question_id,
                plan_id=plan_id,
                kind="document",
                text=text,
                citations=citations,
                blocks=[{"type": "markdown", "content": text}],
                trust_code=code.value,
                envelope=envelope,
                number_check={"untraceable_final": 0},
                agent_name="worker-agent",
                product_scope="not_applicable",
            )
        )
        locked.status = "answered"
        locked.answered_at = func.now()
    payload = final_payload(
        question_id=str(question_id),
        answer_id=str(answer_id),
        kind="document",
        status="answered",
        text=text,
        blocks=[{"type": "markdown", "content": text}],
    )
    payload["trust_code"] = code.value
    await publish(str(question_id), "answer", payload)
    await publish(str(question_id), "done", {**payload, "status": "answered"})
    return True


def _user_of(event: Event) -> uuid.UUID | None:
    user = event.payload.get("user_id")
    return uuid.UUID(str(user)) if user else None
