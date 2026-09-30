"""The workloads to benchmark — one per LLM role the product actually runs.

Nothing here is a synthetic prompt. Each profile loads the same system prompt
file its node loads at import time, and carries the `max_tokens` / `temperature`
that node passes to litellm, cited below. A benchmark built from round numbers
("512-token prompt, 128-token output") measures the round numbers; the point of
these is that a change to `planner_system.txt` shows up in the next run.

Prompt size is the whole story for time-to-first-token, and the six roles differ
by more than an order of magnitude in it, so they cannot share one measurement:

    guardrail     ~0.6k prefix   64 out    fast tier   every request, blocking
    planner       ~2.5k prefix  256 out    fast tier   every request, blocking
    judge         ~0.5k prefix  128 out    fast tier   after the answer
    text_to_sql   schema-sized  512 out    main tier   data path
    responder     retrieval-sized 1000 out main tier   the streamed answer
    orch-planner  ~1.0k prefix  256 out    main tier   multi-agent path

The user turn varies per `variant` while the system prefix stays byte-identical,
because that is the shape production sends and therefore the shape vLLM's prefix
cache sees. Holding the tail fixed too would report a cache hit rate no real
traffic achieves.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
_WR_PROMPTS = REPO_ROOT / "backend" / "services" / "workflow_runtime" / "prompts"
_ORCH_PROMPTS = REPO_ROOT / "backend" / "services" / "workflow_runtime" / "application" / "orchestrator" / "prompts"
_SHARED_PROMPTS = REPO_ROOT / "backend" / "libs" / "shared" / "prompts"
_EVAL_CASES = REPO_ROOT / "model" / "eval" / "cases"


class ProfileError(Exception):
    """A profile that cannot be built. Fatal on purpose: a benchmark that
    quietly substitutes a placeholder prompt reports a latency for a prompt the
    product never sends."""


def _prompt(path: Path) -> str:
    if not path.exists():
        raise ProfileError(f"missing prompt file: {path}")
    return path.read_text(encoding="utf-8")


def _cases(name: str) -> list[dict[str, Any]]:
    path = _EVAL_CASES / f"{name}.jsonl"
    if not path.exists():
        raise ProfileError(f"missing eval case file: {path}")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _cases_with_contexts() -> list[dict[str, Any]]:
    """Grounding cases that carry their source passages.

    The `live-kb-lookup` case (gnd-021) has none: its context is
    whatever the deployed agent retrieves, so there is nothing to put in a
    prompt offline. Taking it anyway raised KeyError for every profile.
    """
    corpus = [case for case in _cases("grounding") if case.get("contexts")]
    if not corpus:
        raise ProfileError("no grounding case carries contexts to build prompts from")
    return corpus


# The caps handlers/data.py enforces on the schema block it pastes into the
# text-to-SQL prompt, and the caps responder.py enforces on the retrieved
# context. Benchmarking at the cap is deliberate: the cap is the latency budget
# the product has committed to, and a measurement below it says nothing about
# the request that finally hits it.
MAX_SCHEMA_TABLES = 12      # handlers/data.py::_MAX_SCHEMA_TABLES
MAX_SCHEMA_CHARS = 6000     # handlers/data.py::_MAX_SCHEMA_CHARS
CONTEXT_MAX_CHUNKS = 5      # responder.py cfg "context_max_chunks"
CONTEXT_MAX_CHARS = 4000    # responder.py cfg "context_max_chars"


def _schema_text() -> str:
    """A schema block at the size the text-to-SQL prompt is capped to.

    The real block is a tenant's warehouse, so there is none in the repo to
    load. Two halves, and only one of them is made up: the *columns* come from
    `cases/catalogs.json`, so they are the columns the SQL corpus is written
    against; the block is then extended with further tables of the same shape
    until it reaches the 12-table / 6000-char ceiling the code enforces. The
    extra table names are filler and mean nothing — what is being measured is
    how long a 6000-character prompt takes to prefill, and that does not depend
    on what the tables are called.
    """
    catalogs = json.loads((_EVAL_CASES / "catalogs.json").read_text(encoding="utf-8"))
    tables: dict[str, dict[str, str]] = {}
    for name, catalog in catalogs.items():
        if name.startswith("_") or not isinstance(catalog, dict):
            continue
        for metric in catalog.get("metrics", []):
            columns = tables.setdefault(metric["source_table"], {"id": "bigint"})
            columns[metric["expression"]] = "numeric"
            columns[metric["time_column"]] = "timestamptz"
            for dimension in metric.get("dimensions", []):
                columns[dimension["column"]] = "text"
            for filt in metric.get("default_filters", []):
                columns[filt["column"]] = "text"
    if not tables:
        raise ProfileError("catalogs.json declared no metrics to derive a schema from")

    def render(table: str, columns: dict[str, str]) -> str:
        body = ",\n".join(f"  {col} {typ}" for col, typ in sorted(columns.items()))
        return f"CREATE TABLE {table} (\n{body}\n);"

    shapes = sorted(tables.items())
    blocks = [render(table, columns) for table, columns in shapes]

    # Both caps bind together in production — 12 tables that between them fill
    # 6000 characters — so the filler tables are widened rather than merely
    # counted. A dozen three-column tables would be 12 tables and a quarter of
    # the prompt, which is the wrong prefill to measure.
    filler = MAX_SCHEMA_TABLES - len(blocks)
    if filler > 0:
        budget = (MAX_SCHEMA_CHARS - len("\n\n".join(blocks))) // filler
        for index in range(filler):
            table, columns = shapes[index % len(shapes)]
            widened = dict(columns)
            attribute = 0
            while len(render(f"{table}_part{index:02d}", widened)) < budget:
                attribute += 1
                widened[f"attr_{attribute:02d}"] = "text"
            blocks.append(render(f"{table}_part{index:02d}", widened))

    text = "\n\n".join(blocks)
    return text[:MAX_SCHEMA_CHARS]


@dataclass(frozen=True)
class Profile:
    """One benchmarkable role.

    `alias` is what the code asks LiteLLM for; `tier` is which vLLM backend that
    alias resolves to in `deploy/docker/litellm/config.yaml` today. Both are
    recorded so a report stays readable after an alias is re-pointed — that
    re-pointing is exactly the change a benchmark is run to justify.
    """

    name: str
    alias: str
    tier: str
    system: str
    max_tokens: int
    temperature: float
    user_turns: tuple[str, ...]

    def messages(self, variant: int) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": self.system},
            {"role": "user", "content": self.user_turns[variant % len(self.user_turns)]},
        ]


# How many distinct user turns each profile offers. A sweep that runs more
# requests than it has turns replays earlier prompts, and vLLM then serves them
# from the prefix cache — which reports a hit rate the profile manufactured
# rather than one production would see. `run.py` warns when a run asks for more.
VARIANTS = 96


def _questions() -> tuple[str, ...]:
    """Every question in the corpus, intent cases first.

    Both corpora are used because a sweep needs more distinct prompts than any
    one of them has, and because they are the same *kind* of thing: a user
    question arriving at a planner. Two pools only look like padding until a
    run at a non-zero variant offset runs out of the first one.
    """
    return tuple(
        [case["question"] for case in _cases("intent")]
        + [case["question"] for case in _cases("sql")]
        + [case["question"] for case in _cases("grounding")]
    )


def _guardrail_turns() -> tuple[str, ...]:
    """Mirrors guardrail_check.llm_guardrail_check: CHECK_TYPE header + the text.

    Ordinary questions *and* the policy corpus's adversarial inputs, because
    that mix is what the guardrail sees. A guardrail benchmarked only on benign
    input is being asked an easier question than production asks it, and the
    refusal path is not the same length as the pass-through path.
    """
    texts = [*_questions(), *(case["input"] for case in _cases("policy"))]
    return tuple(f"CHECK_TYPE: input\nCONTENT: {text}" for text in texts)


def _judge_turns() -> tuple[str, ...]:
    """An answer and the sources it was built from.

    The corpus has fewer cases than a sweep has requests, so each variant pairs
    a case with a rotating distractor passage. That is not padding for its own
    sake: the judge sees the whole reranked set in production, gold chunk and
    near-misses together, so a variant built from only the gold context would
    be an easier and shorter prompt than the real one.
    """
    corpus = _cases_with_contexts()
    passages = [c for case in corpus for c in case["contexts"]]
    turns = []
    for index in range(VARIANTS):
        case = corpus[index % len(corpus)]
        distractors = [passages[(index + offset) % len(passages)] for offset in (1, 2)]
        contexts = "\n".join(f"- {c}" for c in [*case["contexts"], *distractors])
        turns.append(
            f"CÂU HỎI: {case['question']}\n\nNGUỒN:\n{contexts}\n\n"
            f"CÂU TRẢ LỜI: {case['ground_truth']}"
        )
    return tuple(turns)


def _sql_turns(schema: str) -> tuple[str, ...]:
    # Same template handlers/data.py builds inline for the text_to_sql call.
    return tuple(
        f"Chuyển câu hỏi sau thành SQL.\n\nSchema:\n{schema}\n\n"
        f"Chỉ trả SQL thuần, không giải thích.\n\nCâu hỏi: {question}"
        for question in _questions()
    )


def _responder_turns() -> tuple[str, ...]:
    """A question plus retrieved context at the size responder.py caps it to.

    `[n] source=…` is the header the responder actually renders, kept because
    the citation format is part of what the model has to parse. The corpus
    passages are one sentence each, so they are cycled until the block reaches
    CONTEXT_MAX_CHARS across CONTEXT_MAX_CHUNKS items — the real ceiling, from
    real Vietnamese text. Vietnamese costs meaningfully more tokens per
    character than the English a generic benchmark pads with, and on this
    product that difference is the measurement rather than a detail.

    Each variant starts its window at a different passage, so no two requests
    in a sweep send the same context.
    """
    corpus = _cases_with_contexts()
    passages = [c for case in corpus for c in case["contexts"]]
    per_chunk = CONTEXT_MAX_CHARS // CONTEXT_MAX_CHUNKS
    turns = []
    for index in range(VARIANTS):
        case = corpus[index % len(corpus)]
        cursor = index
        parts = []
        for n in range(CONTEXT_MAX_CHUNKS):
            body = ""
            while len(body) < per_chunk:
                body += passages[cursor % len(passages)] + " "
                cursor += 1
            parts.append(f"[{n + 1}] source=kb/doc-{index:02d}-{n}.md\n{body[:per_chunk].strip()}")
        turns.append("NGUỒN:\n" + "\n\n".join(parts) + f"\n\nCÂU HỎI: {case['question']}")
    return tuple(turns)


def load() -> dict[str, Profile]:
    """Every profile, keyed by name. Built at call time, not import time, so a
    missing prompt file fails the run that needs it with a path in the message."""
    schema = _schema_text()
    profiles = [
        # guardrail_check.py: max_tokens 64, temperature 0, alias "guardrail".
        Profile("guardrail", "guardrail", "fast",
                _prompt(_SHARED_PROMPTS / "guardrail_system.txt"), 64, 0.0,
                _guardrail_turns()),
        # nodes/model_harness/planner.py: max_tokens 256, temperature 0.
        Profile("planner", "planner", "fast",
                _prompt(_WR_PROMPTS / "planner_system.txt"), 256, 0.0,
                _questions()),
        # nodes/model_harness/judge.py: max_tokens 128, temperature 0.
        Profile("judge", "judge", "fast",
                _prompt(_WR_PROMPTS / "judge_system.txt"), 128, 0.0,
                _judge_turns()),
        # handlers/data.py text_to_sql: max_tokens 512, temperature 0. That call
        # sends no system turn — the schema rides in the user turn — so the
        # system prefix here is the responder's short one, matching production's
        # much weaker prefix-cache position on this path.
        Profile("text_to_sql", "text_to_sql", "main",
                _prompt(_WR_PROMPTS / "responder_system.txt"), 512, 0.0,
                _sql_turns(schema)),
        # nodes/model_harness/responder.py: max_tokens 1000, temperature 0.2.
        Profile("responder", "responder", "main",
                _prompt(_WR_PROMPTS / "responder_system.txt")
                + "\n" + _prompt(_WR_PROMPTS / "responder_citation_suffix.txt"),
                1000, 0.2, _responder_turns()),
        # orchestrator/planner — the multi-agent path's blocking plan step.
        Profile("orchestrator-planner", "orchestrator-planner", "main",
                _prompt(_ORCH_PROMPTS / "planner_system.txt"), 256, 0.0,
                _questions()),
    ]
    return {profile.name: profile for profile in profiles}
