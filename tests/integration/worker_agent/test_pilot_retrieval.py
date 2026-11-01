"""The retrieval cases of the pilot (model/eval/cases/retrieval.jsonl) against the pilot's own
passages (deploy/docker/sql/v3-pilot-seed.sql), keyword half only (RG-01, RG-02).

Two things hold whatever the ranking: every gold quote is in a passage of its document, so an edit
of the seed cannot leave the gold pointing at nothing; and no expired or replaced document comes
back at its case's date, which is the validity filter, not a matter of tuning. The rates are
printed as the keyword baseline; the full stack's are measured on a cluster with the CLI.
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest

from services.worker_agent.documents.retrieval_eval import parse_cases, run
from services.worker_agent.documents.search import SearchServices

from .conftest import Seed
from .test_document_answers import add_document, library

pytestmark = [pytest.mark.integration]

ROOT = pathlib.Path(__file__).parents[3]
SEED = ROOT / "deploy" / "docker" / "sql" / "v3-pilot-seed.sql"
CASES = ROOT / "model" / "eval" / "cases" / "retrieval.jsonl"
_CHUNK = re.compile(
    r"'(QD-[A-Z]+-\d{4}/\d{2})',\s*'([^']*)',\s*\d+,\s*date '([\d-]+)',\s*(?:NULL|date '([\d-]+)')"
)


def _pilot_chunks() -> list[tuple[str, str, str, str | None]]:
    """(doc_no, text, effective_from, effective_to) of each chunk the seed writes."""
    sql = SEED.read_text(encoding="utf-8")
    block = sql[sql.index("INSERT INTO document.chunk (") :]
    block = block[: block.index(";\n")]
    return [(d, t, f, to or None) for d, t, f, to in _CHUNK.findall(block)]


async def test_the_pilot_cases_point_at_real_passages_and_respect_validity(seed: Seed) -> None:
    chunks = _pilot_chunks()
    assert len(chunks) == 3
    cases = parse_cases(CASES.read_text(encoding="utf-8").splitlines())
    texts = {doc_no: text.lower() for doc_no, text, _, _ in chunks}
    for case in cases:
        for ref in case.gold:
            assert ref.doc_no in texts, f"{case.id}: no pilot document {ref.doc_no}"
            assert ref.quote is None or ref.quote in texts[ref.doc_no], f"{case.id}: stale quote"

    lib = library(seed)
    ids: dict[str, object] = {}
    for doc_no, text, start, end in chunks:
        # QD-GV-2026/02 replaces QD-GV-2026/01, as the seed's supersedes_id says.
        replaces = ids.get("QD-GV-2026/01") if doc_no == "QD-GV-2026/02" else None
        ids[doc_no], _ = add_document(
            seed,
            lib,
            text,
            title=doc_no,
            doc_no=doc_no,
            effective_from=start,
            effective_to=end,
            supersedes=replaces,  # type: ignore[arg-type]
        )

    report = await run(lib.org, "DS-NH", cases, SearchServices(qdrant_url=None, embed_url=None))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    assert report["overall"]["cases"] == len(cases)
    assert report["overall"]["forbidden_rate"] == 0.0
    assert report["by_tag"]["theo-ngay"]["passed"] == report["by_tag"]["theo-ngay"]["cases"]
