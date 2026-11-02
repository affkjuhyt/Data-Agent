"""The agent's long-term memory of a person in a Data Space (qa.memory_fact), after Memori:

* extract: after an answered turn the planner model reads the exchange and proposes at most five
  durable things the person said (a presentation preference, a definition they use, a fact about
  their work, a correction of the agent), with a list of what is never stored: nothing a query
  returned, no credentials, nothing about a single task (an idea also in Data-Analysis-Agent,
  whose licence forbids commercial use: independently written, none of its text used). A proposal holding a figure the turn
  fetched, or anything that looks like a secret, is dropped by the server, not left to the model.
* store: one row per (space, person, normalised content); saying it again bumps `times` and
  `last_seen_at` (Memori's num_times / date_last_time) instead of adding a row.
* recall: before planning, the person's active memories are ranked by their words in common with
  the question (BM25, the lexical weight raised for short questions, as Memori does), how recently
  and how often they were said; the best few go to the planner inside <memory>, each with the date
  it was said, and the prompt says to use one only when it bears on the question.

A definition the person states never changes a certified metric: it helps read their question,
and the numbers still come from the semantic layer or labelled exploratory SQL.
"""

from __future__ import annotations

import datetime
import hashlib
import logging
import math
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from libs.dataagent_db import scoped_session, uuid7
from libs.dataagent_db.models import qa
from services.worker_agent.agentic.prompts import BOUNDARY
from services.worker_agent.llm import Gateway, ModelUnavailable
from services.worker_agent.understand.text import norm

logger = logging.getLogger(__name__)

ROLE = "svc_qa"
RECALL_LIMIT = 5
# Memories ranked from these at most (Memori's candidate limit).
CANDIDATES = 200
RELEVANCE_THRESHOLD = 0.1
MAX_OPS = 5
MAX_CONTENT = 300

Kind = Literal["preference", "definition", "fact", "feedback"]

EXTRACT = f"""# Vai trò
Bạn trích trí nhớ dài hạn cho trợ lý phân tích dữ liệu, từ một lượt hội thoại.

{BOUNDARY}

# Ghi nhớ gì
- preference: sở thích trình bày người dùng nói rõ (ngôn ngữ, đơn vị, định dạng số, kiểu biểu đồ,
  độ dài câu trả lời, "từ nay luôn…").
- definition: cách người dùng định nghĩa một khái niệm ("khách hàng mới = mua lần đầu trong năm",
  "doanh thu thuần = doanh thu trừ chiết khấu").
- fact: sự kiện bền về công việc của họ (phụ trách vùng nào, quan tâm sản phẩm nào, kỳ báo cáo).
- feedback: người dùng sửa agent ("không, ý tôi là…", "đừng dùng chỉ số X cho việc này").

# Không ghi
Số liệu do truy vấn trả về, việc chỉ của câu hỏi này, chuyện xã giao, mật khẩu, khoá, chuỗi kết
nối, thông tin cá nhân nhạy cảm. Điều đã có trong <known> thì không ghi lại (trừ khi sửa nó).

# Định dạng
JSON: {{"ops": [{{"kind": "preference|definition|fact|feedback", "content": "<một câu, ngôn ngữ
người dùng>"}}]}}. Tối đa {MAX_OPS}. Không có gì đáng nhớ: {{"ops": []}}."""


class MemoryOp(BaseModel):
    kind: Kind
    content: str = Field(max_length=MAX_CONTENT)


class Extraction(BaseModel):
    ops: list[MemoryOp] = Field(default_factory=list)


@dataclass(frozen=True)
class Memory:
    kind: str
    content: str
    times: int
    last_seen_at: datetime.datetime


_SECRET = re.compile(
    r"(password|passwd|mật khẩu|mat khau|secret|token|api[_ -]?key|private key|"
    r"postgres(ql)?://|mysql://|jdbc:|bearer\s)",
    re.IGNORECASE,
)
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


# Words that say something meant to last (a preference, a definition, a correction): a turn without
# any is not read for memories, which saves a model call on most questions (RES-05).
_LASTING = (
    "toi ",
    "minh ",
    "chung toi",
    "luon",
    "tu nay",
    "tu gio",
    "lan sau",
    "mac dinh",
    "dinh nghia",
    "nghia la",
    "hieu la",
    "duoc tinh",
    "tinh bang",
    "dung ",
    "khong phai",
    "y toi",
    "toi muon",
    "toi phu trach",
    "toi quan tam",
    "always",
    "from now",
    "by default",
    "define",
    "means",
    "i mean",
    "i am",
    "i'm",
    "my ",
    "we ",
    "don't",
    "do not",
    "instead",
)


def worth_remembering(question: str) -> bool:
    text = f" {norm(question)} "
    return any(f" {w}" in text for w in _LASTING)


def content_hash(content: str) -> str:
    return hashlib.sha256(norm(content).encode()).hexdigest()


def _kept(op: MemoryOp, fetched_numbers: set[str]) -> bool:
    """The server's own rules over the model's proposal: a secret-looking text, or one quoting a
    figure the turn fetched, is never stored."""
    text = op.content.strip()
    if len(text) < 6 or _SECRET.search(text):
        return False
    return not any(n in fetched_numbers for n in _NUMBER.findall(text) if len(n) >= 3)


async def extract(
    model: Gateway,
    *,
    question: str,
    answer: str,
    history: str,
    known: Sequence[Memory],
    fetched_numbers: set[str],
) -> list[MemoryOp]:
    user = (
        (f"<conversation>\n{history}\n</conversation>\n\n" if history else "")
        + f"<question>\n{question}\n</question>\n\n<answer>\n{answer[:1500]}\n</answer>\n\n"
        + "<known>\n"
        + "\n".join(f"- [{m.kind}] {m.content}" for m in known)
        + "\n</known>"
    )
    try:
        reply = await model.json_chat("planner", EXTRACT, user, Extraction, max_tokens=500)
    except ModelUnavailable:
        return []
    return [op for op in reply.content.ops[:MAX_OPS] if _kept(op, fetched_numbers)]


async def store(
    org_id: uuid.UUID,
    space_id: uuid.UUID,
    user_id: uuid.UUID,
    question_id: uuid.UUID,
    ops: Sequence[MemoryOp],
) -> int:
    if not ops:
        return 0
    async with scoped_session(ROLE, org_id, user_id) as session:
        t = qa.MemoryFact.__table__
        for op in ops:
            stmt = insert(qa.MemoryFact).values(
                id=uuid7(),
                org_id=org_id,
                data_space_id=space_id,
                user_id=user_id,
                kind=op.kind,
                content=op.content.strip(),
                content_hash=content_hash(op.content),
                source_question_id=question_id,
                created_by=user_id,
                updated_by=user_id,
            )
            await session.execute(
                stmt.on_conflict_do_update(
                    index_elements=[t.c.data_space_id, t.c.user_id, t.c.content_hash],
                    set_={
                        "times": t.c.times + 1,
                        "last_seen_at": func.now(),
                        "status": "active",
                        "kind": op.kind,
                        "updated_at": func.now(),
                        "updated_by": user_id,
                    },
                )
            )
    return len(ops)


def _bm25(query: list[str], docs: list[list[str]], k1: float = 1.2, b: float = 0.75) -> list[float]:
    """BM25 of each document against the query, divided by the best (Memori's lexical half)."""
    if not docs or not query:
        return [0.0] * len(docs)
    avg = sum(len(d) for d in docs) / len(docs) or 1.0
    df = {w: sum(1 for d in docs if w in d) for w in set(query)}
    scores = []
    for d in docs:
        s = 0.0
        for w in set(query):
            tf = d.count(w)
            if not tf:
                continue
            idf = math.log(1 + (len(docs) - df[w] + 0.5) / (df[w] + 0.5))
            s += idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * len(d) / avg))
        scores.append(s)
    top = max(scores) or 1.0
    return [s / top for s in scores]


def rank(
    question: str, memories: Sequence[Memory], now: datetime.datetime, limit: int = RECALL_LIMIT
) -> list[Memory]:
    """The memories that bear on the question, best first: words in common (weighted more for a
    short question, where exact words carry the signal), then how recently and how often said.
    A presentation preference applies to every question, so it needs no word in common."""
    query = norm(question).split()
    lexical = _bm25(query, [norm(m.content).split() for m in memories])
    w_lex = 0.7 if len(query) <= 2 else 0.55
    scored = []
    for m, lex in zip(memories, lexical, strict=True):
        days = max((now - m.last_seen_at).total_seconds() / 86400, 0.0)
        recency = 1.0 / (1.0 + days / 30)
        often = min(math.log1p(m.times) / math.log1p(10), 1.0)
        score = w_lex * lex + (1 - w_lex) * (0.6 * recency + 0.4 * often)
        if m.kind == "preference":
            score = max(score, 0.5 * recency + 0.2)
        elif lex == 0:
            continue
        if score >= RELEVANCE_THRESHOLD:
            scored.append((score, m))
    scored.sort(key=lambda x: -x[0])
    return [m for _, m in scored[:limit]]


async def load(org_id: uuid.UUID, space_id: uuid.UUID, user_id: uuid.UUID) -> list[Memory]:
    async with scoped_session(ROLE, org_id, user_id) as session:
        rows = (
            await session.scalars(
                select(qa.MemoryFact)
                .where(
                    qa.MemoryFact.data_space_id == space_id,
                    qa.MemoryFact.user_id == user_id,
                    qa.MemoryFact.status == "active",
                )
                .order_by(qa.MemoryFact.last_seen_at.desc())
                .limit(CANDIDATES)
            )
        ).all()
    return [Memory(r.kind, r.content, r.times, r.last_seen_at) for r in rows]


def render(memories: Sequence[Memory]) -> str:
    """The recalled memories as the planner and the reporter read them (Memori's injection)."""
    if not memories:
        return ""
    lines = [
        f"- [{m.kind}] {m.content} (nói lúc {m.last_seen_at.date().isoformat()}, {m.times} lần)"
        for m in memories
    ]
    return (
        "<memory>\nĐiều người dùng đã nói ở các hội thoại trước. Chỉ dùng điều liên quan tới câu "
        "hỏi này; một định nghĩa họ nêu giúp hiểu câu hỏi nhưng không thay chỉ số chuẩn, và số "
        "liệu vẫn chỉ đến từ công cụ.\n" + "\n".join(lines) + "\n</memory>"
    )
