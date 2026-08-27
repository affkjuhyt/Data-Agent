"""Document-level RBAC on the retrieval path.

`operations/access.py` has a real role hierarchy — PLATFORM_ADMIN >
AI_ENGINEER > EXECUTIVE > BUSINESS_OPERATOR — and `document_role_filter`
enforces it on the document APIs. `/api/pipeline/light-rag/retrieve`, the path
every agent answer is built from, never applied it: the endpoint had the
caller's `role_id` in hand from the verified identity and passed query,
tenant_id, collection and knowledge_base to the pipeline while dropping it. A
business operator's question could return chunks from a document only a platform
admin was allowed to open.

The audit's cited evidence — kb_search.py forging an admin role set — was fixed
earlier, and fixing it changed nothing on its own: the right roles arrived at a
door with no lock. These tests are about the lock.

Four things, in order of how badly each would hurt:

  * **The relational join is the only authority.** A vector index is not
    access-controlled storage. Filtering the Postgres fetch while still falling
    back to the Qdrant payload's summary would leak the same document by
    another name.
  * **Both retrieval paths are covered.** A fresh search and a semantic-cache
    hit resolve block ids through the same function, which is why the filter
    lives there and not in one branch.
  * **The result cache is keyed by role.** Otherwise the first caller to ask a
    question decides the answer everyone else gets.
  * **No tier, no retrieval.** An empty role_id must not mean "unfiltered".
"""
from __future__ import annotations

import pathlib
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
KB = REPO_ROOT / "services" / "kb_backend"

pytestmark = pytest.mark.unit

# The pipeline and the access module import qdrant/neo4j/sqlalchemy at module
# scope. The checks that need the real functions are guarded so they RUN in CI
# and SKIP where the drivers are absent, rather than joining the pile of
# dependency failures where a genuine regression would be invisible. Everything
# that can be checked without them — the wiring, read from the AST — runs
# everywhere and is not guarded.
def _needs_drivers() -> None:
    pytest.importorskip("sqlalchemy", reason="kb_backend drivers not installed here")
    pytest.importorskip("qdrant_client", reason="kb_backend drivers not installed here")
    pytest.importorskip("neo4j", reason="kb_backend drivers not installed here")


@pytest.fixture
def drivers():
    _needs_drivers()
    sys.path.insert(0, str(REPO_ROOT))
    yield

PLATFORM_ADMIN = "00000000-0000-0000-0000-000000000010"
AI_ENGINEER = "00000000-0000-0000-0000-000000000011"
BUSINESS_OPERATOR = "00000000-0000-0000-0000-000000000012"
EXECUTIVE = "00000000-0000-0000-0000-000000000013"


class TestTheHierarchyItself:
    """Unchanged behaviour, pinned because the retrieval filter now depends on
    it and a change here would silently widen or narrow every agent answer."""

    def test_a_role_sees_its_own_tier_and_everything_below(self, drivers):
        from services.kb_backend.operations.access import visible_role_ids

        assert visible_role_ids(PLATFORM_ADMIN) == [
            PLATFORM_ADMIN, AI_ENGINEER, EXECUTIVE, BUSINESS_OPERATOR,
        ]
        assert visible_role_ids(BUSINESS_OPERATOR) == [BUSINESS_OPERATOR]

    def test_an_unranked_role_sees_only_itself(self, drivers):
        from services.kb_backend.operations.access import visible_role_ids

        assert visible_role_ids("some-other-role") == ["some-other-role"]

    def test_a_lower_tier_cannot_see_a_higher_one(self, drivers):
        from services.kb_backend.operations.access import visible_role_ids

        assert PLATFORM_ADMIN not in visible_role_ids(EXECUTIVE)
        assert AI_ENGINEER not in visible_role_ids(BUSINESS_OPERATOR)


class TestTheFilterReachesTheQuery:
    """Read from the source: the fetch has to constrain KBData.role_id, and the
    endpoint has to hand it the caller's tier."""

    def _pipeline_source(self) -> str:
        return (KB / "pipeline" / "light_rag" / "pipeline.py").read_text(encoding="utf-8")

    def test_the_chunk_fetch_constrains_the_document_role(self):
        import ast

        tree = ast.parse(self._pipeline_source())
        fetch = next(
            n for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and n.name == "_fetch_chunks_postgres"
        )
        args = [a.arg for a in fetch.args.args]
        assert "role_id" in args, "the chunk fetch takes no role"

        attrs = {
            n.attr for n in ast.walk(fetch)
            if isinstance(n, ast.Attribute)
        }
        assert "role_id" in attrs, "the chunk fetch never constrains KBData.role_id"

    def test_both_retrieval_paths_pass_the_role_to_that_fetch(self):
        """A fresh search and a semantic-cache hit. Covering one would leave the
        bypass open through the other, and the cache path is the likelier one on
        a repeated question."""
        import ast

        tree = ast.parse(self._pipeline_source())
        calls = [
            n for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id == "_fetch_chunks_postgres"
        ]
        assert len(calls) == 2, f"expected both call sites, found {len(calls)}"
        for call in calls:
            passed = [a.id for a in call.args if isinstance(a, ast.Name)]
            assert "role_id" in passed, "a retrieval path fetches chunks without a role"

    @pytest.mark.parametrize("route", ["retrieve", "retrieve_stream"])
    def test_both_routes_take_the_role_from_the_verified_identity(self, route):
        import ast

        tree = ast.parse((KB / "router" / "knowledge" / "light_rag.py").read_text(encoding="utf-8"))
        fn = next(
            n for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == route
        )
        called = {
            n.func.id for n in ast.walk(fn)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        assert "_require_role_id" in called, f"{route} does not resolve a document tier"

        # And never from the request body, which the caller controls.
        body_attrs = {
            n.attr for n in ast.walk(fn)
            if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
            and n.value.id == "body"
        }
        assert "role_id" not in body_attrs


class TestNoTierNoRetrieval:
    def test_an_identity_without_a_document_role_is_refused(self, drivers):
        """Empty must not mean unfiltered — which is what it meant before, for
        every request."""
        from fastapi import HTTPException

        from services.kb_backend.router.knowledge.light_rag import _require_role_id

        assert _require_role_id({"role_id": BUSINESS_OPERATOR}) == BUSINESS_OPERATOR

        for empty in ({}, {"role_id": ""}, {"role_id": None}):
            with pytest.raises(HTTPException) as exc:
                _require_role_id(empty)
            assert exc.value.status_code == 403


class TestAVectorIndexIsNotAccessControlledStorage:
    def test_a_filtered_out_chunk_is_dropped_not_summarised(self, drivers):
        """The subtle half. `_build_result_list` falls back to the Qdrant
        payload's `summary` when there is no Postgres row — so filtering the
        fetch alone would have refused the document and then served a summary
        derived from it."""
        from services.kb_backend.pipeline.light_rag.pipeline import _build_result_list

        hits = [{"payload": {"block_id": "b1", "summary": "SECRET SUMMARY"},
                 "rerank_rank": 1, "rerank_score": 0.9}]

        # No Postgres row: the caller's tier does not reach this document.
        filtered = _build_result_list(hits, {}, {}, role_filtered=True)
        assert filtered == []

        # Same input without role scoping keeps the old behaviour, so this
        # cannot break callers that legitimately have no tier to apply.
        unfiltered = _build_result_list(hits, {}, {}, role_filtered=False)
        assert len(unfiltered) == 1
        assert unfiltered[0]["chunk_context"] == "SECRET SUMMARY"

    def test_a_visible_chunk_still_comes_back_whole(self, drivers):
        from services.kb_backend.pipeline.light_rag.pipeline import _build_result_list

        hits = [{"payload": {"block_id": "b1"}, "rerank_rank": 1, "rerank_score": 0.8}]
        rows = {"b1": {"block_id": "b1", "block_index": 0, "content": "visible text",
                       "doc_id": "d1", "doc_name": "Doc", "source_type": "pdf",
                       "current_tier": "gold"}}

        out = _build_result_list(hits, rows, {}, role_filtered=True)

        assert len(out) == 1
        assert out[0]["chunk_context"] == "visible text"
        assert out[0]["source"]["doc_id"] == "d1"

    def test_a_mixed_result_keeps_only_what_is_visible(self, drivers):
        from services.kb_backend.pipeline.light_rag.pipeline import _build_result_list

        hits = [
            {"payload": {"block_id": "visible"}, "rerank_rank": 1, "rerank_score": 0.9},
            {"payload": {"block_id": "hidden", "summary": "leak"}, "rerank_rank": 2, "rerank_score": 0.8},
        ]
        rows = {"visible": {"block_id": "visible", "content": "ok", "doc_id": "d1",
                            "doc_name": "D", "block_index": 0, "source_type": "pdf",
                            "current_tier": "gold"}}

        out = _build_result_list(hits, rows, {}, role_filtered=True)

        assert [r["source"]["block_id"] for r in out] == ["visible"]


class TestTheCacheCannotLaunderPrivilege:
    def test_the_result_cache_key_includes_the_role(self):
        """Otherwise the first caller to ask a question decides the answer
        everyone else gets — an admin's result, computed over documents a
        business operator cannot open, served straight back to that operator."""
        from libs.shared.kb_result_cache import query_hash

        admin = query_hash("doanh thu quy 3", "kb", role_id=PLATFORM_ADMIN)
        operator = query_hash("doanh thu quy 3", "kb", role_id=BUSINESS_OPERATOR)

        assert admin != operator

    def test_the_same_role_still_shares_a_key(self):
        """The cache has to keep working — role scoping must not turn every
        request into a miss."""
        from libs.shared.kb_result_cache import query_hash

        first = query_hash("cùng một câu hỏi", "kb", role_id=AI_ENGINEER)
        second = query_hash("  Cùng  một   câu hỏi ", "kb", role_id=AI_ENGINEER)

        assert first == second

    def test_the_semantic_cache_is_refiltered_rather_than_replayed(self):
        """The Qdrant semantic cache stores block ids, not text, and the hit
        path re-resolves them through the role-filtered fetch. So an entry
        written for a privileged caller yields only what the reader may see —
        which is why it needs no key change of its own."""
        import ast

        tree = ast.parse((KB / "pipeline" / "light_rag" / "pipeline.py").read_text(encoding="utf-8"))
        fn = next(
            n for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and n.name == "_cache_hit_results"
        )
        assert "role_id" in [a.arg for a in fn.args.args]
        called = {
            n.func.id for n in ast.walk(fn)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        assert {"_fetch_chunks_postgres", "_build_result_list"} <= called


class TestTheAgentStillSendsARealIdentity:
    """The earlier half of this finding, kept pinned: kb_search must not go back
    to asserting an identity of its own."""

    def _kb_search_source(self) -> str:
        return (REPO_ROOT / "services" / "workflow-runtime" / "nodes" / "sandbox"
                / "kb_search.py").read_text(encoding="utf-8")

    def test_no_hardcoded_role_set_survives_in_code(self):
        """AST, not substring — the module docstring describes the very headers
        it stopped sending, and a text scan matches its own explanation."""
        import ast

        tree = ast.parse(self._kb_search_source())
        literals = {
            n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and not isinstance(getattr(n, "parent", None), ast.Expr)
        }
        # Docstrings are Expr-wrapped Constants; drop them explicitly.
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                doc = ast.get_docstring(node, clean=False)
                if doc:
                    docstrings.add(doc)
        literals -= docstrings

        for value in literals:
            assert "platform-admin" not in value, f"hardcoded admin role in a literal: {value!r}"
            assert "X-Kong-Verified" not in value, f"forged gateway marker: {value!r}"

    def test_it_refuses_to_search_without_caller_roles(self):
        import ast

        tree = ast.parse(self._kb_search_source())
        fn = next(
            n for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and n.name == "_identity_headers"
        )
        raises = [n for n in ast.walk(fn) if isinstance(n, ast.Raise)]
        assert raises, "_identity_headers cannot fail — it asserts some identity unconditionally"
