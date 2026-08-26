"""Pure-function unit tests for the ingestion entity registry
(services/kb-ingestion-worker/utils/entity_registry.py).

Covers the deterministic, infra-free core of entity resolution: Vietnamese-aware
canonicalization, the specific-type merge guard, raw-entity cleaning, and the
final canonical de-dup. These back the three-band merge decision that keeps the
knowledge graph from fragmenting into near-duplicate nodes.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

pytestmark = pytest.mark.unit

# Import-time deps of the module (not exercised by the pure functions below).
pytest.importorskip("sqlalchemy")
pytest.importorskip("qdrant_client")

_WORKER_DIR = str(pathlib.Path(__file__).resolve().parents[2] / "services" / "kb-ingestion-worker")


@pytest.fixture(scope="module", autouse=True)
def _worker_on_path():
    # entity_registry does `from utils import config` — the worker dir must be on
    # sys.path for that bare package import to resolve.
    added = _WORKER_DIR not in sys.path
    if added:
        sys.path.insert(0, _WORKER_DIR)
    yield
    # Leave sys.path as-is if another module already needed it; only clean our add.
    if added and _WORKER_DIR in sys.path:
        sys.path.remove(_WORKER_DIR)


@pytest.fixture(scope="module")
def er(_worker_on_path):
    import importlib

    return importlib.import_module("utils.entity_registry")


# ── to_canonical — Vietnamese-preserving ───────────────────────────────────────

def test_to_canonical_preserves_diacritics(er):
    assert er.to_canonical("Sài Gòn") == "sài_gòn"
    assert er.to_canonical("Thành phố Hồ Chí Minh") == "thành_phố_hồ_chí_minh"


def test_to_canonical_folds_case_and_separators(er):
    assert er.to_canonical("Hoa Kỳ") == "hoa_kỳ"
    assert er.to_canonical("A/B-C") == "a_b_c"


def test_to_canonical_drops_punctuation_collapses_underscores(er):
    assert er.to_canonical("  Nghị định  13!  ") == "nghị_định_13"


def test_to_canonical_composed_equals_decomposed(er):
    assert er.to_canonical("bậc") == er.to_canonical("bậc")


# ── _types_conflict — specific-type merge guard ────────────────────────────────

def test_types_conflict_blocks_distinct_specific_types(er):
    assert er._types_conflict("person", "location") is True
    assert er._types_conflict("organization", "law_document") is True


def test_types_conflict_allows_same_type(er):
    assert er._types_conflict("person", "person") is False


def test_types_conflict_concept_never_blocks(er):
    # LLM typing is noisy; "concept" is treated as unknown and never blocks a merge.
    assert er._types_conflict("concept", "person") is False
    assert er._types_conflict("person", "concept") is False


def test_types_conflict_missing_types_never_block(er):
    assert er._types_conflict("", "person") is False
    assert er._types_conflict("person", "") is False


# ── _clean_raw_entities ────────────────────────────────────────────────────────

def test_clean_raw_entities_normalizes_and_drops_unnamed(er):
    raw = [
        {"name": "  Việt Nam  ", "description": " a country ", "type": "LOCATION",
         "aliases": ["VN", "  ", 5]},
        {"name": "", "description": "skip me"},
        {"not_a_dict": True},
    ]
    cleaned = er._clean_raw_entities(raw)
    assert len(cleaned) == 1
    entry = cleaned[0]
    assert entry["name"] == "Việt Nam"
    assert entry["description"] == "a country"
    assert entry["entity_type"] == "location"      # lower-cased
    assert entry["aliases"] == ["VN"]              # blanks + non-strings dropped


def test_clean_raw_entities_empty(er):
    assert er._clean_raw_entities([]) == []


# ── _dedupe_by_canonical ───────────────────────────────────────────────────────

def test_dedupe_keeps_first_occurrence(er):
    names = ["a", "b", "a", "", "c"]
    details = [{"n": 0}, {"n": 1}, {"n": 2}, {"n": 3}, {"n": 4}]
    out_names, out_details = er._dedupe_by_canonical(names, details)
    assert out_names == ["a", "b", "c"]
    # first "a" detail wins; empty placeholder dropped
    assert out_details == [{"n": 0}, {"n": 1}, {"n": 4}]


# ── deterministic entity point id ──────────────────────────────────────────────

def test_entity_point_id_is_deterministic(er):
    tid = "11111111-1111-1111-1111-111111111111"
    a = er._entity_point_id(tid, "sài_gòn")
    b = er._entity_point_id(tid, "sài_gòn")
    assert a == b
    # different canonical → different id
    assert a != er._entity_point_id(tid, "hà_nội")


def test_embed_text_combines_name_and_description(er):
    assert er._embed_text("Mỹ", "United States") == "Mỹ: United States"
    assert er._embed_text("Mỹ", "") == "Mỹ"
