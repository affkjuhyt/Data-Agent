"""Answer a question about the Data Space itself, from what the asker may see, before any model.

Two kinds, neither a metric choice:
* an overview ("dữ liệu ở đây nói về gì", "có những bảng nào", "thế dữ liệu chúng ta đang trao đổi
  nói về gì"): the tables, metrics and documents the asker may read;
* a listing of one dimension's members ("có những nhóm sản phẩm nào", "khu vực nào đang có"), from
  the value index, when it holds them (a dimension of hundreds of members is not indexed and is
  left to the route: the agent lists it from the tables).

Handed to the model reader instead, both were read as the conversation's last metric (24/11: "thế
dữ liệu chúng ta đang trao đổi nói về gì" answered customers by country, with the countries of the
answer before as filters and the current year as the period).
"""

from __future__ import annotations

import itertools
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db import scoped_session, uuid7
from libs.dataagent_db.models import catalog, governance, qa
from libs.dataagent_db.models import org as org_models
from services.worker_agent.agentic.corpus import CorpusBrief
from services.worker_agent.ask.contract import NumericIntent, Purpose, RequestContract
from services.worker_agent.events import Event
from services.worker_agent.understand.resolver import Vocabulary, dimension_names
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
        "ban",
        "bao",
        "biet",
        "bo",
        "cac",
        "chung",
        "cho",
        "can",
        "co",
        "current",
        "dang",
        "day",
        "do",
        "doi",
        "du",
        "duoc",
        "gi",
        "gioi",
        "gom",
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
        "minh",
        "mot",
        "nao",
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
        "ta",
        "tai",
        "tell",
        "the",
        "thieu",
        "toi",
        "trao",
        "there",
        "these",
        "this",
        "those",
        "va",
        "ve",
        "we",
        "what",
        "which",
        "xem",
        "you",
        "your",
    }
)
# A listing asks which members: "nào", "gì", "những", "liệt kê", "which", "list".
_LISTING = frozenset({"nao", "gi", "nhung", "liet", "ke", "list", "which", "what", "cac"})
# Members named in one answer; the rest are counted.
_MAX_MEMBERS = 50
_OVERVIEW_REASONS = frozenset({"UNCATALOGUED", "DOC_ONLY"})
_ROLE = "svc_qa"
_MAX_DISABLED = 30
_MAX_DIMENSIONS = 12
TIME_CUT = "thời gian (ngày, tháng, quý, năm)"
_TABLE_WORDS = frozenset({"bang", "table", "tables", "catalog", "nguon", "dataset", "datasets"})


@dataclass(frozen=True)
class SpaceOverview:
    readable: tuple[str, ...]
    disabled: tuple[str, ...]
    certified: tuple[str, ...]
    drafts: tuple[str, ...]
    documents: tuple[str, ...]
    # What the space is about, in its owner's words (org.data_space): the answer to "nói về gì".
    name: str = ""
    description: str = ""
    # The cuts a figure can be read by, by name (Nhóm sản phẩm, Khu vực, …); time ones last.
    dimensions: tuple[str, ...] = ()
    # The question names the tables themselves ("có những bảng nào"): they are listed, else
    # only counted, as a person asking what the data is about wants its subject, not its DDL.
    tables_asked: bool = False


def answers_from_context(reason: str, text: str) -> RequestContract | None:
    """An orient contract when this route is an overview. None leaves the route alone."""
    if reason not in _OVERVIEW_REASONS or not _is_overview(text):
        return None
    return RequestContract(Purpose.ORIENT, NumericIntent.NONE)


def describe_space(space: SpaceOverview) -> str:
    """What the space is about, then what can be asked of it: its description, its metrics, the
    cuts and documents, and how many tables hold it. The tables are named only when asked for
    (25/11: "bộ dữ liệu này đang nói về gì" was answered with 46 table names, each twice). No
    metric is offered as a choice."""
    if not (space.readable or space.disabled or space.certified or space.drafts or space.documents):
        return "Data Space này không có bảng, chỉ số hay tài liệu nào để đọc."
    parts: list[str] = []
    about = space.description.strip()
    if space.name and about:
        parts.append(f"**{space.name}**: {about}")
    elif about:
        parts.append(about)
    elif space.name:
        parts.append(f"**{space.name}**.")
    asked: list[str] = []
    if space.certified:
        asked.append(
            f"- {len(space.certified)} chỉ số đã chứng nhận: " + _join(space.certified) + "."
        )
    if space.drafts:
        asked.append(
            f"- {len(space.drafts)} chỉ số chưa chứng nhận (số tính theo định nghĩa đang chờ "
            "duyệt): " + _join(space.drafts) + "."
        )
    if space.dimensions:
        shown = space.dimensions[:_MAX_DIMENSIONS]
        more = len(space.dimensions) - len(shown)
        asked.append(
            "- Xem theo: " + _join(shown) + (f" và {more} chiều khác" if more > 0 else "") + "."
        )
    if space.documents:
        asked.append("- Tài liệu: " + _join(space.documents) + ".")
    if asked:
        parts.append("**Có thể hỏi về**\n" + "\n".join(asked))
    if space.tables_asked:
        tables: list[str] = []
        if space.readable:
            tables.append(
                f"- {len(space.readable)} bảng truy vấn được: " + _join(space.readable) + "."
            )
        if space.disabled:
            tables.append(
                f"- {len(space.disabled)} bảng đang tắt, chưa truy vấn được: "
                + _join(space.disabled)
                + "."
            )
        if tables:
            parts.append("**Bảng dữ liệu**\n" + "\n".join(tables))
    elif space.readable or space.disabled:
        counted = f"{len(space.readable)} bảng truy vấn được"
        if space.disabled:
            counted += f", {len(space.disabled)} bảng đang tắt"
        parts.append(f"Dữ liệu nằm trong {counted}; hỏi “có những bảng nào” để xem danh sách.")
    examples = _examples(space)
    if examples:
        parts.append("Ví dụ: " + "; ".join(f"“{e}”" for e in examples) + ".")
    return "\n\n".join(parts)


def _examples(space: SpaceOverview) -> list[str]:
    """Two questions the space answers as worded, from its own metrics and cuts."""
    metrics = space.certified or space.drafts
    if not metrics:
        return []
    out = [f"{metrics[0]} theo tháng"]
    cut = next((d for d in space.dimensions if d != TIME_CUT), None)
    if cut:
        second = metrics[1] if len(metrics) > 1 else metrics[0]
        out.append(f"{second} theo {cut[:1].lower()}{cut[1:]}")
    return out


def overview_of(
    brief: CorpusBrief,
    disabled: tuple[str, ...],
    *,
    name: str = "",
    description: str = "",
    dimensions: tuple[str, ...] = (),
    tables_asked: bool = False,
) -> SpaceOverview:
    documents = tuple(doc.name for doc in brief.documents) or tuple(
        page.title for page in brief.pages
    )
    # One name once: two sources of the space may hold the same table (a test source that
    # registered the warehouse again, 25/11), and a name readable through one is readable.
    readable = tuple(dict.fromkeys(table.qualified for table in brief.tables))
    return SpaceOverview(
        readable=readable,
        disabled=tuple(n for n in dict.fromkeys(disabled) if n not in set(readable)),
        certified=tuple(dict.fromkeys(m.name for m in brief.metrics if m.certified)),
        drafts=tuple(dict.fromkeys(m.name for m in brief.metrics if not m.certified)),
        documents=tuple(dict.fromkeys(documents)),
        name=name,
        description=description,
        dimensions=tuple(dict.fromkeys(dimensions)),
        tables_asked=tables_asked,
    )


def _cuts(vocab: Vocabulary) -> tuple[str, ...]:
    """The cuts by name, the time ones said once: six date columns (order date, stock date, …)
    are one way of asking, by time."""
    names = [d.name for d in vocab.dimensions if not d.is_time]
    if any(d.is_time for d in vocab.dimensions):
        # First: every figure can be read by time, and a long list must not cut it off.
        names.insert(0, TIME_CUT)
    return tuple(names)


def asks_for_tables(text: str) -> bool:
    return any(token in _TABLE_WORDS for token in norm(text).split())


def _is_overview(text: str) -> bool:
    tokens = tuple(norm(text).split())
    if not tokens or any(token not in _ALLOWED for token in tokens):
        return False
    if any(token in _SUBJECTS for token in tokens):
        return True
    return any(pair in _BIGRAMS for pair in itertools.pairwise(tokens))


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


def listed_dimension(text: str, vocab: Vocabulary) -> str | None:
    """The one dimension a question asks the members of ("có những nhóm sản phẩm nào"), by its
    name or a synonym, with nothing else asked: no metric, no period, no other word than the
    overview's and the listing's. None otherwise."""
    folded = f" {norm(text)} "
    found: list[tuple[int, str, str]] = []
    for d in vocab.dimensions:
        if d.is_time:
            continue
        for name in dimension_names(d.code, vocab):
            phrase = norm(name)
            if phrase and f" {phrase} " in folded:
                found.append((len(phrase), d.code, phrase))
    if not found:
        return None
    found.sort(reverse=True)
    _, code, phrase = found[0]
    rest = folded.replace(f" {phrase} ", " ").split()
    if any(f" {norm(m.name)} " in folded for m in vocab.metrics):
        return None
    if not any(w in _LISTING for w in rest) or any(
        w not in _ALLOWED and w not in _LISTING and w not in phrase.split() for w in rest
    ):
        return None
    return code


def describe_members(label: str, members: list[str]) -> str:
    shown = members[:_MAX_MEMBERS]
    more = len(members) - len(shown)
    tail = f" và {more} giá trị khác" if more > 0 else ""
    return f"{label} có {len(members)} giá trị: {', '.join(shown)}{tail}."


async def _sees_less(
    session: AsyncSession, layer: Any, space_id: uuid.UUID, principal: uuid.UUID
) -> bool:
    """Whether the asker may see less than the whole space: a row scope, or an access policy on
    any of its datasets (as questions._filed_restricted refuses a filed figure)."""
    from services.worker_agent.query.scope import OutOfScope, active_policies, active_row_scopes

    try:
        if (
            await active_row_scopes(session, data_space_id=space_id, principal_id=principal)
            is not None
        ):
            return True
        policies = await active_policies(
            session,
            principal_id=principal,
            datasets=list(layer.datasets.values()),
            caller_kind="question",
        )
    except OutOfScope:
        return True
    return bool(policies)


async def answer_overview(event: Event, question_id: uuid.UUID) -> bool:
    """Store the overview and return True. False when the question is not an overview, so the
    caller keeps its route. A question already past received is left alone."""
    from libs.contracts.v1 import trust as answer_trust
    from services.worker_agent.agentic.corpus import load_corpus
    from services.worker_agent.agentic.stream_contract import final_payload
    from services.worker_agent.progress import publish
    from services.worker_agent.semantic.loader import load

    user_id = _user_of(event)
    async with scoped_session(_ROLE, event.org_id, user_id) as session:
        question = await session.get(qa.Question, question_id)
        if question is None:
            return False
        if question.status != "received":
            return True
        conversation = await session.get(qa.Conversation, question.conversation_id)
        if conversation is None:
            return False
        space_id = conversation.data_space_id
        principal = conversation.user_id
        overview = _is_overview(question.text)
        layer = await load(session, space_id)
        listed = None if overview else listed_dimension(question.text, layer.vocabulary)
        if listed is not None and await _sees_less(session, layer, space_id, principal):
            # The value index is the whole space's: an asker held to some rows or under an access
            # policy is answered by the path that applies them (the security review of 24/11).
            listed = None
        members = [v.label for v in layer.vocabulary.values if v.dimension_code == listed]
        if not overview and not members:
            return False
        if overview:
            brief = await load_corpus(session, space_id, principal, layer, purpose=question.purpose)
            disabled = await disabled_table_names(session, space_id, principal)
            data_space = await session.get(org_models.DataSpace, space_id)
            space = overview_of(
                brief,
                disabled,
                name=data_space.name if data_space else "",
                description=(data_space.description or "") if data_space else "",
                dimensions=_cuts(layer.vocabulary),
                tables_asked=asks_for_tables(question.text),
            )
            text = describe_space(space)
            citations = _citations(space)
            tool = "describe_space"
        else:
            name = next(d.name for d in layer.vocabulary.dimensions if d.code == listed)
            text = describe_members(name[:1].upper() + name[1:], sorted(set(members)))
            citations = [{"title": name, "kind": "dimension"}]
            tool = "list_members"
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
                steps=[{"tool": tool}],
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
