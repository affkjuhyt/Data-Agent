"""The retrieval cases (model/eval/cases/retrieval.jsonl) against the documents of the Data Space
DS-AW (deploy/docker/sql/v3-adventureworks-documents.sql), keyword half only (RG-01, RG-02).

Two things hold whatever the ranking: every gold quote is in a passage of its document, so an edit
of the corpus cannot leave the gold pointing at nothing; and no expired, replaced or injected
document comes back, which is the pre-filter, not a matter of tuning. The rates are printed as the
keyword baseline; the full stack's are measured on a cluster with `make eval-retrieval`.
"""

from __future__ import annotations

import json
import pathlib
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

import pytest

from services.worker_agent.documents.retrieval_eval import Case, parse_cases, run
from services.worker_agent.documents.search import SearchServices

from .conftest import Seed
from .test_document_answers import library

pytestmark = [pytest.mark.integration]

ROOT = pathlib.Path(__file__).parents[3]
SEED = ROOT / "deploy" / "docker" / "sql" / "v3-adventureworks-documents.sql"
CASES = ROOT / "model" / "eval" / "cases" / "retrieval.jsonl"
# The values the generated seed writes: literals, dates, NULL, psql variables, numbers, booleans.
_TOKEN = re.compile(r"'(?:[^']|'')*'|date '[\d-]+'|NULL|:\w+::uuid|-?\d+|true|false|;")


@dataclass(frozen=True)
class SeedDocument:
    id: str
    title: str
    doc_no: str
    effective_from: str | None
    effective_to: str | None
    supersedes: str | None
    pages: int


@dataclass(frozen=True)
class SeedChunk:
    document: str
    page_from: int
    page_to: int
    section_path: str | None
    context_header: str | None
    text: str
    effective_from: str | None
    injection: bool


def _normalized(text: str) -> str:
    # As retrieval_eval compares a quote: NFC, lower case, one space.
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text).lower()).strip()


def _value(token: str) -> object:
    if token == "NULL":
        return None
    if token in ("true", "false"):
        return token == "true"
    if token.startswith("date "):
        return token[6:-1]
    if token.startswith("'"):
        return token[1:-1].replace("''", "'")
    if token.startswith(":"):
        return token
    return int(token)


def _rows(sql: str, table: str, width: int) -> list[list[object]]:
    rows: list[list[object]] = []
    for start in [m.end() for m in re.finditer(rf"INSERT INTO {table} \(", sql)]:
        body = sql[sql.index("VALUES", start) + len("VALUES") :]
        values: list[object] = []
        for m in _TOKEN.finditer(body):
            if m.group() == ";":
                break
            values.append(_value(m.group()))
        assert len(values) % width == 0, f"{table}: {len(values)} values"
        rows += [values[i : i + width] for i in range(0, len(values), width)]
    return rows


def corpus() -> tuple[list[SeedDocument], list[SeedChunk]]:
    sql = SEED.read_text(encoding="utf-8")
    documents = [
        SeedDocument(r[0], r[9], r[10], r[12], r[13], r[14], r[15])  # type: ignore[arg-type]
        for r in _rows(sql, "document.document", 19)
    ]
    chunks = [
        SeedChunk(r[2], r[4], r[5], r[6], r[7], r[8], r[10], r[12])  # type: ignore[arg-type]
        for r in _rows(sql, "document.chunk", 13)
    ]
    return documents, chunks


def _cases() -> list[Case]:
    return parse_cases(CASES.read_text(encoding="utf-8").splitlines())


def test_the_cases_point_at_real_passages() -> None:
    documents, chunks = corpus()
    assert len(documents) == 22
    by_id = {d.id: d for d in documents}
    texts: dict[str, list[str]] = {}
    for c in chunks:
        texts.setdefault(by_id[c.document].doc_no, []).append(_normalized(c.text))
    for case in _cases():
        for ref in [*case.gold, *case.forbid]:
            assert ref.doc_no in texts, f"{case.id}: no DS-AW document {ref.doc_no}"
        for ref in case.gold:
            assert ref.quote is None or any(
                ref.quote in t for t in texts[ref.doc_no]
            ), f"{case.id}: stale quote {ref.quote!r}"


async def _keyword_report(seed: Seed, cases: list[Case]) -> dict[str, Any]:
    """The corpus written as the seed writes it, in one collection, and the cases run over it."""
    documents, chunks = corpus()
    lib = library(seed)
    ids: dict[str, object] = {}
    # Replaced documents first, as the seed writes them: supersedes_id names an existing row.
    for d in sorted(documents, key=lambda d: d.supersedes is not None):
        ids[d.id] = seed.row(
            "document.document",
            org_id=lib.org,
            collection_id=lib.collection,
            sha256=d.id,
            media="html",
            file_name=d.doc_no,
            object_key="k",
            size_bytes=10,
            title=d.title,
            doc_no=d.doc_no,
            status="ready",
            page_count=d.pages,
            effective_from=d.effective_from,
            effective_to=d.effective_to,
            supersedes_id=ids[d.supersedes] if d.supersedes else None,
        )
        for page_no in range(1, d.pages + 1):
            seed.row(
                "document.document_page",
                org_id=lib.org,
                document_id=ids[d.id],
                page_no=page_no,
                page_hash=f"{d.id}-{page_no}",
                usable=True,
            )
    for c in chunks:
        seed.row(
            "document.chunk",
            org_id=lib.org,
            document_id=ids[c.document],
            collection_id=lib.collection,
            page_from=c.page_from,
            page_to=c.page_to,
            section_path=c.section_path,
            context_header=c.context_header,
            text=c.text,
            tokens=len(c.text.split()),
            effective_from=c.effective_from,
            injection_suspected=c.injection,
        )

    report = await run(lib.org, "DS-NH", cases, SearchServices(qdrant_url=None, embed_url=None))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


async def test_no_expired_replaced_or_injected_passage_comes_back(seed: Seed) -> None:
    cases = _cases()
    report = await _keyword_report(seed, cases)
    assert report["overall"]["cases"] == len(cases)
    assert report["overall"]["forbidden_rate"] == 0.0
    assert report["by_tag"]["theo-ngay"]["passed"] == report["by_tag"]["theo-ngay"]["cases"]


# Gaps of the keyword half the corpus shows. Each is strict: fixing it turns the test red, and the
# marker goes.
@pytest.mark.xfail(
    strict=True,
    reason="retrieve._STOP lists Vietnamese function words only and the full-text index uses the "
    "'simple' configuration (no stemming): an English question's 'what/should/the' and "
    "'pedals' vs 'pedal' count against the 0.6 overlap bar, so most English cases find nothing",
)
async def test_english_questions_find_their_passage_on_the_keyword_half(seed: Seed) -> None:
    cases = [c for c in _cases() if "tieng-anh" in c.tags and c.expect == "found"]
    report = await _keyword_report(seed, cases)
    assert report["overall"]["passed"] == len(cases)


@pytest.mark.xfail(
    strict=True,
    reason="the keyword half matches words as typed: a question without diacritics shares no "
    "word with a passage that has them (no unaccent), so only the dense half can find it",
)
async def test_questions_without_diacritics_find_their_passage_on_the_keyword_half(
    seed: Seed,
) -> None:
    cases = [c for c in _cases() if "khong-dau" in c.tags and c.expect == "found"]
    report = await _keyword_report(seed, cases)
    assert report["overall"]["passed"] == len(cases)
