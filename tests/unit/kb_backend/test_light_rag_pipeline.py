"""Pure-function unit tests for the LightRAG retrieval pipeline
(backend/services/kb_backend/pipeline/light_rag/pipeline.py).

These functions carry the trickiest logic in the subsystem (Vietnamese-aware
canonicalization, RRF fusion, entity ranking, question-shape typing) yet had
ZERO coverage. They need no infrastructure — the DB clients are only touched by
the orchestrator — so they are the highest-ROI place to start closing the KB
test gap. The heavy runtime deps (sqlalchemy/qdrant/neo4j) are import-only for
these functions; importorskip keeps the file Docker-free when they're absent.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit

# Import-time deps of the pipeline module (not used by the pure functions below).
pytest.importorskip("sqlalchemy")
pytest.importorskip("qdrant_client")
pytest.importorskip("neo4j")

from services.kb_backend.pipeline.light_rag import config as cfg  # noqa: E402
from services.kb_backend.pipeline.light_rag.pipeline import (  # noqa: E402
    ENTITY_TYPE_FIELDS,
    _CANONICALIZE_SYSTEM,
    _canon,
    _clamp01,
    _entities_from_relationships,
    _parse_json_obj,
    _rank_entities,
    _sigmoid,
    preferred_entity_types,
    rrf_merge,
)


# ── _canon — Vietnamese-preserving canonicalization ────────────────────────────

def test_canon_preserves_vietnamese_diacritics():
    # An ASCII fold would destroy this to "s_i_g_n"; diacritics are lexical.
    assert _canon("Sài Gòn") == "sài_gòn"
    assert _canon("Đường sắt Việt Nam") == "đường_sắt_việt_nam"


def test_canon_lowercases_and_joins_separators():
    assert _canon("RAG") == "rag"
    assert _canon("New York / NYC") == "new_york_nyc"
    assert _canon("A - B") == "a_b"


def test_canon_drops_punctuation_and_collapses_underscores():
    assert _canon("Nghị định 13!!!") == "nghị_định_13"
    assert _canon("  spaced   out  ") == "spaced_out"


def test_canon_composed_equals_decomposed():
    # NFC normalisation: decomposed "ậ" must canonicalize identically to composed.
    composed = _canon("bậc")
    decomposed = _canon("bậc")  # a + dot-below + circumflex
    assert composed == decomposed == "bậc"


# ── rrf_merge — reciprocal rank fusion ─────────────────────────────────────────

def _hit(pid: str, score: float = 1.0) -> dict:
    return {"point_id": pid, "score": score, "payload": {"block_id": pid}}


def test_rrf_merge_fuses_and_dedupes_across_lists():
    list_a = [_hit("x"), _hit("y")]
    list_b = [_hit("y"), _hit("z")]
    merged = rrf_merge([list_a, list_b], top_n=10)
    ids = [m["point_id"] for m in merged]
    # y appears in both lists → highest fused score → ranked first.
    assert ids[0] == "y"
    assert set(ids) == {"x", "y", "z"}


def test_rrf_merge_uses_config_k_constant():
    # A single list, rank-1 item scores exactly 1/(RRF_K + 1).
    merged = rrf_merge([[_hit("solo")]], top_n=1)
    assert merged[0]["rrf_score"] == pytest.approx(1.0 / (cfg.RRF_K + 1))


def test_rrf_merge_respects_top_n_and_skips_missing_ids():
    lists = [[_hit("a"), _hit("b"), _hit("c")], [{"score": 1.0}]]  # 2nd hit has no point_id
    merged = rrf_merge(lists, top_n=2)
    assert len(merged) == 2
    assert all(m.get("point_id") for m in merged)


def test_rrf_merge_empty_input():
    assert rrf_merge([], top_n=5) == []
    assert rrf_merge([[], []], top_n=5) == []


# ── _rank_entities — frequency × relevance seeding ─────────────────────────────

def test_rank_entities_orders_by_frequency_times_score():
    pass1 = [[
        {"score": 2.0, "payload": {"entities": ["hanoi", "vietnam"]}},
        {"score": 1.0, "payload": {"entities": ["vietnam"]}},
    ]]
    # vietnam: 2.0 + 1.0 = 3.0 ; hanoi: 2.0 → vietnam ranks first.
    assert _rank_entities(pass1, top_n=2) == ["vietnam", "hanoi"]


def test_rank_entities_top_n_limits_seeds():
    pass1 = [[{"score": 1.0, "payload": {"entities": ["a", "b", "c", "d"]}}]]
    assert len(_rank_entities(pass1, top_n=2)) == 2


def test_rank_entities_ignores_blank_entities():
    pass1 = [[{"score": 1.0, "payload": {"entities": ["", "  ", "real"]}}]]
    assert _rank_entities(pass1, top_n=5) == ["real"]


# ── preferred_entity_types — question-shape → answer type ──────────────────────

def test_preferred_types_vietnamese_where_question():
    assert "location" in preferred_entity_types("Trận Điện Biên Phủ ở đâu?")


def test_preferred_types_english_who_question():
    types = preferred_entity_types("Who signed the treaty?")
    assert "person" in types


def test_preferred_types_law_keywords():
    assert "law_document" in preferred_entity_types("Nghị định 13 quy định gì?")


def test_preferred_types_deduplicates():
    types = preferred_entity_types("Ai là người ở đâu", intent="person")
    assert len(types) == len(set(types))


def test_preferred_types_empty_when_no_hint():
    assert preferred_entity_types("xyzzy") == []


# ── _parse_json_obj — fenced-JSON tolerance ────────────────────────────────────

def test_parse_json_plain_object():
    assert _parse_json_obj('{"a": 1}') == {"a": 1}


def test_parse_json_strips_code_fence():
    assert _parse_json_obj('```json\n{"a": 1}\n```') == {"a": 1}


def test_parse_json_garbage_returns_empty_dict():
    assert _parse_json_obj("not json at all") == {}
    assert _parse_json_obj("") == {}


def test_parse_json_non_object_returns_empty_dict():
    assert _parse_json_obj("[1, 2, 3]") == {}


# ── small numeric guards ───────────────────────────────────────────────────────

def test_sigmoid_bounds_and_midpoint():
    assert _sigmoid(0.0) == pytest.approx(0.5)
    assert 0.0 < _sigmoid(-50.0) < 0.01
    assert 0.99 < _sigmoid(50.0) <= 1.0


def test_sigmoid_handles_bad_input():
    # Non-numeric strings raise ValueError inside float() → caught → 0.0.
    assert _sigmoid("abc") == 0.0  # type: ignore[arg-type]
    assert _sigmoid(None) == 0.0   # type: ignore[arg-type]


def test_clamp01():
    assert _clamp01(-3) == 0.0
    assert _clamp01(0.4) == 0.4
    assert _clamp01(9) == 1.0
    assert _clamp01("bad") == 0.0


# ── _entities_from_relationships ───────────────────────────────────────────────

def test_entities_from_relationships_pulls_endpoints():
    rels = [["NYU", "distance", "Trento"], ["A", "rel", "B"]]
    assert _entities_from_relationships(rels) == ["NYU", "Trento", "A", "B"]


def test_entities_from_relationships_ignores_malformed():
    assert _entities_from_relationships([["only_two", "rel"], "nope", []]) == []


# ── single-sourced vocabulary (no drift between prompt and config) ─────────────

def test_entity_type_fields_are_the_closed_vocab():
    assert "person" in ENTITY_TYPE_FIELDS
    assert "organization" in ENTITY_TYPE_FIELDS
    assert len(ENTITY_TYPE_FIELDS) == 8


def test_canonicalize_prompt_lists_every_config_intent():
    # The prompt's intent vocabulary is built from cfg.CONTENT_INTENTS — assert the
    # single-source really flows through so the two can never drift.
    for intent in cfg.CONTENT_INTENTS:
        assert intent in _CANONICALIZE_SYSTEM
