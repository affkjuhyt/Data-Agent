"""How well document retrieval finds the passage a question needs (RG-02, docs/v3/04-backlog.md §3e).

This runs `retrieve` itself, not the answer. A citation only says the answer kept a passage; the
rank says whether retrieval found it at all, which is what thresholds, chunking and the lexical
half are tuned against. The relevance bar of `retrieve` stays on: what is scored is the list the
answer would be given.

A case names its gold passages by document number and a short quote, never by chunk id, so the
gold survives a re-chunk or a re-index (worker_document.reprocess):

    {"id": "ret-005", "question": "Tỷ suất lãi gộp được tính thế nào?", "as_of": "2013-09-30",
     "expect": "found", "gold": [{"doc_no": "07/2012/QĐ-AWC", "quote": "không lấy trung bình"}],
     "forbid": [{"doc_no": "01/2011/QĐ-AWC"}], "tags": ["dinh-nghia"]}

`expect: not_found` says the documents the space allows do not hold the answer: any passage
returned is a miss. `forbid` names documents that must not come back at `as_of` (expired or
replaced): retrieval's validity filter is scored with the rest.

    kubectl -n dataagent-service exec -i deploy/worker-agent -- \\
        python -m services.worker_agent.documents.retrieval_eval --org <org id> --space DS-AW - \\
        < model/eval/cases/retrieval.jsonl

The report says which halves ran (dense needs QDRANT_URL and an embeddings endpoint, rerank
DOCUMENT_RERANK_URL): a keyword-only number is not a baseline for the full stack.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import re
import sys
import unicodedata
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, TextIO

from sqlalchemy import select

from libs.dataagent_db import scoped_session
from libs.dataagent_db.models import org
from services.worker_agent.documents.retrieve import Passage, retrieve
from services.worker_agent.documents.search import SearchServices, services_from_env

ROLE = "svc_qa"
LIMIT = 10
CUTOFFS = (1, 3, 10)
EXPECTS = ("found", "not_found")

_SPACE = re.compile(r"\s+")


class CaseError(ValueError):
    """A malformed case file. Fatal: a score over a corpus that quietly lost cases is wrong."""


@dataclass(frozen=True)
class Ref:
    doc_no: str
    quote: str | None = None


@dataclass(frozen=True)
class Case:
    id: str
    question: str
    as_of: datetime.date
    expect: str
    gold: tuple[Ref, ...] = ()
    forbid: tuple[Ref, ...] = ()
    purpose: str | None = None
    tags: tuple[str, ...] = ()


@dataclass
class Result:
    case: Case
    # 1-based rank of the first passage matching a gold reference; None when none matched.
    rank: int | None
    returned: int
    forbidden: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        if self.forbidden:
            return False
        if self.case.expect == "not_found":
            return self.returned == 0
        return self.rank is not None


def _normalized(text: str) -> str:
    return _SPACE.sub(" ", unicodedata.normalize("NFC", text).lower()).strip()


def _refs(raw: Any, case_id: str, key: str) -> tuple[Ref, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise CaseError(f"{case_id}: {key} must be a list")
    refs = []
    for item in raw:
        if not isinstance(item, dict) or not item.get("doc_no"):
            raise CaseError(f"{case_id}: every {key} entry needs a doc_no")
        quote = item.get("quote")
        refs.append(Ref(str(item["doc_no"]), _normalized(quote) if quote else None))
    return tuple(refs)


def parse_cases(lines: Iterable[str]) -> list[Case]:
    cases: list[Case] = []
    seen: set[str] = set()
    for lineno, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CaseError(f"line {lineno}: {exc}") from exc
        case_id = str(raw.get("id") or "")
        if not case_id or case_id in seen:
            raise CaseError(f"line {lineno}: missing or repeated id {case_id!r}")
        seen.add(case_id)
        expect = raw.get("expect")
        if expect not in EXPECTS:
            raise CaseError(f"{case_id}: expect must be one of {EXPECTS}")
        gold = _refs(raw.get("gold"), case_id, "gold")
        if expect == "found" and not gold:
            raise CaseError(f"{case_id}: a found case names its gold passages")
        if expect == "not_found" and gold:
            raise CaseError(f"{case_id}: a not_found case has no gold")
        try:
            as_of = datetime.date.fromisoformat(str(raw["as_of"]))
        except (KeyError, ValueError) as exc:
            raise CaseError(f"{case_id}: as_of must be an ISO date") from exc
        cases.append(
            Case(
                id=case_id,
                question=str(raw.get("question") or ""),
                as_of=as_of,
                expect=expect,
                gold=gold,
                forbid=_refs(raw.get("forbid"), case_id, "forbid"),
                purpose=raw.get("purpose"),
                tags=tuple(str(t) for t in raw.get("tags") or ()),
            )
        )
    return cases


def _matches(passage: Passage, ref: Ref) -> bool:
    if passage.doc_no != ref.doc_no:
        return False
    return ref.quote is None or ref.quote in _normalized(passage.text)


def score(case: Case, passages: list[Passage]) -> Result:
    rank = next(
        (i for i, p in enumerate(passages, start=1) if any(_matches(p, g) for g in case.gold)),
        None,
    )
    forbidden = sorted(
        {p.doc_no or "" for p in passages if any(_matches(p, f) for f in case.forbid)}
    )
    return Result(case, rank, len(passages), forbidden)


def _rates(results: list[Result]) -> dict[str, Any]:
    found = [r for r in results if r.case.expect == "found"]
    absent = [r for r in results if r.case.expect == "not_found"]
    out: dict[str, Any] = {"cases": len(results), "passed": sum(r.passed for r in results)}
    if found:
        for k in CUTOFFS:
            hits = sum(r.rank is not None and r.rank <= k for r in found)
            out[f"recall@{k}"] = round(hits / len(found), 4)
        out["mrr"] = round(sum(1 / r.rank for r in found if r.rank) / len(found), 4)
    if absent:
        out["not_found_accuracy"] = round(sum(r.returned == 0 for r in absent) / len(absent), 4)
    out["forbidden_rate"] = round(sum(bool(r.forbidden) for r in results) / len(results), 4)
    return out


def summarize(results: list[Result], services: SearchServices) -> dict[str, Any]:
    tags = sorted({t for r in results for t in r.case.tags})
    return {
        "halves": {"keyword": True, "dense": services.dense_configured, "rerank": services.reranks},
        "overall": _rates(results) if results else {"cases": 0},
        "by_tag": {t: _rates([r for r in results if t in r.case.tags]) for t in tags},
        "misses": [
            {
                "id": r.case.id,
                "expect": r.case.expect,
                "rank": r.rank,
                "returned": r.returned,
                "forbidden": r.forbidden,
            }
            for r in results
            if not r.passed
        ],
    }


async def run(
    org_id: uuid.UUID,
    space_code: str,
    cases: list[Case],
    services: SearchServices,
    *,
    limit: int = LIMIT,
) -> dict[str, Any]:
    results: list[Result] = []
    async with scoped_session(ROLE, org_id) as session:
        space_id = await session.scalar(
            select(org.DataSpace.id).where(
                org.DataSpace.org_id == org_id, org.DataSpace.code == space_code
            )
        )
        if space_id is None:
            raise CaseError(f"no Data Space {space_code} in org {org_id}")
        for case in cases:
            passages = await retrieve(
                session,
                services,
                data_space_id=space_id,
                question=case.question,
                as_of=case.as_of,
                purpose=case.purpose,
                limit=limit,
            )
            results.append(score(case, passages))
    return summarize(results, services)


def main(argv: list[str] | None = None, stdin: TextIO = sys.stdin) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--org", type=uuid.UUID, required=True)
    parser.add_argument("--space", required=True, help="Data Space code, e.g. DS-AW")
    parser.add_argument("--limit", type=int, default=LIMIT)
    parser.add_argument("cases", help="a JSONL file, or - for stdin")
    args = parser.parse_args(argv)
    if args.cases == "-":
        cases = parse_cases(stdin)
    else:
        with open(args.cases, encoding="utf-8") as f:
            cases = parse_cases(f)
    report = asyncio.run(run(args.org, args.space, cases, services_from_env(), limit=args.limit))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
